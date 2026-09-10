# Ground-truth check: does the replay tool reproduce the real engine? — read-only

**Date:** 2026-09-10
**Purpose:** independently verify `tools/evaluation/replay_state_machine.py` — the new
reimplementation of `RiskEngine`'s EWMA + K-of-N + hysteresis logic that produced the
"actual HIGH-state occupancy" numbers in
[docs/genuine-user-validation-2026-09-10.md](genuine-user-validation-2026-09-10.md) — against
real, historically-recorded decisions from the live pilot, before that number is treated as
final.
**Status:** evaluation-only. No source file, config file, model artifact, or calibration was
changed. `config/risk.development.yaml` (currently 0.80/0.90) was not touched; the OLD
0.45/0.75 config was read from git history (`git show HEAD:config/risk.development.yaml`)
into a scratch file, never written back into the repository.

---

## 1. What was persisted (item 1)

The replay core (`Reading`, `ReplayStep`, `replay_risk_levels`) that used to live only in a
session-scratchpad throwaway script now lives at
[tools/evaluation/replay_state_machine.py](../tools/evaluation/replay_state_machine.py),
inside the pre-existing `tools/evaluation/` package (which already held `cli.py`, `freeze.py`,
etc.). Its module docstring states plainly:

- it is **not production code** and is not imported by, and must never be imported by,
  `backend/`, `ml/`, or `tools/enrollment/`;
- it is a **reimplementation**, not a call into the real `RiskEngine` — it can drift from the
  real engine if `backend/app/risk/engine.py`'s state-machine arithmetic changes, and must be
  re-verified against fresh `risk_events` data if that ever happens;
- this document is the verification referenced from that docstring.

The file also keeps the CLI entry point that reproduces the frozen-corpus TRAIN / VALIDATION /
EVALUATION analysis from the first report (`python -m tools.evaluation.replay_state_machine`),
now calling the same persisted `replay_risk_levels` function rather than a duplicate inline
copy of the logic. Running it reproduces the original report's numbers exactly (spot-checked
during this task).

---

## 2. Ground-truth comparison against real `risk_events` (item 2)

### Method

- Loaded the **OLD** risk config (`medium_threshold=0.45`, `high_threshold=0.75`,
  `ewma_alpha=0.4`, `breach_k=3`, `breach_n=5`) via `git show HEAD:config/risk.development.yaml`
  into a scratch file and `backend.app.risk.config.load_risk_settings` on that file. Its
  computed `config_checksum` — `128193724a89c255688e63d691324e7f50651becff1436686cb89ce8fd6e3841`
  — matches the checksum `diagnosis.md` recorded as the runtime's actual active config at the
  time, confirming this is genuinely the config that produced the real decisions being checked
  against, not a guess at its values.
- Queried the live pilot database (`mode=ro`) for **every** `risk_events` row for `manas-01`
  with `stored_at_utc <= 2026-09-10T04:49:08.440320Z`. That cutoff was derived by finding the
  timestamp at which the live database's `(risk_level, user_state, action, reason_code,
  shadow_mode, enforcement_applied)` breakdown exactly equals the 10-row table
  `diagnosis.md` §2 reports (604 / 15 / 14 / 13 / 9 / 9 / 6 / 2 / 2 / 1 = 675) — necessary
  because **the live pilot has kept running since `diagnosis.md` was written**: the database
  now holds 881 `risk_events` rows and 268 post-activation scored windows for `manas-01`, not
  675 and 62. This report checks exactly the 675-row snapshot `diagnosis.md` analyzed, per the
  task's instruction, not the database's current (larger) state.
- For each row, joined to its `scores.score_json` (the real keyboard/mouse `available` /
  `calibrated_score` values actually recorded at decision time — not re-scored from the
  frozen artifacts) and reconstructed the fused input exactly as
  `RiskEngine._required_failure` / `RiskEngine._fusion` would: a window whose quality label
  requires a modality that was not `available` becomes `fused_score=None` (→ `UNAVAILABLE`,
  matching `RiskEngine.process`'s failure path, which never touches history/smoothed); an
  `INSUFFICIENT_DATA` window is `fused_score=None` unconditionally; otherwise the available
  modalities are fused with weight 0.5/0.5, matching `RiskEngine._fusion`. The real recorded
  `context_confidence` (not an assumed 1.0) was used for the EWMA input, `adjusted = fused *
  context_confidence`.
- Verified `t_decision_us` is monotonically non-decreasing across the 675 rows (the ordering
  `RiskEngine.process` itself requires) before replaying.
- Fed the resulting 675 `Reading`s, in that exact chronological order, through the persisted
  `replay_risk_levels` at the OLD thresholds — a single continuous replay, no resets at any
  point (see §3 for why).
- Compared the replayed `risk_level` and `smoothed_score` against the real
  `risk_events.risk_level` / `risk_events.smoothed_score` for every one of the 675 rows.

### Result

```
MATCHES: 675/675
MISMATCHES: 0
Max |real_smoothed_score - replayed_smoothed_score| over all rows with both present: 0.00e+00

