# Live MAX Deployment Handoff

This note records the current local deployment without storing credentials.

## Runtime layout

- Plugin repository: `hermes-agent-vk-max`.
- Hermes plugin link: `~/.hermes/plugins/max` -> this repository's `plugins/max`.
- Secret file: `~/.hermes/.env`, loaded by the user systemd service, mode `600`.
- MAX trust bundle: `~/.hermes/max-ca-bundle.pem`, mode `600`.
- Service drop-in: `~/.config/systemd/user/hermes-gateway.service.d/override.conf`.
- Allowed MAX user: configured through `MAX_ALLOWED_USERS`; the value is intentionally
  not duplicated in this repository.

The token must remain outside Git and outside `config.yaml`. Rotate it in MAX when
the current development phase is over.

## Platform ordering

The user config contains an explicit `platforms.max.enabled: true` entry before the
Telegram entry. Hermes initializes configured platforms sequentially. On this host,
Telegram's first connection can remain in its network connect path for several
minutes; putting MAX first keeps the MAX channel available without changing Hermes
core or disabling Telegram permanently.

## Verified on 2026-08-08

- MAX Bot API `/me`: HTTP 200 and the expected bot identity.
- MAX outbound message to the configured user: HTTP 200.
- MAX Long Polling: updates and marker were returned successfully.
- Hermes loader smoke: the external plugin was discovered and registered as `max`.
- Live adapter smoke: `connect()` and `disconnect()` completed with a temporary
  SQLite state store and a collector instead of an LLM handler.
- Active gateway: a live TLS connection to `platform-api2.max.ru` was observed from
  the Hermes process after restart.
- Repository test suite: `60 passed`.

## Dev candidate verified on 2026-09-21

- Isolated worktree: `hermes-agent-ru-messengers-dev`, branch `max-media-v1`.
- The live plugin link was switched only to this external plugin checkout; the
  previous checkout remains the rollback target at `hermes-agent-ru-messengers`.
- The gateway was restarted in the maintenance window and remained `active`
  with one MAX polling consumer.
- Full suite: `118 passed`; loader smoke reported `writes_hermes_core=no`.
- Direct `/me` and text delivery succeeded. A separate media smoke uploaded a
  Cyrillic-named image and text document and delivered both through the real
  MAX API. The live document send retried after MAX reported that processing
  was not ready.
- TLS remained verified. The plugin now combines the host trust context with
  the deployment-managed MAX bundle, so the existing MAX-only bundle also
  covers the upload CDN.

## Pending acceptance test

The MAX API cannot safely emulate a user-originated message. Send a fresh message
from the allowed MAX account to the bot and verify the Hermes reply. Use `/new` first
if the existing Hermes session reports a context-overflow error; that error is a
session/model-state issue, not a MAX transport failure.

Do not run a second direct Long Polling smoke while the gateway is active: it can
compete with the gateway's single MAX poller.

## Rollback

Stop the gateway, remove or retarget only `~/.hermes/plugins/max`, remove the
`platforms.max` block from the user config, and restart. Hermes core files and
release directories do not need to be changed.
