"""Participant pause control for the collector's named manual-reset event."""

from __future__ import annotations

import ctypes
import errno
import sys
from typing import Protocol


class PauseControlError(RuntimeError):
    pass


class WindowsPauseController:
    """Set/reset the collector pause event; heartbeat emission remains active."""

    _EVENT_MODIFY_STATE = 0x0002

    def __init__(self, event_name: str) -> None:
        if not event_name or "\\" in event_name or "/" in event_name:
            raise ValueError("pause event name must be a non-path local identifier")
        self.event_name = event_name

    def _change(self, paused: bool) -> None:
        if sys.platform != "win32":
            raise PauseControlError("collector pause control is available only on Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_event = kernel32.OpenEventW
        open_event.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        open_event.restype = ctypes.c_void_p
        handle = open_event(self._EVENT_MODIFY_STATE, False, self.event_name)
        if not handle:
            raise PauseControlError("collector pause event is unavailable")
        try:
            operation = kernel32.SetEvent if paused else kernel32.ResetEvent
            operation.argtypes = [ctypes.c_void_p]
            operation.restype = ctypes.c_int
            if not operation(handle):
                raise PauseControlError("collector pause event update failed")
        finally:
            kernel32.CloseHandle(handle)

    def pause(self) -> None:
        self._change(True)

    def resume(self) -> None:
        self._change(False)


class PauseController(Protocol):
    def pause(self) -> None: ...

    def resume(self) -> None: ...


class MacOSPauseController:
    """Set/reset the collector's private POSIX named semaphore."""

    def __init__(self, event_name: str) -> None:
        if not event_name or "\\" in event_name or "/" in event_name:
            raise ValueError("pause event name must be a non-path local identifier")
        self.event_name = event_name

    def _open(self) -> tuple[ctypes.CDLL, int]:
        library = ctypes.CDLL(None, use_errno=True)
        sem_open = library.sem_open
        sem_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
        sem_open.restype = ctypes.c_void_p
        library.sem_post.argtypes = [ctypes.c_void_p]
        library.sem_post.restype = ctypes.c_int
        library.sem_trywait.argtypes = [ctypes.c_void_p]
        library.sem_trywait.restype = ctypes.c_int
        library.sem_close.argtypes = [ctypes.c_void_p]
        library.sem_close.restype = ctypes.c_int
        handle = sem_open(f"/{self.event_name}".encode("ascii"), 0)
        if handle == ctypes.c_void_p(-1).value:
            raise PauseControlError("collector pause signal is unavailable")
        return library, int(handle)

    def _change(self, paused: bool) -> None:
        if sys.platform != "darwin":
            raise PauseControlError("macOS pause control requires macOS")
        library, handle = self._open()
        try:
            if paused:
                result = library.sem_trywait(ctypes.c_void_p(handle))
                if result != 0 and ctypes.get_errno() != errno.EAGAIN:
                    raise PauseControlError("collector pause signal update failed")
                if library.sem_post(ctypes.c_void_p(handle)) != 0:
                    raise PauseControlError("collector pause signal update failed")
            else:
                while library.sem_trywait(ctypes.c_void_p(handle)) == 0:
                    pass
                if ctypes.get_errno() != errno.EAGAIN:
                    raise PauseControlError("collector pause signal update failed")
        finally:
            library.sem_close(ctypes.c_void_p(handle))

    def pause(self) -> None:
        self._change(True)

    def resume(self) -> None:
        self._change(False)


def make_pause_controller(event_name: str) -> PauseController:
    if sys.platform == "win32":
        return WindowsPauseController(event_name)
    if sys.platform == "darwin":
        return MacOSPauseController(event_name)
    raise PauseControlError("collector pause control requires Windows or macOS")
