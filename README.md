# Hermes Agent Russian Messenger Plugins

External community plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
The first implementation target is MAX; VK remains a separate follow-up adapter.

## Safety boundary

The plugin lives outside Hermes core. Install it under `~/.hermes/plugins/` or in a
separate project repository. Hermes can be upgraded or rolled back independently;
the MAX plugin is versioned and tested separately. Do not copy its files into
Hermes' `gateway/` or edit Hermes source to enable it.

The plugin does not claim a universal whitelist guarantee. MAX availability must
be measured for the target region, operator, device and incident mode.

## Current status

The MAX plugin currently provides text, native media and interactive controls:

- MAX Bot API v2 REST client at `platform-api2.max.ru`.
- `Authorization` header, typed API errors and retry metadata.
- MAX `message_created` normalization using `body.mid` and `recipient.chat_type`.
- Hermes external plugin registration with `plugin.yaml`, `is_reconnect`,
  `build_source`, `apply_yaml_config_fn`, and standalone sender hooks.
- Development Long Polling with marker handling.
- Webhook secret validation, bounded queue and dedup receiver.
- DM/group allowlist policy, participant-scoped group sessions and text
  chunking at 4000 characters.
- Native MAX inline keyboards for Hermes clarify prompts, dangerous-command
  approvals and slash confirmations. Button callbacks use the official
  `message_callback` -> `/answers` flow.
- Native `/model` picker with provider and model selection, using the same
  callback state and Hermes `on_model_selected` hook as Telegram.
- Opaque, short-lived, single-use callback state bound to the MAX user and
  chat. Group approvals, model changes and control commands require
  `MAX_ADMIN_USERS`; clarify buttons remain bound to their initiator.
- Bounded inbound image, document, audio and video downloads into Hermes'
  existing media cache, including MAX video-token resolution.
- Native outbound image, document, audio/voice and video methods, remote image
  re-upload, multiple images, `MEDIA:`, standalone/cron delivery and
  `[[as_document]]` through the current MAX `/uploads` token flow.
- MAX slash-command registration, `/menu`, `/start`, `/maxstatus`, inline
  command buttons and typing actions.
- Durable polling/Webhook inbox state with explicit failed diagnostics and no
  silent automatic replay of ambiguous processing.
- Configurable `MAX_MEDIA_MAX_BYTES` limit (50 MiB by default) and CDN URL
  host allowlist; the bot token is not sent to signed media URLs.
- TLS verification with an optional deployment-managed `MAX_CA_BUNDLE`; the
  plugin keeps system roots when adding the MAX chain.

Outbound image/document upload and delivery have passed a live smoke. Inbound
media, phone rendering, model-use acceptance, and a no-VPN field test remain
release gates. Streaming edits remain a separate gate. The optional Webhook
ingress is a separate process and is not embedded in the Hermes gateway.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

The current suite includes contract tests for the MAX API client, update parsing,
TLS policy, bounded Webhook queue and Hermes loader compatibility.

Live checks, with secrets supplied only through the environment:

```bash
python scripts/max_live_smoke.py --user-id 9533440 --send
python scripts/max_media_live_smoke.py --user-id 9533440
python scripts/max_adapter_live_smoke.py --seconds 8
```

The second command uses a disposable SQLite state directory and a collector in
place of the model handler; it does not start or restart the active gateway.

## Installation into Hermes

Copy or junction only the MAX plugin directory. Keep this repository separate
from the Hermes checkout:

```powershell
New-Item -ItemType Junction `
  -Path "$HOME\.hermes\plugins\max" `
  -Target "D:\Cloude\Ai Agents\hermes-agent-ru-messengers\plugins\max"
```

The directory must contain lowercase `plugin.yaml`. After changing Hermes,
re-run the loader smoke test before restarting the production gateway.

## Configuration

At minimum:

```env
MAX_BOT_TOKEN=<token from MAX for Business>
MAX_ALLOWED_USERS=<numeric MAX user ids separated by commas>
MAX_GROUP_ALLOWED_USERS=<numeric MAX user ids for groups>
MAX_GROUP_ALLOWED_CHATS=<numeric MAX group chat ids>
MAX_ADMIN_USERS=<numeric MAX group administrators>
# Optional; 50 MiB default for each inbound or outbound attachment.
MAX_MEDIA_MAX_BYTES=52428800
# Optional; defaults to 600 seconds and is intentionally in-memory only.
MAX_CALLBACK_TTL_SECONDS=600
```

Hermes applies `MAX_ALLOWED_USERS` before the adapter. Group-only users must
also be listed there; inside the plugin a group request requires both the
allowlisted sender and allowlisted chat. Group sessions use a separate
`chat_id + user_id` scope while MAX delivery still targets the physical chat.

Development polling is used when `MAX_WEBHOOK_URL` is absent. Production Webhook
requires an HTTPS endpoint on port 443 and `MAX_WEBHOOK_SECRET`. If the host
trust store lacks the current MAX chain, set `MAX_CA_BUNDLE` to a verified PEM
bundle. Never disable TLS verification.

For a separate ingress process, install `.[webhook]`, keep `MAX_INBOX_PATH`
identical in both processes, and run:

```powershell
python -m pip install -e ".[webhook]"
python scripts/max_webhook_server.py
```

See [Russian setup](docs/ru/max-setup.md), [English setup](docs/en/max-setup.md),
the [current-state analysis](docs/analysis/2026-08-08-current-state.md), and the
[implementation plan](docs/superpowers/plans/2026-08-08-hermes-max.md).

## License

MIT.
