# Подключение MAX к Hermes Agent

Это инструкция для текущего внешнего плагина. Плагин не обещает универсальную
доступность MAX при любых региональных ограничениях связи: результат зависит
от региона, оператора, устройства и режима ограничения.

## 1. Создание бота

Используйте актуальный процесс MAX для бизнеса и получите Bot API token.
Старые инструкции про `@MasterBot` и `/newbot` намеренно не используются:
правила регистрации и верификации MAX менялись.

Официальные страницы:

- https://dev.max.ru/docs/chatbots/bots-coding/prepare
- https://dev.max.ru/docs-api

## 2. Установка без изменения Hermes core

Репозиторий плагина должен оставаться отдельным от Hermes:

```bash
python -m pip install -e ".[dev]"
ln -s "/path/to/hermes-agent-ru-messengers/plugins/max" "$HOME/.hermes/plugins/max"
```

В Windows вместо `ln -s` используйте junction или каталог-копию. Внутри
установленного каталога должен быть файл `plugin.yaml` в нижнем регистре.
Файлы Hermes `gateway/`, `agent/` и `hermes_cli/` изменять не нужно.

## 3. Минимальная конфигурация

```env
MAX_BOT_TOKEN=<token MAX для бизнеса>
MAX_ALLOWED_USERS=<числовые user_id через запятую>
# Для групп нужны одновременно разрешённые пользователи и чаты.
MAX_GROUP_ALLOWED_USERS=<user_id через запятую>
MAX_GROUP_ALLOWED_CHATS=<chat_id через запятую>
MAX_ADMIN_USERS=<администраторы групп через запятую>
# Необязательно: максимум 50 MiB на одно входящее/исходящее вложение.
MAX_MEDIA_MAX_BYTES=52428800
```

Если `MAX_WEBHOOK_URL` не задан, используется Long Polling. Это режим для
разработки и smoke-тестов. Для production MAX рекомендует Webhook.

## 4. TLS и сертификаты

Актуальный API: `https://platform-api2.max.ru`.

Если в системном хранилище нет нужной цепочки, задайте путь к проверенному PEM
bundle:

```env
MAX_CA_BUNDLE=/etc/hermes/max-ca-bundle.pem
```

Плагин сохраняет системные корни ОС и добавляет сертификаты из указанного
bundle. Поэтому bundle может содержать только актуальную доверенную цепочку
MAX; нельзя исправлять проблему через `verify=False`.

## 5. Webhook

```env
MAX_WEBHOOK_URL=https://example.ru/hermes/max
MAX_WEBHOOK_SECRET=<5-256 символов: латиница, цифры, _ или ->
```

MAX требует HTTPS на порту 443, доверенный сертификат и HTTP 200 не позднее
30 секунд. Плагин предоставляет `MaxAdapter.handle_webhook()`, проверку
`X-Max-Bot-Api-Secret`, bounded queue и дедупликацию. В текущем MVP публичный
HTTP listener внутри Hermes не запускается: нужен отдельный ASGI/reverse-proxy
ingress, который передаст запрос в этот метод. В репозитории есть готовый
отдельный процесс:

```powershell
python -m pip install -e ".[webhook]"
python scripts/max_webhook_server.py
```

`MAX_INBOX_PATH` должен указывать на один и тот же SQLite-файл у ingress и
Hermes. Публичный HTTPS/443 и TLS termination остаются ответственностью
reverse proxy. Подробности: `docs/ops/max-upgrade-safe.md` и
`docs/ops/max-certificates.md`.

## 6. Политика доступа

По умолчанию доступ закрыт:

- `MAX_ALLOWED_USERS` — разрешённые DM и группы;
- `MAX_GROUP_ALLOWED_USERS` — пользователи только для групп;
- `MAX_GROUP_ALLOWED_CHATS` — разрешённые group chat ID;
- `MAX_ADMIN_USERS` — пользователи, которым разрешены approval и управляющие
  действия в группах;
- `MAX_ALLOW_ALL_USERS=true` — только временная разработческая настройка.

