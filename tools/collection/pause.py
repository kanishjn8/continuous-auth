"""Participant pause control for the collector's named manual-reset event."""

from __future__ import annotations

import ctypes
import sys


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
