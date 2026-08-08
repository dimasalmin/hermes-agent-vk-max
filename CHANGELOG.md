# Changelog

## 0.2.0 - 2026-08-08

- Rebuilt MAX integration as an external Hermes plugin using the current v0.20.0
  adapter contract.
- Added direct MAX Bot API v2 client, Long Polling marker persistence, Webhook
  secret validation, durable SQLite inbox and optional standalone ASGI ingress.
- Added DM/group allowlist policy, MAX reply `link.mid`, persistent target type,
  TLS fail-closed checks and global/per-dialog rate limiting.
- Marked media, callbacks, streaming edit coalescing, health metrics and live
  no-VPN validation as remaining release gates.

This release does not modify Hermes core and is not a claim of universal MAX
availability during regional network restrictions.
