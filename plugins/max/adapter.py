"""MAX Bot API v2 platform adapter for Hermes Agent.

This module is intentionally a third-party plugin boundary.  It does not add
MAX to Hermes core, so upgrading Hermes replaces the core installation without
overwriting this adapter.  Transport is handled by :mod:`.client`; the
adapter only translates MAX events into Hermes' normalized event contract.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import unquote, urlsplit

try:
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import (
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
    )
except ImportError:  # pragma: no cover - used only by standalone unit tests
    @dataclass
    class _FallbackMessageEvent:
        text: str
        message_type: Any = None
        source: Any = None
        raw_message: Any = None
        message_id: Optional[str] = None
        reply_to_message_id: Optional[str] = None
        media_urls: List[str] = None
        media_types: List[str] = None
        metadata: Dict[str, Any] = None

    @dataclass
    class _FallbackSendResult:
        success: bool
        message_id: Optional[str] = None
        error: Optional[str] = None
        raw_response: Any = None
        retryable: bool = False
        retry_after: Optional[float] = None
        continuation_message_ids: tuple = ()
        error_kind: Optional[str] = None

    class _FallbackBase:
        def __init__(self, config: Any, platform: Any) -> None:
            self.config = config
            self.platform = platform
            self._running = False

        def build_source(self, **kwargs: Any) -> Any:
            return SimpleNamespace(platform=self.platform, **kwargs)

        def _mark_connected(self) -> None:
            self._running = True

        def _mark_disconnected(self) -> None:
            self._running = False

        async def handle_message(self, _event: Any) -> None:
            return None

    BasePlatformAdapter = _FallbackBase  # type: ignore[misc,assignment]
    Platform = None  # type: ignore[assignment]
    PlatformConfig = Any  # type: ignore[assignment,misc]
    MessageEvent = _FallbackMessageEvent  # type: ignore[assignment]
    MessageType = SimpleNamespace(
        TEXT="text", COMMAND="command", PHOTO="photo", VIDEO="video", AUDIO="audio",
        VOICE="voice", DOCUMENT="document"
    )
    SendResult = _FallbackSendResult  # type: ignore[assignment]

from .client import DEFAULT_API_BASE, DEFAULT_MEDIA_MAX_BYTES, MaxApiError, MaxClient
from .common import AccessPolicy, split_message
from .interactive import MaxCallbackStore, build_inline_keyboard
from .media import (
    OUTBOUND_MEDIA_HOSTS,
    attachment_from_payload,
    is_allowed_outbound_media_url,
    media_type_for_file,
    mime_type_for_file,
)
from .models import MaxCallback, MaxMessage
from .polling_state import MaxTargetStore, PollingMarkerStore
from .rate_limit import MAX_MESSAGE_LENGTH, MaxRateLimiter, with_backoff
from .rate_limit import MAX_ATTACHMENTS_PER_MESSAGE
from .tls import tls_verify_from_env
from .webhook import MaxWebhookReceiver, WebhookResult

logger = logging.getLogger(__name__)

PLATFORM_NAME = "max"
PLATFORM_LABEL = "MAX Messenger"
PLATFORM_EMOJI = "💬"
PLATFORM_HINT = (
    "You are chatting via MAX. Keep a response within MAX's 4000-character message limit. "
    "Use Markdown only where it improves readability; availability depends on the configured "
    "MAX client and network, not on a universal whitelist guarantee."
)
MAX_UPDATE_TYPES = (
    "message_created",
    "message_callback",
    "bot_started",
    "bot_stopped",
    "dialog_removed",
)
GROUP_ADMIN_COMMANDS = frozenset(
    {
        "new", "stop", "model", "compress", "undo", "rollback", "sethome",
        "restart", "approve", "deny", "background", "queue", "resume",
    }
)
GROUP_ADMIN_CALLBACKS = frozenset({"approval", "slash", "model"})
GROUP_SESSION_SEPARATOR = "::user::"


def _is_max_rate_limit(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 429 or getattr(exc, "code", None) in {
        "too_many_requests",
        "rate_limit",
    }


def _retry_after(exc: BaseException) -> Optional[float]:
    value = getattr(exc, "retry_after", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_media_send_retryable(exc: BaseException) -> bool:
    return _is_max_rate_limit(exc) or getattr(exc, "code", None) in {
        "attachment.not.ready",
        "attachment_not_ready",
    }


def _media_retry_after(exc: BaseException) -> Optional[float]:
    retry_after = _retry_after(exc)
    if retry_after is not None:
        return retry_after
    if getattr(exc, "code", None) in {"attachment.not.ready", "attachment_not_ready"}:
        return 1.0
    return None


def _is_retryable_media_error(exc: BaseException) -> bool:
    return _is_media_send_retryable(exc) or bool(getattr(exc, "retryable", False))


def _safe_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        return parsed.port in (None, 443)
    except ValueError:
        return False


def _find_download_url(value: Any) -> Optional[str]:
    """Find a URL in GET /videos/{token} response without accepting tokens."""

    if isinstance(value, str) and _safe_https_url(value):
        return value
    if isinstance(value, Mapping):
        for key in ("url", "download_url", "mp4", "stream", "download"):
            found = _find_download_url(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = _find_download_url(nested)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_download_url(nested)
            if found:
                return found
    return None


def _message_type(message: MaxMessage) -> Any:
    if _is_command(message.text):
        return getattr(MessageType, "COMMAND", getattr(MessageType, "TEXT", None))
    for attachment in message.attachments:
        kind = str(attachment.get("type") or "").lower()
        if kind == "image":
            return getattr(MessageType, "PHOTO", getattr(MessageType, "TEXT", None))
        if kind in {"audio", "voice"}:
            message_kind = "VOICE" if kind == "voice" else "AUDIO"
            return getattr(MessageType, message_kind, getattr(MessageType, "VOICE", getattr(MessageType, "TEXT", None)))
        if kind == "video":
            return getattr(MessageType, "VIDEO", getattr(MessageType, "DOCUMENT", getattr(MessageType, "TEXT", None)))
        if kind == "file":
            return getattr(MessageType, "DOCUMENT", getattr(MessageType, "TEXT", None))
    return getattr(MessageType, "TEXT", None)


def _reply_message_id(link: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not link:
        return None
    for key in ("message", "message_id", "mid"):
        value = link.get(key)
        if value:
            return str(value)
    return None


def _reply_link(reply_to: Optional[str]) -> Optional[dict[str, str]]:
    return {"type": "reply", "mid": str(reply_to)} if reply_to else None


def _append_event_note(existing: Optional[str], note: str) -> str:
    if not note:
        return existing or ""
    if not existing:
        return note
    return f"{existing}\n\n{note}"


def _cache_media_bytes(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
    default_kind: str,
) -> Any:
    """Delegate media persistence to Hermes without importing it at module load."""

    from gateway.platforms.base import cache_media_bytes

    return cache_media_bytes(
        data,
        filename=filename,
        mime_type=mime_type,
        default_kind=default_kind,
    )


def _build_message_event(
    adapter: "MaxAdapter",
    message: MaxMessage,
    *,
    text: Optional[str] = None,
    media_urls: Optional[List[str]] = None,
    media_types: Optional[List[str]] = None,
) -> Any:
    """Translate a normalized MAX message through Hermes' public contract."""

    chat_type = "group" if message.is_group else "dm"
    session_chat_id = message.chat_id
    if message.is_group:
        session_chat_id = f"{message.chat_id}{GROUP_SESSION_SEPARATOR}{message.user_id}"
    source = adapter.build_source(
        chat_id=session_chat_id,
        chat_name=message.chat_title or message.user_name,
        chat_type=chat_type,
        user_id=message.user_id,
        user_name=message.user_name,
        message_id=message.message_id,
        role_authorized=False,
    )
    return MessageEvent(
        text=message.text if text is None else text,
        message_type=_message_type(message),
        source=source,
        raw_message=message.raw_message,
        message_id=message.message_id,
        reply_to_message_id=_reply_message_id(message.link),
        media_urls=list(media_urls or []),
        media_types=list(media_types or []),
        metadata={
            "max_chat_type": message.chat_type,
            "max_chat_id": message.chat_id,
            "max_target_type": "chat" if message.is_group else "user",
            "max_user_id": message.user_id,
        },
    )


