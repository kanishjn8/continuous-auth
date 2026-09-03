# PILOT Collection Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a normal collection run record real `PILOT` participant data with the verification anchors, configuration, and operator visibility a five-day human pilot requires.

**Architecture:** Provenance is already derived solely from the storage profile; this plan completes the surrounding lifecycle. It adds A3 scheduled verification anchors (a scheduler plus a `ChallengeService` entry point that bypasses the disabled enforcement path), a pilot collection config, a fix to provenance-mislabelled session evidence, an operator-visible provenance indicator, and documentation corrections.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, pytest, React + TypeScript (Vite), PowerShell.

**Spec:** `docs/superpowers/specs/2026-09-03-pilot-default-workflow-design.md` (Sections 7, 4, 6). Read Section 7 before starting.

## Global Constraints

- `ml/training/gate.py::require_promotion_gate` is not modified. Not one line.
- G1–G6 semantics, `quarantine_days: 7`, `retraining_cadence_days: 7`, `regression_tolerance`, and `min_promotable_windows` are unchanged.
- `config/storage.development.yaml`, `config/collection.development.yaml`, `config/updates.development.yaml`, `config/synthetic.development.yaml`, `tools/synthetic/`, and `tools/demo/` are not modified.
- The `development_only: Literal[True]` constraint in all six runtime config loaders stays. Do not relax it. No `config/updates.pilot.yaml` is created.
- No new tunable is hardcoded outside `config/`. A3 cadence comes from `update_manager.scheduled_anchor_interval_seconds` (existing value `14400`).
- Never persist, log, or expose typed content, key identity, window titles, paths, URLs, clipboard, or screen data. Challenge answers are never stored, logged, audited, or returned.
- Synthetic fixtures only in tests. Real participant records are never test fixtures.
- Run `python tools/guardrails/check.py` before each commit that touches Python.
- Every new parameter is keyword-only with a default, so existing public signatures keep working.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/app/updates/anchors.py` (create) | A3 due-ness decision. Pure logic, no I/O, no anchor creation. |
| `backend/app/decisions/challenge.py` (modify) | Add `open_scheduled()` — register a pending challenge with no `RiskDecision`. |
| `backend/app/runtime/orchestrator.py` (modify) | Drive the scheduler on the heartbeat path; record the anchor on a correct answer. |
| `backend/app/api/backend.py` (modify) | Route a scheduled-challenge response to the A3 anchor recorder; expose storage provenance. |
| `backend/app/api/routes.py` (modify) | Add the provenance status field to the existing status surface. |
| `config/collection.pilot.yaml` (create) | Pilot collection targets and eligibility. |
| `dashboard/src/*` (modify) | Surface a pending scheduled verification and the active provenance. |
| Docs (modify) | Remove the assumption that normal collection is synthetic. |

---

### Task 1: A3 due-ness scheduler

Pure decision logic, separated from the runtime so it can be tested without a database, a session, or a clock.

**Files:**
- Create: `backend/app/updates/anchors.py`
- Test: `backend/tests/test_scheduled_anchors.py`

**Interfaces:**
- Consumes: `UpdateSettings` from `backend/app/updates/config.py` (existing; `settings.update_manager.scheduled_anchor_interval_seconds` is an `int`).
- Produces: `ScheduledAnchorScheduler` with `__init__(self, interval_seconds: float)`, `session_started(self, *, session_id: str, at: datetime) -> None`, `due(self, *, session_id: str, now: datetime) -> bool`, `anchor_recorded(self, *, session_id: str, at: datetime) -> None`, `session_ended(self, *, session_id: str) -> None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_scheduled_anchors.py`:

```python
"""A3 prompt scheduling: only a successful A3 resets the clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.updates.anchors import ScheduledAnchorScheduler

FOUR_HOURS = 4 * 3600.0
START = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


def _scheduler() -> ScheduledAnchorScheduler:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    scheduler.session_started(session_id="s1", at=START)
    return scheduler


def test_not_due_before_the_interval_elapses() -> None:
    scheduler = _scheduler()
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=3, minutes=59)) is False


def test_due_once_the_interval_elapses() -> None:
    scheduler = _scheduler()
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=4)) is True


def test_recording_an_anchor_resets_the_clock() -> None:
    scheduler = _scheduler()
    at = START + timedelta(hours=4)
    assert scheduler.due(session_id="s1", now=at) is True
    scheduler.anchor_recorded(session_id="s1", at=at)
    assert scheduler.due(session_id="s1", now=at + timedelta(hours=3)) is False
    assert scheduler.due(session_id="s1", now=at + timedelta(hours=4)) is True


def test_unknown_session_is_never_due() -> None:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    assert scheduler.due(session_id="absent", now=START) is False


def test_ended_session_is_never_due() -> None:
    scheduler = _scheduler()
    scheduler.session_ended(session_id="s1")
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=9)) is False


def test_naive_timestamps_are_rejected() -> None:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    with pytest.raises(ValueError):
        scheduler.session_started(session_id="s1", at=datetime(2026, 9, 3, 9, 0))


def test_non_positive_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScheduledAnchorScheduler(0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_scheduled_anchors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.updates.anchors'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/updates/anchors.py`:

```python
"""Decide when an A3 scheduled verification prompt is due.

PLAN.md Section 12.2 defines A3 as a deliberate low-frequency prompt whose
purpose is to create verification anchors for otherwise uneventful sessions.

Only a successful A3 resets the clock. A1 anchors a session's first segment
and nothing else, so letting it suppress the next prompt would remove prompts
precisely where A3 is the only mechanism that can anchor a segment, and would
make the effective cadence depend on session length.

This module decides timing only. It never creates an anchor and never
fabricates evidence; a prompt must still be answered correctly by a human.
"""

from __future__ import annotations

from datetime import UTC, datetime


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("scheduled anchor timestamps must include a timezone")
    return value.astimezone(UTC)


class ScheduledAnchorScheduler:
    """Track, per active session, when the next A3 prompt becomes due."""

    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("scheduled anchor interval must be positive")
        self._interval_seconds = float(interval_seconds)
        self._last_anchor: dict[str, datetime] = {}

    def session_started(self, *, session_id: str, at: datetime) -> None:
        self._last_anchor[session_id] = _as_utc(at)

    def anchor_recorded(self, *, session_id: str, at: datetime) -> None:
        if session_id in self._last_anchor:
            self._last_anchor[session_id] = _as_utc(at)

    def due(self, *, session_id: str, now: datetime) -> bool:
        last = self._last_anchor.get(session_id)
        if last is None:
            return False
        return (_as_utc(now) - last).total_seconds() >= self._interval_seconds

    def session_ended(self, *, session_id: str) -> None:
        self._last_anchor.pop(session_id, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_scheduled_anchors.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add backend/app/updates/anchors.py backend/tests/test_scheduled_anchors.py
git commit -m "feat: add A3 scheduled verification anchor scheduler"
```

---

### Task 2: `ChallengeService.open_scheduled`

A scheduled verification is not a risk decision. `ChallengeService.open()` requires a `RiskDecision`, and routing A3 through `EnforcementCoordinator` would be skipped entirely because `enforcement.enabled` is `false` during collection. This adds a parallel entry point.

**Files:**
- Modify: `backend/app/decisions/challenge.py`
- Test: `backend/tests/test_challenge.py`

**Interfaces:**
- Consumes: `PendingChallenge`, `ChallengeCredential`, `ChallengeNotConfigured` (existing in the same module).
- Produces: `ChallengeService.open_scheduled(self, *, user_id: str, session_id: str, segment_id: str) -> PendingChallenge`. The returned `decision_id` is `f"scheduled-anchor:{token}"`. `action` is `DecisionAction.SOFT_CHALLENGE`, so `blocking` is `False` — a verification prompt must never block a participant's work.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_challenge.py`. Reuse the existing fixture that builds a configured `ChallengeService` in that file; if it is named differently, adapt the fixture name and keep the assertions.

```python
def test_open_scheduled_registers_a_non_blocking_pending_challenge(
    configured_service,
) -> None:
    pending = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    assert pending.decision_id.startswith("scheduled-anchor:")
    assert pending.user_id == "participant-01"
    assert pending.session_id == "s1"
    assert pending.segment_id == "seg1"
    assert pending.blocking is False
    assert pending in configured_service.pending()


def test_open_scheduled_requires_a_configured_challenge(unconfigured_service) -> None:
    from backend.app.decisions.challenge import ChallengeNotConfigured

    with pytest.raises(ChallengeNotConfigured):
        unconfigured_service.open_scheduled(
            user_id="participant-01", session_id="s1", segment_id="seg1"
        )


def test_scheduled_challenges_get_distinct_ids(configured_service) -> None:
    first = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    second = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg2"
    )
    assert first.decision_id != second.decision_id


def test_a_correct_answer_to_a_scheduled_challenge_yields_evidence(
    configured_service,
) -> None:
    from backend.app.decisions.challenge import ResponseOutcome

    pending = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    result = configured_service.respond(pending.decision_id, "correct-horse")
    assert result.outcome is ResponseOutcome.ACCEPTED
    assert result.evidence_reference is not None
```

If `configured_service` / `unconfigured_service` fixtures do not exist in that file, add them:

```python
@pytest.fixture
def unconfigured_service(tmp_path):
    from backend.app.decisions.config import load_enforcement_settings
    from backend.app.storage.config import load_storage_settings
    from backend.app.storage.service import StorageService

    root = Path(__file__).resolve().parents[2]
    settings = load_storage_settings(
        root / "config/storage.development.yaml",
        workspace_root=root,
        environment={"LOCALAPPDATA": str(tmp_path)},
    )
    return ChallengeService(StorageService.open(settings), load_enforcement_settings())


@pytest.fixture
def configured_service(unconfigured_service):
    unconfigured_service.configure(
        question="first pet",
        answer="correct-horse",
        confirm_answer="correct-horse",
    )
    return unconfigured_service
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_challenge.py -k scheduled -v`
Expected: FAIL — `AttributeError: 'ChallengeService' object has no attribute 'open_scheduled'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/decisions/challenge.py`, add this method to `ChallengeService` immediately after `open()`:

```python
    def open_scheduled(
        self,
        *,
        user_id: str,
        session_id: str,
        segment_id: str,
    ) -> PendingChallenge:
        """Register a pending A3 scheduled verification prompt.

        Deliberately parallel to :meth:`open` rather than routed through
        ``EnforcementCoordinator``. ``config/enforcement.development.yaml``
        sets ``enabled: false`` for ordinary collection, so
        ``NativeChallengeAdapter`` would return ``ENFORCEMENT_DISABLED`` and no
        prompt would ever appear during a pilot. Enforcement policy governs
        whether the system acts against a participant; it must not govern
        whether research evidence can be created.

        A scheduled verification is not a risk decision, so no ``RiskDecision``
        is accepted or fabricated. It is non-blocking: a verification prompt
        must never stop a participant working.
        """

        credential = self._credential_or_none()
        if credential is None:
            raise ChallengeNotConfigured("no security challenge has been configured")
        now = self._clock()
        pending = PendingChallenge(
            decision_id=f"scheduled-anchor:{secrets.token_urlsafe(16)}",
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            action=DecisionAction.SOFT_CHALLENGE,
            question=credential.question,
            opened_at=now,
            expires_at=now + timedelta(seconds=self._settings.challenge_timeout_seconds),
            response_token=secrets.token_urlsafe(32),
        )
        with self._lock:
            self._pending[pending.decision_id] = pending
        return pending
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_challenge.py -v`
Expected: PASS, including the four new tests and all pre-existing ones.

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add backend/app/decisions/challenge.py backend/tests/test_challenge.py
git commit -m "feat: add open_scheduled for A3 prompts outside the enforcement path"
```

---

### Task 3: Session entry evidence is no longer labelled synthetic

`create_collection_application` stamps every session's A1 anchor with `synthetic-entry-...`, including real pilot sessions. The anchor is the evidence a future promotion decision rests on, so this is a data-integrity fix.

**Files:**
- Modify: `backend/app/runtime/application.py:151`
- Test: `backend/tests/test_provenance_flow.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new. Behaviour change only.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_provenance_flow.py`:

```python
def test_session_entry_evidence_is_not_labelled_synthetic() -> None:
    """A pilot session's A1 anchor must not carry synthetic-run wording.

    The anchor is the evidence a later promotion decision rests on, so a
    provenance word baked into it is a data-integrity problem, not cosmetics.
    """

    source = (
        WORKSPACE / "backend" / "app" / "runtime" / "application.py"
    ).read_text(encoding="utf-8")
    assert "synthetic-entry-" not in source
    assert "session-entry-" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_provenance_flow.py -k entry_evidence -v`
Expected: FAIL — `assert 'synthetic-entry-' not in source`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/runtime/application.py`, change the `start_runtime` body:

```python
        _, emissions = orchestrator.start_authenticated_session(
            user_id=participant_id,
            evidence_reference=f"session-entry-{secrets.token_urlsafe(32)}",
        )
```

In the same file, replace the module docstring first line:

```python
"""Construct the complete collection API plus collector runtime."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_provenance_flow.py -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add backend/app/runtime/application.py backend/tests/test_provenance_flow.py
git commit -m "fix: stop labelling pilot session entry evidence as synthetic"
```

---

### Task 4: Wire the A3 scheduler into the runtime

**Files:**
- Modify: `backend/app/runtime/orchestrator.py`
- Modify: `backend/app/runtime/application.py`
- Test: `backend/tests/test_scheduled_anchors.py`

**Interfaces:**
- Consumes: `ScheduledAnchorScheduler` (Task 1); `ChallengeService.open_scheduled` (Task 2); `EnforcementCoordinator.record_scheduled_verification` (existing); `AdapterResult`, `ActionStatus` from `backend.app.decisions.adapters`.
- Produces: `RuntimeOrchestrator.__init__` gains keyword-only `anchor_scheduler: ScheduledAnchorScheduler | None = None` and `challenge_service: ChallengeService | None = None`. New methods `RuntimeOrchestrator.check_scheduled_anchor(self, now: datetime | None = None) -> PendingChallenge | None` and `RuntimeOrchestrator.complete_scheduled_anchor(self, *, decision_id: str, session_id: str, segment_id: str, evidence_reference: str, at: datetime | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_scheduled_anchors.py`:

```python
def test_scheduler_is_not_due_without_an_active_session() -> None:
    """A prompt is only meaningful inside a session that can be anchored."""

    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    assert scheduler.due(session_id="s1", now=START + timedelta(days=1)) is False


def test_interval_comes_from_update_settings() -> None:
    """The cadence is configuration, never a literal in code (AGENTS.md 7)."""

    from pathlib import Path

    from backend.app.updates.config import load_update_settings

    root = Path(__file__).resolve().parents[2]
    settings = load_update_settings(root / "config/updates.development.yaml")
    seconds = settings.update_manager.scheduled_anchor_interval_seconds
    scheduler = ScheduledAnchorScheduler(seconds)
    scheduler.session_started(session_id="s1", at=START)
    assert scheduler.due(session_id="s1", now=START + timedelta(seconds=seconds - 1)) is False
    assert scheduler.due(session_id="s1", now=START + timedelta(seconds=seconds)) is True


def test_collection_and_runtime_cadences_agree() -> None:
    """Two configs describe one concept; they must not drift apart."""

    from pathlib import Path

    from backend.app.updates.config import load_update_settings
    from tools.collection.config import load_collection_settings

    root = Path(__file__).resolve().parents[2]
    runtime_seconds = load_update_settings(
        root / "config/updates.development.yaml"
    ).update_manager.scheduled_anchor_interval_seconds
    collection_hours = load_collection_settings(
        root / "config/collection.pilot.yaml"
    ).scheduled_anchor_interval_hours
    assert collection_hours * 3600 == runtime_seconds
```

Note: `test_collection_and_runtime_cadences_agree` depends on Task 5 creating `config/collection.pilot.yaml`. If executing tasks in order, expect it to fail until Task 5 lands; run it again at the end of Task 5.

- [ ] **Step 2: Run the two scheduler tests to confirm Task 1 holds up**

Run: `python -m pytest backend/tests/test_scheduled_anchors.py -k "active_session or update_settings" -v`
Expected: PASS for both — they exercise Task 1 code and should already pass. If either fails, Task 1 is incomplete; fix Task 1 before continuing.

- [ ] **Step 3: Wire the scheduler into the orchestrator**

In `backend/app/runtime/orchestrator.py`, add to the imports:

```python
from backend.app.decisions.challenge import ChallengeError, ChallengeService, PendingChallenge
from backend.app.decisions.adapters import ActionStatus, AdapterResult
from backend.app.updates.anchors import ScheduledAnchorScheduler
```

Add two keyword-only parameters to `RuntimeOrchestrator.__init__`, alongside the existing `update_manager` parameter:

```python
        anchor_scheduler: ScheduledAnchorScheduler | None = None,
        challenge_service: ChallengeService | None = None,
```

and store them in the body:

```python
        self.anchor_scheduler = anchor_scheduler
        self.challenge_service = challenge_service
```

In `start_authenticated_session`, immediately after the existing `self.enforcement.record_authenticated_entry(...)` call, add:

```python
        if self.anchor_scheduler is not None:
            self.anchor_scheduler.session_started(
                session_id=lifecycle.session_id, at=observed
            )
```

In `end_session`, before `self._active_user = None`, add:

```python
        if self.anchor_scheduler is not None and self._session_id is not None:
            self.anchor_scheduler.session_ended(session_id=self._session_id)
```

If `RuntimeOrchestrator` has no `self._session_id`, add `self._session_id: str | None = None` to `__init__`, set it to `lifecycle.session_id` in `start_authenticated_session`, and reset it to `None` in `end_session`.

Add the two new methods:

```python
    def check_scheduled_anchor(self, now: datetime | None = None) -> PendingChallenge | None:
        """Open an A3 prompt when one is due, or return None.

        Called from the heartbeat path so it runs on the existing periodic
        tick rather than adding a timer. Any failure is swallowed into an
        availability signal: a missing verification prompt must never take
        down ingestion (ADR-011, fail-open).
        """

        if self.anchor_scheduler is None or self.challenge_service is None:
            return None
        if self._active_user is None or self._session_id is None:
            return None
        segment_id = next(iter(self._builders), None)
        if segment_id is None:
            return None
        instant = now or datetime.now(UTC)
        if not self.anchor_scheduler.due(session_id=self._session_id, now=instant):
            return None
        try:
            return self.challenge_service.open_scheduled(
                user_id=self._active_user,
                session_id=self._session_id,
                segment_id=segment_id,
            )
        except ChallengeError:
            self._on_ingestion_availability(
                AvailabilityEvent(
                    code="SCHEDULED_ANCHOR_UNAVAILABLE",
                    component="verification",
                    detail="scheduled verification prompt could not be opened",
                )
            )
            return None

    def complete_scheduled_anchor(
        self,
        *,
        decision_id: str,
        session_id: str,
        segment_id: str,
        evidence_reference: str,
        at: datetime | None = None,
    ) -> None:
        """Record an A3 anchor for a correctly answered scheduled prompt."""

        del decision_id
        if self._active_user is None:
            return
        instant = at or datetime.now(UTC)
        verification = self.enforcement.record_scheduled_verification(
            user_id=self._active_user,
            session_id=session_id,
            segment_id=segment_id,
            result=AdapterResult(
                ActionStatus.SUCCEEDED,
                "SCHEDULED_VERIFICATION_ACCEPTED",
                evidence_reference,
            ),
            authenticated_at=instant,
        )
        if verification is not None and self.anchor_scheduler is not None:
            self.anchor_scheduler.anchor_recorded(session_id=session_id, at=instant)
```

In `check_heartbeat`, immediately after the early-return guard that handles a healthy heartbeat, call `self.check_scheduled_anchor()` and ignore the return value — the pending challenge is read by the API from `ChallengeService.pending()`.

- [ ] **Step 4: Construct the scheduler in the application factory**

In `backend/app/runtime/application.py`, after `update_manager` is built, add:

```python
    update_settings = load_update_settings(updates_config)
    anchor_scheduler = ScheduledAnchorScheduler(
        update_settings.update_manager.scheduled_anchor_interval_seconds
    )
```

Change the existing `UpdateManager(...)` construction to reuse `update_settings` instead of calling `load_update_settings(updates_config)` a second time, and pass the two new arguments to `RuntimeOrchestrator`:

```python
        anchor_scheduler=anchor_scheduler,
        challenge_service=challenge_service,
```

Add the import: `from backend.app.updates.anchors import ScheduledAnchorScheduler`.

- [ ] **Step 5: Run the runtime suites**

Run: `python -m pytest backend/tests/test_scheduled_anchors.py backend/tests/test_runtime_integration.py backend/tests/test_runtime_service.py -v`
Expected: PASS. The pre-existing runtime tests must still pass — the new parameters default to `None`, so an orchestrator built without them behaves exactly as before.

- [ ] **Step 6: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add backend/app/runtime/orchestrator.py backend/app/runtime/application.py backend/tests/test_scheduled_anchors.py
git commit -m "feat: dispatch A3 scheduled verification prompts from the runtime"
```

---

### Task 5: `config/collection.pilot.yaml`

**Files:**
- Create: `config/collection.pilot.yaml`
- Test: `tools/collection/tests/test_collection.py`

**Interfaces:**
- Consumes: `load_collection_settings` from `tools/collection/config.py` (existing).
- Produces: a config file. No code interface.

- [ ] **Step 1: Write the failing test**

Append to `tools/collection/tests/test_collection.py`:

```python
def test_pilot_collection_profile_matches_the_planned_round() -> None:
    from pathlib import Path

    from protocol.generated.python.contracts import DataProvenance
    from tools.collection.config import load_collection_settings

    root = Path(__file__).resolve().parents[3]
    settings = load_collection_settings(root / "config/collection.pilot.yaml")
    assert settings.target_collection_days == 5
    assert settings.eligible_provenance == frozenset({DataProvenance.PILOT})
    assert settings.min_distinct_days >= 3
    assert settings.scheduled_anchor_interval_hours == 4.0


def test_five_days_partition_into_three_non_empty_splits() -> None:
    """A 5-day round must still yield day-disjoint TRAIN/VALIDATION/EVALUATION."""

    from pathlib import Path

    from tools.collection.config import load_collection_settings
    from tools.collection.freeze import _partition_days

    root = Path(__file__).resolve().parents[3]
    settings = load_collection_settings(root / "config/collection.pilot.yaml")
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"]
    assignments = _partition_days(days, settings)
    assert set(assignments.values()) == {"TRAIN", "VALIDATION", "EVALUATION"}


def test_development_collection_profile_is_unchanged() -> None:
    """Requirement: existing development configuration is not modified."""

    from pathlib import Path

    from protocol.generated.python.contracts import DataProvenance
    from tools.collection.config import load_collection_settings

    root = Path(__file__).resolve().parents[3]
    settings = load_collection_settings(root / "config/collection.development.yaml")
    assert settings.target_collection_days == 7
    assert settings.eligible_provenance == frozenset(
        {DataProvenance.TEAM, DataProvenance.PILOT}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools/collection/tests/test_collection.py -k pilot_collection_profile -v`
Expected: FAIL — `CollectionConfigError: collection config rejected: ... No such file or directory`

- [ ] **Step 3: Create the config**

Create `config/collection.pilot.yaml`:

```yaml
# Collection targets for the current pilot round.
#
# `collection.development.yaml` is deliberately left untouched and still
# describes the 7-day team/development round. This file describes the planned
# 5-day participant round and nothing else.
#
# These remain engineering placeholders, not approved pilot-protocol values.
# Human reviewers must confirm them against the approved protocol before
# participant collection begins; `config_version` says so.
config_version: collection-pilot-unreviewed-v1
protocol_version: 1.0.0

collection:
  # The planned round is 5 days. Leaving this at 7 would make
  # COLLECTION_DAYS_BELOW_TARGET fire for every participant on every health
  # report, which would train the operator to ignore the shortfall signal.
  target_collection_days: 5
  min_windows_per_day: 50
  min_full_modality_fraction: 0.40
  max_observed_gap_hours: 24.0
  # A3 cadence. Must equal update_manager.scheduled_anchor_interval_seconds
  # in config/updates.development.yaml (14400s); a test asserts this.
  scheduled_anchor_interval_hours: 4.0
  # This round collects from pilot participants only. The development profile
  # keeps [TEAM, PILOT]. Synthetic and public are structurally forbidden here
  # by _CollectionValues.participant_provenance_only.
  eligible_provenance: [PILOT]

freeze:
  # Five days partitions to a valid day-disjoint 2/2/1 split under
  # freeze._partition_days, which reserves one whole day per partition first.
  min_distinct_days: 3
  training_fraction: 0.60
  validation_fraction: 0.20
  evaluation_fraction: 0.20
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tools/collection/tests/test_collection.py backend/tests/test_scheduled_anchors.py -v`
Expected: PASS, including `test_collection_and_runtime_cadences_agree` from Task 4.

- [ ] **Step 5: Commit**

```bash
python tools/guardrails/check.py
git add config/collection.pilot.yaml tools/collection/tests/test_collection.py
git commit -m "feat: add pilot collection profile for the 5-day round"
```

---

### Task 6: Scheduled-challenge responses record A3 anchors

**Files:**
- Modify: `backend/app/api/backend.py`
- Test: `backend/tests/test_challenge_api.py`

**Interfaces:**
- Consumes: `RuntimeOrchestrator.complete_scheduled_anchor` (Task 4); `ChallengeService.respond` and `ResponseOutcome` (existing).
- Produces: `SQLiteApiBackend.__init__` gains keyword-only `scheduled_anchor_sink: Callable[..., None] | None = None`, called with `decision_id`, `session_id`, `segment_id`, `evidence_reference` when a `scheduled-anchor:` challenge is answered correctly.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_challenge_api.py`:

```python
def test_correct_scheduled_response_records_an_anchor(api_backend_with_sink) -> None:
    backend, service, recorded = api_backend_with_sink
    pending = service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    backend.respond_to_challenge(
        pending.decision_id, "correct-horse", response_token=pending.response_token
    )
    assert len(recorded) == 1
    assert recorded[0]["session_id"] == "s1"
    assert recorded[0]["segment_id"] == "seg1"
    assert recorded[0]["evidence_reference"]


def test_wrong_scheduled_response_records_no_anchor(api_backend_with_sink) -> None:
    """A wrong answer must never become a verification anchor."""

    backend, service, recorded = api_backend_with_sink
    pending = service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    backend.respond_to_challenge(
        pending.decision_id, "wrong-answer", response_token=pending.response_token
    )
    assert recorded == []


def test_ordinary_challenge_response_does_not_use_the_scheduled_sink(
    api_backend_with_sink, sample_decision
) -> None:
    """A2 anchors continue to flow through EnforcementCoordinator, not here."""

    backend, service, recorded = api_backend_with_sink
    pending = service.open(sample_decision)
    backend.respond_to_challenge(
        pending.decision_id, "correct-horse", response_token=pending.response_token
    )
    assert recorded == []
```

Add the fixture in the same file:

```python
@pytest.fixture
def api_backend_with_sink(...):
    """Build SQLiteApiBackend with a recording scheduled-anchor sink.

    Mirror the existing challenge-API fixture in this file for storage and
    service construction; the only addition is the sink below.
    """

    recorded: list[dict[str, str]] = []

    def sink(**kwargs: str) -> None:
        recorded.append(kwargs)

    # ... build storage, challenge service, enforcement as the existing
    # fixture in this file does, then:
    backend = SQLiteApiBackend(
        storage,
        active_user_provider=lambda: "participant-01",
        challenge_service=service,
        enforcement=enforcement,
        enforcement_settings=settings,
        scheduled_anchor_sink=sink,
    )
    return backend, service, recorded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_challenge_api.py -k scheduled -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'scheduled_anchor_sink'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/api/backend.py`, add the keyword-only parameter to `SQLiteApiBackend.__init__`:

```python
        scheduled_anchor_sink: Callable[..., None] | None = None,
```

store it as `self._scheduled_anchor_sink = scheduled_anchor_sink`, and in `respond_to_challenge`, after the existing response handling produces an accepted result, add:

```python
        if (
            result.outcome is ResponseOutcome.ACCEPTED
            and result.challenge.decision_id.startswith("scheduled-anchor:")
            and self._scheduled_anchor_sink is not None
            and result.evidence_reference is not None
        ):
            # A3 only. An ordinary SOFT_CHALLENGE/REAUTH response already
            # produces its anchor through EnforcementCoordinator; routing it
            # here as well would double-record the evidence.
            self._scheduled_anchor_sink(
                decision_id=result.challenge.decision_id,
                session_id=result.challenge.session_id,
                segment_id=result.challenge.segment_id,
                evidence_reference=result.evidence_reference,
            )
```

Ensure `Callable` is imported from `collections.abc` and `ResponseOutcome` from `backend.app.decisions.challenge`.

In `backend/app/runtime/application.py`, pass the sink when constructing the backend:

```python
        scheduled_anchor_sink=orchestrator.complete_scheduled_anchor,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_challenge_api.py backend/tests/test_challenge.py -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add backend/app/api/backend.py backend/app/runtime/application.py backend/tests/test_challenge_api.py
git commit -m "feat: record A3 anchors from scheduled challenge responses"
```

---

### Task 7: Operator-visible provenance indicator

An operator currently cannot confirm before day 1 that a run records `PILOT` rather than `SYNTHETIC`.

**Files:**
- Modify: `protocol/schemas/api.openapi.yaml`
- Modify: `backend/app/api/backend.py`
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/test_provenance_flow.py`

**Interfaces:**
- Consumes: `StorageSettings.environment`, `.data_policy`, `.collection_provenance` (existing).
- Produces: `GET /v1/collection/provenance` returning `{"environment": str, "data_policy": str, "collection_provenance": str, "config_version": str}`. `SQLiteApiBackend.collection_provenance(self) -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_provenance_flow.py`:

```python
def test_provenance_endpoint_reports_the_active_store(tmp_path: Path) -> None:
    """An operator must be able to confirm what a run is recording."""

    from backend.app.api.backend import SQLiteApiBackend
    from backend.app.storage.service import StorageService

    settings = _settings(PILOT_CONFIG, tmp_path)
    backend = SQLiteApiBackend(
        StorageService.open(settings), active_user_provider=lambda: None
    )
    reported = backend.collection_provenance()
    assert reported["environment"] == "PILOT"
    assert reported["data_policy"] == "APPROVED_COLLECTION"
    assert reported["collection_provenance"] == "PILOT"
    assert reported["config_version"] == settings.config_version


def test_provenance_endpoint_reports_synthetic_for_development(tmp_path: Path) -> None:
    from backend.app.api.backend import SQLiteApiBackend
    from backend.app.storage.service import StorageService

    settings = _settings(DEVELOPMENT_CONFIG, tmp_path)
    backend = SQLiteApiBackend(
        StorageService.open(settings), active_user_provider=lambda: None
    )
    assert backend.collection_provenance()["collection_provenance"] == "SYNTHETIC"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_provenance_flow.py -k provenance_endpoint -v`
Expected: FAIL — `AttributeError: 'SQLiteApiBackend' object has no attribute 'collection_provenance'`

- [ ] **Step 3: Update the contract first**

Per AGENTS.md, the shared interface changes before the implementation. In `protocol/schemas/api.openapi.yaml`, add under `paths:`:

```yaml
  /v1/collection/provenance:
    get:
      summary: Report the storage environment and derived collection provenance
      operationId: getCollectionProvenance
      responses:
        "200":
          description: Active storage profile identity
          content:
            application/json:
              schema:
                type: object
                additionalProperties: false
                required: [environment, data_policy, collection_provenance, config_version]
                properties:
                  environment:
                    type: string
                    enum: [DEVELOPMENT, PILOT, EVALUATION]
                  data_policy:
                    type: string
                    enum: [SYNTHETIC_ONLY, APPROVED_COLLECTION]
                  collection_provenance:
                    type: string
                    enum: [SYNTHETIC, PUBLIC, TEAM, PILOT]
                  config_version:
                    type: string
        "401":
          description: Unauthenticated
```

Then run the generator and commit the regenerated bindings in the same change:

```bash
python protocol/codegen/generate.py
```

- [ ] **Step 4: Write the implementation**

In `backend/app/api/backend.py`, add to `SQLiteApiBackend`:

```python
    def collection_provenance(self) -> dict[str, str]:
        """Report what this run records, derived from the storage profile.

        Read directly from StorageSettings so the reported value cannot
        disagree with what is actually written to disk.
        """

        settings = self.storage.settings
        return {
            "environment": settings.environment.value,
            "data_policy": settings.data_policy.value,
            "collection_provenance": settings.collection_provenance.value,
            "config_version": settings.config_version,
        }
```

In `backend/app/api/routes.py`, alongside the existing `/v1/enforcement/status` route:

```python
    @app.get("/v1/collection/provenance")
    def collection_provenance(
        ca_session: str | None = Cookie(default=None),
    ) -> dict[str, str]:
        if auth.authenticate(ca_session) is None:
            raise HTTPException(status_code=401)
        return backend.collection_provenance()
```

Match the exact authentication idiom used by the neighbouring routes in that file rather than the sketch above if they differ.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_provenance_flow.py protocol/tests/test_contracts.py backend/tests/test_api_stream.py -v`
Expected: PASS

- [ ] **Step 6: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add protocol/schemas/api.openapi.yaml protocol/generated backend/app/api/backend.py backend/app/api/routes.py backend/tests/test_provenance_flow.py
git commit -m "feat: expose active collection provenance to the operator"
```

---

### Task 8: Dashboard surfaces provenance and pending scheduled verification

**Files:**
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/challenge.ts`
- Test: `dashboard/tests/challenge.test.ts`

**Interfaces:**
- Consumes: `GET /v1/collection/provenance` (Task 7); the existing challenge status endpoint.
- Produces: `loadCollectionProvenance(): Promise<CollectionProvenance>` in `client.ts`; `CollectionProvenance` type `{ environment: string; data_policy: string; collection_provenance: string; config_version: string }` in `challenge.ts`.

- [ ] **Step 1: Write the failing test**

Append to `dashboard/tests/challenge.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import type { CollectionProvenance } from "../src/challenge";

describe("collection provenance", () => {
  it("marks an approved-collection store as recording real data", () => {
    const provenance: CollectionProvenance = {
      environment: "PILOT",
      data_policy: "APPROVED_COLLECTION",
      collection_provenance: "PILOT",
      config_version: "storage-pilot-1",
    };
    expect(provenance.collection_provenance).toBe("PILOT");
    expect(provenance.data_policy).toBe("APPROVED_COLLECTION");
  });

  it("distinguishes a synthetic development store", () => {
    const provenance: CollectionProvenance = {
      environment: "DEVELOPMENT",
      data_policy: "SYNTHETIC_ONLY",
      collection_provenance: "SYNTHETIC",
      config_version: "storage-development-1",
    };
    expect(provenance.collection_provenance).toBe("SYNTHETIC");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- challenge.test.ts`
Expected: FAIL — no exported member `CollectionProvenance`.

- [ ] **Step 3: Write the implementation**

In `dashboard/src/challenge.ts`, add:

```typescript
export type CollectionProvenance = {
  readonly environment: string;
  readonly data_policy: string;
  readonly collection_provenance: string;
  readonly config_version: string;
};
```

In `dashboard/src/api/client.ts`, add a loader matching the existing `loadChallengeStatus` idiom in that file:

```typescript
export async function loadCollectionProvenance(): Promise<CollectionProvenance> {
  return request<CollectionProvenance>("/v1/collection/provenance");
}
```

In `dashboard/src/App.tsx`, load it once on mount alongside the existing challenge/enforcement loads, and render a persistent header badge:

```tsx
{provenance && (
  <span
    className={
      provenance.collection_provenance === "PILOT"
        ? "provenance-badge provenance-badge--pilot"
        : "provenance-badge provenance-badge--synthetic"
    }
  >
    Recording: {provenance.collection_provenance}
  </span>
)}
```

Add the two classes to `dashboard/src/styles.css`, following the visual conventions already used by `ProtectionBanner`. The badge must be visible on every view, not only Settings — its purpose is that an operator cannot miss it.

- [ ] **Step 4: Run tests and typecheck**

Run: `cd dashboard && npm test && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src dashboard/tests
git commit -m "feat: show active collection provenance in the dashboard"
```

---

### Task 9: Surface the scheduled verification prompt in the dashboard

**Files:**
- Modify: `dashboard/src/views/ChallengeForm.tsx`
- Modify: `dashboard/src/App.tsx`
- Test: `dashboard/tests/challenge.test.ts`

**Interfaces:**
- Consumes: the existing challenge status endpoint, which reports the active pending challenge including `scheduled-anchor:` ids.
- Produces: no new module interface. `ChallengeForm` distinguishes a scheduled prompt from an enforcement challenge in its copy.

- [ ] **Step 1: Write the failing test**

Append to `dashboard/tests/challenge.test.ts`:

```typescript
import { isScheduledVerification } from "../src/challenge";

describe("scheduled verification", () => {
  it("recognises a scheduled anchor prompt by its decision id", () => {
    expect(isScheduledVerification("scheduled-anchor:abc123")).toBe(true);
  });

  it("does not mistake an enforcement challenge for one", () => {
    expect(isScheduledVerification("decision-9f2c")).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- challenge.test.ts`
Expected: FAIL — no exported member `isScheduledVerification`.

- [ ] **Step 3: Write the implementation**

In `dashboard/src/challenge.ts`:

```typescript
const SCHEDULED_PREFIX = "scheduled-anchor:";

/** A scheduled verification is routine, not a risk response. The dashboard
 *  says so, so a participant is not alarmed by a periodic prompt. */
