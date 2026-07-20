"""Tests for tui.app.DaemonClient against an in-test asyncio unix-socket
server speaking the ARCHITECTURE.md §5 protocol. No real daemon is used."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from textforme.tui.app import DaemonClient


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            request = json.loads(line)
            method = request.get("method")
            req_id = request.get("id")
            params = request.get("params") or {}

            if method == "ping":
                response = {"id": req_id, "ok": True, "result": {}}
            elif method == "echo":
                response = {"id": req_id, "ok": True, "result": {"params": params}}
            elif method == "boom":
                response = {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "INTERNAL", "message": "boom"},
                }
            elif method == "hang":
                # Never respond; used to exercise the client's timeout.
                await asyncio.sleep(2)
                response = {"id": req_id, "ok": True, "result": {}}
            else:
                response = {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "UNKNOWN_METHOD", "message": str(method)},
                }
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def sock_path():
    tmp_dir = tempfile.mkdtemp(dir="/tmp")
    path = Path(tmp_dir) / "d.sock"
    server = await asyncio.start_unix_server(_handle, path=str(path))
    try:
        yield path
    finally:
        server.close()
        await server.wait_closed()
        try:
            os.unlink(path)
        except OSError:
            pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def test_connect_succeeds_and_is_connected(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=2.0)
    assert client.is_connected is False
    assert await client.connect() is True
    assert client.is_connected is True
    await client.close()
    assert client.is_connected is False


async def test_connect_fails_when_socket_missing(tmp_path) -> None:
    client = DaemonClient(socket_path=tmp_path / "does-not-exist.sock", timeout=0.5)
    assert await client.connect() is False
    assert client.is_connected is False


async def test_request_returns_result_dict(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=2.0)
    result = await client.request("ping")
    assert result == {}
    await client.close()


async def test_request_auto_increments_id(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=2.0)
    first = await client.request("echo", {"n": 1})
    second = await client.request("echo", {"n": 2})
    assert first["params"] == {"n": 1}
    assert second["params"] == {"n": 2}
    await client.close()


async def test_request_connects_lazily_if_needed(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=2.0)
    assert client.is_connected is False
    result = await client.request("ping")
    assert result == {}
    assert client.is_connected is True
    await client.close()


async def test_error_response_raises_runtime_error_with_code(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=2.0)
    with pytest.raises(RuntimeError, match="INTERNAL"):
        await client.request("boom")
    await client.close()


async def test_unknown_method_error_code_surfaced(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=2.0)
    with pytest.raises(RuntimeError, match="UNKNOWN_METHOD"):
        await client.request("nonexistent")
    await client.close()


async def test_request_times_out(sock_path: Path) -> None:
    client = DaemonClient(socket_path=sock_path, timeout=0.2)
    with pytest.raises(RuntimeError):
        await client.request("hang")
    await client.close()


async def test_request_without_connection_raises(tmp_path) -> None:
    client = DaemonClient(socket_path=tmp_path / "missing.sock", timeout=0.3)
    with pytest.raises(RuntimeError, match="NOT_CONNECTED"):
        await client.request("ping")
