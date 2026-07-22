"""Factory helpers for tests."""

from __future__ import annotations

from textforme.config import Settings
from textforme.database import ContactRecord
from textforme.messaging.models import Message


def make_contact(
    chat_guid: str = "test-guid-001",
    chat_id: int = 1,
    display_name: str = "Test Contact",
    address: str = "+1234567890",
    service: str = "iMessage",
    is_group: bool = False,
    ai_enabled: bool = True,
    last_seen_message_guid: str | None = None,
    description: str = "",
    reply_timer_enabled: bool = False,
) -> ContactRecord:
    """Create a ContactRecord with sensible defaults."""
    return ContactRecord(
        chat_guid=chat_guid,
        chat_id=chat_id,
        display_name=display_name,
        address=address,
        service=service,
        is_group=is_group,
        ai_enabled=ai_enabled,
        last_seen_message_guid=last_seen_message_guid,
        description=description,
        reply_timer_enabled=reply_timer_enabled,
    )


def make_message(
    rowid: int = 1,
    guid: str = "msg-guid-001",
    chat_id: int = 1,
    text: str = "Test message",
    sender: str = "+1234567890",
    is_from_me: bool = False,
    created_at: str = "2026-07-20T10:00:00+00:00",
    is_reaction: bool = False,
    has_attachments: bool = False,
    **kwargs: object,
) -> Message:
    """Create a Message with sensible defaults."""
    # Allow overrides via kwargs
    if kwargs:
        for key, value in kwargs.items():
            if hasattr(Message, key):
                locals()[key] = value

    return Message(
        rowid=rowid,
        guid=guid,
        chat_id=chat_id,
        text=text,
        sender=sender,
        is_from_me=is_from_me,
        created_at=created_at,
        is_reaction=is_reaction,
        has_attachments=has_attachments,
    )


def make_settings(
    selected_model_id: str = "claude-3-5-sonnet-20241022",
    global_ai_enabled: bool = True,
    paused: bool = False,
    context_message_limit: int = 10,
    failure_pause_threshold: int = 5,
    last_seen_rowid: int = 0,
    onboarding_complete: bool = False,
    **kwargs: object,
) -> Settings:
    """Create a Settings object with sensible defaults."""
    # Allow overrides via kwargs
    if kwargs:
        for key, value in kwargs.items():
            if hasattr(Settings, key):
                locals()[key] = value

    return Settings(
        selected_model_id=selected_model_id,
        global_ai_enabled=global_ai_enabled,
        paused=paused,
        context_message_limit=context_message_limit,
        failure_pause_threshold=failure_pause_threshold,
        last_seen_rowid=last_seen_rowid,
        onboarding_complete=onboarding_complete,
    )
