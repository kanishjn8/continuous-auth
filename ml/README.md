# ml/ — Shared feature pipeline, modelling, and evaluation (Manas)

Implements T-008 (shared windowing/feature extraction), the synthetic +
public-dataset fixtures for pipeline validation, T-010 (per-user modelling),
and T-011 (day-disjoint evaluation), per `PLAN.md` and `TASK_DELEGATION.md`.

## Working-fixture assumptions (flag clearly, reconcile later)

`protocol/` (T-002, Joel) and the native collector (T-004/T-005, Kanish) do
not exist yet in this repository. Per instruction, this package uses the
event and feature-window schemas fully specified in `PLAN.md` Section
7.2/7.3 as a **working fixture**, implemented with Pydantic v2 (the same
library the real generated bindings will use). The specific assumptions
made, and where to look when reconciling against the real `protocol/`:

| Assumption | Where | Reconcile when |
| --- | --- | --- |
| Event/window schema reproduced by hand instead of codegen'd | `ml/features/schema.py` | T-002 lands — swap imports for generated bindings, keep field names |
| `config/` tunables live under `ml/config/` instead of repo-root `config/` | `ml/config/thresholds.yaml` | T-002/T-009 land — move file, update loader path only |
| Session/segment attribution assumed already done by caller | `ml/features/windowing.py` | T-007 lands — feed its output directly into `extract_windows` |
| Hand/row mapping for class-transition latency only defined for alphabetic key classes | `ml/features/keyboard.py`, `ml/features/schema.py` (`KEY_CLASS_HAND`/`KEY_CLASS_ROW`) | Revisit once real typing data is available (Phase 2 distribution analysis) |
| Mouse resolution/DPI normalization takes an optional `device_resolution` since `MouseEvent` doesn't carry it (Section 7.2) | `ml/features/mouse.py` | T-005 lands and defines where resolution/DPI actually travels in the IPC stream |
| ADR-005 quality-gate thresholds (`min_keystrokes`, `min_mouse_samples`) are engineering placeholders, not the `[OPEN]` decision's resolution | `ml/config/thresholds.yaml` | Phase 2 empirical distribution analysis (PLAN.md Section 2.10) |

## Layout

- `features/` — T-008. `schema.py` (fixture schema), `keyboard.py` /
  `mouse.py` (feature blocks), `windowing.py` (window closing + ADR-005
  quality gate), `extractor.py` (single entry point used by both training
  fixtures and, eventually, live inference).
- `datasets/` — synthetic event generator and public free-text keystroke
  dataset loader (PLAN.md Section 9.1/9.5/9.6 — **pipeline validation only,
  never headline results**; every window's `provenance` field records this).
- `training/`, `calibration/`, `baselines/` — T-010: per-user Isolation
  Forest models, percentile calibration, statistical/alternative one-class
  baselines (PLAN.md Section 10.4).
- `evaluation/` — T-011: day-disjoint / leave-one-day-out splitting,
  FAR/FRR/EER, zero-effort (I1) cross-evaluation, baseline comparison.
- `config/thresholds.yaml` — every tunable value used by this package (no
  hardcoded thresholds in source, per guardrail G9).
- `tests/` — one test module per implementation module; run with
  `pytest ml/tests -q` from the repo root.

## Running tests

```bash
python -m pytest ml/tests -q
```
