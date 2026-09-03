"""Context adjustment comparison: multiplicative damping vs per-context normalization.

Purpose
-------
The risk engine has always attenuated an already-fused score by a context
confidence in ``[0, 1]``. That is a *one-directional* correction: it lowers the
score of a genuine window in a behaviourally noisy context (good, fewer false
rejects) and, by exactly the same factor, lowers the score of an *impostor*
window in that context (bad, more false accepts). Nothing in the repository
measured that second effect, because no evaluation path is stratified by
context.

This module measures it, and compares damping against a per-context
normalization that maps a score off the genuine distribution observed in its
own context and onto the user profile-wide genuine distribution.

Standing of the result
----------------------
**This is a mechanism study, not an accuracy claim.** It runs on synthetic
users with *deliberately injected* context-conditional behavioural variance,
because that variance is the precise condition the adjustment exists to
correct and the repository's own synthetic generator does not produce it
(``generate_context_stream`` emits categories but the keyboard/mouse
parameters do not depend on them). No number produced here is a property of
any real user, and none may be reported as system accuracy (``PLAN.md``
Section 9.6, ``docs/evaluation.md``).

What it *is* valid for is choosing between two formulas on a correctness
basis: under the condition both are designed to handle, which one preserves
the separation between genuine and impostor evidence? That is a design
question, and it is answerable on synthetic data.

Recorded outcome
----------------
Stable across three cohorts (4 users/5 days, 5/6, 6/7); GAMING is the
high-variance context, PRODUCTIVITY the steady one::

    mode                        EER      GAMING FAR/FRR    PROD FAR/FRR
    NO_CONTEXT_ADJUSTMENT       0.070-0.090   0.04 / 0.17-0.22   0.09-0.12 / 0.02
    MULTIPLICATIVE_DAMPING      0.139-0.176   0.01 / 0.39-0.48   0.20-0.26 / 0.02
    PER_CONTEXT_NORMALIZATION   0.057-0.062   0.18 / 0.05        0.00 / 0.06

Three conclusions drove ``adjustment_mode: PER_CONTEXT_NORMALIZATION`` in
``config/context.development.yaml``:

1. **Damping is worse than doing nothing.** It applies a *different* constant
   factor per context, which pushes the two contexts' score ranges apart
   rather than aligning them. A single global threshold then fits neither, so
   overall EER roughly doubles against the unadjusted baseline. It also does
   not even achieve its stated goal -- GAMING false rejects rise, because the
   global threshold has to fall far enough to still catch the now-compressed
   GAMING scores, and that collapse drives PRODUCTIVITY false accepts from
   ~0.10 to ~0.23.

2. **Normalization fixes the false-anomaly problem it was meant to fix.**
   GAMING false rejects fall from ~0.19 to ~0.05 while PRODUCTIVITY false
   accepts fall to ~0.00, and overall EER improves on the unadjusted baseline
   rather than degrading it.

3. **Its cost is honest and localised.** GAMING false accepts rise (~0.04 ->
   ~0.17). Dividing by a large in-context standard deviation is what removes
   the false rejects, and it necessarily compresses impostor deviation in that
   same context. A genuinely noisy context carries less identity evidence;
   normalization surfaces that instead of hiding it behind a blanket discount,
   and the confidence value is still reported on every decision so a risk
   policy can require more sustained evidence there.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np

from backend.app.risk.context import ContextConfidenceLayer
from ml.datasets.synthetic import (
    SyntheticUserProfile,
    _SeqCounter,
    generate_keyboard_stream,
    generate_mouse_stream,
)
from ml.evaluation.metrics import compute_eer, compute_far_frr
from ml.features.config import MLConfig
from ml.features.extractor import extract_windows
from ml.features.schema import AppCategory, ContextEvent, FeatureWindow, Provenance
from ml.training.common import ModelArtifact, score_window
from ml.training.isolation_forest import train_user_modality_isolation_forest
from protocol.generated.python.contracts import (
    AppFocusShare,
    ContextAdjustmentMode,
    ContextConfig,
)

#: Applications the synthetic users move between. The identifiers are opaque
#: integers exactly as the collector's ``stable_app_id`` produces them; no
#: process name is involved anywhere in this study.
_STEADY_APP = (101, AppCategory.PRODUCTIVITY)
_VOLATILE_APP = (202, AppCategory.GAMING)

#: Multiplier applied to a profile's timing spread inside the volatile
#: application. This is the injected condition under test: the same person
#: behaves far less consistently here, so their own genuine windows drift
#: toward the anomalous end of their enrollment distribution.
_VOLATILE_SPREAD_MULTIPLIER = 3.0


@dataclass(frozen=True)
class StratifiedRates:
    """FAR/FRR for one context at one operating threshold."""

    category: str
    genuine_count: int
    impostor_count: int
    far: float
    frr: float


@dataclass(frozen=True)
class ModeResult:
    """Outcome for one adjustment mode across all contexts."""

    mode: str
    eer: float
    eer_threshold: float
    overall_far: float
    overall_frr: float
    per_context: tuple[StratifiedRates, ...]

    def context(self, category: str) -> StratifiedRates:
        for row in self.per_context:
            if row.category == category:
                return row
        raise KeyError(category)


@dataclass(frozen=True)
class ScoredWindow:
    """A fused risk score with the context it was produced in."""

    user_id: str
    app_id: int
    category: AppCategory
    fused: float
    genuine: bool


# ----------------------------------------------------------------------
# Corpus generation (context-conditional behaviour)
# ----------------------------------------------------------------------
def _profile_for_app(profile: SyntheticUserProfile, app_id: int) -> SyntheticUserProfile:
    """Widen the profile's timing spread inside the volatile application."""

    if app_id != _VOLATILE_APP[0]:
        return profile
    return replace(
        profile,
        std_dwell_us=profile.std_dwell_us * _VOLATILE_SPREAD_MULTIPLIER,
        std_dd_latency_us=profile.std_dd_latency_us * _VOLATILE_SPREAD_MULTIPLIER,
        std_mouse_speed_px_s=profile.std_mouse_speed_px_s * _VOLATILE_SPREAD_MULTIPLIER,
    )


