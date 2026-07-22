"""Unit tests for src/textforme/service/briefer.py."""

from __future__ import annotations

import pytest

from textforme.service import briefer
from textforme.service.briefer import BRIEF_MODEL_ID, generate_brief

from tests.fixtures.factories import make_contact, make_message


class FakeClient:
    """Records the complete() call and returns a canned summary."""

    def __init__(self, reply: str = "  a tidy summary  ") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def complete(self, model_id, system, messages, max_tokens):
        self.calls.append(
            {"model_id": model_id, "system": system, "messages": messages, "max_tokens": max_tokens}
        )
        return self.reply


async def test_generate_brief_uses_sonnet_and_strips() -> None:
    client = FakeClient()
    contact = make_contact(display_name="Mom", chat_guid="c1")
    history = [
        make_message(text="you coming sunday?", is_from_me=False),
        make_message(text="yep around 5", is_from_me=True),
    ]

    summary = await generate_brief(client, [(contact, history)])

    assert summary == "a tidy summary"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model_id"] == BRIEF_MODEL_ID == "claude-sonnet-5"
    # Both turns reach the model, labelled by direction.
    content = call["messages"][0]["content"]
    assert "Mom: you coming sunday?" in content
    assert "You (AI): yep around 5" in content


async def test_generate_brief_skips_reactions_and_empty() -> None:
    client = FakeClient()
    contact = make_contact(display_name="Alex", chat_guid="c2")
    history = [
        make_message(text="Liked a message", is_from_me=False, is_reaction=True),
        make_message(text="   ", is_from_me=False),
        make_message(text="real message", is_from_me=False),
    ]

    await generate_brief(client, [(contact, history)])

    content = client.calls[0]["messages"][0]["content"]
    assert "real message" in content
    assert "Liked a message" not in content


async def test_generate_brief_empty_when_no_substantive_messages() -> None:
    client = FakeClient()
    contact = make_contact(display_name="Sam", chat_guid="c3")
    history = [make_message(text="", is_from_me=False)]

    summary = await generate_brief(client, [(contact, history)])

    assert summary == ""
    # No point calling the model with nothing to summarize.
    assert client.calls == []
