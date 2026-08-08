# VK adapter setup for Hermes Agent

A community plugin connecting Hermes Agent to [VKontakte](https://vk.com), Russia's largest social network. VK's messenger sits on the regulator-defined whitelist — users in the RF can reach your agent without a VPN under regional connectivity restrictions.

## 1. Get a community access token

1. Create or open a community on vk.com.
2. **Manage → Messages → Enabled**.
3. **Manage → API usage → Long Poll API → Enabled** (latest API version).
4. **API usage → API capabilities**: enable `messages`, `photos`, `docs`, `users`.
5. **Access tokens → Create token** with scopes: community messages, photos, documents.
6. Copy the token to `VK_GROUP_TOKEN`. Group ID = number from `vk.com/club<ID>`.

## 2. Install

```bash
git clone https://github.com/<placeholder>/hermes-agent-ru-messengers
cd hermes-agent-ru-messengers
pip install -e ".[vk]"
ln -s "$(pwd)/plugins/vk" ~/.hermes/plugins/vk
```

## 3. Config

```env
VK_GROUP_TOKEN=vk1.a.AAA...
VK_GROUP_ID=123456789
VK_ALLOWED_USERS=1234567
```

```bash
hermes gateway setup
hermes gateway start
```

## 4. VK peculiarities

- **peer_id**: DM peer_id == user_id; multi-user chats use `2_000_000_000 + chat_id`. Use peer_id (not chat_id) in `VK_GROUP_ALLOWED_CHATS`.
- Mention forms: `@<screen_name>` or `[club<group_id>|@label]` — both are recognized.
- Voice notes (`audio_message`) → auto-transcribed by Hermes STT.
- VK does not expose `Retry-After`; throttle (codes 6/9) is handled by exponential backoff.

## 5. Capabilities

- ✅ Text, voice (STT), photos, documents
- ✅ Allowlist + admin-tier
- ✅ Community Long Poll
- ✅ Streaming via `messages.edit`
- ✅ Cron delivery via `VK_HOME_CHANNEL`
