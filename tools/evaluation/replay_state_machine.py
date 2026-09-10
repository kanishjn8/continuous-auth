"""Offline, read-only replay of the risk engine's temporal state machine.

**NOT PRODUCTION CODE.** This is an evaluation/analysis tool only. It must
never be imported by, and is not imported by, any `backend/`, `ml/`, or
`tools/enrollment/` production code path. Nothing in this module is on the
live decision path: it exists purely to answer questions about historical
or frozen-corpus data that the live `RiskEngine`'s own decision log cannot
answer by itself, e.g. "what HIGH-state occupancy rate would the K-of-N
logic actually produce over this chronological sequence of fused scores,
as opposed to the static per-window threshold crossing rate."

It deliberately does not import `backend.app.risk.engine.RiskEngine` --
`replay_risk_levels` below is a from-scratch reimplementation of that
engine's EWMA smoothing, `_candidate_level` breach counting, and
`_with_hysteresis` transition rule (see backend/app/risk/engine.py:97-136
for the source of truth this mirrors). Being a reimplementation rather
than a call into the real engine means it can drift from the real engine
if that file changes; it was verified against real historical
`risk_events` rows recorded by the live engine at the time this module was
written (see docs/genuine-user-validation-2026-09-10-groundtruth.md for
that check: 675/675 real historical decisions matched exactly at the
0.45/0.75 operating point that produced them). If
`backend/app/risk/engine.py`'s state-machine arithmetic ever changes, this
module must be re-verified against a fresh sample of real `risk_events`
before its output is trusted again -- the ground-truth check is not a
one-time proof that survives unrelated engine changes.

Two independent uses live in this file:

1. `replay_risk_levels` -- the reusable core. Takes one fused-risk reading
   per window (or `None` when the window produced no fused score at all,
   e.g. `INSUFFICIENT_DATA` or a required modality that failed to score --
   mirroring `RiskEngine._required_failure` / the `INSUFFICIENT_DATA`
   branch of `RiskEngine.process`, both of which leave history/smoothed
   untouched and record `UNAVAILABLE` for that decision) and returns one
   `ReplayStep` per reading, in the same order.
2. `main` -- a CLI that runs the frozen `pilot-v1` corpus (TRAIN /
   VALIDATION / EVALUATION) for participant `manas-01` through
   `replay_risk_levels` at whatever thresholds `config/risk.development.yaml`
   currently declares, and reports the static per-window crossing rate
   alongside the actual post-K-of-N state distribution. This reproduces
   the numbers in item 6/7 of
   docs/genuine-user-validation-2026-09-10.md. It opens every database
   handle `mode=ro` and never writes, fits, or refits anything.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from backend.app.risk.config import load_risk_settings
from ml.evaluation.fusion import fuse_percentile_scores
from ml.training.common import ModelSchemaMismatchError, score_window
from ml.training.persistence import load_artifact
from tools.collection import corpus as corpus_module

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


@dataclass(frozen=True)
class Reading:
    """One window's evidence, in the units RiskEngine consumes just before EWMA.

    ``fused_score`` is the availability-renormalised fused calibrated risk
    (``RiskEngine._fusion``'s output), or ``None`` when no fused value
    exists for this window at all -- either the window's quality label was
    ``INSUFFICIENT_DATA`` or a modality that quality label requires failed
    to score. Both cases leave the engine's history/smoothed state
    untouched and record ``UNAVAILABLE`` (``RiskEngine.process``,
    ``RiskEngine._required_failure``, ``RiskEngine._failure_outcome``).

    ``context_confidence`` is ``ContextAssessment.confidence`` under the
    default ``MULTIPLICATIVE_DAMPING`` mode (``adjusted = fused *
    confidence``); it defaults to 1.0, a no-op, matching the only mode ever
    observed in this pilot's data (diagnosis.md section 12).
    """

    fused_score: float | None
    context_confidence: float = 1.0


@dataclass(frozen=True)
class ReplayStep:
    smoothed_score: float | None
    risk_level: str  # "UNAVAILABLE" | "LOW" | "MEDIUM" | "HIGH"


def replay_risk_levels(
    readings: list[Reading],
    *,
    medium_threshold: float,
    high_threshold: float,
    ewma_alpha: float,
    breach_k: int,
    breach_n: int,
) -> list[ReplayStep]:
    """Reproduce RiskEngine's risk_level for each reading, in order.

    Starts cold: empty history, no smoothed value yet, level = LOW -- the
    same initial state a freshly constructed ``RiskEngine`` has
    (``self._history = deque(maxlen=breach_n)``, ``self._smoothed = None``,
    ``self._risk_level = RiskLevel.LOW`` in ``RiskEngine.__init__``).

    Mirrors, line for line:
        RiskEngine.process (INSUFFICIENT_DATA / failure short-circuit,
                             EWMA update, history append)
        RiskEngine._candidate_level
        RiskEngine._with_hysteresis
    """

    history: deque[float] = deque(maxlen=breach_n)
    smoothed: float | None = None
    level = "LOW"
    steps: list[ReplayStep] = []

    for reading in readings:
        if reading.fused_score is None:
            # RiskEngine.process: INSUFFICIENT_DATA and _required_failure
            # both return before touching self._history / self._smoothed,
            # and both record risk_level=UNAVAILABLE for that decision.
            steps.append(ReplayStep(smoothed_score=smoothed, risk_level="UNAVAILABLE"))
            continue

        adjusted = reading.fused_score * reading.context_confidence
        smoothed = adjusted if smoothed is None else ewma_alpha * adjusted + (1 - ewma_alpha) * smoothed
        history.append(smoothed)

        medium_count = sum(v >= medium_threshold for v in history)
        high_count = sum(v >= high_threshold for v in history)
        if high_count >= breach_k:
            candidate = "HIGH"
        elif medium_count >= breach_k:
            candidate = "MEDIUM"
        else:
            candidate = "LOW"

        if _RANK[candidate] >= _RANK[level]:
            level = candidate
        elif len(history) >= breach_n:
            boundary = high_threshold if level == "HIGH" else medium_threshold
            if all(v < boundary for v in history):
                level = candidate
            # else: stay at current level (hysteresis holds)
        # else: deque not yet full, current level holds regardless of candidate

        steps.append(ReplayStep(smoothed_score=smoothed, risk_level=level))

    return steps


# ---------------------------------------------------------------------------
# CLI: frozen-corpus partition analysis (reproduces the numbers reported in
# docs/genuine-user-validation-2026-09-10.md items 6/7). Read-only throughout.
# ---------------------------------------------------------------------------

PARTICIPANT = "manas-01"
DATABASE = Path(r"C:\Users\Manas\AppData\Local\ContinuousAuthentication\Pilot\continuous-auth.db")
MANIFEST = Path(r"C:\Users\Manas\Desktop\manas_ml-pipeline\data\frozen\pilot-v1\manifest.json")
RISK_CONFIG = Path(r"C:\Users\Manas\Desktop\manas_ml-pipeline\config\risk.development.yaml")
KBD_ARTIFACT = Path(
    r"C:\Users\Manas\AppData\Local\ContinuousAuthentication\Pilot\models\manas-01"
    r"\20260910T034630.812846.joblib"
)
MOUSE_ARTIFACT = Path(
    r"C:\Users\Manas\AppData\Local\ContinuousAuthentication\Pilot\models\manas-01"
    r"\20260910T034630.955195.joblib"
)
PARTITIONS = ["TRAIN", "VALIDATION", "EVALUATION"]


def _score_partition(keyboard_artifact, mouse_artifact, partition: str):
    frozen = corpus_module.load_frozen_corpus(DATABASE, MANIFEST, partition)
    windows = frozen.windows_by_user.get(PARTICIPANT, [])

    quality_counts: dict[str, int] = {}
    fused_series: list[tuple[int, float]] = []
    skipped_insufficient = 0
    skipped_unscorable = 0

    for w in windows:
        quality_counts[w.quality_label] = quality_counts.get(w.quality_label, 0) + 1
        if w.quality_label == "INSUFFICIENT_DATA":
            skipped_insufficient += 1
            continue

        kbd_pct = None
        mouse_pct = None
        try:
            if keyboard_artifact is not None:
                r = score_window(keyboard_artifact, w)
                if r.available and r.percentile_score is not None:
                    kbd_pct = r.percentile_score
        except ModelSchemaMismatchError:
            raise
        except ValueError:
            pass
        try:
            if mouse_artifact is not None:
                r = score_window(mouse_artifact, w)
                if r.available and r.percentile_score is not None:
                    mouse_pct = r.percentile_score
        except ModelSchemaMismatchError:
            raise
        except ValueError:
            pass

        fused_pct = fuse_percentile_scores(kbd_pct, mouse_pct, w_kbd=0.5, w_mouse=0.5)
        if fused_pct is None:
            skipped_unscorable += 1
            continue

        fused_series.append((w.t_start_us, 1.0 - fused_pct / 100.0))

    fused_series.sort(key=lambda pair: pair[0])
    return {
        "total_windows": len(windows),
        "quality_counts": quality_counts,
        "skipped_insufficient_data": skipped_insufficient,
        "skipped_unscorable": skipped_unscorable,
        "fused_series": fused_series,
    }


def main() -> None:
    risk_settings = load_risk_settings(RISK_CONFIG)
    risk = risk_settings.risk
    keyboard_artifact = load_artifact(KBD_ARTIFACT)
    mouse_artifact = load_artifact(MOUSE_ARTIFACT)

    print(f"config_version={risk_settings.config_version}  "
          f"medium={risk.medium_threshold}  high={risk.high_threshold}")

    results = {}
    for partition in PARTITIONS:
        data = _score_partition(keyboard_artifact, mouse_artifact, partition)
        fused_series = data["fused_series"]
        readings = [Reading(fused_score=v) for _, v in fused_series]

        steps = replay_risk_levels(
            readings,
            medium_threshold=risk.medium_threshold,
            high_threshold=risk.high_threshold,
            ewma_alpha=risk.ewma_alpha,
            breach_k=risk.breach_k,
            breach_n=risk.breach_n,
        )
        levels = [s.risk_level for s in steps]
        n = len(levels)
        high_n = levels.count("HIGH")
        med_n = levels.count("MEDIUM")
        low_n = levels.count("LOW")
        entries_into_high = sum(
            1 for i in range(1, n) if levels[i] == "HIGH" and levels[i - 1] != "HIGH"
        ) + (1 if n and levels[0] == "HIGH" else 0)

        fused_values = [v for _, v in fused_series]
        medium_crossings = sum(1 for v in fused_values if v >= risk.medium_threshold)
        high_crossings = sum(1 for v in fused_values if v >= risk.high_threshold)

        print(f"\n{partition}: n={n}")
        print(f"  static crossing: medium={medium_crossings}/{n}={medium_crossings/n:.4f}  "
              f"high={high_crossings}/{n}={high_crossings/n:.4f}")
        print(f"  post-K-of-N state occupancy: LOW={low_n}/{n}={low_n/n:.4f}  "
              f"MEDIUM={med_n}/{n}={med_n/n:.4f}  HIGH={high_n}/{n}={high_n/n:.4f}  "
              f"(distinct HIGH entries={entries_into_high})")

        results[partition] = {
            "n": n,
            "medium_crossing_rate": medium_crossings / n,
            "high_crossing_rate": high_crossings / n,
            "low_occupancy": low_n / n,
            "medium_occupancy": med_n / n,
            "high_occupancy": high_n / n,
            "high_entries": entries_into_high,
        }

    out = Path(__file__).with_name("replay_state_machine_last_run.json")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
