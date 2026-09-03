from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.risk import ContextConfidenceLayer, load_context_config
from backend.app.risk.context_config import ContextConfigError
from protocol.generated.python.contracts import (
    AppFocusShare,
    ApplicationCategory,
    ConfidenceSource,
    ContextAdjustmentMode,
)

STEADY_APP = 101
VOLATILE_APP = 202
OTHER_APP = 303


def _shares(*pairs: tuple[int, ApplicationCategory, float]) -> list[AppFocusShare]:
    return [
        AppFocusShare(app_id=app_id, category=category, fraction=fraction)
        for app_id, category, fraction in pairs
    ]


def _single(
    app_id: int, category: ApplicationCategory = ApplicationCategory.UNKNOWN
) -> list[AppFocusShare]:
    return _shares((app_id, category, 1.0))


def _layer(**overrides: object) -> ContextConfidenceLayer:
    config = load_context_config()
    if overrides:
        config = config.model_copy(update=overrides)
    return ContextConfidenceLayer(config)


def _observe(
    layer: ContextConfidenceLayer,
    user_id: str,
    shares: list[AppFocusShare],
    values: tuple[float, ...],
) -> None:
    for value in values:
        layer.observe_genuine(user_id=user_id, shares=shares, risk_score=value)


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
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


def test_config_requires_the_new_adjustment_tunables(tmp_path: Path) -> None:
    """Adjustment mode and scales are tunables, so they must come from config."""

    invalid = tmp_path / "context.yaml"
    invalid.write_text(
        "config_version: x\nprotocol_version: 1.0.0\ndevelopment_only: true\n"
        "context: {confidence_floor: 0.5, min_empirical_observations: 1, "
        "bootstrap_weights: {PRODUCTIVITY: 1, BROWSING: 1, DEVELOPMENT: 1, CREATIVE: 1, "
        "GAMING: 1, SYSTEM: 1, UNKNOWN: 1}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContextConfigError, match="failed validation"):
        load_context_config(invalid)


# ----------------------------------------------------------------------
# Bootstrap / cold start
# ----------------------------------------------------------------------
def test_bootstrap_prior_applies_before_any_observation() -> None:
    layer = _layer()
    known = layer.assess(
        user_id="user-a",
        shares=_single(STEADY_APP, ApplicationCategory.PRODUCTIVITY),
        dominant_category=ApplicationCategory.PRODUCTIVITY,
    )
    unknown = layer.assess(
        user_id="user-a",
        shares=_single(OTHER_APP, ApplicationCategory.UNKNOWN),
        dominant_category=ApplicationCategory.UNKNOWN,
    )
    assert known.assessment.source == ConfidenceSource.BOOTSTRAP
    assert known.assessment.confidence == pytest.approx(0.9)
    assert unknown.assessment.source == ConfidenceSource.NEUTRAL_FALLBACK
    assert unknown.assessment.confidence == 1.0


def test_empty_share_list_stays_neutral_and_never_zero() -> None:
    """A window with no attributable focus must not silence monitoring (G06)."""

    layer = _layer()
    result = layer.assess(
        user_id="user-a", shares=[], dominant_category=ApplicationCategory.UNKNOWN
    )
    assert result.assessment.source == ConfidenceSource.NEUTRAL_FALLBACK
    assert result.assessment.confidence > 0


# ----------------------------------------------------------------------
# The fix: UNKNOWN learns, and learning is keyed on app_id
# ----------------------------------------------------------------------
def test_unknown_category_accumulates_empirical_statistics_per_app() -> None:
    """Regression: UNKNOWN used to be pinned to bootstrap 1.0 forever.

    With an empty ``config/app_categories.yaml`` every application resolves to
    UNKNOWN, so excluding UNKNOWN from the empirical path disabled the entire
    context layer in the shipped configuration.
    """

    layer = _layer()
    shares = _single(VOLATILE_APP, ApplicationCategory.UNKNOWN)
    _observe(layer, "user-a", shares, (0.05, 0.55, 0.05, 0.55))

    result = layer.assess(
        user_id="user-a", shares=shares, dominant_category=ApplicationCategory.UNKNOWN
    )
    assert result.assessment.source == ConfidenceSource.EMPIRICAL
    # Genuine scores here swing widely, so confidence must fall below the
    # neutral 1.0 that the old implementation was stuck at.
    assert result.assessment.confidence < 1.0


def test_two_unknown_applications_learn_independently() -> None:
    """Statistics key on app_id, so one noisy app does not taint a steady one."""

    layer = _layer()
    steady = _single(STEADY_APP, ApplicationCategory.UNKNOWN)
    volatile = _single(VOLATILE_APP, ApplicationCategory.UNKNOWN)
    _observe(layer, "user-a", steady, (0.10, 0.11, 0.10, 0.11))
    _observe(layer, "user-a", volatile, (0.0, 0.9, 0.0, 0.9))

    steady_confidence = layer.assess(
        user_id="user-a", shares=steady, dominant_category=ApplicationCategory.UNKNOWN
    ).assessment.confidence
    volatile_confidence = layer.assess(
        user_id="user-a", shares=volatile, dominant_category=ApplicationCategory.UNKNOWN
    ).assessment.confidence
    assert steady_confidence > volatile_confidence


def test_a_brand_new_application_still_scores_and_then_adapts() -> None:
    """The OS-wide rule of thumb: a new app needs no map entry and no model."""

    layer = _layer()
    brand_new = _single(999_999, ApplicationCategory.UNKNOWN)
    first = layer.assess(
        user_id="user-a", shares=brand_new, dominant_category=ApplicationCategory.UNKNOWN
    )
    assert first.assessment.confidence > 0

    _observe(layer, "user-a", brand_new, (0.0, 0.8, 0.0, 0.8))
    later = layer.assess(
        user_id="user-a", shares=brand_new, dominant_category=ApplicationCategory.UNKNOWN
    )
    assert later.assessment.source == ConfidenceSource.EMPIRICAL
    assert later.assessment.confidence < first.assessment.confidence


def test_empirical_supersedes_bootstrap_only_at_observation_requirement() -> None:
    layer = _layer()
    shares = _single(STEADY_APP, ApplicationCategory.GAMING)
    _observe(layer, "user-a", shares, (0.1, 0.2, 0.1))
    assert (
        layer.assess(
            user_id="user-a", shares=shares, dominant_category=ApplicationCategory.GAMING
        ).assessment.source
        == ConfidenceSource.BOOTSTRAP
    )
    layer.observe_genuine(user_id="user-a", shares=shares, risk_score=0.2)
    result = layer.assess(
        user_id="user-a", shares=shares, dominant_category=ApplicationCategory.GAMING
    )
    assert result.assessment.source == ConfidenceSource.EMPIRICAL
    assert result.observed_evidence == pytest.approx(4.0)


def test_high_variance_hits_floor_and_monitoring_never_reaches_zero() -> None:
    layer = _layer()
    shares = _single(STEADY_APP, ApplicationCategory.CREATIVE)
    _observe(layer, "user-a", shares, (0.0, 1.0, 0.0, 1.0))
    result = layer.assess(
        user_id="user-a", shares=shares, dominant_category=ApplicationCategory.CREATIVE
    )
    assert result.assessment.source == ConfidenceSource.EMPIRICAL
    assert result.assessment.confidence == layer.config.confidence_floor
    assert result.assessment.confidence > 0


def test_statistics_are_isolated_per_user_and_per_application() -> None:
    layer = _layer()
    shares = _single(STEADY_APP, ApplicationCategory.DEVELOPMENT)
    _observe(layer, "user-a", shares, (0.1, 0.1, 0.1, 0.1))

    assert (
        layer.assess(
            user_id="user-a", shares=shares, dominant_category=ApplicationCategory.DEVELOPMENT
        ).assessment.source
        == ConfidenceSource.EMPIRICAL
    )
    assert (
        layer.assess(
            user_id="user-b", shares=shares, dominant_category=ApplicationCategory.DEVELOPMENT
        ).assessment.source
        == ConfidenceSource.BOOTSTRAP
    )
    assert (
        layer.assess(
            user_id="user-a",
            shares=_single(OTHER_APP, ApplicationCategory.DEVELOPMENT),
            dominant_category=ApplicationCategory.DEVELOPMENT,
        ).assessment.source
        == ConfidenceSource.BOOTSTRAP
    )


# ----------------------------------------------------------------------
# Mixed windows use the whole focus mixture
# ----------------------------------------------------------------------
def test_mixed_window_confidence_blends_both_applications() -> None:
    """A window spanning a switch is not attributed wholly to the dominant app."""

    layer = _layer()
    mixed = _shares(
        (STEADY_APP, ApplicationCategory.PRODUCTIVITY, 0.6),
        (VOLATILE_APP, ApplicationCategory.GAMING, 0.4),
    )
    result = layer.assess(
        user_id="user-a", shares=mixed, dominant_category=ApplicationCategory.PRODUCTIVITY
    )
    # 0.6 * 0.9 (PRODUCTIVITY) + 0.4 * 0.65 (GAMING) = 0.80, strictly between
    # the two bootstrap priors -- the dominant-category-only implementation
    # would have returned 0.9 exactly.
    assert result.assessment.confidence == pytest.approx(0.80)
    assert result.assessment.confidence < 0.9


def test_mixed_window_attributes_genuine_evidence_by_focus_share() -> None:
    layer = _layer()
    mixed = _shares(
        (STEADY_APP, ApplicationCategory.UNKNOWN, 0.5),
        (VOLATILE_APP, ApplicationCategory.UNKNOWN, 0.5),
    )
    _observe(layer, "user-a", mixed, (0.2, 0.2, 0.2, 0.2))
    statistics = layer.statistics()
    # Four windows at half share each => 2.0 of evidence per application, so
    # neither has reached the 4-observation requirement yet.
    assert statistics[("user-a", STEADY_APP)].weight == pytest.approx(2.0)
    assert statistics[("user-a", VOLATILE_APP)].weight == pytest.approx(2.0)
    assert (
        layer.assess(
            user_id="user-a", shares=mixed, dominant_category=ApplicationCategory.UNKNOWN
        ).assessment.source
        != ConfidenceSource.EMPIRICAL
    )


def test_zero_fraction_shares_are_ignored() -> None:
    layer = _layer()
    shares = _shares(
        (STEADY_APP, ApplicationCategory.PRODUCTIVITY, 1.0),
        (VOLATILE_APP, ApplicationCategory.GAMING, 0.0),
    )
    _observe(layer, "user-a", shares, (0.2,))
    assert ("user-a", VOLATILE_APP) not in layer.statistics()


# ----------------------------------------------------------------------
# Adjustment modes
# ----------------------------------------------------------------------
def test_damping_mode_scales_the_fused_score_by_confidence() -> None:
    layer = _layer(adjustment_mode=ContextAdjustmentMode.MULTIPLICATIVE_DAMPING)
    shares = _single(VOLATILE_APP, ApplicationCategory.GAMING)
    result = layer.assess(
        user_id="user-a", shares=shares, dominant_category=ApplicationCategory.GAMING
    )
    assessment = result.assessment
    assert not assessment.normalization_available
    assert assessment.adjust(0.8) == pytest.approx(0.8 * assessment.confidence)


def test_normalization_recentres_a_context_onto_the_profile_baseline() -> None:
    """The chosen mode maps an in-context score onto the whole-profile scale."""

    layer = _layer()
    steady = _single(STEADY_APP, ApplicationCategory.UNKNOWN)
    volatile = _single(VOLATILE_APP, ApplicationCategory.UNKNOWN)
    # A calm baseline, then a noisy context centred much higher.
    _observe(layer, "user-a", steady, (0.10, 0.12, 0.10, 0.12))
    _observe(layer, "user-a", volatile, (0.50, 0.70, 0.50, 0.70))

    assessment = layer.assess(
        user_id="user-a", shares=volatile, dominant_category=ApplicationCategory.UNKNOWN
    ).assessment
    assert assessment.normalization_available
    # A score sitting at the centre of the volatile context is ordinary for
    # that context, so it must land near the profile baseline rather than
    # near 0.6.
    assert assessment.adjust(0.60) < 0.45


def test_normalization_preserves_impostor_separation() -> None:
    """Re-centring must not flatten a genuinely deviant score."""

    layer = _layer()
    volatile = _single(VOLATILE_APP, ApplicationCategory.UNKNOWN)
    _observe(layer, "user-a", volatile, (0.40, 0.60, 0.40, 0.60))
    assessment = layer.assess(
        user_id="user-a", shares=volatile, dominant_category=ApplicationCategory.UNKNOWN
    ).assessment
    typical = assessment.adjust(0.50)
    deviant = assessment.adjust(0.95)
    assert deviant > typical


def test_normalization_falls_back_to_damping_without_enough_evidence() -> None:
    layer = _layer()
    shares = _single(STEADY_APP, ApplicationCategory.PRODUCTIVITY)
    assessment = layer.assess(
        user_id="user-a", shares=shares, dominant_category=ApplicationCategory.PRODUCTIVITY
    ).assessment
    assert not assessment.normalization_available
    assert assessment.adjust(0.8) == pytest.approx(0.8 * assessment.confidence)


def test_adjusted_score_stays_inside_the_contract_range() -> None:
    """``smoothed_score`` is contract-bounded to [0, 1]; adjustment must respect it."""

    layer = _layer()
    volatile = _single(VOLATILE_APP, ApplicationCategory.UNKNOWN)
    _observe(layer, "user-a", volatile, (0.50, 0.501, 0.50, 0.501))
    assessment = layer.assess(
        user_id="user-a", shares=volatile, dominant_category=ApplicationCategory.UNKNOWN
    ).assessment
    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert 0.0 <= assessment.adjust(value) <= 1.0


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def test_invalid_genuine_observations_are_rejected() -> None:
    layer = _layer()
    with pytest.raises(ValueError, match="finite"):
        layer.observe_genuine(
            user_id="user-a",
            shares=_single(STEADY_APP, ApplicationCategory.SYSTEM),
            risk_score=float("nan"),
        )


def test_blank_user_identifiers_are_rejected() -> None:
    layer = _layer()
    with pytest.raises(ValueError, match="must not be blank"):
        layer.assess(
            user_id="  ",
            shares=_single(STEADY_APP),
            dominant_category=ApplicationCategory.UNKNOWN,
        )
    with pytest.raises(ValueError, match="must not be blank"):
        layer.observe_genuine(user_id="", shares=_single(STEADY_APP), risk_score=0.2)
