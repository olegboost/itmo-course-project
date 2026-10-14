# Обновление до паттерна «sequential с валидатором» (improvement-2): ручной импорт

Что и зачем меняется, описано в [pattern-and-economics.md](pattern-and-economics.md). Меняются **BRD-01** (база знаний без файлов из корзины) и **BRD-04** (паттерн, две ветки сбоя, безопасная публикация), добавляется новый **BRD-98** (тестовые входы). Остальные воркфлоу не трогаем.

## Шаг 0. BRD-01: убрать дубли из базы знаний

Это замечание ревьювера №2: `.docx` из корзины Drive индексировались вторым экземпляром.

Переимпортируйте BRD-01 из `build/BRD-01_kb-sync.json`, затем Save и Publish, затем **Manual run**. Ожидаемо: в отчёте «удалённых: 12» (`.docx` из корзины), в `kb_sync_log` строки `deleted`, в Pinecone 85 записей вместо 170. Корзину Drive после этого можно очистить.

Делайте это **до** серии прогонов: писатель получает 6 фрагментов на запрос, и с дублями часть из них повторяет друг друга.

## Шаг 1. Лист `brd_runs`

В таблице «BRD agent — logs» добавьте лист `brd_runs` с заголовками из `sheets-templates/logs__brd_runs.csv`:

```
finished_at,session_id,chat_id,title,outcome,limit_type,validator_first,violations_first,repaired,repair_rounds,repair_actions,validator_final,violations_final,from_draft,found_existing_doc,kb_calls,kb_queries,writer_model,fixer_model,run_cost_usd,cost_estimate_usd,doc_url,error,execution_url
```

### Колонки в Data Table `brd_sessions`

Добавьте две колонки, обе типа **String**:
- `brd_draft` — готовый, но не опубликованный текст BRD (если упала загрузка в Drive);
- `brd_draft_title` — его название.

*Зачем:* при техническом сбое публикации повторный `/done` опубликует сохранённый текст, а не будет генерировать документ заново.

*Зачем:* это журнал прогонов паттерна. Каждый прогон BRD-04 (успешный, остановленный или упавший) пишет сюда строку: вердикт валидатора, было ли исправление, какие запросы к базе знаний сделал агент и **фактическую цену** из OpenRouter. Из этого листа заполняется таблица пяти прогонов.

## Шаг 2. `.env` и сборка

Новые переменные я уже дописал в конец вашего `.env`:

```
CFG_WRITER_MODEL=deepseek/deepseek-v4-pro-0813
CFG_WRITER_PRICE_IN=0.66
CFG_WRITER_PRICE_OUT=1.98
```

Затем:

```bash
python3 scripts/check_workflows.py
python3 scripts/deploy.py --dry-run
```

## Шаг 3. Импорт BRD-04

Как и раньше: открыть BRD-04 → Cmd+A → Delete → **⋯ → Import from File** → `build/BRD-04_brd-generator.json`.

Что проверить после импорта:
- **LLM (writer)** и **LLM (writer, regen)**: credential OpenRouter, модель `deepseek/deepseek-v4-pro-0813`. *LLM (writer)* — новое название узла, раньше он назывался *LLM (BRD)*.
- **LLM (fixer)**: credential OpenRouter, модель `deepseek/deepseek-v4.1-flash`.
- **KnowledgeBase (regen)** и **Embeddings (regen)**: те же credentials Pinecone и cloud.ru, что у основных.
- **Upload to Drive as Google Doc**, **Find uploaded doc**, **Retry upload (once)**: credential Google Drive OAuth2 (ваш собственный OAuth-клиент). У *Upload…* в Settings узла должно быть **Retry On Fail выключено**: так задумано.
- **Key usage (before)** и **Key usage (after)**: credential Header Auth «OpenRouter API key (header)».
- **Append to brd_runs**, **Register in BRD registry**: credential Google Sheets.

На холсте должна быть видна петля: *Revalidate* → *Quality gate*. Это раунды исправления, не ошибка.
- **Settings**: Timeout Workflow выключен, Error workflow = BRD-00. Импорт настройки не переносит, они должны остаться прежними.

Затем **Save → Publish**.

## Шаг 4. BRD-98 (новый)

**Create workflow** → **⋯ → Import from File** → `build/BRD-98_eval-seed.json` → Settings → Error workflow = BRD-00 → Save. Публиковать не нужно: он запускается только вручную.

## Шаг 5. Проектный прогон на топовой модели (по желанию)

Принцип «проектируем на топовой, запускаем на лёгкой» можно подтвердить одним прогоном:

1. В BRD-04 в узле *LLM (writer)* поставьте `anthropic/claude-opus-5.5`, в *Config* — `WRITER_PRICE_IN = 4`, `WRITER_PRICE_OUT = 20`. Затем Save и Publish.
2. BRD-98 → **Execute** (запишутся 5 тестовых сессий), возьмите `session_id` от `eval-1`, затем BRD-04 → Test input → **Manual test**.
3. Посмотрите BRD и строку `brd_runs`. Сравнение с v4-pro пойдёт в обоснование модели писателя.
4. Верните `deepseek/deepseek-v4-pro-0813` и цены 0.66 / 1.98 (или переимпортируйте BRD-04).

⚠️ **Проектный прогон тоже считается в потолке «5 BRD за 24 часа».** Если сделать его в тот же день, пятый прогон серии остановится по пределу `artifacts_day`. Поэтому делайте его не меньше чем за 24 часа до серии, либо пропустите.

## Шаг 6. Серия из пяти прогонов

**Перед серией:**
- проверьте, что лимит ключа OpenRouter сбросился (понедельник, 03:00 МСК) или что на ключе осталось хотя бы ~$1.5;
- не пишите боту, пока идёт серия: цена прогона считается по разнице расхода ключа, и чужие вызовы попадут в неё.

**Порядок:**
1. **BRD-98 → Execute.** В выходе узла *Session IDs for BRD-04* будут 5 `session_id` (`eval-1-…` … `eval-5-…`). Если делали проектный прогон, запустите BRD-98 ещё раз, чтобы серия получила свежие сессии.
2. Для каждого из 5 по очереди: **BRD-04 → Test input** = `session_id` → **Manual test**. Дождитесь окончания: 3–6 минут плюс 15 секунд ожидания перед замером цены.
3. После каждого прогона появятся строка в `brd_runs` и сообщение «Новый BRD (тестовый прогон)» в чате команды. Если прогон остановился по пределу, будет строка в `manual_queue`.
4. Откройте каждый опубликованный BRD и сверьте его с колонкой «ожидаемый результат» в [pattern-and-economics.md](pattern-and-economics.md). Ответ «успешно: да/нет» — строго по записанному критерию.

Пришлите мне строки `brd_runs` (можно скриншотом) и ваши «да/нет» по каждому прогону. Я заполню таблицу результатов и посчитаю стоимость успешного результата.

## Что сохранить для сдачи

- экспорт `workflows/BRD-04_brd-generator.json`: паттерн и инструмент у агента;
- скриншот BRD-04 на холсте: видна цепочка «писатель → валидатор → исправление → повторная проверка»;
- скриншот листа `brd_runs` после серии: колонки `kb_queries` и `run_cost_usd`;
- скриншот execution одного прогона, где виден `intermediateSteps` писателя.
