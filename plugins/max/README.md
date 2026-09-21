# MAX Hermes plugin

This directory is intentionally self-contained because Hermes loads external
plugins as `hermes_plugins.<slug>`. Keep internal imports relative and do not
depend on sibling packages from this repository.

The plugin targets MAX Bot API v2 at `platform-api2.max.ru` and uses the public
Hermes platform adapter contract. The current release path includes text,
native media delivery, inbound media caching, slash-command registration,
inline buttons, DM/group access policy, Webhook and development Long Polling.
The native command menu is registered through `PATCH /me/commands`; `/start`,
`/menu` and `/commands` also send an ordinary-text command list for MAX clients
that do not render the native menu.

Install this directory as `~/.hermes/plugins/max/` with the lowercase
`plugin.yaml` manifest. Do not install it inside the Hermes source tree.

See the Russian setup guide at `../../docs/ru/max-setup.md`, the interactive
button guide at `../../docs/ru/max-interactive.md`, and the implementation
record at `../../docs/analysis/2026-09-20-max-media-implementation.md`.
