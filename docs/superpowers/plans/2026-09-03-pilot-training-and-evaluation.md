# PILOT Training and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a frozen `PILOT` corpus legitimately reach model training, first-profile activation, scheduled updates, and the E1/E2 experiments, without weakening any existing gate.

**Architecture:** PLAN.md Section 12 is an *update* gate that presupposes an active profile — G3 counts scored windows and scoring requires a model, so no first profile can satisfy it. ADR-013 (approved 2026-09-03) resolves this by adding a distinct enrollment admission gate for a user's first profile only. `require_promotion_gate` is untouched and continues to govern every subsequent update.

**Tech Stack:** Python 3.12, Pydantic v2, scikit-learn, NumPy, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-pilot-default-workflow-design.md` (Sections 5, 6, 8). Read Section 5 in full before starting — the reasoning matters more than the code.

**Prerequisite:** `docs/superpowers/plans/2026-09-03-pilot-collection-readiness.md` is complete and a frozen corpus exists at `data/frozen/<version>/manifest.json`.

## Global Constraints

- `ml/training/gate.py::require_promotion_gate` is not modified. Not one line. A test asserts this.
- G1–G6 semantics, `quarantine_days: 7`, `retraining_cadence_days: 7`, `regression_tolerance`, `min_promotable_windows` unchanged.
- `tools/demo/bootstrap_first_model.py` is left in place, unchanged.
- The `development_only: Literal[True]` loader constraint stays.
- Windows whose provenance is entirely `SYNTHETIC` or `PUBLIC` require **neither** admission boundary. Existing synthetic and public paths behave exactly as today.
- For any `TEAM`/`PILOT` window: **exactly one** of `promoted_candidates` or `enrollment_admission`. Neither raises `PromotionGateRequiredError`; both raises `ValueError`. There is no fallback — absence of `promoted_candidates` never implies enrollment mode.
- The `EVALUATION` partition is never read during training or first-profile activation.
- Synthetic fixtures only. Real participant records are never test fixtures.
- Run `python tools/guardrails/check.py` before each commit.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `ml/training/enrollment.py` (create) | The enrollment admission gate. Pure validation, no I/O. |
| `ml/training/common.py` (modify) | Require exactly one admission boundary. |
| `ml/training/isolation_forest.py`, `single_fused_model.py`, `ml/baselines/*.py` (modify) | Forward the new keyword. |
| `tools/collection/corpus.py` (create) | Manifest-verified corpus loading. |
| `ml/evaluation/pipeline.py` (modify) | Thread admission to trainers. |
| `tools/enrollment/` (create) | First-profile activation CLI. |
| `tools/updates/` (create) | Scheduled update run driver and candidate builder. |
| `tools/guardrails/check.py` (modify) | G07 accepts either boundary; both must remain present. |
| `PLAN.md` (modify) | Insert approved ADR-013 and its cross-references. |

---

### Task 1: The enrollment admission gate

**Files:**
- Create: `ml/training/enrollment.py`
- Test: `ml/tests/test_enrollment_gate.py`

**Interfaces:**
- Consumes: `FeatureWindow`, `Provenance` from `ml/features/schema.py`; `ConsentRecord`, `EnrollmentRecord`, `eligibility_reasons` from `tools/collection/eligibility.py`; `CollectionSettings` from `tools/collection/config.py`.
- Produces: `EnrollmentAdmission` (frozen dataclass) and `require_enrollment_admission(user_id: str, windows: Sequence[FeatureWindow], admission: EnrollmentAdmission) -> None`, raising `EnrollmentAdmissionError`.

`EnrollmentAdmission` fields, in order: `settings: CollectionSettings`, `consent: ConsentRecord | None`, `enrollment: EnrollmentRecord | None`, `manifest_window_ids: frozenset[str]`, `observed_at_by_window: Mapping[str, datetime]`, `min_windows: int`, `min_distinct_days: int`, `user_has_active_profile: bool`.

- [ ] **Step 1: Write the failing test**

Create `ml/tests/test_enrollment_gate.py`. Build fixtures with the synthetic window helpers already used in `ml/tests/conftest.py`; if a helper named `make_window` does not exist there, write a local one that constructs a valid `FeatureWindow` with settable `user_id`, `window_id`, `collection_day`, `segment_id`, and `provenance`.

```python
"""ADR-013: a user's first profile is admitted on consent and corpus evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ml.features.schema import Provenance
from ml.training.enrollment import (
    EnrollmentAdmission,
    EnrollmentAdmissionError,
    require_enrollment_admission,
)
from tools.collection.config import load_collection_settings
from tools.collection.eligibility import ConsentRecord, EnrollmentRecord

CONSENTED = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _settings():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return load_collection_settings(root / "config/collection.pilot.yaml")


def _windows(count: int = 30, days: int = 3, user_id: str = "participant-01"):
    from ml.tests.conftest import make_window

    return [
        make_window(
            user_id=user_id,
            window_id=f"w{index}",
            segment_id=f"seg{index % 5}",
            collection_day=f"2026-09-0{1 + index % days}",
            provenance=Provenance.PILOT,
        )
        for index in range(count)
    ]


def _admission(windows, **overrides):
    defaults = dict(
        settings=_settings(),
        consent=ConsentRecord(
            participant_id="participant-01",
            protocol_revision="v1",
            consented_at=CONSENTED,
        ),
        enrollment=EnrollmentRecord(
            participant_id="participant-01",
            enrolled_at=CONSENTED,
            collector_version="1.0.0",
            protocol_version="1.0.0",
        ),
        manifest_window_ids=frozenset(w.window_id for w in windows),
        observed_at_by_window={
            w.window_id: CONSENTED + timedelta(hours=index)
            for index, w in enumerate(windows)
        },
        min_windows=20,
        min_distinct_days=3,
        user_has_active_profile=False,
    )
    defaults.update(overrides)
    return EnrollmentAdmission(**defaults)


def test_complete_evidence_is_admitted() -> None:
    windows = _windows()
    require_enrollment_admission("participant-01", windows, _admission(windows))


def test_a_window_from_another_user_is_refused() -> None:
    windows = _windows() + _windows(count=1, user_id="participant-02")
    with pytest.raises(EnrollmentAdmissionError, match="FOREIGN_USER_WINDOW"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_synthetic_provenance_is_refused() -> None:
    from ml.tests.conftest import make_window

    windows = _windows()
    windows.append(
        make_window(
            user_id="participant-01",
            window_id="w-synth",
            segment_id="seg0",
            collection_day="2026-09-01",
            provenance=Provenance.SYNTHETIC,
        )
    )
    with pytest.raises(EnrollmentAdmissionError, match="INELIGIBLE_PROVENANCE"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_missing_consent_is_refused() -> None:
    windows = _windows()
    with pytest.raises(EnrollmentAdmissionError, match="MISSING_CONSENT"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, consent=None)
        )


def test_withdrawn_consent_is_refused() -> None:
    windows = _windows()
    withdrawn = ConsentRecord(
        participant_id="participant-01",
        protocol_revision="v1",
        consented_at=CONSENTED,
        withdrawn_at=CONSENTED + timedelta(minutes=1),
    )
    with pytest.raises(EnrollmentAdmissionError, match="CONSENT_NOT_ACTIVE"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, consent=withdrawn)
        )


def test_missing_enrollment_is_refused() -> None:
    windows = _windows()
    with pytest.raises(EnrollmentAdmissionError, match="MISSING_ENROLLMENT"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, enrollment=None)
        )


def test_a_window_outside_the_frozen_corpus_is_refused() -> None:
    windows = _windows()
    partial = frozenset(w.window_id for w in windows[:-1])
    with pytest.raises(EnrollmentAdmissionError, match="WINDOW_NOT_IN_FROZEN_CORPUS"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, manifest_window_ids=partial)
        )


def test_a_window_without_an_observation_time_is_refused() -> None:
    windows = _windows()
    partial = {w.window_id: CONSENTED for w in windows[:-1]}
    with pytest.raises(EnrollmentAdmissionError, match="MISSING_OBSERVATION_TIME"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, observed_at_by_window=partial)
        )


def test_too_few_windows_is_refused() -> None:
    windows = _windows(count=5)
    with pytest.raises(EnrollmentAdmissionError, match="INSUFFICIENT_WINDOWS"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_too_few_distinct_days_is_refused() -> None:
    windows = _windows(days=1)
    with pytest.raises(EnrollmentAdmissionError, match="INSUFFICIENT_DISTINCT_DAYS"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_a_user_with_an_active_profile_is_refused() -> None:
    """Once a profile exists, only the Model Update Manager may add data."""

    windows = _windows()
    with pytest.raises(EnrollmentAdmissionError, match="PROFILE_ALREADY_ACTIVE"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, user_has_active_profile=True)
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ml/tests/test_enrollment_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.training.enrollment'`

- [ ] **Step 3: Write minimal implementation**

Create `ml/training/enrollment.py`:

```python
"""First-profile training-data admission boundary (ADR-013).

PLAN.md Section 12 is an *update* gate: it admits new data into the training
set of a profile that already exists. Gate G3 counts scored windows, and
scoring requires an active model, so no first profile can ever satisfy it.
PLAN.md Section 5.3 treats the first model as an enrollment transition, not a
promotion.

This module is that enrollment boundary. It is not weaker than the promotion
gate, it is evidence of a different kind: consent, enrollment, and corpus
integrity rather than risk-and-verification evidence that cannot exist before
a model does. It applies to a user's FIRST profile only -- once a profile is
active this gate refuses, and `ml.training.gate.require_promotion_gate` is the
only remaining route.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ml.features.schema import FeatureWindow, Provenance
from tools.collection.config import CollectionSettings
from tools.collection.eligibility import (
    ConsentRecord,
    EnrollmentRecord,
    eligibility_reasons,
)

_PROVENANCE_BY_NAME = {item.value: item for item in Provenance}


class EnrollmentAdmissionError(ValueError):
    """Raised when first-profile training data lacks complete admission evidence."""


@dataclass(frozen=True)
class EnrollmentAdmission:
    """Every piece of evidence required to admit a user's first profile."""

    settings: CollectionSettings
    consent: ConsentRecord | None
    enrollment: EnrollmentRecord | None
    manifest_window_ids: frozenset[str]
    observed_at_by_window: Mapping[str, datetime]
    min_windows: int
    min_distinct_days: int
    user_has_active_profile: bool


def require_enrollment_admission(
    user_id: str,
    windows: Sequence[FeatureWindow],
    admission: EnrollmentAdmission,
) -> None:
    """Validate that a user's first profile may be trained on these windows."""

    if admission.user_has_active_profile:
        raise EnrollmentAdmissionError(
            f"PROFILE_ALREADY_ACTIVE: user {user_id!r} has an active profile; "
            "further training data must pass the Model Update Manager promotion gate"
        )

    for window in windows:
        if window.user_id != user_id:
            raise EnrollmentAdmissionError(
                f"FOREIGN_USER_WINDOW: window {window.window_id!r} belongs to another user"
            )
        if window.window_id not in admission.manifest_window_ids:
            raise EnrollmentAdmissionError(
                f"WINDOW_NOT_IN_FROZEN_CORPUS: window {window.window_id!r} is not "
                "named in the verified freeze manifest"
            )
        observed_at = admission.observed_at_by_window.get(window.window_id)
        if observed_at is None:
            raise EnrollmentAdmissionError(
                f"MISSING_OBSERVATION_TIME: window {window.window_id!r} has no "
                "recorded observation time; it is never defaulted"
            )
        reasons = eligibility_reasons(
            participant_id=user_id,
            provenance=_PROVENANCE_BY_NAME[window.provenance.value],
            observed_at=observed_at,
            consent=admission.consent,
            enrollment=admission.enrollment,
            settings=admission.settings,
        )
        if reasons:
            raise EnrollmentAdmissionError(
                f"{','.join(reasons)}: window {window.window_id!r} is not "
                "evaluation-eligible"
            )

    if len(windows) < admission.min_windows:
        raise EnrollmentAdmissionError(
            f"INSUFFICIENT_WINDOWS: {len(windows)} < {admission.min_windows}"
        )
    distinct_days = {window.collection_day for window in windows}
    if len(distinct_days) < admission.min_distinct_days:
        raise EnrollmentAdmissionError(
            f"INSUFFICIENT_DISTINCT_DAYS: {len(distinct_days)} < "
            f"{admission.min_distinct_days}"
        )
```

Note on the provenance lookup: `ml.features.schema.Provenance` and `protocol...DataProvenance` are distinct enums with matching values. `_PROVENANCE_BY_NAME` maps by value so `eligibility_reasons` receives the enum it expects. If the two are already the same type in this codebase, drop the mapping and pass `window.provenance` directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest ml/tests/test_enrollment_gate.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add ml/training/enrollment.py ml/tests/test_enrollment_gate.py
git commit -m "feat: add first-profile enrollment admission gate (ADR-013)"
```

---

### Task 2: Require exactly one admission boundary

**Files:**
- Modify: `ml/training/common.py`
- Modify: `ml/training/isolation_forest.py`, `ml/training/single_fused_model.py`, `ml/baselines/mahalanobis.py`, `ml/baselines/alt_one_class.py`
- Test: `ml/tests/test_training.py`

**Interfaces:**
- Consumes: `require_enrollment_admission`, `EnrollmentAdmission` (Task 1); `require_promotion_gate` (existing, unmodified).
- Produces: every trainer gains keyword-only `enrollment_admission: EnrollmentAdmission | None = None`, positioned after `promoted_candidates`.

- [ ] **Step 1: Write the failing test**

Append to `ml/tests/test_training.py`:

```python
def test_pilot_windows_with_neither_boundary_still_raise() -> None:
    """The existing failure mode is preserved exactly."""

    from ml.training.gate import PromotionGateRequiredError
    from ml.training.isolation_forest import train_user_modality_isolation_forest

    windows = _pilot_windows()
    with pytest.raises(PromotionGateRequiredError):
        train_user_modality_isolation_forest(
            "participant-01", "keyboard", windows, _ml_config()
        )


def test_pilot_windows_with_both_boundaries_are_rejected() -> None:
    """An ambiguous admission route is how a boundary silently becomes optional."""

    from ml.training.isolation_forest import train_user_modality_isolation_forest

    windows = _pilot_windows()
    with pytest.raises(ValueError, match="exactly one"):
        train_user_modality_isolation_forest(
            "participant-01",
            "keyboard",
            windows,
            _ml_config(),
            promoted_candidates={},
            enrollment_admission=_valid_admission(windows),
        )


def test_pilot_windows_train_with_enrollment_admission() -> None:
    from ml.training.isolation_forest import train_user_modality_isolation_forest

    windows = _pilot_windows()
    artifact = train_user_modality_isolation_forest(
        "participant-01",
        "keyboard",
        windows,
        _ml_config(),
        enrollment_admission=_valid_admission(windows),
    )
    assert artifact.user_id == "participant-01"


def test_synthetic_windows_still_need_no_boundary() -> None:
    """Requirement: existing synthetic development paths are unaffected."""

    from ml.training.isolation_forest import train_user_modality_isolation_forest

    artifact = train_user_modality_isolation_forest(
        "synthetic-user", "keyboard", _synthetic_windows(), _ml_config()
    )
    assert artifact.user_id == "synthetic-user"
```

Define `_pilot_windows()`, `_synthetic_windows()`, `_ml_config()`, and `_valid_admission()` as module-level helpers in that test file, reusing the `_admission` construction from `ml/tests/test_enrollment_gate.py` and the ML config loader already used elsewhere in `test_training.py`. Each window set must contain at least `min_baseline_windows` (20) windows with keyboard features present, or training raises `InsufficientDataError` before the gate assertion is reached.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ml/tests/test_training.py -k "boundaries or enrollment_admission" -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'enrollment_admission'`

- [ ] **Step 3: Write minimal implementation**

In `ml/training/common.py`, add the import and replace the single `require_promotion_gate(...)` call:

```python
from ml.training.enrollment import EnrollmentAdmission, require_enrollment_admission
from ml.training.gate import require_promotion_gate
```

Add the parameter to `train_one_class_model`, after `promoted_candidates`:

```python
    enrollment_admission: EnrollmentAdmission | None = None,
```

Replace the existing `require_promotion_gate(user_id, windows, promoted_candidates)` line with:

```python
    _admit_training_data(
        user_id,
        windows,
        promoted_candidates=promoted_candidates,
        enrollment_admission=enrollment_admission,
    )
```

and add the module-level helper:

```python
_DEVELOPMENT_PROVENANCE = frozenset({Provenance.SYNTHETIC, Provenance.PUBLIC})


def _admit_training_data(
    user_id: str,
    windows: Sequence[FeatureWindow],
    *,
    promoted_candidates: Mapping[str, UpdateCandidate] | None,
    enrollment_admission: EnrollmentAdmission | None,
) -> None:
    """Route training data through exactly one reviewed admission boundary.

    Synthetic and public development data needs neither. Any team or pilot
    window requires exactly one: the Model Update Manager promotion gate for
    an existing profile, or the ADR-013 enrollment gate for a first profile.

    There is deliberately no fallback. Absence of `promoted_candidates` never
    implies enrollment mode, because a boundary that can be reached by
    omitting an argument is not a boundary.
    """

    if promoted_candidates is not None and enrollment_admission is not None:
        raise ValueError(
            "training data must pass exactly one admission boundary; "
            "promoted_candidates and enrollment_admission are mutually exclusive"
        )
    if enrollment_admission is not None:
        if all(window.provenance in _DEVELOPMENT_PROVENANCE for window in windows):
            raise ValueError(
                "enrollment admission is for participant data; synthetic and "
                "public windows require no admission boundary"
            )
        require_enrollment_admission(user_id, windows, enrollment_admission)
        return
    require_promotion_gate(user_id, windows, promoted_candidates)
```

Ensure `Provenance` is imported from `ml.features.schema` in `common.py`.

In each of the four forwarding trainers, add the same keyword-only parameter and pass it through to `train_one_class_model`. For `ml/training/isolation_forest.py`, both `train_user_modality_isolation_forest` and `train_user_profile` take and forward it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ml/tests -v`
Expected: PASS, including every pre-existing training and gate test.

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add ml/training ml/baselines ml/tests/test_training.py
git commit -m "feat: require exactly one training admission boundary"
```

---

### Task 3: Guardrail G07 recognises both boundaries

**Files:**
- Modify: `tools/guardrails/check.py`
- Test: `tools/guardrails/tests/test_guardrails.py`

**Interfaces:**
- Produces: `check_training_gate` accepts either boundary name; new `check_admission_boundaries_present` asserts `ml/training/common.py` references both.

- [ ] **Step 1: Write the failing test**

Append to `tools/guardrails/tests/test_guardrails.py`:

```python
def test_training_call_may_cite_the_enrollment_boundary() -> None:
    from tools.guardrails.check import check_training_gate

    findings = check_training_gate(
        "train.py", "require_enrollment_admission(user, windows, admission)\nmodel.fit(X)"
    )
    assert findings == []


def test_training_call_with_no_boundary_is_still_flagged() -> None:
    from tools.guardrails.check import check_training_gate

    findings = check_training_gate("train.py", "model.fit(X)")
    assert len(findings) == 1
    assert findings[0].code == "G07_PROMOTION_GATE"


def test_common_must_reference_both_boundaries() -> None:
    """Neither boundary may be deleted without the guardrail failing."""

    from tools.guardrails.check import check_admission_boundaries_present

    complete = "require_promotion_gate\nrequire_enrollment_admission"
    assert check_admission_boundaries_present("ml/training/common.py", complete) == []

    missing = "require_promotion_gate only"
    findings = check_admission_boundaries_present("ml/training/common.py", missing)
    assert len(findings) == 1
    assert findings[0].code == "G07_ADMISSION_BOUNDARY"


def test_other_files_are_not_required_to_reference_both() -> None:
    from tools.guardrails.check import check_admission_boundaries_present

    assert check_admission_boundaries_present("ml/training/other.py", "nothing") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools/guardrails/tests/test_guardrails.py -k "enrollment or boundaries" -v`
Expected: FAIL — `ImportError: cannot import name 'check_admission_boundaries_present'`

- [ ] **Step 3: Write minimal implementation**

In `tools/guardrails/check.py`, change `check_training_gate`:

```python
def check_training_gate(path: str, text: str) -> list[Finding]:
    training_call = re.search(r"\.(fit|partial_fit)\s*\(|\btrain_model\s*\(", text)
    boundaries = ("require_promotion_gate", "require_enrollment_admission")
    if training_call and not any(name in text for name in boundaries):
        line = text[: training_call.start()].count("\n") + 1
        return _finding(
            "G07_PROMOTION_GATE",
            path,
            "training call lacks a reviewed admission boundary "
            "(require_promotion_gate or require_enrollment_admission)",
            line,
        )
    return []
```

and add:

```python
_ADMISSION_ROUTER = "ml/training/common.py"


def check_admission_boundaries_present(path: str, text: str) -> list[Finding]:
    """Both admission boundaries must remain reachable from the router.

    ADR-013 splits training-data admission into an update path and an
    enrollment path. Deleting either one silently would make the other the
    only route, which is exactly the kind of quiet weakening this guardrail
    exists to catch.
    """

    if not path.replace("\\", "/").endswith(_ADMISSION_ROUTER):
        return []
    missing = [
        name
        for name in ("require_promotion_gate", "require_enrollment_admission")
        if name not in text
    ]
    if missing:
        return _finding(
            "G07_ADMISSION_BOUNDARY",
            path,
            f"training admission router is missing: {', '.join(missing)}",
        )
    return []
```

Register `check_admission_boundaries_present` in the list of checks the module runs over each file, following the existing registration pattern.

- [ ] **Step 4: Run tests and the guardrail itself**

Run: `python -m pytest tools/guardrails -v && python tools/guardrails/check.py`
Expected: PASS, no findings.

- [ ] **Step 5: Commit**

```bash
git add tools/guardrails
git commit -m "feat: guardrail recognises both training admission boundaries"
```

---

### Task 4: Insert approved ADR-013 into PLAN.md

**Files:**
- Modify: `PLAN.md`
- Test: `ml/tests/test_enrollment_gate.py`

**Interfaces:** none.

- [ ] **Step 1: Write the failing test**

Append to `ml/tests/test_enrollment_gate.py`:

```python
def test_adr_013_is_recorded_in_the_plan() -> None:
    """The gate and the decision that authorises it ship together."""

    from pathlib import Path

    plan = (Path(__file__).resolve().parents[2] / "PLAN.md").read_text(encoding="utf-8")
    assert "ADR-013" in plan
    assert "Enrollment Admission Is Distinct From Update Promotion" in plan


def test_promotion_gate_source_is_unmodified() -> None:
    """ADR-013 adds a boundary; it does not relax the existing one."""

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "ml" / "training" / "gate.py"
    ).read_text(encoding="utf-8")
    for gate in ("g1_risk", "g2_verification", "g3_volume", "g4_continuity",
                 "g5_quarantine", "g6_schedule"):
        assert gate in source
    assert "CandidateDisposition.PROMOTED" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ml/tests/test_enrollment_gate.py -k adr_013 -v`
Expected: FAIL — `assert 'ADR-013' in plan`

- [ ] **Step 3: Insert the ADR**

In `PLAN.md`, immediately after ADR-012 and before the `---` that closes Section 6, insert the ADR-013 text verbatim from spec Section 5.4, with `**Status:** Accepted, 2026-09-03.`

Add a cross-reference to Section 5.3, after the existing `[OPEN]` note:

> First-profile training data is admitted by the ADR-013 enrollment gate. Section 12's promotion gate governs every subsequent change to a user's training data.

Add a cross-reference to Section 12.1, after the gate table:

> These gates govern *updates* to an existing profile. G3 counts scored windows, which requires an active model, so a user's first profile is admitted by the ADR-013 enrollment gate instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ml/tests/test_enrollment_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add PLAN.md ml/tests/test_enrollment_gate.py
git commit -m "docs: record approved ADR-013 in PLAN.md"
```

---

### Task 5: Frozen-corpus loader

**Files:**
- Create: `tools/collection/corpus.py`
- Test: `tools/collection/tests/test_corpus.py`

**Interfaces:**
- Consumes: `verify_freeze`, `FreezeError` from `tools/collection/freeze.py`; `load_window_summaries` from `tools/collection/repository.py`; `FeatureWindow` from `ml/features/schema.py`.
- Produces:
  - `FrozenCorpus` (frozen dataclass): `windows_by_user: dict[str, list[FeatureWindow]]`, `manifest_window_ids: frozenset[str]`, `observed_at_by_window: dict[str, datetime]`, `day_assignments: dict[str, dict[str, str]]`.
  - `load_frozen_corpus(database: Path, manifest: Path, partition: str) -> FrozenCorpus`. `partition` is one of `"TRAIN"`, `"VALIDATION"`, `"EVALUATION"`.

- [ ] **Step 1: Write the failing test**

Create `tools/collection/tests/test_corpus.py`. Build a temporary SQLite database with the project's own migrations and insert synthetic `feature_windows` rows, then freeze it with `build_freeze` so the manifest and database genuinely agree. Reuse the database-construction helper already used in `tools/collection/tests/test_collection.py`.

```python
"""A frozen corpus is loaded only through its verified manifest."""

from __future__ import annotations

import pytest

from tools.collection.corpus import load_frozen_corpus
from tools.collection.freeze import FreezeError


def test_train_partition_excludes_other_partitions(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    corpus = load_frozen_corpus(database, manifest, "TRAIN")
    for user_id, windows in corpus.windows_by_user.items():
        days = corpus.day_assignments[user_id]
        assert {days[w.collection_day] for w in windows} == {"TRAIN"}


def test_a_tampered_manifest_is_refused(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    text = manifest.read_text(encoding="utf-8").replace('"TRAIN"', '"EVALUATION"', 1)
    manifest.chmod(0o600)
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(FreezeError):
        load_frozen_corpus(database, manifest, "TRAIN")


def test_windows_added_after_the_freeze_are_excluded(frozen_fixture, add_window) -> None:
    """Post-freeze windows must never leak into a frozen result."""

    database, manifest = frozen_fixture
    add_window(database, window_id="late-window")
    with pytest.raises(FreezeError):
        load_frozen_corpus(database, manifest, "TRAIN")


def test_observation_times_are_returned_for_every_window(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    corpus = load_frozen_corpus(database, manifest, "TRAIN")
    for windows in corpus.windows_by_user.values():
        for window in windows:
            assert window.window_id in corpus.observed_at_by_window


def test_unknown_partition_is_rejected(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    with pytest.raises(ValueError):
        load_frozen_corpus(database, manifest, "HOLDOUT")
```

Note on `test_windows_added_after_the_freeze_are_excluded`: `verify_freeze` raises when frozen windows are missing or changed. A purely *added* window does not change any frozen record, so if `verify_freeze` passes, assert instead that `load_frozen_corpus` excludes `late-window` from its output. Write whichever assertion matches actual `verify_freeze` behaviour — run it and see — but the loader must never return a window absent from the manifest.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools/collection/tests/test_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.collection.corpus'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/collection/corpus.py`:

```python
"""Load a frozen participant corpus into ML feature windows.

Nothing here reads the database without first verifying the manifest. The
freeze manifest is the corpus's integrity boundary (PLAN.md Section 9.4) and,
under ADR-013, part of its admission evidence: a corpus that no longer matches
its manifest is never loaded, and a window absent from the manifest is never
returned.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ml.features.schema import FeatureWindow

from .freeze import FreezeError, verify_freeze
from .repository import load_window_summaries

_PARTITIONS = frozenset({"TRAIN", "VALIDATION", "EVALUATION"})


@dataclass(frozen=True)
class FrozenCorpus:
    windows_by_user: dict[str, list[FeatureWindow]]
    manifest_window_ids: frozenset[str]
    observed_at_by_window: dict[str, datetime]
    day_assignments: dict[str, dict[str, str]]


def _row_to_window(row: sqlite3.Row) -> FeatureWindow:
    return FeatureWindow.model_validate(
        {
            "schema_version": row["schema_version"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "segment_id": row["segment_id"],
            "window_id": row["window_id"],
            "t_start_us": row["t_start_us"],
            "t_end_us": row["t_end_us"],
            "quality_label": row["quality_label"],
            "key_event_count": row["key_event_count"],
            "mouse_event_count": row["mouse_event_count"],
            "collection_day": row["collection_day"],
            "provenance": row["provenance"],
            "keyboard_features": (
                None
                if row["keyboard_features_json"] is None
                else json.loads(row["keyboard_features_json"])
            ),
            "mouse_features": (
                None
                if row["mouse_features_json"] is None
                else json.loads(row["mouse_features_json"])
            ),
            "context": json.loads(row["context_json"]),
        }
    )


def load_frozen_corpus(database: Path, manifest: Path, partition: str) -> FrozenCorpus:
    """Return one partition of a checksum-verified frozen corpus."""

    if partition not in _PARTITIONS:
        raise ValueError(f"unknown partition {partition!r}; expected one of {sorted(_PARTITIONS)}")

    # Verify before reading. A mismatch is a hard stop, never a warning.
    verify_freeze(manifest, load_window_summaries(database))

    document: Any = json.loads(manifest.read_text(encoding="utf-8"))
    records = {record["window_id"]: record for record in document["records"]}
    wanted = {
        window_id
        for window_id, record in records.items()
        if record["partition"] == partition
    }

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                   quality_label, key_event_count, mouse_event_count, collection_day,
                   provenance, keyboard_features_json, mouse_features_json, context_json,
                   schema_version, stored_at_utc
            FROM feature_windows ORDER BY user_id, window_id
            """
        ).fetchall()
    finally:
        connection.close()

    windows_by_user: dict[str, list[FeatureWindow]] = defaultdict(list)
    observed_at: dict[str, datetime] = {}
    for row in rows:
        window_id = str(row["window_id"])
        if window_id not in records:
            # Present in the database but not in the manifest: a post-freeze
            # window. It is silently excluded rather than quietly included.
            continue
        observed_at[window_id] = datetime.fromisoformat(
            str(row["stored_at_utc"]).replace("Z", "+00:00")
        ).astimezone(UTC)
        if window_id in wanted:
            windows_by_user[str(row["user_id"])].append(_row_to_window(row))

    missing = wanted - set(observed_at)
    if missing:
        raise FreezeError(f"{len(missing)} frozen windows are absent from the database")

    return FrozenCorpus(
        windows_by_user=dict(windows_by_user),
        manifest_window_ids=frozenset(records),
        observed_at_by_window=observed_at,
        day_assignments=document["day_assignments"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tools/collection -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add tools/collection/corpus.py tools/collection/tests/test_corpus.py
git commit -m "feat: load frozen participant corpora through verified manifests"
```

---

### Task 6: Thread admission through the evaluation pipeline

**Files:**
- Modify: `ml/evaluation/pipeline.py`
- Test: `ml/tests/test_evaluation_pipeline.py`

**Interfaces:**
- Consumes: `EnrollmentAdmission` (Task 1).
- Produces: `run_baseline_comparison`, the fusion comparison function, and `run_enrollment_length_experiment` each gain keyword-only `admission: EnrollmentAdmission | None = None`, forwarded to every trainer call.

- [ ] **Step 1: Write the failing test**

Append to `ml/tests/test_evaluation_pipeline.py`:

```python
def test_pilot_corpus_without_admission_raises() -> None:
    """Today's failure mode is preserved: real data needs a boundary."""

    from ml.training.gate import PromotionGateRequiredError
    from ml.evaluation.pipeline import run_baseline_comparison

    corpus = {"participant-01": _pilot_windows()}
    with pytest.raises(PromotionGateRequiredError):
        run_baseline_comparison(
            corpus, "keyboard", {"participant-01": ["2026-09-03"]}, _ml_config()
        )


def test_pilot_corpus_with_admission_trains() -> None:
    from ml.evaluation.pipeline import run_baseline_comparison

    windows = _pilot_windows()
    corpus = {"participant-01": windows}
    results = run_baseline_comparison(
        corpus,
        "keyboard",
        {"participant-01": ["2026-09-03"]},
        _ml_config(),
        model_types=("isolation_forest",),
        admission=_valid_admission(windows),
    )
    assert "isolation_forest" in results
    assert "participant-01" not in results["isolation_forest"].excluded_users


def test_synthetic_corpus_needs_no_admission() -> None:
    """Existing synthetic evaluation behaviour is unchanged."""

    from ml.evaluation.pipeline import run_baseline_comparison

    corpus = {"synthetic-user": _synthetic_windows()}
    results = run_baseline_comparison(
        corpus,
        "keyboard",
        {"synthetic-user": ["2026-09-03"]},
        _ml_config(),
        model_types=("isolation_forest",),
    )
    assert "isolation_forest" in results
```

Reuse the `_pilot_windows`, `_synthetic_windows`, `_ml_config`, and `_valid_admission` helpers from Task 2 by importing them, or duplicate them locally in this file — do not leave them undefined.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ml/tests/test_evaluation_pipeline.py -k admission -v`
Expected: FAIL — `TypeError: run_baseline_comparison() got an unexpected keyword argument 'admission'`

- [ ] **Step 3: Write minimal implementation**

In `ml/evaluation/pipeline.py`, add `from ml.training.enrollment import EnrollmentAdmission` and add the keyword-only parameter to each of the three public entry points. Forward it at every trainer call site:

- `run_baseline_comparison`: `trainer(user_id, modality, split.train, config, enrollment_admission=admission)` — note the baseline trainers take `promoted_candidates` positionally today; pass `admission` by keyword so the positional argument is unaffected.
- The fusion comparison: both `train_user_modality_isolation_forest` calls and the `train_user_single_fused_model` call.
- `run_enrollment_length_experiment`: the `train_user_modality_isolation_forest` call.

Each parameter's docstring line: `admission` admits a first profile's participant data per ADR-013; leave it `None` for synthetic, public, or already-promoted data.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ml/tests -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add ml/evaluation/pipeline.py ml/tests/test_evaluation_pipeline.py
git commit -m "feat: thread enrollment admission through the evaluation pipeline"
```

---

### Task 7: First-profile activation CLI

**Files:**
- Create: `tools/enrollment/__init__.py`, `tools/enrollment/__main__.py`, `tools/enrollment/activate.py`
- Test: `tools/enrollment/tests/test_activate.py`

**Interfaces:**
- Consumes: `load_frozen_corpus` (Task 5); `train_user_profile` with `enrollment_admission` (Task 2); `load_administration_records` (existing); `SQLiteUpdateRepository.activate_profile`, `ModelProfile`, `ValidationMetrics`, `ValidationReport` (existing); `zero_effort_cross_evaluation` (existing).
- Produces: `activate_first_profile(...) -> ModelProfile`, and `python -m tools.enrollment activate --participant-id ... --database ... --manifest ... --administration ... --artifact-root ... --storage-config ... --collection-config ... --ml-config ... --risk-config ...`

- [ ] **Step 1: Write the failing test**

Create `tools/enrollment/tests/test_activate.py`:

```python
"""First-profile activation trains on TRAIN and never reads EVALUATION."""

from __future__ import annotations

import pytest


def test_refuses_a_user_who_already_has_an_active_profile(enrollment_fixture) -> None:
    from ml.training.enrollment import EnrollmentAdmissionError
    from tools.enrollment.activate import activate_first_profile

    context = enrollment_fixture(with_active_profile=True)
    with pytest.raises(EnrollmentAdmissionError, match="PROFILE_ALREADY_ACTIVE"):
        activate_first_profile(**context)


def test_activates_a_profile_from_the_train_partition(enrollment_fixture) -> None:
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**enrollment_fixture())
    assert profile.user_id == "participant-01"
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"


def test_the_evaluation_partition_is_never_loaded(enrollment_fixture, monkeypatch) -> None:
    """Reading EVALUATION during enrollment would contaminate the headline result."""

    from tools.collection import corpus as corpus_module
    from tools.enrollment.activate import activate_first_profile

    requested: list[str] = []
    original = corpus_module.load_frozen_corpus

    def spy(database, manifest, partition):
        requested.append(partition)
        return original(database, manifest, partition)

    monkeypatch.setattr(corpus_module, "load_frozen_corpus", spy)
    activate_first_profile(**enrollment_fixture())
    assert "EVALUATION" not in requested


def test_the_activated_profile_is_readable_by_the_runtime(enrollment_fixture) -> None:
    from backend.app.runtime.profiles import DirectoryProfileProvider
    from tools.enrollment.activate import activate_first_profile

    context = enrollment_fixture()
    activate_first_profile(**context)
    provider = DirectoryProfileProvider(context["storage"], context["artifact_root"])
    assert provider("participant-01") is not None
```

Write an `enrollment_fixture` factory fixture in the same file that builds a temporary pilot-profile database with at least 5 distinct days and 20+ keyboard-bearing windows for two participants (a second participant is required so FAR can be computed by cross-evaluation), freezes it, writes an administration record with active consent and enrollment, and returns the keyword arguments `activate_first_profile` takes.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools/enrollment -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.enrollment'`

- [ ] **Step 3: Write the implementation**

Create `tools/enrollment/activate.py`. Core flow:

```python
"""Train and activate a participant's first model profile (ADR-013).

This is the reviewed replacement for tools/demo/bootstrap_first_model.py on
participant data. The demo script stays in place for synthetic development.

Two honest differences from the demo bootstrap:

* Training data passes the ADR-013 enrollment admission gate rather than
  skipping the boundary because its provenance happens to be SYNTHETIC.
* The activation ValidationReport carries measured numbers. FRR comes from the
  held-out VALIDATION partition and FAR from zero-effort cross-evaluation
  against other participants. The EVALUATION partition is never read: it is
  reserved for the headline result, and touching it here would make that
  result untrustworthy.

The reason code states plainly that no prior profile existed to regress
against. That is a fact about enrollment, not a defect, and it must not be
disguised as a passing regression check.
"""
```

Implement `activate_first_profile(*, participant_id, database, manifest, administration, artifact_root, storage, collection_settings, ml_config, risk_settings) -> ModelProfile` which:

1. Loads TRAIN and VALIDATION via `load_frozen_corpus`. Never EVALUATION.
2. Reads consent and enrollment via `load_administration_records`.
3. Queries `model_profiles` for an ACTIVE row for this user to set `user_has_active_profile`.
4. Builds `EnrollmentAdmission` with `min_windows` and `min_distinct_days` from `risk_settings.enrollment`, and `manifest_window_ids` / `observed_at_by_window` from the TRAIN corpus.
5. Calls `train_user_profile(participant_id, train_windows, ml_config, enrollment_admission=admission)`.
6. Raises if `train_user_profile` returns an empty dict, with the eligible-window count in the message.
7. Saves artifacts under `artifact_root / participant_id / f"{version}.joblib"` via `save_artifact`.
8. Computes FRR on VALIDATION and FAR by `zero_effort_cross_evaluation` against the other participants' VALIDATION windows, producing real `ValidationMetrics`. Uses the same metrics object for baseline and candidate, and records `code="ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"`.
9. Activates through `SQLiteUpdateRepository(storage).activate_profile(profile, retained_versions=...)` using the configured `retained_model_versions`.

Create `tools/enrollment/__main__.py` and an `argparse` CLI in the same style as `tools/collection/cli.py`, with `--storage-config` defaulting to `config/storage.pilot.yaml` and `--collection-config` defaulting to `config/collection.pilot.yaml`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tools/enrollment -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add tools/enrollment
git commit -m "feat: activate a participant's first profile from the frozen corpus"
```

---

### Task 8: Scheduled update run driver

**Files:**
- Create: `tools/updates/__init__.py`, `tools/updates/__main__.py`, `tools/updates/run.py`
- Test: `tools/updates/tests/test_run.py`

**Interfaces:**
- Consumes: `UpdateManager.run_scheduled`, `CandidateBuild`, `ValidationMetrics` (existing); `load_frozen_corpus` (Task 5); `train_user_profile` with `promoted_candidates` (Task 2).
- Produces: `build_candidate(user_id, promoted, *, context) -> CandidateBuild` and `run_update(...) -> UpdateRunOutcome`, plus `python -m tools.updates run --user-id ... --scheduled-for ...`

- [ ] **Step 1: Write the failing test**

Create `tools/updates/tests/test_run.py`:

```python
"""Scheduled retraining receives only promoted candidates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def test_a_run_before_quarantine_elapses_promotes_nothing(update_fixture) -> None:
    """G5 is seven days; a five-day round cannot promote inside it."""

    context = update_fixture(segment_completed_at=datetime(2026, 9, 5, tzinfo=UTC))
    from tools.updates.run import run_update

    outcome = run_update(**context, scheduled_for=datetime(2026, 9, 8, tzinfo=UTC))
    assert outcome.status == "REJECTED"
    assert outcome.code == "NO_ELIGIBLE_CANDIDATES"


def test_a_run_after_quarantine_promotes_and_trains(update_fixture) -> None:
    context = update_fixture(segment_completed_at=datetime(2026, 9, 5, tzinfo=UTC))
    from tools.updates.run import run_update

    outcome = run_update(**context, scheduled_for=datetime(2026, 9, 12, tzinfo=UTC))
    assert outcome.status in {"ACTIVATED", "REJECTED"}
    if outcome.status == "REJECTED":
        assert outcome.code != "NO_ELIGIBLE_CANDIDATES"


def test_training_receives_the_promoted_candidates(update_fixture, monkeypatch) -> None:
    """The promotion gate must actually be exercised, not bypassed."""

    seen: list[object] = []
    import ml.training.isolation_forest as forest

    original = forest.train_user_profile

    def spy(user_id, windows, config, promoted_candidates=None, **kwargs):
        seen.append(promoted_candidates)
        return original(user_id, windows, config, promoted_candidates, **kwargs)

    monkeypatch.setattr(forest, "train_user_profile", spy)
    context = update_fixture(segment_completed_at=datetime(2026, 9, 5, tzinfo=UTC))
    from tools.updates.run import run_update

    run_update(**context, scheduled_for=datetime(2026, 9, 12, tzinfo=UTC))
    assert seen and seen[0], "training must receive promoted candidates"


def test_quarantine_and_cadence_values_are_unchanged() -> None:
    from pathlib import Path

    from backend.app.updates.config import load_update_settings

    root = Path(__file__).resolve().parents[3]
    config = load_update_settings(root / "config/updates.development.yaml").update_manager
    assert config.quarantine_days == 7
    assert config.retraining_cadence_days == 7
```

Write an `update_fixture` factory that builds a pilot database with an already-active profile, a completed segment carrying an A1 or A3 anchor, at least `min_promotable_windows` scored windows with an available modality, and a frozen manifest.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools/updates -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.updates'`

- [ ] **Step 3: Write the implementation**

Create `tools/updates/run.py` with a `build_candidate` closure supplied to `UpdateManager.run_scheduled` as its `candidate_builder`. It must:

1. Load the promoted candidates' segments' windows from the TRAIN partition of the frozen corpus.
2. Call `train_user_profile(user_id, windows, ml_config, promoted_candidates={c.segment_id: c for c in promoted})` — the promotion gate is exercised, never bypassed.
3. Save artifacts and compute `baseline_metrics` from the currently active profile and `candidate_metrics` from the new one, both on the VALIDATION partition with cross-user impostors.
4. Return a `CandidateBuild`.

`run_update` loads settings, constructs `UpdateManager` with `SQLiteUpdateRepository`, and calls `run_scheduled`. Add the `argparse` CLI in `__main__.py`, `--scheduled-for` accepting an ISO-8601 timezone-aware timestamp.

Add a module docstring recording that `quarantine_days` and `retraining_cadence_days` remain 7 and that a five-day collection round therefore produces no live promotion — the pilot collects; the update manager acts afterwards.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tools/updates -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add tools/updates
git commit -m "feat: drive scheduled update runs over the frozen corpus"
```

---

### Task 9: E1 and E2 experiment drivers

**Files:**
- Create: `tools/experiments/__init__.py`, `tools/experiments/__main__.py`, `tools/experiments/drift.py`, `tools/experiments/poisoning.py`
- Test: `tools/experiments/tests/test_drivers.py`

**Interfaces:**
- Consumes: `evaluate_drift_benefit`, the poisoning-resistance function from `ml/experiments/update_manager.py` (existing); `load_frozen_corpus` (Task 5); `UpdateManager` (existing).
- Produces: `run_drift_benefit(...) -> DriftBenefitResult` and `run_poisoning_resistance(...) -> PoisoningResistanceResult`, plus `python -m tools.experiments e1|e2`.

- [ ] **Step 1: Write the failing test**

Create `tools/experiments/tests/test_drivers.py`:

```python
"""E1 and E2 run over a frozen corpus, never over live data."""

from __future__ import annotations

import pytest


def test_e1_requires_a_verified_evaluation_freeze(experiment_fixture) -> None:
    from tools.evaluation.freeze import EvaluationFreezeError
    from tools.experiments.drift import run_drift_benefit

    context = experiment_fixture(tamper_config=True)
    with pytest.raises(EvaluationFreezeError):
        run_drift_benefit(**context)


def test_e2_fails_loudly_if_an_injected_segment_is_promoted(experiment_fixture) -> None:
    """E2's whole point is that this must never happen silently."""

    from tools.experiments.poisoning import run_poisoning_resistance

    result = run_poisoning_resistance(**experiment_fixture())
    assert result.injected_promoted == 0


def test_e2_injected_segments_are_blocked_by_the_gate(experiment_fixture) -> None:
    from tools.experiments.poisoning import run_poisoning_resistance

    result = run_poisoning_resistance(**experiment_fixture())
    assert result.injected_segments > 0
    assert result.all_injected_blocked
```

Adapt the final assertion to the actual field name on `PoisoningResistanceResult` in `ml/experiments/update_manager.py` — read it before writing the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools/experiments -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.experiments'`

- [ ] **Step 3: Write the implementation**

`tools/experiments/drift.py` (E1): verify the evaluation freeze first via `verify_evaluation_freeze`, load the frozen corpus ordered by date, train a frozen profile on early TRAIN days and an updated profile through `tools.updates.run.build_candidate` at simulated timestamps past quarantine, score the same paired EVALUATION windows with both, and call `evaluate_drift_benefit` with the bootstrap settings from `config/evaluation.development.yaml`.

`tools/experiments/poisoning.py` (E2): inject labelled impostor segments (another participant's windows relabelled) into the candidate stream, run them through `UpdateManager.submit_segment` and `reassess`, and pass the resulting candidates to the poisoning-resistance calculation.

Both modules must state in their docstrings that results are not evidence until run on the frozen eligible corpus with the evaluation and config freeze verified, matching `docs/update-manager.md`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tools/experiments -v`
Expected: PASS

- [ ] **Step 5: Run guardrails and commit**

```bash
python tools/guardrails/check.py
git add tools/experiments
git commit -m "feat: add E1 drift and E2 poisoning experiment drivers"
```

---

### Task 10: Documentation and full-suite verification

**Files:**
- Modify: `docs/evaluation.md`, `docs/update-manager.md`, `guide.md`

- [ ] **Step 1: Update `docs/update-manager.md`**

Add a paragraph: the six gates govern updates to an existing profile; a user's first profile is admitted by the ADR-013 enrollment gate, which requires eligible provenance, active consent, a recorded enrollment, membership of a verified freeze manifest, the configured minimum windows and distinct days, and the absence of an active profile. State that `quarantine_days` and `retraining_cadence_days` remain 7, so a five-day round produces no live promotion by design.

- [ ] **Step 2: Update `docs/evaluation.md`**

Document the order: freeze the corpus, create the evaluation lock, activate first profiles via `python -m tools.enrollment activate`, then run evaluation with `admission` supplied. State that the EVALUATION partition is read only for the headline result.

- [ ] **Step 3: Replace the open question in `guide.md`**

Replace the "Note for Manas: the promotion gate" section with the resolved workflow: ADR-013 is approved, the enrollment gate admits the first profile from the frozen corpus, the promotion gate is unchanged and governs every update, and the 7-day quarantine means the first update run comes no earlier than day 12.

- [ ] **Step 4: Run every suite**

```bash
python -m pytest backend/tests ml/tests tools protocol/tests -q
python tools/guardrails/check.py
```

Expected: PASS, no findings.

- [ ] **Step 5: Confirm the promotion gate is byte-identical**

```bash
git diff --stat main -- ml/training/gate.py
```

Expected: no output. If `ml/training/gate.py` shows any change, revert it — this plan must not modify it.

- [ ] **Step 6: Commit**

```bash
git add docs guide.md
git commit -m "docs: document the enrollment gate and pilot training workflow"
```
