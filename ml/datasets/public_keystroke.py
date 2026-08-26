"""Loader for a public free-text keystroke dataset (PLAN.md Section 9.1).

**Pipeline validation only. Never a headline result.** PLAN.md Section 9.1:
"Public keystroke datasets contain no mouse data and no application
context -- they can validate at most half of this architecture. They are
not this project's results. They are a plumbing check." Every window built
from this loader's output must carry ``Provenance.PUBLIC``.

ASSUMPTION (`[OPEN]` per PLAN.md Section 9.1: "Specific dataset choice --
to be decided in Phase 0/1 after reviewing licensing and format"): no
specific dataset is bundled or downloaded by this repository (participant/
third-party data must never be committed, and this environment has no
network access to fetch one). This module instead:

1. Implements ``classify_char_to_key_class``, the content-free classifier
   any raw free-text keystroke dataset must be passed through -- this is
   the same responsibility ADR-004 gives the native collector, applied here
   to an external text corpus instead of live OS input. The mapping is
   QWERTY-layout-based (ASSUMPTION, since PLAN.md does not specify a
   physical-key-to-class table beyond the class list itself, Section 6
   ADR-004). Raw characters are discarded immediately after classification,
   exactly like the collector hook callback -- they never appear in the
   returned ``KeyboardEvent`` objects.
2. Implements ``load_public_keystroke_csv``, a generic adapter for the
   common "one row per keystroke, columns for participant/user id, key
   press time, key release time, and the key/character" shape shared by
   most published free-text keystroke corpora (e.g. Dhakal et al.'s 136M
   Keystrokes dataset, the Aalto University keystroke-dynamics datasets).
   Column names are configurable so the concrete dataset choice remains a
   drop-in decision, not a rewrite, once T-006 (Akshay) selects one.
3. Provides ``write_fixture_csv`` / uses it in tests, to prove the loader
   works end-to-end without any real or network-fetched dataset -- the
   fixture text is clearly synthetic filler, not treated as real data
   anywhere in this codebase.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from ml.features.schema import DeviceClass, KeyboardEvent, KeyClass

_LEFT_HOME = set("asdf")
_LEFT_UPPER = set("qwert")
_LEFT_LOWER = set("zxcvb")
_RIGHT_HOME = set("jkl;")
_RIGHT_UPPER = set("yuiop")
_RIGHT_LOWER = set("nm,./")

_NAV_TOKENS = {"left", "right", "up", "down", "home", "end", "pageup", "pagedown", "tab"}
_MODIFIER_TOKENS = {"shift", "ctrl", "control", "alt", "meta", "cmd", "win", "capslock"}
_ENTER_TOKENS = {"enter", "return", "\n", "\r"}
_BACKSPACE_TOKENS = {"backspace", "\b"}
_DELETE_TOKENS = {"delete", "del"}


def classify_char_to_key_class(raw_key: str) -> KeyClass:
    """Map one dataset key token to an ADR-004 KeyClass.

    The input ``raw_key`` is consumed and discarded by this function; the
    caller must not retain it. This function is the loader's sole point of
    contact with dataset content, mirroring the collector hook callback's
    "classify then discard" invariant (ADR-004).
    """
    if raw_key is None or raw_key == "":
        return KeyClass.OTHER
    if raw_key == " ":
        return KeyClass.SPACE

    token = raw_key.strip().lower()

    if token in _BACKSPACE_TOKENS:
        return KeyClass.BACKSPACE
    if token in _DELETE_TOKENS:
        return KeyClass.DELETE
    if token in _ENTER_TOKENS:
        return KeyClass.ENTER
    if token in _MODIFIER_TOKENS:
        return KeyClass.MODIFIER
    if token in _NAV_TOKENS:
        return KeyClass.NAVIGATION
    if len(token) >= 2 and token[0] == "f" and token[1:].isdigit():
        return KeyClass.FUNCTION
    if token == " " or token == "space":
        return KeyClass.SPACE

    if len(token) == 1:
        ch = token
        if ch in _LEFT_HOME:
            return KeyClass.ALPHA_L_HOME
        if ch in _LEFT_UPPER:
            return KeyClass.ALPHA_L_UPPER
        if ch in _LEFT_LOWER:
            return KeyClass.ALPHA_L_LOWER
        if ch in _RIGHT_HOME:
            return KeyClass.ALPHA_R_HOME
        if ch in _RIGHT_UPPER:
            return KeyClass.ALPHA_R_UPPER
        if ch in _RIGHT_LOWER:
            return KeyClass.ALPHA_R_LOWER
        if ch.isdigit():
            return KeyClass.DIGIT
        if ch.isprintable():
            return KeyClass.PUNCT

    return KeyClass.OTHER


def load_public_keystroke_csv(
    path: Path | str,
    *,
    user_col: str = "participant_id",
    press_time_col: str = "press_time_ms",
    release_time_col: str = "release_time_ms",
    key_col: str = "key",
    time_unit_to_us: float = 1000.0,
    app_id: int = 0,
) -> dict[str, list[KeyboardEvent]]:
    """Load a free-text keystroke CSV into per-user KeyboardEvent lists.

    Returns a mapping ``user_id -> [KeyboardEvent, ...]`` sorted by
    capture time. No mouse or context events are produced (PLAN.md Section
    9.1 explicit limitation). ``device_class`` is set to ``UNKNOWN`` since
    public datasets do not report device metadata (ADR-009 note).

    Raises ``ValueError`` on missing required columns rather than silently
    skipping rows -- a malformed dataset must fail loudly, not produce a
    partial, silently-wrong corpus.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"public keystroke dataset not found: {path}")

    per_user_rows: dict[str, list[tuple[float, float, KeyClass]]] = defaultdict(list)
    seq_counters: dict[str, int] = defaultdict(int)

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {user_col, press_time_col, release_time_col, key_col}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = required - set(reader.fieldnames or [])
            raise ValueError(f"CSV missing required columns: {missing}")

        for row in reader:
            user_id = row[user_col]
            try:
                press_us = float(row[press_time_col]) * time_unit_to_us
                release_us = float(row[release_time_col]) * time_unit_to_us
            except (TypeError, ValueError) as e:
                raise ValueError(f"non-numeric timestamp in row for user {user_id!r}: {e}") from e
            if release_us < press_us:
                raise ValueError(
                    f"row for user {user_id!r} has release_time ({release_us}us) before "
                    f"press_time ({press_us}us) -- malformed row, refusing to silently clamp it"
                )
            key_class = classify_char_to_key_class(row[key_col])
            per_user_rows[user_id].append((press_us, release_us, key_class))

    result: dict[str, list[KeyboardEvent]] = {}
    for user_id, rows in per_user_rows.items():
        rows.sort(key=lambda r: r[0])
        events: list[KeyboardEvent] = []
        for press_us, release_us, key_class in rows:
            down_seq = seq_counters[user_id]
            seq_counters[user_id] += 1
            up_seq = seq_counters[user_id]
            seq_counters[user_id] += 1
            events.append(
                KeyboardEvent(
                    type="KEY_DOWN",
                    t_capture_us=int(press_us),
                    key_class=key_class,
                    is_repeat=False,
                    device_class=DeviceClass.UNKNOWN,
                    app_id=app_id,
                    seq=down_seq,
                )
            )
            events.append(
                KeyboardEvent(
                    type="KEY_UP",
                    t_capture_us=int(release_us),
                    key_class=key_class,
                    is_repeat=False,
                    device_class=DeviceClass.UNKNOWN,
                    app_id=app_id,
                    seq=up_seq,
                )
            )
        result[user_id] = events
    return result


def write_fixture_csv(
    path: Path | str,
    rows: Iterable[tuple[str, float, float, str]],
    *,
    user_col: str = "participant_id",
    press_time_col: str = "press_time_ms",
    release_time_col: str = "release_time_ms",
    key_col: str = "key",
) -> None:
    """Write a small CSV in the loader's expected shape -- for tests only.

    ``rows`` is ``(user_id, press_time_ms, release_time_ms, key)``. This
    function exists solely so ``ml/tests`` can validate
    ``load_public_keystroke_csv`` end-to-end without a real dataset; the
    written content is placeholder text, not real or participant data, and
    must never be treated as such.
    """
    path = Path(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([user_col, press_time_col, release_time_col, key_col])
        for user_id, press_ms, release_ms, key in rows:
            writer.writerow([user_id, press_ms, release_ms, key])
