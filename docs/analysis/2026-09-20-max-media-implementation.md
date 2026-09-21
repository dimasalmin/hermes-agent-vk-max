# MAX Media And Control Implementation Record

Initial implementation date: 2026-09-20. Verification update: 2026-09-21.

This record describes the first implementation pass from the approved MAX
channel plan. It is intentionally separate from Hermes core and is meant to be
read together with `docs/ops/max-upgrade-safe.md`.

## Boundary

- Development checkout: branch `max-media-v1` in the isolated worktree.
- Production plugin and Telegram were not changed during implementation.
- No Hermes source, model, memory, Telegram session or existing SQLite session
  was edited.
- The live polling consumer was not duplicated during tests.
- The repository remains the rollback unit; the old plugin checkout stays
  available until live acceptance is complete.

## Implemented

1. The test bootstrap extends the installed Hermes `plugins` namespace instead
   of shadowing it. Loader smoke imports the plugin using the same external
   plugin boundary as Hermes.
2. MAX overrides the Hermes media contract: `send_image`, `send_image_file`,
   `send_document`, `send_voice`, `send_video`, `send_animation` and
   `send_multiple_images`.
3. Local and approved remote images use `/uploads`, multipart field `data`,
   then a token attachment. `MEDIA:` and standalone delivery share the same
   upload contract. `[[as_document]]`, multiple files, Cyrillic names, 50 MiB
   bounds are supported. MAX image/video batches are capped at 12; files and
   audio are split into compatible messages because MAX rejects a file mixed
   with image/video media.
4. Incoming media uses Hermes' existing byte cache. Images, documents, audio,
   voice and video are mapped to the corresponding `MessageType`; video
   attachments without a URL are resolved through `GET /videos/{token}`.
5. MAX command registration uses `PATCH /me/commands` and only commands found
   in the installed Hermes gateway registry. `/menu`, `/start` and
   `/maxstatus` are plugin-owned commands. Inline menu buttons route to normal
   Hermes commands rather than issuing an LLM prompt themselves.
6. Groups require both an allowlisted sender and an allowlisted chat. Hermes
   session scope is `MAX chat + participant`; outbound calls decode this scope
   back to the real MAX chat. Group approvals, model selection and control
   commands require `MAX_ADMIN_USERS`; clarify callbacks remain bound to the
   initiating user.
7. Polling persists each received update and the next marker in one SQLite
   transaction. Processing is explicit: `pending -> processing -> processed`
   or `failed`. Failed/ambiguous events remain visible and are not silently
   replayed. Webhook inbox has the same processing states.
8. CDN requests never receive the bot token. HTTPS media hosts, ports,
   redirect targets and literal private IP addresses are checked before
   download. API retries honor `Retry-After` and the live MAX message
   `errors.process.attachment.file.not.processed` response, normalized as
   `attachment.not.ready`.
9. A configured MAX CA bundle is added to the system trust context rather than
   replacing system roots; this covers both the MAX API and its upload CDN.

## Evidence

Commands executed against the installed Hermes runtime:

```text
PYTHONPATH=/home/xidden/.hermes/hermes-agent \
  /home/xidden/.hermes/hermes-agent/venv/bin/python -m pytest -q
118 passed

python scripts/max_loader_smoke.py \
  --hermes-root /home/xidden/.hermes/hermes-agent \
  --plugin-dir plugins/max
plugin_import=ok
adapter_instantiation=ok
writes_hermes_core=no
```

Live transport evidence on 2026-09-21, using the configured bot and one
existing gateway poller:

- `GET /me` returned the expected bot identity.
- A direct text message reached the configured MAX user.
- The outbound media smoke uploaded a Cyrillic-named PNG and TXT, then sent
  them as two MAX-compatible messages. The test used the normal
  `MAX_CA_BUNDLE`; TLS stayed enabled, and the document send retried once
  while MAX processed the attachment.
- The live `/me` response contained 15 registered commands. Because native
  command-menu rendering is client-dependent, `/start`, `/menu` and
  `/commands` now also send the command list as ordinary text.
- The gateway remained the only polling consumer; no direct smoke called
  `get_updates`.

These facts prove transport/API behavior and outbound delivery from the host,
not phone rendering, inbound media, Hermes model use of media, group callbacks,
or availability on a mobile network without VPN.

The tests prove protocol shapes, method contracts, durable state transitions,
URL/token policy, group callback authorization and synthetic group-session
translation. They do not prove that a real MAX client delivered a file to a
phone or that Hermes' model used its contents.

## Required live acceptance

Use a disposable bot or an agreed maintenance window with one MAX polling
consumer only. Verify, separately, DM and group behavior for JPG/PNG, PDF/DOCX,
XLSX/TXT, OGG/MP3 and MP4; no-caption and Cyrillic filenames; 50 MiB boundary;
oversize rejection; image-as-document; multiple attachments; inbound video
token; voice STT and audio response; menu visibility; button expiry and admin
restriction. Ask the model to quote a control string from a document and image
and to transcribe a control phrase from audio. Treat video playback as a
transport check, not as semantic video understanding.

Also test one mobile network without VPN. MAX application reachability does not
prove CDN or model-provider reachability, so record each dependency separately.

## Rollback

1. Stop/drain the gateway in the normal maintenance window.
2. Retarget only `~/.hermes/plugins/max` to the previous plugin checkout.
3. Keep Hermes core, Telegram state, configuration and SQLite backups intact.
4. Start the gateway and verify both Telegram and MAX text paths.

Do not run a second MAX poller against the live bot while the gateway is active.

## Sources used for protocol decisions

- [MAX uploads](https://dev.max.ru/docs-api/methods/POST/uploads)
- [MAX commands](https://dev.max.ru/docs-api/methods/PATCH/me/commands)
- [MAX updates](https://dev.max.ru/docs-api/methods/GET/updates)
- [MAX Update object](https://dev.max.ru/docs-api/objects/Update)
- [Realmagnum/hermes-max-integration](https://github.com/Realmagnum/hermes-max-integration)
- [olegbalbekov/openclaw-max](https://github.com/olegbalbekov/openclaw-max)
- [Hermes media contract issue](https://github.com/NousResearch/hermes-agent/issues/77392)
- [Hermes cached-file delivery issue](https://github.com/NousResearch/hermes-agent/issues/76022)
