# Эксплуатация: kill-switch, стадии rollout, BRD-08 — что сделать в n8n

Что и зачем — [operations.md](operations.md). Для демо и защиты ничего включать не нужно: по умолчанию
`CFG_MAINTENANCE=false`, `CFG_ROLLOUT_STAGE=autonomous` — система работает как раньше.

1. **Папка проверки.** В Google Drive создайте папку «BRD на проверке» (доступ — только команде AIOps) и впишите её ID
   в `.env`: `CFG_REVIEW_FOLDER_ID=<ID>`. Нужна для стадий shadow и hitl.
2. **Лист `brd_runs`.** Добавьте колонку `rollout_stage` после `limit_type` (заголовки — `sheets-templates/logs__brd_runs.csv`).
3. **Сборка:** `python3 scripts/check_workflows.py` и `python3 scripts/deploy.py --dry-run`.
4. **Импорт** (открыть → Cmd+A → Delete → Import from File → Save → **Publish**):
   - **BRD-03** — kill-switch (`MAINTENANCE` в *Config*, ветка *Maintenance reply*);
   - **BRD-04** — стадии rollout (`ROLLOUT_STAGE`, `REVIEW_FOLDER_ID` в *Config*);
   - **BRD-06** — в дайджесте BRD, ждущие утверждения;
   - **BRD-08** (новый) — Create workflow → Import → credentials Google Drive (*Move to wiki folder*) → Settings →
     Error workflow = BRD-00 → Save. Публиковать не нужно: запускает проверяющий вручную.
5. **Проверка механизмов отката** — чек-лист в конце [operations.md](operations.md). Его стоит пройти до пилота;
   для защиты достаточно показать, что механизмы есть в экспорте и описаны.

Переключение без переимпорта: kill-switch и стадия — поля в узлах *Config* BRD-03 и BRD-04 (Publish после правки).
При следующем импорте значения перезапишутся из `.env` — держите `.env` в том же состоянии.
