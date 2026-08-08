# MAX как канал Hermes Agent: анализ текущего состояния

Дата среза: 2026-08-08, Europe/Moscow.

## 1. Решение на текущем этапе

**Первым каналом следует делать MAX.** Это решение основано не на утверждении, что MAX уже лучше Telegram или VK по зрелости API, а на целевом ограничении проекта: сохранить связь в России при временных ограничениях мобильного интернета и без VPN. MAX и сервисы VK находятся в российском контуре сервисов, которые упоминаются в материалах о «белом списке», но сам факт наличия сервиса в таком списке не является гарантией доставки каждого Bot API-запроса во всех регионах и у всех операторов.

**VK нужно оставить вторым каналом на общей модели адаптера.** У VK зрелее экосистема и исторически больше интеграций, но для поставленной задачи MAX является более актуальным направлением: его Bot API активно меняется, у него уже есть официальный TypeScript SDK и есть работающий внешний плагин OpenClaw. Это сокращает риск начинать с чистого листа, но повышает требования к контролю изменений API.

**Текущий прототип не следует считать готовым плагином.** Его 29 тестов проходят, однако аудит показал несовместимости с текущими правилами плагинов Hermes и с актуальным API MAX. Реализацию нужно продолжать как внешний плагин, не изменяя грязное рабочее дерево Hermes core.

## 2. Что подтверждено, а что пока нельзя утверждать

### Подтверждено

- В актуальной документации Hermes рекомендован внешний plugin adapter: каталог `~/.hermes/plugins/<name>/`, файл `plugin.yaml`, `adapter.py`, регистрация через `register(ctx)`.
- Hermes adapter должен использовать `BasePlatformAdapter`, `self.build_source(...)` и `self.handle_message(event)`. Текущий контракт включает `connect(*, is_reconnect: bool = False)`.
- MAX Bot API v2 использует `https://platform-api2.max.ru`, токен передается в заголовке `Authorization`.
- MAX поддерживает Webhook и Long Polling. Документация рекомендует Webhook для production; Long Polling оставляет для разработки и тестов.
- Для Webhook нужны HTTPS, доверенный сертификат и порт 443; с 25 мая 2026 MAX прекратил поддержку HTTP и самоподписанных сертификатов.
- Входящие события включают `bot_started`, `message_created`, `message_callback`, изменения и удаления сообщений, а также события жизненного цикла бота.
- Текст сообщения ограничен 4000 символами. Есть Markdown/HTML, кнопки и callback-события. Медиа загружаются через `/uploads`, после чего передаются как attachment.
- Один бот не должен одновременно использовать Webhook и Long Polling. Webhook может быть отписан MAX после длительного отсутствия успешного ответа, поэтому регистрацию нужно проверять и восстанавливать при старте.
- В текущем WSL-окружении `platform-api2.max.ru` разрешается в DNS и отвечает `401` при запросе без токена. Обычная проверка TLS сейчас не проходит из-за отсутствующего доверенного корня в локальном CA bundle; это отдельная эксплуатационная задача, а не доказательство недоступности MAX.
- В реальном `~/.hermes` команда сообщает Hermes Agent v0.20.0 (2026.8.3), install directory `~/.hermes/releases/hermes-agent/0.19.0`; проверенный исходный commit Hermes: `25df9d8b5dd5d7f9636329dee17faec5c61f4d3f`. MAX/VK-плагинов в активном `~/.hermes/plugins` нет.

### Не подтверждено и требует полевого теста

- Что MAX будет доступен в каждом конкретном регионе, у каждого оператора и при каждом виде ограничения связи.
- Что мобильное приложение доступно в конкретном российском Apple ID, Google-аккаунте или на конкретной модели устройства.
- Что именно API-хост бота, а не только клиентский домен MAX, входит в механизм доступа при ограничениях мобильного интернета.
- Стабильность Webhook, callback-кнопок, медиа и повторной доставки на длительном интервале.
- Возможность регистрации бота для конкретного владельца: актуальные правила MAX Business и верификации могут меняться.
- Возможность создать MAX-бота обычному физическому лицу без подтвержденного бизнес-профиля. В актуальной инструкции MAX описаны российские юрлица, ИП и самозанятые с подтверждением; это нужно проверить до начала live-интеграции.

