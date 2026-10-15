#!/usr/bin/env python3
"""Раздел 5 паспорта: As-Is (занятие 7), To-Be и KPI (занятие 8) + improvement-log.

Один источник данных → файлы:
  docs/as-is.md, docs/to-be.md, docs/improvement-log.md — для репозитория
  docs/Раздел 5 паспорта.xlsx                            — для сдачи (нужен openpyxl)

Значения «до» в KPI To-Be берутся из листа As-Is этого же файла — не вводятся заново.
Числа меняются только здесь (например, после хронометража) и пересобираются:
  python3 scripts/build_section5.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD_OUT = ROOT / "docs/as-is.md"
TOBE_OUT = ROOT / "docs/to-be.md"
LOG_OUT = ROOT / "docs/improvement-log.md"
XLSX_OUT = ROOT / "docs/Раздел 5 паспорта.xlsx"

DATE = "15.10.2026"
DATE_TOBE = "15.10.2026"
PROCESS = "Сбор требований бизнес-заказчиков и подготовка BRD в wiki до утверждения схемы ПО"

# Стоимость успешного результата — таблица пяти прогонов (docs/pattern-and-economics.md)
COST_SUCCESS = 0.039        # $ за успешный BRD: факт OpenRouter Activity за серию 14.10 ($0.1936 ÷ 5)

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


# ===========================================================================
# To-Be (занятие 8): судьба шагов, исполнители, точки контроля, петли, KPI, оставленное человеку
TOBE_ROW0 = {
    "откуда пришла задача": "единый вход — Telegram-бот BRD-агента (вместо встречи, звонка, письма, Jira)",
    "кто приносит": "бизнес-заказчик — сам, без аналитика",
    "что остаётся в записи": "полный транскрипт интервью в brd_sessions.history (с метками времени реплик)",
}

# Судьба каждого шага As-Is (n — номер шага As-Is)
FATE = {
    1: ("перепроектировать + схлопнуть с шагом 3",
        "Встреча аналитика с заказчиком заменена структурированным интервью в боте: заказчик отвечает сам, "
        "когда удобно; аналитик из шага убран. → To-Be 1"),
    3: ("схлопнуть в To-Be 1",
        "Признак: его и соседний (шаг 1) подряд делают одни и те же люди — аналитик и заказчик, с передачей "
        "задачи туда-обратно. Интервью задаёт все вопросы за один проход, «не знаю» сразу становится "
        "открытым вопросом BRD — отдельного круга уточнений нет."),
    2: ("перепроектировать",
        "Ручное оформление по шаблону → генерация по стандарту с проверкой кодом (паттерн sequential "
        "с валидатором); размещение в wiki выделено в отдельный автоматический шаг. → To-Be 2 и 3"),
    4: ("оставить (вход перепроектирован)",
        "Решение остаётся у людей, но на вход приходит только BRD, прошедший валидатор стандарта: "
        "согласующие проверяют содержание, а не форму. → To-Be 4"),
    5: ("оставить", "Проектирование схемы остаётся человеку на этой итерации. → To-Be 5"),
    6: ("оставить",
        "Комитет по схеме остаётся людям. Схлопнуть с шагом 4 пока нельзя: согласующие разные, пересекается "
        "только архитектор. Кандидат на будущее — когда состав станет единым. → To-Be 6"),
    7: ("перепроектировать",
        "Напоминания заказчику по незавершённому интервью делает workflow BRD-05 (уже работает); "
        "напоминания согласующим в Jira — workflow в плане. Ручной шаг исчезает. → To-Be 7"),
}

TOBE = [
    dict(n=1, step="Провести интервью заказчика в Telegram и подтвердить резюме", from_asis="1 + 3",
         executor="агент", how="BRD-03: агент-интервьюер (DeepSeek v4.1-flash) + инструмент KnowledgeBase (агент сам "
         "решает, идти ли в базу знаний); голос → Whisper",
         control="экран «Всё верно? Если да — сформирую BRD» в чате с ботом: без явного «да» заказчика BRD не создаётся",
         loop="заказчик поправил резюме → агент учитывает правки и снова просит подтверждения (адресат — заказчик)"),
    dict(n=2, step="Сформировать BRD по стандарту и проверить кодом", from_asis="2",
         executor="агент", how="BRD-04: писатель (DeepSeek v4-pro + KnowledgeBase) → валидатор стандарта (код) → "
         "до 2 раундов исправления (перегенерация части / v4.1-flash)",
         control="— (автоматический шаг; человек видит результат на To-Be 4)",
         loop="BRD не прошёл валидатор после 2 раундов → ручная очередь manual_queue (адресат — команда ИИ-среды); "
              "сработал потолок control layer → та же очередь"),
    dict(n=3, step="Опубликовать BRD в wiki, внести в реестр и уведомить", from_asis="2 (часть)",
         executor="workflow", how="BRD-04 (публикация Google Doc без дублей по appProperties) + brd_registry + "
         "BRD-02 (ссылка заказчику и в чат команды)",
         control="—", loop="техсбой публикации → текст сохраняется в сессии, повторный /done только публикует "
                           "(адресат — заказчик; саппорт получает алерт)"),
    dict(n=4, step="Согласовать BRD в wiki", from_asis="4",
         executor="человек", how="руководитель AIOps + архитекторы (3 чел.) в wiki; решение фиксируется в "
         "brd_registry.status (Approved / Returned)",
         control="согласование в wiki — там, где согласующие уже работают",
         loop="BRD отклонён → возврат к бизнес-заказчику на доинтервью по замечаниям: команда ИИ-среды пишет "
              "заказчику, он проходит интервью в боте заново (/new). Переоткрытие сессии ботом — следующий шаг"),
    dict(n=5, step="Спроектировать схему ПО в archi / draw.io", from_asis="5",
         executor="человек", how="руководитель AIOps; на вход — согласованный BRD (разделы 6–8: требования, "
         "нагрузка, DoD)", control="—", loop="схема не утверждена → доработка схемы (адресат — руководитель AIOps)"),
    dict(n=6, step="Утвердить схему на комитете в wiki", from_asis="6",
         executor="человек", how="комитет: архитектор, ИБ, разработчик (3 чел.)",
         control="утверждение в wiki", loop="схема отклонена → To-Be 5 (адресат — руководитель AIOps); "
                                             "изменились требования → To-Be 1 (адресат — заказчик)"),
    dict(n=7, step="Напомнить о зависших задачах", from_asis="7",
         executor="workflow", how="BRD-05: заказчику — после 24 ч тишины, не больше 2 раз, закрытие через 72 ч "
         "(работает); согласующим в Jira — план",
         control="—", loop="после 2 напоминаний и 72 ч — сессия abandoned, уведомление в чат команды"),
]

LEFT_TO_HUMAN = [
    ("Согласование BRD (To-Be 4)",
     "Это решение, запускать ли проект и в каком объёме: оно необратимо, за ним бюджет и приоритеты, "
     "а цена ошибки — переделка или закрытие проекта. Агент собирает и проверяет форму, но не берёт на себя "
     "ответственность за решение."),
    ("Утверждение схемы на комитете (To-Be 6)",
     "ИБ согласует меры защиты: по политике DOC-07 меры согласуются ИБ, а агенту в BRD прямо запрещено их "
     "предлагать (раздел 7.5 — только факты). Регуляторная ответственность остаётся за людьми."),
    ("Проектирование схемы (To-Be 5)",
     "Архитектурное решение с последствиями для всей реализации; на этой итерации агентные шаги — интервью и BRD "
     "(норма 1–2). Часть 1 видела здесь агента — расхождение в improvement-log."),
    ("Разбор ручной очереди (manual_queue)",
     "Решение после сработавшего потолка: продолжить, сформировать BRD по собранному или закрыть. Повтор после "
     "предела запрещён, поэтому решает человек с правом решить — команда ИИ-среды."),
]

MEASURE_ACTIVE = ("сумма интервалов между репликами интервью ≤ 10 мин по меткам времени в brd_sessions.history "
                  "(паузы дольше 10 мин не считаются)")


def kpis(rows):
    by = {r["n"]: r for r in rows}
    analyst_week = sum(by[n]["minutes"] * by[n]["per_week"] for n in (1, 2, 3))
    customer_brd = by[1]["minutes"] + by[3]["minutes"]
    return [
        dict(name="Минут аналитика на сбор требований в неделю",
             before=f"{analyst_week:g} мин/нед (As-Is шаги 1–3: (120 + 60 + 60) × 3; встреча — календарь, частота — "
                    f"история wiki, оформление и переписка — по памяти)",
             target="≤ 60 мин/нед (только разбор ручной очереди и возвратов)",
             measure="календарь: встреч по требованиям нет; manual_queue: разборы (минуты записываются в resolution)"),
        dict(name="Время заказчика на один BRD",
             before=f"{customer_brd} мин (As-Is: встреча {by[1]['minutes']} — календарь + переписка {by[3]['minutes']} — "
                    f"по памяти)",
             target="≤ 45 мин активного времени (интервью 12–25 ответов по промпту, ~2 мин на ответ)",
             measure=MEASURE_ACTIVE),
        dict(name="Доля BRD, возвращённых после согласования",
             before="≈ 17 % (As-Is: ~2 возврата из 12 BRD за 4 недели, по памяти)",
             target="≤ 10 % (на вход согласования — только BRD, прошедший валидатор стандарта)",
             measure="brd_registry.status: Approved / Returned, считается за 4 недели"),
    ]


TOBE_DISCREPANCIES = [
    "Схема ПО (шаг 3 части 1): вердикт части 1 — «агент», в To-Be — человек на этой итерации "
    "(архитектурное решение; агентные шаги — интервью и BRD).",
    "Escalation шагов 1, 2, 4 части 1: «проверка балла 0–100 в 9:00 и 16:00, срок 2 рабочих дня, оповещение "
    "руководителя». Реализовано иначе: BRD-05 напоминает заказчику после 24 ч (не больше 2 раз, закрытие через "
    "72 ч), остановки по пределу уходят в ручную очередь manual_queue к команде ИИ-среды.",
    "Напоминания (шаг 5 части 1, workflow): для заказчика работают (BRD-05), для согласующих в Jira — в плане.",
    "Петля возврата после отклонения BRD: в части 1 — «откатываемся на шаг 1»; в To-Be пока вручную (команда пишет "
    "заказчику, повторное интервью /new); переоткрытие сессии ботом — следующий шаг.",
    "Схлопнуть согласование BRD и комитет по схеме в одно ревью — отложено: составы разные, пересечение только "
    "в архитекторе.",
    "Интеграция с Jira (шаг 1 части 1, «доступ: да, также интеграция с jira») в To-Be не используется: вход — бот, "
    "результат — Google Doc в папке-wiki.",
]

REGRESSION_LOG = [
    "Изменение: промпт шага «писатель BRD» (неуверенный ответ — TBD, а не «рабочая гипотеза, подтверждена "
    "инициатором»). Набор: 13 кейсов (eval/suite.json), прогоны «до» и «после» 16.10.2026. Расхождений по вердиктам: 0 "
    "(код: 11 pass / 1 fail / 1 на разметку; человек: 13 pass — в обоих прогонах). Изменение ничего не сломало.",
    "Изменение не подтверждено набором: ошибка, ради которой его вносили (пометка «подтверждена инициатором» у "
    "неуверенного ответа, серия пяти прогонов, прогоны 3 и 5), в прогоне «до» не воспроизвелась — кейс B2 прошёл уже "
    "до изменения. Ошибка плавающая, один прогон её не ловит. Что делать: оставить изменение (вреда нет), в версии 2 "
    "набора добавить кейс с несколькими неуверенными ответами и проверять правило по всему документу, а не только "
    "по строкам MLflow/GitLab; прогонять такой кейс 3 раза и считать долю.",
    "Расхождение судей на I3 (оба прогона): код — fail, человек — pass. Бот верно сказал о закрытом контуре и риске, "
    "но без слова «закрыт» («за пределы контура», «за периметр»). Дефект правила, а не системы: в версии 2 набора "
    "признак — «закрыт|контур|периметр» и «риск».",
    "Прогонщик BRD-97 не сопоставил ответ BRD-04 с кейсами B1, B2, B4 (outcome и цена пустые; вердикты взяты из "
    "статуса сессии и текста BRD — верны). Цена этих прогонов — в листе brd_runs. В версии 2 — сопоставление по "
    "session_id через лист brd_runs.",
    "Замер цены прогона через разницу расхода ключа OpenRouter (run_cost_usd в brd_runs) не работает: у тестовых BRD "
    "обоих прогонов — 0 даже при ожидании 60 с, у демо-прогона 15.10 — $0.009 при факте $0.018. Фактическая цена — "
    "выгрузка Activity: прогон набора «до» $0.157, «после» $0.146, кейс B1 «после» $0.030. Что делать: брать цену по "
    "каждому вызову (данные генерации OpenRouter или токены из ответа модели × тариф), а не по расходу ключа.",
]

EARLIER_LOG = [
    ("2026-10-11, improvement-1", "Раздел 2 паспорта: шаг 1 — «8 раз в месяц по 40 минут» исправлено на "
     "5 раз в неделю по 240 минут (ошибка расчёта)."),
    ("2026-10-14, improvement-2", "Замечание ревьювера: «бот сформирует новую версию BRD» в разделе 3 паспорта не "
     "называло класс повторяемости; создание документа — класс 3, защищено appProperties (docs/passport-section-3-fixes.md)."),
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
    bottlenecks = [
        ("Bottleneck 1 — шаг 1 «Провести встречу или звонок с заказчиком по требованиям»",
         f"Верх Парето ({b1['loss']} чел.-мин/нед, {b1['loss'] / total:.0%} всех потерь) и единственный шаг, у которого "
         f"и минуты, и частота подтверждены следом (календарь + история wiki). Не автоматизирован: бот пока демо.",
         f"Окупаемость: шаг теряет {b1['minutes']} мин × {fmt(b1['per_week'])} раза в неделю × {b1['participants']} участника "
         f"= {b1['loss']} чел.-мин в неделю ({b1['loss'] / 60:.0f} ч: {b1['loss'] // 2} мин аналитика и столько же заказчика), "
         f"а успешный BRD у агента стоит ≈ ${COST_SUCCESS} (факт OpenRouter, таблица пяти прогонов) — "
         f"при 3 BRD в неделю ≈ ${week_cost:.2f} в неделю против {b1['loss'] / 60:.0f} часов людей. "
         f"Агент заменяет встречу структурированным интервью в Telegram; время заказчика на интервью с ботом "
         f"остаётся и будет измерено в To-Be (занятие 8) по brd_sessions."),
        ("Bottleneck 2 (кандидат) — шаг 3 «Уточнить недостающее у заказчика в переписке»",
         f"Второе место Парето ({b3['loss']} чел.-мин/нед, накопленно с шагом 1 — "
         f"{(b1['loss'] + b3['loss']) / total:.0%}). Не автоматизирован. Частота подтверждена историей wiki, "
         f"минуты — по памяти: шаг становится подтверждённым после хронометража ближайших 2 BRD.",
         f"Окупаемость: шаг теряет {b3['minutes']} мин × {fmt(b3['per_week'])} × {b3['participants']} = {b3['loss']} чел.-мин "
         f"в неделю; закрывается тем же прогоном агента (интервью задаёт все вопросы сразу, недостающее уходит "
         f"в «Открытые вопросы» BRD), то есть входит в те же ≈ ${week_cost:.2f} в неделю — "
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
         "Файл собирается скриптом `scripts/build_section5.py`; та же таблица — в "
         "`docs/Раздел 5 паспорта.xlsx` для сдачи. Продолжение раздела — [to-be.md](to-be.md).", "",
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


def build_tobe_md(rows):
    K = kpis(rows)
    L = ["# Раздел 5 паспорта: To-Be, KPI и оставленное человеку", "",
         f"Занятие 8. **Процесс:** {PROCESS}. **Дата:** {DATE_TOBE}. Продолжение [as-is.md](as-is.md); "
         "расхождения с частью 1 — [improvement-log.md](improvement-log.md).", "",
         "To-Be построен от bottleneck As-Is (встреча с заказчиком, 720 чел.-мин/нед), а не от агента: вход "
         "перенесён в бот, встреча и круг уточнений схлопнуты в одно интервью, ручное оформление заменено "
         "генерацией с проверкой кодом, публикация и напоминания стали workflow. Решения остаются у людей.", "",
         "## Строка 0: откуда приходит задача", "", "| поле | As-Is | To-Be |", "|---|---|---|"]
    L += [f"| {k} | {ROW0[k]} | {TOBE_ROW0[k]} |" for k in ROW0]
    L += ["", "## Судьба шагов As-Is", "", "| As-Is | шаг As-Is | судьба | почему |", "|---|---|---|---|"]
    for r in rows:
        f, why = FATE[r["n"]]
        L.append(f"| {r['n']} | {r['step']} | **{f}** | {why} |")
    L += ["", "## Лист To-Be", "",
          f"Исполнители: агент — {sum(t['executor'] == 'агент' for t in TOBE)} шага из {len(TOBE)} (норма 1–2), "
          f"workflow — {sum(t['executor'] == 'workflow' for t in TOBE)}, человек — "
          f"{sum(t['executor'] == 'человек' for t in TOBE)}.", "",
          "| To-Be | шаг | из As-Is | исполнитель | как устроено | точка контроля | петля возврата (адресат) |",
          "|---|---|---|---|---|---|---|"]
    for t in TOBE:
        L.append(f"| {t['n']} | {t['step']} | {t['from_asis']} | **{t['executor']}** | {t['how']} | {t['control']} | "
                 f"{t['loop']} |")
    L += ["", "## KPI: до → цель → замер", "", "Значения «до» — из листа As-Is ([as-is.md](as-is.md)).", "",
          "| KPI | до | цель | чем меряем |", "|---|---|---|---|"]
    L += [f"| {k['name']} | {k['before']} | {k['target']} | {k['measure']} |" for k in K]
    L += ["", "## Что осознанно оставлено человеку", ""]
    L += [f"- **{what}.** {why}" for what, why in LEFT_TO_HUMAN]
    L += ["", "## Что подтверждает реализацию", "",
          "- Экспорт сценариев: `workflows/BRD-03_interview-agent.json` (To-Be 1), `BRD-04_brd-generator.json` "
          "(To-Be 2–3), `BRD-05_reminders.json` (To-Be 7), `BRD-07_manual-queue.json` (петли в ручную очередь).",
          "- Скринкаст прогона: интервью в боте → подтверждение резюме → ссылка на опубликованный BRD."]
    return "\n".join(L) + "\n"


def build_log_md():
    L = ["# Improvement-log", "",
         "Расхождения с частью 1 и исправления паспорта. Часть 1 задним числом не переписывается — расхождения "
         "фиксируются здесь (веха 3).", "",
         "## Ранее (занятия 5–6)", ""]
    L += [f"- **{when}.** {what}" for when, what in EARLIER_LOG]
    L += ["", "## As-Is (занятие 7, 15.10.2026) — [as-is.md](as-is.md)", ""]
    L += [f"{i}. {d}" for i, d in enumerate(DISCREPANCIES, 1)]
    L += ["", "## To-Be (занятие 8, 15.10.2026) — [to-be.md](to-be.md)", ""]
    L += [f"{i}. {d}" for i, d in enumerate(TOBE_DISCREPANCIES, len(DISCREPANCIES) + 1)]
    L += ["", "## Регрессия eval-набора (занятие 10, 16.10.2026) — [eval-suite.md](eval-suite.md)", ""]
    L += [f"{i}. {d}" for i, d in enumerate(REGRESSION_LOG, len(DISCREPANCIES) + len(TOBE_DISCREPANCIES) + 1)]
    return "\n".join(L) + "\n"


def add_tobe_sheets(wb, rows):
    from openpyxl.styles import Alignment, Font, PatternFill
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    head = PatternFill("solid", fgColor="E2EFDA")

    def table(ws, header, data, widths):
        ws.append(header)
        h = ws.max_row
        for c in range(1, len(header) + 1):
            ws.cell(h, c).font, ws.cell(h, c).fill = bold, head
        for d in data:
            ws.append(d)
        for col, w in zip("ABCDEFGH", widths):
            ws.column_dimensions[col].width = w

    ws = wb.create_sheet("To-Be")
    ws.append(["Раздел 5 паспорта: To-Be"]); ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Строка 0", "As-Is", "To-Be"])
    for c in range(1, 4):
        ws.cell(2, c).font = bold
    for k in ROW0:
        ws.append([k, ROW0[k], TOBE_ROW0[k]])
    ws.append([])
    ws.append(["Судьба шагов As-Is"]); ws.cell(ws.max_row, 1).font = bold
    table(ws, ["As-Is", "шаг As-Is", "судьба", "почему"],
          [[r["n"], r["step"], FATE[r["n"]][0], FATE[r["n"]][1]] for r in rows], (8, 40, 28, 80))
    ws.append([])
    ws.append(["Лист To-Be"]); ws.cell(ws.max_row, 1).font = bold
    table(ws, ["To-Be", "шаг", "из As-Is", "исполнитель", "как устроено", "точка контроля", "петля возврата (адресат)"],
          [[t["n"], t["step"], t["from_asis"], t["executor"], t["how"], t["control"], t["loop"]] for t in TOBE],
          (8, 40, 28, 14, 50, 40, 50))
    for row in ws.iter_rows():
        for c in row:
            c.alignment = wrap

    wk = wb.create_sheet("KPI и человеку")
    table(wk, ["KPI", "до (из As-Is)", "цель", "чем меряем"],
          [[k["name"], k["before"], k["target"], k["measure"]] for k in kpis(rows)], (40, 60, 45, 60))
    wk.append([])
    wk.append(["Осознанно оставлено человеку"]); wk.cell(wk.max_row, 1).font = bold
    for what, why in LEFT_TO_HUMAN:
        wk.append([what, why])
    for row in wk.iter_rows():
        for c in row:
            c.alignment = wrap

    wl = wb.create_sheet("Improvement-log")
    wl.column_dimensions["A"].width = 30
    wl.column_dimensions["B"].width = 120
    for when, what in EARLIER_LOG:
        wl.append([when, what])
    for i, d in enumerate(DISCREPANCIES, 1):
        wl.append([f"As-Is, расхождение {i}", d])
    for i, d in enumerate(TOBE_DISCREPANCIES, len(DISCREPANCIES) + 1):
        wl.append([f"To-Be, расхождение {i}", d])
    for i, d in enumerate(REGRESSION_LOG, len(DISCREPANCIES) + len(TOBE_DISCREPANCIES) + 1):
        wl.append([f"Регрессия, запись {i}", d])
    for row in wl.iter_rows():
        for c in row:
            c.alignment = wrap


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
    add_tobe_sheets(wb, rows)
    XLSX_OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(XLSX_OUT)


if __name__ == "__main__":
    rows, pareto, total = compute()
    MD_OUT.write_text(build_md(rows, pareto, total), encoding="utf-8")
    TOBE_OUT.write_text(build_tobe_md(rows), encoding="utf-8")
    LOG_OUT.write_text(build_log_md(), encoding="utf-8")
    for f in (MD_OUT, TOBE_OUT, LOG_OUT):
        print(f"  {f.relative_to(ROOT)}")
    try:
        build_xlsx(rows, pareto, total)
        print(f"  {XLSX_OUT.relative_to(ROOT)}")
    except ImportError:
        print("  xlsx пропущен: нет openpyxl (pip install openpyxl)")
