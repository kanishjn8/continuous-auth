"""Blocking, host-local byte-stream servers behind one runtime interface."""

from __future__ import annotations

import ctypes
import os
import socket
import stat
import sys
from collections.abc import Callable
from contextlib import suppress
from ctypes import wintypes
from pathlib import Path
from typing import Any, Protocol


class LocalTransportError(RuntimeError):
    """A content-free local collector transport failure."""


class NamedPipeError(LocalTransportError):
    pass


class UnixSocketError(LocalTransportError):
    pass


class LocalByteStreamServer(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


def _last_error() -> int:
    # Reached via getattr so this module still type-checks on platforms
    # where `ctypes.get_last_error` does not exist (see native.py for the
    # same pattern applied to `ctypes.windll`).
    return int(getattr(ctypes, "get_last_error")())  # noqa: B009


class WindowsNamedPipeServer:
    """Own one local inbound pipe instance used by the native collector client."""

    _PIPE_ACCESS_INBOUND = 0x00000001
    _FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    _PIPE_TYPE_BYTE = 0x00000000
    _PIPE_READMODE_BYTE = 0x00000000
    _PIPE_WAIT = 0x00000000
    _PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    _PIPE_UNLIMITED_INSTANCES = 255
    _ERROR_PIPE_CONNECTED = 535
    _ERROR_BROKEN_PIPE = 109
    _ERROR_NO_DATA = 232
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self, name: str, *, read_bytes: int, buffer_bytes: int) -> None:
        if not name or "\\" in name or "/" in name:
            raise ValueError("pipe name must be a non-path local identifier")
        if read_bytes < 1 or buffer_bytes < read_bytes:
            raise ValueError("pipe buffer sizes are invalid")
        if sys.platform != "win32":
            raise NamedPipeError("production named-pipe ingestion requires Windows")
        self.name: str = name
        self.read_bytes: int = read_bytes
        self.buffer_bytes: int = buffer_bytes
        loader: Callable[..., Any] = ctypes.WinDLL
        self._kernel32: Any = loader("kernel32", use_last_error=True)
        self._handle: int | None = None
        self._connected: bool = False
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self._kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        self._kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        self._kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self._kernel32.ConnectNamedPipe.restype = wintypes.BOOL
        self._kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._kernel32.ReadFile.restype = wintypes.BOOL
        self._kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        self._kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
        self._kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self._kernel32.CancelIoEx.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def _open(self) -> None:
        if self._handle is not None:
            return
        endpoint = rf"\\.\pipe\{self.name}"
        handle = self._kernel32.CreateNamedPipeW(
            endpoint,
            self._PIPE_ACCESS_INBOUND | self._FILE_FLAG_FIRST_PIPE_INSTANCE,
            self._PIPE_TYPE_BYTE
            | self._PIPE_READMODE_BYTE
            | self._PIPE_WAIT
            | self._PIPE_REJECT_REMOTE_CLIENTS,
            self._PIPE_UNLIMITED_INSTANCES,
            self.buffer_bytes,
            self.buffer_bytes,
            0,
            None,
        )
        if handle == self._INVALID_HANDLE_VALUE:
            raise NamedPipeError("named pipe creation failed")
        self._handle = int(handle)

    def _connect(self) -> None:
        self._open()
        assert self._handle is not None
        if self._connected:
            return
        connected = self._kernel32.ConnectNamedPipe(self._handle, None)
        if not connected and _last_error() != self._ERROR_PIPE_CONNECTED:
            raise NamedPipeError("collector connection to named pipe failed")
        self._connected = True

    def read(self) -> bytes:
        """Block for the next bytes; return empty bytes after a clean disconnect."""

        self._connect()
        assert self._handle is not None
        buffer = ctypes.create_string_buffer(self.read_bytes)
        received = wintypes.DWORD()
        succeeded = self._kernel32.ReadFile(
            self._handle,
            buffer,
            self.read_bytes,
            ctypes.byref(received),
            None,
        )
        if succeeded:
            return bytes(buffer.raw[: received.value])
        error = _last_error()
        if error in {self._ERROR_BROKEN_PIPE, self._ERROR_NO_DATA}:
            self.disconnect()
            return b""
        raise NamedPipeError("named pipe read failed")

    def disconnect(self) -> None:
        if self._handle is not None and self._connected:
            self._kernel32.DisconnectNamedPipe(self._handle)
        self._connected = False

    def close(self) -> None:
        if self._handle is None:
            return
        self._kernel32.CancelIoEx(self._handle, None)
        if self._connected:
            self._kernel32.DisconnectNamedPipe(self._handle)
        self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self._connected = False

    def __enter__(self) -> WindowsNamedPipeServer:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