def _segment_windows(
    profile: SyntheticUserProfile,
    *,
    seed: int,
    app: tuple[int, AppCategory],
    collection_day: str,
    session_id: str,
    segment_id: str,
    duration_us: int,
    t_start_us: int,
    config: MLConfig,
) -> list[FeatureWindow]:
    """One single-application segment, so focus attribution is unambiguous."""

    app_id, category = app
    rng = np.random.default_rng(seed)
    seq = _SeqCounter()
    shaped = _profile_for_app(profile, app_id)

    approx_keystrokes = max(1, int(duration_us / max(shaped.mean_dd_latency_us, 1.0)))
    kbd = generate_keyboard_stream(
        shaped, rng, n_keystrokes=approx_keystrokes, t_start_us=t_start_us, seq=seq, app_id=app_id
    )
    kbd = [e for e in kbd if e.t_capture_us <= t_start_us + duration_us]
    mouse = generate_mouse_stream(
        shaped, rng, duration_us=duration_us, t_start_us=t_start_us, seq=seq, app_id=app_id
    )
    context = [
        ContextEvent(t_capture_us=t_start_us, app_id=app_id, category=category, seq=seq.next())
    ]
    return extract_windows(
        kbd,
        mouse,
        context,
        user_id=profile.user_id,
        session_id=session_id,
        segment_id=segment_id,
        collection_day=collection_day,
        provenance=Provenance.SYNTHETIC,
        config=config,
    )


def build_corpus(
    profile: SyntheticUserProfile,
    *,
    base_seed: int,
    num_days: int,
    config: MLConfig,
    segment_duration_us: int = 20 * 60 * 1_000_000,
) -> list[FeatureWindow]:
    """Multi-day corpus alternating between a steady and a volatile application."""

    base_date = date(2026, 1, 1)
    windows: list[FeatureWindow] = []
    for day_index in range(num_days):
        collection_day = (base_date + timedelta(days=day_index)).isoformat()
        for segment_index, app in enumerate((_STEADY_APP, _VOLATILE_APP, _STEADY_APP)):
            t_start = (day_index * 8 + segment_index) * segment_duration_us
            windows.extend(
                _segment_windows(
                    profile,
                    seed=base_seed + day_index * 100 + segment_index,
                    app=app,
                    collection_day=collection_day,
                    session_id=f"{profile.user_id}-s{day_index}",
                    segment_id=f"{profile.user_id}-s{day_index}-{segment_index}",
                    duration_us=segment_duration_us,
                    t_start_us=t_start,
                    config=config,
                )
            )
    return windows


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------
def _fused_risk(
    keyboard: ModelArtifact, mouse: ModelArtifact, window: FeatureWindow
) -> float | None:
    """Reproduce the live keyboard/mouse fusion and its normality->risk inversion.

    Mirrors ``RiskEngine._fusion`` (availability-renormalised weighted mean of
    calibrated scores) and ``backend/app/models/service.py`` (percentile
    normality -> ``1 - pct/100`` risk). Modality weights are equal here, as in
    ``config/risk.development.yaml``.
    """

    parts: list[float] = []
    for artifact in (keyboard, mouse):
        try:
            result = score_window(artifact, window)
        except ValueError:
            continue
        if result.available and result.percentile_score is not None:
            parts.append(1.0 - result.percentile_score / 100.0)
    if not parts:
        return None
    return float(sum(parts) / len(parts))


