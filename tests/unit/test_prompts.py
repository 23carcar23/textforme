"""Unit tests for src/textforme/anthropic/prompts.py."""

from __future__ import annotations

from textforme.anthropic.prompts import SYSTEM_PROMPT, build_request
from textforme.messaging.models import Message


def make_message(
    guid: str,
    text: str,
    is_from_me: bool = False,
    rowid: int = 1,
    is_reaction: bool = False,
) -> Message:
    return Message(
        rowid=rowid,
        guid=guid,
        chat_id=1,
        text=text,
        sender="+15551234567",
        is_from_me=is_from_me,
        is_reaction=is_reaction,
    )


# -- SYSTEM_PROMPT content -----------------------------------------------------


def test_system_prompt_is_nonempty_template():
    assert SYSTEM_PROMPT
    assert "{contact_name}" in SYSTEM_PROMPT
    assert "{max_chars}" in SYSTEM_PROMPT


def test_system_prompt_contains_untrusted_warning():
    lowered = SYSTEM_PROMPT.lower()
    assert "untrusted" in lowered
    assert "instruction" in lowered


def test_system_prompt_states_output_only_reply_text():
    lowered = SYSTEM_PROMPT.lower()
    assert "only" in lowered


def test_system_prompt_never_claims_real_world_actions():
    lowered = SYSTEM_PROMPT.lower()
    assert "real-world action" in lowered or "real world action" in lowered


def test_system_prompt_formats_with_placeholders():
    filled = SYSTEM_PROMPT.format(contact_name="Bob", max_chars=200)
    assert "Bob" in filled
    assert "200" in filled
    assert "{contact_name}" not in filled
    assert "{max_chars}" not in filled


# -- build_request: role mapping -----------------------------------------------


def test_build_request_maps_roles():
    incoming = make_message("g3", "how are you?", is_from_me=False)
    history = [
        make_message("g1", "hey", is_from_me=False, rowid=1),
        make_message("g2", "not much, you?", is_from_me=True, rowid=2),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hey"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "not much, you?"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "how are you?"


def test_build_request_returns_filled_system_prompt():
    incoming = make_message("g1", "hi", is_from_me=False)
    system, _ = build_request("Alice", [], incoming, max_reply_chars=150)
    assert "Alice" in system
    assert "150" in system


# -- build_request: merging consecutive same-role turns ------------------------


def test_build_request_merges_consecutive_user_turns():
    incoming = make_message("g3", "you there?", is_from_me=False)
    history = [
        make_message("g1", "hey", is_from_me=False, rowid=1),
        make_message("g2", "also, happy birthday", is_from_me=False, rowid=2),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hey\nalso, happy birthday\nyou there?"


def test_build_request_merges_consecutive_assistant_turns():
    incoming = make_message("g4", "cool", is_from_me=False)
    history = [
        make_message("g1", "hi", is_from_me=False, rowid=1),
        make_message("g2", "hey!", is_from_me=True, rowid=2),
        make_message("g3", "how's it going", is_from_me=True, rowid=3),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "hey!\nhow's it going"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "cool"


# -- build_request: incoming message is final user turn, never duplicated -----


def test_build_request_appends_incoming_when_not_in_history():
    incoming = make_message("new-guid", "final message", is_from_me=False)
    history = [make_message("g1", "hello", is_from_me=False, rowid=1)]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].endswith("final message")


def test_build_request_does_not_duplicate_incoming_already_last():
    incoming = make_message("dup-guid", "final message", is_from_me=False, rowid=5)
    history = [
        make_message("g1", "hello", is_from_me=False, rowid=1),
        make_message("dup-guid", "final message", is_from_me=False, rowid=5),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    # "final message" text should appear exactly once across all turns.
    combined = " ".join(m["content"] for m in messages)
    assert combined.count("final message") == 1


def test_build_request_incoming_is_final_turn_and_role_user():
    incoming = make_message("last", "ping", is_from_me=False)
    history = [
        make_message("g1", "a", is_from_me=False, rowid=1),
        make_message("g2", "b", is_from_me=True, rowid=2),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "ping"


# -- build_request: leading assistant turns dropped ----------------------------


def test_build_request_drops_leading_assistant_turn():
    incoming = make_message("g3", "you around?", is_from_me=False)
    history = [
        make_message("g1", "leading assistant text", is_from_me=True, rowid=1),
        make_message("g2", "hi", is_from_me=False, rowid=2),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert messages[0]["role"] == "user"
    assert all("leading assistant text" != m["content"] for m in messages)


def test_build_request_drops_multiple_leading_assistant_turns():
    # Even if merged into a single leading assistant turn, it must be dropped.
    # The trailing user turn ("user text") and the incoming user message are
    # consecutive same-role turns, so per the merge rule they combine into one.
    incoming = make_message("g4", "?", is_from_me=False)
    history = [
        make_message("g1", "a1", is_from_me=True, rowid=1),
        make_message("g2", "a2", is_from_me=True, rowid=2),
        make_message("g3", "user text", is_from_me=False, rowid=3),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "user text\n?"


def test_build_request_all_leading_assistant_with_no_user_history():
    # Only assistant history plus the incoming user message.
    incoming = make_message("g2", "hello?", is_from_me=False)
    history = [make_message("g1", "assistant only", is_from_me=True, rowid=1)]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello?"


# -- build_request: filtering empty/reaction messages --------------------------


def test_build_request_drops_reaction_messages():
    incoming = make_message("g3", "final", is_from_me=False)
    history = [
        make_message("g1", "hi", is_from_me=False, rowid=1),
        make_message("g2", "Loved a message", is_from_me=False, rowid=2, is_reaction=True),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    combined = " ".join(m["content"] for m in messages)
    assert "Loved a message" not in combined


def test_build_request_drops_empty_text_messages():
    incoming = make_message("g3", "final", is_from_me=False)
    history = [
        make_message("g1", "hi", is_from_me=False, rowid=1),
        make_message("g2", "   ", is_from_me=True, rowid=2),
    ]
    system, messages = build_request("Bob", history, incoming, max_reply_chars=200)

    assert all(m["content"].strip() for m in messages)


def test_build_request_empty_history_only_incoming():
    incoming = make_message("g1", "hello there", is_from_me=False)
    system, messages = build_request("Bob", [], incoming, max_reply_chars=200)

    assert messages == [{"role": "user", "content": "hello there"}]


# -- build_request: owner-written contact description ---------------------------


def test_build_request_appends_contact_description():
    incoming = make_message("g1", "hello", is_from_me=False)
    system, _ = build_request(
        "Bob", [], incoming, max_reply_chars=200,
        contact_description="my very strict mom so be nice to her",
    )
    assert "my very strict mom so be nice to her" in system
    assert "note" in system.lower()


def test_build_request_omits_description_section_when_empty():
    incoming = make_message("g1", "hello", is_from_me=False)
    system_default, _ = build_request("Bob", [], incoming, max_reply_chars=200)
    system_empty, _ = build_request(
        "Bob", [], incoming, max_reply_chars=200, contact_description=""
    )
    assert system_default == system_empty
    assert "owner has left this note" not in system_default
