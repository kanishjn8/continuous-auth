from __future__ import annotations

from typing import Literal

import numpy as np
import pytest

from backend.app.models import ModelScoringService, ProfileArtifacts
from ml.calibration.percentile import PercentileCalibrator
from ml.features.config import load_config as load_ml_config
from ml.features.keyboard import KEYBOARD_FEATURE_NAMES
from ml.features.mouse import MOUSE_FEATURE_NAMES
from ml.training.common import ModelArtifact, PreprocessingParams
from protocol.generated.python.contracts import ModelStatus, WindowQuality
from tools.synthetic import generate_scenario, load_synthetic_config
from tools.synthetic.generator import ScenarioBundle


class _ConstantModel:
    def __init__(self, score: float):
        self.score = score

    def fit(self, X: np.ndarray) -> _ConstantModel:
        return self

    def normality_score(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), self.score)


def _artifact(
    user_id: str,
    modality: Literal["keyboard", "mouse"],
    score: float = 0.0,
) -> ModelArtifact:
    names = KEYBOARD_FEATURE_NAMES if modality == "keyboard" else MOUSE_FEATURE_NAMES
    artifact = ModelArtifact(
        user_id=user_id,
        modality=modality,
        model_type="synthetic-test-model",
        feature_schema_version="1.0.0",
        feature_names=list(names),
        training_data_date_range=("2026-01-01", "2026-01-01"),
        provenance_mix={"SYNTHETIC": 1},
        hyperparameters={},
        preprocessing=PreprocessingParams(
            mean=[0.0] * len(names),
            scale=[1.0] * len(names),
        ),
        calibration=PercentileCalibrator(reference_scores=[-1.0, 0.0, 1.0, 2.0]),
        metrics_at_training={},
        version=f"{modality}-v1",
    )
    artifact.model = _ConstantModel(score)
    return artifact


@pytest.fixture(scope="module")
def scenario() -> ScenarioBundle:
    return generate_scenario(
        load_synthetic_config(),
        "baseline",
        ml_config=load_ml_config(),
    )


def test_normality_percentile_becomes_bounded_risk_oriented_c3(
    scenario: ScenarioBundle,
) -> None:
    window = scenario.feature_windows[0]
    profile = ProfileArtifacts(
        user_id=window.user_id,
        profile_version="profile-v1",
        model_version="model-v1",
        keyboard=_artifact(window.user_id, "keyboard", score=0.0),
        mouse=_artifact(window.user_id, "mouse", score=2.0),
    )
    outcome = ModelScoringService(profile).score(window)
    assert outcome.issues == ()
    assert outcome.score.keyboard.calibrated_score == pytest.approx(0.5)
    assert outcome.score.mouse.calibrated_score == pytest.approx(0.0)
    assert outcome.score.quality_label == WindowQuality.FULL
    assert outcome.score.model_dump(mode="json")["schema_version"] == "1.0.0"


def test_present_modality_without_model_is_loudly_unavailable(
    scenario: ScenarioBundle,
) -> None:
    window = scenario.feature_windows[0]
    profile = ProfileArtifacts(
        user_id=window.user_id,
        profile_version="profile-v1",
        model_version="model-v1",
        keyboard=_artifact(window.user_id, "keyboard"),
        mouse=None,
    )
    outcome = ModelScoringService(profile).score(window)
    assert outcome.score.mouse.status == ModelStatus.UNAVAILABLE
    assert [issue.code for issue in outcome.issues] == ["MODEL_UNAVAILABLE"]


def test_absent_modality_does_not_create_false_failure(scenario: ScenarioBundle) -> None:
    source = scenario.feature_windows[0]
    window = source.model_copy(
        update={"quality_label": WindowQuality.KBD_ONLY, "mouse_features": None}
    )
    profile = ProfileArtifacts(
        user_id=window.user_id,
        profile_version="profile-v1",
        model_version="model-v1",
        keyboard=_artifact(window.user_id, "keyboard"),
        mouse=None,
    )
    outcome = ModelScoringService(profile).score(window)
    assert outcome.issues == ()
    assert outcome.score.mouse.available is False


def test_schema_mismatch_and_model_failure_are_contained(scenario: ScenarioBundle) -> None:
    window = scenario.feature_windows[0]
    mismatch = _artifact(window.user_id, "keyboard")
    mismatch.feature_schema_version = "incompatible"
    broken = _artifact(window.user_id, "mouse", score=float("nan"))
    profile = ProfileArtifacts(
        user_id=window.user_id,
        profile_version="profile-v1",
        model_version="model-v1",
        keyboard=mismatch,
        mouse=broken,
    )
    outcome = ModelScoringService(profile).score(window)
    assert outcome.score.keyboard.status == ModelStatus.SCHEMA_MISMATCH
    assert outcome.score.mouse.status == ModelStatus.FAILED
    assert {issue.code for issue in outcome.issues} == {
        "FEATURE_SCHEMA_MISMATCH",
        "MODEL_FAILED",
    }


def test_cross_user_and_mislabelled_artifacts_are_rejected(
    scenario: ScenarioBundle,
) -> None:
    window = scenario.feature_windows[0]
    with pytest.raises(ValueError, match="another user"):
        ProfileArtifacts(
            user_id=window.user_id,
            profile_version="profile-v1",
            model_version="model-v1",
            keyboard=_artifact("someone-else", "keyboard"),
            mouse=None,
        )

    service = ModelScoringService(
        ProfileArtifacts(
            user_id=window.user_id,
            profile_version="profile-v1",
            model_version="model-v1",
            keyboard=_artifact(window.user_id, "keyboard"),
            mouse=None,
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        service.score(window.model_copy(update={"user_id": "someone-else"}))
