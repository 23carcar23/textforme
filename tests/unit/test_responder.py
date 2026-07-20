"""Unit tests for src/textforme/service/responder.py.

Uses a fake AnthropicClient duck-type (no network, no real SDK) to verify
AnthropicResponder wires build_request -> complete -> validate_reply
correctly.
"""

from __future__ import annotations

import pytest

from textforme.database import ContactRecord
from textforme.messaging.events import AnthropicUnavailableError, ReplyValidationError
from textforme.messaging.models import Message
from textforme.service.responder import AnthropicResponder


class FakeAnthropicClient:
    def __init__(self, reply_text: str = "", error: Exception | None = None):
        self.reply_text = reply_text
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, model_id, system, messages, max_tokens):
        self.calls.append(
            {
                "model_id": model_id,
                "system": system,
                "messages": messages,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return self.reply_text


def make_contact() -> ContactRecord:
    return ContactRecord(
        chat_guid="guid-1",
        chat_id=1,
        display_name="Alice",
        address="+15551234567",
        service="iMessage",
        is_group=False,
        ai_enabled=True,
    )


def make_message(guid: str, text: str, is_from_me: bool = False, rowid: int = 1) -> Message:
    return Message(
        rowid=rowid,
        guid=guid,
        chat_id=1,
        text=text,
        sender="+15551234567",
        is_from_me=is_from_me,
    )


async def test_generate_reply_returns_validated_text():
    client = FakeAnthropicClient(reply_text="  sounds good!  ")
    responder = AnthropicResponder(client)
    incoming = make_message("g2", "want to grab lunch?")
    history = [make_message("g1", "hey", rowid=1)]

    result = await responder.generate_reply(
        contact=make_contact(),
        recent_messages=history,
        incoming_message=incoming,
        model_id="claude-x",
        max_reply_chars=300,
    )

    assert result == "sounds good!"


async def test_generate_reply_calls_complete_with_built_prompt():
    client = FakeAnthropicClient(reply_text="ok")
    responder = AnthropicResponder(client)
    incoming = make_message("g2", "want to grab lunch?")
    # A prior assistant turn keeps the incoming message from merging into the
    # preceding history turn, so we can assert it lands as its own final turn.
    history = [make_message("g1", "hey there", rowid=1, is_from_me=True)]

    await responder.generate_reply(
        contact=make_contact(),
        recent_messages=history,
        incoming_message=incoming,
        model_id="claude-x",
        max_reply_chars=300,
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model_id"] == "claude-x"
    assert "Alice" in call["system"]
    assert "300" in call["system"]
    assert call["messages"][-1] == {"role": "user", "content": "want to grab lunch?"}


async def test_generate_reply_max_tokens_derived_from_max_reply_chars():
    client = FakeAnthropicClient(reply_text="ok")
    responder = AnthropicResponder(client)
    incoming = make_message("g1", "hi")

    await responder.generate_reply(
        contact=make_contact(),
        recent_messages=[],
        incoming_message=incoming,
        model_id="claude-x",
        max_reply_chars=300,
    )

    assert client.calls[0]["max_tokens"] == 100  # 300 // 3


async def test_generate_reply_max_tokens_has_floor_of_64():
    client = FakeAnthropicClient(reply_text="ok")
    responder = AnthropicResponder(client)
    incoming = make_message("g1", "hi")

    await responder.generate_reply(
        contact=make_contact(),
        recent_messages=[],
        incoming_message=incoming,
        model_id="claude-x",
        max_reply_chars=30,  # 30 // 3 == 10, below floor
    )

    assert client.calls[0]["max_tokens"] == 64


async def test_generate_reply_truncates_overlong_completion():
    long_text = "word " * 100
    client = FakeAnthropicClient(reply_text=long_text)
    responder = AnthropicResponder(client)
    incoming = make_message("g1", "hi")

    result = await responder.generate_reply(
        contact=make_contact(),
        recent_messages=[],
        incoming_message=incoming,
        model_id="claude-x",
        max_reply_chars=20,
    )

    assert len(result) <= 20


async def test_generate_reply_raises_validation_error_on_empty_completion():
    client = FakeAnthropicClient(reply_text="   \x00  ")
    responder = AnthropicResponder(client)
    incoming = make_message("g1", "hi")

    with pytest.raises(ReplyValidationError):
        await responder.generate_reply(
            contact=make_contact(),
            recent_messages=[],
            incoming_message=incoming,
            model_id="claude-x",
            max_reply_chars=300,
        )


async def test_generate_reply_propagates_anthropic_unavailable_error():
    client = FakeAnthropicClient(error=AnthropicUnavailableError("boom"))
    responder = AnthropicResponder(client)
    incoming = make_message("g1", "hi")

    with pytest.raises(AnthropicUnavailableError):
        await responder.generate_reply(
            contact=make_contact(),
            recent_messages=[],
            incoming_message=incoming,
            model_id="claude-x",
            max_reply_chars=300,
        )


async def test_generate_reply_uses_display_name_fallback_to_address():
    client = FakeAnthropicClient(reply_text="ok")
    responder = AnthropicResponder(client)
    incoming = make_message("g1", "hi")
    contact = ContactRecord(
        chat_guid="guid-2",
        chat_id=2,
        display_name="",
        address="+15559998888",
        service="iMessage",
        is_group=False,
        ai_enabled=True,
    )

    await responder.generate_reply(
        contact=contact,
        recent_messages=[],
        incoming_message=incoming,
        model_id="claude-x",
        max_reply_chars=300,
    )

    assert "+15559998888" in client.calls[0]["system"]