def _dominant_app(window: FeatureWindow) -> tuple[int, AppCategory]:
    share = max(window.context.app_shares, key=lambda item: item.fraction)
    return share.app_id, share.category


def score_population(
    artifacts: dict[str, tuple[ModelArtifact, ModelArtifact]],
    test_windows: dict[str, list[FeatureWindow]],
) -> list[ScoredWindow]:
    """Score every user's model against its own and every other user's windows."""

    scored: list[ScoredWindow] = []
    for owner, (keyboard, mouse) in artifacts.items():
        for subject, windows in test_windows.items():
            for window in windows:
                fused = _fused_risk(keyboard, mouse, window)
                if fused is None:
                    continue
                app_id, category = _dominant_app(window)
                scored.append(
                    ScoredWindow(owner, app_id, category, fused, genuine=subject == owner)
                )
    return scored


# ----------------------------------------------------------------------
# Adjustment simulation
# ----------------------------------------------------------------------
def _warm_layer(
    config: ContextConfig,
    artifacts: dict[str, tuple[ModelArtifact, ModelArtifact]],
    train_windows: dict[str, list[FeatureWindow]],
) -> ContextConfidenceLayer:
    """Accumulate genuine statistics the way the orchestrator does at runtime.

    Enrollment windows are genuine by construction, which is the offline
    equivalent of the live LOW-risk gate in ``RuntimeOrchestrator``.
    """

    layer = ContextConfidenceLayer(config)
    for user_id, (keyboard, mouse) in artifacts.items():
        for window in train_windows[user_id]:
            fused = _fused_risk(keyboard, mouse, window)
            if fused is None:
                continue
            layer.observe_genuine(
                user_id=user_id, shares=window.context.app_shares, risk_score=fused
            )
    return layer


def _adjusted_scores(
    layer: ContextConfidenceLayer, scored: Sequence[ScoredWindow]
) -> list[tuple[ScoredWindow, float]]:
    """Assess and adjust each scored window through the live confidence layer."""

    adjusted: list[tuple[ScoredWindow, float]] = []
    for item in scored:
        # Segments are single-application, so one share carries the window.
        shares = [AppFocusShare(app_id=item.app_id, category=item.category, fraction=1.0)]
        trace = layer.assess(user_id=item.user_id, shares=shares, dominant_category=item.category)
        adjusted.append((item, trace.assessment.adjust(item.fused)))
    return adjusted


def _rates(
    adjusted: Sequence[tuple[ScoredWindow, float]], threshold: float
) -> tuple[float, float, tuple[StratifiedRates, ...]]:
    """Overall and per-context FAR/FRR at one operating threshold.

    Convention here is the risk convention (higher = more anomalous), so an
    impostor is falsely accepted when its adjusted score stays *below* the
    threshold and a genuine window is falsely rejected when it rises above.
    """

    genuine = [value for item, value in adjusted if item.genuine]
    impostor = [value for item, value in adjusted if not item.genuine]
    overall_far = float(np.mean([v < threshold for v in impostor])) if impostor else 0.0
    overall_frr = float(np.mean([v >= threshold for v in genuine])) if genuine else 0.0

    rows: list[StratifiedRates] = []
    for category in sorted({item.category.value for item, _ in adjusted}):
        g = [v for item, v in adjusted if item.genuine and item.category.value == category]
        i = [v for item, v in adjusted if not item.genuine and item.category.value == category]
        rows.append(
            StratifiedRates(
                category=category,
                genuine_count=len(g),
                impostor_count=len(i),
                far=float(np.mean([v < threshold for v in i])) if i else float("nan"),
                frr=float(np.mean([v >= threshold for v in g])) if g else float("nan"),
            )
        )
    return overall_far, overall_frr, tuple(rows)


def evaluate_mode(
    mode: ContextAdjustmentMode | None,
    base_config: ContextConfig,
    artifacts: dict[str, tuple[ModelArtifact, ModelArtifact]],
    train_windows: dict[str, list[FeatureWindow]],
    scored: Sequence[ScoredWindow],
) -> ModeResult:
    """Evaluate one adjustment mode; ``None`` means no context handling at all."""

    if mode is None:
        adjusted = [(item, item.fused) for item in scored]
        label = "NO_CONTEXT_ADJUSTMENT"
    else:
        config = base_config.model_copy(update={"adjustment_mode": mode})
        layer = _warm_layer(config, artifacts, train_windows)
        adjusted = _adjusted_scores(layer, scored)
        label = mode.value

    # EER is computed on the normality convention used by ml/evaluation/metrics
    # (accept iff score >= threshold), so risk scores are inverted for it.
    genuine = np.asarray([1.0 - value for item, value in adjusted if item.genuine])
    impostor = np.asarray([1.0 - value for item, value in adjusted if not item.genuine])
    eer = compute_eer(genuine, impostor)
    far_frr = compute_far_frr(genuine, impostor, eer.threshold)
    _, _, per_context = _rates(adjusted, 1.0 - eer.threshold)
    return ModeResult(
        mode=label,
        eer=eer.eer,
        eer_threshold=1.0 - eer.threshold,
        overall_far=far_frr.far,
        overall_frr=far_frr.frr,
        per_context=per_context,
    )


