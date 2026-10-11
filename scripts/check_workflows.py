#!/usr/bin/env python3
"""Статическая проверка workflows/*.json после build_workflows.py.

Проверяет:
  1. связи (connections) ссылаются на существующие узлы, имена узлов уникальны;
  2. JS в Code-узлах синтаксически корректен (нужен Node.js: `node --check`);
  3. все плейсхолдеры __CFG_*__ / __CRED_*__ объявлены в .env.example, __WF_*__ знает deploy.py.

Запуск:  python3 scripts/check_workflows.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from deploy import ORDER, WF_ENV  # noqa: E402

env_keys = {line.split("=", 1)[0].strip() for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")}
known = {f"__{k}__" for k in env_keys if k.startswith(("CFG_", "CRED_"))}
known |= {ph for _, ph, _ in ORDER if ph} | {f"__WF_{k}__" for k in WF_ENV}

node_bin = shutil.which("node")
errors = []
for path in sorted((ROOT / "workflows").glob("*.json")):
    wf = json.loads(path.read_text(encoding="utf-8"))
    names = [n["name"] for n in wf["nodes"]]
    if len(names) != len(set(names)):
        errors.append(f"{path.name}: повторяются имена узлов")
    nameset = set(names)
    for src, kinds in wf["connections"].items():
        if src not in nameset:
            errors.append(f"{path.name}: связь из несуществующего узла «{src}»")
        for outs in kinds.values():
            for out in outs:
                for c in out:
                    if c["node"] not in nameset:
                        errors.append(f"{path.name}: «{src}» → несуществующий узел «{c['node']}»")
    for ph in sorted(set(re.findall(r"__(?:CFG|CRED|WF)_[A-Z_]+__", path.read_text(encoding="utf-8"))) - known):
        errors.append(f"{path.name}: неизвестный плейсхолдер {ph}")
    if not node_bin:
        continue
    for n in wf["nodes"]:
        js = n["parameters"].get("jsCode") if n["type"] == "n8n-nodes-base.code" else None
        if not js:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write("async function __n8n() {\n" + js + "\n}\n")
        r = subprocess.run([node_bin, "--check", f.name], capture_output=True, text=True)
        Path(f.name).unlink()
        if r.returncode:
            msg = next((l for l in r.stderr.splitlines() if "Error" in l), r.stderr.strip()[:200])
            errors.append(f"{path.name}: JS в узле «{n['name']}»: {msg}")

if not node_bin:
    print("Node.js не найден — проверка JS пропущена.")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("OK: связи, JS и плейсхолдеры в порядке.")
