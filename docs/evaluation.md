# Evaluation and reproducibility

Final evaluation starts only after `data/frozen/.../manifest.json` exists. Create an
immutable configuration/code/corpus lock with `tools.evaluation.create_evaluation_freeze`.
Every run must verify that lock before reading evaluation records. Post-freeze tuning
invalidates the result.

Create the lock after recording the exact revision (replace the example values with the
approved corpus and revision):

```powershell
python -m tools.evaluation create `
  --output local-evidence/evaluation/freeze.json `
  --config config/evaluation.development.yaml `
  --config config/ml.development.yaml `
  --config config/risk.development.yaml `
  --dataset-manifest data/frozen/pilot-v1/manifest.json `
  --revision REVISION_ID

python -m tools.evaluation verify `
  --freeze local-evidence/evaluation/freeze.json `
  --config-directory config `
  --dataset-manifest data/frozen/pilot-v1/manifest.json `
  --revision REVISION_ID
```

### Order of operations for real participant data

1. Freeze the corpus (`python -m tools.collection freeze --version ... --output
   data/frozen/.../manifest.json`) so the manifest exists and is checksum-verified.
2. Create the evaluation lock with `tools.evaluation.create_evaluation_freeze` (see
   below), over that same frozen corpus.
3. Activate each participant's first profile with `python -m tools.enrollment
   activate`. This trains on the TRAIN partition of the frozen corpus through the
   ADR-013 enrollment admission gate, measures FRR on the held-out VALIDATION
   partition, and measures FAR by zero-effort cross-evaluation against other
   participants' VALIDATION windows. The EVALUATION partition is never read here — it
   is reserved for the headline result.
4. Run evaluation (`ml/evaluation/pipeline.py`) with `admission` supplied, so that any
   subsequent team/pilot training data continues to clear the appropriate boundary
   (`ml/training/common.py` accepts exactly one of a promotion-gate candidate map or an
   enrollment admission — never neither, never both). The EVALUATION partition is read
   only at this step, for the headline result.

Report window and decision FAR/FRR/EER, ROC/DET, every eligible user, per-user spreads,
bootstrap confidence intervals, cohort and window counts, distinct days, hardware,
configuration checksums, code revision, exclusions, and limitations adjacent to each
claim. Random splits are diagnostics only; headline results are day-disjoint. Public
keyboard data and synthetic traces validate plumbing but are never project accuracy.

Runtime reports must include collector/backend CPU and memory, throughput, drops, and
p50/p95/p99 processing latency. The benchmark tool records hardware context and raw
samples; write its output under ignored `local-evidence/`. I2 informed mimicry, live
hijack A/B, real hardware collection, soak tests, E1, and E2 remain pending until humans
execute the approved protocols. Published accuracy is never an expected system result.

```powershell
python -m tools.benchmarks --pid PROCESS_ID `
  --config config/benchmark.development.yaml `
  --output local-evidence/runtime/backend.json
```
