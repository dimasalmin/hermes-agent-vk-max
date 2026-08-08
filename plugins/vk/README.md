# Hermes Agent — VK plugin

Подключение Hermes Agent к [ВКонтакте](https://vk.com). Canал работает в РФ без VPN при региональных ограничениях связи (whitelist).

## Установка

```bash
pip install "hermes-agent-ru-messengers[vk]"
ln -s "$(pwd)/plugins/vk" ~/.hermes/plugins/vk
hermes gateway setup
hermes gateway start
```

## Получение токена сообщества

1. Создайте сообщество (или возьмите существующее) на vk.com.
2. **Управление → Сообщения → Включено**.
3. **Управление → Работа с API → Длительный опрос → Включить**, версия API — последняя.
4. **Работа с API → Возможности API**: включите `messages`, `photos`, `docs`.
5. **Ключи доступа → Создать ключ**, права: `сообщения сообщества`, `доступ к фото`, `доступ к документам`.
6. Скопируйте ключ в `VK_GROUP_TOKEN`, ID сообщества — в `VK_GROUP_ID`.

## Минимальный `.env`

```
VK_GROUP_TOKEN=vk1.a.AAA...
VK_GROUP_ID=123456789
VK_ALLOWED_USERS=1234567
```

## Особенности VK

- `peer_id` для DM = user_id; для бесед = `2000000000 + chat_id`. В `VK_GROUP_ALLOWED_CHATS` указывайте именно `peer_id`.
- В беседах включите упоминание сообщества: `@public<group_id>` или `[club<group_id>|@name]`. Бот реагирует на оба формата.
- Голосовые сообщения отдаются как `audio_message` (OGG/Opus), скачиваются и идут в STT pipeline Hermes автоматически.
- VK API НЕ возвращает `Retry-After` — при throttle (коды 6/9) используется экспоненциальный backoff.

## Возможности

- ✅ Текст, голосовые (STT), фото, документы
- ✅ Allowlist + admin-tier
- ✅ LongPoll сообщества
- ✅ Стриминг через `messages.edit`
- ✅ Cron-доставка
- ✅ Profile-safe token locking

См. также: [docs/ru/vk-setup.md](../../docs/ru/vk-setup.md).
