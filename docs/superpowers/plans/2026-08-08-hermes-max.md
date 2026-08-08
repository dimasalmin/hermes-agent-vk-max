# MAX Channel for Hermes Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an external Hermes Agent plugin that provides Telegram-level basic interaction through MAX, works without VPN in the intended Russian network conditions, and has measured behavior during mobile-internet restrictions.

**Architecture:** A standalone Python plugin uses Hermes' public platform-adapter contract and a small direct REST client over `httpx`. Production receives MAX Webhooks over HTTPS/443, acknowledges quickly, deduplicates, and queues updates before invoking Hermes. Long Polling is a development-only transport. Media, STT/TTS, memory, tools, and cron remain Hermes responsibilities.

**Tech Stack:** Python 3.11, Hermes Agent v0.20.0 runtime contract (source commit `25df9d8b5dd5d7f9636329dee17faec5c61f4d3f`), `httpx`, `pytest`, optional `ruff`/`mypy`, MAX Bot API v2, standalone uvicorn/reverse proxy for production Webhook.

## Implementation checkpoint: 2026-08-08

Completed in the external plugin repository:

- MAX API v2 REST client with `Authorization`, `platform-api2.max.ru`, typed errors and retry metadata.
- MAX update normalization using `body.mid` and `recipient.chat_type`.
- Hermes loader-compatible `plugin.yaml`, relative internal imports, `register(ctx)`, `apply_yaml_config_fn`, `is_reconnect`, `build_source`, and standalone sender hook.
- Development polling with marker, bounded reconnect, allowlist/group policy, text chunking, and text send/edit methods.
- Webhook secret validation, durable SQLite inbox, bounded wake-up queue, deduplication, adapter `handle_webhook()` ingress seam, and standalone uvicorn ingress.
- TLS policy that refuses disabled verification and supports a deployment-managed CA bundle.
- MAX `link.mid` reply payload, persistent target type, and global/per-dialog rate limiter.
- 60 local tests and a read-only Hermes loader import/register smoke test.

Not yet production-ready: media, callback resolver wiring, streaming edit coalescing/message-age handling, subscription health metrics, and no-VPN field validation. These remain release gates below.

## Global Constraints

- Do not edit Hermes core for the first implementation.
- Do not use the current MAX prototype as evidence of compatibility without passing the contract tests in this plan.
- Use lowercase `plugin.yaml`, `register(ctx)`, `BasePlatformAdapter`, `self.build_source(...)`, and `self.handle_message(...)`.
- Use `https://platform-api2.max.ru` and the `Authorization` header.
- Keep TLS verification enabled. Do not use `verify=False`.
- Trust the official certificate chain through a controlled CA bundle; do not silently mutate global OS trust.
- Keep production on Webhook; expose Long Polling as an explicit development mode.
- Default to deny for users not in the allowlist/pairing store.
- Never place bot tokens, personal message text, or raw authorization headers in logs or tests.
- Preserve existing local changes in Hermes and in this repository.

## Phase 0: Baseline and evidence lock

### Task 0.1: Freeze the contract baseline

- [x] Record the Hermes version and install path used for tests.
- [x] Record the exact current plugin guide and MAX API URLs in the analysis document.
- [x] Mark all claims as confirmed, anecdotal, or requiring field validation.
- [x] Keep `docs/analysis/2026-08-08-current-state.md` as the decision record.

**Verification:** The analysis document contains a current date, local runtime evidence, source links, and an explicit unresolved-items list.

### Task 0.2: Reproduce the local prototype baseline

- [x] Run `python -m pytest tests -q` in `hermes-agent-ru-messengers`.
- [x] Preserve the result as a legacy baseline, currently 29 passing tests.
- [x] Do not claim live Hermes integration until plugin discovery and a runtime smoke pass.

**Verification:** The legacy suite passes and no production token is needed.

## Phase 1: Plugin contract skeleton

### Task 1.1: Align plugin discovery and registration

**Files:** `plugins/max/plugin.yaml`, `plugins/max/__init__.py`, `plugins/max/adapter.py`, plugin tests.

- [x] Rename the manifest to `plugin.yaml` and validate its schema against the installed Hermes.
- [x] Implement `register(ctx)` with platform registration through the public context API.
- [x] Add configuration callbacks for token, API base URL, mode, webhook URL/secret, CA bundle, allowlist, and group policy.
- [x] Implement `connect(*, is_reconnect: bool = False)` and `disconnect()` with explicit state transitions.
- [x] Implement `send_message`, `get_chat_info`, and standalone sender using Hermes interfaces.
- [x] Replace direct `SessionSource` construction with `self.build_source(...)`.

**Verification:** A temporary Hermes plugin directory discovers the plugin, imports it, calls registration, and starts/stops without a network token.

### Task 1.2: Build the MAX REST client

**Files:** `plugins/max/client.py`, `plugins/max/models.py`, `plugins/max/errors.py`, client tests.

