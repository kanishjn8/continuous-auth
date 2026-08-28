"""Blocking Windows byte-stream named-pipe server behind a small interface."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from typing import Any


class NamedPipeError(RuntimeError):
    pass


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
        if not connected and ctypes.get_last_error() != self._ERROR_PIPE_CONNECTED:  # type: ignore[attr-defined]
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
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
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
