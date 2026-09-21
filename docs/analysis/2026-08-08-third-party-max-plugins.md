# Аудит существующих MAX-плагинов для Hermes

Дата проверки: 2026-08-08.

Цель проверки: выяснить, есть ли готовая реализация, которую можно безопасно
подключить к текущему upgrade-safe плагину MAX для Hermes, и отделить полезные
эксплуатационные решения от кода, уже устаревшего относительно MAX Bot API v2.

## Проверенные источники и фиксированные версии

| Источник | Зафиксированная версия | Результат проверки |
|---|---|---|
| [MaZzZilka/hermes-max-plugin](https://github.com/MaZzZilka/hermes-max-plugin) | `9e586ad58c39a10b3cf1119e079b00a20946b145` (`master`) | Компилируется; один initial commit, 4 runtime-файла |
| [pavel_botolog/max_hermes_agent_ru](https://gitverse.ru/pavel_botolog/max_hermes_agent_ru) | `c5200d806b19d850267af7e6f218ec7e7cf7afe5` (`init-max-hermes-agent`, также `claude/eloquent-pasteur-ucnovn`) | 126 unit-тестов обнаружены; в нашем окружении 109 passed, 7 failed, 10 errors |
| [MAX API: загрузка медиа](https://dev.max.ru/docs-api/methods/POST/uploads) | текущая официальная документация | Используется `platform-api2.max.ru`, `/uploads?type=...`, token-based attachments |
| [MAX API: отправка сообщений](https://dev.max.ru/docs-api/methods/POST/messages) | текущая официальная документация | `POST /messages`, лимит текста 4000, attachments через `payload.token` или URL для image |

Оба репозитория были клонированы по указанным HEAD в локальный каталог
`.third-party-analysis/`; рабочий плагин Hermes при этом не изменялся.

## Репозиторий MaZzZilka

### Что в нём ценно

- Минимальная форма Hermes platform plugin: `plugin.yaml`, `__init__.py`,
  `register()` и `BasePlatformAdapter`.
- Базовый polling-loop, дедупликация message id и отправка чанков текста.
- Отдельные методы для image/file/voice/video, то есть автор учитывал медиа-
  контракт Hermes.
- Лицензия MIT и понятный маленький объём для изучения.

### Почему нельзя переносить код как есть

- README и код жёстко используют `https://platform-api.max.ru`; актуальная
  документация требует `platform-api2.max.ru` и заголовок `Authorization`.
- Используется устаревший `POST /upload`, multipart-поле `file`, ответ
  `file_id` и `payload.file_id`. Текущий API использует `POST /uploads?type=...`,
  поле `data` и `payload.token`; значение `photo` также больше не поддерживается.
- DM отправляются через `chat_id`, хотя для диалога MAX документирует
  `user_id`; это уже проявилось в нашем live-пилоте как `404 Dialog not found`.
- `message_callback` фактически не реализован как Hermes approval/clarify
  callback: в коде создаётся обычное текстовое событие `/callback ...`.
- Нет allowlist-политики, persistent marker, webhook ingress, bounded queue,
  typed API errors, retry classification, TLS CA configuration и тестового
  набора. README заявляет inline keyboards, но в runtime-коде нет builder-а
  и обработки callback payload.
- Нет отдельного `standalone_sender_fn`, поэтому cron/внешняя доставка не
  покрыта тем же контрактом, что и live gateway.

Вывод: это полезный эскиз API-адаптера и источник названий медиа-методов, но
не источник кода для production-интеграции.

## Репозиторий pavel_botolog

### Что в нём ценно

- Более полный operational слой: persistent polling marker с атомарной записью,
  in-memory dedup с TTL, exponential backoff и безопасное маскирование token/id
  в логах.
- Явная маршрутизация `user:<id>` и `chat:<id>` с правильным выбором
  `user_id`/`chat_id` для исходящего запроса. Это хороший regression-case.
- Реализация `standalone_sender_fn` для cron и cross-platform send tool.
- Fallback progress append через `edit_message`, rate-limit guard для прогресса,
  probe токена через `/me`, allowlist и подробные security/installation docs.
- Хорошая практика: исходный Hermes core не патчится, секреты не кладутся в
  репозиторий, есть `check_secrets.sh`, rollback-документация и отдельные тесты.
- Полезные идеи для отдельного продукта: role registry, ephemeral role prompt,
  dry-run/confirm/rollback для пользовательских профилей.

### Ограничения и найденные проблемы

- `MAX_API_BASE_DEFAULT` и примеры всё ещё указывают на старый
  `platform-api.max.ru`; `verify_max_token.py`, `MAX_BOT_SETUP.md` и troubleshooting
  это повторяют.
- `media_files` в standalone sender намеренно логируются и отбрасываются.
  В `MessageEvent` вложения только обнаруживаются; локальная загрузка медиа не
  реализована.
- `message_callback` не переводится в официальный `POST /answers` и не
  связывается с одноразовым, user/chat-bound callback token. Следовательно,
  approval/clarify/model-picker из Hermes через нативные кнопки не покрыты.
- Значительная часть adapter.py содержит исторические F-этапы и adapter-level
  команды/роли/team-manager. Это расширяет blast radius, зависит от локальных
  путей профилей и не является базовым MAX transport.
- В финальном HEAD тесты запускаются с жёсткими путями `/home/xidden/.hermes`
  и `/root/.hermes`. В нашем WSL окружении запуск дал `109 passed`, `7 failed`,
  `10 errors`: часть ошибок вызвана отсутствием этих путей/прав, часть
  проверок role profile получает `None`, а не рабочий переносимый fixture.
  Это не доказательство, что проект не работает в авторском окружении, но это
  доказательство, что тесты не являются переносимым release gate без изоляции.
- `check_secrets.sh` в клонированном состоянии прошёл, а оба исходных проекта
  не дают доказательства live callback/media acceptance против текущего API.

Вывод: это более полезный источник operational patterns и документации, но его
нельзя ставить поверх текущего плагина. Его role/team слой следует рассматривать
как отдельную optional feature после оценки безопасности и совместимости с
конкретной версией Hermes.

## Сопоставление с текущим плагином

| Область | Готовые репозитории | Текущий плагин |
|---|---|---|
| API/TLS | старый base URL, простые HTTP calls | `platform-api2.max.ru`, typed errors, CA bundle |
| DM/group routing | полезные route tags только в GitVerse | normalized `MaxMessage`, target store, user/chat routing |
| Polling | marker/dedup/backoff в GitVerse | SQLite marker, bounded polling/retry |
| Webhook | нет рабочего production ingress | secret validation, durable inbox, bounded queue |
| Allowlist | GitVerse частично | DM/group policy и command policy |
| Callbacks | заявлены или превращены в текст | `/answers`, opaque single-use user/chat-bound callbacks |
| `/model` | текстовый passthrough | provider/model inline picker через Hermes callback |
| Медиа | GitHub устаревшая outbound-заготовка; GitVerse не отправляет | следующий слой: current `/uploads` token flow и входящие cache |
| Cron/standalone | реализовано в GitVerse | standalone sender уже есть |
| Core isolation | заявлена | plugin symlink, Hermes checkout не изменяется |

## Решение по повторному использованию

Прямой перенос файлов или cherry-pick не делаем: оба проекта используют
неактуальный media/API-контракт либо более узкую архитектуру и не проходят
наши upgrade-safety и live-evidence критерии.

Забираем как проверенные design patterns:

1. route-tagged IDs и regression-тесты для DM/group;
2. persistent marker, bounded retry, dedup и безопасное маскирование логов;
3. standalone sender для cron;
4. progress fallback и документацию по secret scanning/rollback;
5. идею optional role registry, но не включаем её в базовый transport.

Не забираем:

1. старый `/upload`, `file_id` и `platform-api.max.ru`;
2. самодельный callback через текстовое `/callback`;
3. team-manager, который создаёт профили и меняет registry из мессенджера;
4. необоснованные заявления о поддержке media/кнопок без live acceptance.

## Следствие для плана

На момент аудита следующим шагом оставался media. Он уже реализован в текущем
плагине по официальному current API: upload URL через `/uploads?type=...`, поле
`data`, token в `payload.token`, bounded download только с разрешённых HTTPS
host-ов и сохранение через Hermes media cache. Локальные contract-тесты проходят;
остаётся disposable-bot/live acceptance для image, audio, video и file.

Это сохраняет исходное требование: Hermes Agent обновляется отдельно, а MAX
адаптер живёт во внешнем репозитории и проверяется против версии Hermes,
которая реально установлена.
