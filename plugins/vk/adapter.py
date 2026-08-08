"""VKontakte (VK) platform adapter for Hermes Agent.

Implements :class:`BasePlatformAdapter` against the ``vkbottle`` async framework
(Bots LongPoll + Callback API). VK ``peer_id`` semantics: DM peer_id == user_id;
multi-user chats use peer_id >= 2_000_000_001 (= 2_000_000_000 + chat_id).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
from typing import Any, Dict, List, Optional

import httpx

try:
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import (
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
        cache_audio_from_bytes,
        cache_document_from_bytes,
        cache_image_from_bytes,
    )
    from gateway.session import SessionSource
    from gateway.status import acquire_scoped_lock, release_scoped_lock
except ImportError:  # pragma: no cover — exercised outside Hermes runtime
    BasePlatformAdapter = object  # type: ignore[misc,assignment]
    Platform = None  # type: ignore[assignment]
    PlatformConfig = None  # type: ignore[assignment]
    MessageEvent = None  # type: ignore[assignment]
    MessageType = None  # type: ignore[assignment]
    SendResult = None  # type: ignore[assignment]
    SessionSource = None  # type: ignore[assignment]
    cache_audio_from_bytes = cache_document_from_bytes = cache_image_from_bytes = None  # type: ignore[assignment]

    def acquire_scoped_lock(_platform: str, _token: str) -> bool:  # type: ignore[misc]
        return True

    def release_scoped_lock(_platform: str, _token: str) -> None:  # type: ignore[misc]
        return None

from plugins._ru_common import AccessPolicy, extract_media_tags, split_message

from .rate_limit import VK_MESSAGE_LENGTH, with_backoff

logger = logging.getLogger(__name__)

PLATFORM_NAME = "vk"
PLATFORM_LABEL = "VKontakte"
PLATFORM_EMOJI = "🟦"
PLATFORM_HINT = (
    "You are chatting via VK (VKontakte), a Russian social network whose messenger is "
    "whitelisted under regional connectivity restrictions. Users reach you without a VPN. "
    "Messages support text, voice notes (auto-transcribed), photos, and documents."
)

VK_CHAT_PEER_OFFSET = 2_000_000_000


def _is_chat_peer(peer_id: int) -> bool:
    return peer_id > VK_CHAT_PEER_OFFSET


class VkAdapter(BasePlatformAdapter):  # type: ignore[misc]
    """Hermes platform adapter wrapping ``vkbottle.Bot``."""

    SUPPORTS_MESSAGE_EDITING = True

    def __init__(self, config: "PlatformConfig") -> None:  # type: ignore[name-defined]
        platform = Platform(PLATFORM_NAME) if Platform is not None else PLATFORM_NAME
        super().__init__(config, platform)  # type: ignore[arg-type]

        self._config = config
        self._token: str = config.token or os.environ.get("VK_GROUP_TOKEN", "")
        if not self._token:
            raise RuntimeError("VkAdapter requires VK_GROUP_TOKEN")
        self._group_id: int = int(os.environ.get("VK_GROUP_ID", "0") or 0)

        extra: Dict[str, Any] = dict(getattr(config, "extra", {}) or {})
        self._extra = extra
        self._access = AccessPolicy.from_env_and_extra(
            allowed_users_env=os.environ.get("VK_ALLOWED_USERS"),
            group_allowed_users_env=os.environ.get("VK_GROUP_ALLOWED_USERS"),
            group_allowed_chats_env=os.environ.get("VK_GROUP_ALLOWED_CHATS"),
            allow_all_env=os.environ.get("VK_ALLOW_ALL_USERS"),
            guest_mode_env=os.environ.get("VK_GUEST_MODE"),
            extra=extra,
        )
        self._require_mention: bool = bool(extra.get("require_mention", True))
        self._api_version = os.environ.get("VK_API_VERSION")

        self._bot: Any = None
        self._polling_task: Optional[asyncio.Task] = None
        self._group_screen_name: Optional[str] = None

    # ------------------------------------------------------------------ lifecycle

    async def connect(self) -> bool:
        if not acquire_scoped_lock(PLATFORM_NAME, self._token):
            logger.error("VK_GROUP_TOKEN already in use by another Hermes profile")
            return False
        try:
            from vkbottle.bot import Bot
        except ImportError as exc:
            release_scoped_lock(PLATFORM_NAME, self._token)
            logger.error("vkbottle not installed: %s. Run: pip install vkbottle", exc)
            return False

        self._bot = Bot(token=self._token)
        self._register_handlers()

        try:
            api = self._bot.api
            info = await api.groups.get_by_id()
            group = (info.groups if hasattr(info, "groups") else info)[0]
            self._group_screen_name = getattr(group, "screen_name", None)
            if not self._group_id:
                self._group_id = int(getattr(group, "id", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("VK groups.getById failed (continuing): %s", exc)

        self._polling_task = asyncio.create_task(self._bot.run_polling(), name="vk-polling")
        self._mark_connected()
        logger.info("VK adapter connected (group=%s)", self._group_screen_name or self._group_id)
        return True

    async def disconnect(self) -> None:
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._bot is not None:
            try:
                await self._bot.api.http_client.close()
            except Exception:  # noqa: BLE001
                pass
        release_scoped_lock(PLATFORM_NAME, self._token)
        self._mark_disconnected()
        logger.info("VK adapter disconnected")

    # ------------------------------------------------------------------ handlers

    def _register_handlers(self) -> None:
        @self._bot.on.message()
        async def _on_message(message: Any) -> None:
            try:
                await self._handle_incoming(message)
            except Exception:  # noqa: BLE001
                logger.exception("VK incoming handler crashed")

    async def _handle_incoming(self, message: Any) -> None:
        peer_id = int(getattr(message, "peer_id", 0) or 0)
        from_id = int(getattr(message, "from_id", 0) or 0)
        text: str = getattr(message, "text", "") or ""
        message_id = str(getattr(message, "conversation_message_id", None) or getattr(message, "id", "") or "")
        is_group = _is_chat_peer(peer_id)
        user_id = str(from_id)
        chat_id = str(peer_id)
        chat_name: Optional[str] = None
        user_name: Optional[str] = None

        try:
            api = self._bot.api
            users = await api.users.get(user_ids=[from_id]) if from_id > 0 else []
            if users:
                u = users[0]
                user_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() or None
        except Exception:  # noqa: BLE001
            pass

        mentioned = self._mention_check(text)
        if is_group:
            if not self._access.can_group(user_id, chat_id, mentioned=mentioned):
                return
            if self._require_mention and not mentioned and not _is_command(text):
                return
        else:
            if not self._access.can_dm(user_id):
                return

        if _is_command(text) and not self._access.can_run_command(user_id, text, is_group=is_group):
            return

        attachments = list(getattr(message, "attachments", None) or [])
        media_urls, media_types = await self._download_attachments(attachments)

        source = SessionSource(  # type: ignore[call-arg]
            platform=self.platform,
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type="group" if is_group else "dm",
            user_id=user_id,
            user_name=user_name,
            message_id=message_id,
        )
        event = MessageEvent(  # type: ignore[call-arg]
            text=text,
            message_type=_classify(text, media_types),
            source=source,
            raw_message=message,
            message_id=message_id,
            media_urls=media_urls,
            media_types=media_types,
        )
        await self.handle_message(event)

    def _mention_check(self, text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        if self._group_screen_name and f"@{self._group_screen_name.lower()}" in lowered:
            return True
        # VK rich mentions look like [club12345|@name]
        if self._group_id and f"[club{self._group_id}|" in lowered:
            return True
        return False

    async def _download_attachments(self, attachments: List[Any]) -> tuple[List[str], List[str]]:
        urls: List[str] = []
        types: List[str] = []
        for att in attachments:
            kind = (getattr(att, "type", "") or "").lower()
            inner = getattr(att, kind, None) if kind else None
            if kind == "photo" and inner is not None:
                # Pick the largest size.
                sizes = list(getattr(inner, "sizes", None) or [])
                if not sizes:
                    continue
                sizes.sort(key=lambda s: getattr(s, "width", 0) * getattr(s, "height", 0))
                url = getattr(sizes[-1], "url", None)
                if not url:
                    continue
                data = await self._fetch_bytes(url)
                if cache_image_from_bytes:
                    urls.append(cache_image_from_bytes(data, ext=".jpg"))
                    types.append("photo")
            elif kind == "audio_message" and inner is not None:
                url = getattr(inner, "link_ogg", None) or getattr(inner, "link_mp3", None)
                if not url:
                    continue
                data = await self._fetch_bytes(url)
                if cache_audio_from_bytes:
                    ext = ".ogg" if "ogg" in url else ".mp3"
                    urls.append(cache_audio_from_bytes(data, ext=ext))
                    types.append("voice")
            elif kind == "doc" and inner is not None:
                url = getattr(inner, "url", None)
                title = getattr(inner, "title", None) or "file.bin"
                if not url:
                    continue
                data = await self._fetch_bytes(url)
                if cache_document_from_bytes:
                    urls.append(cache_document_from_bytes(data, filename=title))
                    types.append("document")
        return urls, types

    async def _fetch_bytes(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

    # ------------------------------------------------------------------ outbound

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "SendResult":  # type: ignore[name-defined]
        media_paths, text = extract_media_tags(content)
        attachment_strs: List[str] = []
        for path in media_paths:
            try:
                att = await self._upload(chat_id, path)
                if att:
                    attachment_strs.append(att)
            except Exception:  # noqa: BLE001
                logger.exception("VK upload failed for %s", path)

        last_id: Optional[str] = None
        continuation: List[str] = []

        if text:
            chunks = split_message(text, VK_MESSAGE_LENGTH)
            for i, chunk in enumerate(chunks):
                kwargs: Dict[str, Any] = {
                    "peer_id": int(chat_id),
                    "message": chunk,
                    "random_id": _random_id(),
                }
                if i == 0 and reply_to:
                    kwargs["reply_to"] = int(reply_to) if reply_to.isdigit() else None
                if i == 0 and attachment_strs:
                    kwargs["attachment"] = ",".join(attachment_strs)

                async def _do(kwargs=kwargs):
                    return await self._bot.api.messages.send(**{k: v for k, v in kwargs.items() if v is not None})

                try:
                    mid = await with_backoff(_do)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("VK messages.send failed")
                    return SendResult(success=False, error=str(exc))  # type: ignore[call-arg]
                if last_id is not None:
                    continuation.append(str(mid))
                last_id = str(mid)
        elif attachment_strs:
            kwargs = {
                "peer_id": int(chat_id),
                "message": "",
                "random_id": _random_id(),
                "attachment": ",".join(attachment_strs),
            }
            mid = await with_backoff(lambda: self._bot.api.messages.send(**kwargs))
            last_id = str(mid)

        return SendResult(  # type: ignore[call-arg]
            success=True, message_id=last_id, continuation_message_ids=tuple(continuation)
        )

    async def _upload(self, chat_id: str, path: str) -> Optional[str]:
        from vkbottle.tools import (  # type: ignore[import-not-found]
            DocMessagesUploader,
            PhotoMessageUploader,
            VoiceMessageUploader,
        )

        ext = os.path.splitext(path)[1].lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return await PhotoMessageUploader(self._bot.api).upload(path, peer_id=int(chat_id))
        if ext in {".ogg", ".m4a", ".mp3", ".wav"}:
            return await VoiceMessageUploader(self._bot.api).upload(path, peer_id=int(chat_id))
        return await DocMessagesUploader(self._bot.api).upload(
            path, peer_id=int(chat_id), title=os.path.basename(path)
        )

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> "SendResult":  # type: ignore[name-defined]
        _media, text = extract_media_tags(content)
        try:
            await self._bot.api.messages.edit(
                peer_id=int(chat_id),
                conversation_message_id=int(message_id),
                message=text[:VK_MESSAGE_LENGTH],
            )
        except Exception as exc:  # noqa: BLE001
            return SendResult(success=False, error=str(exc))  # type: ignore[call-arg]
        return SendResult(success=True, message_id=message_id)  # type: ignore[call-arg]

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        peer_id = int(chat_id)
        if not _is_chat_peer(peer_id):
            return {"name": chat_id, "type": "dm"}
        try:
            res = await self._bot.api.messages.get_conversations_by_id(peer_ids=[peer_id])
            items = getattr(res, "items", None) or []
            if items:
                title = getattr(getattr(items[0], "chat_settings", None), "title", None)
                return {"name": title or chat_id, "type": "group"}
        except Exception:  # noqa: BLE001
            pass
        return {"name": chat_id, "type": "group"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_command(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("/")


def _random_id() -> int:
    # VK messages.send requires a random_id to dedupe; 2**31 keeps it within int range.
    return secrets.randbelow(2_147_483_647)


def _classify(text: str, media_types: List[str]) -> "MessageType":  # type: ignore[name-defined]
    if MessageType is None:
        return None  # type: ignore[return-value]
    if "voice" in media_types:
        return MessageType.VOICE
    if "photo" in media_types:
        return MessageType.PHOTO
    if "document" in media_types:
        return MessageType.DOCUMENT
    if _is_command(text):
        return MessageType.COMMAND
    return MessageType.TEXT


# ---------------------------------------------------------------------------
# Standalone sender
# ---------------------------------------------------------------------------


async def standalone_send(chat_id: str, content: str, *, token: Optional[str] = None) -> bool:
    token = token or os.environ.get("VK_GROUP_TOKEN")
    if not token:
        logger.error("VK standalone_send: no token")
        return False
    try:
        from vkbottle import API
    except ImportError:
        logger.error("VK standalone_send: vkbottle not installed")
        return False
    api = API(token=token)
    try:
        for chunk in split_message(content, VK_MESSAGE_LENGTH):
            await api.messages.send(peer_id=int(chat_id), message=chunk, random_id=_random_id())
        return True
    except Exception:  # noqa: BLE001
        logger.exception("VK standalone_send failed")
        return False
    finally:
        try:
            await api.http_client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def check_requirements() -> bool:
    try:
        import vkbottle  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config: "PlatformConfig") -> bool:  # type: ignore[name-defined]
    return bool(getattr(config, "token", None) or os.environ.get("VK_GROUP_TOKEN"))


def env_enablement(env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    token = env.get("VK_GROUP_TOKEN")
    if not token:
        return None
    out: Dict[str, Any] = {"enabled": True, "token": token}
    home = env.get("VK_HOME_CHANNEL")
    if home:
        out["home_channel"] = home
    return out


def register(ctx: Any) -> None:
    ctx.register_platform(
        name=PLATFORM_NAME,
        label=PLATFORM_LABEL,
        adapter_factory=lambda cfg: VkAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        env_enablement_fn=env_enablement,
        cron_deliver_env_var="VK_HOME_CHANNEL",
        allowed_users_env="VK_ALLOWED_USERS",
        allow_all_env="VK_ALLOW_ALL_USERS",
        max_message_length=VK_MESSAGE_LENGTH,
        platform_hint=PLATFORM_HINT,
        emoji=PLATFORM_EMOJI,
        standalone_sender_fn=standalone_send,
    )
