"""Typed models for imsg chats and messages. FROZEN contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chat:
    """A conversation as reported by `chats.list`."""

    chat_id: int
    guid: str
    identifier: str = ""
    display_name: str = ""
    service: str = "iMessage"
    is_group: bool = False
    participants: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Chat":
        participants = raw.get("participants") or []
        if participants and isinstance(participants[0], dict):
            participants = [p.get("id") or p.get("address") or "" for p in participants]
        name = raw.get("display_name") or raw.get("name") or ""
        return cls(
            chat_id=int(raw.get("id") or raw.get("chat_id") or 0),
            guid=str(raw.get("guid") or ""),
            identifier=str(raw.get("identifier") or ""),
            display_name=str(name),
            service=str(raw.get("service") or "iMessage"),
            is_group=bool(raw.get("is_group", len(participants) > 1)),
            participants=[str(p) for p in participants],
        )

    @property
    def address(self) -> str:
        """Best-effort phone/email for a direct chat."""
        if self.identifier:
            return self.identifier
        return self.participants[0] if self.participants else ""


@dataclass
class Message:
    """A message from `messages.history` or a watch notification."""

    rowid: int
    guid: str
    chat_id: int
    text: str = ""
    sender: str = ""
    is_from_me: bool = False
    created_at: str = ""
    is_reaction: bool = False
    has_attachments: bool = False

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Message":
        attachments = raw.get("attachments") or []
        return cls(
            rowid=int(raw.get("id") or raw.get("rowid") or 0),
            guid=str(raw.get("guid") or ""),
            chat_id=int(raw.get("chat_id") or 0),
            text=str(raw.get("text") or ""),
            sender=str(raw.get("sender") or ""),
            is_from_me=bool(raw.get("is_from_me", False)),
            created_at=str(raw.get("created_at") or ""),
            is_reaction=bool(raw.get("is_reaction", False)),
            has_attachments=bool(attachments),
        )

    @property
    def is_substantive(self) -> bool:
        """True if this is a real incoming text worth considering for a reply."""
        return bool(self.text.strip()) and not self.is_reaction