export function isScheduledVerification(decisionId: string): boolean {
  return decisionId.startsWith(SCHEDULED_PREFIX);
}
```

In `ChallengeForm.tsx`, branch the heading and helper text on `isScheduledVerification(challenge.decision_id)`:

- Scheduled: "Routine verification" / "A periodic check that confirms it is you. Nothing is wrong."
- Enforcement: keep the existing wording unchanged.

- [ ] **Step 4: Run tests and typecheck**

Run: `cd dashboard && npm test && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/src dashboard/tests
git commit -m "feat: distinguish routine A3 verification in the dashboard prompt"
```

---

### Task 10: Documentation

No code. Every edit keeps the synthetic path documented as a supported explicit option.

**Files:**
- Modify: `backend/README.md`, `docs/deployment/windows.md`, `startup.md`, `docs/architecture.md`, `docs/demo.md`, `docs/pilot/README.md`, `docs/pilot/operator-checklist.md`, `backend/app/runtime/cli.py` (docstring only)

**Interfaces:** none.

- [ ] **Step 1: Update `backend/README.md`**

Replace the synthetic-development walkthrough with the pilot default. The invocation becomes:

```powershell
python -m backend.app.runtime.cli --participant-id <pseudonym> `
  --artifact-root "$env:LOCALAPPDATA/ContinuousAuthentication/Pilot/models"
```

