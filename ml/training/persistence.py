"""Model artifact save/load with checksum and feature-schema-version refusal.

PLAN.md Section 10.8: "A model whose feature schema version does not match
the running feature extractor must be refused at load time, not silently
used." Section 19.3 guardrail: same requirement, phrased as a CI-enforced
invariant. This module is the single place that invariant is implemented
for the ML package.
"""

from __future__ import annotations

from pathlib import Path

import joblib

from ml.training.common import (
    FEATURE_SCHEMA_VERSION,
    ModelArtifact,
    ModelSchemaMismatchError,
    compute_metadata_checksum,
)


class ArtifactCorruptedError(ValueError):
    """Raised when a loaded artifact's checksum does not match its metadata."""


def save_artifact(artifact: ModelArtifact, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)


def load_artifact(path: Path | str, *, expected_feature_schema_version: str = FEATURE_SCHEMA_VERSION) -> ModelArtifact:
    """Load and validate a model artifact.

    Refuses (raises) rather than returning a usable-but-wrong artifact when:
    - the artifact's ``feature_schema_version`` does not match the running
      feature extractor's version (``ModelSchemaMismatchError``);
    - the artifact's checksum does not match its own metadata, indicating
      corruption or tampering (``ArtifactCorruptedError``).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"model artifact not found: {path}")

    artifact: ModelArtifact = joblib.load(path)

    if artifact.feature_schema_version != expected_feature_schema_version:
        raise ModelSchemaMismatchError(
            f"artifact feature_schema_version={artifact.feature_schema_version!r} != "
            f"running feature_schema_version={expected_feature_schema_version!r}; refusing to load"
        )

    recomputed = compute_metadata_checksum(artifact.to_metadata_dict())
    if recomputed != artifact.checksum:
        raise ArtifactCorruptedError(
            f"checksum mismatch for artifact at {path}: stored={artifact.checksum!r} recomputed={recomputed!r}"
        )

    return artifact
