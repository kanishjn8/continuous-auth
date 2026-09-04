"""Shared machinery for T-010: feature-matrix assembly, model artifacts,
and the generic per-user one-class training routine used by both the
Isolation Forest model and the required baselines (PLAN.md Section 10.4).

P3 (per-user independent models) is enforced structurally here: every
public function in this module takes exactly one user's windows (a single
``user_id`` is asserted across the input), so there is no code path through
which a second user's data could reach a model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

import numpy as np

from ml.calibration.percentile import PercentileCalibrator
from ml.features.keyboard import KEYBOARD_FEATURE_NAMES
from ml.features.mouse import MOUSE_FEATURE_NAMES
from ml.features.schema import FEATURE_SCHEMA_VERSION, FeatureWindow, Provenance
from ml.training.enrollment import EnrollmentAdmission, require_enrollment_admission
from ml.training.gate import require_promotion_gate
from protocol.generated.python.contracts import UpdateCandidate

# "combined" is the ADR-006 confirmation-criterion comparison baseline: a
# single model trained on the concatenation of the keyboard and mouse
# feature blocks (ml/training/single_fused_model.py), evaluated directly
# against the production dual-model score-fusion design. It is not a third
# production modality.
Modality = Literal["keyboard", "mouse", "combined"]
COMBINED_FEATURE_NAMES: tuple[str, ...] = KEYBOARD_FEATURE_NAMES + MOUSE_FEATURE_NAMES


class ModelSchemaMismatchError(ValueError):
    """Raised when a loaded artifact's feature-schema version does not match
    the running feature extractor's. PLAN.md guardrail: "Model refuses to
    load on feature-schema version mismatch" -- refuse, never silently use.
    """


class InsufficientDataError(ValueError):
    """Raised when there are too few eligible windows to train a model.

    Callers (e.g. a future state-machine integration, T-013) should treat
    this as "keep the user in ENROLLING", per PLAN.md Section 5.3 -- this
    module does not itself implement the state machine, but it must never
    silently train (and thus implicitly "activate") a model on too little
    evidence.
    """


def feature_names_for(modality: Modality) -> tuple[str, ...]:
    if modality == "keyboard":
        return KEYBOARD_FEATURE_NAMES
    if modality == "mouse":
        return MOUSE_FEATURE_NAMES
    if modality == "combined":
        return COMBINED_FEATURE_NAMES
    raise ValueError(f"unknown modality: {modality!r}")


def build_feature_matrix(
    windows: Sequence[FeatureWindow],
    modality: Modality,
) -> tuple[np.ndarray, list[FeatureWindow]]:
    """Build an (n, d) matrix from windows where ``modality`` was scored.

    Returns the matrix and the parallel list of windows actually used (only
    those whose ``quality_label`` made this modality available -- ADR-005 /
    ADR-006: an unavailable modality contributes no row, it is never
    imputed).

    For ``modality="combined"`` (the ADR-006 single fused-vector comparison
    baseline), a row requires *both* blocks to be present -- there is no
    vector to concatenate from a window missing one modality, so such
    windows are excluded from training exactly like an unavailable
    single modality is excluded above.
    """
    names = feature_names_for(modality)
    rows: list[list[float]] = []
    used: list[FeatureWindow] = []
    for w in windows:
        if modality == "combined":
            if w.keyboard_features is None or w.mouse_features is None:
                continue
            block = {
                **w.keyboard_features.model_dump(mode="python"),
                **w.mouse_features.model_dump(mode="python"),
            }
        else:
            selected = w.keyboard_features if modality == "keyboard" else w.mouse_features
            if selected is None:
                continue
            block = selected.model_dump(mode="python")
        rows.append([block[name] for name in names])
        used.append(w)
    if not rows:
        return np.empty((0, len(names))), []
    return np.asarray(rows, dtype=float), used


def compute_metadata_checksum(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass
class PreprocessingParams:
    """Per-user normalisation baseline (PLAN.md Section 10.2 item 2)."""

    mean: list[float]
    scale: list[float]  # standard deviation per feature; never 0 (see fit())

    def transform(self, X: np.ndarray) -> np.ndarray:
        transformed = (X - np.asarray(self.mean)) / np.asarray(self.scale)
        return np.asarray(transformed, dtype=float)

    @classmethod
    def fit(cls, X: np.ndarray) -> PreprocessingParams:
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        # A constant feature (std == 0) must not produce a divide-by-zero /
        # NaN; treat it as already-centered and leave it untouched by
        # scaling (scale=1.0), rather than silently propagating NaN/inf
        # downstream (Section 19.3 guardrail spirit).
        std_safe = np.where(std > 1e-12, std, 1.0)
        return cls(mean=mean.tolist(), scale=std_safe.tolist())


class OneClassModel(Protocol):
    def fit(self, X: np.ndarray) -> OneClassModel: ...

    def normality_score(self, X: np.ndarray) -> np.ndarray:
        """Higher = more normal (matches sklearn IsolationForest.score_samples
        convention). Baselines must adapt to this sign convention themselves.
        """
        ...


@dataclass
class ModelArtifact:
    """PLAN.md Section 10.8 model artifact fields, C5 in TASK_DELEGATION.md."""

    user_id: str
    modality: Modality
    model_type: str
    feature_schema_version: str
    feature_names: list[str]
    training_data_date_range: tuple[str, str]
    provenance_mix: dict[str, int]
    hyperparameters: dict[str, object]
    preprocessing: PreprocessingParams
    calibration: PercentileCalibrator
    metrics_at_training: dict[str, float]
    version: str
    checksum: str = field(default="")
    model: OneClassModel | None = field(default=None, repr=False, compare=False)

    def to_metadata_dict(self) -> dict[str, object]:
        """Everything except the fitted model object itself (for checksum/audit).

        Builds the dict from individual fields rather than calling
        ``dataclasses.asdict(self)`` -- ``asdict`` deep-copies every field
        including the fitted sklearn ``model`` before any field could be
        dropped, which is wasted work every time a checksum is computed.
        """
        d: dict[str, object] = {
            "user_id": self.user_id,
            "modality": self.modality,
            "model_type": self.model_type,
            "feature_schema_version": self.feature_schema_version,
            "feature_names": list(self.feature_names),
            "training_data_date_range": list(self.training_data_date_range),
            "provenance_mix": dict(self.provenance_mix),
            "hyperparameters": dict(self.hyperparameters),
            "preprocessing": asdict(self.preprocessing),
            "calibration": asdict(self.calibration),
            "metrics_at_training": dict(self.metrics_at_training),
            "version": self.version,
        }
        return d


def _training_metadata(
    user_id: str,
    modality: Modality,
    windows: Sequence[FeatureWindow],
    model_type: str,
    hyperparameters: dict[str, object],
    preprocessing: PreprocessingParams,
    calibrator: PercentileCalibrator,
    metrics: dict[str, float],
) -> ModelArtifact:
    days = sorted({w.collection_day for w in windows})
    provenance_mix: dict[str, int] = {}
    for w in windows:
        provenance_mix[w.provenance.value] = provenance_mix.get(w.provenance.value, 0) + 1

    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
    artifact = ModelArtifact(
        user_id=user_id,
        modality=modality,
        model_type=model_type,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=list(feature_names_for(modality)),
        training_data_date_range=(days[0], days[-1]) if days else ("", ""),
        provenance_mix=provenance_mix,
        hyperparameters=hyperparameters,
        preprocessing=preprocessing,
        calibration=calibrator,
        metrics_at_training=metrics,
        version=version,
    )
    artifact.checksum = compute_metadata_checksum(artifact.to_metadata_dict())
    return artifact


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
    an existing profile (ADR unchanged, PLAN.md Section 12), or the ADR-013
    enrollment gate for a first profile.

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


def train_one_class_model(
    user_id: str,
    modality: Modality,
    windows: Sequence[FeatureWindow],
    *,
    model_factory: Callable[[], OneClassModel],
    model_type: str,
    hyperparameters: dict[str, object],
    min_windows: int,
    promoted_candidates: Mapping[str, UpdateCandidate] | None = None,
    enrollment_admission: EnrollmentAdmission | None = None,
) -> ModelArtifact:
    """Generic per-user, single-modality one-class training routine.

    ``model_factory() -> OneClassModel`` constructs a fresh, unfit model
    instance (e.g. ``lambda: IsolationForestWrapper(**hp)``).

    P3 enforcement: this function has no parameter through which more than
    one user's windows could be supplied, and asserts single-user input
    defensively (a caller bug that concatenates users' windows before
    calling this function is the only way that could happen -- the assert
    catches it immediately rather than training silently on contaminated
    data).
    """
    distinct_users = {w.user_id for w in windows}
    if len(distinct_users) > 1:
        raise ValueError(
            f"train_one_class_model received windows from multiple users "
            f"({distinct_users}); per-user model isolation (P3) violated"
        )

    _admit_training_data(
        user_id,
        windows,
        promoted_candidates=promoted_candidates,
        enrollment_admission=enrollment_admission,
    )

    X, used = build_feature_matrix(windows, modality)
    if len(used) < min_windows:
        raise InsufficientDataError(
            f"user={user_id!r} modality={modality!r}: only {len(used)} eligible windows "
            f"(< min_windows={min_windows}); keep user in ENROLLING"
        )

    preprocessing = PreprocessingParams.fit(X)
    X_scaled = preprocessing.transform(X)

    model = model_factory()
    model.fit(X_scaled)
    raw_scores = model.normality_score(X_scaled)
    if not np.all(np.isfinite(raw_scores)):
        raise ValueError(
            f"model produced non-finite scores for user={user_id!r} modality={modality!r}"
        )

    calibrator = PercentileCalibrator.fit(raw_scores)

    metrics = {
        "n_training_windows": len(used),
        "raw_score_mean": float(np.mean(raw_scores)),
        "raw_score_std": float(np.std(raw_scores)),
    }

    artifact = _training_metadata(
        user_id, modality, used, model_type, hyperparameters, preprocessing, calibrator, metrics
    )
    artifact.model = model
    return artifact


@dataclass
class ScoreResult:
    modality: Modality
    available: bool
    raw_score: float | None
    percentile_score: float | None
    model_version: str
    feature_schema_version: str


def score_window(artifact: ModelArtifact, window: FeatureWindow) -> ScoreResult:
    """Score one window against a trained artifact (C3 in TASK_DELEGATION.md).

    If ``window.schema_version`` does not match the artifact's, this
    refuses to score (mismatch guardrail) rather than silently proceeding.
    """
    if window.schema_version != artifact.feature_schema_version:
        raise ModelSchemaMismatchError(
            f"window schema_version={window.schema_version!r} != "
            f"artifact feature_schema_version={artifact.feature_schema_version!r}"
        )

    block = window.keyboard_features if artifact.modality == "keyboard" else window.mouse_features
    if block is None:
        return ScoreResult(
            modality=artifact.modality,
            available=False,
            raw_score=None,
            percentile_score=None,
            model_version=artifact.version,
            feature_schema_version=artifact.feature_schema_version,
        )

    x = np.asarray([[block[name] for name in artifact.feature_names]], dtype=float)
    x_scaled = artifact.preprocessing.transform(x)
    if artifact.model is None:
        raise ValueError(f"artifact {artifact.version!r} has no fitted model")
    raw = float(artifact.model.normality_score(x_scaled)[0])
    if not np.isfinite(raw):
        # Never let a bad model/feature interaction propagate downstream as
        # a silently-usable score -- fail loudly, per Section 19.3's "no
        # silent fallback that masks a bug" guardrail spirit.
        raise ValueError(
            f"non-finite score for user={artifact.user_id!r} modality={artifact.modality!r} "
            f"window_id={window.window_id!r}"
        )
    pct = float(artifact.calibration.transform(np.asarray([raw]))[0])
    return ScoreResult(
        modality=artifact.modality,
        available=True,
        raw_score=raw,
        percentile_score=pct,
        model_version=artifact.version,
        feature_schema_version=artifact.feature_schema_version,
    )
