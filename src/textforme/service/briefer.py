"""Conversation briefing: summarize the chats the AI has been handling.

Read-only, like the responder: conversation context in, a short plain-text
summary out. No sending, no settings mutation, no tools. Fixed to Sonnet 5 —
the brief is a lightweight digest, not a per-reply generation.
"""

from __future__ import annotations

from ..anthropic.client import AnthropicClient
from ..database import ContactRecord
from ..messaging.models import Message

# Summaries always run on Sonnet 5, independent of the reply model the owner
# has selected for the daemon.
BRIEF_MODEL_ID = "claude-sonnet-5"

# Room for a few tight paragraphs; the brief is meant to be skimmable.
_BRIEF_MAX_TOKENS = 700

_SYSTEM_PROMPT = (
    "You are briefing the phone's owner on the text conversations their AI "
    "assistant has been auto-replying to on their behalf. Read the "
    "conversations below and write a short, skimmable summary so the owner "
    "can catch up at a glance. Lead with anything that needs their personal "
    "attention (a question only they can answer, plans being made, something "
    "time-sensitive or emotionally significant). Use one short bullet per "
    "contact. Be concise and factual; do not invent details that are not in "
    "the messages. Output plain text only."
)


def _format_conversation(contact: ContactRecord, history: list[Message]) -> str:
    name = contact.display_name or contact.address or "Unknown contact"
    lines: list[str] = []
    for msg in history:
        text = (msg.text or "").strip()
        if not text or msg.is_reaction:
            continue
        who = "You (AI)" if msg.is_from_me else name
        lines.append(f"{who}: {text}")
    if not lines:
        return ""
    return f"Conversation with {name}:\n" + "\n".join(lines)


async def generate_brief(
    client: AnthropicClient,
    conversations: list[tuple[ContactRecord, list[Message]]],
) -> str:
    """Summarize the given conversations with Sonnet 5.

    ``conversations`` is a list of (contact, recent messages oldest→newest).
    Raises AnthropicUnavailableError on API failure.
    """
    blocks = [
        block
        for contact, history in conversations
        if (block := _format_conversation(contact, history))
    ]
    if not blocks:
        return ""

    user_content = (
        "Here are the recent conversations the AI has replied to:\n\n"
        + "\n\n".join(blocks)
    )

    summary = await client.complete(
        model_id=BRIEF_MODEL_ID,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=_BRIEF_MAX_TOKENS,
    )
    return summary.strip()
