"""Scheduled retraining receives only promoted candidates."""

from __future__ import annotations

from datetime import UTC, datetime


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


def test_a_single_participant_corpus_is_refused_before_any_training(
    update_fixture, monkeypatch
) -> None:
    """ADR-014: model updates are disabled for a single-participant pilot.

    The refusal must come before training and before any artifact is written,
    so a refused run leaves nothing behind to clean up.
    """
    from ml.training import isolation_forest
    from tools.updates.run import run_update

    trained: list[str] = []
    monkeypatch.setattr(
        isolation_forest,
        "train_user_profile",
        lambda *args, **kwargs: trained.append("trained"),
    )

    context = update_fixture(
        segment_completed_at=datetime(2026, 1, 1, tzinfo=UTC), single_participant=True
    )
    artifact_root = context["artifact_root"]
    before = sorted(path.name for path in artifact_root.rglob("*.joblib"))

    outcome = run_update(**context, scheduled_for=datetime(2026, 1, 12, tzinfo=UTC))

    assert outcome.status == "SKIPPED"
    assert outcome.code == "UPDATE_REQUIRES_IMPOSTOR_COHORT"
    assert outcome.profile is None
    assert trained == []
    assert sorted(path.name for path in artifact_root.rglob("*.joblib")) == before


def test_a_cohort_corpus_is_still_processed_normally(update_fixture) -> None:
    """Control: the refusal is specific to the missing impostor cohort."""
    from tools.updates.run import run_update

    context = update_fixture(segment_completed_at=datetime(2026, 1, 1, tzinfo=UTC))
    outcome = run_update(**context, scheduled_for=datetime(2026, 1, 12, tzinfo=UTC))
    assert outcome.code != "UPDATE_REQUIRES_IMPOSTOR_COHORT"
