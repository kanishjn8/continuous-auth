from __future__ import annotations

import numpy as np
import pytest

from ml.evaluation.cross_evaluation import pairwise_far_matrix, zero_effort_cross_evaluation
from ml.evaluation.fusion import fuse_percentile_scores, fused_cross_evaluation
from ml.evaluation.metrics import compute_eer
from ml.evaluation.pipeline import run_baseline_comparison, run_enrollment_length_experiment
from ml.evaluation.splitting import day_disjoint_split, distinct_days
from ml.training.isolation_forest import train_user_modality_isolation_forest
from ml.tests.conftest import generate_multiday_user_windows


def _two_user_corpus(ml_config, num_days=6, segment_minutes=15):
    alice = generate_multiday_user_windows(
        "alice", 1, ml_config, num_days=num_days, segments_per_day=1, segment_minutes=segment_minutes,
        mean_dd_latency_us=150_000.0, std_dd_latency_us=15_000.0,
    )
    bob = generate_multiday_user_windows(
        "bob", 2, ml_config, num_days=num_days, segments_per_day=1, segment_minutes=segment_minutes,
        mean_dd_latency_us=500_000.0, std_dd_latency_us=15_000.0,
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
        artifacts[user_id] = train_user_modality_isolation_forest(user_id, "keyboard", split.train, ml_config)
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
        artifacts[user_id] = train_user_modality_isolation_forest(user_id, "keyboard", split.train, ml_config)
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
        artifacts[user_id] = train_user_modality_isolation_forest(user_id, "keyboard", split.train, ml_config)
        test_windows[user_id] = split.test

    results = zero_effort_cross_evaluation(artifacts, test_windows)
    matrix = pairwise_far_matrix(results, threshold=50.0)
    assert matrix["alice"]["bob"] >= 0.0
    assert matrix["bob"]["alice"] >= 0.0


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
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=4, segments_per_day=1, segment_minutes=20)
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
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=4, segments_per_day=1, segment_minutes=20)
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


# --- Enrollment-length experiment (Section 10.5) ---------------------------


def test_enrollment_length_experiment_reports_all_requested_lengths(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=6, segments_per_day=1, segment_minutes=15)
    days = distinct_days(windows)
    result = run_enrollment_length_experiment(
        "u1", "keyboard", windows, day_lengths=[1, 2, 3], holdout_days=[days[-1]], config=ml_config
    )
    assert set(result.keys()) == {1, 2, 3}
    for n_days, entry in result.items():
        assert entry["status"] in ("ok", "insufficient_data")


def test_enrollment_length_experiment_more_days_gives_more_training_windows(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=6, segments_per_day=1, segment_minutes=15)
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
