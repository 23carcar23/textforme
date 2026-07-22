"""Restricted responder: context in, validated text out. Owner: Agent 4.

No sending, no settings access, no tools. Only the daemon (after policy
approval) calls this, and only the daemon sends the result.
"""

from __future__ import annotations

from ..anthropic.client import AnthropicClient
from ..anthropic.prompts import build_request
from ..config import MAX_CONTACT_NOTE_CHARS, MAX_PROMPT_CHARS, MAX_REPLY_CHARS
from ..database import ContactRecord
from ..messaging.models import Message
from .policies import validate_reply


def _sanitize(text: str, limit: int) -> str:
    """Keep printable characters plus spaces and newlines (so multi-paragraph
    owner text survives), drop other control characters, bound the length."""
    cleaned = "".join(ch for ch in (text or "") if ch.isprintable() or ch in " \n")
    return cleaned[:limit].strip()


class AnthropicResponder:
    def __init__(self, client: AnthropicClient) -> None:
        self._client = client

    async def generate_reply(
        self,
        contact: ContactRecord,
        recent_messages: list[Message],
        incoming_message: Message,
        model_id: str,
        max_reply_chars: int = MAX_REPLY_CHARS,
        system_prompt: str = "",
        persona_prompt: str = "",
        style_profile: str = "",
    ) -> str:
        """Build prompt (anthropic.prompts), call complete(), validate_reply().
        max_tokens derived from max_reply_chars (~1 token/3 chars, min 64).
        system_prompt/persona_prompt/style_profile are owner-authored (empty =
        built-in default).
        Raises AnthropicUnavailableError or ReplyValidationError."""
        # The display name can be set by the contact themselves; keep what
        # reaches the system prompt short and free of control characters.
        raw_name = contact.display_name or contact.address or "them"
        contact_name = "".join(ch for ch in raw_name if ch.isprintable())[:80] or "them"
        # The description is owner-written via the TUI; still keep it printable
        # and bounded before it reaches the system prompt.
        raw_description = contact.description or ""
        description = _sanitize(raw_description, MAX_CONTACT_NOTE_CHARS)
        system, messages = build_request(
            contact_name=contact_name,
            recent_messages=recent_messages,
            incoming_message=incoming_message,
            max_reply_chars=max_reply_chars,
            contact_description=description,
            system_prompt=_sanitize(system_prompt, MAX_PROMPT_CHARS),
            persona=_sanitize(persona_prompt, MAX_PROMPT_CHARS),
            style_profile=_sanitize(style_profile, MAX_PROMPT_CHARS),
        )

        max_tokens = max(64, max_reply_chars // 3)

        raw_reply = await self._client.complete(
            model_id=model_id,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )

        return validate_reply(raw_reply, max_reply_chars)