Real risk_level distribution:     {'LOW': 2, 'MEDIUM': 35, 'HIGH': 23, 'UNAVAILABLE': 615}
Replayed risk_level distribution:  {'LOW': 2, 'MEDIUM': 35, 'HIGH': 23, 'UNAVAILABLE': 615}
```

**All 675 of 675 real historical decisions match exactly** — both the categorical
`risk_level` and the numeric `smoothed_score` (bit-identical, max absolute difference `0.0`)
— including every `UNAVAILABLE` row (615 of them, correctly derived from real modality
availability rather than assumed), every `LOW` (2), every `MEDIUM` (35), and every `HIGH`
(23). There are no mismatches to show.

This is as strong a correctness result as this kind of check can produce: the replay tool
was fed the real recorded per-modality scores and the real recorded context confidence for
the actual historical window sequence, asked to reproduce the real engine's own output, and
did so exactly, with zero floating-point drift, across all four `risk_level` categories and
across five real sessions.

---

## 3. How partition boundaries are handled (item 3)

Two different things must not be conflated:

**A. This ground-truth check (§2), against the real live pilot's history.** There is **no
partition concept at all** here — it is one continuous, unbroken replay across all 675 rows,
spanning 5 real sessions (4 pre-activation collection sessions plus the post-activation live
session) and both pre- and post-activation. History/EWMA state is **never reset** at any
session or day boundary. This is not an assumption: it is the configuration that reproduced
all 675 real decisions exactly. `RiskEngine` itself has no logic anywhere that resets
`self._history`, `self._smoothed`, or `self._risk_level` on a new session, segment, or day —
those fields belong to the long-lived `RiskEngine` instance for a user, and the only thing
that would reset them is the orchestrator process re-instantiating that engine (e.g. a
restart), which did not happen during this window. A caveat the data cannot rule out: because
every pre-activation row is `UNAVAILABLE` (no model existed yet, so fusion never runs and
history is never touched), the 675-row check only exercises **actual state continuity** within
the single post-activation session — it does not independently prove state survives a
session boundary *while fusion is succeeding on both sides*, since no such boundary exists yet
in this participant's history. The absence of any reset logic in `engine.py` is what backs the
"no reset across sessions" model, not an observed session-to-session carryover of live scores.

**B. The frozen-corpus, TRAIN/VALIDATION/EVALUATION analysis** (item 6 of the first report,
and `replay_state_machine.py`'s `main()`). Here the replay is **deliberately reset** — fresh
`history=deque()`, `smoothed=None`, `level="LOW"` — at the start of **each partition**,
because `replay_risk_levels` is called once per partition with no state carried from the
previous call. TRAIN's two collection days (2026-09-05 and 2026-09-06) are replayed as one
continuous sequence (no reset between them), but VALIDATION (2026-09-07) and EVALUATION
(2026-09-08) each start cold.

**Does B match how the real engine would actually initialize for "a fresh partition"?
No — and it should not be read as if it does.** `TRAIN` / `VALIDATION` / `EVALUATION` are a
corpus-splitting construct for offline evaluation (`tools/collection/corpus.py`); they have no
counterpart in the live `RiskEngine`, which has no notion of a "partition" and would carry
EWMA/deque state continuously across those same calendar days if it had been actively scoring
live during collection (exactly as confirmed not to reset in part A). The per-partition
cold-start in the frozen-corpus analysis is an **evaluation convention this report chose**,
answering "how would a fresh instance of the state machine respond to a full day of this
user's genuine behavior" — a legitimate question, but a different one from "what would the
live engine's actual state have been on day N, carrying forward whatever accumulated on days
1..N-1." The item-6 occupancy numbers (0% HIGH on TRAIN, 1.34% on VALIDATION, 0% on
EVALUATION) are specific to the cold-start-per-partition convention; a continuous
TRAIN→VALIDATION→EVALUATION replay was not computed and could show different HIGH occupancy
on VALIDATION/EVALUATION, since it would carry over EWMA state from the prior partition
instead of starting at `smoothed=None`. This was not in scope for the original report and is
called out here as an explicit limitation, not fixed silently.

---

## 4. Discrepancy disposition (item 4)

There was no discrepancy: the ground-truth replay matched all 675/675 real decisions exactly,
with zero numeric drift on `smoothed_score`. Per the task's instruction, if this check had
failed, the failure would have been reported here without silently patching the tool or
re-running the 0.80/0.90 numbers. Since it did not fail, the original report's item-6 HIGH-state
occupancy numbers for the 0.80/0.90 operating point stand as previously reported, with the
partition-boundary caveat in §3.B now made explicit.

---

## 5. Confirmations

- No source file was modified. `tools/evaluation/replay_state_machine.py` is new, additive,
  and explicitly labeled non-production in its own docstring.
- No configuration was modified. `config/risk.development.yaml` (0.80/0.90) was not read for
  this ground-truth check at all, since the check is specifically about the OLD 0.45/0.75
  decisions; the OLD config was reconstructed read-only from git history into a scratch file.
- No model artifact was modified or retrained; this check did not even need to load the
  `.joblib` artifacts, since the real historical per-modality scores were already recorded in
  `scores.score_json` and were used directly rather than being recomputed.
- No calibration/retraining occurred.
- The attacker drill was not run.
