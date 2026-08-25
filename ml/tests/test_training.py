from __future__ import annotations

import numpy as np
import pytest

from ml.baselines.alt_one_class import train_user_modality_one_class_svm
from ml.baselines.mahalanobis import MahalanobisWrapper, train_user_modality_mahalanobis
from ml.training.common import (
    FEATURE_SCHEMA_VERSION,
    InsufficientDataError,
    ModelSchemaMismatchError,
    PreprocessingParams,
    build_feature_matrix,
    score_window,
    train_one_class_model,
)
from ml.training.isolation_forest import train_user_modality_isolation_forest, train_user_profile
from ml.training.model_wrappers import IsolationForestWrapper
from ml.training.persistence import ArtifactCorruptedError, load_artifact, save_artifact
from ml.tests.conftest import generate_user_windows


# --- Preprocessing -----------------------------------------------------


def test_preprocessing_fit_transform_gives_zero_mean_unit_std():
    rng = np.random.default_rng(0)
    X = rng.normal(loc=10.0, scale=3.0, size=(200, 4))
    params = PreprocessingParams.fit(X)
    X_scaled = params.transform(X)
    assert np.allclose(X_scaled.mean(axis=0), 0.0, atol=1e-9)
    assert np.allclose(X_scaled.std(axis=0), 1.0, atol=1e-9)


def test_preprocessing_handles_constant_feature_without_nan():
    X = np.column_stack([np.full(50, 7.0), np.random.default_rng(1).normal(size=50)])
    params = PreprocessingParams.fit(X)
    X_scaled = params.transform(X)
    assert np.all(np.isfinite(X_scaled))
    assert np.allclose(X_scaled[:, 0], 0.0)  # constant feature becomes exactly 0, not NaN


# --- build_feature_matrix -----------------------------------------------


def test_build_feature_matrix_only_uses_windows_with_that_modality(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=30)
    X_kbd, used_kbd = build_feature_matrix(windows, "keyboard")
    X_mouse, used_mouse = build_feature_matrix(windows, "mouse")
    assert all(w.keyboard_features is not None for w in used_kbd)
    assert all(w.mouse_features is not None for w in used_mouse)
    assert X_kbd.shape[0] == len(used_kbd)
    assert X_mouse.shape[0] == len(used_mouse)


# --- Cross-user isolation (P3) -------------------------------------------


def test_train_one_class_model_rejects_multi_user_input(ml_config):
    w1 = generate_user_windows("alice", seed=1, config=ml_config, duration_minutes=15)
    w2 = generate_user_windows("bob", seed=2, config=ml_config, duration_minutes=15)
    with pytest.raises(ValueError, match="multiple users"):
        train_one_class_model(
            "alice",
            "keyboard",
            [*w1, *w2],
            model_factory=lambda: IsolationForestWrapper(random_state=42),
            model_type="isolation_forest",
            hyperparameters={},
            min_windows=1,
        )


def test_insufficient_windows_raises(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=1)
    with pytest.raises(InsufficientDataError):
        train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)


# --- Isolation Forest end-to-end -----------------------------------------


def test_isolation_forest_artifact_has_required_metadata_fields(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)

    assert artifact.user_id == "u1"
    assert artifact.modality == "keyboard"
    assert artifact.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert artifact.feature_names
    assert artifact.training_data_date_range == ("2026-01-01", "2026-01-01")
    assert artifact.provenance_mix.get("synthetic", 0) > 0
    assert artifact.hyperparameters
    assert artifact.calibration.reference_scores
    assert artifact.metrics_at_training["n_training_windows"] > 0
    assert artifact.version
    assert artifact.checksum


def test_isolation_forest_never_trains_on_another_users_windows(ml_config):
    alice_windows = generate_user_windows("alice", seed=1, config=ml_config, duration_minutes=60)
    profile = train_user_profile("alice", alice_windows, ml_config)
    assert "keyboard" in profile
    assert profile["keyboard"].user_id == "alice"


def test_scoring_separates_normal_from_distinctly_different_behavior(ml_config):
    # Train on user "alice"'s own enrollment windows, then score a window
    # generated with a distinctly different typing rhythm; its percentile
    # should land, on average, further from the genuine distribution's
    # center than alice's own held-out windows.
    alice_train = generate_user_windows(
        "alice", seed=1, config=ml_config, duration_minutes=60, mean_dd_latency_us=150_000.0, std_dd_latency_us=20_000.0
    )
    alice_holdout = generate_user_windows(
        "alice", seed=99, config=ml_config, duration_minutes=15, mean_dd_latency_us=150_000.0, std_dd_latency_us=20_000.0
    )
    impostor_like = generate_user_windows(
        "alice", seed=2, config=ml_config, duration_minutes=15, mean_dd_latency_us=600_000.0, std_dd_latency_us=20_000.0
    )

    artifact = train_user_modality_isolation_forest("alice", "keyboard", alice_train, ml_config)

    genuine_pcts = [
        score_window(artifact, w).percentile_score
        for w in alice_holdout
        if w.keyboard_features is not None
    ]
    impostor_pcts = [
        score_window(artifact, w).percentile_score
        for w in impostor_like
        if w.keyboard_features is not None
    ]
    assert genuine_pcts and impostor_pcts
    assert np.mean(genuine_pcts) > np.mean(impostor_pcts)


