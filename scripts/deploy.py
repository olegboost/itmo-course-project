#!/usr/bin/env python3
"""Деплой BRD-агента в n8n (Cloud или self-hosted) через n8n Public API.

Что делает:
  1. Берёт workflows/*.json (их собирает build_workflows.py).
  2. Подставляет конфигурацию (__CFG_*__) и ID credentials (__CRED_*__) из .env.
  3. Создаёт воркфлоу (или обновляет одноимённые) в правильном порядке и
     подставляет ID связанных воркфлоу (__WF_*__): Execute Workflow и Settings → Error workflow.
     BRD-00 и BRD-07 ссылаются друг на друга, поэтому такие связи дописываются вторым проходом.
  4. С флагом --activate включает воркфлоу с триггерами (BRD-01, 03, 05, 06).

Режим --dry-run ничего не отправляет: пишет готовые к ручному импорту файлы в build/
(ID связанных воркфлоу в этом случае нужно выбрать в UI вручную).

Только стандартная библиотека Python 3.9+.
  python3 scripts/deploy.py --dry-run
  python3 scripts/deploy.py
  python3 scripts/deploy.py --activate
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Порядок важен: сначала подворкфлоу, на которые ссылаются остальные.
ORDER = [
    ("BRD-00_errors-and-alerts.json", "__WF_ERRORS__", False),
    ("BRD-02_notify.json", "__WF_NOTIFY__", False),
    ("BRD-07_manual-queue.json", "__WF_QUEUE__", False),
    ("BRD-04_brd-generator.json", "__WF_BRD_GENERATOR__", False),
    ("BRD-08_approve-brd.json", None, False),
    ("BRD-01_kb-sync.json", None, True),
    ("BRD-03_interview-agent.json", "__WF_INTERVIEW__", True),
    ("BRD-05_reminders.json", None, True),
    ("BRD-06_daily-digest.json", None, True),
    ("BRD-97_eval-suite.json", None, False),
    ("BRD-98_eval-seed.json", None, False),
    ("BRD-99_setup-data-tables.json", None, False),
]
API_FIELDS = ("name", "nodes", "connections", "settings")
# Ручной импорт (без Public API): переменные .env с ID заранее созданных пустых подворкфлоу.
WF_ENV = ("ERRORS", "NOTIFY", "QUEUE", "BRD_GENERATOR", "INTERVIEW")


def load_env(path):
    env = {}
    if not path.exists():
        sys.exit(f"Нет файла {path}. Скопируйте .env.example в .env и заполните.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = re.split(r"(?:^|\s)#", v, maxsplit=1)[0]  # инлайн-комментарий
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def substitute(obj, mapping):
    text = json.dumps(obj, ensure_ascii=False)
    for k, v in mapping.items():
        text = text.replace(k, json.dumps(v, ensure_ascii=False)[1:-1])
    return json.loads(text)


def strip_missing_creds(wf, env):
    """Узлы с незаполненными CRED_* оставляем без credentials — их выберут в UI."""
    for node in wf["nodes"]:
        creds = node.get("credentials")
        if not creds:
            continue
        for ctype in list(creds):
            cid = creds[ctype]["id"]
            if cid.startswith("__CRED_"):
                del creds[ctype]
        if not creds:
            node.pop("credentials")


class Api:
    def __init__(self, base, key):
        self.base = base.rstrip("/") + "/api/v1"
        self.key = key

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "X-N8N-API-KEY": self.key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path} → HTTP {e.code}: {e.read().decode(errors='replace')[:800]}")

    def find_by_name(self, name):
        cursor = None
        while True:
            q = "/workflows?limit=250" + (f"&cursor={cursor}" if cursor else "")
            res = self.call("GET", q)
            for wf in res.get("data", []):
                if wf.get("name") == name:
                    return wf["id"]
            cursor = res.get("nextCursor")
            if not cursor:
                return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=str(ROOT / ".env"))
    ap.add_argument("--dry-run", action="store_true", help="только записать build/*.json")
    ap.add_argument("--activate", action="store_true", help="активировать воркфлоу с триггерами")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    mapping = {f"__{k}__": v for k, v in env.items() if (k.startswith("CFG_") or k.startswith("CRED_")) and v}
    # Ручной импорт (без Public API): ID заранее созданных пустых воркфлоу BRD-00/02/07/04.
    for key in WF_ENV:
        if env.get(f"WF_{key}_ID"):
            mapping[f"__WF_{key}__"] = env[f"WF_{key}_ID"]
    left = set()

    api = None if args.dry_run else Api(env.get("N8N_BASE_URL", ""), env.get("N8N_API_KEY", ""))
    if api and (not env.get("N8N_BASE_URL") or not env.get("N8N_API_KEY")):
        sys.exit("Заполните N8N_BASE_URL и N8N_API_KEY в .env (или используйте --dry-run).")

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    wf_ids = {}
    to_activate = []
    deployed = {}  # fname -> id: для второго прохода (связи на воркфлоу, созданные позже)
    for fname, placeholder, activate in ORDER:
        wf = json.loads((ROOT / "workflows" / fname).read_text(encoding="utf-8"))
        wf = substitute(wf, {**mapping, **wf_ids})
        strip_missing_creds(wf, env)
        (build / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        if api:
            body = {k: wf[k] for k in API_FIELDS}
            existing = api.find_by_name(wf["name"])
            if existing:
                api.call("PUT", f"/workflows/{existing}", body)
                wid = existing
                print(f"updated  {wid}  {wf['name']}")
            else:
                wid = api.call("POST", "/workflows", body)["id"]
                print(f"created  {wid}  {wf['name']}")
            if placeholder:
                wf_ids[placeholder] = wid
            deployed[fname] = wid
            if activate:
                to_activate.append((wid, wf["name"]))
        else:
            print(f"built    build/{fname}")

    # Второй проход: подставить ID воркфлоу, созданных позже ссылающегося (BRD-00 → BRD-07).
    for fname, _, _ in ORDER:
        wf = json.loads((build / fname).read_text(encoding="utf-8"))
        pending = set(re.findall(r"__WF_[A-Z_]+__", json.dumps(wf))) & set(wf_ids)
        if pending:
            wf = substitute(wf, wf_ids)
            (build / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
            if api:
                api.call("PUT", f"/workflows/{deployed[fname]}", {k: wf[k] for k in API_FIELDS})
                print(f"linked   {deployed[fname]}  {wf['name']}  ({', '.join(sorted(pending))})")
        left |= set(re.findall(r"__(?:CFG|WF)_[A-Z_]+__", json.dumps(wf)))

    if api and args.activate:
        for wid, name in to_activate:
            api.call("POST", f"/workflows/{wid}/activate")
            print(f"active   {wid}  {name}")

    if left:
        print("\nВнимание: не заполнены плейсхолдеры (задайте в .env или в узлах Config):")
        for p in sorted(left):
            print("  ", p)


if __name__ == "__main__":
    main()
