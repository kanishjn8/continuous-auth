"""Adapt ML normality percentiles into authoritative, risk-oriented C3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ml.features.schema import FeatureWindow
from ml.training.common import (
    ModelArtifact,
    ModelSchemaMismatchError,
)
from ml.training.common import ScoreResult as MLScoreResult
from ml.training.common import score_window as score_ml_window
from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    ModalityScore,
    ModelStatus,
    ScoreResult,
)

Modality = Literal["keyboard", "mouse"]


@dataclass(frozen=True)
class ProfileArtifacts:
    user_id: str
    profile_version: str
    model_version: str
    keyboard: ModelArtifact | None
    mouse: ModelArtifact | None

    def __post_init__(self) -> None:
        if (
            not self.user_id.strip()
            or not self.profile_version.strip()
            or not self.model_version.strip()
        ):
            raise ValueError("profile identifiers must not be blank")
        for expected, artifact in (("keyboard", self.keyboard), ("mouse", self.mouse)):
            if artifact is None:
                continue
            if artifact.user_id != self.user_id:
                raise ValueError(f"{expected} artifact belongs to another user")
            if artifact.modality != expected:
                raise ValueError(f"expected {expected} artifact, got {artifact.modality}")


ScoringIssueCode = Literal[
    "MODEL_UNAVAILABLE",
    "MODEL_FAILED",
    "FEATURE_SCHEMA_MISMATCH",
]


@dataclass(frozen=True)
class ScoringIssue:
    code: ScoringIssueCode
    modality: Modality
    detail: str


@dataclass(frozen=True)
class ScoringOutcome:
    score: ScoreResult
    issues: tuple[ScoringIssue, ...]


def calibrated_risk_from_percentile(percentile: float) -> float:
    """Map an ML normality percentile (0..100) onto the C3 risk scale (0..1).

    This is the single definition of that mapping. Anything that needs to
    reason about where the live system's decision boundaries fall on the
    percentile scale must invert it with ``percentile_for_calibrated_risk``
    rather than restating the arithmetic, so the two can never drift apart.
    """

    return 1.0 - percentile / 100.0


def percentile_for_calibrated_risk(calibrated_risk: float) -> float:
    """Inverse of :func:`calibrated_risk_from_percentile`.

    ``RiskEngine._candidate_level`` counts a window as breaching a threshold
    when ``calibrated_risk >= threshold``. Because the mapping is decreasing,
    the equivalent condition on the percentile scale is
    ``percentile <= percentile_for_calibrated_risk(threshold)`` -- note the
    inclusive comparison flips sides, which matters exactly at the boundary.

    Used by ``tools/enrollment/activate.py`` to measure a first profile's FRR
    at the operating point the deployed system actually uses, rather than at
    an Equal Error Rate point that a single-participant corpus cannot produce
    (ADR-014).
    """

    return (1.0 - calibrated_risk) * 100.0


def _unavailable(status: ModelStatus) -> ModalityScore:
    return ModalityScore(
        available=False,
        raw_score=None,
        calibrated_score=None,
        status=status,
    )


def _risk_or_failure(
    result: MLScoreResult,
    *,
    modality: Modality,
) -> tuple[ModalityScore, ScoringIssue | None]:
    if not result.available:
        return _unavailable(ModelStatus.UNAVAILABLE), None
    raw = result.raw_score
    percentile = result.percentile_score
    if (
        raw is None
        or percentile is None
        or not math.isfinite(raw)
        or not math.isfinite(percentile)
        or not 0 <= percentile <= 100
    ):
        return (
            _unavailable(ModelStatus.FAILED),
            ScoringIssue(
                code="MODEL_FAILED",
                modality=modality,
                detail="model returned an invalid normality percentile",
            ),
        )
    # ML calibration is a 0..100 normality percentile. C3/risk processing is
    # 0..1 with higher values representing greater anomaly/risk.
    calibrated_risk = 1.0 - percentile / 100.0
    return (
        ModalityScore(
            available=True,
            raw_score=raw,
            calibrated_score=calibrated_risk,
            status=ModelStatus.SCORED,
        ),
        None,
    )


class ModelScoringService:
    def __init__(self, profile: ProfileArtifacts):
        self.profile = profile

    def _score_modality(
        self,
        window: FeatureWindow,
        modality: Modality,
        artifact: ModelArtifact | None,
    ) -> tuple[ModalityScore, ScoringIssue | None]:
        block_available = (
            window.keyboard_features is not None
            if modality == "keyboard"
            else window.mouse_features is not None
        )
        if artifact is None:
            issue = (
                ScoringIssue(
                    code="MODEL_UNAVAILABLE",
                    modality=modality,
                    detail=f"{modality} evidence is present but no model is loaded",
                )
                if block_available
                else None
            )
            return _unavailable(ModelStatus.UNAVAILABLE), issue
        try:
            result = score_ml_window(artifact, window)
        except ModelSchemaMismatchError as exc:
            return (
                _unavailable(ModelStatus.SCHEMA_MISMATCH),
                ScoringIssue("FEATURE_SCHEMA_MISMATCH", modality, str(exc)),
            )
        except Exception as exc:
            return (
                _unavailable(ModelStatus.FAILED),
                ScoringIssue("MODEL_FAILED", modality, f"{type(exc).__name__}: {exc}"),
            )
        return _risk_or_failure(result, modality=modality)

    def score(self, window: FeatureWindow) -> ScoringOutcome:
        if window.user_id != self.profile.user_id:
            raise ValueError(
                f"window user {window.user_id!r} does not match profile "
                f"{self.profile.user_id!r}"
            )
        keyboard, keyboard_issue = self._score_modality(window, "keyboard", self.profile.keyboard)
        mouse, mouse_issue = self._score_modality(window, "mouse", self.profile.mouse)
        score = ScoreResult(
            schema_version=PROTOCOL_VERSION,
            user_id=window.user_id,
            profile_version=self.profile.profile_version,
            feature_schema_version=window.schema_version,
            model_version=self.profile.model_version,
            window_id=window.window_id,
            quality_label=window.quality_label,
            keyboard=keyboard,
            mouse=mouse,
        )
        return ScoringOutcome(
            score=score,
            issues=tuple(issue for issue in (keyboard_issue, mouse_issue) if issue is not None),
        )
