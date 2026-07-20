"""Unit tests for src/textforme/messaging/client.py (Agent 2 owns this).

These tests spawn a *real* subprocess running a tiny fake `imsg rpc` server
written to a temp file, so ImsgClient's stdio JSON-RPC framing, request/
response correlation, notification routing, and restart/resubscribe behavior
are exercised against real pipes -- never the real `imsg` binary and never
the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from textforme.messaging import client as client_module
from textforme.messaging.events import ImsgRequestError, ImsgUnavailableError
from textforme.messaging.models import Chat, Message

FAKE_IMSG_SCRIPT = '''#!/usr/bin/env python3
import json
import sys
import time

LOG_PATH = {log_path!r}


def log(entry):
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\\n")


def main():
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {{}}
        log({{"method": method, "params": params}})

        if method == "chats.list":
            resp = {{
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {{
                    "chats": [
                        {{
                            "id": 1,
                            "guid": "chat-guid-1",
                            "identifier": "+15551234567",
                            "display_name": "Alice",
                            "service": "iMessage",
                            "is_group": False,
                            "participants": ["+15551234567"],
                        }}
                    ]
                }},
            }}
        elif method == "messages.history":
            resp = {{
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {{
                    "messages": [
                        {{
                            "id": 7,
                            "guid": "m7",
                            "chat_id": params.get("chat_id"),
                            "text": "second",
                            "sender": "+1",
                            "is_from_me": False,
                            "created_at": "2026-01-01T00:00:01Z",
                        }},
                        {{
                            "id": 5,
                            "guid": "m5",
                            "chat_id": params.get("chat_id"),
                            "text": "first",
                            "sender": "+1",
                            "is_from_me": False,
                            "created_at": "2026-01-01T00:00:00Z",
                        }},
                    ]
                }},
            }}
        elif method == "send":
            resp = {{
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {{"ok": True, "id": 99, "guid": "sent-guid-1"}},
            }}
        elif method == "watch.subscribe":
            resp = {{"jsonrpc": "2.0", "id": req_id, "result": {{"subscription": 1}}}}
        elif method == "__emit__":
            note = {{"jsonrpc": "2.0", "method": "message", "params": params}}
            print(json.dumps(note), flush=True)
            resp = {{"jsonrpc": "2.0", "id": req_id, "result": {{"ok": True}}}}
        elif method == "__die__":
            sys.exit(0)
        elif method == "__error__":
            resp = {{"jsonrpc": "2.0", "id": req_id, "error": {{"code": -1, "message": "boom"}}}}
        elif method == "__slow__":
            time.sleep(float(params.get("seconds", 5)))
            resp = {{"jsonrpc": "2.0", "id": req_id, "result": {{}}}}
        else:
            resp = {{
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {{"code": -32601, "message": "method not found"}},
            }}

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
'''


def write_fake_imsg(tmp_path: Path, name: str = "fake_imsg") -> tuple[Path, Path]:
    """Write an executable fake `imsg` script; returns (script_path, log_path)."""
    log_path = tmp_path / f"{name}.log"
    script_path = tmp_path / name
    script_path.write_text(FAKE_IMSG_SCRIPT.format(log_path=str(log_path)))
    script_path.chmod(0o755)
    return script_path, log_path


def read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


# -- start() -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_raises_imsg_unavailable_for_missing_binary(tmp_path):
    missing = tmp_path / "does-not-exist-binary"
    imsg = client_module.ImsgClient(binary=str(missing))
    with pytest.raises(ImsgUnavailableError):
        await imsg.start()


@pytest.mark.asyncio
async def test_start_spawns_process(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        assert imsg._process is not None
        assert imsg._process.returncode is None
    finally:
        await imsg.stop()


# -- request/response correlation -----------------------------------------------


@pytest.mark.asyncio
async def test_list_contacts_parses_chats(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        chats = await imsg.list_contacts(limit=50)
    finally:
        await imsg.stop()

    assert len(chats) == 1
    chat = chats[0]
    assert isinstance(chat, Chat)
    assert chat.chat_id == 1
    assert chat.guid == "chat-guid-1"
    assert chat.display_name == "Alice"
    assert chat.is_group is False


@pytest.mark.asyncio
async def test_get_history_sorted_oldest_to_newest(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        history = await imsg.get_history(chat_id=1, limit=10)
    finally:
        await imsg.stop()

    assert [m.rowid for m in history] == [5, 7]
    assert isinstance(history[0], Message)
    assert history[0].text == "first"
    assert history[1].text == "second"


@pytest.mark.asyncio
async def test_send_message_returns_guid(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        guid = await imsg.send_message(chat_id=1, text="hello")
    finally:
        await imsg.stop()

    assert guid == "sent-guid-1"


@pytest.mark.asyncio
async def test_concurrent_requests_correlate_by_id(tmp_path):
    """Fire several requests concurrently; each must get its own matching result."""
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        results = await asyncio.gather(
            imsg.list_contacts(),
            imsg.get_history(chat_id=1),
            imsg.send_message(chat_id=1, text="hi"),
            imsg.list_contacts(),
        )
    finally:
        await imsg.stop()

    assert len(results[0]) == 1  # list_contacts
    assert len(results[1]) == 2  # get_history
    assert results[2] == "sent-guid-1"  # send_message
    assert len(results[3]) == 1  # list_contacts


@pytest.mark.asyncio
async def test_jsonrpc_error_response_raises_imsg_request_error(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        with pytest.raises(ImsgRequestError) as exc_info:
            await imsg._call("__error__", {})
        assert exc_info.value.code == -1
    finally:
        await imsg.stop()


@pytest.mark.asyncio
async def test_unknown_method_raises_imsg_request_error(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        with pytest.raises(ImsgRequestError):
            await imsg._call("totally.unknown.method", {})
    finally:
        await imsg.stop()


@pytest.mark.asyncio
async def test_request_timeout_raises_imsg_unavailable(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        with pytest.raises(ImsgUnavailableError):
            await imsg._call("__slow__", {"seconds": 2}, timeout=0.1)
    finally:
        await imsg.stop()


# -- health_check ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_true_when_healthy(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        assert await imsg.health_check() is True
    finally:
        await imsg.stop()


@pytest.mark.asyncio
async def test_health_check_false_never_raises_when_not_started(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    # Never started -> no process -> health_check must return False, not raise.
    assert await imsg.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_after_process_dies(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    try:
        await imsg._call("__die__", {}, timeout=1.0)
    except Exception:
        pass
    # Give the reader loop a beat to notice EOF.
    await asyncio.sleep(0.2)
    assert await imsg.health_check() is False
    await imsg.stop()


# -- watch_messages: notifications ------------------------------------------------


@pytest.mark.asyncio
async def test_watch_messages_yields_notifications(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()

    watcher = imsg.watch_messages(since_rowid=0)

    # Trigger the fake server to push a "message" notification.
    emit_task = asyncio.create_task(
        imsg._call(
            "__emit__",
            {
                "subscription": 1,
                "message": {
                    "id": 42,
                    "guid": "notif-guid-1",
                    "chat_id": 1,
                    "text": "hello there",
                    "sender": "+15551234567",
                    "is_from_me": False,
                    "created_at": "2026-01-01T00:00:02Z",
                },
            },
        )
    )

    msg = await asyncio.wait_for(watcher.__anext__(), timeout=5.0)
    await emit_task

    assert isinstance(msg, Message)
    assert msg.rowid == 42
    assert msg.guid == "notif-guid-1"
    assert msg.text == "hello there"

    await watcher.aclose()
    await imsg.stop()


@pytest.mark.asyncio
async def test_watch_messages_subscribes_with_since_rowid(tmp_path):
    script, log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()

    watcher = imsg.watch_messages(since_rowid=123)
    emit_task = asyncio.create_task(
        imsg._call(
            "__emit__",
            {"subscription": 1, "message": {"id": 200, "guid": "g", "chat_id": 1, "text": "x"}},
        )
    )
    await asyncio.wait_for(watcher.__anext__(), timeout=5.0)
    await emit_task
    await watcher.aclose()
    await imsg.stop()

    log = read_log(log_path)
    subscribe_calls = [entry for entry in log if entry["method"] == "watch.subscribe"]
    assert len(subscribe_calls) == 1
    assert subscribe_calls[0]["params"]["since_rowid"] == 123
    assert subscribe_calls[0]["params"]["include_reactions"] is True
    assert subscribe_calls[0]["params"]["debounce_ms"] == 500


@pytest.mark.asyncio
async def test_watch_messages_since_rowid_zero_omits_param(tmp_path):
    script, log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()

    watcher = imsg.watch_messages(since_rowid=0)
    emit_task = asyncio.create_task(
        imsg._call("__emit__", {"subscription": 1, "message": {"id": 1, "guid": "g", "chat_id": 1, "text": "x"}})
    )
    await asyncio.wait_for(watcher.__anext__(), timeout=5.0)
    await emit_task
    await watcher.aclose()
    await imsg.stop()

    log = read_log(log_path)
    subscribe_calls = [entry for entry in log if entry["method"] == "watch.subscribe"]
    assert len(subscribe_calls) == 1
    assert "since_rowid" not in subscribe_calls[0]["params"]


# -- watch_messages: restart / resubscribe (sleep/wake safety) ---------------------


@pytest.mark.asyncio
async def test_watch_messages_restarts_and_resubscribes_from_highest_rowid(tmp_path, monkeypatch):
    # Speed up the exponential backoff so this test doesn't take 1s+.
    monkeypatch.setattr(client_module, "_INITIAL_BACKOFF", 0.02)
    monkeypatch.setattr(client_module, "_MAX_BACKOFF", 0.05)

    script, log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()

    watcher = imsg.watch_messages(since_rowid=0)

    # First notification, rowid=10, on the original subprocess.
    first_emit = asyncio.create_task(
        imsg._call("__emit__", {"subscription": 1, "message": {"id": 10, "guid": "g10", "chat_id": 1, "text": "a"}})
    )
    first_msg = await asyncio.wait_for(watcher.__anext__(), timeout=5.0)
    await first_emit
    assert first_msg.rowid == 10

    dying_process = imsg._process
    assert dying_process is not None

    # The generator is suspended right after the `yield` above; pulling the
    # next item is what drives it back into its loop, where it will notice
    # the death, respawn, and resubscribe.
    next_task = asyncio.create_task(watcher.__anext__())

    # Kill the subprocess (fire-and-forget; it exits before replying).
    kill_task = asyncio.create_task(imsg._call("__die__", {}))
    with contextlib.suppress(Exception):
        await kill_task

    # Wait until a second watch.subscribe call lands in the log -- proof the
    # client respawned and resubscribed on a new subprocess.
    for _ in range(200):
        subs = [e for e in read_log(log_path) if e["method"] == "watch.subscribe"]
        if len(subs) >= 2:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("client never resubscribed after the subprocess died")

    subs = [e for e in read_log(log_path) if e["method"] == "watch.subscribe"]
    assert subs[0]["params"].get("since_rowid") is None
    assert subs[-1]["params"]["since_rowid"] == 10
    assert imsg._process is not dying_process

    # The new subprocess is alive and subscribed; push a fresh notification.
    second_emit = asyncio.create_task(
        imsg._call("__emit__", {"subscription": 1, "message": {"id": 11, "guid": "g11", "chat_id": 1, "text": "b"}})
    )
    second_msg = await asyncio.wait_for(next_task, timeout=5.0)
    await second_emit
    assert second_msg.rowid == 11

    await watcher.aclose()
    await imsg.stop()


# -- stop() ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_is_idempotent(tmp_path):
    script, _log_path = write_fake_imsg(tmp_path)
    imsg = client_module.ImsgClient(binary=str(script))
    await imsg.start()
    await imsg.stop()
    await imsg.stop()  # must not raise


@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    imsg = client_module.ImsgClient(binary="irrelevant")
    await imsg.stop()  # must not raise even though start() was never called
