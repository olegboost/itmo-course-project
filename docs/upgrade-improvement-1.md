# Обновление до control layer (improvement-1): ручной импорт в n8n Cloud

Инструкция для уже работающей установки, где воркфлоу импортированы вручную, без Public API. Что и зачем меняется, описано в [control-layer.md](control-layer.md).

**Что изменилось:**

| Воркфлоу | Изменение |
|---|---|
| BRD-00 | Таймауты и исчерпанный лимит ключа уходят в ручную очередь |
| BRD-03, BRD-04 | Модель через OpenRouter, потолки шагов, денег, времени и артефактов, заморозка сессии |
| BRD-05 | Сторож зависшей генерации |
| BRD-06 | Расход ключа OpenRouter и остановленные сессии в дайджесте |
| **BRD-07** (новый) | Ручная очередь |

BRD-01 и BRD-02 по логике не менялись, их переимпортировать не нужно.

---

## Шаг 1. Ключ OpenRouter с лимитом

1. На openrouter.ai: **Credits → Add credits**. Достаточно $10: OpenRouter берёт небольшую комиссию при пополнении, а расходоваться будет не больше лимита ключа.
2. **Keys → Create key**:
   - Name: `brd-agent-n8n`;
   - **Credit limit: `5`**;
   - **Reset limit: `Weekly`** (сброс по понедельникам, 00:00 UTC).

   *Зачем:* это и есть денежный потолок агента. В n8n денежного лимита внутри сценария нет: что разрешает ключ, то может и агент. Число $5 обосновано в control-layer.md.
3. Скопируйте ключ (OpenRouter показывает его один раз).
4. **Сделайте снимок экрана с настройками ключа** (лимит и сброс). Он нужен для сдачи по пункту 2.

## Шаг 2. Credentials в n8n

*Overview → Credentials → Create*:

| Credential | Тип | Настройка |
|---|---|---|
| `OpenRouter (key with limit)` | **OpenRouter** | API Key — ключ из шага 1, без префикса `Bearer` |
| `OpenRouter API key (header)` | **Header Auth** | Name `Authorization`, Value `Bearer <ключ из шага 1>` |

*Зачем:* первая credential нужна моделям агентов. Вторая нужна дайджесту: он читает расход и остаток лимита через `GET /api/v1/key`.

Кнопка **Test** у credential «OpenRouter» обращается к `GET /api/v1/key`. «Unauthorized» значит, что OpenRouter не принял ключ: ключ скопирован не целиком, вставлен с `Bearer`, это provisioning key вместо обычного API-ключа или ключ выключен.

ID каждой credential (из URL открытой credential) впишите в `.env`: `CRED_OPENROUTER` и `CRED_HTTP_OPENROUTER`. Если оставить пустыми, credentials можно будет выбрать в узлах вручную (шаг 7). Старая credential DeepSeek больше не используется, её можно оставить.

## Шаг 3. Лист `manual_queue`

В таблице **«BRD agent — logs»** добавьте лист `manual_queue` и вставьте в первую строку заголовки из `sheets-templates/logs__manual_queue.csv`:

```
queued_at,queue_id,limit_type,limit_name,limit_value,observed,workflow,session_id,chat_id,customer,details,execution_url,outcome,assignee,status,resolution,resolved_at
```

*Зачем:* это и есть ручная очередь, куда уходит любой прогон, упёршийся в потолок. Без заголовков запись падает с `Could not retrieve the column data`.

## Шаг 4. Новые колонки в Data Table `brd_sessions`

*Overview → Data tables → brd_sessions → добавить колонки*:

| Колонка | Тип | Что хранит |
|---|---|---|
| `cost_usd` | Number | Накопленный расход сессии (оценка), для бюджета $1 |
| `limit_hit` | String | Какой потолок сработал |
| `brd_attempts` | Number | Число попыток генерации BRD |
| `brd_created_at` | String | Когда создан BRD, для потолков «BRD за 24 ч» |

*Зачем:* без этих колонок сохранение сессии упадёт. BRD-99 таблицу не пересоздаёт, если она уже есть, поэтому колонки добавляются вручную.

## Шаг 5. Пустой воркфлоу BRD-07

1. В n8n: **Create workflow**, назовите `BRD-07 Manual Queue (limits)`, сохраните.
2. Скопируйте ID из URL (`…/workflow/<ID>`) и впишите в `.env`: `WF_QUEUE_ID=<ID>`.

*Зачем:* BRD-00, 03, 04 и 05 вызывают BRD-07 по ID. При ручном импорте ID должен быть известен заранее.