def test_score_window_missing_modality_returns_unavailable_not_fabricated(ml_config):
    from ml.datasets.synthetic import SyntheticUserProfile, generate_keyboard_stream, _SeqCounter
    from ml.features.extractor import extract_windows
    from ml.features.schema import Provenance

    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "mouse", windows, ml_config)

    # Build a window with keyboard activity only (no mouse events at all) --
    # this always yields mouse_features=None regardless of gate thresholds.
    profile = SyntheticUserProfile(user_id="u1")
    rng = np.random.default_rng(123)
    kbd_events = generate_keyboard_stream(
        profile, rng, n_keystrokes=20, t_start_us=0, seq=_SeqCounter()
    )
    kbd_only_windows = extract_windows(
        kbd_events,
        [],
        [],
        user_id="u1",
        session_id="s-kbd-only",
        segment_id="seg-kbd-only",
        collection_day="2026-01-01",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    kbd_only_window = next(w for w in kbd_only_windows if w.mouse_features is None)

    result = score_window(artifact, kbd_only_window)
    assert result.available is False
    assert result.raw_score is None
    assert result.percentile_score is None


def test_score_window_raises_loudly_on_non_finite_model_output(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)

    class _BrokenModel:
        def normality_score(self, X):
            return np.array([np.nan])

    artifact.model = _BrokenModel()
    with pytest.raises(ValueError, match="non-finite"):
        score_window(artifact, windows[0])


def test_score_window_refuses_on_schema_mismatch(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)

    tampered = windows[0].model_copy(update={"feature_schema_version": "9.9.9-incompatible"})
    with pytest.raises(ModelSchemaMismatchError):
        score_window(artifact, tampered)


# --- Baselines (PLAN.md Section 10.4) ------------------------------------


def test_mahalanobis_baseline_trains_and_scores(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_mahalanobis("u1", "keyboard", windows, ml_config)
    assert artifact.model_type == "mahalanobis_centroid"
    result = score_window(artifact, windows[0])
    assert result.available is True
    assert np.isfinite(result.raw_score)


def test_mahalanobis_singular_covariance_does_not_crash():
    # Fewer samples than dimensions -> singular empirical covariance.
    rng = np.random.default_rng(0)
    X = rng.normal(size=(5, 24))
    model = MahalanobisWrapper(ridge=1e-6).fit(X)
    scores = model.normality_score(X)
    assert np.all(np.isfinite(scores))


def test_one_class_svm_baseline_trains_and_scores(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_one_class_svm("u1", "keyboard", windows, ml_config)
    assert artifact.model_type == "one_class_svm"
    result = score_window(artifact, windows[0])
    assert result.available is True
    assert np.isfinite(result.raw_score)


def test_baselines_and_primary_model_use_identical_preprocessing_and_calibration_procedure(ml_config):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    iso = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)
    maha = train_user_modality_mahalanobis("u1", "keyboard", windows, ml_config)
    svm = train_user_modality_one_class_svm("u1", "keyboard", windows, ml_config)
    # Same feature set, same preprocessing means (all trained on identical windows).
    assert iso.feature_names == maha.feature_names == svm.feature_names
    assert iso.preprocessing.mean == maha.preprocessing.mean == svm.preprocessing.mean


# --- Persistence / artifact refusal ---------------------------------------


def test_artifact_round_trips_through_save_load(ml_config, tmp_path):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)
    path = tmp_path / "u1_keyboard.joblib"
    save_artifact(artifact, path)
    loaded = load_artifact(path)
    assert loaded.checksum == artifact.checksum
    assert loaded.user_id == artifact.user_id

    result = score_window(loaded, windows[0])
    assert result.available is True


def test_load_refuses_on_feature_schema_mismatch(ml_config, tmp_path):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)
    path = tmp_path / "u1_keyboard.joblib"
    save_artifact(artifact, path)
    with pytest.raises(ModelSchemaMismatchError):
        load_artifact(path, expected_feature_schema_version="9.9.9-different")


def test_load_refuses_on_corrupted_checksum(ml_config, tmp_path):
    windows = generate_user_windows("u1", seed=1, config=ml_config, duration_minutes=60)
    artifact = train_user_modality_isolation_forest("u1", "keyboard", windows, ml_config)
    artifact.checksum = "deadbeef" * 8  # tamper
    path = tmp_path / "u1_keyboard.joblib"
    save_artifact(artifact, path)
    with pytest.raises(ArtifactCorruptedError):
        load_artifact(path)


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_artifact(tmp_path / "does_not_exist.joblib")