## 3. MAX и VK: актуальность и востребованность

### Почему MAX первый

1. **Соответствие цели проекта.** В материалах Минцифры и операторов MAX и сервисы VK фигурируют среди российских сервисов, которым может сохраняться доступ при временных ограничениях мобильного интернета. Перечень динамический и формируется с учетом требований безопасности, поэтому это основание для приоритета, но не SLA.
2. **Официальный Bot API уже пригоден для базового канала.** Есть текстовые сообщения, события, Webhook, polling, медиа, кнопки и callbacks.
3. **Есть актуальный аналог.** OpenClaw уже имеет отдельный `openclaw-max-plugin`, поддерживающий текст, Markdown-чанкинг, DM/group routing, allowlist/pairing, медиа, Webhook и polling fallback. Это полезный источник поведения и edge cases, но не библиотека, которую можно механически перенести в Hermes.
4. **Низкая стоимость первой проверки.** В текущем WSL endpoint уже сетево достижим; для первого smoke-test нужен токен и корректный сертификатный bundle, а не VPN-мост для Telegram.

### Почему VK не исключается

- VK имеет зрелый Bot API, большую историческую пользовательскую базу и больше готовых библиотек.
- VK также упоминается в контексте российских сервисов, доступных при ограничениях.
- Реальные требования пользователя могут привести к необходимости fallback-канала, поэтому базовые типы Hermes-событий, allowlist, session key, chunking, медиа и observability нужно вынести в общую модель.
- По данным VK для инвесторов, месячная аудитория VK в России в Q1 2026 составляла 93,4 млн. Это усиливает аргумент за второй адаптер, но не меняет приоритет MAX для сценария белого списка.

### Про магазины приложений

Утверждение «MAX и VK заблокированы во всех магазинах» как общее текущее утверждение не подтверждается. Официальные страницы MAX продолжают указывать Android, iPhone, RuStore, AppGallery и веб-версии; страница MAX в Google Play была доступна на дату среза и показывала 50M+ установок. Одновременно существуют свежие сообщения об исчезновении MAX из отдельных магазинов или региональных витрин. Значит, доступность нужно считать **региональной и зависящей от аккаунта/витрины**, а не бинарной глобальной характеристикой.

Для проекта это не блокер: доступность приложения в магазине и доступность Bot API для сервера — разные контуры. Нужно отдельно провести матрицу установки/обновления клиента и отдельно матрицу доставки бота.

## 4. Сравнение возможностей для Hermes

| Возможность | MAX Bot API | VK Bot API | Приоритет в MAX MVP |
|---|---|---|---|
| Личная переписка | Да | Да | Да |
| Группы/чаты | Да, с особенностями адресации | Да | После DM |
| Webhook | Да, HTTPS/443/trusted CA | Да | Да, production |
| Long Polling | Да, для dev/test | Да | Да, только dev/fallback |
| Текст | До 4000 символов | Есть лимиты платформы | Да |
| Markdown/HTML | Да | Частично отличается | Да, с нормализацией |
| Редактирование | Да, PUT message | Да | Для streaming-like UX |
| Кнопки/callback | Да | Да | Для approval/clarify |
| Изображения/аудио/файлы | Да, через upload + attachment | Да, через upload API | После текста |
| Голосовые сообщения | Нужен mapping media + Hermes STT | Нужен mapping media + Hermes STT | После медиа |
| Threads/reactions | Не следует считать доступными без отдельной проверки | Не переносить автоматически | Не входят в MVP |
| Cron/standalone send | Возможен через Bot API | Возможен | Да |
| Telegram-подобный UX | Достижим для базовых сценариев | Достижим частично | Цель MVP |

## 5. Что уже есть

### Hermes

Официальный guide Hermes предлагает плагины как основной путь расширения платформ. Существуют отдельные lifecycle-методы для подключения, отправки, chat info, standalone sender и интерактивных действий `send_clarify`, `send_exec_approval`, `send_slash_confirm`, `send_model_picker`. Это позволяет дать MAX не только обычный текст, но и подтверждения опасных действий через callback-кнопки.

### OpenClaw MAX plugin