# ----------------------------------------------------------------------
# Study driver
# ----------------------------------------------------------------------
def run_comparison(
    *,
    ml_config: MLConfig,
    context_config: ContextConfig,
    num_users: int = 6,
    num_days: int = 6,
    holdout_days: int = 2,
    segment_duration_us: int = 20 * 60 * 1_000_000,
) -> list[ModeResult]:
    """Train per-user models on a day-disjoint split and compare adjustment modes."""

    profiles = [
        SyntheticUserProfile(
            user_id=f"user-{index:02d}",
            mean_dwell_us=80_000.0 + index * 9_000.0,
            std_dwell_us=12_000.0 + index * 900.0,
            mean_dd_latency_us=180_000.0 + index * 18_000.0,
            std_dd_latency_us=38_000.0 + index * 2_600.0,
            mean_mouse_speed_px_s=650.0 + index * 110.0,
            std_mouse_speed_px_s=160.0 + index * 18.0,
        )
        for index in range(num_users)
    ]

    base_date = date(2026, 1, 1)
    test_days = {
        (base_date + timedelta(days=offset)).isoformat()
        for offset in range(num_days - holdout_days, num_days)
    }

    train_windows: dict[str, list[FeatureWindow]] = {}
    test_windows: dict[str, list[FeatureWindow]] = {}
    artifacts: dict[str, tuple[ModelArtifact, ModelArtifact]] = {}

    for index, profile in enumerate(profiles):
        corpus = build_corpus(
            profile,
            base_seed=7_000 + index * 977,
            num_days=num_days,
            config=ml_config,
            segment_duration_us=segment_duration_us,
        )
        train = [w for w in corpus if w.collection_day not in test_days]
        test = [w for w in corpus if w.collection_day in test_days]
        # Day-disjoint by construction; assert rather than trust.
        assert not ({w.collection_day for w in train} & {w.collection_day for w in test})
        train_windows[profile.user_id] = train
        test_windows[profile.user_id] = test
        artifacts[profile.user_id] = (
            train_user_modality_isolation_forest(profile.user_id, "keyboard", train, ml_config),
            train_user_modality_isolation_forest(profile.user_id, "mouse", train, ml_config),
        )

    scored = score_population(artifacts, test_windows)
    return [
        evaluate_mode(mode, context_config, artifacts, train_windows, scored)
        for mode in (
            None,
            ContextAdjustmentMode.MULTIPLICATIVE_DAMPING,
            ContextAdjustmentMode.PER_CONTEXT_NORMALIZATION,
        )
    ]


def format_report(results: Sequence[ModeResult]) -> str:
    lines = [
        "Context adjustment comparison -- MECHANISM STUDY ON SYNTHETIC DATA.",
        "Not an accuracy claim; no number here describes any real user.",
        "",
        f"{'mode':<32}{'EER':>9}{'FAR':>9}{'FRR':>9}",
    ]
    for result in results:
        lines.append(
            f"{result.mode:<32}{result.eer:>9.4f}{result.overall_far:>9.4f}"
            f"{result.overall_frr:>9.4f}"
        )
    lines.append("")
    lines.append("Per-context rates at each mode's own EER threshold:")
    lines.append(f"{'mode':<32}{'context':<16}{'FAR':>9}{'FRR':>9}{'n_gen':>8}{'n_imp':>8}")
    for result in results:
        for row in result.per_context:
            far = "n/a" if math.isnan(row.far) else f"{row.far:.4f}"
            frr = "n/a" if math.isnan(row.frr) else f"{row.frr:.4f}"
            lines.append(
                f"{result.mode:<32}{row.category:<16}{far:>9}{frr:>9}"
                f"{row.genuine_count:>8}{row.impostor_count:>8}"
            )
    return "\n".join(lines)


__all__ = [
    "ModeResult",
    "ScoredWindow",
    "StratifiedRates",
    "build_corpus",
    "evaluate_mode",
    "format_report",
    "run_comparison",
    "score_population",
]
