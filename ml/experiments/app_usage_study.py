"""Phase 0 offline study: is application-usage composition an identity signal?

Scope and isolation
-------------------
This module is **offline analysis only**. It imports nothing from the risk
engine or the fusion path, it is not reachable from ``RuntimeOrchestrator``,
and nothing here may be wired into a live decision. Its entire purpose is to
answer, before any integration is contemplated, whether a session-level
application-usage vector carries enough per-user signal to be worth pursuing.

What is deliberately *not* modelled
-----------------------------------
* **No clock position.** ``AGENTS.md`` constraint 4 and guardrail
  ``G04_TEMPORAL_IDENTITY`` forbid absolute temporal features, and the
  app-aware architecture reference (section 14) agrees. Every feature here is
  compositional (what proportion of focus went where) or elapsed-duration
  based (how long a stretch of focus lasted). "User X browses at 9am" is not
  representable and must not become representable.
* **No sub-application identity.** The collector observes an executable only.
  Two web properties inside one browser are the *same* application here, and
  separating them would require a URL or window title, which is prohibited by
  ``AGENTS.md`` constraint 2 and guardrails ``G01``/``G02``. Any usage signal
  is therefore process-level and category-level, never site-level.
* **No per-application model.** Applications enter only as opaque
  ``app_id`` values inside aggregate statistics. A new application changes a
  vector component, never a code path.

Data provenance rule
--------------------
``assess_corpus`` reports what is actually available before anything is
trained. If real logged data is insufficient, the correct outcome is to defer,
**not** to substitute synthetic data and report a number. Synthetic runs are
supported for one purpose only -- verifying that this code executes end to end
-- and every result carries a ``plumbing_check`` flag saying so.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ml.calibration.percentile import PercentileCalibrator
from ml.evaluation.metrics import compute_eer, compute_far_frr
from ml.features.config import MLConfig
from ml.features.schema import AppCategory, FeatureWindow
from ml.training.common import PreprocessingParams
from ml.training.model_wrappers import IsolationForestWrapper

# ----------------------------------------------------------------------
# Sufficiency policy
# ----------------------------------------------------------------------
#: A cross-user impostor design needs a cohort; below this the false-accept
#: denominator is too small for the rate to mean anything.
MIN_USERS = 5
#: Day-disjoint splitting is mandatory (``ml/evaluation/splitting.py``). At
#: session granularity the day is still the split unit, so a usable study needs
#: enough days to hold some out and still leave a profile behind.
MIN_DAYS_PER_USER = 20
#: Sessions are the sample unit; this is the per-user sample size.
MIN_SESSIONS_PER_USER = 40
#: Aggregating a session from a handful of samples yields a vector dominated by
#: sampling noise rather than habit.
MIN_SAMPLES_PER_SESSION = 10
#: Below this share of non-UNKNOWN focus time the category-composition block is
#: constant and only the application-level block carries any information.
MIN_CATEGORISED_FRACTION = 0.5

USAGE_FEATURE_NAMES: tuple[str, ...] = (
    *(f"category_share_{category.value.lower()}" for category in AppCategory),
    "category_entropy",
    "switch_rate_mean",
    "switch_rate_dispersion",
    "distinct_app_density",
    "app_concentration",
    "dominant_app_share",
    "mean_focus_run_length",
    "transition_entropy",
    "session_extent",
)


# ----------------------------------------------------------------------
# Corpus inspection
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class UserCoverage:
    user_id: str
    distinct_days: int
    sessions: int
    samples: int
    categorised_fraction: float
    distinct_apps: int


@dataclass(frozen=True)
class CorpusAssessment:
    """What real logged data exists, and whether it can support the study."""

    source: str
    exists: bool
    users: tuple[UserCoverage, ...] = ()
    blocking_reasons: tuple[str, ...] = ()

    @property
    def sufficient(self) -> bool:
        return self.exists and not self.blocking_reasons

    def report(self) -> str:
        lines = [f"Corpus: {self.source}"]
        if not self.exists:
            lines.append("  STATUS: no stored corpus found.")
        else:
            lines.append(f"  users: {len(self.users)}")
            header = (
                f"  {'user':<20}{'days':>6}{'sessions':>10}"
                f"{'samples':>9}{'apps':>6}{'categorised':>13}"
            )
            lines.append(header)
            for user in self.users:
                lines.append(
                    f"  {user.user_id:<20}{user.distinct_days:>6}{user.sessions:>10}"
                    f"{user.samples:>9}{user.distinct_apps:>6}"
                    f"{user.categorised_fraction:>13.3f}"
                )
        lines.append(f"  SUFFICIENT FOR A REPORTABLE RESULT: {self.sufficient}")
        for reason in self.blocking_reasons:
            lines.append(f"    - {reason}")
        return "\n".join(lines)


def _coverage_from_windows(windows: Sequence[FeatureWindow]) -> tuple[UserCoverage, ...]:
    by_user: dict[str, list[FeatureWindow]] = {}
    for item in windows:
        by_user.setdefault(item.user_id, []).append(item)

    coverage: list[UserCoverage] = []
    for user_id, owned in sorted(by_user.items()):
        categorised = 0.0
        apps: set[int] = set()
        for item in owned:
            for share in item.context.app_shares:
                apps.add(share.app_id)
                if share.category != AppCategory.UNKNOWN:
                    categorised += share.fraction
        coverage.append(
            UserCoverage(
                user_id=user_id,
                distinct_days=len({item.collection_day for item in owned}),
                sessions=len({item.session_id for item in owned}),
                samples=len(owned),
                categorised_fraction=categorised / len(owned) if owned else 0.0,
                distinct_apps=len(apps),
            )
        )
    return tuple(coverage)


def _blocking_reasons(users: Sequence[UserCoverage]) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(users) < MIN_USERS:
        reasons.append(
            f"{len(users)} enrolled user(s); a cross-user impostor design needs >= {MIN_USERS}"
        )
    for user in users:
        if user.distinct_days < MIN_DAYS_PER_USER:
            reasons.append(
                f"{user.user_id}: {user.distinct_days} distinct day(s), "
                f"need >= {MIN_DAYS_PER_USER} for a day-disjoint session-level split"
            )
        if user.sessions < MIN_SESSIONS_PER_USER:
            reasons.append(
                f"{user.user_id}: {user.sessions} session(s), need >= {MIN_SESSIONS_PER_USER}"
            )
        if user.categorised_fraction < MIN_CATEGORISED_FRACTION:
            reasons.append(
                f"{user.user_id}: only {user.categorised_fraction:.2f} of focus time carries a "
                f"category (need >= {MIN_CATEGORISED_FRACTION}); the category-composition block "
                "is uninformative on this data"
            )
    return tuple(reasons)


def assess_corpus(database_path: Path | str) -> CorpusAssessment:
    """Inspect the live storage database without training anything.

    Reads only the aggregate ``feature_windows`` table. No raw events are
    stored by this system, so there is nothing else to inspect.
    """

    path = Path(database_path)
    if not path.exists():
        return CorpusAssessment(
            source=str(path),
            exists=False,
            blocking_reasons=("no storage database exists at this path",),
        )

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT user_id, session_id, collection_day, context_json FROM feature_windows"
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        return CorpusAssessment(
            source=str(path),
            exists=True,
            blocking_reasons=("storage database exists but holds zero feature windows",),
        )

    coverage: list[UserCoverage] = []
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["user_id"], []).append(row)
    for user_id, owned in sorted(grouped.items()):
        categorised = 0.0
        apps: set[int] = set()
        for row in owned:
            context = json.loads(row["context_json"])
            for share in context.get("app_shares", []):
                apps.add(int(share["app_id"]))
                if share["category"] != AppCategory.UNKNOWN.value:
                    categorised += float(share["fraction"])
        coverage.append(
            UserCoverage(
                user_id=user_id,
                distinct_days=len({row["collection_day"] for row in owned}),
                sessions=len({row["session_id"] for row in owned}),
                samples=len(owned),
                categorised_fraction=categorised / len(owned),
                distinct_apps=len(apps),
            )
        )
    users = tuple(coverage)
    return CorpusAssessment(
        source=str(path), exists=True, users=users, blocking_reasons=_blocking_reasons(users)
    )


def assess_windows(windows: Sequence[FeatureWindow], *, source: str) -> CorpusAssessment:
    """Same sufficiency policy, applied to an in-memory corpus."""

    users = _coverage_from_windows(windows)
    return CorpusAssessment(
        source=source, exists=bool(windows), users=users, blocking_reasons=_blocking_reasons(users)
    )


# ----------------------------------------------------------------------
# Session-level usage vectors
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class UsageSample:
    user_id: str
    session_id: str
    collection_day: str
    features: dict[str, float] = field(default_factory=dict)

    def vector(self) -> list[float]:
        return [self.features[name] for name in USAGE_FEATURE_NAMES]


def _entropy(values: Iterable[float]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    parts = [value / total for value in values if value > 0]
    if len(parts) <= 1:
        return 0.0
    raw = -sum(part * math.log(part) for part in parts)
    return raw / math.log(len(parts)) if len(parts) > 1 else 0.0


def _dominant_app(item: FeatureWindow) -> int:
    return max(item.context.app_shares, key=lambda share: share.fraction).app_id


def build_usage_sample(user_id: str, session: Sequence[FeatureWindow]) -> UsageSample:
    """Aggregate one session's windows into a single usage vector.

    All components are proportions, dispersions, or elapsed-duration
    statistics. None encodes when the session happened.
    """

    ordered = sorted(session, key=lambda item: item.t_start_us)
    count = len(ordered)

    category_time: dict[str, float] = {}
    app_time: dict[int, float] = {}
    for item in ordered:
        for category, fraction in item.context.category_fractions.items():
            category_time[category] = category_time.get(category, 0.0) + fraction
        for share in item.context.app_shares:
            app_time[share.app_id] = app_time.get(share.app_id, 0.0) + share.fraction

    total_category = sum(category_time.values()) or 1.0
    total_app = sum(app_time.values()) or 1.0

    features: dict[str, float] = {
        f"category_share_{category.value.lower()}": category_time.get(category.value, 0.0)
        / total_category
        for category in AppCategory
    }
    features["category_entropy"] = _entropy(category_time.values())

    switch_rates = np.asarray([item.context.app_switch_rate for item in ordered], dtype=float)
    features["switch_rate_mean"] = float(switch_rates.mean()) if count else 0.0
    features["switch_rate_dispersion"] = float(switch_rates.std()) if count else 0.0

    features["distinct_app_density"] = len(app_time) / count if count else 0.0
    shares = np.asarray([value / total_app for value in app_time.values()], dtype=float)
    features["app_concentration"] = float((shares**2).sum()) if shares.size else 0.0
    features["dominant_app_share"] = float(shares.max()) if shares.size else 0.0

    # Mean length of an uninterrupted run of focus on one application,
    # expressed in samples so it stays a duration statistic rather than a
    # clock position.
    runs: list[int] = []
    previous: int | None = None
    for item in ordered:
        current = _dominant_app(item)
        if current == previous and runs:
            runs[-1] += 1
        else:
            runs.append(1)
        previous = current
    features["mean_focus_run_length"] = (sum(runs) / len(runs)) / count if runs and count else 0.0

    transitions: Counter[tuple[str, str]] = Counter()
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        transitions[
            (earlier.context.dominant_category.value, later.context.dominant_category.value)
        ] += 1
    features["transition_entropy"] = _entropy(transitions.values())

    # Elapsed extent of the session, log-compressed. Duration, not position.
    features["session_extent"] = math.log1p(count)

    return UsageSample(
        user_id=user_id,
        session_id=ordered[0].session_id,
        collection_day=ordered[0].collection_day,
        features=features,
    )


def build_usage_samples(windows: Sequence[FeatureWindow]) -> list[UsageSample]:
    grouped: dict[tuple[str, str], list[FeatureWindow]] = {}
    for item in windows:
        grouped.setdefault((item.user_id, item.session_id), []).append(item)
    samples = [
        build_usage_sample(user_id, session)
        for (user_id, _), session in sorted(grouped.items())
        if len(session) >= MIN_SAMPLES_PER_SESSION
    ]
    return samples


# ----------------------------------------------------------------------
# Per-user one-class model over usage vectors
# ----------------------------------------------------------------------
@dataclass
class UsageProfile:
    user_id: str
    preprocessing: PreprocessingParams
    calibration: PercentileCalibrator
    model: IsolationForestWrapper

    def score(self, sample: UsageSample) -> float:
        matrix = np.asarray([sample.vector()], dtype=float)
        raw = float(self.model.normality_score(self.preprocessing.transform(matrix))[0])
        return float(self.calibration.transform(np.asarray([raw]))[0])


def fit_usage_profile(user_id: str, samples: Sequence[UsageSample]) -> UsageProfile:
    matrix = np.asarray([sample.vector() for sample in samples], dtype=float)
    preprocessing = PreprocessingParams.fit(matrix)
    model = IsolationForestWrapper(
        n_estimators=100, contamination="auto", max_samples="auto", random_state=42
    )
    model.fit(preprocessing.transform(matrix))
    calibration = PercentileCalibrator.fit(model.normality_score(preprocessing.transform(matrix)))
    return UsageProfile(user_id, preprocessing, calibration, model)


# ----------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class UsageStudyResult:
    """Standalone performance of the usage signal. Never merged with a
    keyboard/mouse result -- the sample unit and denominator differ."""

    plumbing_check: bool
    users: int
    train_sessions: int
    test_sessions: int
    eer: float
    eer_threshold: float
    far: float
    frr: float
    auc: float

    def report(self) -> str:
        banner = (
            "PLUMBING CHECK ONLY -- synthetic data, NOT a finding"
            if self.plumbing_check
            else "Result on real logged data"
        )
        return "\n".join(
            [
                banner,
                f"  users={self.users} train_sessions={self.train_sessions} "
                f"test_sessions={self.test_sessions}",
                f"  session-level EER={self.eer:.4f} AUC={self.auc:.4f}",
                f"  at EER threshold: FAR={self.far:.4f} FRR={self.frr:.4f}",
                "  Denominator is one decision per session; these rates are NOT",
                "  comparable to per-window keyboard/mouse rates and must be",
                "  reported on their own axis.",
            ]
        )


def _auc(genuine: np.ndarray, impostor: np.ndarray) -> float:
    if genuine.size == 0 or impostor.size == 0:
        return float("nan")
    combined = np.concatenate([genuine, impostor])
    order = combined.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, combined.size + 1, dtype=float)
    genuine_ranks = ranks[: genuine.size].sum()
    return float(
        (genuine_ranks - genuine.size * (genuine.size + 1) / 2) / (genuine.size * impostor.size)
    )


def run_study(
    windows: Sequence[FeatureWindow],
    *,
    test_days: Sequence[str],
    plumbing_check: bool,
) -> UsageStudyResult:
    """Day-disjoint, cross-user session-level evaluation of the usage signal."""

    samples = build_usage_samples(windows)
    holdout = set(test_days)
    train = [sample for sample in samples if sample.collection_day not in holdout]
    test = [sample for sample in samples if sample.collection_day in holdout]
    if not train or not test:
        raise ValueError("day-disjoint split left an empty partition")
    overlap = {s.collection_day for s in train} & {s.collection_day for s in test}
    if overlap:
        raise ValueError(f"train/test partitions share day(s): {sorted(overlap)}")

    train_by_user: dict[str, list[UsageSample]] = {}
    for sample in train:
        train_by_user.setdefault(sample.user_id, []).append(sample)

    genuine: list[float] = []
    impostor: list[float] = []
    profiles = {
        user_id: fit_usage_profile(user_id, owned)
        for user_id, owned in train_by_user.items()
        if len(owned) >= 2
    }
    for user_id, profile in profiles.items():
        for sample in test:
            score = profile.score(sample)
            if sample.user_id == user_id:
                genuine.append(score)
            else:
                impostor.append(score)

    genuine_array = np.asarray(genuine, dtype=float)
    impostor_array = np.asarray(impostor, dtype=float)
    eer = compute_eer(genuine_array, impostor_array)
    rates = compute_far_frr(genuine_array, impostor_array, eer.threshold)
    return UsageStudyResult(
        plumbing_check=plumbing_check,
        users=len(profiles),
        train_sessions=len(train),
        test_sessions=len(test),
        eer=eer.eer,
        eer_threshold=eer.threshold,
        far=rates.far,
        frr=rates.frr,
        auc=_auc(genuine_array, impostor_array),
    )


# ----------------------------------------------------------------------
# Synthetic corpus -- PLUMBING VERIFICATION ONLY
# ----------------------------------------------------------------------
def build_synthetic_corpus(
    *,
    ml_config: MLConfig,
    num_users: int = 6,
    num_days: int = 24,
    sessions_per_day: int = 2,
) -> list[FeatureWindow]:
    """Build a synthetic corpus solely to verify this module executes.

    Users are given deliberately different application mixes, so the pipeline
    has *something* to separate. That makes any resulting number a property of
    the generator, not of human behaviour: it says the code runs, and nothing
    whatsoever about whether real people differ this way. Results built from
    this corpus must always carry ``plumbing_check=True``.
    """

    from datetime import date, timedelta

    import numpy as np

    from ml.datasets.synthetic import (
        SyntheticUserProfile,
        _SeqCounter,
        generate_keyboard_stream,
        generate_mouse_stream,
    )
    from ml.features.extractor import extract_windows
    from ml.features.schema import ContextEvent, Provenance

    catalogue = [
        (11, AppCategory.PRODUCTIVITY),
        (22, AppCategory.BROWSING),
        (33, AppCategory.DEVELOPMENT),
        (44, AppCategory.CREATIVE),
        (55, AppCategory.GAMING),
    ]
    base_date = date(2026, 1, 1)
    segment_us = 6 * 60 * 1_000_000
    windows: list[FeatureWindow] = []

    for index in range(num_users):
        profile = SyntheticUserProfile(
            user_id=f"user-{index:02d}",
            mean_dwell_us=80_000.0 + index * 8_000.0,
            mean_dd_latency_us=180_000.0 + index * 15_000.0,
            mean_mouse_speed_px_s=650.0 + index * 100.0,
        )
        # Each synthetic user favours a different rotation of the catalogue.
        rotation = catalogue[index % len(catalogue) :] + catalogue[: index % len(catalogue)]
        mix = rotation[:3]
        for day_index in range(num_days):
            collection_day = (base_date + timedelta(days=day_index)).isoformat()
            for session_index in range(sessions_per_day):
                session_id = f"{profile.user_id}-d{day_index}-s{session_index}"
                for slot in range(4):
                    app_id, category = mix[(slot + session_index) % len(mix)]
                    seed = 3000 + index * 9001 + day_index * 37 + session_index * 7 + slot
                    rng = np.random.default_rng(seed)
                    seq = _SeqCounter()
                    t_start = (day_index * 100 + session_index * 10 + slot) * segment_us
                    keystrokes = max(1, int(segment_us / profile.mean_dd_latency_us))
                    kbd = generate_keyboard_stream(
                        profile,
                        rng,
                        n_keystrokes=keystrokes,
                        t_start_us=t_start,
                        seq=seq,
                        app_id=app_id,
                    )
                    kbd = [e for e in kbd if e.t_capture_us <= t_start + segment_us]
                    mouse = generate_mouse_stream(
                        profile, rng, duration_us=segment_us, t_start_us=t_start, seq=seq,
                        app_id=app_id,
                    )
                    context = [
                        ContextEvent(
                            t_capture_us=t_start,
                            app_id=app_id,
                            category=category,
                            seq=seq.next(),
                        )
                    ]
                    windows.extend(
                        extract_windows(
                            kbd,
                            mouse,
                            context,
                            user_id=profile.user_id,
                            session_id=session_id,
                            segment_id=f"{session_id}-{slot}",
                            collection_day=collection_day,
                            provenance=Provenance.SYNTHETIC,
                            config=ml_config,
                        )
                    )
    return windows


def data_requirement_summary() -> str:
    """What would have to accumulate before this study can produce a finding."""

    return "\n".join(
        [
            "Minimum corpus for a trustworthy app-usage result:",
            f"  - enrolled users:            >= {MIN_USERS} (cross-user impostor denominator)",
            f"  - distinct days per user:    >= {MIN_DAYS_PER_USER}",
            f"  - sessions per user:         >= {MIN_SESSIONS_PER_USER}",
            f"  - samples per session:       >= {MIN_SAMPLES_PER_SESSION}",
            f"  - non-UNKNOWN focus share:   >= {MIN_CATEGORISED_FRACTION}",
            "",
            "The day count dominates. A session-level signal yields a handful of",
            "samples per day, and the split unit must stay the calendar day, so",
            "days are simultaneously the sample budget and the split budget.",
            "A daily-granularity variant would need substantially more still,",
            "because there the day IS the sample.",
        ]
    )


__all__ = [
    "MIN_DAYS_PER_USER",
    "MIN_SAMPLES_PER_SESSION",
    "MIN_SESSIONS_PER_USER",
    "MIN_USERS",
    "USAGE_FEATURE_NAMES",
    "CorpusAssessment",
    "UsageProfile",
    "UsageSample",
    "UsageStudyResult",
    "UserCoverage",
    "assess_corpus",
    "assess_windows",
    "build_synthetic_corpus",
    "build_usage_sample",
    "build_usage_samples",
    "data_requirement_summary",
    "fit_usage_profile",
    "run_study",
]
