# Candidate (medium, high) risk-threshold pairs — read-only analysis

**Date:** 2026-09-10
**Participant:** `manas-01`
**Frozen dataset:** `pilot-v1` (`manifest_checksum = 9bf59ebe184ed6fb8f632d4ff78c549bac870d9a87ae6a4616c32db6c541eb9a`)
**Profile scored:** `20260910T034634.521619` — keyboard artifact `20260910T034630.812846`, mouse artifact `20260910T034630.955195`
**Status:** read-only. **No configuration file was modified. No threshold was changed. No model was retrained and no calibrator was refitted.**

This report exists because [diagnosis.md](../diagnosis.md) §8 swept `medium_threshold` **in isolation**. That is not directly actionable: [backend/app/risk/config.py:59-60](../backend/app/risk/config.py#L59-L60) refuses to load a config where `medium_threshold >= high_threshold`, so raising `medium_threshold` to, say, 0.95 while `high_threshold` stays at 0.75 produces a `RiskConfigError` and the runtime will not start. Any change to one boundary requires the other to be re-derived at the same time.

This report therefore restates the same evidence as **valid pairs**. It presents data only — **it does not recommend a pair and does not pick one.** Selecting an operating point requires the human approval that [config/risk.development.yaml](../config/risk.development.yaml) already demands for the O3/O7/O8/O9 questions.

---

## 1. Method

Identical to `diagnosis.md`, with no refitting at any step:

1. Both `.joblib` artifacts were loaded through `ml.training.persistence.load_artifact`, which verifies the metadata checksum and the feature-schema version — the same path the live runtime uses ([backend/app/runtime/profiles.py:53-66](../backend/app/runtime/profiles.py#L53-L66)).
2. The frozen TRAIN / VALIDATION / EVALUATION partitions were loaded through `tools.collection.corpus.load_frozen_corpus`, which runs `verify_freeze` before reading and returns manifest members only.
3. Each window was scored with `ml.training.common.score_window`. The percentile is a rank against the **frozen TRAIN reference distribution stored inside each artifact** (236 keyboard / 345 mouse in-sample scores) — `PercentileCalibrator.transform` is called, never `fit`.
4. Percentiles were fused per window with `ml.evaluation.fusion.fuse_percentile_scores` at the engine's configured weights (`keyboard_weight = mouse_weight = 0.5`, from [config/risk.development.yaml](../config/risk.development.yaml)). Fusion is availability-renormalised, so a single-modality window fuses to that modality's own percentile.
5. A boundary at calibrated risk `t` was converted to the percentile scale with `backend.app.models.service.percentile_for_calibrated_risk` (`pct = (1 − t) · 100`), and a window counts as breaching when `percentile <= pct`. This is the same inclusive comparison the diagnosis used, and it is equivalent to the engine's `value >= threshold` on the risk scale because `risk = 1 − pct/100` is decreasing.

### Sample sizes

| partition | day | fused per-window | pooled per-(window, modality) |
|---|---|---|---|
| TRAIN | 2026-09-05 + 2026-09-06 | 384 | 581 |
| VALIDATION | 2026-09-07 | 373 | 543 |
| EVALUATION | 2026-09-08 | 445 | 671 |

The fused count is lower than the window count of each partition because `INSUFFICIENT_DATA` windows produce no score in either modality and are excluded (5 / 9 / 5 respectively).

### Units — read this before reading the tables

The **primary table uses fused per-window risk, which is the unit the live engine actually breaches on** (`RiskEngine._fusion` → `_candidate_level`, [backend/app/risk/engine.py:97-121](../backend/app/risk/engine.py#L97-L121)). The secondary table repeats the sweep in the pooled per-(window, modality) unit that the pilot's `validation_frr = 0.8195` was reported in, so the two can be reconciled; that reporting-unit mismatch is diagnosis defect 2 and has since been corrected in `tools/enrollment/activate.py`.

### What these rates are, and what they are not

- They are **per-window breach rates on genuine (legitimate-user) data only**, measured **before** context adjustment, EWMA smoothing and K-of-N counting.
- The "medium" column is therefore a per-window **false-rejection rate** at that boundary.
- The "high" column is the per-window rate of crossing the HIGH boundary. **It is not the probability of the system entering the HIGH state**, which additionally requires 3 of the last 5 *smoothed* values to be above the boundary (`breach_k = 3`, `breach_n = 5`). The state-level rate is lower than the per-window rate for isolated breaches and higher for sustained ones; deriving it requires replaying the engine and is out of scope here.
- **No FAR / impostor column exists, in either unit, at any pair.** The frozen corpus contains one participant, so no false-acceptance rate can be measured at any operating point (ADR-014). Every row below describes only the cost side of the trade-off. **A pair cannot be chosen from this table alone.**

---

## 2. Candidate pairs — fused per-window (engine breach unit)

All eight pairs satisfy `medium_threshold < high_threshold` and will load. Row 1 is the currently deployed pair, included as a baseline for comparison.

| # | medium | high | ⟺ pct (med) | ⟺ pct (high) | TRAIN med | TRAIN high | VALIDATION med | VALIDATION high | EVALUATION med | EVALUATION high |
|---|---|---|---|---|---|---|---|---|---|---|
| **1** | **0.450** | **0.750** | **55.0** | **25.0** | **0.5547** | **0.1901** | **0.8418** | **0.4638** | **0.7663** | **0.3820** |
| 2 | 0.600 | 0.800 | 40.0 | 20.0 | 0.3646 | 0.1380 | 0.7024 | 0.3753 | 0.6292 | 0.2876 |
| 3 | 0.700 | 0.850 | 30.0 | 15.0 | 0.2422 | 0.0990 | 0.5523 | 0.2922 | 0.4787 | 0.2000 |
| 4 | 0.750 | 0.900 | 25.0 | 10.0 | 0.1901 | 0.0599 | 0.4638 | 0.1823 | 0.3820 | 0.1056 |
| 5 | 0.800 | 0.900 | 20.0 | 10.0 | 0.1380 | 0.0599 | 0.3753 | 0.1823 | 0.2876 | 0.1056 |
| 6 | 0.900 | 0.950 | 10.0 | 5.0 | 0.0599 | 0.0312 | 0.1823 | 0.1072 | 0.1056 | 0.0449 |
| 7 | 0.950 | 0.980 | 5.0 | 2.0 | 0.0312 | 0.0156 | 0.1072 | 0.0214 | 0.0449 | 0.0090 |
| 8 | 0.980 | 0.995 | 2.0 | 0.5 | 0.0156 | 0.0026 | 0.0214 | 0.0080 | 0.0090 | 0.0067 |

**Row 1 reconciles with the diagnosis:** VALIDATION med `0.8418` is the fused-per-window figure quoted in `diagnosis.md` §3 and §6, and TRAIN med `0.5547` is the in-sample floor quoted in §1 and §16.

---

## 3. The same pairs — pooled per-(window, modality)

Provided for continuity with the pilot's originally reported `validation_frr`. Each `FULL` window contributes two samples here (one per model), `KBD_ONLY` / `MOUSE_ONLY` contribute one, `INSUFFICIENT_DATA` none.

| # | medium | high | ⟺ pct (med) | ⟺ pct (high) | TRAIN med | TRAIN high | VALIDATION med | VALIDATION high | EVALUATION med | EVALUATION high |
|---|---|---|---|---|---|---|---|---|---|---|
| **1** | **0.450** | **0.750** | **55.0** | **25.0** | **0.5473** | **0.2496** | **0.8195** | **0.4936** | **0.7571** | **0.4456** |
| 2 | 0.600 | 0.800 | 40.0 | 20.0 | 0.3993 | 0.1979 | 0.7109 | 0.4052 | 0.6453 | 0.3621 |
| 3 | 0.700 | 0.850 | 30.0 | 15.0 | 0.2978 | 0.1480 | 0.5562 | 0.3315 | 0.5171 | 0.2757 |
| 4 | 0.750 | 0.900 | 25.0 | 10.0 | 0.2496 | 0.0981 | 0.4936 | 0.2486 | 0.4456 | 0.1863 |
| 5 | 0.800 | 0.900 | 20.0 | 10.0 | 0.1979 | 0.0981 | 0.4052 | 0.2486 | 0.3621 | 0.1863 |
| 6 | 0.900 | 0.950 | 10.0 | 5.0 | 0.0981 | 0.0482 | 0.2486 | 0.1308 | 0.1863 | 0.0745 |
| 7 | 0.950 | 0.980 | 5.0 | 2.0 | 0.0482 | 0.0172 | 0.1308 | 0.0368 | 0.0745 | 0.0104 |
| 8 | 0.980 | 0.995 | 2.0 | 0.5 | 0.0172 | 0.0034 | 0.0368 | 0.0147 | 0.0104 | 0.0075 |

**Row 1, VALIDATION med = 0.8195** is exactly the `validation_frr` recorded in `model_profiles.validation_json` for the pilot profile.

---

## 4. Supporting distribution summary

Fused per-window percentile (higher = more normal), against the frozen TRAIN reference distribution:

| partition | n | min | p05 | p25 | median | mean | p75 | p95 | max |
|---|---|---|---|---|---|---|---|---|---|
| TRAIN (in-sample) | 384 | 0.29 | 8.53 | 31.42 | **51.00** | 50.16 | 68.56 | 91.22 | 99.42 |
| VALIDATION | 373 | 0.00 | 2.85 | 13.37 | **27.67** | 31.42 | 44.93 | 77.78 | 98.73 |
| EVALUATION | 445 | 0.00 | 5.22 | 17.34 | **31.30** | 37.00 | 52.04 | 84.68 | 99.13 |

The TRAIN row is near-uniform (median ≈ 51, p05 ≈ 9, p95 ≈ 91), which is what a rank calibrator fitted on its own training scores must produce. This is the mechanical reason the in-sample columns in §2 and §3 track the percentile boundary almost exactly: at percentile boundary *p*, the in-sample breach rate is ≈ *p*/100.

---

## 5. Observations on the data (no recommendation)

These are properties of the tables, stated without selecting a pair:

1. **The in-sample column is approximately the percentile boundary itself.** At pct 55 the TRAIN rate is 0.5547; at pct 25 it is 0.1901; at pct 10, 0.0599; at pct 2, 0.0156. Any pair's in-sample cost is therefore predictable from its percentile boundary alone, independently of this participant's data.
2. **The held-out penalty is roughly a constant multiple, not a constant offset.** VALIDATION / TRAIN at the medium boundary is ≈ 1.5× at pct 55, ≈ 2.4× at pct 25, ≈ 3.0× at pct 10, and ≈ 1.4× at pct 2. The multiplier is largest in the mid range and compresses at both extremes.
3. **VALIDATION is consistently worse than EVALUATION** at every pair and both boundaries, in both units. The two held-out days bracket each other rather than agreeing exactly, so a single held-out day is not a stable estimate of held-out cost.
4. **Rows 4 and 5 share a `high_threshold` (0.900)** and therefore share their HIGH columns exactly; they differ only in the medium boundary. Rows 3 and 4 illustrate the opposite: different pairs, monotone in both columns.
5. **The two units disagree more at the HIGH boundary than at the MEDIUM boundary.** At row 1 the medium figures are close (0.8418 fused vs 0.8195 pooled) while the high figures differ more (0.4638 vs 0.4936, and on EVALUATION 0.3820 vs 0.4456). Averaging two modalities pulls extreme single-modality values toward the middle, which matters most where the boundary is deepest in the tail.
6. **Every row costs something on held-out genuine data.** Even the strictest pair (row 8) rejects 2.14% of held-out genuine windows at the MEDIUM boundary per window, before smoothing.
7. **Nothing here quantifies detection.** Because FAR is unmeasurable on this corpus, moving down the table reduces measured false rejection while changing detection by an unknown amount. **The table shows one axis of a two-axis decision.**

---

## 6. Constraints any chosen pair must satisfy

Recorded so that whoever selects a pair does not have to re-derive them:

| constraint | source |
|---|---|
| `medium_threshold < high_threshold` (strict) | [backend/app/risk/config.py:59-60](../backend/app/risk/config.py#L59-L60) |
| `keyboard_weight > 0` and `mouse_weight > 0` | [backend/app/risk/config.py:57-58](../backend/app/risk/config.py#L57-L58) |
| `breach_k <= breach_n` | [backend/app/risk/config.py:61-62](../backend/app/risk/config.py#L61-L62) |
| Both thresholds must lie in [0, 1] to be reachable, since `calibrated_risk ∈ [0, 1]` | [backend/app/models/service.py:79](../backend/app/models/service.py#L79) |
| A config file loaded by the runtime must declare `development_only: true` | [backend/app/risk/config.py:28](../backend/app/risk/config.py#L28) |
| Changing either threshold changes `config_checksum`, so decisions recorded before and after are distinguishable in `risk_events.threshold_config_version` / `config_checksum` | [backend/app/risk/config.py:63-64](../backend/app/risk/config.py#L63-L64) |

A further practical note, from `diagnosis.md` §7: the exit condition from MEDIUM is *all* `breach_n = 5` smoothed values below `medium_threshold`. Raising `medium_threshold` widens the recovery region and shortens the expected time to return to LOW, but that relationship is mediated by the EWMA and is not quantified in this report.

---

## 7. Reproduction

The sweep was produced by a throwaway script in the session scratchpad (not added to the repository), using only repository code paths: `load_artifact`, `load_frozen_corpus`, `score_window`, `fuse_percentile_scores`, `percentile_for_calibrated_risk`.

Every database handle was opened `mode=ro`. Nothing was fitted, refitted, written, deleted, or reconfigured; the only artifacts of this analysis are this file's tables.
