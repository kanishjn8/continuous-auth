# Genuine-user validation at the 0.80/0.90 operating point — read-only

**Date:** 2026-09-10
**Participant:** `manas-01`
**Frozen dataset:** `pilot-v1` (`data/frozen/pilot-v1/manifest.json`)
**Profile scored:** keyboard artifact `20260910T034630.812846`, mouse artifact `20260910T034630.955195`
**Active config:** `config/risk.development.yaml`, `config_version = t013-synthetic-development-v2-pilot-op-0.80-0.90`, `medium_threshold = 0.80`, `high_threshold = 0.90`
**Status:** evaluation-only. No source file, config file, threshold, model artifact, calibration, or data split was changed in the course of this run.

**Correctness check:** the replay logic behind item 6 below has since been persisted to
[tools/evaluation/replay_state_machine.py](../tools/evaluation/replay_state_machine.py) and
independently checked against 675 real historical `risk_events` decisions from the live
pilot, matching all 675 exactly. See
[genuine-user-validation-2026-09-10-groundtruth.md](genuine-user-validation-2026-09-10-groundtruth.md)
for that verification and an explicit note on how partition boundaries are (and are not)
handled in the analysis below.

---

## 1. Method

Same code paths as `docs/threshold-pair-analysis-2026-09-10.md` (which is itself the source of pair #5, the pair now deployed):

- `ml.training.persistence.load_artifact` — loads the two frozen `.joblib` artifacts, verifying checksum and feature-schema version.
- `tools.collection.corpus.load_frozen_corpus` — verifies the freeze manifest, then reads TRAIN / VALIDATION / EVALUATION windows for `manas-01` (database opened `mode=ro`).
- `ml.training.common.score_window` — per-modality raw score + percentile against the frozen TRAIN reference distribution stored inside each artifact (`PercentileCalibrator.transform`, never `fit`).
- `ml.evaluation.fusion.fuse_percentile_scores` — availability-renormalised fusion at the engine's configured weights (0.5/0.5).
- `backend.app.models.service.percentile_for_calibrated_risk` — converts the deployed thresholds to the percentile scale.
- `backend.app.risk.config.load_risk_settings` — loaded the **live** `config/risk.development.yaml` to read the active thresholds; nothing in it was written.

**New for this report (item 6 below):** a chronological state-machine replay, written as a new, read-only, throwaway script (`genuine_validation_080_090.py`, kept in the session scratchpad, **not added to the repository**) that reimplements — line-for-line, against the constants read live from `RiskSettings`, never hardcoded — `RiskEngine`'s EWMA smoothing, `_candidate_level` breach counting, and `_with_hysteresis` from `backend/app/risk/engine.py:97-136`. It does not import, call, or modify the real `RiskEngine`; it is a separate reproduction of that state machine's arithmetic, run cold (state=LOW, empty history) at the start of each partition, over that partition's windows sorted chronologically by `t_start_us`. Context confidence is treated as `1.0` (a pass-through), matching `diagnosis.md` §12's finding that context confidence was a strict no-op throughout the live run, and matching the convention already used in the frozen threshold-pair-analysis doc. **This is a faithful reimplementation of the real logic, not an approximation** — it was cross-checked against the static per-window crossing rates below and reproduces them exactly at n→state-machine-off (see §3).

---

## 2. Results by partition

### TRAIN (2026-09-05 + 2026-09-06, in-sample — reported only for internal consistency)

| Item | Value |
|---|---|
| 1. Dataset/split | pilot-v1, partition=TRAIN |
| 2. Windows evaluated | 389 total (FULL 197, KBD_ONLY 39, MOUSE_ONLY 148, INSUFFICIENT_DATA 5) → **384 scorable/fused** |
| 3. Fused per-window FRR (engine's thresholding unit) | **0.1380 (13.80%)** |
| 4. Medium crossing rate | 53/384 = **0.1380 (13.80%)** |
| 5. High crossing rate | 23/384 = **0.0599 (5.99%)** |
| 6. Actual HIGH-state entries (3-of-5 K-of-N replay) | **0 distinct entries**; 0/384 windows spent in HIGH (0.00%) |
| 7. State distribution | LOW 97.66% (375), MEDIUM 2.34% (9), HIGH 0.00% (0) |
| 8. Anomalies | none |

### VALIDATION (2026-09-07)

| Item | Value |
|---|---|
| 1. Dataset/split | pilot-v1, partition=VALIDATION |
| 2. Windows evaluated | 382 total (FULL 170, KBD_ONLY 22, MOUSE_ONLY 181, INSUFFICIENT_DATA 9) → **373 scorable/fused** |
| 3. Fused per-window FRR (engine's thresholding unit) | **0.3753 (37.53%)** |
| 4. Medium crossing rate | 140/373 = **0.3753 (37.53%)** |
| 5. High crossing rate | 68/373 = **0.1823 (18.23%)** |
| 6. Actual HIGH-state entries (3-of-5 K-of-N replay) | **1 distinct entry**; 5/373 windows spent in HIGH (**1.34%**) |
| 7. State distribution | LOW 67.02% (250), MEDIUM 31.64% (118), HIGH 1.34% (5) |
| 8. Anomalies | none |

### EVALUATION (2026-09-08)

| Item | Value |
|---|---|
| 1. Dataset/split | pilot-v1, partition=EVALUATION |
| 2. Windows evaluated | 450 total (FULL 226, KBD_ONLY 34, MOUSE_ONLY 185, INSUFFICIENT_DATA 5) → **445 scorable/fused** |
| 3. Fused per-window FRR (engine's thresholding unit) | **0.2876 (28.76%)** |
| 4. Medium crossing rate | 128/445 = **0.2876 (28.76%)** |
| 5. High crossing rate | 47/445 = **0.1056 (10.56%)** |
| 6. Actual HIGH-state entries (3-of-5 K-of-N replay) | **0 distinct entries**; 0/445 windows spent in HIGH (0.00%) |
| 7. State distribution | LOW 76.40% (340), MEDIUM 23.60% (105), HIGH 0.00% (0) |
| 8. Anomalies | none |

`INSUFFICIENT_DATA` windows (5 / 9 / 5) are never scored, matching `RiskEngine` behaviour: they do not update history/EWMA and are excluded from every denominator above, same as the frozen threshold-pair-analysis.

---

## 3. Item 6 in full — why it matters, stated plainly

**Do not read item 5 (static high crossing rate) as the system's HIGH-state rate.** They diverge sharply once K-of-N and EWMA smoothing are applied:

| partition | static HIGH crossing rate (item 5) | actual HIGH-state occupancy after K-of-N (item 6) | distinct HIGH entries |
|---|---|---|---|
| TRAIN | 5.99% | **0.00%** | 0 |
| VALIDATION | 18.23% | **1.34%** | 1 |
| EVALUATION | 10.56% | **0.00%** | 0 |

On VALIDATION, roughly 1 in 5 individual windows crosses the HIGH boundary, but because HIGH requires 3-of-the-last-5 *smoothed* values to be at or above 0.90, sustained escalation to HIGH happened only once across the whole day (5 consecutive windows), and never at all on TRAIN or EVALUATION. This is the direct, intended effect of raising the operating point from 0.45/0.75 to 0.80/0.90: isolated high-percentile-anomaly windows are far more common than sustained runs of them, and K-of-N filters exactly that.

MEDIUM state occupancy (item 7) is still substantial — 31.64% on VALIDATION, 23.60% on EVALUATION — because reaching MEDIUM only needs 3-of-5 smoothed values ≥ 0.80, a lower bar than HIGH's 0.90.

---

## 4. Comparison against the previous 0.45/0.75 operating point (item 9)

Same corpus, same artifacts, static per-window crossing rates only (0.45/0.75 has no corresponding K-of-N replay in this report — that would require a separate replay at the old thresholds, which was out of scope for this evaluation and is not needed to characterize the new operating point):

| partition | medium @0.45 (old) | medium @0.80 (new) | high @0.75 (old) | high @0.90 (new) |
|---|---|---|---|---|
| TRAIN | 55.47% | **13.80%** (−41.67 pts) | 19.01% | **5.99%** (−13.02 pts) |
| VALIDATION | 84.18% | **37.53%** (−46.65 pts) | 46.38% | **18.23%** (−28.15 pts) |
| EVALUATION | 76.63% | **28.76%** (−47.87 pts) | 38.20% | **10.56%** (−27.64 pts) |

The new operating point roughly halves-to-thirds the held-out medium crossing rate and roughly halves-to-quarters the held-out high crossing rate, consistent with `diagnosis.md`'s finding that the old 0.45/0.75 pair was rejecting the user's own in-sample training windows at ~55%.

---

## 5. Comparison against the frozen threshold-pair-analysis expectations for pair #5 (item 10)

| partition | metric | expected (frozen doc) | observed (this run) | diff |
|---|---|---|---|---|
| TRAIN | medium | 0.1380 | 0.1380 | +0.00002 |
| TRAIN | high | 0.0599 | 0.0599 | −0.00000 |
| VALIDATION | medium | 0.3753 | 0.3753 | +0.00004 |
| VALIDATION | high | 0.1823 | 0.1823 | +0.00001 |
| EVALUATION | medium | 0.2876 | 0.2876 | +0.00004 |
| EVALUATION | high | 0.1056 | 0.1056 | +0.00002 |

All six numbers reproduce the frozen doc to within floating-point display rounding (4th decimal). This confirms the deployed config (`config/risk.development.yaml`, `medium_threshold=0.80`, `high_threshold=0.90`) is exactly the pair the frozen analysis called pair #5, and that no drift occurred between the analysis and the currently active runtime configuration.

---

## 6. EVALUATION is confirmatory, not blind held-out

**Explicit note, as required:** the EVALUATION partition's crossing rates for all 8 candidate pairs — including pair #5, the one now deployed — were already computed and surfaced in `docs/threshold-pair-analysis-2026-09-10.md`, and the team used that table (across all 8 pairs, across all three partitions) to select pair #5. **The EVALUATION numbers in this report are therefore confirmatory of that prior analysis, not a blind held-out estimate.** EVALUATION has already informed one round of threshold selection for this participant; a genuinely blind held-out measurement of this operating point does not exist for this corpus.

---

## 7. What item 6 is, and is not

- Items 3, 4, 5 are **per-window threshold crossing rates**, measured before EWMA smoothing and before K-of-N counting — exactly the quantity `activate.py`'s `_frr_at_deployed_operating_point` and the frozen threshold-pair-analysis measure.
- Item 6 is the **actual HIGH-state entry/occupancy rate**, computed by replaying the real temporal state machine (EWMA α=0.4, 3-of-5 breach counting, asymmetric hysteresis) in chronological order. It is **not** derived from item 5, and it is substantially lower than item 5 on every partition, as shown in §3.
- The **headline FRR** for this operating point, in the sense `activate.py` reports it (fused per-window rate at `medium_threshold`, before smoothing/K-of-N), is item 3 / item 4 — they are the same number, since MEDIUM is the FRR-defining boundary.
- No FAR/impostor measurement exists at any operating point for this corpus (ADR-014: single-participant pilot, ADR still open on that question). Nothing above should be read as a detection-rate or accuracy claim; it describes cost on genuine-user data only.

Item 6 was computed with a real replay of the temporal logic (not approximated) — the reimplementation is described in §1 and the script is available in the session scratchpad if it needs to be inspected or re-run.

---

## 8. Confirmations

- **No source files were modified** by this evaluation run, aside from the one new, clearly-labeled, read-only analysis script (`genuine_validation_080_090.py`), which was written to the session scratchpad directory, **not committed to the repository**. `git status` before and after this evaluation is identical (the pre-existing working-tree modifications to `backend/tests/test_risk_engine.py`, `config/risk.development.yaml`, `tools/enrollment/activate.py`, and `tools/enrollment/tests/test_activate.py`, plus the two pre-existing untracked docs, all predate this evaluation and were not touched by it).
- **No configuration was modified.** `config/risk.development.yaml` was only read (`load_risk_settings`), never written; thresholds used were exactly `medium_threshold=0.80`, `high_threshold=0.90` as currently configured.
- **No model artifacts were modified.** Both `.joblib` files were only read via `load_artifact`, which verifies (and does not alter) their checksums.
- **No calibration or retraining occurred.** Every percentile was computed with `PercentileCalibrator.transform` against the frozen TRAIN reference distribution already stored inside each artifact; `.fit` was never called.
- **No data split was changed.** The TRAIN/VALIDATION/EVALUATION day assignments came from the existing `data/frozen/pilot-v1/manifest.json`, verified by `load_frozen_corpus`'s freeze check before any row was read.
- **The attacker drill was not run.**
- This report makes **no recommendation** about changing the thresholds; it establishes genuine-user performance at the currently-selected 0.80/0.90 operating point only.