State that this records real `PILOT` data via the default `config/storage.pilot.yaml`, and that adding `--storage-config config/storage.development.yaml` records disposable `SYNTHETIC` data instead.

- [ ] **Step 2: Update `docs/deployment/windows.md`**

Replace `-SyntheticUser synthetic-user` with `-ParticipantId <pseudonym>`. Replace the claim that the deployment "stores `SYNTHETIC` provenance" and "must not be used for participant collection" with an accurate statement: the default profile records `PILOT` participant data, and `-StorageProfile storage.development.yaml` selects the synthetic profile.

- [ ] **Step 3: Update `startup.md`**

Correct the four synthetic-assuming passages: the `runtime-data` volume description, the "supplied configuration accepts synthetic development" claim, the `--synthetic-user` invocation, and the synthetic-session narrative. Keep the Docker section's note that the container path is dashboard/API only and still uses the development storage profile.

- [ ] **Step 4: Update `docs/architecture.md`**

Replace "Development launchers accept synthetic provenance only; pilot operation requires separately reviewed consent, storage, and collection configuration" with a statement that the default launcher records `PILOT` against a reviewed storage and collection profile, that provenance is derived solely from the storage profile, and that runtime tunables remain unreviewed development placeholders (spec Section 7.3).

- [ ] **Step 5: Update `docs/pilot/README.md`**

