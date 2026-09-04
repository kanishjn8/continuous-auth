from __future__ import annotations

import numpy as np
import pytest

from ml.calibration.percentile import PercentileCalibrator
from ml.evaluation.cross_evaluation import (
    _modality_scores,
    pairwise_far_matrix,
    zero_effort_cross_evaluation,
)
from ml.evaluation.fusion import fuse_percentile_scores, fused_cross_evaluation
from ml.evaluation.metrics import compute_eer
from ml.evaluation.pipeline import (
    run_baseline_comparison,
    run_enrollment_length_experiment,
    run_fusion_vs_single_model_comparison,
)
from ml.evaluation.splitting import day_disjoint_split, distinct_days
from ml.features.keyboard import KEYBOARD_FEATURE_NAMES
from ml.features.schema import (
    FEATURE_SCHEMA_VERSION,
    AppCategory,
    AppFocusShare,
    ContextBlock,
    DeviceClass,
    FeatureWindow,
    KeyboardFeatures,
    Provenance,
    QualityLabel,
)
from ml.tests.conftest import generate_multiday_user_windows
from ml.tests.test_training import _ml_config, _pilot_windows, _valid_admission
from ml.training.common import ModelArtifact, ModelSchemaMismatchError, PreprocessingParams
from ml.training.isolation_forest import train_user_modality_isolation_forest


def _two_user_corpus(ml_config, num_days=6, segment_minutes=15):
    alice = generate_multiday_user_windows(
        "alice",
        1,
        ml_config,
        num_days=num_days,
        segments_per_day=1,
        segment_minutes=segment_minutes,
        mean_dd_latency_us=150_000.0,
        std_dd_latency_us=15_000.0,
    )
    bob = generate_multiday_user_windows(
        "bob",
        2,
        ml_config,
        num_days=num_days,
        segments_per_day=1,
        segment_minutes=segment_minutes,
        mean_dd_latency_us=500_000.0,
        std_dd_latency_us=15_000.0,
    )
    return {"alice": alice, "bob": bob}


# --- Cross-evaluation (I1) ------------------------------------------------


def test_zero_effort_cross_evaluation_no_training_data_crosses_users(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}

    artifacts = {}
    test_windows = {}
    for user_id, windows in corpus.items():
        split = day_disjoint_split(windows, test_days=test_days[user_id])
        artifacts[user_id] = train_user_modality_isolation_forest(
            user_id, "keyboard", split.train, ml_config
        )
        test_windows[user_id] = split.test

    results = zero_effort_cross_evaluation(artifacts, test_windows)
    assert set(results.keys()) == {"alice", "bob"}
    assert results["alice"].genuine_scores
    assert "bob" in results["alice"].impostor_scores
    assert results["bob"].genuine_scores
    assert "alice" in results["bob"].impostor_scores


