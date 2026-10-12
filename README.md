# Передача всех сервисов Render

Снимок конфигурации: **12 октября 2026**, прочитан из Render API. Production не изменялся. Все исходники в `source/` публичные; значения секретов, базы клиентов и исторические ключи не входят в репозиторий.

| Сервис | Исходники | Как работал на Render | Данные |
|---|---|---|---|
| iiko / Финдиректор | [source/iiko](source/iiko) | Python, `deploy-cashshifts`, Frankfurt; uvicorn api:app | отдельная SQLite на диске `/data`, 1 GB |
| iiko-demo | [source/iiko-demo](source/iiko-demo) | Python, `feat/sched-old`, Frankfurt; отдельный процесс | отдельная SQLite на другом диске `/data`, 1 GB |
| saya-kds | [source/saya-kds](source/saya-kds) | Docker, `phase1-backend-ingest`, Frankfurt; React + FastAPI + Socket.IO | Render PostgreSQL 16 `saya-kds-db` |
| reading-platform | [source/reading-platform](source/reading-platform) | Node / Next.js, `main`, Frankfurt | внешняя Supabase; не Render PostgreSQL |
| orientir | [source/orientir](source/orientir) | static site, `main`, publish `.`; сейчас suspended | отдельной базы в инвентаризации не обнаружено |

## Начать отсюда

1. [Точные команды, ветки, адреса и диски](render/services.json).
2. [Коммиты реально работающих версий](render/live-commits.json). Это снимки live-деплоев, а не предположение по текущему HEAD.
3. `render/*.env.example` — все имена переменных из Render. Значения перенести через защищённый канал с владельцем. Заполненные env-файлы и базы **не публиковать**.
4. [Порядок переноса и проверки](MIGRATION.md).
5. [Политика конфиденциальности Финдиректора](docs/privacy.html) и [что ещё требует подтверждения](docs/PRIVACY_IMPLEMENTATION.md).
6. [Какие файлы очищены](SANITIZATION.json). Оригинальная история iiko не публикуется: в ней обнаружены ключи Telegram и Anthropic. В опубликованной копии ключи 2ГИС вынесены в env, личные ссылки и внутренние снимки исключены; исходные репозитории и запущенные сервисы не переписывались.

## Важные зависимости

- iiko обращается к iiko Cloud / RMS, Telegram, Anthropic, площадкам отзывов и к `saya-kds` через `CHASPIK_BASE_URL` и `ANALYTICS_API_KEY`.
- saya-kds содержит polling, отчёты Telegram, обработку вебхуков, предзаказы и Socket.IO внутри web-процесса. Два одновременно активных экземпляра при переключении могут повторно обрабатывать события. Переносить с контролируемым отключением старого процесса; endpoint вебхуков менять только после проверки новой среды.
- reading-platform использует Supabase service_role только на сервере. Нужно сохранить схему, RLS, функции, данные, проектные настройки; одного переноса Node недостаточно.
- Шифрование паролей iiko зависит от прежнего `ENCRYPTION_KEY`: при переносе существующей SQLite его **нельзя просто заменить новым**.
- Связи Telegram BotFather / webhooks, iiko webhook URLs и API_URL в мобильных сборках обновляются отдельно. Это не сделано автоматически этим репозиторием.

Нет новых подтверждённых параметров сервера или целевой базы: их выбирает принимающий специалист. Этот комплект не заявляет, что перенос выполнен, PostgreSQL заменяет SQLite без переделки запросов или приложения уже готовы к выпуску.
