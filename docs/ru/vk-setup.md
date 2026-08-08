# Настройка VK-адаптера для Hermes Agent

## 1. Регистрация и токен сообщества

1. Создайте сообщество (или используйте имеющееся) на [vk.com](https://vk.com).
2. **Управление → Сообщения**: переключатель в положение «Включены».
3. **Управление → Работа с API**:
   - **Длительный опрос (Bots Long Poll API)** → «Включён», API version — актуальная (>= 5.199 на момент Q2-2026).
   - **Возможности API**: пометить `messages`, `photos`, `docs`, `users`.
4. **Ключи доступа → Создать ключ** с правами:
   - Сообщения сообщества;
   - Доступ к фото;
   - Доступ к документам;
   - (опционально) Доступ к управлению сообществом — только если хотите автоматически менять настройки.
5. Сохраните токен — `VK_GROUP_TOKEN`. ID сообщества виден в URL `vk.com/club<ID>` или в **Управление → Главное**.

## 2. Установка

```bash
git clone https://github.com/<placeholder>/hermes-agent-ru-messengers
cd hermes-agent-ru-messengers
pip install -e ".[vk]"
ln -s "$(pwd)/plugins/vk" ~/.hermes/plugins/vk
```

## 3. Конфигурация

`~/.hermes/.env`:

```env
VK_GROUP_TOKEN=vk1.a.AAA...
VK_GROUP_ID=123456789
VK_ALLOWED_USERS=1234567
```

Получить **свой VK user_id**: откройте свою страницу `vk.com/<screen_name>`, при наведении на «Подробнее» — `vk.com/id<NNNN>`. Этот номер и есть user_id.

`~/.hermes/config.yaml` (опционально):

```yaml
platforms:
  vk:
    require_mention: true
    extra:
      allow_from: ["1234567"]
      allow_admin_from: ["1234567"]
      user_allowed_commands: ["help", "whoami", "model"]
      group_allowed_chats: ["2000000001"]  # peer_id, НЕ chat_id
      channel_prompts:
        "2000000001": "Ты ассистент команды. Краткие ответы."
```

## 4. Запуск

```bash
hermes gateway setup
hermes gateway start
```

## 5. Группы и беседы

- В беседах VK `peer_id = 2_000_000_000 + chat_id`. Запишите его (в URL беседы), указывайте именно `peer_id` в `VK_GROUP_ALLOWED_CHATS` и `MAX_HOME_CHANNEL`.
- Чтобы бот видел все сообщения в беседе, дайте сообществу права администратора чата.
- Упоминание сообщества: `@<screen_name>` или `[club<group_id>|@название]` — оба считаются mention.

## 6. Голос и медиа

- Голосовые VK приходят как `audio_message` (OGG/Opus). Hermes автоматически скачивает их и пускает через STT (faster-whisper / Groq / OpenAI — по вашей конфигурации в Hermes).
- Фото берётся в максимальном доступном размере.
- Документы любых типов скачиваются и доступны агенту через `media_urls`.

## 7. Диагностика

| Симптом | Что проверить |
|---------|---------------|
| Бот не отвечает в DM | `user_id` в `VK_ALLOWED_USERS`? Сообщения сообщества включены? Long Poll включён? |
| `vkbottle not installed` | `pip install "hermes-agent-ru-messengers[vk]"` |
| `Permission denied` от API | у токена нет прав `messages`/`photos`/`docs` — пересоздайте ключ |
| Бот молчит в беседе | `require_mention=true` и нет упоминания; или сообщество не админ беседы |
| Коды ошибок 6/9 | rate limit, backoff отработает автоматически |

## 8. Удаление

```bash
rm ~/.hermes/plugins/vk
hermes gateway restart
```

Токен — на vk.com отозвать через **Управление → Работа с API → Ключи доступа**.