def test_cross_evaluation_separates_distinctly_different_typists(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    artifacts = {}
    test_windows = {}
    for user_id, windows in corpus.items():
        split = day_disjoint_split(windows, test_days=test_days[user_id])
        artifacts[user_id] = train_user_modality_isolation_forest(
            user_id, "keyboard", split.train, ml_config
        )
        test_windows[user_id] = split.test

    results = zero_effort_cross_evaluation(artifacts, test_windows)
    alice = results["alice"]
    eer = compute_eer(np.asarray(alice.genuine_scores), alice.all_impostor_scores())
    # Alice and Bob have very different mean typing latency (150ms vs
    # 500ms) -- the EER should be well below chance (0.5).
    assert eer.eer < 0.3


def test_pairwise_far_matrix_shape(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    artifacts = {}
    test_windows = {}
    for user_id, windows in corpus.items():
        split = day_disjoint_split(windows, test_days=test_days[user_id])
        artifacts[user_id] = train_user_modality_isolation_forest(
            user_id, "keyboard", split.train, ml_config
        )
        test_windows[user_id] = split.test

    results = zero_effort_cross_evaluation(artifacts, test_windows)
    matrix = pairwise_far_matrix(results, threshold=50.0)
    assert matrix["alice"]["bob"] >= 0.0
    assert matrix["bob"]["alice"] >= 0.0


class _ScriptedModel:
    """Fake OneClassModel returning a scripted raw score per call, in order."""

    def __init__(self, scores):
        self._scores = list(scores)
        self._i = 0

    def normality_score(self, X):
        v = self._scores[self._i]
        self._i += 1
        return np.array([v])


def _fake_window(window_id, *, feature_schema_version=FEATURE_SCHEMA_VERSION):
    window = FeatureWindow(
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        window_id=window_id,
        t_start_us=0,
        t_end_us=1000,
        quality_label=QualityLabel.KBD_ONLY,
        key_event_count=10,
        mouse_event_count=0,
        collection_day="2026-01-01",
        provenance=Provenance.SYNTHETIC,
        keyboard_features=KeyboardFeatures(**{name: 0.0 for name in KEYBOARD_FEATURE_NAMES}),
        mouse_features=None,
        context=ContextBlock(
            dominant_category=AppCategory.UNKNOWN,
            category_fractions={AppCategory.UNKNOWN.value: 1.0},
            app_switch_rate=0.0,
            device_class=DeviceClass.UNKNOWN,
            app_shares=[AppFocusShare(app_id=0, category=AppCategory.UNKNOWN, fraction=1.0)],
        ),
    )
    if feature_schema_version != FEATURE_SCHEMA_VERSION:
        return window.model_copy(update={"schema_version": feature_schema_version})
    return window


def _fake_artifact(model, *, feature_schema_version=FEATURE_SCHEMA_VERSION):
    return ModelArtifact(
        user_id="u1",
        modality="keyboard",
        model_type="fake",
        feature_schema_version=feature_schema_version,
        feature_names=list(KEYBOARD_FEATURE_NAMES[:2]),
        training_data_date_range=("2026-01-01", "2026-01-01"),
        provenance_mix={},
        hyperparameters={},
        preprocessing=PreprocessingParams(mean=[0.0, 0.0], scale=[1.0, 1.0]),
        calibration=PercentileCalibrator.fit(np.array([0.3, 0.5, 0.7])),
        metrics_at_training={},
        version="v1",
        checksum="",
        model=model,
    )


def test_modality_scores_excludes_one_nonfinite_window_without_crashing():
    # A single degenerate window (non-finite raw score) must not abort
    # scoring for every other window/user pair in a cross-evaluation loop
    # -- matches how an unavailable modality already contributes no row
    # rather than crashing the whole run.
    artifact = _fake_artifact(_ScriptedModel([float("inf"), 0.5]))
    scores = _modality_scores(artifact, [_fake_window("w1"), _fake_window("w2")])
    assert len(scores) == 1


def test_modality_scores_still_propagates_schema_mismatch():
    # A schema mismatch applies to the whole artifact, not one window
    # (PLAN.md guardrail: "refuse, never silently use") -- unlike a lone
    # non-finite score, this must still abort rather than be silently
    # excluded.
    artifact = _fake_artifact(_ScriptedModel([0.5]))
    windows = [_fake_window("w1", feature_schema_version="0.9.0-other")]
    with pytest.raises(ModelSchemaMismatchError):
        _modality_scores(artifact, windows)


# --- Fusion ablation -------------------------------------------------------


def test_fuse_percentile_scores_availability_weighted():
    assert fuse_percentile_scores(80.0, 60.0, w_kbd=0.5, w_mouse=0.5) == 70.0
    assert fuse_percentile_scores(80.0, None) == 80.0
    assert fuse_percentile_scores(None, 60.0) == 60.0
    assert fuse_percentile_scores(None, None) is None


def test_fuse_percentile_scores_rejects_zero_total_weight():
    with pytest.raises(ValueError):
        fuse_percentile_scores(80.0, 60.0, w_kbd=0.0, w_mouse=0.0)


def test_fused_cross_evaluation_only_includes_users_with_both_modalities(ml_config):
    windows = generate_multiday_user_windows(
        "u1", 1, ml_config, num_days=4, segments_per_day=1, segment_minutes=20
    )
    days = distinct_days(windows)
    split = day_disjoint_split(windows, test_days=[days[-1]])
    kbd_artifact = train_user_modality_isolation_forest("u1", "keyboard", split.train, ml_config)
    mouse_artifact = train_user_modality_isolation_forest("u1", "mouse", split.train, ml_config)

    results = fused_cross_evaluation(
        {"u1": kbd_artifact}, {"u1": mouse_artifact}, {"u1": split.test}
    )
    assert "u1" in results
    assert results["u1"].genuine_scores


def test_fused_cross_evaluation_excludes_user_missing_one_modality(ml_config):
    windows = generate_multiday_user_windows(
        "u1", 1, ml_config, num_days=4, segments_per_day=1, segment_minutes=20
    )
    days = distinct_days(windows)
    split = day_disjoint_split(windows, test_days=[days[-1]])
    kbd_artifact = train_user_modality_isolation_forest("u1", "keyboard", split.train, ml_config)

    results = fused_cross_evaluation({"u1": kbd_artifact}, {}, {"u1": split.test})
    assert results == {}


# --- Baseline comparison (Section 10.4) ------------------------------------


def test_run_baseline_comparison_covers_all_required_model_types(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    results = run_baseline_comparison(corpus, "keyboard", test_days, ml_config)
    assert set(results.keys()) == {"isolation_forest", "mahalanobis_centroid", "one_class_svm"}
    for model_type, result in results.items():
        assert result.per_user_eer, f"{model_type} produced no per-user EER results"
        assert result.aggregate["mean"] >= 0.0


def test_run_baseline_comparison_retains_artifacts_for_traceability(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    results = run_baseline_comparison(corpus, "keyboard", test_days, ml_config)
    for model_type, result in results.items():
        assert "alice" in result.artifacts
        assert result.artifacts["alice"].model_type == model_type
        assert result.artifacts["alice"].checksum


def test_run_baseline_comparison_uses_identical_splits_across_model_types(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    results = run_baseline_comparison(corpus, "keyboard", test_days, ml_config)
    n_genuine = {mt: r.per_user_n_genuine.get("alice") for mt, r in results.items()}
    # Same split -> same number of genuine test windows for every model type.
    assert len(set(n_genuine.values())) == 1


def test_run_baseline_comparison_excludes_user_with_bad_test_day_instead_of_crashing(ml_config):
    # day_disjoint_split() raises ValueError for a test_day absent from a
    # given user's windows (e.g. a globally-computed holdout day one user
    # has no data for) -- this call was previously outside the per-user
    # try/except, so it aborted run_baseline_comparison for every user and
    # every model_type instead of just excluding this one user. A 3rd user
    # is required so the surviving two still have each other as I1
    # impostors once alice is excluded (with only 2 users, excluding one
    # leaves the other with zero impostors -- correctly excluded too, but
    # for an unrelated reason that would mask this regression).
    corpus = _two_user_corpus(ml_config)
    corpus["carol"] = generate_multiday_user_windows(
        "carol",
        3,
        ml_config,
        num_days=6,
        segments_per_day=1,
        segment_minutes=15,
        mean_dd_latency_us=300_000.0,
        std_dd_latency_us=15_000.0,
    )
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    test_days["alice"] = ["2099-01-01"]
    results = run_baseline_comparison(corpus, "keyboard", test_days, ml_config)
    for model_type, result in results.items():
        assert "alice" in result.excluded_users, model_type
        assert "bob" in result.per_user_eer, model_type
        assert "carol" in result.per_user_eer, model_type


# --- Enrollment-length experiment (Section 10.5) ---------------------------


def test_enrollment_length_experiment_reports_all_requested_lengths(ml_config):
    windows = generate_multiday_user_windows(
        "u1", 1, ml_config, num_days=6, segments_per_day=1, segment_minutes=15
    )
    days = distinct_days(windows)
    result = run_enrollment_length_experiment(
        "u1", "keyboard", windows, day_lengths=[1, 2, 3], holdout_days=[days[-1]], config=ml_config
    )
    assert set(result.keys()) == {1, 2, 3}
    for _n_days, entry in result.items():
        assert entry["status"] in ("ok", "insufficient_data")


def test_enrollment_length_experiment_more_days_gives_more_training_windows(ml_config):
    windows = generate_multiday_user_windows(
        "u1", 1, ml_config, num_days=6, segments_per_day=1, segment_minutes=15
    )
    days = distinct_days(windows)
    result = run_enrollment_length_experiment(
        "u1", "keyboard", windows, day_lengths=[1, 3], holdout_days=[days[-1]], config=ml_config
    )
    assert result[1]["n_training_windows"] < result[3]["n_training_windows"]


def test_enrollment_length_experiment_with_impostor_data_reports_eer(ml_config):
    corpus = _two_user_corpus(ml_config)
    days = distinct_days(corpus["alice"])
    result = run_enrollment_length_experiment(
        "alice",
        "keyboard",
        corpus["alice"],
        day_lengths=[1, 2],
        holdout_days=[days[-1]],
        config=ml_config,
        impostor_windows=corpus["bob"],
    )
    for entry in result.values():
        if entry["status"] == "ok" and entry.get("n_impostor_test", 0) > 0:
            assert "eer" in entry


# --- Fusion vs single fused-vector model comparison (ADR-006 confirmation) -


def test_run_fusion_vs_single_model_comparison_runs_both_arms_against_real_artifacts(ml_config):
    # This is the T-011 wiring: fused_cross_evaluation (ml.evaluation.fusion)
    # run against real trained isolation-forest artifacts, not the
    # hand-built fakes fusion.py's own module docstring warns are
    # "in isolation".
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}

    result = run_fusion_vs_single_model_comparison(corpus, test_days, ml_config)

    for arm in (
        result.dual_model_fusion,
        result.single_fused_model,
        result.keyboard_only,
        result.mouse_only,
    ):
        assert arm.per_user_eer, f"{arm.label} produced no per-user EER results"
        assert arm.aggregate["mean"] >= 0.0
    assert (
        "alice" in result.kbd_artifacts
        and "alice" in result.mouse_artifacts
        and "alice" in result.single_artifacts
    )
    assert result.single_artifacts["alice"].model_type == "single_fused_isolation_forest"
    assert result.kbd_artifacts["alice"].model_type == "isolation_forest"


def test_run_fusion_vs_single_model_comparison_uses_identical_split_for_both_arms(ml_config):
    corpus = _two_user_corpus(ml_config)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}

    result = run_fusion_vs_single_model_comparison(corpus, test_days, ml_config)

    # Test corpus is entirely FULL-quality windows, so both arms score
    # exactly the same held-out windows -- same n_genuine per user.
    assert (
        result.dual_model_fusion.per_user_n_genuine["alice"]
        == result.single_fused_model.per_user_n_genuine["alice"]
    )


def test_run_fusion_vs_single_model_comparison_excludes_user_with_bad_test_day(ml_config):
    corpus = _two_user_corpus(ml_config)
    corpus["carol"] = generate_multiday_user_windows(
        "carol",
        3,
        ml_config,
        num_days=6,
        segments_per_day=1,
        segment_minutes=15,
        mean_dd_latency_us=300_000.0,
        std_dd_latency_us=15_000.0,
    )
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}
    test_days["alice"] = ["2099-01-01"]

    result = run_fusion_vs_single_model_comparison(corpus, test_days, ml_config)

    assert "alice" in result.excluded_users
    assert "bob" in result.dual_model_fusion.per_user_eer
    assert "carol" in result.dual_model_fusion.per_user_eer


def test_run_fusion_vs_single_model_comparison_surfaces_single_model_imputation_failure_mode(
    ml_config,
):
    # ADR-006's confirmation criterion requires the comparison to show *how*
    # each approach handles a missing modality, not just a headline EER.
    # Simulate a genuine user's own test-day window where mouse activity
    # simply stopped for a bit (ADR-005 KBD_ONLY) -- keyboard behavior is
    # untouched, only the mouse block is dropped.
    corpus = _two_user_corpus(ml_config, num_days=6, segment_minutes=20)
    test_days = {u: [distinct_days(w)[-1]] for u, w in corpus.items()}

    alice_windows = list(corpus["alice"])
    last_day = test_days["alice"][0]
    full_indices = [
        i
        for i, w in enumerate(alice_windows)
        if w.collection_day == last_day and w.quality_label == QualityLabel.FULL
    ]
    assert (
        len(full_indices) >= 4
    ), "test setup needs enough FULL test windows to convert some to KBD_ONLY"
    for i in full_indices[: len(full_indices) // 2]:
        alice_windows[i] = alice_windows[i].model_copy(
            update={"mouse_features": None, "quality_label": QualityLabel.KBD_ONLY}
        )
    corpus["alice"] = alice_windows

    result = run_fusion_vs_single_model_comparison(corpus, test_days, ml_config)

    dual_kbd_only = result.dual_model_fusion.missing_modality["kbd_only"]
    single_kbd_only = result.single_fused_model.missing_modality["kbd_only"]

    assert dual_kbd_only.n_windows > 0
    assert single_kbd_only.n_windows == dual_kbd_only.n_windows
    assert (
        dual_kbd_only.n_scored == dual_kbd_only.n_windows
    )  # dual model: clean, always scores on keyboard alone
    assert (
        single_kbd_only.n_scored == single_kbd_only.n_windows
    )  # single model: always scores, via imputation
    assert dual_kbd_only.mean_percentile_score is not None
    assert single_kbd_only.mean_percentile_score is not None
    # The predicted failure mode: the single model's zero-imputed mouse
    # block reads as far more anomalous than the dual model's honest
    # "score on keyboard alone" for the exact same genuine windows.
    assert single_kbd_only.mean_percentile_score < dual_kbd_only.mean_percentile_score


# --- Enrollment admission threaded through evaluation (ADR-013, Task 6) ----
#
# Adaptation note: the brief's literal helper calls do not survive contact
# with day_disjoint_split. ``_pilot_windows()`` (Task 2) spans exactly the
# three days 2026-01-01..03 (not "2026-09-03", which appears in no window
# and would make day_disjoint_split raise before the admission boundary is
# ever reached), and ``_synthetic_windows()`` spans a single day, which
# day_disjoint_split refuses to hold out entirely (it would leave nothing to
# train on). ``_valid_admission(windows)`` also fixes min_distinct_days=3
# against whatever windows are actually handed to the trainer -- which, once
# run_baseline_comparison holds one day out for testing, is the *training*
# subset, not the full corpus. Holding a day out of ``_pilot_windows()``'s
# 3-day corpus would leave only 2 distinct training days, short of that
# floor, so the "trains" case below uses a local 4-day pilot corpus instead
# (same construction as ``_pilot_windows``, one more day) so 3 days remain
# after the split. The "raises" case is unaffected by that floor (the
# promotion gate rejects on provenance before any admission check runs), so
# it keeps the unmodified 3-day ``_pilot_windows()``. The synthetic case
# uses a local 3-day synthetic corpus for the same day-disjoint-split reason.


def _pilot_windows_multi_day(user_id: str = "participant-01", *, num_days: int = 4):
    windows = generate_multiday_user_windows(
        user_id,
        base_seed=1,
        config=_ml_config(),
        num_days=num_days,
        segments_per_day=2,
        segment_minutes=20,
    )
    return [w.model_copy(update={"provenance": Provenance.PILOT}) for w in windows]


def test_pilot_corpus_without_admission_raises() -> None:
    """Today's failure mode is preserved: real data needs a boundary."""

    from ml.training.gate import PromotionGateRequiredError

    corpus = {"participant-01": _pilot_windows()}
    with pytest.raises(PromotionGateRequiredError):
        run_baseline_comparison(
            corpus, "keyboard", {"participant-01": ["2026-01-03"]}, _ml_config()
        )


def test_pilot_corpus_with_admission_trains() -> None:
    """``admission`` unblocks training on this participant's PILOT windows.

    Only training is asserted here, not a computed EER: ``admission`` is a
    single object forwarded identically to every trainer call in this run
    (per this task's interface), so it can admit only one consenting
    participant at a time. A second corpus user would either need the same
    (mismatched) consent record -- refused by eligibility -- or synthetic
    windows, which ``_admit_training_data`` itself refuses to train under a
    non-``None`` admission (that guard is exactly what stops admitted
    participant evidence from being silently mixed with synthetic data).
    Multi-user cross-evaluation is already covered by the synthetic-corpus
    tests elsewhere in this file; what ADR-013 adds is that training itself
    -- producing an artifact -- no longer raises for this user.
    """

    windows = _pilot_windows_multi_day()
    corpus = {"participant-01": windows}
    results = run_baseline_comparison(
        corpus,
        "keyboard",
        {"participant-01": ["2026-01-04"]},
        _ml_config(),
        model_types=("isolation_forest",),
        admission=_valid_admission(windows),
    )
    assert "isolation_forest" in results
    assert "participant-01" in results["isolation_forest"].artifacts


def test_synthetic_corpus_needs_no_admission() -> None:
    """Existing synthetic evaluation behaviour is unchanged.

    Cross-evaluation (I1) needs a second user to supply impostor scores, so
    this reuses the two-user corpus already used throughout this file rather
    than the brief's single-user ``_synthetic_windows()`` (which also, being
    single-day, cannot be day-disjoint split at all).
    """

    corpus = _two_user_corpus(_ml_config())
    results = run_baseline_comparison(
        corpus,
        "keyboard",
        {"alice": ["2026-01-06"], "bob": ["2026-01-06"]},
        _ml_config(),
        model_types=("isolation_forest",),
    )
    assert "isolation_forest" in results
    assert "alice" not in results["isolation_forest"].excluded_users
