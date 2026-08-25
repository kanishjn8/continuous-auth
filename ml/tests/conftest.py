from __future__ import annotations

import pytest

from ml.features.config import load_config
from ml.features.schema import (
    DeviceClass,
    KeyClass,
    KeyboardEvent,
    MouseButton,
    MouseEvent,
)


def kbd(seq: int, t_us: int, key_class: KeyClass, ev_type: str, *, is_repeat: bool = False) -> KeyboardEvent:
    return KeyboardEvent(
        type=ev_type,
        t_capture_us=t_us,
        key_class=key_class,
        is_repeat=is_repeat,
        device_class=DeviceClass.INTERNAL_KEYBOARD,
        app_id=1,
        seq=seq,
    )


def mouse_move(seq: int, t_us: int, x: float, y: float) -> MouseEvent:
    return MouseEvent(
        type="MOVE",
        t_capture_us=t_us,
        x=x,
        y=y,
        device_class=DeviceClass.EXTERNAL_MOUSE,
        app_id=1,
        seq=seq,
    )


def mouse_click(seq: int, t_us: int, ev_type: str, button: MouseButton = MouseButton.LEFT) -> MouseEvent:
    return MouseEvent(
        type=ev_type,
        t_capture_us=t_us,
        x=0.0,
        y=0.0,
        button=button,
        device_class=DeviceClass.EXTERNAL_MOUSE,
        app_id=1,
        seq=seq,
    )


def mouse_scroll(seq: int, t_us: int, dy: float = -1.0) -> MouseEvent:
    return MouseEvent(
        type="SCROLL",
        t_capture_us=t_us,
        x=0.0,
        y=0.0,
        scroll_dy=dy,
        device_class=DeviceClass.EXTERNAL_MOUSE,
        app_id=1,
        seq=seq,
    )


@pytest.fixture()
def ml_config():
    return load_config()
