from __future__ import annotations

import socket
import stat
import threading
import uuid
from contextlib import suppress
from pathlib import Path

import pytest

from backend.app.runtime.named_pipe import (
    LocalTransportError,
    MacOSUnixSocketServer,
    make_local_transport_server,
)


@pytest.fixture
def socket_directory() -> Path:
    path = Path("/tmp") / f"continuous-auth-test-{uuid.uuid4().hex}"
    try:
        yield path
    finally:
        with suppress(OSError):
            path.rmdir()


def test_macos_unix_socket_is_private_and_streams_bytes(socket_directory: Path) -> None:
    server = MacOSUnixSocketServer(
        "synthetic-transport",
        read_bytes=1024,
        buffer_bytes=4096,
        runtime_directory=socket_directory,
    )
    received: list[bytes] = []
    reader = threading.Thread(target=lambda: received.append(server.read()))
    reader.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(server.socket_path))
        client.sendall(b"synthetic-frame")
        reader.join(timeout=1)
        assert received == [b"synthetic-frame"]
        assert stat.S_IMODE(socket_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
    finally:
        client.close()
        server.close()
    assert not server.socket_path.exists()


def test_second_server_cannot_replace_an_active_endpoint(socket_directory: Path) -> None:
    first = MacOSUnixSocketServer(
        "synthetic-exclusive",
        read_bytes=1024,
        buffer_bytes=4096,
        runtime_directory=socket_directory,
    )
    try:
        with pytest.raises(LocalTransportError, match="already active"):
            MacOSUnixSocketServer(
                "synthetic-exclusive",
                read_bytes=1024,
                buffer_bytes=4096,
                runtime_directory=socket_directory,
            )
    finally:
        first.close()


def test_close_unblocks_a_waiting_reader(socket_directory: Path) -> None:
    server = MacOSUnixSocketServer(
        "synthetic-close",
        read_bytes=1024,
        buffer_bytes=4096,
        runtime_directory=socket_directory,
    )
    reader = threading.Thread(target=server.read)
    reader.start()
    server.close()
    reader.join(timeout=1)
    assert not reader.is_alive()


def test_factory_selects_macos_transport(
    monkeypatch: pytest.MonkeyPatch, socket_directory: Path
) -> None:
    monkeypatch.setattr("backend.app.runtime.named_pipe.sys.platform", "darwin")
    monkeypatch.setattr(
        "backend.app.runtime.named_pipe._macos_runtime_directory", lambda: socket_directory
    )
    server = make_local_transport_server("synthetic-factory", read_bytes=1024, buffer_bytes=4096)
    try:
        assert isinstance(server, MacOSUnixSocketServer)
    finally:
        server.close()


def test_factory_rejects_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.runtime.named_pipe.sys.platform", "linux")
    with pytest.raises(LocalTransportError, match="Windows or macOS"):
        make_local_transport_server("synthetic", read_bytes=1024, buffer_bytes=4096)
