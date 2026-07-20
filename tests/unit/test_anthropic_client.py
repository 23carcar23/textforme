"""Unit tests for src/textforme/anthropic/client.py.

The real anthropic.AsyncAnthropic is never instantiated for real and no
network call is ever made: anthropic.AsyncAnthropic is monkeypatched with a
fake that records calls and lets each test control success/failure.
"""

from __future__ import annotations

import httpx
import pytest
import anthropic

from textforme.anthropic.client import AnthropicClient
from textforme.messaging.events import AnthropicUnavailableError


def make_request() -> httpx.Request:
    return httpx.Request("GET", "https://api.anthropic.com/v1/models")


def make_response(status_code: int) -> httpx.Response:
    request = make_request()
    return httpx.Response(
        status_code,
        request=request,
        json={"type": "error", "error": {"type": "error", "message": "boom"}},
    )


class FakePage:
    """Mimics anthropic's AsyncPage: both awaitable and async-iterable."""

    def __init__(self, items=None, error: Exception | None = None):
        self._items = items or []
        self._error = error

    def __await__(self):
        async def _resolve():
            if self._error is not None:
                raise self._error
            return self

        return _resolve().__await__()

    async def __aiter__(self):
        if self._error is not None:
            raise self._error
        for item in self._items:
            yield item


class FakeModels:
    def __init__(self, page_factory):
        self._page_factory = page_factory
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self._page_factory()


class FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeNonTextBlock:
    def __init__(self):
        self.type = "thinking"
        self.text = "should be ignored"


class FakeMessageResponse:
    def __init__(self, content):
        self.content = content


class FakeMessages:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


class FakeModelInfo:
    def __init__(self, model_id: str, display_name: str):
        self.id = model_id
        self.display_name = display_name


class FakeAsyncAnthropic:
    """Stand-in for anthropic.AsyncAnthropic; records constructor kwargs."""

    last_instance: "FakeAsyncAnthropic | None" = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.models = FakeModels(lambda: FakePage([]))
        self.messages = FakeMessages(result=FakeMessageResponse([FakeTextBlock("")]))
        FakeAsyncAnthropic.last_instance = self


@pytest.fixture()
def fake_sdk(monkeypatch):
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAsyncAnthropic)
    return FakeAsyncAnthropic


# -- __init__ -------------------------------------------------------------


def test_init_constructs_sdk_client_with_expected_kwargs(fake_sdk):
    client = AnthropicClient(api_key="sk-test-key", timeout_seconds=12.5)
    fake = FakeAsyncAnthropic.last_instance
    assert fake is not None
    assert fake.init_kwargs["api_key"] == "sk-test-key"
    assert fake.init_kwargs["timeout"] == 12.5
    assert fake.init_kwargs["max_retries"] == 2


def test_api_key_never_appears_in_repr(fake_sdk):
    client = AnthropicClient(api_key="sk-super-secret", timeout_seconds=30.0)
    assert "sk-super-secret" not in repr(client)
    assert "sk-super-secret" not in str(client)


# -- validate_key -----------------------------------------------------------


async def test_validate_key_true_on_success(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage([]))

    assert await client.validate_key() is True


async def test_validate_key_false_on_authentication_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    response = make_response(401)
    error = anthropic.AuthenticationError("invalid key", response=response, body=None)
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(error=error))

    assert await client.validate_key() is False


async def test_validate_key_false_on_permission_denied_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    response = make_response(403)
    error = anthropic.PermissionDeniedError("forbidden", response=response, body=None)
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(error=error))

    assert await client.validate_key() is False


async def test_validate_key_raises_unavailable_on_connection_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    error = anthropic.APIConnectionError(message="network down", request=make_request())
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(error=error))

    with pytest.raises(AnthropicUnavailableError):
        await client.validate_key()


async def test_validate_key_raises_unavailable_on_status_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    response = make_response(500)
    error = anthropic.InternalServerError("server error", response=response, body=None)
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(error=error))

    with pytest.raises(AnthropicUnavailableError):
        await client.validate_key()


# -- list_models --------------------------------------------------------------


async def test_list_models_returns_model_info_in_api_order(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    items = [
        FakeModelInfo("claude-newest", "Claude Newest"),
        FakeModelInfo("claude-mid", "Claude Mid"),
        FakeModelInfo("claude-oldest", "Claude Oldest"),
    ]
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(items))

    result = await client.list_models()

    assert [m.model_id for m in result] == ["claude-newest", "claude-mid", "claude-oldest"]
    assert [m.display_name for m in result] == ["Claude Newest", "Claude Mid", "Claude Oldest"]


async def test_list_models_exhausts_full_iterator(fake_sdk):
    # Simulate an async-iterable spanning what would be multiple pages
    # upstream; our client must not stop early.
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    items = [FakeModelInfo(f"model-{i}", f"Model {i}") for i in range(50)]
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(items))

    result = await client.list_models()

    assert len(result) == 50
    assert result[-1].model_id == "model-49"


async def test_list_models_raises_unavailable_on_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    error = anthropic.APIConnectionError(message="network down", request=make_request())
    FakeAsyncAnthropic.last_instance.models = FakeModels(lambda: FakePage(error=error))

    with pytest.raises(AnthropicUnavailableError):
        await client.list_models()


# -- complete -----------------------------------------------------------------


async def test_complete_calls_messages_create_without_tools(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    fake_messages = FakeMessages(result=FakeMessageResponse([FakeTextBlock("hello there")]))
    FakeAsyncAnthropic.last_instance.messages = fake_messages

    result = await client.complete(
        model_id="claude-x",
        system="be nice",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
    )

    assert result == "hello there"
    assert len(fake_messages.calls) == 1
    call_kwargs = fake_messages.calls[0]
    assert call_kwargs["model"] == "claude-x"
    assert call_kwargs["system"] == "be nice"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert call_kwargs["max_tokens"] == 64
    assert "tools" not in call_kwargs


async def test_complete_concatenates_only_text_blocks(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    blocks = [FakeTextBlock("part one "), FakeNonTextBlock(), FakeTextBlock("part two")]
    FakeAsyncAnthropic.last_instance.messages = FakeMessages(result=FakeMessageResponse(blocks))

    result = await client.complete(
        model_id="claude-x", system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=64
    )

    assert result == "part one part two"
    assert "should be ignored" not in result


async def test_complete_maps_timeout_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    error = anthropic.APITimeoutError(request=make_request())
    FakeAsyncAnthropic.last_instance.messages = FakeMessages(error=error)

    with pytest.raises(AnthropicUnavailableError) as exc_info:
        await client.complete(
            model_id="claude-x", system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=64
        )
    assert "timeout" in str(exc_info.value).lower()


async def test_complete_maps_status_error_after_retries(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    response = make_response(500)
    error = anthropic.InternalServerError("server exploded", response=response, body=None)
    FakeAsyncAnthropic.last_instance.messages = FakeMessages(error=error)

    with pytest.raises(AnthropicUnavailableError):
        await client.complete(
            model_id="claude-x", system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=64
        )


async def test_complete_maps_connection_error(fake_sdk):
    client = AnthropicClient(api_key="sk-test", timeout_seconds=30.0)
    error = anthropic.APIConnectionError(message="dns fail", request=make_request())
    FakeAsyncAnthropic.last_instance.messages = FakeMessages(error=error)

    with pytest.raises(AnthropicUnavailableError):
        await client.complete(
            model_id="claude-x", system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=64
        )
