# Diagnosis — Persistent MEDIUM/HIGH Risk for the Legitimate User After First Activation

**Date of investigation:** 2026-09-10
**Participant:** `manas-01`
**Frozen dataset:** `pilot-v1` (`manifest_checksum = 9bf59ebe184ed6fb8f632d4ff78c549bac870d9a87ae6a4616c32db6c541eb9a`)
**Profile under test:** `profile_version = 20260910T034634.521619`
**Mode:** read-only diagnostic pass. No code, config, threshold, model, database, manifest, or artifact was modified. Every database handle was opened `mode=ro`. No retraining, no additional collection, no attacker drill, no re-freeze.

Every claim below is tagged:

- **VERIFIED** — read directly from code or reproduced numerically from the database / frozen corpus / live model artifacts.
- **INFERENCE** — a reasoned conclusion from verified evidence, labelled as such.
- **NOT DETERMINABLE** — cannot be established from the data that exists today.

---

## Table of contents

1. [Executive conclusion](#1-executive-conclusion)
2. [Current system state](#2-current-system-state)
3. [81.95% FRR — exact calculation](#3-8195-frr--exact-calculation)
4. [FRR by modality](#4-frr-by-modality)
5. [Model score → calibrated risk trace](#5-model-score--calibrated-risk-trace)
6. [Where live risk first becomes elevated](#6-where-live-risk-first-becomes-elevated)
7. [Why MEDIUM persisted](#7-why-medium-persisted)
8. [Calibration analysis](#8-calibration-analysis)
9. [Training data representativeness](#9-training-data-representativeness)
10. [Keyboard vs mouse analysis](#10-keyboard-vs-mouse-analysis)
11. [Fusion analysis](#11-fusion-analysis)
12. [Context confidence analysis](#12-context-confidence-analysis)
13. [Threshold / operating-point verification](#13-threshold--operating-point-verification)
14. [Enforcement semantics](#14-enforcement-semantics)
15. [Data-contamination check](#15-data-contamination-check)
16. [Root-cause classification](#16-root-cause-classification)
17. [Evidence / files / code paths](#17-evidence--files--code-paths)
18. [Recommended next step](#18-recommended-next-step)
19. [Appendix A — full live decision trace](#appendix-a--full-live-decision-trace-62-post-activation-decisions)
20. [Appendix B — reproduction method](#appendix-b--reproduction-method)

---

## 1. EXECUTIVE CONCLUSION

**The legitimate user is not being judged anomalous by any bug in the risk pipeline. The elevated risk originates at the very first numerical stage — raw model score → percentile calibration — and the deployed threshold then guarantees escalation.**

Two independent causes, both verified numerically:

1. **The operating point is structurally aggressive (dominant cause).** `risk.medium_threshold = 0.45` maps to *percentile ≤ 55* of the user's **own training-score distribution**. Percentile calibration produces a (near-)uniform rank in-sample, so **54.7% of the model's own training windows breach MEDIUM** and **19.0% breach HIGH** — before any drift, any held-out data, any live behaviour. MEDIUM at 0.45 literally means *"less normal than the 55th percentile of yourself."*
2. **Out-of-sample degradation pushes 55% → 82%.** Held-out genuine windows score systematically lower (more anomalous) than in-sample ones — a combination of in-sample calibration bias and real day-to-day variation, worst in the mouse acceleration features. Median held-out percentile is 25–28, not 50.

The live behaviour is a **precise reproduction of the frozen VALIDATION distribution**: per-window fused risk median **0.759 live vs 0.723 on VALIDATION**; **81.7% vs 84.2% ≥ 0.45**. Nothing between fusion and enforcement misbehaves.

**Classification: MIXED — primarily CONFIGURATION / CALIBRATION-SEMANTICS, secondarily MODEL/DATA. The risk pipeline itself is EXPECTED_BEHAVIOR given its inputs.** Four genuine but non-causal defects are listed in [§16](#16-root-cause-classification).

**Answer to "which of A–I is true":** **A + I**, amplified by **D and E**. Explicitly **not** B, C, F, or G.

---

## 2. CURRENT SYSTEM STATE

**VERIFIED FROM DATA** — `%LOCALAPPDATA%/ContinuousAuthentication/Pilot/continuous-auth.db`, opened `mode=ro`.

| Item | Value |
|---|---|
| `users` | `manas-01` / `ACTIVE`, created `2026-09-05T06:30:02Z`, updated `2026-09-10T03:58:34Z` |
| `sessions` | 6 (4 collection days + 1 empty 45 s session + 1 live session still open) |
| `segments` | 15 |
| `feature_windows` | **1283** = 1221 frozen + 62 post-activation live |
| `scores` | 1283 — **62** at `profile_version = 20260910T034634.521619`, **1221** at `'unavailable'` |
| `risk_events` / `decisions` | 675 / 675 |
| `model_profiles` | 1 row, `ACTIVE`, created + activated `2026-09-10T03:46:34Z` |
| `models` table | **0 rows** (see [§15](#15-data-contamination-check) — provenance gap) |
| `drill_sessions` | **0** — no attacker data exists anywhere |
| `update_runs` | 0 |
| `update_candidates` | 14, **all `REJECTED`** |
| `alerts` | 625 — `RISK_INPUT_UNAVAILABLE` ×604, `COLLECTOR_HEARTBEAT_LOST` ×18, `BEHAVIORAL_RISK_HIGH` ×3 |
| `verification_anchors` | 6 |
| `storage_metadata` | `database_schema_version=4`, `storage_config_version=storage-pilot-1`, `protocol_version=1.0.0` |
| Model artifacts on disk | `models/manas-01/20260910T034630.812846.joblib` (keyboard, 236 train windows, raw mean −0.40368, raw sd 0.04756)<br>`models/manas-01/20260910T034630.955195.joblib` (mouse, 345 train windows, raw mean −0.39247, raw sd 0.04589)<br>both `training_data_date_range = (2026-09-05, 2026-09-06)`, `random_state=42`, `n_estimators=100`, `contamination="auto"` |
| Active risk config at runtime | `threshold_config_version = t013-synthetic-development-v1`, `config_checksum = 128193724a89c255688e63d691324e7f50651becff1436686cb89ce8fd6e3841` → [config/risk.development.yaml](config/risk.development.yaml) — **not** the `risk.demo-live-single-day.yaml` override |
| Window cadence | 30 s or 100 keystrokes, whichever first ([config/ml.development.yaml](config/ml.development.yaml) `windowing`) |

### Sessions

```
session-e840e8f5…  2026-09-05T06:30:02Z -> 2026-09-05T09:30:40Z   101 windows  drill=0
session-e490d859…  2026-09-06T06:53:39Z -> 2026-09-06T18:42:39Z   288 windows  drill=0
session-81097750…  2026-09-07T03:52:29Z -> 2026-09-07T18:21:29Z   382 windows  drill=0
session-3ef397e6…  2026-09-08T03:44:44Z -> 2026-09-08T18:20:38Z   450 windows  drill=0
session-2abcdff2…  2026-09-10T03:42:49Z -> 2026-09-10T03:43:34Z     0 windows  drill=0
session-ed161d47…  2026-09-10T03:47:30Z -> (open)                  62 windows  drill=0
```

### Frozen partition composition

From [data/frozen/pilot-v1/manifest.json](data/frozen/pilot-v1/manifest.json) (1221 records):

| partition | day | FULL | KBD_ONLY | MOUSE_ONLY | INSUF | total | kbd-eligible | mouse-eligible |
|---|---|---|---|---|---|---|---|---|
| TRAIN | 2026-09-05 | 34 | 5 | 61 | 1 | 101 | | |
| TRAIN | 2026-09-06 | 163 | 34 | 87 | 4 | 288 | | |
| **TRAIN total** | | **197** | **39** | **148** | **5** | **389** | **236** | **345** |
| **VALIDATION** | 2026-09-07 | **170** | **22** | **181** | **9** | **382** | **192** | **351** |
| **EVALUATION** | 2026-09-08 | **226** | **34** | **185** | **5** | **450** | **260** | **411** |

Day assignments confirmed exactly as reported:

```json
{"manas-01": {"2026-09-05": "TRAIN", "2026-09-06": "TRAIN",
              "2026-09-07": "VALIDATION", "2026-09-08": "EVALUATION"}}
```

**Observation (VERIFIED):** the day-disjoint 60/20/20 split gives TRAIN only **389 of 1221 windows (31.9%)**, because day sizes are unequal (101 / 288 / 382 / 450). *INFERENCE: the split is correct by its own rule — it splits **days**, not evidence volume — but "60%" describes days, not windows, and the earliest day is the smallest.*

### Live decision summary (all 675 `risk_events`)

```
level=UNAVAILABLE action=NONE           reason=FAIL_OPEN_COMPONENT_UNAVAILABLE state=DEGRADED    shadow=0 applied=0 n=604
level=MEDIUM      action=SOFT_CHALLENGE reason=RISK_ESCALATION                state=ACTIVE      shadow=0 applied=1 n= 15
level=MEDIUM      action=NONE           reason=ACTION_COOLDOWN                state=ACTIVE      shadow=0 applied=0 n= 14
level=HIGH        action=REAUTH         reason=RISK_ESCALATION                state=ACTIVE      shadow=0 applied=1 n= 13
level=HIGH        action=NONE           reason=ACTION_COOLDOWN                state=ACTIVE      shadow=0 applied=0 n=  9
level=UNAVAILABLE action=NONE           reason=INSUFFICIENT_EVIDENCE_HOLD     state=DEGRADED    shadow=0 applied=0 n=  9
level=MEDIUM      action=SOFT_CHALLENGE reason=SHADOW_OR_NON_ENFORCING        state=CALIBRATING shadow=1 applied=0 n=  6
level=LOW         action=CONTINUE       reason=SHADOW_OR_NON_ENFORCING        state=CALIBRATING shadow=1 applied=0 n=  2
level=UNAVAILABLE action=NONE           reason=INSUFFICIENT_EVIDENCE_HOLD     state=ACTIVE      shadow=0 applied=0 n=  2
level=HIGH        action=REAUTH         reason=SHADOW_OR_NON_ENFORCING        state=CALIBRATING shadow=1 applied=0 n=  1
```

The 604 `DEGRADED` / `FAIL_OPEN_COMPONENT_UNAVAILABLE` rows are the **pre-activation collection days**: no model existed, so `_required_failure` fired `MODEL_UNAVAILABLE` on every window. Expected, not a defect. **Only 62 rows belong to the post-activation live test; only 2 of the 675 were ever LOW.**

---

## 3. 81.95% FRR — EXACT CALCULATION

**VERIFIED FROM CODE AND REPRODUCED BIT-EXACTLY.**

### Code path

[tools/enrollment/activate.py:300](tools/enrollment/activate.py#L300)
→ `_measure_validation_metrics` ([activate.py:118-201](tools/enrollment/activate.py#L118-L201))
→ no impostor scores exist (single-participant corpus)
→ `_frr_at_deployed_operating_point` ([activate.py:204-227](tools/enrollment/activate.py#L204-L227)).

### Exact formula (verbatim, [activate.py:217-218](tools/enrollment/activate.py#L217-L218))

```python
percentile_threshold = percentile_for_calibrated_risk(medium_threshold)   # -> 55.00000000000001
frr = float(np.mean(genuine_percentiles <= percentile_threshold))
```

### Sample construction ([activate.py:167-177](tools/enrollment/activate.py#L167-L177))

```python
for artifact in profile.values():                       # keyboard, then mouse
    cross_results = zero_effort_cross_evaluation({participant_id: artifact},
                                                 validation_windows_by_user)
    result = cross_results.get(participant_id)
    genuine.extend(result.genuine_scores)               # ONE POOLED LIST
    impostor.extend(result.all_impostor_scores().tolist())
```

Each artifact scores every VALIDATION window. A window whose modality is absent returns `available=False` and contributes nothing ([ml/training/common.py:359-368](ml/training/common.py#L359-L368)); a non-finite raw score is skipped ([ml/evaluation/cross_evaluation.py:44-52](ml/evaluation/cross_evaluation.py#L44-L52)).

### Exact numbers (re-scored with the live artifacts)

| Model | Validation samples scored | Rejected (`percentile ≤ 55`) | Rate |
|---|---|---|---|
| keyboard | 192 | 148 | 0.770833 |
| mouse | 351 | 297 | 0.846154 |
| **POOLED** | **543** | **445** | **0.8195211786372008** |

```
445 / 543 = 0.8195211786372008
stored     = 0.8195211786372008     ->  bit-identical
```

### Answers to the specific questions asked

| Question | Answer |
|---|---|
| Validation sample count | **543** `(window, modality)` pairs — **not** 382 windows and **not** 373 scorable windows |
| Number rejected | **445** |
| Exact numerator | **445** |
| Exact denominator | **543** |
| Exact formula | `mean(genuine_percentiles <= 55.00000000000001)` |
| Threshold used | `risk.medium_threshold = 0.45` → percentile `55.00000000000001` |
| Calibration mapping | `PercentileCalibrator.transform` = `searchsorted(ref, s, side="right") / len(ref) * 100`, where `ref` = the **236 / 345 in-sample TRAIN raw scores** stored inside each artifact |
| Comparison direction correct? | **Yes.** `risk = 1 − pct/100` is strictly decreasing, so `risk ≥ 0.45 ⟺ pct ≤ 55`. `>= 55` would be wrong. Higher raw score = more normal = higher percentile = lower risk. No sign error anywhere. |
| Before smoothing? | **Yes — verified.** `_frr_at_deployed_operating_point` receives raw percentiles; no `RiskEngine`, no EWMA is instantiated. |
| Before K-of-N? | **Yes — verified.** No `deque`, no `_candidate_level`, no hysteresis. |
| Is the claimed operating point string true? | **True on smoothing and K-of-N; incomplete on fusion** (see below). |

### The definition actually used vs the definition you asked about

The reported rate is **rejected modality-scores / eligible modality-scores**, not *"rejected genuine windows / eligible genuine windows"*.

- A `FULL` window is counted **twice** (once per model).
- A `KBD_ONLY` / `MOUSE_ONLY` window is counted **once**.
- An `INSUFFICIENT_DATA` window (9 in VALIDATION) is counted **zero** times.

The live engine's actual breach unit is the **fused per-window** calibrated risk. Measured that way, the same VALIDATION partition gives **FRR = 0.8418 (314 / 373)**. The two figures differ, and the `operating_point` string discloses "per-window before smoothing and K-of-N" but **does not disclose the cross-modality pooling** — so "per-window" is slightly misleading; it is per *(window, modality)*.

**This is defect 2 in [§16](#16-root-cause-classification). It is a measurement-definition mismatch, not a cause of the elevated risk.**

---

## 4. FRR BY MODALITY

**VERIFIED — recomputed from the live artifacts against the frozen VALIDATION partition.**

### Rejection breakdown by quality label

| quality | model | eligible windows | rejected | FRR | median percentile |
|---|---|---|---|---|---|
| FULL | keyboard | 170 | 137 | **0.8059** | 24.15 |
| FULL | mouse | 170 | 136 | **0.8000** | 26.81 |
| KBD_ONLY | keyboard | 22 | 11 | **0.5000** | 56.78 |
| MOUSE_ONLY | mouse | 181 | 161 | **0.8895** | 24.93 |
| INSUFFICIENT_DATA | — | 9 | n/a | **excluded** | — |

**Is one modality driving the 81.95%?** Yes — **mouse**. It supplies 351 of 543 samples (64.6%) at the highest rejection rate (0.846), and `MOUSE_ONLY` at 0.8895 is the single worst cell. Keyboard is meaningfully better (0.771), and `KBD_ONLY` is the only cell near the in-sample floor.

### `INSUFFICIENT_DATA` treatment (VERIFIED)

- **Offline:** never scored (no feature block present) → contributes nothing to FRR.
- **Live:** [backend/app/risk/engine.py:184-209](backend/app/risk/engine.py#L184-L209) emits `risk_level = UNAVAILABLE`, `reason_code = INSUFFICIENT_EVIDENCE_HOLD`, `fused_score = NULL`, `smoothed_score = NULL` — and critically **does not append to `_history` and does not update `_smoothed`**. An idle window therefore neither helps nor hurts recovery; the risk level is frozen across it. Observed twice live (i=48 at 04:27:14, i=50 at 04:30:56).

### Per-model score distributions (percentile scale; higher = more normal)

| set | model | n | min | p10 | median | mean | p90 | max | % ≤ 55 (rejected) |
|---|---|---|---|---|---|---|---|---|---|
| TRAIN (in-sample) | keyboard | 236 | 0.42 | 10.38 | 50.21 | 50.21 | 90.04 | 100.00 | **0.5466** |
| TRAIN (in-sample) | mouse | 345 | 0.29 | 10.26 | 50.14 | 50.14 | 90.03 | 100.00 | **0.5478** |
| VALIDATION | keyboard | 192 | 0.00 | 5.08 | 28.39 | 34.87 | 83.26 | 99.58 | 0.7708 |
| VALIDATION | mouse | 351 | 0.00 | 3.48 | 25.51 | 29.94 | 73.62 | 99.71 | 0.8462 |
| EVALUATION | keyboard | 260 | 0.00 | 5.08 | 28.60 | 38.39 | 85.59 | 100.00 | 0.7000 |
| EVALUATION | mouse | 411 | 0.00 | 7.25 | 29.86 | 34.52 | 73.62 | 99.13 | 0.7932 |

### Two conclusions this table forces

1. **The in-sample rate is 54.7% — the mathematical floor of this operating point.** A percentile calibrator fitted on its own training scores produces a near-uniform rank distribution (visible above: median 50.2 / 50.1, p10 ≈ 10, p90 ≈ 90 — textbook uniform). Thresholding at percentile 55 therefore rejects ~55% of the training data itself. **Perfect calibration on perfectly stationary data would still yield ~55% FRR.** The threshold, not the model, sets that floor.
2. **The held-out degradation is real and reproducible on two independent unseen days** (VALIDATION pooled 0.8195, EVALUATION pooled 0.7825), so it is not an artefact of 2026-09-07.

> **Methodological note:** `activate_first_profile` never reads EVALUATION ([activate.py:257-258](tools/enrollment/activate.py#L257-L258)). EVALUATION was read **only now, in this read-only diagnosis**, purely to confirm reproducibility. No artifact was refitted and no headline result was computed from it.

---

## 5. MODEL SCORE → CALIBRATED RISK TRACE

**VERIFIED FROM CODE.** Complete single-window chain:

```
FeatureWindow.{keyboard,mouse}_features
  │
  ├─ PreprocessingParams.transform      z = (x − train_mean) / train_std        ml/training/common.py:119-121
  ├─ IsolationForest.score_samples      raw ∈ ℝ,  HIGHER = MORE NORMAL           ml/training/common.py:374
  ├─ PercentileCalibrator.transform     pct = rank(raw in TRAIN raws) · 100      ml/calibration/percentile.py:36-43
  ├─ calibrated_risk = 1 − pct/100      ∈ [0,1], HIGHER = MORE ANOMALOUS         backend/app/models/service.py:79 / 135
  ├─ RiskEngine._fusion                 availability-renormalised weighted mean  backend/app/risk/engine.py:97-112
  ├─ ContextAssessment.adjust           × confidence  (= 1.0 in this run)        backend/app/risk/types.py:59-75
  ├─ EWMA   s ← α·adjusted + (1−α)·s    α = 0.4                                  backend/app/risk/engine.py:232
  ├─ _history.append(s)                 deque(maxlen = breach_n = 5)             backend/app/risk/engine.py:233
  ├─ _candidate_level                   count(s ≥ thr) ≥ breach_k = 3            backend/app/risk/engine.py:114-121
  ├─ _with_hysteresis                   up immediately; down needs 5-of-5        backend/app/risk/engine.py:123-136
  └─ DecisionPolicy.decide              ladder + 60 s cooldown + budget 2        backend/app/decisions/policy.py:30-70
```

**Direction confirmed end to end: higher anomaly → higher risk. No inversion at any stage.**

### The mapping, stated as arithmetic

```
raw score      : IsolationForest.score_samples, higher = more normal
                 keyboard TRAIN: mean −0.40368, sd 0.04756  (236 reference values)
                 mouse    TRAIN: mean −0.39247, sd 0.04589  (345 reference values)

percentile     : pct(raw) = 100 · |{r ∈ ref : r ≤ raw}| / |ref|
                 in-sample this is ~uniform on (0, 100]

calibrated risk: risk = 1 − pct/100        (decreasing, so risk ~uniform on [0, 1))

deployed gate  : breach MEDIUM  ⟺  smoothed ≥ 0.45  ⟺  percentile ≤ 55
                 breach HIGH    ⟺  smoothed ≥ 0.75  ⟺  percentile ≤ 25
```

### Boundary semantics (`_candidate_level`, [engine.py:115](backend/app/risk/engine.py#L115))

The comparison is `value >= threshold`, applied to the **smoothed** value:

| smoothed value | `>= 0.45`? | outcome |
|---|---|---|
| 0.449999… | No | not a MEDIUM breach |
| **0.450** | **Yes** | MEDIUM breach |
| 0.450001… | Yes | MEDIUM breach |

### Percentile direction — is `<= 55` consistent with the calibration function?

**Yes, and `>= 55` would be mathematically wrong.** The inverse pair at [backend/app/models/service.py:70-96](backend/app/models/service.py#L70-L96) is self-consistent:

```python
calibrated_risk_from_percentile(pct) = 1.0 − pct / 100.0        # decreasing
percentile_for_calibrated_risk(r)    = (1.0 − r) * 100.0        # inverse
```

Because the map is decreasing, the inclusive comparison flips sides: `risk ≥ t ⟺ pct ≤ (1−t)·100`. The code documents this explicitly and implements it correctly.

### One real float-boundary defect (VERIFIED — immaterial in this dataset)

```
percentile_for_calibrated_risk(0.45)  = 55.00000000000001      (not exactly 55)
calibrated_risk_from_percentile(55.0) = 0.44999999999999996    (which is < 0.45)
  -> engine:  0.44999999999999996 >= 0.45  ->  False  ->  NOT a breach
  -> FRR:     55.0 <= 55.00000000000001    ->  True   ->  counted as REJECTED
```

A window at percentile **exactly** 55.0 is counted as a false rejection by the activation measure but would **not** breach in the live engine. This contradicts the "the inclusive comparison flips sides … which is why the boundary case is asserted in the tests" claim at [activate.py:150-156](tools/enrollment/activate.py#L150-L156).

**Impact here: zero windows.** Attainable percentiles are multiples of `100/n`; for n=236 the value 55 needs k=129.8 and for n=345 it needs k=189.75 — neither is an integer. **This is defect 3 in [§16](#16-root-cause-classification); it contributes nothing to 81.95%.**

---

## 6. WHERE LIVE RISK FIRST BECOMES ELEVATED

**VERIFIED FROM DATABASE — all 62 post-activation decisions traced end to end.**

### Answer

**The first stage at which a value is unexpectedly high is the calibrated per-modality risk — immediately after the percentile lookup.** Fusion, context confidence, EWMA, K-of-N, hysteresis and the escalation ladder each behave exactly as specified *on those inputs*.

Mapped onto the options given:

| option | verdict |
|---|---|
| **A** — model score already anomalous, downstream reasonable | **TRUE** (in the sense that the *calibrated* score is already above threshold; the *raw* score is only mildly shifted — see the amplification note below) |
| B — calibration makes a reasonable score highly anomalous | **FALSE as a bug.** Calibration reports rank faithfully. But it is a **high-gain transfer function** (see [§8](#8-calibration-analysis)) and it is fitted in-sample — so it *is* part of the causal chain, just not misbehaving |
| C — fusion makes it anomalous | **FALSE.** Fusion is an exact availability-renormalised mean; verified numerically ([§11](#11-fusion-analysis)) |
| D — smoothing/EWMA keeps it elevated | **TRUE as an amplifier**, not a source |
| E — K-of-N keeps MEDIUM active too long | **TRUE as an amplifier**, not a source |
| F — threshold semantics incorrect | **FALSE** (direction and boundary are correct). The threshold *value* is inappropriate, which is a different finding |
| G — context confidence affecting the result | **FALSE.** Context is a strict no-op at confidence 1.000 ([§12](#12-context-confidence-analysis)) |
| H — some combination | **TRUE**: A + I, amplified by D + E |
| **I** — behaviour is expected given the trained model/dataset | **TRUE.** The live distribution reproduces the frozen VALIDATION distribution almost exactly |

### The amplification mechanism (VERIFIED, quantified)

The *raw* model score barely moves; the *calibrated* score moves enormously, because the reference distribution is extremely tight:

```
mouse TRAIN     raw median = −0.3804,  raw sd = 0.0459
mouse VALIDATION raw median = −0.4141

absolute shift  = 0.0337       (tiny)
in training-sd  = −0.73 sd     (moderate)
percentile      = 50  ->  25   (large)
calibrated risk = 0.50 -> 0.75 (crosses BOTH thresholds)
```

**A 0.03 shift in an IsolationForest score becomes a 0.25 shift in risk, and the MEDIUM gate sits only 0.05 above the in-sample median.** That is the whole story of the elevated risk in one calculation.

### Escalation trace — first MEDIUM and first HIGH

Joined `risk_events` × `scores` × `feature_windows`:

| i | stored_at (UTC) | quality | kbd_raw | kbd_risk | mouse_risk | fused | conf | smoothed | level | action | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 03:54:04 | FULL | −0.4429 | 0.8517 | 0.8957 | 0.8737 | 1.000 | 0.874 | **LOW** | CONTINUE | SHADOW_OR_NON_ENFORCING |
| 1 | 03:54:34 | MOUSE_ONLY | — | — | 0.8493 | 0.8493 | 1.000 | 0.864 | **LOW** | CONTINUE | SHADOW_OR_NON_ENFORCING |
| 2 | 03:55:01 | FULL | −0.3696 | 0.1653 | 0.7130 | 0.4391 | 1.000 | 0.694 | **MEDIUM** | SOFT_CHALLENGE | SHADOW_OR_NON_ENFORCING |
| 3 | 03:55:21 | KBD_ONLY | −0.4295 | 0.8051 | — | 0.8051 | 1.000 | 0.738 | MEDIUM | SOFT_CHALLENGE | SHADOW_OR_NON_ENFORCING |
| 4 | 03:55:41 | KBD_ONLY | −0.4095 | 0.6864 | — | 0.6864 | 1.000 | 0.718 | MEDIUM | SOFT_CHALLENGE | SHADOW_OR_NON_ENFORCING |
| 5 | 03:56:14 | FULL | −0.3839 | 0.4576 | 0.9594 | 0.7085 | 1.000 | 0.714 | MEDIUM | SOFT_CHALLENGE | SHADOW_OR_NON_ENFORCING |
| 6 | 03:56:44 | MOUSE_ONLY | — | — | 0.9362 | 0.9362 | 1.000 | 0.803 | MEDIUM | SOFT_CHALLENGE | SHADOW_OR_NON_ENFORCING |
| 7 | 03:57:26 | FULL | −0.3984 | 0.6144 | 0.9565 | 0.7855 | 1.000 | 0.796 | MEDIUM | SOFT_CHALLENGE | SHADOW_OR_NON_ENFORCING |
| **8** | 03:58:04 | MOUSE_ONLY | — | — | 0.7536 | 0.7536 | 1.000 | **0.779** | **HIGH** | REAUTH | SHADOW_OR_NON_ENFORCING |
| 9 | 03:58:34 | FULL | −0.3754 | 0.2754 | 0.8406 | 0.5580 | 1.000 | 0.691 | HIGH | REAUTH | **RISK_ESCALATION** |

*(full 62-row trace in [Appendix A](#appendix-a--full-live-decision-trace-62-post-activation-decisions))*

### Verified arithmetic at each transition

**i=2, LOW → MEDIUM:** `_history = [0.874, 0.864, 0.694]`, `medium_count = 3 ≥ breach_k = 3` → candidate MEDIUM; rank increases so hysteresis passes it straight through.

> **Important:** i=0 and i=1 read **LOW only because `_history` held fewer than 3 entries** — their fused risks were **0.874 and 0.849**, i.e. deeply anomalous. LOW here is an artefact of a cold deque, not of normal-looking behaviour. **The system was never actually calm at any point after activation.**

**i=8, MEDIUM → HIGH:** `_history = [0.718, 0.714, 0.803, 0.796, 0.779]`, `high_count = 3 ≥ 3` (0.803, 0.796, 0.779 all ≥ 0.75) → HIGH.

**EWMA check at i=8:** `0.4 × 0.7536 + 0.6 × 0.796 = 0.30144 + 0.4776 = 0.779` ✓ matches the stored `smoothed_score`.

### Your observed dashboard values are literal database rows

| Dashboard reading | Matching decision |
|---|---|
| `EVIDENCE: MOUSE_ONLY / FUSED RISK 0.774 / CONFIDENCE 1.000 / REASON RISK_ESCALATION` | **i=38** (04:19:46, mouse_risk = fused = **0.7739**, HIGH, REAUTH, RISK_ESCALATION) |
| `SMOOTHED RISK 0.692 / CONTEXT CONFIDENCE 1.000 / MODALITIES MOUSE_ONLY / ENFORCEMENT Applied` | **i=38** again — `smoothed_score = 0.692`, `risk_events.enforcement_applied = 1` |
| `CURRENT RISK HIGH / LAST ACTION REAUTH` | any of the 13 `HIGH`/`REAUTH`/`RISK_ESCALATION` rows |
| `CURRENT RISK MEDIUM / LAST ACTION SOFT_CHALLENGE` | any of the 15 `MEDIUM`/`SOFT_CHALLENGE`/`RISK_ESCALATION` rows |

### The decisive comparison — live vs frozen

Per-window **fused** calibrated risk (equal weights, before smoothing, before K-of-N):

| dataset | n | median | mean | ≥ 0.45 | ≥ 0.75 |
|---|---|---|---|---|---|
| Frozen TRAIN (in-sample) | 384 | 0.4900 | 0.4984 | **0.5547** | **0.1901** |
| Frozen VALIDATION (held out) | 373 | 0.7233 | 0.6858 | **0.8418** | **0.4638** |
| Frozen EVALUATION (held out) | 445 | 0.6870 | 0.6300 | 0.7663 | 0.3820 |
| **LIVE post-activation** | **60** | **0.7594** | **0.6843** | **0.8167** | **0.5333** |

**The live legitimate user's fused-risk distribution is statistically indistinguishable from the held-out VALIDATION distribution that produced the 82% FRR.** The runtime is reproducing its own validation measurement faithfully. *This is the single strongest piece of evidence that there is no defect in the live scoring/fusion/decision path.*

Live smoothed-score distribution: n=60, min **0.3933**, p10 0.4989, median **0.6983**, mean 0.6875, p90 0.8130, p95 0.8589, max 0.8835 — **95.0% ≥ 0.45**, **31.7% ≥ 0.75**.

---

## 7. WHY MEDIUM PERSISTED

**VERIFIED FROM CODE AND DATA.**

### The transition logic in full

`_with_hysteresis`, [backend/app/risk/engine.py:123-136](backend/app/risk/engine.py#L123-L136):

```python
def _with_hysteresis(self, candidate: RiskLevel) -> RiskLevel:
    current = self._risk_level
    if _RANK[candidate] >= _RANK[current]:
        return candidate                              # UPWARD: immediate, no conditions
    if len(self._history) < self.settings.risk.breach_n:
        return current                                # need a full window of 5 samples
    boundary = (self.settings.risk.high_threshold
                if current == RiskLevel.HIGH
                else self.settings.risk.medium_threshold)
    if all(value < boundary for value in self._history):
        return candidate                              # DOWNWARD: ALL 5 must clear
    return current
```

`_candidate_level`, [engine.py:114-121](backend/app/risk/engine.py#L114-L121), counts over the same deque of **smoothed** values:

```python
medium_count = sum(v >= medium_threshold for v in self._history)   # 0.45
high_count   = sum(v >= high_threshold   for v in self._history)   # 0.75
if high_count   >= breach_k: return HIGH,   high_count             # k = 3
if medium_count >= breach_k: return MEDIUM, high_count
return LOW, high_count
```

### Exact conditions for every transition

| transition | requirement |
|---|---|
| LOW → MEDIUM | ≥ **3 of last 5** smoothed values ≥ 0.45 |
| MEDIUM → HIGH | ≥ **3 of last 5** smoothed values ≥ 0.75 |
| HIGH → MEDIUM | deque full (5) **and all 5** smoothed < **0.75**, and candidate resolves to MEDIUM |
| **MEDIUM → LOW** | deque full (5) **and all 5** smoothed < **0.45** |
| HIGH → LOW (direct) | possible only if all 5 < 0.75 **and** `medium_count < 3` in the same call |

### Answers to the specific questions asked

**How many good windows should be required to leave MEDIUM?**
**All 5 entries in the deque must be below 0.45.** Because the deque stores *smoothed* (EWMA) values rather than instantaneous ones, EWMA inertia adds further delay. Computed from the observed `smoothed = 0.692` with α = 0.4:

| instantaneous fused risk held constant | windows until 5 consecutive smoothed < 0.45 | wall-clock (30 s windows) |
|---|---|---|
| 0.00 (percentile 100 — perfect) | 5 | ≈ 150 s |
| 0.10 (percentile 90) | 6 | ≈ 180 s |
| 0.20 (percentile 80) | 6 | ≈ 180 s |
| 0.30 (percentile 70) | 6 | ≈ 180 s |
| 0.40 (percentile 60) | 8 | ≈ 240 s |
| ≥ 0.45 | **never** | ∞ |

EWMA trace from 0.692 at x = 0.30: `0.535 → 0.441 → 0.385 → 0.351 → 0.330 → 0.318`.

**Is there a different threshold for entering vs leaving MEDIUM?**
**No — the same 0.45 boundary is used in both directions.** The asymmetry is in the *counting*: **3-of-5 to enter, 5-of-5 to leave**. That asymmetry *is* the hysteresis, and it is intentional and correctly implemented.

**Can a score below 0.450 actually return the state to LOW?**
**Yes — but only 5–6 consecutive such windows can.** A single sub-threshold window cannot, by construction.

**If yes, under what exact conditions?**
`len(_history) == 5` **and** every one of the 5 smoothed values `< 0.45` **and** `_candidate_level()` returns LOW (which follows automatically, since `medium_count` is then 0).

**Then why did it never happen?**
Because the required run of clean windows is astronomically unlikely at this model's score distribution:

- The run needs ~6 consecutive windows at instantaneous fused risk ≤ 0.30, i.e. **percentile ≥ 70**.
- Only **~30% of even in-sample TRAIN windows** reach percentile ≥ 70.
- Live, only **18.3% of windows** had fused risk < 0.45.
- `P(5 in a row) ≈ 0.183⁵ ≈ 2 × 10⁻⁴` per window position → expected wait of **thousands of windows** (many hours of continuous work).
- **Observed: only 3 of 60 smoothed values fell below 0.45** (0.3933, 0.399, 0.426) — and **never consecutively**.

**MEDIUM is effectively an absorbing state under this model/threshold combination.**

### The downward logic itself is not broken

`HIGH → MEDIUM` fired **twice** during the live run:

- **i=16** (04:03:21): `_history = [0.695, 0.668, 0.596, 0.698, 0.709]` — all < 0.75 → `medium_count = 5 ≥ 3` → **MEDIUM**. ✓
- **i=45** (04:25:28): same rule. ✓

So the mechanism works; it is starved of qualifying input. **The failure is upstream, in the score distribution, not in the state machine.**

### What is *not* in the code (VERIFIED absence)

There is **no** minimum state duration, **no** hold period, **no** explicit decay term beyond the EWMA, **no** separate recovery threshold, and **no** consecutive-good-window counter distinct from the 5-of-5 rule. `RiskConfig` contains only the parameters listed in [§13](#13-threshold--operating-point-verification).

### Cooldown is not the cause of persistence

The alternating `RISK_ESCALATION` / `ACTION_COOLDOWN` pattern throughout the trace is the **60 s per-action cooldown** working correctly ([backend/app/decisions/policy.py:59-69](backend/app/decisions/policy.py#L59-L69)). It suppresses **actions**, never the risk level. `ACTION_BUDGET` was never hit (`budget_exhausted` never fired; no `ACTION_BUDGET_EXHAUSTED` alerts exist).

---

## 8. CALIBRATION ANALYSIS

Two distinct things are called "calibration" in this system. Both were checked.

### (a) Model-score calibration — the percentile calibrator

**VERIFIED — this is the root of the amplification.**

`PercentileCalibrator.fit(raw_scores)` at [ml/training/common.py:316-322](ml/training/common.py#L316-L322):

```python
model.fit(X_scaled)
raw_scores = model.normality_score(X_scaled)      # IN-SAMPLE scores of the training set
calibrator = PercentileCalibrator.fit(raw_scores) # reference distribution = those scores
```

Consequences, all verified:

1. **The reference distribution is in-sample.** IsolationForest scores its own training points as more normal than unseen points from the same process, so every held-out genuine window is biased toward low percentiles *before any real drift is considered*.
2. **The reference distribution is extremely tight** — keyboard raw sd **0.0476**, mouse **0.0459** (from `metrics_at_training` inside the artifacts). This makes the percentile map a **high-gain transfer function**: a 0.7-sd raw shift moves percentile 50 → 25 and risk 0.50 → 0.75.
3. **It is stored in the artifact and never refitted at runtime.** `score_window` only calls `transform` ([common.py:383](ml/training/common.py#L383)). Live scoring therefore uses exactly the 236 / 345 frozen TRAIN reference values — which is why the offline re-scoring in this report matches live behaviour exactly.
4. **Percentile 0.0 is attainable out-of-sample.** `searchsorted(..., side="right")` returns 0 for a raw score below every reference value → `pct = 0.0` → `risk = 1.0000`. Observed live at **i=11** (`mouse_risk = 1.0000`). Structurally correct, and a symptom of the untruncated mouse acceleration tail.

**Does calibration explain the elevated scores?** Partly, and the split is quantifiable. Same pooled VALIDATION evidence, swept across candidate thresholds:

| `medium_threshold` | ⟺ percentile | TRAIN (in-sample) rate | VALIDATION FRR |
|---|---|---|---|
| **0.45 (deployed)** | 55 | **0.5473** | **0.8195** |
| 0.50 | 50 | 0.4991 | 0.7974 |
| 0.60 | 40 | 0.3993 | 0.7109 |
| 0.70 | 30 | 0.2978 | 0.5562 |
| 0.75 | 25 | 0.2496 | 0.4936 |
| 0.80 | 20 | 0.1979 | 0.4052 |
| 0.90 | 10 | 0.0981 | 0.2486 |
| 0.95 | 5 | 0.0482 | 0.1308 |
| 0.98 | 2 | 0.0172 | 0.0368 |
| 0.99 | 1 | 0.0086 | 0.0184 |

**Attribution:** of the 82 points of FRR, **~55 points are the operating point itself** (visible in the in-sample column) and **~27 points are out-of-sample score degradation**.

### (b) The runtime `CALIBRATING` phase

**VERIFIED CLEAN — this is not a contributor to the elevated risk.**

| check | result |
|---|---|
| Requirement to leave CALIBRATING | `calibration_windows = 10` scored windows ([config/risk.development.yaml](config/risk.development.yaml)), enforced at [backend/app/risk/state.py:52-56](backend/app/risk/state.py#L52-L56) |
| Are pre-activation `profile_version='unavailable'` records counted? | **No.** The counting SQL at [backend/app/runtime/orchestrator.py:655-663](backend/app/runtime/orchestrator.py#L655-L663) filters `profile_version = ?` (the active version) **and** requires `json_extract(score_json,'$.keyboard.available')=1 OR '$.mouse.available'=1`. Confirmed in data: 1221 rows carry `'unavailable'` and contributed nothing. |
| Which modalities count? | Either — a window counts if **at least one** model produced a score. `INSUFFICIENT_DATA` windows are excluded by the `available` clause. |
| Is calibration per-modality? | **No.** The runtime gate is a single per-user **count**, not a per-modality statistical fit. |
| Global or per-user? | **Per-user** (`WHERE user_id = ?`). |
| Does it change the risk distribution? | **No.** Nothing is re-normalised at `CALIBRATING → ACTIVE`. The only change is `shadow_mode` flipping off, which alters *actions*, not *scores*. |
| Can it explain the elevated scores? | **No.** |
| Observed behaviour | `CALIBRATING` covered decisions i=0…i=8 (9 rows, all `shadow_mode=1`, all `SHADOW_OR_NON_ENFORCING`); `ACTIVE` began at **i=9**, i.e. exactly the 10th scorable window. **The PLAN.md §5.3 shadow period happened as designed.** |
| Why `ENROLLING → CALIBRATING` was instant | `enrollment_windows = 1283 ≥ min_windows = 20` and `distinct_days = 5 ≥ min_distinct_days = 3`, counted over the whole non-drill history — satisfied on the first window after activation. |

---

## 9. TRAINING DATA REPRESENTATIVENESS

**VERIFIED — measured in the model's own scaled space (TRAIN mean / std from the artifacts). No refitting was performed.**

### TRAIN partition composition

| | count |
|---|---|
| total TRAIN windows | **389** |
| FULL | 197 |
| KBD_ONLY | 39 |
| MOUSE_ONLY | 148 |
| INSUFFICIENT_DATA | 5 |
| **keyboard-eligible (= model rows)** | **236** |
| **mouse-eligible (= model rows)** | **345** |
| 2026-09-05 share | 101 windows → **39 keyboard rows (16.5%)**, **95 mouse rows (27.5%)** |
| 2026-09-06 share | 288 windows → 197 keyboard rows, 250 mouse rows |

**INFERENCE:** the `min_train_distinct_days: 2` policy ([config/ml.development.yaml](config/ml.development.yaml)) is satisfied *nominally*, but the identity baseline is **dominated by a single day** — 09-06 supplies 83.5% of keyboard evidence. The policy's own stated purpose ("so that a single day's posture, device position, or mood cannot alone define the identity baseline") is only weakly met.

### Do the two training days differ? Yes.

Deviation of each group from the model's own scaled space (`|z|` = absolute standardised feature values; RMS z = 1.0 means "as spread as TRAIN"):

**Keyboard**

| group | n | mean\|z\| | p50\|z\| | p95\|z\| | RMS z | worst feature mean shift |
|---|---|---|---|---|---|---|
| TRAIN (all) | 236 | 0.580 | 0.392 | 1.707 | **1.000** | 0.000 |
| TRAIN-0905 | 39 | 0.555 | 0.398 | 1.597 | 0.994 | 0.454 (`homerow_dwell_ratio`) |
| TRAIN-0906 | 197 | 0.585 | 0.392 | 1.767 | 1.001 | 0.090 |
| **VALIDATION** | 192 | 0.736 | 0.474 | 2.515 | **1.344** | 0.655 (`dwell_p90`) |
| **EVALUATION** | 260 | 0.707 | 0.438 | 2.194 | **1.983** | 0.650 (`dwell_median`) |
| LIVE-0910 | 38 | 0.618 | 0.393 | 2.275 | 0.981 | 0.220 (`modifier_ratio`) |

**Mouse**

| group | n | mean\|z\| | p50\|z\| | p95\|z\| | RMS z | worst feature mean shift |
|---|---|---|---|---|---|---|
| TRAIN (all) | 345 | 0.617 | 0.435 | 1.755 | **1.000** | 0.000 |
| TRAIN-0905 | 95 | 0.721 | 0.491 | 2.259 | **1.264** | 0.685 (`jerk_mean`) |
| TRAIN-0906 | 250 | 0.577 | 0.422 | 1.607 | **0.879** | 0.260 (`jerk_mean`) |
| **VALIDATION** | 351 | 1.094 | 0.551 | 2.391 | **14.068** | **4.223 (`accel_std`)** |
| **EVALUATION** | 411 | 1.968 | 0.581 | 2.074 | **65.670** | **12.381 (`accel_std`)** |
| LIVE-0910 | 44 | 0.925 | 0.581 | 2.545 | **1.755** | 2.780 (`accel_mean`) |

### Top TRAIN → VALIDATION mean shifts (in training-sd units)

**Keyboard — moderate and coherent:**

| feature | train mean | val mean | shift | val sd | train sd | live mean |
|---|---|---|---|---|---|---|
| `dwell_p90` | −0.00 | +0.66 | **+0.66** | 2.20 | 1.00 | +0.11 |
| `modifier_ratio` | −0.00 | +0.65 | **+0.65** | 1.44 | 1.00 | +0.22 |
| `dwell_mean` | +0.00 | +0.57 | +0.57 | 1.99 | 1.00 | +0.09 |
| `rollover_ratio` | +0.00 | +0.56 | +0.56 | 1.39 | 1.00 | +0.20 |
| `dwell_median` | −0.00 | +0.54 | +0.54 | 1.77 | 1.00 | −0.07 |
| `dwell_std` | −0.00 | +0.37 | +0.37 | 2.13 | 1.00 | +0.11 |
| `homerow_dwell_ratio` | +0.00 | −0.28 | −0.28 | 1.09 | 1.00 | +0.11 |
| `burst_mean_length` | +0.00 | −0.28 | −0.28 | 1.06 | 1.00 | −0.12 |

**Mouse — severe and heavy-tailed:**

| feature | train mean | val mean | shift | val sd | train sd | live mean |
|---|---|---|---|---|---|---|
| `accel_std` | −0.00 | +4.22 | **+4.22** | **63.05** | 1.00 | +0.61 |
| `accel_mean` | +0.00 | +1.96 | **+1.96** | **12.11** | 1.00 | **+2.78** |
| `jerk_mean` | +0.00 | +1.56 | **+1.56** | **8.90** | 1.00 | **+2.50** |
| `scroll_burst_mean` | −0.00 | +0.81 | +0.81 | 4.44 | 1.00 | −0.35 |
| `velocity_max` | +0.00 | +0.70 | +0.70 | 8.60 | 1.00 | +0.51 |
| `pause_count_per_segment` | −0.00 | −0.60 | −0.60 | 0.76 | 1.00 | −0.74 |
| `curvature_mean` | +0.00 | +0.54 | +0.54 | 1.27 | 1.00 | +0.33 |
| `curvature_std` | +0.00 | +0.51 | +0.51 | 1.26 | 1.00 | +0.46 |

**INFERENCE (well supported):** the mouse acceleration / jerk features are **unbounded ratio statistics with no winsorisation or clipping**, and the two training days simply never contained the tail. `PreprocessingParams` standardises with **mean and standard deviation** ([ml/training/common.py:124-132](ml/training/common.py#L124-L132)), which is not robust to that kind of tail. A single fast flick on an unseen day therefore lands tens of training-sd away, and the IsolationForest treats it as far outside the learned region.

### Raw normality score by group (higher = more normal)

**Keyboard**

| group | n | mean | sd | min | p5 | median | max |
|---|---|---|---|---|---|---|---|
| TRAIN | 236 | −0.4037 | 0.0476 | −0.6182 | −0.5067 | −0.3871 | −0.3518 |
| TRAIN-0905 | 39 | −0.3931 | 0.0431 | −0.6182 | −0.4532 | −0.3803 | −0.3600 |
| TRAIN-0906 | 197 | −0.4058 | 0.0481 | −0.6109 | −0.5145 | −0.3899 | −0.3518 |
| VALIDATION | 192 | −0.4285 | 0.0553 | −0.6665 | −0.5270 | −0.4128 | −0.3525 |
| EVALUATION | 260 | −0.4258 | 0.0567 | −0.6328 | −0.5382 | −0.4119 | −0.3509 |
| LIVE-0910 | 38 | −0.4166 | 0.0499 | −0.5397 | −0.5347 | −0.4037 | −0.3522 |

**Mouse**

| group | n | mean | sd | min | p5 | median | max |
|---|---|---|---|---|---|---|---|
| TRAIN | 345 | −0.3925 | 0.0459 | −0.6102 | −0.4737 | −0.3804 | −0.3364 |
| TRAIN-0905 | 95 | −0.4124 | 0.0588 | −0.6102 | −0.5346 | −0.4029 | −0.3410 |
| TRAIN-0906 | 250 | −0.3849 | 0.0372 | −0.5462 | −0.4562 | −0.3755 | −0.3364 |
| VALIDATION | 351 | −0.4257 | 0.0575 | −0.6777 | −0.5396 | −0.4141 | −0.3371 |
| EVALUATION | 411 | −0.4121 | 0.0463 | −0.6747 | −0.4855 | −0.4077 | −0.3397 |
| LIVE-0910 | 44 | −0.4403 | 0.0535 | −0.6255 | −0.5447 | −0.4291 | −0.3527 |

Note how **small the raw-score shifts are in absolute terms** (mouse median −0.3804 → −0.4141 → live −0.4291) compared with how large the resulting risk shifts are. This is the amplification described in [§6](#6-where-live-risk-first-becomes-elevated) and [§8](#8-calibration-analysis).

### Verdict on this section

**The observed FRR and live behaviour ARE consistent with a genuine train/validation distribution mismatch**, and the mismatch is far larger in mouse than in keyboard. **But drift alone cannot explain the result** — the in-sample floor is already 54.7%. Both causes are needed to reach 82%.

---

## 10. KEYBOARD VS MOUSE ANALYSIS

**VERIFIED. The mouse model is materially worse on every measure.**

### Live post-activation calibrated risk per modality (from `scores.score_json`)

| model | n | min | median | mean | p90 | p95 | p99 | max | ≥ 0.45 | ≥ 0.75 |
|---|---|---|---|---|---|---|---|---|---|---|
| keyboard | 38 | 0.0042 | 0.6441 | 0.5890 | 0.9284 | 0.9710 | 0.9772 | 0.9788 | **0.711** | **0.395** |
| **mouse** | 44 | 0.1652 | **0.8261** | **0.7847** | 0.9696 | 0.9822 | 0.9938 | **1.0000** | **0.909** | **0.705** |

### Frozen VALIDATION agrees

| model | n | FRR at 0.45 | median percentile |
|---|---|---|---|
| keyboard | 192 | 0.7708 | 28.39 |
| mouse | 351 | 0.8462 | 25.51 |

`MOUSE_ONLY` at **0.8895** is the worst cell in the whole matrix; `KBD_ONLY` at **0.5000** is the best.

### Representative FULL windows showing the split

Keyboard is often reasonable while mouse saturates:

| i | kbd_raw | kbd_risk | mouse_risk | fused | resulting level |
|---|---|---|---|---|---|
| 5 | −0.3839 | 0.4576 | **0.9594** | 0.7085 | MEDIUM |
| 12 | −0.3563 | **0.0169** | **0.8058** | 0.4114 | HIGH (held) |
| 24 | −0.3575 | **0.0212** | 0.4029 | 0.2120 | MEDIUM (held) |
| 33 | −0.3786 | 0.3390 | **0.9159** | 0.6275 | MEDIUM |
| 40 | −0.5116 | 0.9492 | 0.9739 | 0.9615 | HIGH |
| 42 | −0.3522 | **0.0042** | — | 0.0042 | HIGH (held) |

`mouse_risk = 1.0000` at **i=11** indicates a raw score **below every one of the 345 training reference values** — percentile 0.0. This is the untruncated acceleration tail reaching the live path.

### Model quality summary

*INFERENCE:* the keyboard model is weak-but-plausible (median live risk 0.64, i.e. median percentile ≈ 36). The mouse model is **not usable at the deployed threshold** (median live risk 0.83, i.e. median percentile ≈ 17), and because it is available on **90.7% of windows** (`mouse_window_fraction = 0.9066`) versus keyboard's 56.3%, it dominates the fused signal.

---

## 11. FUSION ANALYSIS

**VERIFIED FROM CODE AND CONFIRMED BY ARITHMETIC — fusion is correct and applies no penalty for a missing modality.**

`_fusion`, [backend/app/risk/engine.py:97-112](backend/app/risk/engine.py#L97-L112):

```python
weighted = []
if score.keyboard.available and score.keyboard.calibrated_score is not None:
    weighted.append((score.keyboard.calibrated_score, keyboard_weight))
if score.mouse.available and score.mouse.calibrated_score is not None:
    weighted.append((score.mouse.calibrated_score, mouse_weight))
total = sum(weight for _, weight in weighted)
if not weighted or total <= 0:
    raise ValueError("no positively weighted calibrated modality is available")
fused = sum(value * weight for value, weight in weighted) / total     # renormalised
```

This is **availability renormalisation**, not zero-imputation. With `keyboard_weight = mouse_weight = 0.5`:

| evidence | formula | reduces to | verified live example |
|---|---|---|---|
| **FULL** | `(0.5·k + 0.5·m) / 1.0` | arithmetic **mean** | i=0: (0.8517 + 0.8957)/2 = **0.8737** ✓ |
| **KBD_ONLY** | `0.5·k / 0.5` | **k exactly** | i=3: k = 0.8051 → fused **0.8051** ✓ |
| **MOUSE_ONLY** | `0.5·m / 0.5` | **m exactly** | i=1: m = 0.8493 → fused **0.8493** ✓ |

### Specific verifications requested

| claim to check | result |
|---|---|
| Does `MOUSE_ONLY` receive an artificially high risk because keyboard is absent? | **No.** Fused equals the mouse risk to the last digit. Its higher breach rate (0.909 live, 0.8895 on VALIDATION) is entirely the mouse model's own scores. |
| Does `KBD_ONLY` receive an artificial penalty? | **No.** Fused equals the keyboard risk exactly. `KBD_ONLY` is in fact the **best-behaved** evidence class (VALIDATION FRR 0.50, median percentile 56.78, per-window fused ≥0.45 only 0.5000). |
| Does `FULL` behave as expected relative to the individual modality risks? | **Yes — it is exactly the mean**, neither amplifying nor damping. Because mouse is the worse model, FULL sits between the two: per-window fused ≥ 0.45 is FULL 0.8353, MOUSE_ONLY 0.8895, KBD_ONLY 0.5000. |
| Is a missing modality ever imputed? | **Never.** [ml/training/common.py:359-368](ml/training/common.py#L359-L368) returns `available=False` with `raw_score=None`; [ml/training/common.py:87-101](ml/training/common.py#L87-L101) excludes the row from training; [backend/app/risk/engine.py:85-95](backend/app/risk/engine.py#L85-L95) only *requires* the modalities the quality label promises. |

### Per-window fused risk by evidence class (frozen VALIDATION)

| quality | n | ≥ 0.45 | ≥ 0.75 | median |
|---|---|---|---|---|
| FULL | 170 | 0.8353 | 0.4412 | 0.7225 |
| KBD_ONLY | 22 | **0.5000** | 0.1818 | **0.4322** |
| MOUSE_ONLY | 181 | **0.8895** | **0.5193** | **0.7507** |
| **all** | 373 | 0.8418 | 0.4638 | 0.7233 |

**Option C is ruled out. Fusion contributes nothing to the problem.**

---

## 12. CONTEXT CONFIDENCE ANALYSIS

**VERIFIED — confidence is 1.000 for a mundane configuration reason, and the entire context layer is currently a mathematical no-op. It is not amplifying anything.**

### Why exactly 1.000

1. [config/app_categories.yaml](config/app_categories.yaml) contains `"process_categories": {}` — **deliberately empty**, with `"default_category": "UNKNOWN"`. This is an explicitly supported configuration (AGENTS.md constraint 1 forbids an application-specific integration list).
2. Every application therefore resolves to `UNKNOWN`, whose bootstrap weight in [config/context.development.yaml](config/context.development.yaml) is **`UNKNOWN: 1.0`** — the highest of all seven categories (PRODUCTIVITY 0.9, BROWSING 0.85, DEVELOPMENT 0.9, CREATIVE 0.7, GAMING 0.65, SYSTEM 0.9, UNKNOWN **1.0**).
3. `_application_confidence` ([backend/app/risk/context.py:168-180](backend/app/risk/context.py#L168-L180)) returns that prior until an app accumulates `min_empirical_observations = 4` focus-weighted genuine observations. `assess` blends per-app confidences by focus share and clamps with `min(1.0, max(floor, blended))` → **1.0**.

### Observed values (VERIFIED FROM DATA)

```
context_confidence in risk_events:
  1.0                 642 rows
  0.9999999999999999   32 rows      <- float artefacts of the focus-share blend
  0.9999999999999998    1 row

confidence_source in decision_json:
  NEUTRAL_FALLBACK    614   (pre-activation: no attributable app focus)
  BOOTSTRAP            61   (all scored post-activation decisions)
  EMPIRICAL             0   <- never occurs
```

### Does it influence risk? Can it amplify risk?

`ContextAssessment.adjust`, [backend/app/risk/types.py:59-75](backend/app/risk/types.py#L59-L75):

```python
if not self.normalization_available:
    return fused * self.confidence           # <- THIS branch, with confidence = 1.0
...
standardized = (fused - self.context_location) / self.context_scale
adjusted = self.baseline_location + standardized * self.baseline_scale
return min(1.0, max(0.0, adjusted))
```

- **In this run: `adjusted = fused × 1.0 = fused`.** The context layer is a **strict no-op**. Confirmed in the trace — `smoothed` is the EWMA of `fused` at every one of the 60 rows.
- **In `MULTIPLICATIVE_DAMPING` mode context can only reduce risk**, since `confidence ∈ (0,1]` (enforced by `__post_init__`). 1.0 is the neutral maximum. **It cannot amplify.**
- **In `PER_CONTEXT_NORMALIZATION` mode it *could* raise a score** (the affine remap is clamped to [0,1] but not bounded above by `fused`). That path never armed — see below.
- Context **only modifies the score multiplicatively / affinely**; it never enters an identity-model feature array and never authenticates by itself ([context.py:1-25](backend/app/risk/context.py#L1-L25)).

### Why `PER_CONTEXT_NORMALIZATION` never armed — a real design deadlock

`adjustment_mode: PER_CONTEXT_NORMALIZATION` **is** configured, but `_build_assessment` ([backend/app/risk/context.py:267-284](backend/app/risk/context.py#L267-L284)) requires all of:

- `empirical_coverage ≥ 0.5` (apps covering ≥ half the window must each have ≥ 4 observations), **and**
- a per-user baseline with `weight ≥ min_empirical_observations = 4`.

Those statistics come **only** from `observe_genuine`, which the orchestrator calls **only when `risk_level == LOW`** ([backend/app/runtime/orchestrator.py:701-720](backend/app/runtime/orchestrator.py#L701-L720)):

```python
if (outcome.decision.risk_level == RiskLevel.LOW
        and outcome.decision.fused_score is not None):
    self.context_layer.observe_genuine(...)
```

**Exactly 2 LOW decisions ever occurred** (i=0, i=1) → baseline weight = 2 < 4 → **the layer that exists to absorb context-specific score offsets can never activate once risk is elevated.**

Worse: those two "genuine" observations recorded **fused risks of 0.8737 and 0.8493** as the genuine reference — and both were LOW only because `_history` had fewer than 3 entries. So the little context evidence that was gathered is itself mislabelled.

**This is defect 1 in [§16](#16-root-cause-classification). It is a genuine implementation/design defect, but it is not the cause of the elevated risk — a no-op layer cannot raise a score.**

### Drill-isolation behaviour (VERIFIED PRESENT, NOT EXERCISED)

Requested check: *attacker/drill data must not update the empirical context baseline.* Confirmed at two independent levels:

| level | mechanism | location |
|---|---|---|
| Measurement isolation (in-memory) | `suspend_learning(True)` is set for the whole process whenever `drill_label` is present; `observe_genuine` then returns immediately | [orchestrator.py:126-131](backend/app/runtime/orchestrator.py#L126-L131), [context.py:112-149](backend/app/risk/context.py#L112-L149) |
| Corpus isolation (persistent) | `DRILL_EXCLUSION_PREDICATE = "session_id NOT IN (SELECT session_id FROM drill_sessions)"`, applied by the corpus loader and by the enrollment-progress query | [backend/app/storage/drill.py](backend/app/storage/drill.py), [tools/collection/corpus.py:105](tools/collection/corpus.py#L105), [orchestrator.py:637](backend/app/runtime/orchestrator.py#L637) |
| Build guardrail | G12 in `tools/guardrails/check.py` fails the build if a corpus loader stops filtering | [backend/app/storage/drill.py:38-41](backend/app/storage/drill.py#L38-L41) |

**No drill was run. `drill_sessions` contains 0 rows**, so the machinery has had nothing to exclude. Also note `assess()` keeps working during a drill from what was learned during genuine operation — the correct comparison baseline.

---

## 13. THRESHOLD / OPERATING-POINT VERIFICATION

**VERIFIED.** Active configuration is [config/risk.development.yaml](config/risk.development.yaml) — `config_version = t013-synthetic-development-v1`, `config_checksum = 128193724a89c255688e63d691324e7f50651becff1436686cb89ce8fd6e3841`, matching all 675 `risk_events`. The `risk.demo-live-single-day.yaml` override (`…-demo-single-day`) was **not** used.

### Active values and their effect

| parameter | value | effect |
|---|---|---|
| `risk.medium_threshold` (low/medium boundary) | **0.45** | ⟺ percentile ≤ **55** of the user's own enrollment score distribution |
| `risk.high_threshold` (medium/high boundary) | **0.75** | ⟺ percentile ≤ **25** |
| `risk.ewma_alpha` | **0.4** | 60% of the previous smoothed value is retained each window |
| `risk.breach_k` / `risk.breach_n` | **3 / 5** | 3-of-5 to escalate; 5-of-5 below the boundary to de-escalate |
| `risk.cooldown_seconds` | **60** | per-action suppression → `reason_code = ACTION_COOLDOWN` |
| `risk.action_budget` | **2** | applied actions per rolling 60 s window → `ACTION_BUDGET` |
| `risk.keyboard_weight` / `risk.mouse_weight` | **0.5 / 0.5** | equal, renormalised on availability |
| `enrollment.min_windows` | 20 | ENROLLING → CALIBRATING |
| `enrollment.min_distinct_days` | 3 | ENROLLING → CALIBRATING |
| `enrollment.calibration_windows` | 10 | CALIBRATING → ACTIVE |
| **challenge threshold** | **none separate** | derived from level |
| **reauth threshold** | **none separate** | derived from level |
| **hysteresis parameters** | **none separate** | reuses `medium_threshold` / `high_threshold` as the exit boundary |
| **minimum evidence requirement** | `INSUFFICIENT_DATA` → `UNAVAILABLE`, excluded from `_history` | |
| **recovery requirements** | **none separate** | the 5-of-5 all-below rule is the only recovery mechanism |

### The escalation ladder ([backend/app/decisions/policy.py:30-39](backend/app/decisions/policy.py#L30-L39))

```
LOW    -> CONTINUE
MEDIUM -> SOFT_CHALLENGE
HIGH   -> REAUTH        (and TERMINATE if high_count >= breach_n, i.e. 5 of 5)
other  -> NONE
```

`TERMINATE` was **never** requested during the live run — `high_count` never reached 5.

Config validation refuses inconsistent values at load time ([backend/app/risk/config.py:56-62](backend/app/risk/config.py#L56-L62)): both weights must be positive, `medium_threshold < high_threshold`, `breach_k ≤ breach_n`.

### Mapping the observed values onto these thresholds

**`smoothed risk = 0.692` → HIGH.** This is decision **i=38** (04:19:46, MOUSE_ONLY, fused 0.7739, smoothed 0.692). It displays **HIGH even though 0.692 < 0.75**, because:

1. `_candidate_level` counts breaches **across the last 5 smoothed values**, not the current one.
2. At i=38 the deque was inside a HIGH run (`… 0.858, 0.638, 0.692 …`), so `high_count` was still ≥ 3 — or, where it was not, `_with_hysteresis` held HIGH because **all 5** entries were not yet below 0.75.
3. Therefore a single sub-0.75 window can never demote the level.

Other instances of the same mechanism in the trace:

| i | smoothed | displayed level | why |
|---|---|---|---|
| 9 | 0.691 | HIGH | inside a HIGH run; not all 5 below 0.75 |
| 13 | 0.668 | HIGH | ditto |
| 14 | 0.596 | HIGH | ditto |
| **25** | **0.426** | **MEDIUM** | **below 0.45**, but the other four deque entries were above it |
| 30 | 0.484 | MEDIUM | 5-of-5 rule not satisfied |
| 42 | 0.393 | HIGH | lowest smoothed value in the whole run; still held |

**This is documented hysteresis behaving exactly as written — not a display bug and not an inconsistency between the dashboard and the engine.** The dashboard renders `risk_level` and `smoothed_score` from the same `RiskDecision` row; they are simply not required to agree pointwise, because the level is a function of the last five values.

---

## 14. ENFORCEMENT SEMANTICS

**VERIFIED — real enforcement was genuinely suppressed. The Overview label is the only problem, and it is cosmetic.**

### There are two different `enforcement_applied` fields with different meanings

| field | meaning | observed value |
|---|---|---|
| `risk_events.enforcement_applied` | **Policy-level:** an enforcing action was *selected and not suppressed by cooldown or budget* ([policy.py:65-70](backend/app/decisions/policy.py#L65-L70)) | **1** for 28 rows (15 SOFT_CHALLENGE + 13 REAUTH) |
| `decisions.enforcement_applied` + `decisions.outcome` | **Execution-level:** what the adapter actually did | **0** for all 675 rows; `outcome = 'ENFORCEMENT_DISABLED'` for the 28 |

### `decisions` table, complete breakdown

```
action=NONE            applied=0  outcome=STATE_GATED_NO_ENFORCEMENT   n=638
action=SOFT_CHALLENGE  applied=0  outcome=ENFORCEMENT_DISABLED         n= 15
action=REAUTH          applied=0  outcome=ENFORCEMENT_DISABLED         n= 13
action=SOFT_CHALLENGE  applied=0  outcome=STATE_GATED_NO_ENFORCEMENT   n=  6
action=CONTINUE        applied=0  outcome=STATE_GATED_NO_ENFORCEMENT   n=  2
action=REAUTH          applied=0  outcome=STATE_GATED_NO_ENFORCEMENT   n=  1
```

### Verification chain

1. `enabled: false` in [config/enforcement.development.yaml](config/enforcement.development.yaml).
2. `prompt_enabled = enabled and native_prompt = False`; `workstation_lock_enabled = enabled and lock_workstation = False` ([backend/app/decisions/config.py:51-57](backend/app/decisions/config.py#L51-L57)).
3. The native adapter returns `AdapterResult(ActionStatus.SKIPPED, "ENFORCEMENT_DISABLED")` **before touching the OS** ([backend/app/decisions/native.py:138](backend/app/decisions/native.py#L138), [native.py:167](backend/app/decisions/native.py#L167)).
4. `EnforcementService.execute` records that outcome ([backend/app/decisions/adapters.py:176-221](backend/app/decisions/adapters.py#L176-L221)).

**Conclusion: no native prompt was ever shown, and the workstation was never locked. This is confirmed by recorded data, not merely by reading the config.** Correct shadow-mode behaviour.

### What "Applied" means in the dashboard

[dashboard/src/views/OverviewView.tsx:45-46](dashboard/src/views/OverviewView.tsx#L45-L46):

```tsx
<dt>Enforcement</dt>
<dd>{latest?.enforcement_applied ? "Applied" : "Not applied"}</dd>
```

`latest` is the streamed `RiskDecision` ([dashboard/src/protocol.ts:41](dashboard/src/protocol.ts#L41)), i.e. the **`risk_events` policy flag**.

| question | answer |
|---|---|
| What does "Applied" mean internally? | *"An enforcing action was selected and dispatched to the enforcement service, and was not suppressed by cooldown or action budget."* |
| Does it mean the logical action was selected? | **Yes — that is exactly and only what it means.** |
| Was native enforcement actually suppressed? | **Yes**, at the adapter, recorded as `decisions.outcome = ENFORCEMENT_DISABLED`. |
| Is this merely a dashboard terminology issue? | **Yes.** The truthful execution result exists in `decisions.outcome` but is **not surfaced on the Overview**. [dashboard/src/views/SettingsView.tsx:26-35](dashboard/src/views/SettingsView.tsx#L26-L35) states the real state correctly ("Real enforcement is **disabled** … no native prompt is shown and the workstation is never locked"). |

**Your instruction not to read "Enforcement: Applied" as real workstation enforcement is correct, and the data confirms it independently.** No UI change was made.

---

## 15. DATA-CONTAMINATION CHECK

**VERIFIED CLEAN on every requirement.**

| Check | Result |
|---|---|
| Are live post-activation windows in the frozen manifest? | **No — 0.** 1283 DB windows − 1221 manifest records = 62, and that difference set is **exactly** the post-activation scored set (symmetric difference = 0; intersection with the manifest = 0). |
| Are live windows in the original model training data? | **No.** Both artifacts report `training_data_date_range = (2026-09-05, 2026-09-06)`; the live day is 2026-09-10. Window counts (236 / 345) match the TRAIN partition's keyboard-/mouse-eligible counts exactly. |
| Are live windows in the original validation metrics? | **No.** `_measure_validation_metrics` reads only `load_frozen_corpus(..., "VALIDATION")`, which returns **manifest members only** — a post-freeze window is silently excluded ([tools/collection/corpus.py:116-119](tools/collection/corpus.py#L116-L119)). Timing also forbids it: activation completed **03:46:34Z**, first live window stored **03:54:04Z**. |
| Are live windows in the initial calibration artifacts? | **No.** The `PercentileCalibrator` reference lists are frozen inside the joblib files (236 / 345 in-sample TRAIN scores, verified by `len(calibration.reference_scores)`), and `score_window` only calls `transform` — never `fit`. Nothing recalibrates at runtime. |
| Model-update training? | **None occurred.** `update_runs` = 0 rows. All 14 `update_candidates` are `REJECTED`. `model_profiles` holds exactly one row. |
| Was the EVALUATION partition touched during activation? | **No.** `activate_first_profile` loads only TRAIN and VALIDATION ([tools/enrollment/activate.py:257-258](tools/enrollment/activate.py#L257-L258)). *(It was read only during this read-only diagnosis, to check reproducibility; nothing was refitted and no headline result was derived from it.)* |
| Has any attacker/drill data been collected? | **No. `drill_sessions` = 0 rows.** The exclusion machinery is present and wired but has had nothing to exclude. |
| Corpus integrity | `verify_freeze` runs before any window is read ([corpus.py:80](tools/collection/corpus.py#L80)); the corpus loaded cleanly under the recorded `manifest_checksum`, so the frozen corpus is byte-consistent with its manifest. |
| Architecture requirement | **Upheld.** Initial model training = legitimate user only, 2 frozen TRAIN days. Initial calibration = in-sample TRAIN scores only, as designed. Attacker = post-activation live experiment, not yet run. Attacker data = never training/calibration/update data (enforced structurally, currently vacuous). |
| Windows by collection day | `2026-09-05: 101`, `2026-09-06: 288`, `2026-09-07: 382`, `2026-09-08: 450`, `2026-09-10: 62` |

### One provenance gap found (VERIFIED — not a cause of anything)

`activate_first_profile` writes the artifacts with `save_artifact` and inserts the `model_profiles` row via `SQLiteUpdateRepository.activate_profile` ([backend/app/updates/repository.py:142-187](backend/app/updates/repository.py#L142-L187)), but **never calls `StorageService.store_model`** ([backend/app/storage/service.py:887](backend/app/storage/service.py#L887)).

Consequence: **the `models` table is empty.** The database records no checksum, feature-schema version, or training date range for the two live `.joblib` files.

Integrity is still enforced at load time — `DirectoryProfileProvider` recomputes and compares the `aggregate_checksum` ([backend/app/runtime/profiles.py:71-80](backend/app/runtime/profiles.py#L71-L80)) and `load_artifact` verifies each artifact's own metadata checksum and feature-schema version ([ml/training/persistence.py:59-66](ml/training/persistence.py#L59-L66)) — so nothing is unsafe. But **the audit trail for artifact provenance lives only on disk.** This is defect 4 in [§16](#16-root-cause-classification).

---

## 16. ROOT-CAUSE CLASSIFICATION

## **MIXED** — with a clear ranking.

### Primary (≈ 55 of the 82 points): **CONFIGURATION / THRESHOLD-SEMANTICS PROBLEM**

**Evidence.** At `medium_threshold = 0.45` ⟺ percentile 55, **54.7% of the model's own in-sample training windows are MEDIUM breaches, and 19.0% are HIGH breaches** (per-window fused: 0.5547 / 0.1901; pooled per-modality: keyboard 129/236 = 0.5466, mouse 189/345 = 0.5478). No model, no dataset, and no drift can be blamed for that — it is arithmetic.

**Why it happens.** A percentile rank is a **uniform** quantity, not a probability of anomaly. Applying a 0.45 cut on the `1 − pct/100` scale places the acceptance region at the **top 45% of the user's own behaviour**. The in-sample percentile distributions in [§4](#4-frr-by-modality) are textbook-uniform (median 50.2 / 50.1, p10 ≈ 10, p90 ≈ 90), which is exactly what a self-referential rank calibrator must produce — and exactly why a 0.45 threshold on that scale is not an operating point.

**This is a value that was never derived from the distribution it is applied to.** [config/risk.development.yaml](config/risk.development.yaml) says so itself in its header: *"the numeric O3/O7/O8/O9 questions remain OPEN and require Phase 4/5 evidence plus human approval. These values exercise code paths; they are not approved production policy."* The pilot ran against an explicitly unresolved policy value, which the file warned about.

### Secondary (≈ 27 of the 82 points): **MODEL / DATA PROBLEM**

**Evidence.** Held-out pooled rate **0.8195** vs in-sample **0.5473**, reproduced independently on a second unseen day (EVALUATION **0.7825**). Median held-out percentile 25–28 rather than 50.

**Contributing mechanisms, all verified:**

1. **In-sample percentile calibration** ([common.py:316-322](ml/training/common.py#L316-L322)) — the reference distribution is the IsolationForest's own training scores, which biases every unseen window downward regardless of drift.
2. **A thin, day-unbalanced TRAIN partition** — 389 windows total; 2026-09-05 supplies only 39 of 236 keyboard rows (16.5%).
3. **Untruncated heavy-tailed mouse features** — `accel_std` has a VALIDATION sd of **63 training-sd** and an EVALUATION mean shift of **+12.4 training-sd**, standardised with non-robust mean/std ([common.py:124-132](ml/training/common.py#L124-L132)).
4. **A very tight reference distribution** (raw sd ≈ 0.046) making the percentile map high-gain: a 0.7-sd raw shift moves risk 0.50 → 0.75.

### Explicitly ruled out

| candidate | verdict and evidence |
|---|---|
| **RISK_PIPELINE_PROBLEM** | **Ruled out.** Fusion is exact ([§11](#11-fusion-analysis), verified to the last digit on FULL/KBD_ONLY/MOUSE_ONLY). EWMA arithmetic checks out (`0.4·0.7536 + 0.6·0.796 = 0.779` ✓). K-of-N and hysteresis match the code as written, at both the up- and down-transitions actually observed. Ladder, cooldown and budget behave correctly. **Options B, C, F and G are ruled out; D and E are real *amplifiers*, not sources** — smoothing plus the 5-of-5 rule make MEDIUM effectively absorbing (P(escape) ≈ 2×10⁻⁴ per window), but they are amplifying a distribution that is already 82% above threshold. |
| **CALIBRATION_PROBLEM (runtime phase)** | **Ruled out.** The `CALIBRATING` gate is clean; pre-activation `'unavailable'` rows are correctly excluded; the shadow period ran for exactly 9 decisions before `ACTIVE`. Nothing is re-normalised at the transition. |
| **Context confidence** | **Ruled out as a cause.** A strict no-op at confidence 1.000; in damping mode it cannot raise a score at all. |
| **Enforcement** | **Ruled out.** Genuinely suppressed; zero workstation impact; the only issue is a label. |
| **EXPECTED_BEHAVIOR (as a complete answer)** | **Partly true, insufficient.** The *pipeline* is behaving exactly as specified given its inputs, and the live distribution reproduces the frozen validation measurement. But the *system* is not fit for purpose at this operating point — see below. |

### Genuine implementation defects found (none of which cause the 82%)

| # | Defect | Location | Severity |
|---|---|---|---|
| **1** | **Context-confidence learning deadlock.** `observe_genuine` is gated on `risk_level == LOW`, so `PER_CONTEXT_NORMALIZATION` can never arm once risk is elevated — the compensation mechanism is unreachable precisely when it is needed. Additionally, the only 2 observations recorded were fused risks of **0.8737 / 0.8493**, labelled LOW purely because `_history` was cold. | [orchestrator.py:701-720](backend/app/runtime/orchestrator.py#L701-L720) + [context.py:267-284](backend/app/risk/context.py#L267-L284) | **Design defect** — makes a configured feature dead code in practice |
| **2** | **FRR unit mismatch.** The reported rate is over 543 *(window, modality)* pairs while the engine breaches on *fused per-window* values (0.8418 on the same data). The `operating_point` string discloses "before smoothing and K-of-N" but not the cross-modality pooling, so "per-window" is misleading. | [activate.py:167-177](tools/enrollment/activate.py#L167-L177), [activate.py:219-223](tools/enrollment/activate.py#L219-L223) | **Reporting defect** |
| **3** | **Float boundary asymmetry** at percentile exactly 55.0: the FRR measure counts it as rejected, the engine does not breach on it — contradicting the documented "inclusive comparison flips sides" invariant. **Zero windows affected in this dataset.** | [service.py:70-96](backend/app/models/service.py#L70-L96), [activate.py:150-156](tools/enrollment/activate.py#L150-L156) | **Latent, immaterial here** |
| **4** | **`models` table never populated** by the activation path — no DB record of artifact checksum, schema version or training range. Load-time integrity is unaffected. | [activate.py:307-316](tools/enrollment/activate.py#L307-L316) vs [storage/service.py:887](backend/app/storage/service.py#L887) | **Provenance/audit gap** |

### On "do not call it a bug merely because FRR is high"

Agreed, and the analysis honours it: **the 81.95% is a faithful measurement of a real property of this model at this threshold.** The reproduction is bit-exact, the direction of every comparison is correct, and the live runtime reproduces the offline number. The defect-shaped part of the finding is narrower and more specific: **the threshold was never derived from the score distribution it is applied to**, and the in-sample floor (54.7%) proves that no amount of model improvement could rescue this operating point.

### Is the observed behaviour EXPECTED?

- **For the risk pipeline: yes, exactly.** Every stage reproduces its specification on the inputs it receives.
- **For the system as a product: no.** A 54.7% in-sample rejection floor means the deployed operating point cannot meaningfully separate the enrolled user from anyone. **A subsequent attacker drill run at this operating point would be uninformative**: FAR would look excellent for the trivial reason that almost every window — genuine or not — is rejected. That result would not be defensible as evidence of discrimination.

---

## 17. EVIDENCE / FILES / CODE PATHS

| Topic | Location |
|---|---|
| FRR formula, denominator, operating-point string | [tools/enrollment/activate.py:118-227](tools/enrollment/activate.py#L118-L227) (formula at 217-218) |
| Pooled genuine list built per artifact | [tools/enrollment/activate.py:167-177](tools/enrollment/activate.py#L167-L177) |
| `zero_effort_cross_evaluation`, per-window score skipping | [ml/evaluation/cross_evaluation.py:33-85](ml/evaluation/cross_evaluation.py#L33-L85) |
| Percentile calibrator (definition + convention) | [ml/calibration/percentile.py:19-43](ml/calibration/percentile.py#L19-L43) |
| Calibrator fitted on **in-sample** training scores | [ml/training/common.py:314-322](ml/training/common.py#L314-L322) |
| `risk = 1 − pct/100` and its inverse | [backend/app/models/service.py:70-96](backend/app/models/service.py#L70-L96), applied at [135](backend/app/models/service.py#L135) |
| Feature standardisation (mean/std, non-robust to tails) | [ml/training/common.py:112-132](ml/training/common.py#L112-L132) |
| Feature matrix excludes unavailable modality (no imputation) | [ml/training/common.py:67-104](ml/training/common.py#L67-L104) |
| `score_window` — schema refusal, unavailable modality, percentile lookup | [ml/training/common.py:347-391](ml/training/common.py#L347-L391) |
| IsolationForest hyperparameters from config | [ml/training/isolation_forest.py:21-47](ml/training/isolation_forest.py#L21-L47) |
| Per-modality independence (one model may train while the other skips) | [ml/training/isolation_forest.py:50-78](ml/training/isolation_forest.py#L50-L78) |
| Enrollment admission gate (ADR-013) | [ml/training/enrollment.py:66-132](ml/training/enrollment.py#L66-L132) |
| Availability-renormalised fusion | [backend/app/risk/engine.py:97-112](backend/app/risk/engine.py#L97-L112) |
| Required-modality failure check → DEGRADED | [backend/app/risk/engine.py:85-95](backend/app/risk/engine.py#L85-L95), [138-172](backend/app/risk/engine.py#L138-L172) |
| `INSUFFICIENT_DATA` → UNAVAILABLE, history untouched | [backend/app/risk/engine.py:184-209](backend/app/risk/engine.py#L184-L209) |
| Context adjust (`fused × confidence`, or affine normalisation) | [backend/app/risk/types.py:59-75](backend/app/risk/types.py#L59-L75) |
| EWMA + history append | [backend/app/risk/engine.py:229-236](backend/app/risk/engine.py#L229-L236) |
| K-of-N breach counting | [backend/app/risk/engine.py:114-121](backend/app/risk/engine.py#L114-L121) |
| Hysteresis (5-of-5 to descend) | [backend/app/risk/engine.py:123-136](backend/app/risk/engine.py#L123-L136) |
| DEGRADED recovery (no longer one-way) | [backend/app/risk/engine.py:225-227](backend/app/risk/engine.py#L225-L227), [352-363](backend/app/risk/engine.py#L352-L363) |
| Escalation ladder / cooldown / action budget | [backend/app/decisions/policy.py:30-70](backend/app/decisions/policy.py#L30-L70) |
| User state machine transitions | [backend/app/risk/state.py:36-81](backend/app/risk/state.py#L36-L81) |
| Risk config loader + validation rules | [backend/app/risk/config.py:46-71](backend/app/risk/config.py#L46-L71) |
| Active thresholds | [config/risk.development.yaml](config/risk.development.yaml) |
| Demo override (**not** used) | [config/risk.demo-live-single-day.yaml](config/risk.demo-live-single-day.yaml) |
| Context confidence = bootstrap `UNKNOWN: 1.0` | [config/app_categories.yaml](config/app_categories.yaml) (empty map) + [config/context.development.yaml](config/context.development.yaml) + [backend/app/risk/context.py:168-180](backend/app/risk/context.py#L168-L180) |
| Normalisation arming conditions | [backend/app/risk/context.py:250-284](backend/app/risk/context.py#L250-L284) |
| `observe_genuine` gated on LOW | [backend/app/runtime/orchestrator.py:701-720](backend/app/runtime/orchestrator.py#L701-L720) |
| Calibration-window counting (excludes `'unavailable'`) | [backend/app/runtime/orchestrator.py:608-668](backend/app/runtime/orchestrator.py#L608-L668) |
| Live window processing order (store → score → progress → assess → decide) | [backend/app/runtime/orchestrator.py:676-724](backend/app/runtime/orchestrator.py#L676-L724) |
| Profile load + aggregate checksum verification | [backend/app/runtime/profiles.py:34-90](backend/app/runtime/profiles.py#L34-L90) |
| Artifact load: schema refusal + checksum | [ml/training/persistence.py:42-68](ml/training/persistence.py#L42-L68) |
| Profile activation (writes `model_profiles`, not `models`) | [backend/app/updates/repository.py:142-187](backend/app/updates/repository.py#L142-L187) |
| Enforcement master switch | [config/enforcement.development.yaml](config/enforcement.development.yaml), [backend/app/decisions/config.py:40-75](backend/app/decisions/config.py#L40-L75) |
| `ENFORCEMENT_DISABLED` returned before touching the OS | [backend/app/decisions/native.py:138](backend/app/decisions/native.py#L138), [167](backend/app/decisions/native.py#L167) |
| Enforcement execution + outcome recording | [backend/app/decisions/adapters.py:176-221](backend/app/decisions/adapters.py#L176-L221) |
| Overview "Applied" label (policy flag, not execution) | [dashboard/src/views/OverviewView.tsx:45-46](dashboard/src/views/OverviewView.tsx#L45-L46), [dashboard/src/protocol.ts:41](dashboard/src/protocol.ts#L41) |
| Settings page truthful enforcement text | [dashboard/src/views/SettingsView.tsx:26-35](dashboard/src/views/SettingsView.tsx#L26-L35) |
| Drill corpus exclusion (single definition) | [backend/app/storage/drill.py](backend/app/storage/drill.py) |
| Drill exclusion applied by corpus loader | [tools/collection/corpus.py:92-110](tools/collection/corpus.py#L92-L110) |
| Drill learning suspension | [backend/app/runtime/orchestrator.py:117-131](backend/app/runtime/orchestrator.py#L117-L131), [backend/app/risk/context.py:112-149](backend/app/risk/context.py#L112-L149) |
| Frozen manifest (1221 records) | [data/frozen/pilot-v1/manifest.json](data/frozen/pilot-v1/manifest.json) |
| ML tunables (window size, quality gate, IF hyperparameters, train-admission policy) | [config/ml.development.yaml](config/ml.development.yaml) |
| Storage profile (DB location, PILOT policy, retention) | [config/storage.pilot.yaml](config/storage.pilot.yaml) |
| Live database (read `mode=ro` only) | `%LOCALAPPDATA%/ContinuousAuthentication/Pilot/continuous-auth.db` |
| Model artifacts | `%LOCALAPPDATA%/ContinuousAuthentication/Pilot/models/manas-01/*.joblib` |

### NOT DETERMINABLE FROM CURRENT DATA

1. **FAR / discriminative power — completely unknown.** No impostor cohort and no drill data exist, so it is impossible to say whether these models separate `manas-01` from anyone else. `validation_far = null` is honest and correct: **not measured**, never 0.0. The 81.95% FRR is one-sided evidence only, and on its own it says nothing about security value.
2. **How much of the 27-point held-out gap is in-sample calibration bias versus true day-to-day drift.** Separating them requires fitting a calibrator on held-out scores — i.e. retraining — which is out of scope by instruction.
3. **Whether the mouse degradation is behavioural or instrumental.** The `accel_std` / `accel_mean` / `jerk_mean` tail could be genuine motor variability or an artefact of polling-rate / DPI / resolution differences between days. `reference_screen_width_px` and `reference_screen_height_px` are documented placeholder constants in [config/ml.development.yaml](config/ml.development.yaml), and per-event resolution is noted as not yet carried in the IPC schema. Distinguishing the two would require raw-event inspection, which retention policy forbids (`raw_debug_capture_enabled: false`, `pilot_mode: true`).
4. **Whether the two LOW-labelled context observations would have mattered.** Because `min_empirical_observations = 4` was never reached, the effect of seeding the context baseline with fused risks of 0.87 / 0.85 is unobserved.
5. **Any statement about real-world accuracy.** Per the config headers and ADR-014, nothing in this run may be cited as evidence of real-world accuracy; it demonstrates that the live pipeline mechanically reaches decision-producing state end to end.

---

## 18. RECOMMENDED NEXT STEP

**Do not run the attacker drill yet.** At the current operating point it would produce a flattering FAR for entirely the wrong reason — almost every window is rejected regardless of who is at the keyboard — and the result would not be defensible as evidence of discrimination.

The single highest-value next action is **read-only, and needs no retraining, no new collection, and no code change**: **derive the operating point from the score distribution that now exists**, instead of inheriting the placeholder `0.45`. The sweep in [§8](#8-calibration-analysis) already gives the shape of that decision — for example `medium_threshold ≈ 0.95` corresponds to percentile 5 and yields in-sample **4.8%** / held-out **13.1%**. Choosing the target FRR is a policy call and requires the human approval that [config/risk.development.yaml](config/risk.development.yaml) already demands; the technical point is simply that **0.45 was never derived from any distribution**, and the in-sample floor proves no model improvement can rescue it.

Two second-order items worth recording while the evidence is fresh:

1. **The context-learning deadlock** ([§12](#12-context-confidence-analysis), defect 1) — the layer designed to absorb context-specific score offsets can never engage after the first escalation, because its only source of evidence is LOW-risk windows.
2. **The FRR unit mismatch** ([§16](#16-root-cause-classification), defect 2) — the headline number is not measured in the engine's own breach unit; **0.8418** (fused, per window) is the directly comparable figure.

Longer-term, the mouse feature tail ([§9](#9-training-data-representativeness)) is the largest single lever on held-out score quality — but investigating it means changing feature computation, which is a separate, reviewed piece of work and explicitly out of scope for this pass.

---

## APPENDIX A — Full live decision trace (62 post-activation decisions)

Session `session-ed161d47…`, profile `20260910T034634.521619`, 2026-09-10 03:54:04Z → 04:40:28Z (≈ 46 minutes). Columns: `kbd_raw` = raw IsolationForest normality score; `kbd_risk` / `mou_risk` = calibrated risk `1 − pct/100`; `fused` = availability-renormalised mean; `conf` = context confidence; `smooth` = EWMA (α = 0.4); `appl` = `risk_events.enforcement_applied` (policy flag, **not** native execution).

```
i    stored_at (UTC)      quality        kbd_raw  kbd_risk  mou_risk     fused   conf  smooth  level        action          reason                      appl
0    2026-09-10T03:54:04  FULL           -0.4429    0.8517    0.8957    0.8737  1.000   0.874  LOW          CONTINUE        SHADOW_OR_NON_ENFORCING       0
1    2026-09-10T03:54:34  MOUSE_ONLY           -         -    0.8493    0.8493  1.000   0.864  LOW          CONTINUE        SHADOW_OR_NON_ENFORCING       0
2    2026-09-10T03:55:01  FULL           -0.3696    0.1653    0.7130    0.4391  1.000   0.694  MEDIUM       SOFT_CHALLENGE  SHADOW_OR_NON_ENFORCING       0
3    2026-09-10T03:55:21  KBD_ONLY       -0.4295    0.8051         -    0.8051  1.000   0.738  MEDIUM       SOFT_CHALLENGE  SHADOW_OR_NON_ENFORCING       0
4    2026-09-10T03:55:41  KBD_ONLY       -0.4095    0.6864         -    0.6864  1.000   0.718  MEDIUM       SOFT_CHALLENGE  SHADOW_OR_NON_ENFORCING       0
5    2026-09-10T03:56:14  FULL           -0.3839    0.4576    0.9594    0.7085  1.000   0.714  MEDIUM       SOFT_CHALLENGE  SHADOW_OR_NON_ENFORCING       0
6    2026-09-10T03:56:44  MOUSE_ONLY           -         -    0.9362    0.9362  1.000   0.803  MEDIUM       SOFT_CHALLENGE  SHADOW_OR_NON_ENFORCING       0
7    2026-09-10T03:57:26  FULL           -0.3984    0.6144    0.9565    0.7855  1.000   0.796  MEDIUM       SOFT_CHALLENGE  SHADOW_OR_NON_ENFORCING       0
8    2026-09-10T03:58:04  MOUSE_ONLY           -         -    0.7536    0.7536  1.000   0.779  HIGH         REAUTH          SHADOW_OR_NON_ENFORCING       0
9    2026-09-10T03:58:34  FULL           -0.3754    0.2754    0.8406    0.5580  1.000   0.691  HIGH         REAUTH          RISK_ESCALATION               1
10   2026-09-10T03:59:41  KBD_ONLY       -0.5397    0.9788         -    0.9788  1.000   0.806  HIGH         REAUTH          RISK_ESCALATION               1
11   2026-09-10T04:00:31  MOUSE_ONLY           -         -    1.0000    1.0000  1.000   0.884  HIGH         NONE            ACTION_COOLDOWN               0
12   2026-09-10T04:01:01  FULL           -0.3563    0.0169    0.8058    0.4114  1.000   0.695  HIGH         REAUTH          RISK_ESCALATION               1
13   2026-09-10T04:01:43  KBD_ONLY       -0.4009    0.6271         -    0.6271  1.000   0.668  HIGH         NONE            ACTION_COOLDOWN               0
14   2026-09-10T04:02:03  KBD_ONLY       -0.3862    0.4873         -    0.4873  1.000   0.596  HIGH         REAUTH          RISK_ESCALATION               1
15   2026-09-10T04:02:50  KBD_ONLY       -0.4438    0.8517         -    0.8517  1.000   0.698  HIGH         NONE            ACTION_COOLDOWN               0
16   2026-09-10T04:03:21  FULL           -0.3844    0.4703    0.9797    0.7250  1.000   0.709  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
17   2026-09-10T04:04:18  MOUSE_ONLY           -         -    0.1652    0.1652  1.000   0.491  MEDIUM       NONE            ACTION_COOLDOWN               0
18   2026-09-10T04:04:54  MOUSE_ONLY           -         -    0.2609    0.2609  1.000   0.399  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
19   2026-09-10T04:05:24  MOUSE_ONLY           -         -    0.6754    0.6754  1.000   0.510  MEDIUM       NONE            ACTION_COOLDOWN               0
20   2026-09-10T04:05:54  MOUSE_ONLY           -         -    0.8348    0.8348  1.000   0.640  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
21   2026-09-10T04:06:24  FULL           -0.4275    0.8051    0.9507    0.8779  1.000   0.735  MEDIUM       NONE            ACTION_COOLDOWN               0
22   2026-09-10T04:09:43  MOUSE_ONLY           -         -    0.8957    0.8957  1.000   0.799  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
23   2026-09-10T04:10:25  MOUSE_ONLY           -         -    0.9478    0.9478  1.000   0.859  MEDIUM       NONE            ACTION_COOLDOWN               0
24   2026-09-10T04:10:55  FULL           -0.3575    0.0212    0.4029    0.2120  1.000   0.600  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
25   2026-09-10T04:11:25  KBD_ONLY       -0.3699    0.1653         -    0.1653  1.000   0.426  MEDIUM       NONE            ACTION_COOLDOWN               0
26   2026-09-10T04:12:14  KBD_ONLY       -0.3971    0.6102         -    0.6102  1.000   0.500  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
27   2026-09-10T04:13:09  FULL           -0.4147    0.7500    0.7855    0.7678  1.000   0.607  MEDIUM       NONE            ACTION_COOLDOWN               0
28   2026-09-10T04:13:39  KBD_ONLY       -0.4097    0.6864         -    0.6864  1.000   0.639  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
29   2026-09-10T04:14:18  KBD_ONLY       -0.4001    0.6271         -    0.6271  1.000   0.634  MEDIUM       NONE            ACTION_COOLDOWN               0
30   2026-09-10T04:14:43  KBD_ONLY       -0.3743    0.2585         -    0.2585  1.000   0.484  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
31   2026-09-10T04:15:31  KBD_ONLY       -0.4376    0.8390         -    0.8390  1.000   0.626  MEDIUM       NONE            ACTION_COOLDOWN               0
32   2026-09-10T04:16:02  KBD_ONLY       -0.4064    0.6610         -    0.6610  1.000   0.640  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
33   2026-09-10T04:16:32  FULL           -0.3786    0.3390    0.9159    0.6275  1.000   0.635  MEDIUM       NONE            ACTION_COOLDOWN               0
34   2026-09-10T04:17:08  MOUSE_ONLY           -         -    0.9855    0.9855  1.000   0.775  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
35   2026-09-10T04:17:38  MOUSE_ONLY           -         -    0.8435    0.8435  1.000   0.802  MEDIUM       NONE            ACTION_COOLDOWN               0
36   2026-09-10T04:18:08  MOUSE_ONLY           -         -    0.9420    0.9420  1.000   0.858  HIGH         REAUTH          RISK_ESCALATION               1
37   2026-09-10T04:18:49  MOUSE_ONLY           -         -    0.3072    0.3072  1.000   0.638  HIGH         NONE            ACTION_COOLDOWN               0
38   2026-09-10T04:19:46  MOUSE_ONLY           -         -    0.7739    0.7739  1.000   0.692  HIGH         REAUTH          RISK_ESCALATION               1
39   2026-09-10T04:20:16  FULL           -0.4523    0.8814    0.9478    0.9146  1.000   0.781  HIGH         NONE            ACTION_COOLDOWN               0
40   2026-09-10T04:21:36  FULL           -0.5116    0.9492    0.9739    0.9615  1.000   0.853  HIGH         REAUTH          RISK_ESCALATION               1
41   2026-09-10T04:22:00  KBD_ONLY       -0.3796    0.3517         -    0.3517  1.000   0.653  HIGH         NONE            ACTION_COOLDOWN               0
42   2026-09-10T04:22:49  KBD_ONLY       -0.3522    0.0042         -    0.0042  1.000   0.393  HIGH         REAUTH          RISK_ESCALATION               1
43   2026-09-10T04:23:29  MOUSE_ONLY           -         -    0.6348    0.6348  1.000   0.490  HIGH         NONE            ACTION_COOLDOWN               0
44   2026-09-10T04:24:06  FULL           -0.4074    0.6695    0.9826    0.8261  1.000   0.624  HIGH         REAUTH          RISK_ESCALATION               1
45   2026-09-10T04:25:28  FULL           -0.5382    0.9746    0.6754    0.8250  1.000   0.705  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
46   2026-09-10T04:25:59  FULL           -0.4636    0.9110    0.8928    0.9019  1.000   0.784  MEDIUM       NONE            ACTION_COOLDOWN               0
47   2026-09-10T04:26:19  KBD_ONLY       -0.3762    0.2924         -    0.2924  1.000   0.587  MEDIUM       NONE            ACTION_COOLDOWN               0
48   2026-09-10T04:27:14  INSUFFICIENT_DATA    -         -         -         -  1.000       -  UNAVAILABLE  NONE            INSUFFICIENT_EVIDENCE_HOLD    0
49   2026-09-10T04:28:44  MOUSE_ONLY           -         -    0.5536    0.5536  1.000   0.574  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
50   2026-09-10T04:30:56  INSUFFICIENT_DATA    -         -         -         -  1.000       -  UNAVAILABLE  NONE            INSUFFICIENT_EVIDENCE_HOLD    0
51   2026-09-10T04:33:06  MOUSE_ONLY           -         -    0.8957    0.8957  1.000   0.702  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
52   2026-09-10T04:33:36  FULL           -0.3715    0.1949    0.8087    0.5018  1.000   0.622  MEDIUM       NONE            ACTION_COOLDOWN               0
53   2026-09-10T04:34:06  FULL           -0.4720    0.9195    0.7072    0.8134  1.000   0.699  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
54   2026-09-10T04:34:37  FULL           -0.4601    0.9068    0.9014    0.9041  1.000   0.781  MEDIUM       NONE            ACTION_COOLDOWN               0
55   2026-09-10T04:35:43  FULL           -0.4307    0.8093    0.7507    0.7800  1.000   0.781  MEDIUM       SOFT_CHALLENGE  RISK_ESCALATION               1
56   2026-09-10T04:36:26  FULL           -0.5341    0.9703    0.7304    0.8504  1.000   0.808  HIGH         REAUTH          RISK_ESCALATION               1
57   2026-09-10T04:37:53  MOUSE_ONLY           -         -    0.7652    0.7652  1.000   0.791  HIGH         REAUTH          RISK_ESCALATION               1
58   2026-09-10T04:38:29  MOUSE_ONLY           -         -    0.6841    0.6841  1.000   0.748  HIGH         NONE            ACTION_COOLDOWN               0
59   2026-09-10T04:39:13  FULL           -0.3867    0.4958    0.6754    0.5856  1.000   0.683  HIGH         REAUTH          RISK_ESCALATION               1
60   2026-09-10T04:39:57  MOUSE_ONLY           -         -    0.8174    0.8174  1.000   0.737  HIGH         NONE            ACTION_COOLDOWN               0
61   2026-09-10T04:40:28  MOUSE_ONLY           -         -    0.7507    0.7507  1.000   0.742  HIGH         REAUTH          RISK_ESCALATION               1
```

### Reading the trace

- **i=0, i=1 are LOW only because `_history` had fewer than `breach_k = 3` entries** — their fused risks were 0.874 and 0.849. The system was never genuinely calm.
- **Never LOW again after i=1.** MEDIUM or HIGH for the entire remaining 45 minutes.
- **Lowest smoothed value in the whole run: 0.393** (i=42) — and it was immediately followed by 0.490. Only 3 of 60 smoothed values fell below 0.45, never consecutively, so the 5-of-5 exit condition was never met.
- **Alternating `RISK_ESCALATION` / `ACTION_COOLDOWN`** is the 60 s per-action cooldown, working correctly.
- **`appl = 1` on 28 rows is the policy flag only.** `decisions.outcome` records `ENFORCEMENT_DISABLED` for every one of them; no prompt was shown, no workstation lock occurred.
- **i=48 and i=50 (`INSUFFICIENT_DATA`)** show `smooth = -`: the EWMA and the deque are untouched, so the risk level is frozen across idle windows.

---

## APPENDIX B — Reproduction method

All analysis was performed with four throwaway scripts held in the session scratchpad (**not** in the repository):

| script | purpose |
|---|---|
| `repro_frr.py` | Loads both `.joblib` artifacts and the frozen TRAIN / VALIDATION / EVALUATION partitions; re-scores every window; reproduces the pooled FRR bit-exactly; breaks it down by modality and quality label; computes per-window fused risk. |
| `live_trace.py` | Reads `risk_events` ⋈ `scores` ⋈ `feature_windows` for the live session; prints the full 62-row decision trace; summarises fused / smoothed / per-modality distributions; enumerates context-confidence values and `confidence_source`. |
| `drift.py` | Projects TRAIN (and each training day), VALIDATION, EVALUATION and the live windows into the model's own scaled space; reports `mean|z|`, `RMS z` and the top per-feature mean shifts; reports raw normality-score distributions per group. |
| `floor.py` | Computes the per-window fused distribution per partition; sweeps `medium_threshold` to separate the operating-point floor from out-of-sample degradation; computes EWMA recovery times from the observed `smoothed = 0.692`. |

**Guarantees observed throughout:**

- Every SQLite connection was opened as `sqlite3.connect("file:…?mode=ro", uri=True)`.
- Model artifacts were loaded via `ml.training.persistence.load_artifact`, which verifies checksum and feature-schema version — the same path the live runtime uses, so the re-scored values are exactly the values the runtime produced. This is why the offline and live distributions agree.
- The frozen corpus was loaded via `tools.collection.corpus.load_frozen_corpus`, which runs `verify_freeze` before reading and returns manifest members only.
- **Nothing was fitted, refitted, retrained, written, deleted, or reconfigured.** No file in the repository, no configuration value, no database row, no manifest, and no artifact was modified at any point. The only write performed in this entire investigation is this `diagnosis.md` file.