Проверьте ещё, что в `.env` задан `CFG_N8N_BASE_URL=https://<ваш-инстанс>.app.n8n.cloud`. Тогда в очереди и алертах будут ссылки на executions.

## Шаг 6. Сборка файлов для импорта

```bash
python3 scripts/check_workflows.py      # проверка: связи, JS, плейсхолдеры
python3 scripts/deploy.py --dry-run     # подставляет .env → build/*.json
```

В конце `--dry-run` не должно остаться незаполненных `__WF_…__`. Незаполненные `__CRED_…__` допустимы, их выберете в UI.

## Шаг 7. Импорт

Порядок важен: сначала подворкфлоу, на которые ссылаются остальные.

Для каждого воркфлоу из списка:
1. Откройте его в n8n.
2. Выделите всё (**Cmd+A**) → **Delete**.
3. Выполните **⋯ → Import from File** → `build/<файл>.json`.
4. Проверьте пункты из таблицы ниже.
5. Нажмите **Save** и **Publish**.

| # | Воркфлоу | Файл | Проверить после импорта |
|---|---|---|---|
| 1 | BRD-07 Manual Queue (limits) | `BRD-07_manual-queue.json` | credential Google Sheets в *Append to manual_queue*; Settings → Error workflow = BRD-00 |
| 2 | BRD-00 Errors & Support Alerts | `BRD-00_errors-and-alerts.json` | credentials Sheets и Telegram |
| 3 | BRD-04 BRD Generator & Publisher | `BRD-04_brd-generator.json` | *LLM (BRD)* → credential OpenRouter, модель `deepseek/deepseek-v4.1-flash`; Settings → **Timeout Workflow: выключен** (тариф разрешает максимум 3 минуты, а генерация идёт 3–5 минут; потолок времени держит сторож в BRD-05); Error workflow = BRD-00 |
| 4 | BRD-03 Interview Agent | `BRD-03_interview-agent.json` | *LLM (interview)* → credential OpenRouter; в *Interview agent* → Options: **Max Iterations = 4**, **Return Intermediate Steps = on**; Settings → **Timeout Workflow: вкл, 3 мин** (это максимум тарифа); Error workflow = BRD-00 |
| 5 | BRD-05 Reminders | `BRD-05_reminders.json` | Error workflow = BRD-00 |
| 6 | BRD-06 Daily Digest | `BRD-06_daily-digest.json` | *OpenRouter key usage* → credential Header Auth OpenRouter; Error workflow = BRD-00 |

*Зачем выставлять Settings вручную:* на n8n Cloud при импорте из файла в существующий воркфлоу **настройки воркфлоу не переносятся**, переносятся только узлы и связи. Это проверено на BRD-07. Таймаут и Error workflow в JSON есть (`settings.executionTimeout`, `settings.errorWorkflow`), но в n8n их нужно поставить руками. Воркфлоу, которые уже были настроены раньше, сохраняют свой прежний Error workflow, а новый таймаут всё равно ставится вручную. В n8n 2.x подворкфлоу и Error workflow работают только опубликованными, поэтому после каждой правки BRD-07, 00 и 04 их нужно публиковать заново.

## Шаг 8. Проверка

1. **BRD-07 → Manual test.** Ожидаемо: строка в `manual_queue` и предупреждение «Остановлено по пределу → ручная очередь» в чате команды. Тестовую строку пометьте `resolved`.
2. **Обычный диалог с ботом** (`/new` и пара ответов). Ожидаемо: бот отвечает как раньше; в `brd_sessions` растёт `cost_usd`; в OpenRouter → Activity видны вызовы `deepseek/deepseek-v4.1-flash`; у execution BRD-03 стоит `outcome = done`.
3. **BRD-06 → Manual run.** Ожидаемо: в дайджесте есть строка «OpenRouter: расход за сутки … лимит ключа $5.00 (weekly) …».
4. Дальше — тест [cost-attack.md](cost-attack.md).

## Как команде разбирать очередь

Строка в `manual_queue` со статусом `new` означает прогон, остановленный по пределу. Решения:

- **сформировать BRD по собранному:** BRD-04 → *Manual test* → в *Test input* вставить `session_id`;
- **дать продолжить:** в `brd_sessions` поставить `status = active` (потолки при этом остаются в силе);
- **закрыть:** поставить `status = cancelled`.

После решения в строке очереди проставьте `status = resolved`, `resolution` и `resolved_at`. Пока сессия в `needs_review`, бот не начинает этому заказчику новую беседу: повтор после предела решает человек, а не бот.
