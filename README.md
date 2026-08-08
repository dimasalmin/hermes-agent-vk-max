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

The MAX plugin currently provides the infrastructure and text MVP path:

- MAX Bot API v2 REST client at `platform-api2.max.ru`.
- `Authorization` header, typed API errors and retry metadata.
- MAX `message_created` normalization using `body.mid` and `recipient.chat_type`.
- Hermes external plugin registration with `plugin.yaml`, `is_reconnect`,
  `build_source`, `apply_yaml_config_fn`, and standalone sender hooks.
- Development Long Polling with marker handling.
- Webhook secret validation, bounded queue and dedup receiver.
- DM/group allowlist policy and text chunking at 4000 characters.
- TLS verification with an optional deployment-managed `MAX_CA_BUNDLE`.

Media upload, callback approval buttons and streaming edits are separate release
gates. The optional Webhook ingress is a separate process and is not embedded in
the Hermes gateway.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

The current suite includes contract tests for the MAX API client, update parsing,
TLS policy, bounded Webhook queue and Hermes loader compatibility.

Live checks, with secrets supplied only through the environment:

```bash
python scripts/max_live_smoke.py --user-id 9533440 --send --poll-seconds 30
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
```

Hermes applies `MAX_ALLOWED_USERS` before the adapter. Group-only users must
also be listed there; `MAX_GROUP_ALLOWED_USERS` and
`MAX_GROUP_ALLOWED_CHATS` further restrict group routing inside the plugin.

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