Point the health and freeze examples at `config/collection.pilot.yaml`. Add one paragraph recording that runtime tunables in `api`, `risk`, `context`, `orchestration`, `updates`, and `enforcement` configs remain unreviewed development placeholders while the collected data is real `PILOT` data governed by reviewed storage and collection profiles.

- [ ] **Step 6: Update `docs/pilot/operator-checklist.md`**

Replace "Record completed A3 prompts only after an independent successful verification" with review of automated anchors: "Confirm scheduled verification prompts are being answered; the system records A3 anchors automatically and operators do not create them by hand." Keep every other line, including "Never substitute continuous low risk for consent or a verification anchor."

- [ ] **Step 7: Update `docs/demo.md` and the CLI docstring**

`docs/demo.md`: "Start the packaged synthetic runtime and collector" becomes accurate about which profile the demo uses. `backend/app/runtime/cli.py` module docstring: "Run the complete Windows synthetic-development stack." becomes "Run the complete Windows collection stack."

- [ ] **Step 8: Verify and commit**

```bash
grep -rn "SyntheticUser\|--synthetic-user" docs/ backend/README.md startup.md
```

Expected: only the compatibility-alias mention in `backend/app/runtime/cli.py` and `backend/tests/test_provenance_flow.py`.

```bash
python tools/guardrails/check.py
git add docs backend/README.md startup.md backend/app/runtime/cli.py
git commit -m "docs: describe PILOT as the default collection workflow"
```

---

### Task 11: Full-suite verification

**Files:** none modified.

- [ ] **Step 1: Run every affected suite**

```bash
python -m pytest backend/tests ml/tests tools protocol/tests -q
```

Expected: PASS. Investigate any failure; do not proceed with failures outstanding.

- [ ] **Step 2: Run the guardrail check**

```bash
python tools/guardrails/check.py
```

Expected: no findings.

- [ ] **Step 3: Run the dashboard suite and typecheck**

```bash
cd dashboard && npm test && npx tsc --noEmit && npm run build
```

Expected: PASS.

- [ ] **Step 4: Confirm the synthetic path still works end to end**

```bash
python -m pytest backend/tests/test_provenance_flow.py tools/synthetic -v
```

Expected: PASS. `config/storage.development.yaml` still yields `SYNTHETIC`, and the two stores still refuse each other's data.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "test: verify pilot collection readiness end to end"
```
