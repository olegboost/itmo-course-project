#!/usr/bin/env python3
"""Раздел 5 паспорта (начало): лист As-Is, Парето, bottleneck, окупаемость.

Один источник данных → два файла:
  docs/as-is.md                                             — для репозитория
  docs-for-improvement/improvement-3/Раздел 5 паспорта.xlsx — для сдачи (нужен openpyxl)

Числа меняются только здесь (например, после хронометража) и пересобираются:
  python3 scripts/build_as_is.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD_OUT = ROOT / "docs/as-is.md"
XLSX_OUT = ROOT / "docs-for-improvement/improvement-3/Раздел 5 паспорта.xlsx"

DATE = "15.10.2026"
PROCESS = "Сбор требований бизнес-заказчиков и подготовка BRD в wiki до утверждения схемы ПО"

# Стоимость успешного результата — таблица пяти прогонов (docs/pattern-and-economics.md)
COST_SUCCESS = 0.026        # $ за успешный BRD, факт OpenRouter по журналу brd_runs
COST_SUCCESS_UPPER = 0.038  # $ верхняя граница: весь дневной расход OpenRouter ÷ 5

ROW0 = {
    "откуда пришла задача": "смешанно: встреча, звонок, письмо, задача в Jira — единого канала нет",
    "кто приносит": "бизнес-заказчик",
    "что остаётся в записи": "событие в календаре и переписка; записанной постановки (транскрипта) нет",
}

TRACE_WIKI = "история страниц wiki: 12 BRD за 4 недели"
MEMORY = "по памяти исполнителя — следа нет (хронометраж на ближайших 2 BRD)"

# participants — сколько людей тратят эти минуты (потеря в человеко-минутах)
# confirmed: "full" — минуты и частота подтверждены следом, "freq" — только частота, "none" — ничего
STEPS = [
    dict(n=1, step="Провести встречу или звонок с заказчиком по требованиям",
         who="аналитик + бизнес-заказчик", tool="календарь, телефон / ВКС",
         minutes=120, per_week=3, participants=2, confirmed="full",
         trace=f"минуты — календарь (встречи по требованиям); частота — {TRACE_WIKI}",
         automated="нет (бот — демо)"),
    dict(n=2, step="Оформить BRD по шаблону в wiki",
         who="аналитик", tool="wiki (шаблон BRD)",
         minutes=60, per_week=3, participants=1, confirmed="freq",
         trace=f"частота — {TRACE_WIKI}; минуты — {MEMORY}",
         automated="нет (бот — демо)"),
    dict(n=3, step="Уточнить недостающее у заказчика в переписке",
         who="аналитик + бизнес-заказчик", tool="почта / мессенджер",
         minutes=60, per_week=3, participants=2, confirmed="freq",
         trace=f"частота — по числу BRD ({TRACE_WIKI}); минуты — {MEMORY}",
         automated="нет (бот — демо)"),
    dict(n=4, step="Проверить и согласовать BRD в wiki",
         who="руководитель AIOps + архитекторы (3 чел.)", tool="wiki",
         minutes=20, per_week=3.5, participants=3, confirmed="freq",
         trace="частота — по числу BRD (история wiki) + ~2 повторных согласования за 4 нед после возврата "
               "(по памяти); минуты — по памяти (часть 1)",
         automated="нет; по части 1 остаётся человеку (решение необратимо)"),
    dict(n=5, step="Спроектировать схему ПО в archi / draw.io",
         who="руководитель AIOps", tool="archi / draw.io → wiki",
         minutes=60, per_week=3, participants=1, confirmed="freq",
         trace="частота — по числу BRD (на каждый BRD — схема); минуты — по памяти",
         automated="нет"),
    dict(n=6, step="Утвердить схему на комитете в wiki",
         who="комитет (3 чел.)", tool="wiki",
         minutes=20, per_week=3, participants=3, confirmed="freq",
         trace="частота — по числу BRD (история wiki); минуты — по памяти (часть 1)",
         automated="нет; по части 1 остаётся человеку (решение необратимо)"),
    dict(n=7, step="Напомнить исполнителю о зависшей задаче в Jira",
         who="руководитель AIOps", tool="Jira, почта",
         minutes=5, per_week=round(20 * 12 / 52, 1), participants=1, confirmed="none",
         trace="частота и минуты — по памяти (часть 1: 20 раз в месяц по 5 минут); напоминания вручную, "
               "счётчика нет",
         automated="нет (workflow — в плане)"),
]
REWORK = ("Цена ошибки шагов 1–3: ~2 возврата BRD на доработку из 12 за 4 недели (≈ 17 %, по памяти); "
          "повторное согласование учтено в частоте шага 4; минуты на саму переделку не записаны — "
          "заводим реестр возвратов")

DISCREPANCIES = [
    "Частота шага 1: в паспорте (разделы 2–3) 5 BRD в неделю, по следу (история страниц wiki) — "
    "3 в неделю (12 за 4 недели). Потолки control layer рассчитаны от 5 в неделю и остаются с запасом.",
    "Частота шагов 2–4 части 1 (верификация BRD, схема, комитет): в части 1 «2–3 раза в месяц», "
    "фактически столько же, сколько BRD, — около 3 в неделю, плюс повторные согласования после возвратов.",
    "Длительность схемы (шаг 3 части 1): было «от 20 минут до нескольких часов», сейчас 60 минут (по памяти).",
    "Шаг 1 части 1 (240 минут) в листе разрезан на три шага по правилу «два глагола — два шага»: "
    "встреча 120 + оформление 60 + уточнения 60.",
    "Шаг 5 части 1 (напоминания): вердикт «workflow», но он ещё не реализован — напоминания вручную.",
    "Кандидат в ядро части 1 (шаг «Собрать пожелания…») As-Is подтвердил: расхождения по выбору шага нет.",
]


CONFIRMED = {"full": "да", "freq": "частота — да, минуты — нет", "none": "нет (по памяти)"}


def loss(s):
    return round(s["minutes"] * s["per_week"] * s["participants"])


def fmt(x):
    return str(x).replace(".", ",") if isinstance(x, float) else str(x)


def compute():
    rows = [dict(s, loss=loss(s)) for s in STEPS]
    total = sum(r["loss"] for r in rows)
    acc = 0
    pareto = []
    for r in sorted(rows, key=lambda r: -r["loss"]):
        acc += r["loss"]
        pareto.append(dict(r, share=r["loss"] / total, cum=acc / total))
    return rows, pareto, total


def texts(rows, pareto, total):
    by = {r["n"]: r for r in rows}
    b1, b3 = by[1], by[3]
    week_cost = 3 * COST_SUCCESS
    week_cost_up = 3 * COST_SUCCESS_UPPER
    bottlenecks = [
        ("Bottleneck 1 — шаг 1 «Провести встречу или звонок с заказчиком по требованиям»",
         f"Верх Парето ({b1['loss']} чел.-мин/нед, {b1['loss'] / total:.0%} всех потерь) и единственный шаг, у которого "
         f"и минуты, и частота подтверждены следом (календарь + история wiki). Не автоматизирован: бот пока демо.",
         f"Окупаемость: шаг теряет {b1['minutes']} мин × {fmt(b1['per_week'])} раза в неделю × {b1['participants']} участника "
         f"= {b1['loss']} чел.-мин в неделю ({b1['loss'] / 60:.0f} ч: {b1['loss'] // 2} мин аналитика и столько же заказчика), "
         f"а успешный BRD у агента стоит ≈ ${COST_SUCCESS} (верхняя граница ${COST_SUCCESS_UPPER}) — "
         f"при 3 BRD в неделю ${week_cost:.2f}–{week_cost_up:.2f} в неделю против {b1['loss'] / 60:.0f} часов людей. "
         f"Агент заменяет встречу структурированным интервью в Telegram; время заказчика на интервью с ботом "
         f"остаётся и будет измерено в To-Be (занятие 8) по brd_sessions."),
        ("Bottleneck 2 (кандидат) — шаг 3 «Уточнить недостающее у заказчика в переписке»",
         f"Второе место Парето ({b3['loss']} чел.-мин/нед, накопленно с шагом 1 — "
         f"{(b1['loss'] + b3['loss']) / total:.0%}). Не автоматизирован. Частота подтверждена историей wiki, "
         f"минуты — по памяти: шаг становится подтверждённым после хронометража ближайших 2 BRD.",
         f"Окупаемость: шаг теряет {b3['minutes']} мин × {fmt(b3['per_week'])} × {b3['participants']} = {b3['loss']} чел.-мин "
         f"в неделю; закрывается тем же прогоном агента (интервью задаёт все вопросы сразу, недостающее уходит "
         f"в «Открытые вопросы» BRD), то есть входит в те же ${week_cost:.2f}–{week_cost_up:.2f} в неделю — "
         f"отдельной стоимости нет."),
    ]
    excluded = ("Шаги 4 и 6 (согласование BRD и комитет по схеме) — 3-е и 5-е места Парето, но по части 1 "
                "намеренно остаются человеку: решение необратимо, цена ошибки — переделка проекта. "
                "Агент снижает их нагрузку косвенно — через меньшее число возвратов BRD (≈ 17 % сейчас).")
    return bottlenecks, excluded


def build_md(rows, pareto, total):
    bottlenecks, excluded = texts(rows, pareto, total)
    L = ["# Раздел 5 паспорта: As-Is, Парето, bottleneck", "",
         f"Занятие 7. **Процесс:** {PROCESS}. **Вход:** A — свой бот (BRD-агент). **Дата:** {DATE}.", "",
         "Файл собирается скриптом `scripts/build_as_is.py`; та же таблица — в "
         "`docs-for-improvement/improvement-3/Раздел 5 паспорта.xlsx` для сдачи.", "",
         "## Строка 0: откуда пришла задача", "", "| поле | значение |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in ROW0.items()]
    L += ["", "## Лист As-Is", "",
          "Потеря — время людей в неделю: минут × раз в неделю × число участников (чел.-мин/нед).", "",
          "| № | шаг | участник | инструмент | минут | раз в неделю | чем подтверждается | потеря, чел.-мин/нед |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['n']} | {r['step']} | {r['who']} | {r['tool']} | {r['minutes']} | {fmt(r['per_week'])} | "
                 f"{r['trace']} | {r['loss']} ({r['minutes']} × {fmt(r['per_week'])} × {r['participants']}) |")
    L += ["", REWORK, "",
          "**Что подтверждено следом, а что нет.** Полностью (и минуты, и частота) подтверждён шаг 1: календарь и "
          "история страниц wiki. У шагов 2–6 частота подтверждена числом BRD в wiki, минуты — по памяти. "
          "Шаг 7 — целиком по памяти. Чтобы закрыть пробел, на ближайших 2 BRD ведём хронометраж шагов 2, 3 и 5 "
          "и реестр возвратов после шага 4 (приёмы 2 и 3 из «Три приёма добыть след»).", "",
          "## Парето", "",
          "| место | шаг | потеря, чел.-мин/нед | доля | накопленно | подтверждено следом | автоматизирован сейчас |",
          "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(pareto, 1):
        L.append(f"| {i} | {r['n']}. {r['step']} | {r['loss']} | {r['share']:.0%} | {r['cum']:.0%} | "
                 f"{CONFIRMED[r['confirmed']]} | {r['automated']} |")
    L += [f"| | **итого** | **{total}** | | | | |", "",
          f"Три шага сбора требований (1–3, из семи) дают {sum(r['loss'] for r in rows if r['n'] <= 3)} чел.-мин "
          f"из {total} — {sum(r['loss'] for r in rows if r['n'] <= 3) / total:.0%} потерь.", "",
          "## Bottleneck и окупаемость", ""]
    for title, why, payback in bottlenecks:
        L += [f"### {title}", "", why, "", f"**{payback}**", ""]
    L += ["### Не bottleneck, хотя высоко в Парето", "", excluded, "",
          "## Расхождения с частью 1 (→ improvement-log на вехе 3)", ""]
    L += [f"{i}. {d}" for i, d in enumerate(DISCREPANCIES, 1)]
    return "\n".join(L) + "\n"


def build_xlsx(rows, pareto, total):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.chart import BarChart, LineChart, Reference

    bottlenecks, excluded = texts(rows, pareto, total)
    wb = Workbook()
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    head = PatternFill("solid", fgColor="DDEBF7")

    ws = wb.active
    ws.title = "As-Is"
    ws.append(["Раздел 5 паспорта: As-Is карта с потерями"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Процесс", PROCESS])
    ws.append(["Вход", "A — свой бот (BRD-агент)"])
    ws.append(["Дата", DATE])
    ws.append([])
    ws.append(["Строка 0: откуда пришла задача"])
    ws.cell(ws.max_row, 1).font = bold
    for k, v in ROW0.items():
        ws.append([k, v])
    ws.append([])
    hdr = ["№", "шаг", "участник", "инструмент", "минут", "раз в неделю", "чем подтверждается",
           "участников", "потеря, чел.-мин/нед"]
    ws.append(hdr)
    h = ws.max_row
    for c in range(1, len(hdr) + 1):
        ws.cell(h, c).font, ws.cell(h, c).fill = bold, head
    for r in rows:
        ws.append([r["n"], r["step"], r["who"], r["tool"], r["minutes"], r["per_week"], r["trace"],
                   r["participants"], None])
        i = ws.max_row
        ws.cell(i, 9).value = f"=E{i}*F{i}*H{i}"   # формула: потеря считается в самой таблице
    last = ws.max_row
    ws.append(["", "итого", "", "", "", "", "", "", f"=SUM(I{h + 1}:I{last})"])
    ws.cell(ws.max_row, 2).font = ws.cell(ws.max_row, 9).font = bold
    ws.append([])
    ws.append(["", REWORK])
    for col, w in zip("ABCDEFGHI", (6, 44, 30, 24, 8, 12, 60, 11, 16)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for c in row:
            c.alignment = wrap

    wp = wb.create_sheet("Парето")
    wp.append(["место", "шаг", "потеря, чел.-мин/нед", "доля", "накопленно", "подтверждено следом",
               "автоматизирован сейчас"])
    for c in range(1, 8):
        wp.cell(1, c).font, wp.cell(1, c).fill = bold, head
    for i, r in enumerate(pareto, 1):
        wp.append([i, f"{r['n']}. {r['step']}", r["loss"], round(r["share"], 3), round(r["cum"], 3),
                   CONFIRMED[r["confirmed"]], r["automated"]])
        wp.cell(i + 1, 4).number_format = wp.cell(i + 1, 5).number_format = "0%"
    n = len(pareto)
    bar = BarChart()
    bar.title, bar.y_axis.title = "Парето потерь по шагам", "чел.-мин/нед"
    bar.add_data(Reference(wp, min_col=3, min_row=1, max_row=n + 1), titles_from_data=True)
    bar.set_categories(Reference(wp, min_col=1, min_row=2, max_row=n + 1))
    line = LineChart()
    line.add_data(Reference(wp, min_col=5, min_row=1, max_row=n + 1), titles_from_data=True)
    line.y_axis.axId, line.y_axis.title, line.y_axis.crosses = 200, "накопленно", "max"
    bar += line
    bar.width, bar.height = 18, 9
    wp.add_chart(bar, "B12")
    for col, w in zip("ABCDEFG", (7, 52, 18, 8, 11, 24, 40)):
        wp.column_dimensions[col].width = w

    wbn = wb.create_sheet("Bottleneck")
    for title, why, payback in bottlenecks:
        wbn.append([title]); wbn.cell(wbn.max_row, 1).font = bold
        wbn.append([why]); wbn.append([payback]); wbn.cell(wbn.max_row, 1).font = bold
        wbn.append([])
    wbn.append(["Не bottleneck, хотя высоко в Парето"]); wbn.cell(wbn.max_row, 1).font = bold
    wbn.append([excluded]); wbn.append([])
    wbn.append(["Расхождения с частью 1 (→ improvement-log на вехе 3)"]); wbn.cell(wbn.max_row, 1).font = bold
    for i, d in enumerate(DISCREPANCIES, 1):
        wbn.append([f"{i}. {d}"])
    wbn.column_dimensions["A"].width = 130
    for row in wbn.iter_rows():
        for c in row:
            c.alignment = wrap
    XLSX_OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(XLSX_OUT)


if __name__ == "__main__":
    rows, pareto, total = compute()
    MD_OUT.write_text(build_md(rows, pareto, total), encoding="utf-8")
    print(f"  {MD_OUT.relative_to(ROOT)}")
    try:
        build_xlsx(rows, pareto, total)
        print(f"  {XLSX_OUT.relative_to(ROOT)}")
    except ImportError:
        print("  xlsx пропущен: нет openpyxl (pip install openpyxl)")
