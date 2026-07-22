"""JS bridge exposed to the React UI via pywebview's ``js_api``.

Mirrors the TUI exactly: daemon RPC over the Unix socket for state and
mutations, log file tailed directly from disk, Keychain checked only for
presence. Never exposes message bodies or the API key value.

pywebview invokes these methods from worker threads, while DaemonClient is
asyncio; a dedicated event-loop thread bridges the two. Every method returns
a JSON-safe dict with an ``ok`` flag so the frontend never sees exceptions.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from .. import config, keychain, launchagent
from ..anthropic.prompts import SYSTEM_PROMPT
from ..keychain import has_api_key
from ..tui.app import DaemonClient
from ..tui.logs import clear_log_file, collect_log_lines

_CALL_TIMEOUT = 8.0
# The brief pulls history for several chats and makes a Sonnet call, so it
# needs a much longer budget than a plain state/mutation request.
_BRIEF_TIMEOUT = 60.0

# The three owner-editable prompt settings surfaced in the Prompts screen.
_PROMPT_KEYS = ("system_prompt", "persona_prompt", "style_profile")


class Bridge:
    def __init__(self, client: DaemonClient | None = None) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="textforme-bridge", daemon=True
        )
        self._thread.start()
        self._client = client or DaemonClient()

    def _request(
        self, method: str, params: dict | None = None, timeout: float = _CALL_TIMEOUT
    ) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(
            self._client.request(method, params), self._loop
        )
        try:
            return {"ok": True, "result": future.result(timeout)}
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:  # noqa: BLE001 - bridge must never leak tracebacks to JS
            return {"ok": False, "error": "INTERNAL"}

    # ---- methods callable from JS as window.pywebview.api.<name>() ----

    def get_state(self) -> dict[str, Any]:
        """One snapshot of everything the UI shows."""
        status = self._request("status")
        if not status["ok"]:
            return {
                "ok": True,
                "connected": False,
                "status": {},
                "contacts": [],
                "settings": {},
                "has_api_key": has_api_key(),
                "service_installed": launchagent.is_installed(),
            }
        contacts = self._request("contacts.list")
        settings = self._request("settings.get")
        return {
            "ok": True,
            "connected": True,
            "status": status["result"],
            "contacts": contacts["result"].get("contacts", []) if contacts["ok"] else [],
            "settings": settings["result"].get("settings", {}) if settings["ok"] else {},
            "has_api_key": has_api_key(),
            "service_installed": launchagent.is_installed(),
        }

    def set_ai(self, chat_guid: str, enabled: bool) -> dict[str, Any]:
        return self._request(
            "contacts.set_ai", {"chat_guid": chat_guid, "enabled": bool(enabled)}
        )

    def set_reply_timer(self, chat_guid: str, enabled: bool) -> dict[str, Any]:
        return self._request(
            "contacts.set_reply_timer", {"chat_guid": chat_guid, "enabled": bool(enabled)}
        )

    def set_note(self, chat_guid: str, description: str) -> dict[str, Any]:
        return self._request(
            "contacts.set_description",
            {"chat_guid": chat_guid, "description": str(description)[: config.MAX_CONTACT_NOTE_CHARS]},
        )

    def set_setting(self, key: str, value: str) -> dict[str, Any]:
        value = str(value)
        # Bound the free-text prompt/note fields before they cross the socket.
        if key in _PROMPT_KEYS:
            value = value[: config.MAX_PROMPT_CHARS]
        return self._request("settings.set", {"key": str(key), "value": value})

    def get_prompts(self) -> dict[str, Any]:
        """Current owner prompts plus the built-in default system prompt, so
        the Prompts screen can offer 'restore default' from a real baseline."""
        res = self._request("settings.get")
        settings = res["result"].get("settings", {}) if res["ok"] else {}
        return {
            "ok": True,
            "system_prompt": settings.get("system_prompt", ""),
            "persona_prompt": settings.get("persona_prompt", ""),
            "style_profile": settings.get("style_profile", ""),
            "system_prompt_default": SYSTEM_PROMPT,
        }

    def set_api_key(self, key: str) -> dict[str, Any]:
        """Store/replace the Anthropic key in the Keychain. The daemon rebuilds
        its Anthropic client from the Keychain per reply, so this takes effect
        without a restart."""
        key = str(key).strip()
        if not key.startswith("sk-ant-") or len(key) < 20:
            return {"ok": False, "error": "BAD_KEY"}
        try:
            keychain.set_api_key(key)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "KEYCHAIN_ERROR"}
        return {"ok": True}

    def generate_brief(self) -> dict[str, Any]:
        """Generate a short summary of the conversations the AI has replied to
        since the last brief. Returns {ok, status, summary?, generated_at?}."""
        res = self._request("brief.generate", timeout=_BRIEF_TIMEOUT)
        if not res["ok"]:
            return res
        result = res["result"]
        return {"ok": True, **result}

    def list_models(self) -> dict[str, Any]:
        res = self._request("models.list")
        if not res["ok"]:
            return res
        return {"ok": True, "models": res["result"].get("models", [])}

    def get_logs(self) -> dict[str, Any]:
        return {"ok": True, "lines": collect_log_lines()}

    def clear_logs(self) -> dict[str, Any]:
        if not clear_log_file():
            return {"ok": False, "error": "LOG_CLEAR_ERROR"}
        return {"ok": True}

    def install_service(self) -> dict[str, Any]:
        try:
            launchagent.install()
            launchagent.start()
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "LAUNCHAGENT_ERROR"}
        return {"ok": True}

    def close(self) -> None:
        """Not exposed usefully to JS; called by window.py on shutdown."""
        asyncio.run_coroutine_threadsafe(self._client.close(), self._loop).result(2.0)
        self._loop.call_soon_threadsafe(self._loop.stop)
