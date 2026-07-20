"""Anthropic API access. Owner: Agent 4.

Wraps anthropic.AsyncAnthropic. NO tools are ever passed to the API. The key is
held in memory only; never logged. All calls have explicit timeouts and at most
2 retries with backoff for transient errors (429/5xx/connection). Model list
comes from the live Models API — never hard-coded.
"""

from __future__ import annotations

import anthropic

from ..messaging.events import AnthropicUnavailableError
from .models import ModelInfo


class AnthropicClient:
    def __init__(self, api_key: str, timeout_seconds: float = 30.0) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=2,
        )

    async def validate_key(self) -> bool:
        """True if the key can call GET /v1/models; False on auth failure;
        raises AnthropicUnavailableError on network failure."""
        try:
            await self._client.models.list(limit=1)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            return False
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            raise AnthropicUnavailableError(str(exc)) from exc
        return True

    async def list_models(self) -> list[ModelInfo]:
        """Models available to this key (id + display_name), newest first."""
        models: list[ModelInfo] = []
        try:
            async for model in self._client.models.list(limit=1000):
                models.append(ModelInfo(model_id=model.id, display_name=model.display_name))
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            raise AnthropicUnavailableError(str(exc)) from exc
        return models

    async def complete(
        self,
        model_id: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> str:
        """One untooled Messages API call; returns concatenated text content.
        Raises AnthropicUnavailableError on timeout/exhausted retries."""
        try:
            response = await self._client.messages.create(
                model=model_id,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
            )
        except anthropic.APITimeoutError as exc:
            raise AnthropicUnavailableError(f"timeout: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise AnthropicUnavailableError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise AnthropicUnavailableError(str(exc)) from exc

        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