- [x] Implement typed request/response helpers for `/me`, `/messages`, message edit, `/subscriptions`, and `/updates`.
- [x] Send the raw token in `Authorization` and never in a URL or log line.
- [x] Add bounded timeout, retryable status handling, `Retry-After`, and structured error mapping.
- [x] Add per-client TLS verification with a configured CA bundle.
- [x] Expose rate-limit coordination for the total API budget and the per-conversation message/edit budget.

**Verification:** Mocked HTTP tests prove method, URL, headers, JSON/query placement, timeout behavior, retry behavior, and error mapping.

### Task 1.3: Fix certificate handling

**Files:** `plugins/max/tls.py`, `docs/ops/max-certificates.md`, deployment configuration.

- [x] Define a documented CA bundle location outside the source tree for deployment.
- [x] Document how to obtain the current official Russian trusted root and intermediate certificates from the government/official source.
- [x] Fail closed on a missing or unreadable configured bundle and reject disabled verification.
- [ ] Add a startup probe that reports certificate failure separately from DNS, TCP, HTTP 401, and invalid-token failures.

**Verification:** The client succeeds with the test bundle and fails with a clear diagnostic when the bundle is removed or invalid. No test disables verification.

## Phase 2: Text DM MVP

### Task 2.1: Webhook ingress and update processing

**Files:** `plugins/max/webhook.py`, adapter integration, webhook tests.

- [x] Validate `X-Max-Bot-Api-Secret` before enqueueing an update.
- [x] Return an HTTP status decision immediately after validation and bounded enqueue.
- [x] Process `message_created` updates in a worker; lifecycle events are subscribed and ignored until mapped.
- [x] Store a bounded idempotency key for update/message ids.
- [ ] Emit metrics for received, rejected, queued, processed, duplicate, failed, and outbound events.
- [x] Reconcile the Webhook subscription at startup and expose subscription state through connection logs.

**Verification:** Webhook unit tests prove secret rejection, fast ACK, queue behavior, duplicate suppression, and worker failure isolation.

### Task 2.2: Development Long Polling

**Files:** `plugins/max/polling.py`, mode configuration, polling tests.

- [x] Implement `GET /updates` with in-process marker tracking and bounded timeout.
- [x] Reconnect with exponential backoff.
- [x] Prevent polling from starting when Webhook mode is active.
- [x] Mark polling as development/test mode in logs and documentation.

**Verification:** Mocked polling tests prove marker advancement, reconnect, cancellation, and no duplicate delivery.

### Task 2.3: Policy, session, and text behavior

**Files:** adapter and shared policy/session modules, adapter tests.

- [x] Enforce DM allowlist/pairing before invoking Hermes.
- [x] Define group policy as closed by default; support explicit mention requirement.
- [x] Derive session source through `build_source` and test user/chat isolation.
- [x] Filter the bot's own messages to prevent loops.
- [x] Pass `/` commands through existing Hermes command pathways.
- [x] Map MAX reply metadata where supported.
- [x] Chunk messages at 4000 characters.
- [ ] Add typing/working indication without exceeding the rate limit.

**Verification:** Adapter tests cover unauthorized DM, authorized DM, group isolation, self-message, commands, long text, code fences, and malformed updates.

### Task 2.4: First real MAX smoke

- [ ] Use a disposable MAX bot and a non-production token.
- [ ] Run `/me` from the actual Hermes host.
- [ ] Start a DM and send a short request.
- [ ] Verify exactly one Hermes response and one matching session record.
- [ ] Restart the gateway and repeat the request.

**Verification:** Capture timestamp, event id, message id, latency, status, and sanitized logs. A successful response without event id/dedup evidence is not a release gate.

## Phase 3: Telegram-level interaction

### Task 3.1: Interactive controls

- [x] Map MAX inline buttons and `message_callback` to Hermes clarify prompts.
- [x] Implement exec approval and slash confirmation buttons with short-lived, single-use callback ids.
- [x] Implement the model picker through the existing Hermes hook without core changes.
- [x] Reject stale, replayed, or cross-user callback ids.

**Verification:** End-to-end disposable-bot tests prove approve, deny, timeout, replay, and wrong-user behavior.

### Task 3.2: Streaming-like edits

- [ ] Send a placeholder or first partial answer.
- [ ] Coalesce edits on a timer and enforce at most two outbound operations per second per conversation.
- [ ] Split final output at the MAX limit.
- [ ] Fall back to a new message if edit fails or the message is no longer editable.
- [ ] Keep model timing separate from transport timing.

**Verification:** A long model answer produces a readable sequence without rate-limit errors, lost final output, or duplicate final messages.

### Task 3.3: Cron and standalone sends

- [ ] Implement a standalone sender for an authorized target.
- [ ] Preserve allowlist and target validation for background messages.
- [ ] Add a delivery ledger so retrying a cron job cannot send an unbounded duplicate.

**Verification:** A disposable cron event reaches the intended user after gateway restart and is not duplicated by retry.

