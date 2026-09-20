# Changelog

## Unreleased

- Added native Hermes media overrides for images, documents, voice/audio,
  video, animation and multiple images, including remote image re-upload,
  Cyrillic filenames, `[[as_document]]`, 50 MiB bounds and partial batch
  failure reporting.
- Added inbound video-token resolution, Russian attachment errors, MAX command
  registration/menu, `/menu`, `/start`, `/maxstatus`, typing actions and
  participant-scoped group sessions.
- Made polling and Webhook inbox states durable across receipt/processing;
  ambiguous failed events remain diagnostic instead of being replayed silently.
- Added strict group AND allowlist semantics and explicit `MAX_ADMIN_USERS`.
- Added MAX inline keyboards for Hermes clarify prompts, exec approvals and
  slash confirmations.
- Added a two-step `/model` picker for provider and model selection.
- Added official `/answers` callback acknowledgement, user/chat binding,
  expiry and single-use replay protection.
- Added bounded inbound media caching, outbound `MEDIA:` uploads, standalone
  media delivery and `MAX_MEDIA_MAX_BYTES` configuration.
- Matched the current MAX attachment rules: image/video batches use up to 12
  items, while files and audio are split into compatible messages. Upload
  tokens from the live `photos` id-map response are now extracted, and the
  `attachment.not.ready` processing response is retried.
- Added a TLS context that preserves system roots while adding the configured
  MAX CA bundle; live image/document upload and delivery were verified.
- Kept the feature in the external plugin; Hermes core remains unchanged.

## 0.2.0 - 2026-08-08

- Rebuilt MAX integration as an external Hermes plugin using the current v0.20.0
  adapter contract.
- Added direct MAX Bot API v2 client, Long Polling marker persistence, Webhook
  secret validation, durable SQLite inbox and optional standalone ASGI ingress.
- Added DM/group allowlist policy, MAX reply `link.mid`, persistent target type,
  TLS fail-closed checks and global/per-dialog rate limiting.
- Marked live media acceptance, streaming edit coalescing, health metrics and
  live no-VPN validation as remaining release gates.

This release does not modify Hermes core and is not a claim of universal MAX
availability during regional network restrictions.
