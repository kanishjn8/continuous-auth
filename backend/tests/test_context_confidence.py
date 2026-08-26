from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.risk import ContextConfidenceLayer, load_context_config
from backend.app.risk.context_config import ContextConfigError
from protocol.generated.python.contracts import ApplicationCategory, ConfidenceSource


def test_config_is_strict_and_all_categories_present(tmp_path: Path) -> None:
    config = load_context_config()
    ContextConfidenceLayer(config)
    invalid = tmp_path / "context.yaml"
    invalid.write_text(
        "config_version: x\nprotocol_version: 1.0.0\ndevelopment_only: true\n"
        "context: {confidence_floor: 0, min_empirical_observations: 1, "
        "bootstrap_weights: {UNKNOWN: 1}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContextConfigError, match="failed validation"):
        load_context_config(invalid)


def test_bootstrap_unknown_and_unseen_user_arbitration() -> None:
    layer = ContextConfidenceLayer(load_context_config())
    known = layer.assess(user_id="user-a", category=ApplicationCategory.PRODUCTIVITY)
    unknown = layer.assess(user_id="user-a", category=ApplicationCategory.UNKNOWN)
    assert known.assessment.source == ConfidenceSource.BOOTSTRAP
    assert unknown.assessment.source == ConfidenceSource.NEUTRAL_FALLBACK
    assert unknown.assessment.confidence == 1.0


def test_empirical_layer_supersedes_bootstrap_only_at_observation_requirement() -> None:
    layer = ContextConfidenceLayer(load_context_config())
    category = ApplicationCategory.GAMING
    for value in (0.1, 0.2, 0.1):
        layer.observe_genuine(user_id="user-a", category=category, risk_score=value)
    assert layer.assess(user_id="user-a", category=category).assessment.source == (
        ConfidenceSource.BOOTSTRAP
    )
    layer.observe_genuine(user_id="user-a", category=category, risk_score=0.2)
    result = layer.assess(user_id="user-a", category=category)
    assert result.assessment.source == ConfidenceSource.EMPIRICAL
    assert result.observations == 4


def test_high_variance_hits_floor_and_monitoring_never_reaches_zero() -> None:
    layer = ContextConfidenceLayer(load_context_config())
    category = ApplicationCategory.CREATIVE
    for value in (0.0, 1.0, 0.0, 1.0):
        layer.observe_genuine(user_id="user-a", category=category, risk_score=value)
    result = layer.assess(user_id="user-a", category=category)
    assert result.assessment.source == ConfidenceSource.EMPIRICAL
    assert result.assessment.confidence == layer.config.confidence_floor
    assert result.assessment.confidence > 0


def test_statistics_are_isolated_per_user_and_category() -> None:
    layer = ContextConfidenceLayer(load_context_config())
    for _ in range(4):
        layer.observe_genuine(
            user_id="user-a",
            category=ApplicationCategory.DEVELOPMENT,
            risk_score=0.1,
        )
    assert (
        layer.assess(user_id="user-a", category=ApplicationCategory.DEVELOPMENT).assessment.source
        == ConfidenceSource.EMPIRICAL
    )
    assert (
        layer.assess(user_id="user-b", category=ApplicationCategory.DEVELOPMENT).assessment.source
        == ConfidenceSource.BOOTSTRAP
    )
    assert (
        layer.assess(user_id="user-a", category=ApplicationCategory.BROWSING).assessment.source
        == ConfidenceSource.BOOTSTRAP
    )


def test_invalid_genuine_observations_are_rejected() -> None:
    layer = ContextConfidenceLayer(load_context_config())
    with pytest.raises(ValueError, match="finite"):
        layer.observe_genuine(
            user_id="user-a",
            category=ApplicationCategory.SYSTEM,
            risk_score=float("nan"),
        )