class MacOSUnixSocketServer:
    """Own a private per-user Unix socket used by the macOS collector."""

    def __init__(
        self,
        name: str,
        *,
        read_bytes: int,
        buffer_bytes: int,
        runtime_directory: Path | None = None,
    ) -> None:
        if not name or "\\" in name or "/" in name:
            raise ValueError("socket name must be a non-path local identifier")
        if read_bytes < 1 or buffer_bytes < read_bytes:
            raise ValueError("socket buffer sizes are invalid")
        if not hasattr(os, "getuid"):
            raise UnixSocketError("Unix socket transport requires a POSIX host")

        self.name = name
        self.read_bytes = read_bytes
        self.buffer_bytes = buffer_bytes
        self.runtime_directory = runtime_directory or _macos_runtime_directory()
        self.socket_path = self.runtime_directory / f"{name}.sock"
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._closed = False
        self._prepare_runtime_directory()
        self._open()

    def _prepare_runtime_directory(self) -> None:
        try:
            self.runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = self.runtime_directory.stat()
            if metadata.st_uid != os.getuid():
                raise UnixSocketError("collector socket directory is not private")
            self.runtime_directory.chmod(0o700)
            if stat.S_IMODE(self.runtime_directory.stat().st_mode) != 0o700:
                raise UnixSocketError("collector socket directory is not private")
            encoded = os.fsencode(self.socket_path)
            if len(encoded) >= 104:
                raise UnixSocketError("collector socket endpoint is too long")
        except UnixSocketError:
            raise
        except OSError as exc:
            raise UnixSocketError("collector socket directory is unavailable") from exc

    def _remove_stale_endpoint(self) -> None:
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise UnixSocketError("collector socket endpoint cannot be inspected") from exc
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise UnixSocketError("collector socket endpoint is not a private socket")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(self.socket_path))
        except ConnectionRefusedError:
            pass
        except OSError as exc:
            raise UnixSocketError("collector socket endpoint cannot be checked") from exc
        else:
            raise UnixSocketError("collector socket server is already active")
        finally:
            probe.close()
        try:
            self.socket_path.unlink()
        except OSError as exc:
            raise UnixSocketError("stale collector socket cannot be removed") from exc

    def _open(self) -> None:
        self._remove_stale_endpoint()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.buffer_bytes)
            listener.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
            listener.listen(1)
        except OSError as exc:
            listener.close()
            with suppress(OSError):
                self.socket_path.unlink(missing_ok=True)
            raise UnixSocketError("collector socket creation failed") from exc
        self._listener = listener

    def _accept(self) -> None:
        if self._connection is not None:
            return
        if self._closed:
            return
        if self._listener is None:
            raise UnixSocketError("collector socket is closed")
        try:
            connection, _ = self._listener.accept()
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.buffer_bytes)
            self._connection = connection
        except OSError as exc:
            if self._closed:
                return
            raise UnixSocketError("collector socket accept failed") from exc

    def read(self) -> bytes:
        self._accept()
        if self._connection is None:
            return b""
        try:
            chunk = self._connection.recv(self.read_bytes)
        except OSError as exc:
            if self._closed:
                return b""
            self.disconnect()
            raise UnixSocketError("collector socket read failed") from exc
        if not chunk:
            self.disconnect()
        return chunk

    def disconnect(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        with suppress(OSError):
            connection.shutdown(socket.SHUT_RDWR)
        connection.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.disconnect()
        listener, self._listener = self._listener, None
        if listener is not None:
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            listener.close()
        with suppress(OSError):
            self.socket_path.unlink(missing_ok=True)

    def __enter__(self) -> MacOSUnixSocketServer:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


def _macos_runtime_directory() -> Path:
    return Path("/tmp") / f"continuous-auth-{os.getuid():d}"


def make_local_transport_server(
    name: str,
    *,
    read_bytes: int,
    buffer_bytes: int,
) -> LocalByteStreamServer:
    if sys.platform == "win32":
        return WindowsNamedPipeServer(name, read_bytes=read_bytes, buffer_bytes=buffer_bytes)
    if sys.platform == "darwin":
        return MacOSUnixSocketServer(name, read_bytes=read_bytes, buffer_bytes=buffer_bytes)
    raise LocalTransportError("production collector transport requires Windows or macOS")