Доступ в группе проверяется как логическое «И»: отправитель должен быть в
пользовательском allowlist, а чат — в `MAX_GROUP_ALLOWED_CHATS`. В группах
контекст Hermes разделяется по паре `chat_id + user_id`; физическая доставка
при этом возвращается в исходный MAX chat ID.

Важно: текущий глобальный Hermes registry сначала применяет
`MAX_ALLOWED_USERS`. Поэтому пользователи, которым разрешён доступ только в
группе, всё равно должны присутствовать в глобальном allowlist; групповые
переменные дополнительно сужают решение внутри MAX-плагина, а не обходят
глобальную проверку Hermes.

Не включайте `MAX_ALLOW_ALL_USERS` для публичного бота.

## 7. Что сейчас реализовано

- текстовые DM и закрытые group routing с раздельными сессиями участников;
- нормализация `body.mid` и `recipient.chat_type`;
- chunking до 4000 символов;
- `Authorization` и API v2;
- Long Polling с marker;
- Webhook secret, ACK decision, bounded queue и dedup;
- Hermes plugin contract, YAML hook и standalone sender;
- входящие image/audio/video/file через локальный Hermes media cache, включая
  разрешение video-token через `GET /videos/{token}`;
- исходящие изображения, документы, audio/voice, video, animation, `MEDIA:` и
  standalone/cron через общий `/uploads` → multipart `data` → message поток;
- несколько вложений, кириллические имена и `[[as_document]]`; изображения и
  видео группируются до 12 вложений, файлы и аудио отправляются отдельными
  совместимыми сообщениями; ошибка одного вложения не скрывает остальные;
- меню до 32 подтверждённых Hermes-команд через `PATCH /me/commands`, `/menu`,
  `/start`, `/commands`, `/maxstatus` и inline-кнопки; список команд также
  дублируется текстом, если клиент MAX не показывает системное меню;
- typing/typing_off, durable polling inbox и состояния pending/processing/
  processed/failed без автоматического повтора неоднозначного события;
- ограничение размера и проверка официальных HTTPS media-hosts;
- безопасная TLS-политика.

### Команды MAX

Плагин регистрирует до 32 команд через `PATCH /me/commands`. Фактический
набор зависит от командного реестра установленной версии Hermes. На текущей
проверенной установке доступны:

```text
/menu /commands /help /status /new /stop /model /compress
/sessions /resume /retry /undo /agents /whoami /queue /maxstatus
```

Если приложение MAX не показывает системное меню бота, отправьте `/commands`
или `/menu`: плагин вернёт тот же список обычным текстом и добавит кнопки.
После перезапуска gateway в журнале должна появиться строка вида
`MAX command menu registered (N): ...`.

Для проверки регистрации без polling и без запуска модели:

```bash
python scripts/max_commands_live_smoke.py
```

Скрипт выполняет только `GET /me`, `PATCH /me/commands` и повторный `GET /me`;
токен и содержимое переписки он не печатает. Запускать его следует при одном
единственном владельце polling, чтобы не смешивать диагностический вызов с
обработкой событий.

Не считать доказанными только по unit/loader тестам: реальную доставку медиа
на телефон, чтение содержимого моделью, живой групповой callback и полевой
тест без VPN. Эти проверки требуют disposable/test bot либо согласованного
окна с единственным polling-потребителем.

## 8. Проверка и откат

```bash
python -m pytest -q
```

Для live-проверки задайте `MAX_BOT_TOKEN` и `MAX_CA_BUNDLE` только в окружении
процесса и запустите `scripts/max_live_smoke.py` без `--poll-seconds`, если
gateway уже работает. Для исходящей медиа-проверки используйте
`scripts/max_media_live_smoke.py`; для обезличенной диагностики upload-ответа —
`scripts/max_upload_live_inspect.py`. Проверка
`scripts/max_adapter_live_smoke.py` использует временный SQLite и collector
вместо вызова модели Hermes; активный gateway не запускается и не
перезапускается.

Перед рестартом gateway выполните loader smoke-тест на текущей версии Hermes.
Для отката остановите gateway и удалите или перенаправьте только
`~/.hermes/plugins/max`. Файлы Hermes core при установке плагина не меняются.
