#!/usr/bin/env python3
"""Раздел 6 паспорта: eval-набор, регрессия «до/после», мониторинг → docs/eval-suite.md.

Набор — eval/suite.json (записан до прогона). Результаты прогонов — выгрузки листа eval_results
в eval/results/*.csv (колонки как в sheets-templates/logs__eval_results.csv; run_label = «до» / «после»).

  python3 scripts/build_eval_docs.py
"""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITE = json.loads((ROOT / "eval/suite.json").read_text(encoding="utf-8"))
RESULTS_DIR = ROOT / "eval/results"
OUT = ROOT / "docs/eval-suite.md"

# Изменение для регрессии (описание; сам текст промпта — prompts/brd_writer_system.md, история — git)
CHANGE = ("Промпт шага «писатель BRD» (To-Be 2, prompts/brd_writer_system.md): неуверенный ответ заказчика "
          "(«наверное», «как обычно», «вроде») — это TBD и открытый вопрос, а не «рабочая гипотеза, подтверждена "
          "инициатором». Причина — находка серии пяти прогонов (прогоны 3 и 5).")


def load_results():
    runs = {}
    if RESULTS_DIR.exists():
        for f in sorted(RESULTS_DIR.glob("*.csv")):
            for r in csv.DictReader(f.open(encoding="utf-8")):
                runs.setdefault(r["run_label"], {})[r["case_id"]] = r
    return runs


def verdict(r):
    if not r:
        return "—"
    if r.get("judge") == "человек":
        return r.get("human_verdict") or "ждёт разметки"
    return r.get("verdict", "—")


def build():
    cases = SUITE["cases"]
    types = Counter(c["type"] for c in cases)
    judges = Counter(c["judge"] for c in cases)
    L = ["# Раздел 6 паспорта: eval-набор, регрессия «до/после», мониторинг", "",
         "Веха 4, позиции 6.1 и 6.2. Собирается скриптом `scripts/build_eval_docs.py` из `eval/suite.json` и "
         "выгрузок листа `eval_results` (`eval/results/`).", "",
         "## 1. Eval-набор", "",
         f"**{len(cases)} кейсов** ({', '.join(f'{t} — {n}' for t, n in types.items())}); судья: "
         f"{', '.join(f'{j} — {n}' for j, n in judges.items())}. Набор записан **до прогона** "
         f"(версия {SUITE['version']}, файл `eval/suite.json`). Прогонщик — воркфлоу BRD-97: кейсы I* вызывают "
         "настоящий BRD-03 (имитация сообщения от тестового чата `eval-*`), кейсы B* — настоящий BRD-04; признаки "
         "прохождения проверяет код (узел *Evaluate*), кейс I8 — человек.", "",
         "| # | тип | вход | ожидаемый результат | признак прохождения | кто судья |",
         "|---|---|---|---|---|---|"]
    for c in cases:
        L.append(f"| {c['id']} | {c['type']} | {c['input']} | {c['expected']} | {c['pass']} | {c['judge']} |")
    L += ["", "Почему судья — код: всё, что сверяется с правилом (число вопросов в ответе, наличие слова, статус "
          "сессии, отметка в чек-боксе раздела 8, ветка ошибки), проверяется без модели-оценщика. Человек судит "
          "только I8 — «не выдумал ли агент пункт регламента, которого нет в базе знаний»: это смысл ответа, "
          "правилом его надёжно не поймать.", ""]

    runs = load_results()
    L += ["## 2. Регрессионный прогон «до/после»", "", f"**Изменение:** {CHANGE}", "",
          "Порядок: прогон набора «до» → результаты сохранены в листе `eval_results` → изменение промпта → "
          "тот же набор «после».", ""]
    if runs:
        before, after = runs.get("до", {}), runs.get("после", {})
        L += ["| # | тип | до | после | расхождение |", "|---|---|---|---|---|"]
        diffs = 0
        for c in cases:
            b, a = verdict(before.get(c["id"])), verdict(after.get(c["id"]))
            d = "—" if b == a or a == "—" else "**да** → improvement-log"
            diffs += d.startswith("**")
            L.append(f"| {c['id']} | {c['type']} | {b} | {a} | {d} |")
        cnt = lambda run, v: sum(verdict(run.get(c['id'])) == v for c in cases)
        L += ["", f"Итог: «до» — pass {cnt(before, 'pass')} из {len(cases)}; «после» — pass {cnt(after, 'pass')} из "
              f"{len(cases)}; расхождений — {diffs}. Разбор каждого расхождения — [improvement-log.md](improvement-log.md).", ""]
    else:
        L += ["*Результаты появятся после прогонов: выгрузка листа `eval_results` → `eval/results/`.*", ""]

    L += ["## 3. Базовый мониторинг конкретного запуска", "",
          "Отдельной панели нет — базовый трек: встроенные логи n8n и журналы прогона, которые система пишет сама.", "",
          "| что видно | где смотреть |", "|---|---|",
          "| шаги прогона и их порядок | n8n → *Executions* нужного воркфлоу (BRD-03 — реплика интервью, BRD-04 — генерация BRD): каждый узел с входом и выходом; у агента — `intermediateSteps` (какие запросы к базе знаний он сделал) |",
          "| ошибки | статус execution (Error / Success), ветки ошибок в BRD-04 (*Generation failed*, *Limit stop*); лист `errors` (BRD-00) и алерт в чат саппорта; остановки по пределу — лист `manual_queue` |",
          "| исход прогона | метка `outcome = done / limit_stopped / error` в Execution Data каждого прогона |",
          "| стоимость | лист `brd_runs`: `run_cost_usd` (разница расхода ключа OpenRouter до и после, ожидание 60 с), токены и цена каждого вызова — OpenRouter → Activity (выгрузка CSV, пример — `evidence/runs-2026-10-14/openrouter_calls.csv`) |",
          "| время | длительность execution в n8n; у генерации BRD — `finished_at` в `brd_runs` |", "",
          "Как найти конкретный запуск: по `session_id` из `brd_runs` или `manual_queue` → в n8n фильтр executions "
          "BRD-04 по времени → открыть execution → узлы *BRD writer* (шаги агента), *Validate BRD* (вердикт), "
          "*Run row* (цена). Скриншот или скринкаст такого execution прикладывается к вехе."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"  {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
