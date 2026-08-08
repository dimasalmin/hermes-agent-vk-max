"""Small, dependency-free MAX update models used by the Hermes adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class MaxMessage:
    """Normalized data extracted from a MAX ``message_created`` update."""

    message_id: str
    user_id: str
    user_name: Optional[str]
    chat_id: str
    chat_type: str
    chat_title: Optional[str]
    text: str
    attachments: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    link: Optional[Mapping[str, Any]] = None
    is_bot: bool = False
    timestamp: Optional[int] = None
    raw_message: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return self.chat_type in {"chat", "group", "channel"}

    @classmethod
    def from_update(cls, update: Mapping[str, Any]) -> Optional["MaxMessage"]:
        if update.get("update_type") != "message_created":
            return None

        message = _mapping(update.get("message"))
        body = _mapping(message.get("body"))
        sender = _mapping(message.get("sender"))
        recipient = _mapping(message.get("recipient"))

        message_id = str(body.get("mid") or "").strip()
        if not message_id:
            return None

        user_id = str(sender.get("user_id") or recipient.get("user_id") or "").strip()
        chat_id = str(recipient.get("chat_id") or user_id).strip()
        chat_type = str(recipient.get("chat_type") or "dialog").strip().lower()
        attachments = tuple(
            item for item in (body.get("attachments") or []) if isinstance(item, Mapping)
        )

        return cls(
            message_id=message_id,
            user_id=user_id,
            user_name=sender.get("name") or sender.get("username"),
            chat_id=chat_id,
            chat_type=chat_type,
            chat_title=recipient.get("chat_title") or recipient.get("title"),
            text=str(body.get("text") or ""),
            attachments=attachments,
            link=message.get("link") if isinstance(message.get("link"), Mapping) else None,
            is_bot=bool(sender.get("is_bot", False)),
            timestamp=message.get("timestamp") or update.get("timestamp"),
            raw_message=message,
        )
