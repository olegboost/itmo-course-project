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
              f"{len(cases)}; расхождений по вердиктам — {diffs}. Разбор — [improvement-log.md](improvement-log.md) "
              "(раздел «Регрессия»).", ""]
        # Сверка судьи-кода с разметкой человека (человек разметил все строки обоих прогонов)
        L += ["**Сверка вердиктов кода с разметкой человека** (человек разметил все строки обоих прогонов):", "",
              "| прогон | совпало | расхождения |", "|---|---|---|"]
        for label, run in (("до", before), ("после", after)):
            rows = [run[c["id"]] for c in cases if c["id"] in run and c["judge"] == "код" and run[c["id"]].get("human_verdict")]
            mism = [r["case_id"] for r in rows if r["verdict"] != r["human_verdict"]]
            L.append(f"| {label} | {len(rows) - len(mism)} из {len(rows)} | {', '.join(mism) or '—'} |")
        L += ["", "Расхождение на I3 в обоих прогонах — ложный отказ правила кода: бот по существу верно сказал о "
              "закрытом контуре («за пределы контура», «ни один вызов не выходит за периметр») и о риске, а правило "
              "искало слово «закрыт». Правило между «до» и «после» не менялось (набор один); исправление — в "
              "следующей версии набора.", ""]
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
          "*Run row* (цена). Скриншот или скринкаст такого execution прикладывается к вехе."] + [
          "", "### Пример конкретного запуска: кейс B1, прогон «после» (15.10.2026, 21:31–21:34 UTC)", "",
          "Источники: строка `brd_runs` ([evidence/eval-2026-10-16/brd_runs.csv](evidence/eval-2026-10-16/brd_runs.csv)), "
          "вызовы модели — выгрузка OpenRouter Activity ([evidence/eval-2026-10-16/openrouter_calls.csv](evidence/eval-2026-10-16/openrouter_calls.csv)), "
          "execution BRD-04 в n8n (скриншот — в материалах сдачи).", "",
          "| шаг | узел / модель | токены вход / выход | цена, $ | результат |", "|---|---|---|---|---|",
          "| 1. проверка потолков | *Check limits* (код) | — | 0 | попыток 0 из 2, тестовых BRD за 24 ч — в пределах |",
          "| 2. писатель, часть 1 (разделы 0–5) | *BRD writer*, deepseek-v4-pro (Sail Research) | 5 318 / 1 447 | 0.006195 | текст части без запросов к базе знаний |",
          "| 3. писатель, часть 2 (разделы 6–7) | *BRD writer* + KnowledgeBase | 5 313 / 198 → 22 064 / 3 235 | 0.001324 + 0.019334 | 4 запроса к базе знаний (`kb_queries`), текст части |",
          "| 4. писатель, часть 3 (разделы 8–12) | *BRD writer* | 5 312 / 1 303 | 0.002417 | текст части |",
          "| 5. валидатор | *Validate BRD* (код) | — | 0 | **fail**: `р.4 stakeholders` — в разделе 4 нет ролей DevOps и ИБ |",
          "| 6. раунд исправления 1 | *BRD fixer*, deepseek-v4.1-flash (InferenceNet) | 2 292 / 779 | 0.000821 | переписан только раздел 4 |",
          "| 7. повторная проверка | *Revalidate* → *Quality gate* (код) | — | 0 | **pass** |",
          "| 8. публикация | Drive API, реестр, уведомление команде | — | 0 | `outcome = published`, документ в папке-wiki |",
          "| **итого** | 5 вызовов модели | 40 299 / 6 962 | **0.030091** | ошибок нет; исправление сработало с первого раунда |", "",
          "Ошибки в этом запуске: нет. Пример ветки ошибки — кейс B3 того же прогона: `outcome = error`, «Сессия не найдена», "
          "генерации нет, алерт в чат поддержки.", "",
          "Стоимость прогонов набора целиком (по выгрузке OpenRouter): «до» — $0.156754 (интервьюер 9 вызовов $0.010651, "
          "B1 $0.028455, B2 $0.117648); «после» — $0.146134 (интервьюер $0.009373, B1 $0.030091, B2 $0.106670). B2 в 3–4 раза "
          "дороже B1: OpenRouter отдал его провайдерам Novita и Parasail, где вход дороже и меньше скидка за кэш.", "",
          "**Ограничение мониторинга:** колонка `run_cost_usd` в `brd_runs` у тестовых BRD обоих прогонов — 0: OpenRouter не "
          "обновил расход ключа даже за 60 секунд. Фактическая цена — только из выгрузки Activity (см. improvement-log)."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"  {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