def _is_command(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("/")


def _command_name(text: str) -> str:
    if not _is_command(text):
        return ""
    return text.lstrip().split(None, 1)[0].split("@", 1)[0].lstrip("/").lower()


def _transport_chat_id(
    chat_id: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    if metadata and metadata.get("max_chat_id"):
        return str(metadata["max_chat_id"])
    return str(chat_id).split(GROUP_SESSION_SEPARATOR, 1)[0]


def _response_message_id(response: Mapping[str, Any]) -> Optional[str]:
    message = response.get("message")
    if isinstance(message, Mapping):
        body = message.get("body")
        if isinstance(body, Mapping) and body.get("mid"):
            return str(body["mid"])
    for key in ("message_id", "id"):
        if response.get(key):
            return str(response[key])
    return None


def _send_result_from_ids(message_ids: Iterable[str]) -> Any:
    ids = [str(item) for item in message_ids if item]
    if not ids:
        return SendResult(success=True)
    return SendResult(
        success=True,
        message_id=ids[-1],
        continuation_message_ids=tuple(ids[:-1]),
    )


def _attachment_batches(
    attachments: Iterable[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    """Split MAX media into API-compatible messages.

    MAX permits images/videos together, but a file cannot be combined with
    them and only one file is allowed in a message.  Audio is kept separate
    as well because the current API does not document mixed audio batches.
    """

    batches: list[list[Mapping[str, Any]]] = []
    media_batch: list[Mapping[str, Any]] = []
    for raw_attachment in attachments:
        attachment = dict(raw_attachment)
        attachment_type = str(attachment.get("type") or "").strip().lower()
        if attachment_type in {"file", "audio"}:
            if media_batch:
                batches.append(media_batch)
                media_batch = []
            batches.append([attachment])
            continue
        media_batch.append(attachment)
        if len(media_batch) >= MAX_ATTACHMENTS_PER_MESSAGE:
            batches.append(media_batch)
            media_batch = []
    if media_batch:
        batches.append(media_batch)
    return batches


def _config_extra(config: Any) -> dict[str, Any]:
    value = getattr(config, "extra", {}) or {}
    return dict(value) if isinstance(value, Mapping) else {}


class MaxAdapter(BasePlatformAdapter):  # type: ignore[misc]
    """Webhook-first MAX adapter using Hermes' external plugin contract."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH
    splits_long_messages = True
    supports_code_blocks = False
    SUPPORTS_MESSAGE_EDITING = True

    def __init__(self, config: "PlatformConfig") -> None:
        platform = Platform(PLATFORM_NAME) if Platform is not None else PLATFORM_NAME
        super().__init__(config, platform)  # type: ignore[arg-type]
        self._config = config
        self._extra = _config_extra(config)
        self._token = str(
            getattr(config, "token", None)
            or self._extra.get("token")
            or os.environ.get("MAX_BOT_TOKEN", "")
        ).strip()
        self._api_base = str(
            self._extra.get("api_base_url")
            or os.environ.get("MAX_API_BASE_URL", DEFAULT_API_BASE)
        ).rstrip("/")
        self._webhook_url = str(
            self._extra.get("webhook_url") or os.environ.get("MAX_WEBHOOK_URL", "")
        ).strip() or None
        self._webhook_secret = str(
            self._extra.get("webhook_secret") or os.environ.get("MAX_WEBHOOK_SECRET", "")
        ).strip() or None
        self._polling_timeout = int(self._extra.get("polling_timeout", 30))
        self._access = AccessPolicy.from_env_and_extra(
            allowed_users_env=os.environ.get("MAX_ALLOWED_USERS"),
            group_allowed_users_env=os.environ.get("MAX_GROUP_ALLOWED_USERS"),
            group_allowed_chats_env=os.environ.get("MAX_GROUP_ALLOWED_CHATS"),
            allow_all_env=os.environ.get("MAX_ALLOW_ALL_USERS"),
            guest_mode_env=os.environ.get("MAX_GUEST_MODE"),
            admin_users_env=os.environ.get("MAX_ADMIN_USERS"),
            extra=self._extra,
        )
        self._require_mention = _truthy(
            self._extra.get("require_mention", os.environ.get("MAX_REQUIRE_MENTION", "true"))
        )
        self._client: Optional[MaxClient] = None
        self._receiver: Optional[MaxWebhookReceiver] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._bot_user_id: Optional[str] = None
        self._bot_username: Optional[str] = None
        self._chat_target_types: dict[str, str] = {}
        self._webhook_mode = bool(self._webhook_url)
        default_inbox = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        self._inbox_path = str(
            self._extra.get("inbox_path")
            or os.environ.get("MAX_INBOX_PATH", default_inbox / "max" / "webhook-inbox.sqlite3")
        )
        self._marker_path = str(
            self._extra.get("marker_path")
            or os.environ.get("MAX_MARKER_PATH", default_inbox / "max" / "polling-marker.sqlite3")
        )
        self._target_path = str(
            self._extra.get("target_path")
            or os.environ.get("MAX_TARGET_PATH", default_inbox / "max" / "targets.sqlite3")
        )
        self._marker_store: Optional[PollingMarkerStore] = None
        self._target_store: Optional[MaxTargetStore] = None
        self._rate_limiter = MaxRateLimiter()
        try:
            callback_ttl = float(
                self._extra.get(
                    "callback_ttl_seconds",
                    os.environ.get("MAX_CALLBACK_TTL_SECONDS", "600"),
                )
            )
        except (TypeError, ValueError):
            callback_ttl = 600.0
        self._callbacks = MaxCallbackStore(ttl_seconds=callback_ttl)
        self._model_pickers: dict[str, dict[str, Any]] = {}
        self._last_group_users: dict[str, tuple[str, float]] = {}
        try:
            self._media_max_bytes = int(
                self._extra.get(
                    "media_max_bytes",
                    os.environ.get("MAX_MEDIA_MAX_BYTES", str(DEFAULT_MEDIA_MAX_BYTES)),
                )
            )
        except (TypeError, ValueError):
            self._media_max_bytes = DEFAULT_MEDIA_MAX_BYTES
        if self._media_max_bytes <= 0:
            self._media_max_bytes = DEFAULT_MEDIA_MAX_BYTES
        self._media_max_bytes = min(self._media_max_bytes, DEFAULT_MEDIA_MAX_BYTES)
        self._lock_acquired = False

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self._token:
            logger.error("MAX_BOT_TOKEN is not configured")
            return False
        try:
            acquire_lock = getattr(self, "_acquire_platform_lock", None)
            if callable(acquire_lock):
                if not acquire_lock("max_bot_token", self._token, "MAX bot token"):
                    return False
                self._lock_acquired = True
            verify = tls_verify_from_env(
                {"MAX_CA_BUNDLE": str(self._extra.get("ca_bundle", os.environ.get("MAX_CA_BUNDLE", "")))}
            )
            self._client = MaxClient(self._token, base_url=self._api_base, verify=verify)
            bot = await self._client.get_me()
            self._bot_user_id = str(bot.get("user_id") or bot.get("id") or "") or None
            self._bot_username = str(bot.get("username") or "") or None
            await self._register_commands()
            self._running = True
            self._target_store = MaxTargetStore(self._target_path)

            if self._webhook_mode:
                if not self._webhook_secret:
                    raise ValueError("MAX_WEBHOOK_SECRET is required in Webhook mode")
                self._receiver = MaxWebhookReceiver(
                    self._webhook_secret,
                    inbox_path=self._inbox_path,
                )
                await self._client.subscribe_webhook(
                    self._webhook_url or "",
                    self._webhook_secret,
                    update_types=MAX_UPDATE_TYPES,
                )
                self._worker_task = asyncio.create_task(
                    self._consume_webhook_queue(), name="max-webhook-worker"
                )
            else:
                self._marker_store = PollingMarkerStore(self._marker_path)
                self._polling_task = asyncio.create_task(
                    self._poll_updates(), name="max-polling"
                )
            self._mark_connected()
            logger.info(
                "MAX adapter connected transport=%s reconnect=%s",
                "webhook" if self._webhook_mode else "polling",
                is_reconnect,
            )
            return True
        except (MaxApiError, ValueError, OSError) as exc:
            logger.error("MAX adapter connection failed: %s", exc)
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        self._running = False
        for task in (self._polling_task, self._worker_task):
            if task and not task.done():
                task.cancel()
        for task in (self._polling_task, self._worker_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._polling_task = None
        self._worker_task = None
        self._model_pickers.clear()
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._receiver is not None:
            await self._receiver.close()
        self._receiver = None
        if self._marker_store is not None:
            self._marker_store.close()
            self._marker_store = None
        if self._target_store is not None:
            self._target_store.close()
            self._target_store = None
        if self._lock_acquired:
            release_lock = getattr(self, "_release_platform_lock", None)
            if callable(release_lock):
                release_lock()
            self._lock_acquired = False
        try:
            self._mark_disconnected()
        except AttributeError:
            pass

    def _max_commands(self) -> list[dict[str, str]]:
        """Build a bounded menu from the installed Hermes command registry."""

        descriptions = {
            "menu": "Показать кнопки Hermes",
            "commands": "Показать список доступных команд",
            "maxstatus": "Состояние очереди MAX",
            "help": "Список доступных команд",
            "status": "Статус сессии и модели",
            "new": "Новая сессия",
            "stop": "Остановить фоновые задачи",
            "model": "Выбрать модель",
            "compress": "Сжать контекст",
            "sessions": "Предыдущие сессии",
            "resume": "Возобновить сессию",
            "retry": "Повторить последнее сообщение",
            "undo": "Отменить последний ход",
            "agents": "Активные задачи",
            "queue": "Поставить запрос в очередь",
            "background": "Запустить в фоне",
            "whoami": "Проверить доступ",
        }
        preferred = [
            "menu", "commands", "help", "status", "new", "stop", "model", "compress",
            "sessions", "resume", "retry", "undo", "agents", "whoami",
            "queue", "background", "maxstatus",
        ]
        registry: dict[str, Any] = {}
        try:
            from hermes_cli.commands import COMMAND_REGISTRY, _is_gateway_available

            for item in COMMAND_REGISTRY:
                if getattr(item, "cli_only", False):
                    continue
                if not _is_gateway_available(item):
                    continue
                registry[str(item.name).lower()] = item
        except Exception:  # pragma: no cover - only for reduced standalone runtimes
            registry = {}

        result: list[dict[str, str]] = []
        for name in preferred:
            if name in {"menu", "commands", "maxstatus"}:
                result.append({"name": name, "description": descriptions[name]})
                continue
            item = registry.get(name)
            if item is None:
                continue
            result.append(
                {
                    "name": name,
                    "description": descriptions.get(name, str(item.description)),
                }
            )
        return result[:32]

    async def _register_commands(self) -> None:
        if self._client is None or not callable(getattr(self._client, "set_bot_commands", None)):
            return
        commands = self._max_commands()
        try:
            await self._client.set_bot_commands(commands)
            logger.info(
                "MAX command menu registered (%d): %s",
                len(commands),
                ",".join(item["name"] for item in commands),
            )
        except (MaxApiError, ValueError) as exc:
            # Menu registration is best effort.  A MAX API rollout must not
            # disable text/media delivery when PATCH /me/commands is down.
            logger.warning("MAX command menu registration failed: %s", exc)

    async def _poll_updates(self) -> None:
        marker: Optional[int] = self._marker_store.get() if self._marker_store else None
        backoff = 1.0
        while self._running and self._client is not None:
            try:
                if self._marker_store is not None:
                    pending = self._marker_store.claim_next()
                    if pending is not None:
                        try:
                            await self._dispatch_update(pending)
                        except Exception as exc:  # noqa: BLE001
                            self._marker_store.mark_failed(pending, str(exc))
                            logger.exception(
                                "MAX polling update moved to failed state; "
                                "automatic replay is disabled"
                            )
                        else:
                            self._marker_store.mark_processed(pending)
                        continue
                result = await self._client.get_updates(
                    marker=marker,
                    timeout=self._polling_timeout,
                    types=MAX_UPDATE_TYPES,
                )
                next_marker = result.get("marker", marker)
                updates = [
                    item for item in (result.get("updates", []) or [])
                    if isinstance(item, Mapping)
                ]
                if self._marker_store is not None:
                    self._marker_store.accept_batch(
                        updates,
                        int(next_marker) if next_marker is not None else None,
                    )
                marker = int(next_marker) if next_marker is not None else marker
                backoff = 1.0
                if self._marker_store is None:
                    for update in updates:
                        await self._dispatch_update(update)
            except asyncio.CancelledError:
                return
            except MaxApiError as exc:
                if not self._running:
                    return
                delay = exc.retry_after or min(backoff, 30.0)
                logger.warning("MAX polling failed; retrying in %.1fs: %s", delay, exc)
                await asyncio.sleep(delay)
                backoff = min(backoff * 2.0, 30.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MAX polling failed; retrying in %.1fs: %s", backoff, exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2.0, 30.0)

    async def _consume_webhook_queue(self) -> None:
        if self._receiver is None:
            return
        while self._running:
            try:
                from_queue = True
                try:
                    update = await asyncio.wait_for(self._receiver.get_queued(), timeout=1.0)
                except asyncio.TimeoutError:
                    pending = await self._receiver.pending_updates(limit=1)
                    if not pending:
                        continue
                    update = pending[0]
                    from_queue = False
                try:
                    await self._receiver.mark_processing(update)
                    await self._dispatch_update(update)
                    await self._receiver.mark_processed(update)
                except Exception:
                    await self._receiver.mark_failed(update)
                    raise
                finally:
                    if from_queue:
                        self._receiver.queue.task_done()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.exception("MAX Webhook update processing failed")
                await asyncio.sleep(1.0)

    async def handle_webhook(
        self, headers: Mapping[str, str], update: Mapping[str, Any]
    ) -> WebhookResult:
        """Accept one Webhook update for a reverse proxy/ASGI bridge.

        The method returns a status decision; the HTTP server must send that
        status immediately and leave model processing to the worker.
        """

        if self._receiver is None:
            return WebhookResult(status_code=503, accepted=False)
        return await self._receiver.receive(headers, update)

    async def _populate_message_media(self, message: MaxMessage, event: Any) -> None:
        """Download inbound MAX attachments into Hermes' local media cache."""

        if self._client is None:
            return
        for raw_attachment in message.attachments:
            attachment = attachment_from_payload(raw_attachment)
            if attachment is None:
                continue
            media_url = attachment.url
            if not media_url and attachment.kind == "video" and attachment.token:
                try:
                    video_info = await self._client.get_video(attachment.token)
                    media_url = _find_download_url(video_info)
                except (MaxApiError, ValueError) as exc:
                    logger.warning("MAX video token resolution failed: %s", exc)
            if not media_url:
                event.text = _append_event_note(
                    event.text,
                    f"[Вложение MAX «{attachment.filename}» не удалось получить для обработки.]",
                )
                continue
            try:
                data, remote_mime = await self._client.download_media(
                    media_url,
                    max_bytes=getattr(self, "_media_max_bytes", DEFAULT_MEDIA_MAX_BYTES),
                )
                cached = _cache_media_bytes(
                    data,
                    filename=attachment.filename,
                    mime_type=remote_mime or attachment.mime_type,
                    default_kind=attachment.kind,
                )
            except (MaxApiError, OSError, ValueError) as exc:
                logger.warning(
                    "MAX media download/cache failed kind=%s error=%s",
                    attachment.kind,
                    exc,
                )
                event.text = _append_event_note(
                    event.text,
                    f"[Вложение MAX «{attachment.filename}» не удалось скачать: проверьте формат и размер.]",
                )
                continue
            except Exception:  # noqa: BLE001
                logger.exception(
                    "MAX media cache failed kind=%s",
                    attachment.kind,
                )
                event.text = _append_event_note(
                    event.text,
                    f"[Вложение MAX «{attachment.filename}» не удалось сохранить для Hermes.]",
                )
                continue

            if cached is None:
                event.text = _append_event_note(
                    event.text,
                    f"[Вложение MAX «{attachment.filename}» не распознано как читаемый файл.]",
                )
                continue
            event.media_urls.append(cached.path)
            event.media_types.append(cached.media_type)
            event.text = _append_event_note(event.text, cached.context_note())
            logger.info("MAX inbound media cached kind=%s", attachment.kind)

    async def _dispatch_update(self, update: Mapping[str, Any]) -> None:
        update_type = str(update.get("update_type") or "")
        if update_type == "bot_started":
            await self._dispatch_bot_started(update)
            return
        if update_type in {"bot_stopped", "dialog_removed"}:
            return
        callback = MaxCallback.from_update(update)
        if callback is not None:
            await self._dispatch_callback(callback)
            return

        message = MaxMessage.from_update(update)
        if message is None:
            return
        if self._bot_user_id and message.user_id == self._bot_user_id and message.is_bot:
            return

        is_group = message.is_group
        target_type = "chat" if is_group else "user"
        self._chat_target_types[message.chat_id] = target_type
        if self._target_store is not None:
            self._target_store.set(message.chat_id, target_type)
        mentioned = self._is_mentioned(message.text)
        if is_group:
            if not self._access.can_group(message.user_id, message.chat_id, mentioned=mentioned):
                return
            if self._require_mention and not mentioned and not _is_command(message.text):
                return
        elif not self._access.can_dm(message.user_id):
            return
        if _is_command(message.text) and not self._access.can_run_command(
            message.user_id, message.text, is_group=is_group
        ):
            return
        if is_group and _command_name(message.text) in GROUP_ADMIN_COMMANDS and not self._access.is_admin(message.user_id):
            await self.send(
                message.chat_id,
                "Эта управляющая команда доступна только администратору группы.",
                metadata={"max_target_type": "chat", "max_user_id": message.user_id},
            )
            return
        self._remember_group_user(message.chat_id, message.user_id, is_group=is_group)
        if await self._handle_local_command(message):
            return
        event = _build_message_event(self, message)
        await self._populate_message_media(message, event)
        await self.handle_message(event)

    async def _dispatch_bot_started(self, update: Mapping[str, Any]) -> None:
        user = update.get("user") if isinstance(update.get("user"), Mapping) else {}
        user_id = str(user.get("user_id") or user.get("id") or "").strip()
        if not user_id:
            return
        chat_id = str(update.get("chat_id") or user_id).strip()
        is_group = bool(update.get("is_channel")) or chat_id != user_id
        if is_group:
            if not self._access.can_group(user_id, chat_id, mentioned=True):
                return
            target_type = "chat"
        else:
            if not self._access.can_dm(user_id):
                return
            target_type = "user"
        self._chat_target_types[chat_id] = target_type
        if self._target_store is not None:
            self._target_store.set(chat_id, target_type)
        self._remember_group_user(chat_id, user_id, is_group=is_group)
        await self._send_menu(
            chat_id,
            user_id,
            target_type=target_type,
            welcome=True,
        )

    def _remember_group_user(self, chat_id: str, user_id: str, *, is_group: bool) -> None:
        if is_group:
            if not hasattr(self, "_last_group_users"):
                self._last_group_users = {}
            self._last_group_users[str(chat_id)] = (str(user_id), time.monotonic())

    async def _handle_local_command(self, message: MaxMessage) -> bool:
        command = _command_name(message.text)
        if command == "menu":
            await self._send_menu(
                message.chat_id,
                message.user_id,
                target_type="chat" if message.is_group else "user",
            )
            return True
        if command == "commands":
            await self._send_menu(
                message.chat_id,
                message.user_id,
                target_type="chat" if message.is_group else "user",
            )
            return True
        if command == "maxstatus":
            await self.send(
                message.chat_id,
                self._max_status_text(),
                metadata={
                    "max_target_type": "chat" if message.is_group else "user",
                    "max_user_id": message.user_id,
                },
            )
            return True
        if command == "start":
            await self._send_menu(
                message.chat_id,
                message.user_id,
                target_type="chat" if message.is_group else "user",
                welcome=True,
            )
            return True
        return False

    async def _dispatch_callback(self, callback: MaxCallback) -> None:
        """Resolve a MAX button through Hermes' native interactive hooks."""

        self._remember_group_user(callback.chat_id, callback.user_id, is_group=callback.is_group)
        if callback.is_group:
            authorized = self._access.can_group(
                callback.user_id, callback.chat_id, mentioned=False
            )
        else:
            authorized = self._access.can_dm(callback.user_id)
        if not authorized:
            await self._answer_callback(callback, "Нет доступа к этой кнопке.")
            return

        pending_entry = self._callbacks.peek(callback.payload)
        if (
            callback.is_group
            and pending_entry is not None
            and (
                pending_entry.kind in GROUP_ADMIN_CALLBACKS
                or (
                    pending_entry.kind == "command"
                    and pending_entry.value in GROUP_ADMIN_COMMANDS
                )
            )
            and not self._access.is_admin(callback.user_id)
        ):
            await self._answer_callback(callback, "Это действие доступно только администратору группы.")
            return

        entry = self._callbacks.consume(
            callback.payload,
            user_id=callback.user_id,
            chat_id=callback.chat_id,
        )
        if entry is None:
            await self._answer_callback(callback, "Кнопка устарела или уже использована.")
            return

        try:
            if entry.kind == "command":
                if (
                    callback.is_group
                    and entry.value in GROUP_ADMIN_COMMANDS
                    and not self._access.is_admin(callback.user_id)
                ):
                    await self._answer_callback(
                        callback,
                        "Эта команда доступна только администратору группы.",
                    )
                    return
                await self._answer_callback(callback, "Команда выполняется.")
                command_message = MaxMessage(
                    message_id=callback.message_id or f"callback:{callback.callback_id}",
                    user_id=callback.user_id,
                    user_name=callback.user_name,
                    chat_id=callback.chat_id,
                    chat_type=callback.chat_type,
                    chat_title=None,
                    text=f"/{entry.value}",
                    link={"message_id": callback.message_id} if callback.message_id else None,
                    raw_message=callback.raw_message,
                )
                if await self._handle_local_command(command_message):
                    return
                event = _build_message_event(self, command_message)
                await self._populate_message_media(command_message, event)
                await self.handle_message(event)
                return

            if entry.kind == "approval":
                from tools.approval import resolve_gateway_approval

                count = resolve_gateway_approval(entry.session_key, entry.value)
                labels = {
                    "once": "Разрешено один раз",
                    "session": "Разрешено на сессию",
                    "always": "Разрешено всегда",
                    "deny": "Запрещено",
                }
                label = labels.get(entry.value, "Запрос обработан") if count else "Запрос уже завершен"
                await self._answer_callback(callback, label)
                if count:
                    resume = getattr(self, "resume_typing_for_chat", None)
                    if callable(resume):
                        resume(callback.chat_id)
                return

            if entry.kind == "slash":
                from tools import slash_confirm

                choice, confirm_id = entry.value.split(":", 1)
                result_text = await slash_confirm.resolve(
                    entry.session_key, confirm_id, choice
                )
                labels = {
                    "once": "Подтверждено один раз",
                    "always": "Подтверждено всегда",
                    "cancel": "Отменено",
                }
                await self._answer_callback(callback, labels.get(choice, "Запрос обработан"))
                if result_text:
                    await self.send(
                        callback.chat_id,
                        str(result_text),
                        metadata={
                            "max_target_type": "chat" if callback.is_group else "user"
                        },
                    )
                return

            if entry.kind == "clarify":
                clarify_id, choice_token = entry.value.split(":", 1)
                if choice_token == "other":
                    from tools.clarify_gateway import mark_awaiting_text

                    if mark_awaiting_text(clarify_id):
                        await self._answer_callback(
                            callback, "Введите свой вариант следующим сообщением."
                        )
                    else:
                        await self._answer_callback(callback, "Запрос уже завершен.")
                    return

                idx = int(choice_token)
                resolved_text: Optional[str] = None
                try:
                    from tools.clarify_gateway import _entries as clarify_entries

                    clarify_entry = clarify_entries.get(clarify_id)
                    if clarify_entry and clarify_entry.choices and 0 <= idx < len(clarify_entry.choices):
                        resolved_text = str(clarify_entry.choices[idx])
                except Exception:
                    resolved_text = None
                resolved_text = resolved_text or f"choice {idx + 1}"

                from tools.clarify_gateway import resolve_gateway_clarify

                resolved = resolve_gateway_clarify(clarify_id, resolved_text)
                await self._answer_callback(
                    callback,
                    f"Выбрано: {resolved_text}" if resolved else "Запрос уже завершен.",
                )
                return

            if entry.kind == "model":
                await self._dispatch_model_callback(callback, entry.value)
                return

            await self._answer_callback(callback, "Неизвестная кнопка.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("MAX callback resolution failed: %s", exc, exc_info=True)
            await self._answer_callback(callback, "Не удалось обработать кнопку.")

    async def _dispatch_model_callback(
        self, callback: MaxCallback, value: str
    ) -> None:
        parts = str(value).split(":", 2)
        if len(parts) < 2:
            await self._answer_callback(callback, "Кнопка выбора модели некорректна.")
            return
        picker_id, action = parts[0], parts[1]
        argument = parts[2] if len(parts) == 3 else ""
        state = self._model_pickers.get(picker_id)
        if state is None:
            await self._answer_callback(callback, "Выбор модели устарел. Откройте /model заново.")
            return

        if action == "cancel":
            self._model_pickers.pop(picker_id, None)
            await self._answer_callback(callback, "Выбор модели отменен.")
            return

        if action == "back":
            rows = self._model_provider_rows(state, picker_id, callback)
            await self._answer_callback(
                callback,
                self._model_picker_provider_text(state),
                attachments=[build_inline_keyboard(rows)],
            )
            return

        if action == "provider":
            provider = next(
                (item for item in state["providers"] if str(item.get("slug")) == argument),
                None,
            )
            if provider is None:
                await self._answer_callback(callback, "Провайдер не найден.")
                return
            state["selected_provider"] = argument
            state["selected_provider_name"] = str(provider.get("name") or argument)
            state["model_list"] = [str(model) for model in provider.get("models", [])]
            rows = self._model_rows(state, picker_id, callback)
            await self._answer_callback(
                callback,
                self._model_picker_model_text(state),
                attachments=[build_inline_keyboard(rows)],
            )
            return

        if action == "model":
            try:
                index = int(argument)
            except ValueError:
                await self._answer_callback(callback, "Модель указана некорректно.")
                return
            models = state.get("model_list", [])
            if index < 0 or index >= len(models):
                await self._answer_callback(callback, "Модель не найдена.")
                return
            on_model_selected = state.get("on_model_selected")
            if not callable(on_model_selected):
                self._model_pickers.pop(picker_id, None)
                await self._answer_callback(callback, "Выбор модели устарел.")
                return
            model_id = str(models[index])
            provider_slug = str(state.get("selected_provider") or "")
            try:
                result_text = on_model_selected(callback.chat_id, model_id, provider_slug)
                if inspect.isawaitable(result_text):
                    result_text = await result_text
                result_text = str(result_text or "Модель переключена.")
            except Exception as exc:  # noqa: BLE001
                logger.warning("MAX model picker switch failed: %s", exc, exc_info=True)
                result_text = "Не удалось переключить модель."
            self._model_pickers.pop(picker_id, None)
            await self._answer_callback(callback, result_text)
            return

        await self._answer_callback(callback, "Неизвестное действие выбора модели.")

    async def _answer_callback(
        self,
        callback: MaxCallback,
        text: str,
        *,
        attachments: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> None:
        if self._client is None:
            return
        body = {
            "text": str(text)[:MAX_MESSAGE_LENGTH],
            "attachments": [dict(item) for item in attachments] if attachments is not None else [],
            "format": "markdown",
        }
        try:
            await self._rate_limiter.acquire(callback.chat_id)

            async def _answer() -> Mapping[str, Any]:
                return await self._client.answer_callback(
                    callback.callback_id,
                    message=body,
                )

            await with_backoff(
                _answer,
                is_rate_limit=_is_max_rate_limit,
                extract_retry_after=_retry_after,
            )
        except MaxApiError as exc:
            logger.warning("MAX callback answer failed: %s", exc)

    def _is_mentioned(self, text: str) -> bool:
        return bool(self._bot_username and f"@{self._bot_username.lower()}" in text.lower())

    def _target_type_for(
        self, chat_id: str, metadata: Optional[Mapping[str, Any]] = None
    ) -> str:
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        target_type = self._chat_target_types.get(transport_chat_id)
        target_store = getattr(self, "_target_store", None)
        if target_type is None and target_store is not None:
            target_type = target_store.get(transport_chat_id)
        target_type = target_type or "user"
        if metadata and metadata.get("max_target_type") in {"user", "chat"}:
            target_type = str(metadata["max_target_type"])
        return target_type

    def _max_status_text(self) -> str:
        transport = "webhook" if self._webhook_mode else "polling"
        summary: Mapping[str, Any] = {}
        if self._marker_store is not None:
            summary = self._marker_store.status_summary()
        elif self._receiver is not None:
            summary = self._receiver.status_summary()
        pending = int(summary.get("pending", 0) or 0)
        processing = int(summary.get("processing", 0) or 0)
        failed = int(summary.get("failed", 0) or 0)
        last_error = str(summary.get("last_error") or "")
        error_category = "нет"
        if last_error:
            lowered = last_error.lower()
            if "429" in lowered or "rate" in lowered:
                error_category = "rate limit"
            elif "timeout" in lowered or "transport" in lowered:
                error_category = "сеть/таймаут"
            elif "media" in lowered or "attachment" in lowered:
                error_category = "вложение"
            elif "api" in lowered:
                error_category = "api"
            else:
                error_category = "обработка"
        return (
            "**MAX: диагностика**\n\n"
            f"Транспорт: `{transport}`\n"
            f"Очередь: pending={pending}, processing={processing}, failed={failed}\n"
            f"Последняя категория ошибки: `{error_category}`\n"
            "Повторная обработка неоднозначных событий автоматически не выполняется."
        )

    def _command_list_text(self, *, welcome: bool = False) -> str:
        command_lines = "\n".join(
            f"/{item['name']} — {item['description']}"
            for item in self._max_commands()
        )
        heading = "Hermes готов к работе." if welcome else "Доступные команды Hermes:"
        return f"{heading}\n\n{command_lines}\n\nВыберите действие:"[:MAX_MESSAGE_LENGTH]

    async def _send_menu(
        self,
        chat_id: str,
        user_id: str,
        *,
        target_type: str,
        welcome: bool = False,
    ) -> None:
        if self._client is None:
            return
        label_map = {
            "commands": "Команды",
            "help": "Помощь",
            "status": "Статус",
            "new": "Новая сессия",
            "model": "Модель",
            "stop": "Остановить",
            "compress": "Сжать контекст",
            "maxstatus": "Диагностика MAX",
        }
        available = {item["name"] for item in self._max_commands()}
        button_names = [
            name
            for name in (
                "commands", "help", "status", "new", "model", "stop", "compress", "maxstatus"
            )
            if name in available
        ]
        rows: list[list[Mapping[str, str]]] = []
        buttons: list[Mapping[str, str]] = []
        for name in button_names:
            bound_user = "*" if target_type == "chat" and name in GROUP_ADMIN_COMMANDS else str(user_id)
            payload = self._callbacks.issue(
                "command",
                name,
                user_id=bound_user,
                chat_id=str(chat_id),
                session_key="",
            )
            buttons.append({"type": "callback", "text": label_map[name], "payload": payload})
        for index in range(0, len(buttons), 2):
            rows.append(buttons[index : index + 2])
        text = self._command_list_text(welcome=welcome)
        try:
            await self._rate_limiter.acquire(str(chat_id))

            async def _send() -> Mapping[str, Any]:
                return await self._client.send_message(
                    str(chat_id),
                    text,
                    target_type=target_type,
                    attachments=[build_inline_keyboard(rows)] if rows else None,
                )

            await with_backoff(
                _send,
                is_rate_limit=_is_max_rate_limit,
                extract_retry_after=_retry_after,
            )
        except MaxApiError as exc:
            logger.warning("MAX menu delivery failed: %s", exc)

    def _interactive_context(
        self, chat_id: str, metadata: Optional[Mapping[str, Any]] = None
    ) -> Optional[tuple[str, str]]:
        target_type = self._target_type_for(chat_id, metadata)
        user_id = ""
        if metadata:
            for key in ("max_user_id", "user_id", "sender_id"):
                if metadata.get(key):
                    user_id = str(metadata[key]).strip()
                    break
        if not user_id and target_type == "user":
            user_id = str(chat_id).strip()
        if not user_id and target_type == "chat":
            remembered = getattr(self, "_last_group_users", {}).get(str(chat_id))
            if remembered and time.monotonic() - remembered[1] <= 600.0:
                user_id = remembered[0]
        if not user_id:
            return None
        return target_type, user_id

    async def _send_interactive_prompt(
        self,
        chat_id: str,
        content: str,
        rows: list[list[Mapping[str, str]]],
        *,
        reply_to: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        if not content:
            return SendResult(success=False, error="MAX message is empty")
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound message",
            )
        target_type, _user_id = context
        try:
            await self._rate_limiter.acquire(transport_chat_id)

            async def _send() -> Mapping[str, Any]:
                return await self._client.send_message(
                    transport_chat_id,
                    content[:MAX_MESSAGE_LENGTH],
                    target_type=target_type,
                    link=_reply_link(reply_to),
                    attachments=[build_inline_keyboard(rows)],
                )

            response = await with_backoff(
                _send,
                is_rate_limit=_is_max_rate_limit,
                extract_retry_after=_retry_after,
            )
        except MaxApiError as exc:
            return SendResult(
                success=False,
                error=str(exc),
                retryable=exc.retryable,
                retry_after=exc.retry_after,
            )
        return SendResult(
            success=True,
            message_id=_response_message_id(response),
            raw_response=response,
        )

    def _validated_media_path(self, file_path: str | Path) -> Optional[str]:
        validator = getattr(self, "validate_media_delivery_path", None)
        if callable(validator):
            try:
                safe_path = validator(str(file_path))
            except (OSError, RuntimeError, ValueError):
                return None
            return str(safe_path) if safe_path else None
        try:
            path = Path(file_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None
        return str(path) if path.is_file() else None

    async def _send_local_attachment(
        self,
        chat_id: str,
        file_path: str | Path,
        *,
        media_type: Optional[str] = None,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        force_document: bool = False,
    ) -> Any:
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        safe_path = self._validated_media_path(file_path)
        if not safe_path:
            return SendResult(success=False, error="MAX attachment path is not allowed")
        actual_type = media_type or media_type_for_file(
            safe_path, force_document=force_document
        )
        if actual_type not in {"image", "video", "audio", "file"}:
            return SendResult(success=False, error="MAX attachment type is not supported")
        try:
            upload_kwargs: dict[str, Any] = {
                "media_type": actual_type,
                "max_bytes": getattr(self, "_media_max_bytes", DEFAULT_MEDIA_MAX_BYTES),
                "mime_type": mime_type_for_file(safe_path, actual_type),
            }
            if file_name:
                upload_kwargs["filename"] = str(file_name)
            upload = await with_backoff(
                lambda: self._client.upload_media(safe_path, **upload_kwargs),
                is_rate_limit=_is_retryable_media_error,
                extract_retry_after=_media_retry_after,
                max_attempts=5,
            )
            token = str(upload.get("token") or "").strip()
            if not token:
                raise MaxApiError("MAX media upload returned no attachment token")
            target_type = self._target_type_for(transport_chat_id, metadata)
            attachment = {
                "type": actual_type,
                "payload": {"token": token},
            }
            await self._rate_limiter.acquire(transport_chat_id)

            async def _send() -> Mapping[str, Any]:
                return await self._client.send_message(
                    transport_chat_id,
                    str(caption or "")[:MAX_MESSAGE_LENGTH],
                    target_type=target_type,
                    link=_reply_link(reply_to),
                    attachments=[attachment],
                )

            response = await with_backoff(
                _send,
                is_rate_limit=_is_media_send_retryable,
                extract_retry_after=_media_retry_after,
                max_attempts=5,
            )
        except (MaxApiError, OSError, ValueError) as exc:
            return SendResult(
                success=False,
                error=str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
                retry_after=getattr(exc, "retry_after", None),
            )
        return SendResult(
            success=True,
            message_id=_response_message_id(response),
            raw_response=response,
        )

    async def _send_remote_image(
        self,
        chat_id: str,
        image_url: str,
        *,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if not _safe_https_url(image_url) or not is_allowed_outbound_media_url(image_url):
            return SendResult(success=False, error="MAX image URL is not allowed")
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        target_type = self._target_type_for(transport_chat_id, metadata)
        try:
            data, remote_mime = await self._client.download_media(
                image_url,
                max_bytes=getattr(self, "_media_max_bytes", DEFAULT_MEDIA_MAX_BYTES),
                allowed_hosts=OUTBOUND_MEDIA_HOSTS,
            )
            filename = Path(urlsplit(image_url).path).name or "image.jpg"
            upload = await with_backoff(
                lambda: self._client.upload_media_bytes(
                    data,
                    filename=filename,
                    media_type="image",
                    max_bytes=getattr(self, "_media_max_bytes", DEFAULT_MEDIA_MAX_BYTES),
                    mime_type=remote_mime if str(remote_mime).startswith("image/") else "image/jpeg",
                ),
                is_rate_limit=_is_retryable_media_error,
                extract_retry_after=_media_retry_after,
                max_attempts=5,
            )
            token = str(upload.get("token") or "").strip()
            if not token:
                raise MaxApiError("MAX media upload returned no attachment token")
            attachment = {"type": "image", "payload": {"token": token}}
            await self._rate_limiter.acquire(transport_chat_id)

            async def _send() -> Mapping[str, Any]:
                return await self._client.send_message(
                    transport_chat_id,
                    str(caption or "")[:MAX_MESSAGE_LENGTH],
                    target_type=target_type,
                    link=_reply_link(reply_to),
                    attachments=[attachment],
                )

            response = await with_backoff(
                _send,
                is_rate_limit=_is_media_send_retryable,
                extract_retry_after=_media_retry_after,
                max_attempts=5,
            )
        except (MaxApiError, OSError, ValueError) as exc:
            return SendResult(
                success=False,
                error=str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
                retry_after=getattr(exc, "retry_after", None),
            )
        return SendResult(success=True, message_id=_response_message_id(response), raw_response=response)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        value = str(image_url or "").strip()
        if value.startswith("file://"):
            return await self.send_image_file(
                chat_id,
                unquote(value[7:]),
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        if not urlsplit(value).scheme:
            return await self.send_image_file(
                chat_id,
                value,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        return await self._send_remote_image(
            chat_id, value, caption=caption, reply_to=reply_to, metadata=metadata
        )

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return await self.send_image(
            chat_id,
            animation_url,
            caption=caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        return await self._send_local_attachment(
            chat_id,
            image_path,
            media_type="image",
            caption=caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        return await self._send_local_attachment(
            chat_id,
            file_path,
            media_type="file",
            caption=caption,
            file_name=file_name,
            reply_to=reply_to,
            metadata=metadata,
            force_document=True,
        )

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        return await self._send_local_attachment(
            chat_id,
            audio_path,
            media_type="audio",
            caption=caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        return await self._send_local_attachment(
            chat_id,
            video_path,
            media_type="video",
            caption=caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def send_multiple_images(
        self,
        chat_id: str,
        images: list[tuple[str, str]],
        metadata: Optional[Dict[str, Any]] = None,
        human_delay: float = 0.0,
    ) -> None:
        if len(images) > MAX_ATTACHMENTS_PER_MESSAGE:
            logger.info(
                "MAX image batch has %d items; sending in separate messages",
                len(images),
            )
        for image_url, alt_text in images:
            if human_delay > 0:
                await asyncio.sleep(human_delay)
            result = await self.send_image(
                chat_id,
                image_url,
                caption=alt_text or None,
                metadata=metadata,
            )
            if not getattr(result, "success", False):
                logger.warning("MAX image delivery failed: %s", getattr(result, "error", result))

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        if self._client is None:
            return
        try:
            await self._rate_limiter.acquire(transport_chat_id)
            await with_backoff(
                lambda: self._client.send_action(transport_chat_id, "typing"),
                is_rate_limit=_is_max_rate_limit,
                extract_retry_after=_retry_after,
            )
        except (MaxApiError, ValueError) as exc:
            logger.debug("MAX typing action failed: %s", exc)

    async def stop_typing(self, chat_id: str) -> None:
        transport_chat_id = _transport_chat_id(chat_id)
        if self._client is None:
            return
        try:
            await self._rate_limiter.acquire(transport_chat_id)
            await self._client.send_action(transport_chat_id, "typing_off")
        except (MaxApiError, ValueError) as exc:
            logger.debug("MAX typing_off action failed: %s", exc)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        if not content:
            return SendResult(success=False, error="MAX message is empty")
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        media_files, cleaned_content = self._extract_outbound_media(content)
        if media_files:
            return await self._send_media_files(
                chat_id,
                cleaned_content,
                media_files,
                reply_to=reply_to,
                metadata=metadata,
                force_document="[[as_document]]" in content,
            )
        if "MEDIA:" in content and callable(getattr(self, "extract_media", None)):
            return SendResult(success=False, error="MAX media directive contains no safe local file")
        if "MEDIA:" in content and not callable(getattr(self, "extract_media", None)):
            return SendResult(
                success=False,
                error="MAX media extraction is unavailable in this Hermes runtime",
                retryable=False,
            )

        target_type = self._target_type_for(transport_chat_id, metadata)
        message_ids: list[str] = []
        for index, chunk in enumerate(split_message(content, MAX_MESSAGE_LENGTH)):
            link = _reply_link(reply_to) if index == 0 else None
            await self._rate_limiter.acquire(transport_chat_id)

            async def _send() -> Mapping[str, Any]:
                return await self._client.send_message(
                    transport_chat_id,
                    chunk,
                    target_type=target_type,
                    link=link,
                )

            try:
                response = await with_backoff(
                    _send,
                    is_rate_limit=_is_max_rate_limit,
                    extract_retry_after=_retry_after,
                )
            except MaxApiError as exc:
                return SendResult(
                    success=False,
                    error=str(exc),
                    retryable=exc.retryable,
                    retry_after=exc.retry_after,
                )
            message_id = _response_message_id(response)
            if message_id:
                message_ids.append(message_id)
        return _send_result_from_ids(message_ids)

    def _extract_outbound_media(self, content: str) -> tuple[list, str]:
        if "MEDIA:" not in content:
            return [], content
        extractor = getattr(self, "extract_media", None)
        if not callable(extractor):
            return [], content
        try:
            media_files, cleaned = extractor(content)
            filter_paths = getattr(self, "filter_media_delivery_paths", None)
            if callable(filter_paths):
                media_files = filter_paths(media_files)
            return list(media_files or []), str(cleaned or "")
        except Exception:  # noqa: BLE001
            logger.exception("MAX outbound media extraction failed")
            return [], content

    async def _send_media_files(
        self,
        chat_id: str,
        content: str,
        media_files: list,
        *,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force_document: bool = False,
    ) -> Any:
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        target_type = self._target_type_for(transport_chat_id, metadata)
        attachments: list[Mapping[str, Any]] = []
        if not media_files:
            return SendResult(success=False, error="MAX media attachment list is empty")
        upload_errors: list[str] = []
        for item in media_files:
            try:
                if isinstance(item, (tuple, list)):
                    media_path = str(item[0])
                    is_voice = bool(item[1]) if len(item) > 1 else False
                else:
                    media_path = str(item)
                    is_voice = False
                media_type = media_type_for_file(
                    media_path,
                    is_voice=is_voice,
                    force_document=force_document,
                )
                upload = await with_backoff(
                    lambda: self._client.upload_media(
                        media_path,
                        media_type=media_type,
                        max_bytes=getattr(self, "_media_max_bytes", DEFAULT_MEDIA_MAX_BYTES),
                        mime_type=mime_type_for_file(media_path, media_type),
                    ),
                    is_rate_limit=_is_retryable_media_error,
                    extract_retry_after=_media_retry_after,
                    max_attempts=5,
                )
                if not isinstance(upload, Mapping):
                    raise MaxApiError("MAX media upload returned an invalid response")
                token = str(upload.get("token") or "").strip()
                if not token:
                    raise MaxApiError("MAX media upload returned no attachment token")
                attachments.append({"type": media_type, "payload": {"token": token}})
            except (MaxApiError, OSError, TypeError, ValueError) as exc:
                upload_errors.append(str(exc))

        if not attachments:
            return SendResult(
                success=False,
                error="; ".join(upload_errors) or "MAX media uploads failed",
                error_kind="media_upload",
            )

        chunks = split_message(content, MAX_MESSAGE_LENGTH) if content else [""]
        attachment_batches = _attachment_batches(attachments)
        message_ids: list[str] = []
        send_errors: list[str] = []
        for batch_index, batch in enumerate(attachment_batches):
            await self._rate_limiter.acquire(transport_chat_id)
            chunk = chunks[0] if batch_index == 0 else ""
            link = _reply_link(reply_to) if batch_index == 0 else None

            async def _send() -> Mapping[str, Any]:
                return await self._client.send_message(
                    transport_chat_id,
                    chunk,
                    target_type=target_type,
                    link=link,
                    attachments=batch,
                )

            try:
                response = await with_backoff(
                    _send,
                    is_rate_limit=_is_retryable_media_error,
                    extract_retry_after=_media_retry_after,
                    max_attempts=5,
                )
            except MaxApiError as exc:
                send_errors.append(str(exc))
                continue
            message_id = _response_message_id(response)
            if message_id:
                message_ids.append(message_id)

        if message_ids:
            for chunk in chunks[1:]:
                await self._rate_limiter.acquire(transport_chat_id)

                async def _send_continuation() -> Mapping[str, Any]:
                    return await self._client.send_message(
                        transport_chat_id,
                        chunk,
                        target_type=target_type,
                    )

                try:
                    response = await with_backoff(
                        _send_continuation,
                        is_rate_limit=_is_max_rate_limit,
                        extract_retry_after=_retry_after,
                    )
                except MaxApiError as exc:
                    send_errors.append(str(exc))
                    continue
                message_id = _response_message_id(response)
                if message_id:
                    message_ids.append(message_id)

        if not message_ids:
            return SendResult(
                success=False,
                error="; ".join(upload_errors + send_errors)
                or "MAX media messages failed",
                error_kind="media_send",
            )
        result = _send_result_from_ids(message_ids)
        if upload_errors or send_errors:
            return SendResult(
                success=False,
                message_id=getattr(result, "message_id", None),
                continuation_message_ids=getattr(result, "continuation_message_ids", ()),
                raw_response=getattr(result, "raw_response", None),
                error="Часть вложений не доставлена: "
                + "; ".join(upload_errors + send_errors),
                error_kind="partial_media",
            )
        return result

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not choices:
            return await self.send(chat_id, f"❓ {question}", metadata=metadata)

        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound message",
            )
        _target_type, user_id = context
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        rows: list[list[Mapping[str, str]]] = []
        option_lines = [f"{index + 1}. {choice}" for index, choice in enumerate(choices)]
        for index, _choice in enumerate(choices):
            payload = self._callbacks.issue(
                "clarify",
                f"{clarify_id}:{index}",
                user_id=user_id,
                chat_id=transport_chat_id,
                session_key=session_key,
            )
            rows.append([{"type": "callback", "text": str(index + 1), "payload": payload}])
        other_payload = self._callbacks.issue(
            "clarify",
            f"{clarify_id}:other",
            user_id=user_id,
            chat_id=transport_chat_id,
            session_key=session_key,
        )
        rows.append([{"type": "callback", "text": "Другое", "payload": other_payload}])
        return await self._send_interactive_prompt(
            transport_chat_id,
            "❓ " + str(question) + "\n\n" + "\n".join(option_lines),
            rows,
            metadata=metadata,
        )

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
        allow_permanent: bool = True,
        allow_session: bool = True,
        smart_denied: bool = False,
    ) -> Any:
        del smart_denied
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound message",
            )
        _target_type, user_id = context
        callback_user_id = "*" if _target_type == "chat" else user_id
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        choices: list[tuple[str, str]] = [("once", "Разрешить один раз")]
        if allow_session:
            choices.append(("session", "Разрешить на сессию"))
        if allow_permanent:
            choices.append(("always", "Разрешить всегда"))
        choices.append(("deny", "Запретить"))
        rows: list[list[Mapping[str, str]]] = []
        for index in range(0, len(choices), 2):
            row: list[Mapping[str, str]] = []
            for choice, label in choices[index : index + 2]:
                payload = self._callbacks.issue(
                    "approval",
                    choice,
                    user_id=callback_user_id,
                    chat_id=transport_chat_id,
                    session_key=session_key,
                )
                row.append({"type": "callback", "text": label, "payload": payload})
            rows.append(row)
        preview = str(command)
        if len(preview) > 3200:
            preview = preview[:3200] + "..."
        text = f"⚠️ Требуется подтверждение команды:\n\n```\n{preview}\n```\nПричина: {description}"
        return await self._send_interactive_prompt(transport_chat_id, text, rows, metadata=metadata)

    async def send_slash_confirm(
        self,
        chat_id: str,
        title: str,
        message: str,
        session_key: str,
        confirm_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound message",
            )
        _target_type, user_id = context
        callback_user_id = "*" if _target_type == "chat" else user_id
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        choices = (
            ("once", "Подтвердить один раз"),
            ("always", "Подтверждать всегда"),
            ("cancel", "Отмена"),
        )
        rows: list[list[Mapping[str, str]]] = []
        for choice, label in choices:
            payload = self._callbacks.issue(
                "slash",
                f"{choice}:{confirm_id}",
                user_id=callback_user_id,
                chat_id=transport_chat_id,
                session_key=session_key,
            )
            rows.append([{"type": "callback", "text": label, "payload": payload}])
        return await self._send_interactive_prompt(
            transport_chat_id,
            f"**{title}**\n\n{message}",
            rows,
            metadata=metadata,
        )

    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound message",
            )
        _target_type, user_id = context
        callback_user_id = "*" if _target_type == "chat" else user_id
        transport_chat_id = _transport_chat_id(chat_id, metadata)
        normalized = [item for item in providers if isinstance(item, Mapping)]
        if not normalized:
            return SendResult(success=False, error="MAX model provider list is empty")

        picker_id = token_urlsafe(9)
        self._model_pickers[picker_id] = {
            "providers": normalized,
            "current_model": str(current_model or ""),
            "current_provider": str(current_provider or ""),
            "selected_provider": "",
            "selected_provider_name": "",
            "model_list": [],
            "on_model_selected": on_model_selected,
            "session_key": session_key,
            "user_id": user_id,
            "callback_user_id": callback_user_id,
            "chat_id": transport_chat_id,
        }
        state = self._model_pickers[picker_id]
        rows = self._model_provider_rows(state, picker_id, None)
        result = await self._send_interactive_prompt(
            transport_chat_id,
            self._model_picker_provider_text(state),
            rows,
            metadata=metadata,
        )
        if not getattr(result, "success", False):
            self._model_pickers.pop(picker_id, None)
        return result

    def _model_provider_rows(
        self,
        state: Mapping[str, Any],
        picker_id: str,
        callback: Optional[MaxCallback],
    ) -> list[list[Mapping[str, str]]]:
        chat_id = callback.chat_id if callback else ""
        user_id = callback.user_id if callback else str(state.get("callback_user_id") or state["user_id"])
        rows: list[list[Mapping[str, str]]] = []
        buttons: list[Mapping[str, str]] = []
        for provider in state["providers"][:20]:
            slug = str(provider.get("slug") or "").strip()
            if not slug:
                continue
            name = str(provider.get("name") or slug)
            count = provider.get("total_models", len(provider.get("models", [])))
            label = f"{name} ({count})"
            if slug == state.get("current_provider"):
                label = f"✓ {label}"
            payload = self._callbacks.issue(
                "model",
                f"{picker_id}:provider:{slug}",
                user_id=user_id,
                chat_id=chat_id or str(state.get("chat_id") or "user"),
                session_key=str(state.get("session_key") or ""),
            )
            buttons.append({"type": "callback", "text": label[:60], "payload": payload})
        for index in range(0, len(buttons), 2):
            rows.append(buttons[index : index + 2])
        rows.append([self._model_cancel_button(picker_id, user_id, chat_id, state)])
        return rows

    def _model_rows(
        self,
        state: Mapping[str, Any],
        picker_id: str,
        callback: MaxCallback,
    ) -> list[list[Mapping[str, str]]]:
        rows: list[list[Mapping[str, str]]] = []
        buttons: list[Mapping[str, str]] = []
        for index, model_id in enumerate(state.get("model_list", [])[:50]):
            label = str(model_id).rsplit("/", 1)[-1]
            if len(label) > 40:
                label = label[:37] + "..."
            payload = self._callbacks.issue(
                "model",
                f"{picker_id}:model:{index}",
                user_id=callback.user_id,
                chat_id=callback.chat_id,
                session_key=str(state.get("session_key") or ""),
            )
            buttons.append({"type": "callback", "text": label, "payload": payload})
        for index in range(0, len(buttons), 2):
            rows.append(buttons[index : index + 2])
        back_payload = self._callbacks.issue(
            "model",
            f"{picker_id}:back",
            user_id=callback.user_id,
            chat_id=callback.chat_id,
            session_key=str(state.get("session_key") or ""),
        )
        rows.append([{"type": "callback", "text": "Назад", "payload": back_payload}])
        rows.append([self._model_cancel_button(picker_id, callback.user_id, callback.chat_id, state)])
        return rows

    def _model_cancel_button(
        self,
        picker_id: str,
        user_id: str,
        chat_id: str,
        state: Mapping[str, Any],
    ) -> Mapping[str, str]:
        payload = self._callbacks.issue(
            "model",
            f"{picker_id}:cancel",
            user_id=user_id,
            chat_id=chat_id or str(state.get("chat_id") or "user"),
            session_key=str(state.get("session_key") or ""),
        )
        return {"type": "callback", "text": "Отмена", "payload": payload}

    @staticmethod
    def _model_picker_provider_text(state: Mapping[str, Any]) -> str:
        model = state.get("current_model") or "неизвестна"
        provider = state.get("current_provider") or "неизвестен"
        return f"⚙️ Настройка модели\n\nТекущая модель: `{model}`\nПровайдер: {provider}\n\nВыберите провайдера:"

    @staticmethod
    def _model_picker_model_text(state: Mapping[str, Any]) -> str:
        provider = state.get("selected_provider_name") or state.get("selected_provider")
        models = state.get("model_list", [])
        return f"⚙️ Настройка модели\n\nПровайдер: {provider}\nДоступно моделей: {len(models)}\n\nВыберите модель:"

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> Any:
        del finalize
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        transport_chat_id = _transport_chat_id(chat_id)
        try:
            await self._rate_limiter.acquire(transport_chat_id)
            response = await with_backoff(
                lambda: self._client.edit_message(message_id, content[:MAX_MESSAGE_LENGTH]),
                is_rate_limit=_is_max_rate_limit,
                extract_retry_after=_retry_after,
            )
        except MaxApiError as exc:
            return SendResult(
                success=False,
                error=str(exc),
                retryable=exc.retryable,
                retry_after=exc.retry_after,
            )
        return SendResult(success=True, message_id=message_id, raw_response=response)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        transport_chat_id = _transport_chat_id(chat_id)
        target_type = self._chat_target_types.get(transport_chat_id, "user")
        return {
            "name": transport_chat_id,
            "type": "group" if target_type == "chat" else "dm",
            "chat_id": transport_chat_id,
        }


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def check_requirements() -> bool:
    try:
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config: Any) -> bool:
    return bool(
        str(getattr(config, "token", None) or os.environ.get("MAX_BOT_TOKEN", "")).strip()
    )


def is_connected(config: Any) -> bool:
    return validate_config(config)


def env_enablement() -> Optional[Dict[str, Any]]:
    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        return None
    seed: Dict[str, Any] = {"enabled": True, "token": token}
    home = os.environ.get("MAX_HOME_CHANNEL", "").strip()
    if home:
        seed["home_channel"] = {"chat_id": home, "name": home}
    return seed


def apply_yaml_config(_yaml_cfg: dict, platform_cfg: dict) -> dict[str, Any]:
    """Map plugin-owned ``platforms.max`` keys without overriding env vars."""

    cfg = platform_cfg if isinstance(platform_cfg, Mapping) else {}
    extra = cfg.get("extra") if isinstance(cfg.get("extra"), Mapping) else cfg
    result: dict[str, Any] = {}
    env_map = {
        "api_base_url": "MAX_API_BASE_URL",
        "webhook_url": "MAX_WEBHOOK_URL",
        "webhook_secret": "MAX_WEBHOOK_SECRET",
        "ca_bundle": "MAX_CA_BUNDLE",
        "callback_ttl_seconds": "MAX_CALLBACK_TTL_SECONDS",
        "media_max_bytes": "MAX_MEDIA_MAX_BYTES",
        "require_mention": "MAX_REQUIRE_MENTION",
        "allow_from": "MAX_ALLOWED_USERS",
        "group_allow_from": "MAX_GROUP_ALLOWED_USERS",
        "group_allowed_chats": "MAX_GROUP_ALLOWED_CHATS",
        "allow_admin_from": "MAX_ADMIN_USERS",
    }
    for key, value in extra.items():
        if key in {"enabled", "token"}:
            continue
        env_name = env_map.get(key)
        if env_name and not os.environ.get(env_name):
            os.environ[env_name] = str(value).lower() if isinstance(value, bool) else str(value)
        if key in {
            "api_base_url",
            "webhook_url",
            "webhook_secret",
            "ca_bundle",
            "callback_ttl_seconds",
            "media_max_bytes",
            "require_mention",
        }:
            result[key] = os.environ.get(env_name, value) if env_name else value
        elif key in {"allow_from", "group_allow_from", "group_allowed_chats", "allow_admin_from"}:
            result[key] = value
    return result


async def standalone_send(
    pconfig: Any,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    del thread_id
    token = str(getattr(pconfig, "token", None) or os.environ.get("MAX_BOT_TOKEN", "")).strip()
    if not token:
        return {"error": "MAX_BOT_TOKEN is not configured"}
    extra = _config_extra(pconfig)
    try:
        verify = tls_verify_from_env(
            {"MAX_CA_BUNDLE": str(extra.get("ca_bundle", os.environ.get("MAX_CA_BUNDLE", "")))}
        )
        client = MaxClient(
            token,
            base_url=str(extra.get("api_base_url") or os.environ.get("MAX_API_BASE_URL", DEFAULT_API_BASE)),
            verify=verify,
        )
        try:
            configured_groups = extra.get("group_allowed_chats", [])
            if isinstance(configured_groups, str):
                configured_groups = [item.strip() for item in configured_groups.split(",") if item.strip()]
            target_type = "chat" if str(chat_id) in set(configured_groups or []) else "user"
            target_path = str(
                extra.get("target_path")
                or os.environ.get("MAX_TARGET_PATH", Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "max" / "targets.sqlite3")
            )
            target_store = MaxTargetStore(target_path)
            try:
                target_type = target_store.get(str(chat_id)) or target_type
            finally:
                target_store.close()
            try:
                media_max_bytes = int(
                    extra.get(
                        "media_max_bytes",
                        os.environ.get("MAX_MEDIA_MAX_BYTES", str(DEFAULT_MEDIA_MAX_BYTES)),
                    )
                )
            except (TypeError, ValueError):
                media_max_bytes = DEFAULT_MEDIA_MAX_BYTES
            media_max_bytes = min(max(1, media_max_bytes), DEFAULT_MEDIA_MAX_BYTES)
            attachments: list[Mapping[str, Any]] = []
            raw_media_files = list(media_files or [])
            upload_errors: list[str] = []
            for media_path in raw_media_files:
                if isinstance(media_path, (tuple, list)):
                    path = str(media_path[0])
                    is_voice = bool(media_path[1]) if len(media_path) > 1 else False
                else:
                    path = str(media_path)
                    is_voice = False
                try:
                    safe_path = BasePlatformAdapter.validate_media_delivery_path(path)
                except (OSError, RuntimeError, ValueError):
                    safe_path = None
                if not safe_path:
                    upload_errors.append("MAX standalone attachment path is not allowed")
                    continue
                media_type = media_type_for_file(path, force_document=force_document)
                if is_voice and not force_document:
                    media_type = media_type_for_file(path, is_voice=True)
                try:
                    upload = await with_backoff(
                        lambda: client.upload_media(
                            safe_path,
                            media_type=media_type,
                            max_bytes=media_max_bytes,
                            mime_type=mime_type_for_file(safe_path, media_type),
                        ),
                        is_rate_limit=_is_retryable_media_error,
                        extract_retry_after=_media_retry_after,
                        max_attempts=5,
                    )
                    token_value = str(upload.get("token") or "").strip()
                    if not token_value:
                        raise MaxApiError("MAX media upload returned no attachment token")
                    attachments.append({"type": media_type, "payload": {"token": token_value}})
                except (MaxApiError, OSError, TypeError, ValueError) as exc:
                    upload_errors.append(str(exc))
            if not attachments:
                return {"error": "; ".join(upload_errors) or "MAX media uploads failed"}

            last_id = None
            send_errors: list[str] = []
            chunks = split_message(message, MAX_MESSAGE_LENGTH) if message else [""]
            for batch_index, batch in enumerate(_attachment_batches(attachments)):
                chunk = chunks[0] if batch_index == 0 else ""
                try:
                    response = await with_backoff(
                        lambda: client.send_message(
                            str(chat_id),
                            chunk,
                            target_type=target_type,
                            attachments=batch,
                        ),
                        is_rate_limit=_is_media_send_retryable,
                        extract_retry_after=_media_retry_after,
                        max_attempts=5,
                    )
                    last_id = _response_message_id(response) or last_id
                except MaxApiError as exc:
                    send_errors.append(str(exc))
            if last_id:
                for chunk in chunks[1:]:
                    try:
                        response = await with_backoff(
                            lambda: client.send_message(
                                str(chat_id), chunk, target_type=target_type
                            ),
                            is_rate_limit=_is_media_send_retryable,
                            extract_retry_after=_media_retry_after,
                            max_attempts=5,
                        )
                        last_id = _response_message_id(response) or last_id
                    except MaxApiError as exc:
                        send_errors.append(str(exc))
            errors = upload_errors + send_errors
            if not last_id:
                return {"error": "; ".join(errors) or "MAX media messages failed"}
            result = {
                "success": not errors,
                "platform": PLATFORM_NAME,
                "chat_id": str(chat_id),
                "message_id": last_id,
            }
            if errors:
                result["error"] = "Часть вложений не доставлена: " + "; ".join(errors)
            return result
        finally:
            await client.close()
    except (MaxApiError, ValueError, OSError) as exc:
        return {"error": f"MAX standalone send failed: {exc}"}


def register(ctx: Any) -> None:
    ctx.register_platform(
        name=PLATFORM_NAME,
        label=PLATFORM_LABEL,
        adapter_factory=MaxAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        env_enablement_fn=env_enablement,
        apply_yaml_config_fn=apply_yaml_config,
        cron_deliver_env_var="MAX_HOME_CHANNEL",
        standalone_sender_fn=standalone_send,
        allowed_users_env="MAX_ALLOWED_USERS",
        allow_all_env="MAX_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji=PLATFORM_EMOJI,
        platform_hint=PLATFORM_HINT,
        install_hint="httpx is required; configure MAX_BOT_TOKEN and MAX_CA_BUNDLE when host CA lacks the MAX trust chain",
    )