Источник: [openclaw-max-plugin](https://github.com/AlexBessarabenko/openclaw-max-plugin) и его карточка на [ClawHub](https://hub.openclaw.ai/alexbessarabenko/plugins/openclaw-max).

Полезные решения, которые нужно перенять как поведенческие требования:

- Webhook как production-путь и polling как development fallback.
- Allowlist/pairing и явная DM policy вместо открытого бота.
- Отдельные сессии по пользователю/чату.
- Защита от duplicate update и loopback собственных сообщений.
- Chunking под ограничение MAX.
- Медиа через gateway pipeline, а не отдельный «голосовой интеллект» внутри адаптера.
- Typing, обработка `bot_started`, конфигурация webhook secret и API base URL.

Ограничения аналога:

- Это небольшой внешний проект, а не доказательство стабильного промышленного SLA MAX.
- Он написан под архитектуру OpenClaw и Node.js/официальный TypeScript SDK.
- Его capabilities не означают, что те же методы есть у Hermes или что они одинаково работают в текущей версии MAX API.

### MAX SDK и причина не использовать текущий `maxapi` вслепую

MAX публикует официальный TypeScript SDK [`@maxhub/max-bot-api`](https://github.com/max-messenger/max-bot-api-client-ts) и Go SDK. Python-пакет `maxapi` в открытом репозитории описан как неофициальный fork, а его интерфейс отражает более старую модель API. В частности, текущий прототип ожидает методы вроде `send_photo`, `send_audio`, `send_video`, `send_document` и `set_webhook`, тогда как актуальный HTTP API использует `/uploads`, attachment payload и `/subscriptions`.

Для Hermes, написанного на Python, предпочтителен небольшой типизированный `httpx`-клиент поверх официальной REST-схемы. Node sidecar с официальным SDK возможен, но добавляет второй runtime, процесс и канал отказа. Его следует рассматривать только если MAX начнет быстро менять protocol surface, который неудобно поддерживать в Python.

## 6. Аудит локального прототипа

Путь: `plugins/max`, `plugins/vk`, `plugins/_ru_common` в этом репозитории.

Проверка `python -m pytest tests -q`: **29 passed**. Это полезная базовая регрессия прототипа, но она не заменяет тесты против установленного Hermes.

Критичные замечания:

1. Имена manifest-файлов сейчас `PLUGIN.yaml`, а текущая документация Hermes ожидает `plugin.yaml`.
2. Адаптеры напрямую создают `SessionSource`; актуальный контракт требует `self.build_source(...)` для совместимого session routing и форматирования источника.
3. `connect()` не принимает `is_reconnect`, что может ломать lifecycle Hermes.
4. MAX-адаптер вызывает старые или неподтвержденные методы SDK: `set_webhook`, `send_photo`, `send_audio`, `send_video`, `send_document`. Текущий Python fork также не следует принимать за полную реализацию v2 без проверки исходника и endpoint contract.
5. Разбор входящего события использует неправильные поля: тип чата должен извлекаться из `recipient.chat_type`, а устойчивый id сообщения MAX находится в `message.body.mid`. Без этого ломаются групповой routing, reply, edit и deduplication.
6. Ответ прототипа предполагает Telegram-подобный `reply_to_message_id`, тогда как в MAX это должно быть отражено через объект `link`; медиа требуют `/uploads` и `attachments`.
7. Модель Webhook не проверяет требование MAX вернуть 200 быстро и не выделяет queue/worker для обработки после ACK.
8. Нет отдельной реализации актуальной TLS-политики и контролируемого CA bundle Минцифры.
9. Проверка текущего репозитория не обнаружила установленного MAX/VK-плагина в `~/.hermes/plugins`; прототип пока не подключен к работающему Hermes gateway.

Вывод: тесты прототипа сохранить как регрессионный материал, но реализацию MAX переписать под текущий plugin contract и REST API до установки в рабочий Hermes.

## 7. Рекомендуемая архитектура

```text
MAX client
    |
    | HTTPS Webhook :443, X-Max-Bot-Api-Secret
    v
MAX ingress / Hermes webhook route
    |
    | fast ACK + bounded queue + dedup ledger
    v
MaxAdapter (external Hermes plugin)
    |-- allowlist / pairing / policy
    |-- session key via build_source
    |-- normalized text/media/callback events
    |-- MAX REST client (httpx, platform-api2.max.ru)
    |-- retry/rate-limit/edit coalescing
    v
Hermes Agent v0.20.0
    |-- existing model, tools, memory, STT/TTS, cron
    v
MAX REST API
```

Обязательные архитектурные решения:

- Внешний репозиторий/плагин, без правок Hermes core.
- `plugin.yaml` и `register(ctx)` по текущему guide.
- `httpx.AsyncClient` с `Authorization`, таймаутами, retry policy и per-client CA bundle.
- CA bundle собирается из системных сертификатов и официального корня/промежуточных сертификатов Минцифры; нельзя использовать `verify=False` и не следует менять глобальное доверие ОС без необходимости.
- Webhook быстро подтверждает запрос и передает событие в bounded queue; обработка, вызов модели и отправка ответа выполняются после ACK.
- При старте: проверить `/me`, состояние подписки и выполнить идемпотентную регистрацию Webhook. Polling используется отдельно только для dev/test.
- Дедупликация по устойчивому update/message id; повторная доставка не должна порождать второй turn Hermes.
- Входные и исходящие медиа проходят через существующий Hermes media cache/pipeline.
- Для streaming-like ответа отправляется placeholder или промежуточный текст, а редактирования coalesce-ятся с лимитом не более двух сообщений/изменений в секунду на диалог.
- По умолчанию DM allowlist/pairing закрытый; группы требуют явного разрешения и, если применимо, упоминания бота.

## 8. Риски

| Риск | Вероятность | Последствие | Митигирование |
|---|---:|---:|---|
| MAX меняет API/сертификаты | Высокая | Канал перестает работать после обновления | REST client, contract tests, startup probe, release notes |
| Webhook отписан или не получает 200 | Средняя | Тихая потеря входящих | health metric по входящим, reconcile подписки, alert на нулевой traffic |
| Региональный whitelist не покрывает конкретного оператора | Средняя | Нет связи в инциденте | матрица оператор/регион/режим, VK/Telegram как fallback где доступно |
| Корневой сертификат отсутствует в образе | Высокая для текущего WSL | API недоступен при включенном TLS | проверенный per-client CA bundle, startup TLS probe |
| Открытый бот приводит к злоупотреблению | Средняя | Расходы, утечки, команды от чужих пользователей | pairing/allowlist, policy для групп, audit log |
| Неверная адресация DM/group | Средняя | Сообщение не доставляется или уходит не туда | normalized ChatTarget и реальные integration tests |
| Retry создает дубликаты | Средняя | Двойные ответы и повторные действия | idempotency ledger, message/update dedup, bounded retry |
| Магазин приложения недоступен | Средняя/региональная | Пользователь не может установить клиент | официальные альтернативные витрины, web client, preflight matrix |

## 9. Цели и критерии успеха

### Цель проекта

Дать пользователю Hermes Agent устойчивый российский канал управления через MAX без VPN, с уровнем базового взаимодействия Telegram: личный чат, контекстная сессия, команды, streaming-like обновление, подтверждения опасных действий, медиа и фоновые уведомления.

### MVP

- Аутентифицированный личный диалог с allowlist/pairing.
- `/start`, `/new`, `/reset`, `/status`, `/stop` и базовые команды Hermes.
- Текстовый запрос и ответ с сохранением контекста.
- Chunking до 4000 символов, Markdown-safe fallback.
- Typing/working indicator, reply/quote если доступно.
- Webhook в production, polling в dev.
- Retries, dedup, self-message loop protection, structured logs.
- Автоматическая проверка и восстановление Webhook subscription.

### Расширение

- Callback-кнопки для clarify, approval и model picker.
- Редактирование промежуточного ответа.
- Прием и отправка image/audio/file.
- Передача voice/audio в существующий Hermes STT/TTS pipeline.
- Cron/standalone sender.
- Группы и policy для нескольких пользователей.
- Полевой режим без VPN при ограничениях мобильного интернета.

### Измеримые release gates

- Не менее 100 последовательных inbound messages без потери и дубликатов в baseline smoke.
- P95 Webhook ACK менее 1 секунды; жесткий предел MAX менее 30 секунд.
- 100% сообщений от неразрешенных пользователей отклоняются без вызова модели.
- Корректная доставка текста длиной 3999, 4000 и более 4000 символов.
- Не более 2 update/send операций в секунду на диалог для streaming path.
- Ноль токенов и персональных данных в обычных логах.
- Отдельный incident test на минимум двух операторах и двух регионах, где это возможно; результат маркируется как измеренный, а не как универсальная гарантия.

## 10. Проверки и тесты

### Контракт Hermes

- Discovery по `plugin.yaml`.
- Import и `register(ctx)` против установленного Hermes v0.20.0.
- Вызов `connect(is_reconnect=False/True)`.
- `build_source`, `handle_message`, standalone sender и config/env callbacks.
- Smoke discovery после копирования в временный `~/.hermes/plugins`.

### Unit/contract MAX API

- `/me`, `/messages`, PUT edit, `/uploads`, `/subscriptions`, `/updates`.
- Заголовок Authorization и корректный `platform-api2.max.ru`.
- 401/403/404/409/429/5xx, `Retry-After`, timeout, reconnect.
- Attachment not ready и повтор с backoff.
- TLS verification включена; неверный CA bundle дает понятную диагностику.
- Одновременный Webhook + polling блокируется конфигурацией.

### Webhook

- Secret header обязателен.
- ACK до постановки в очередь.
- Невалидный secret получает 401/403 и не меняет state.
- Повтор одного update дает один turn.
- Переполнение queue не приводит к бесконечному росту памяти.
- Поддерживаются `message_created`, `bot_started`, `message_callback`, edited/removed events.

### End-to-end

На отдельном тестовом боте и без production token:

- `/me`, старт диалога, короткий и длинный текст.
- Перезапуск gateway, reconnect, повторная регистрация Webhook.
- Inline callback для approval/clarify.
- Image/audio/file upload и получение.
- Группа с mention и пользователь без allowlist.
- Отключение Webhook/бота и восстановление.
- Cron delivery и отправка после завершения фоновой задачи.

### Полевой incident test

Тестовая матрица должна фиксировать дату, SIM/operator, регион, Wi-Fi/мобильную сеть, наличие VPN, доступность приложения, доступность `max.ru`, входящий DM, исходящий ответ, задержку, дубликаты и потерю. Одна положительная проверка не доказывает работу во всех будущих блокировках.

## 11. Источники

### Официальные

- [Hermes: Adding Platform Adapters](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters)
- [Hermes: Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Hermes: Messaging](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/)
- [MAX Bot API](https://dev.max.ru/docs-api)
- [MAX send messages](https://dev.max.ru/docs-api/methods/POST/messages)
- [MAX edit messages](https://dev.max.ru/docs-api/methods/PUT/messages)
- [MAX updates / Long Polling](https://dev.max.ru/docs-api/methods/GET/updates)
- [MAX Webhook subscriptions](https://dev.max.ru/docs-api/methods/POST/subscriptions)
- [MAX uploads](https://dev.max.ru/docs-api/methods/POST/uploads)
- [MAX Update object](https://dev.max.ru/docs-api/objects/Update)
- [MAX Message object](https://dev.max.ru/docs-api/objects/Message)
- [MAX download](https://max.ru/download)
- [MAX device support](https://help.max.ru/help/about/na-kakih-ustrojstvah-rabotaet-max)
- [MAX official TypeScript SDK](https://github.com/max-messenger/max-bot-api-client-ts)
- [MAX official Go SDK reply implementation](https://github.com/max-messenger/max-bot-api-client-go/blob/main/message.go)
- [MAX Python client fork](https://github.com/max-messenger/max-botapi-python)
- [OpenClaw MAX plugin](https://github.com/AlexBessarabenko/openclaw-max-plugin)

### Регуляторный контекст и community evidence

- [ГАРАНТ: сообщение Минцифры о расширении белого списка](https://www.garant.ru/products/ipo/prime/doc/414009495/)
- [МТС: сервисы при ограничениях мобильного интернета](https://media.mts.ru/internet/210833-belyj-spisok-interneta)
- [Хабр: практическое MAX Bot API, MVP](https://habr.com/ru/articles/1016164/)
- [Хабр: четыре практические проблемы MAX Bot API](https://habr.com/ru/articles/1060586/)
- [Хабр: миграция API и сертификаты Минцифры](https://habr.com/ru/articles/1053638/)
- [Хабр: ограничения экосистемы и удаления ботов](https://habr.com/ru/articles/1044896/)
- [MAX: условия подготовки/создания бота](https://dev.max.ru/docs/chatbots/bots-coding/prepare)
- [VK: информация для инвесторов, Q1 2026](https://vk.company.ru/ru/investors/info/12312/)
- [Разъяснение о том, что белый список относится к мобильным ограничениям](https://rg.ru/2026/03/21/v-mincifry-oprovergli-novosti-o-vvedenii-belyh-spiskov-dlia-domashnego-interneta.html)
- [Дополнительный community MAX plugin для OpenClaw](https://github.com/olegbalbekov/openclaw-max)
- [Ещё один community MAX plugin для OpenClaw](https://github.com/alexeyavdey/openclaw-max-messenger)

Community articles are used as operational evidence and warnings, not as normative API documentation. Before implementation each endpoint and certificate URL is rechecked against the official MAX portal.

### Независимые обзоры

Три независимых аудита были выполнены доступными агентами, включая отдельный high-reasoning аудит на `gpt-5.6-sol`. Они согласились с этой запиской по главным пунктам: MAX first, внешний Hermes plugin, собственный REST client, Webhook-first, строгая allowlist-политика, TLS bundle, early ACK/queue/dedup и отсутствие доказательств универсальной работы в белом списке. Дополнительные load-bearing замечания аудита: durable inbox вместо одного in-memory queue, persistent polling marker, scoped bot-token lock, корректный `link.mid`, rate limiter, отдельный ingress и проверка фактической PluginManager/registry-последовательности. Выводы Claude и Antigravity в этот документ не приписываются: callable-интеграций этих систем в текущей сессии нет.

## 12. Результат первой реализации

После аудита текущая MAX-заготовка была переработана в отдельный plugin-only
слой:

- `plugins/max/plugin.yaml` вместо `PLUGIN.yaml`;
- собственный REST client вместо `maxapi`;
- относительные внутренние импорты, совместимые с загрузкой Hermes как
  `hermes_plugins.max_platform`;
- Hermes lifecycle `connect(*, is_reconnect=False)`, `build_source`, YAML hook,
  standalone sender;
- typed `MaxApiError`, `Authorization`, v2 URL, marker polling;
- `body.mid`/`recipient.chat_type`, DM/group policy, dedup, bounded Webhook
  queue and fast status decision;
- durable SQLite Webhook inbox, persistent polling marker and persistent
  `user`/`chat` target type;
- standalone ASGI/uvicorn Webhook ingress outside the Hermes gateway;
- MAX reply link with official `link.mid` field and conservative global/per-chat
  rate limiter;
- TLS policy, запрещающая отключение проверки сертификатов;
- read-only loader smoke script and upgrade/rollback runbook.

Live evidence on 2026-08-08: with the official Russian CA bundle supplied per
client, MAX `/me` returned HTTP 200 for bot `id5834024914_1_bot`; one outbound
message to test user `9533440` returned HTTP 200; Long Polling returned three
updates, including two `message_created` events from that user. The real
Hermes adapter connected and disconnected successfully in a disposable
collector smoke without invoking the model. The active gateway was not
restarted and the token was not written to repository or service files.

Проверки после изменений: **60 тестов проходят**; отдельный импорт и регистрация
через loader-style smoke на реальном Hermes v0.20.0 проходят:
`plugin_import=ok`, `platform_name=max`, `writes_hermes_core=no`. Hermes core,
его конфигурация, systemd units and active gateway were not modified.

Current limitations are explicit: no HTTP listener inside Hermes itself (the
separate ingress is available), no media upload/download, no callback resolver
wiring, no streaming edit coalescing/message-age policy, no subscription
health/reconciliation metrics, and no real MAX token smoke yet. Поэтому это
text MVP / integration foundation, not yet a production-ready channel.

## 13. Итог

Начинаем с MAX, но делаем не одноразовый SDK wrapper, а upgrade-safe внешний Hermes plugin с нормализованным channel contract. Первый технический результат должен быть не «бот отвечает в MAX», а воспроизводимый contract smoke: Hermes discovers plugin, MAX API passes TLS and `/me`, Webhook reliably ACKs and queues events, allowlist protects the user, and one text turn reaches the existing Hermes session and returns exactly once. Только после этого добавляем media, callbacks, voice and incident mode.