## Phase 4: Media and voice

### Task 4.1: Incoming media

- [ ] Parse MAX attachments into a normalized Hermes media event.
- [ ] Download through the authenticated MAX client with size/type limits.
- [ ] Store through the existing Hermes media cache.
- [ ] Convert audio/voice to the format expected by the existing STT pipeline.
- [ ] Reject unsupported or oversized media with a user-safe message.

**Verification:** Image, audio, and file receive tests prove cache persistence, MIME/size validation, and cleanup on failure.

### Task 4.2: Outgoing media

- [ ] Upload through `/uploads` with the correct `type`.
- [ ] Wait/retry if MAX reports the attachment is not ready.
- [ ] Send attachment metadata through the message endpoint.
- [ ] Keep media errors independent from the text response path.

**Verification:** Disposable-bot tests cover image, audio, file, upload timeout, and attachment-not-ready retry.

### Task 4.3: Voice parity

- [ ] Reuse Hermes STT/TTS configuration and consent boundaries.
- [ ] Do not add a provider key or a second voice pipeline inside the MAX adapter.
- [ ] Define explicit behavior when voice conversion is unavailable: text fallback or clear error.

**Verification:** A voice message is transcribed by the existing Hermes path and the response is returned as text; failures do not create an infinite retry loop.

## Phase 5: Reliability, security, and incident mode

### Task 5.1: Operational reliability

- [ ] Add structured, redacted logs and counters for transport, queue, API, model, and user-facing outcomes.
- [ ] Add a health command that distinguishes DNS/TLS/auth/subscription/queue/model failures.
- [ ] Add an alert when Webhook receives no events for a configured window while the bot is expected to be active.
- [ ] Add startup subscription reconciliation and a safe, idempotent rollback path.
- [ ] Document reverse proxy, HTTPS/443, secret rotation, systemd, and restart behavior.

**Verification:** Inject DNS failure, CA failure, 401, 429, 5xx, queue saturation, and model timeout; each produces a distinct diagnosis and bounded recovery.

### Task 5.2: Security review

- [ ] Review token storage and process environment exposure.
- [ ] Check all logs and exceptions for token/message leakage.
- [ ] Verify default-deny allowlist and group policy.
- [ ] Verify callback authorization and replay resistance.
- [ ] Check downloaded media path traversal, MIME spoofing, and size limits.
- [ ] Document data processing, retention, and who can initiate agent actions.

**Verification:** Unauthorized and replay test suite passes; a static scan of logs/fixtures finds no credential-shaped values.

### Task 5.3: No-VPN field validation

- [ ] Select at least two Russian operators and two regions where test access is available.
- [ ] Test Wi-Fi and mobile data, VPN disabled and enabled as a comparison only.
- [ ] Test app installation/update from the actual target store/account matrix.
- [ ] Test inbound DM, outbound reply, callback, media, and cron delivery.
- [ ] Record restriction type and exact outcome instead of labeling the channel universally “white-listed”.

**Verification:** Publish a dated matrix with operator, region, device/store, network mode, timings, losses, duplicates, and screenshots/log ids. A pass is scoped to tested conditions.

## Phase 6: Community release and VK follow-up

### Task 6.1: MAX release

- [ ] Write installation and configuration docs in Russian and English.
- [ ] Include a migration note from the current prototype and explicitly list unsupported features.
- [ ] Add CI for unit, contract, lint, and sanitized integration fixtures.
- [ ] Publish a small changelog tied to the MAX API version/date.
- [ ] Ask Hermes maintainers whether the plugin should be listed in the community registry after the field gate.

**Verification:** A clean environment can install the plugin, configure a disposable bot, pass the smoke suite, and roll back to the prior plugin version.

### Task 6.2: VK adapter

- [ ] Reuse normalized policy, session, chunking, media, callback, idempotency, and observability modules.
- [ ] Keep VK transport and platform-specific upload/markup logic isolated.
- [ ] Repeat the same contract and field validation gates.

**Verification:** VK is not declared production-ready until it passes the same delivery, security, and incident metrics as MAX.

## Acceptance summary

The MAX plugin is ready for a limited community pilot only when:

- Hermes discovers and runs it through the current public plugin contract.
- A real disposable bot completes a text turn exactly once through Webhook.
- Long Polling works only in explicit dev mode.
- Allowlist, session isolation, commands, chunking, callbacks, and edit coalescing pass tests.
- TLS and MAX API migration diagnostics pass on the deployment host.
- Media/voice behavior is either tested or clearly marked unavailable.
- No-VPN network evidence is documented with scope and date.
- Rollback, token rotation, and incident diagnostics are documented.

The initial implementation checkpoint has completed the external plugin skeleton and text transport foundation. Phase 2.4, the disposable live MAX smoke without VPN, is the next release gate. Phases 3-5 provide Telegram-level parity and operational credibility; Phase 6 turns the result into a maintainable community contribution.
