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
   ADR-013 enrollment admission gate and measures FRR on the held-out VALIDATION
   partition. FAR is measured by zero-effort cross-evaluation against **other
   participants'** VALIDATION windows *when the manifest contains another
   participant*; when it does not, FAR is recorded as unmeasured (see below). The
   EVALUATION partition is never read here — it is reserved for the headline result.
4. Run evaluation (`ml/evaluation/pipeline.py`) with `admission` supplied, so that any
   subsequent team/pilot training data continues to clear the appropriate boundary
   (`ml/training/common.py` accepts exactly one of a promotion-gate candidate map or an
   enrollment admission — never neither, never both). The EVALUATION partition is read
   only at this step, for the headline result.

### Single-participant order of operations (ADR-014 — the delivered build)

With one participant there is no impostor in the corpus, so `activate` takes the second
of its two paths. It is selected automatically by whether impostor scores were found —
there is no flag, no config switch, and no operator choice.

1. Stop collection.
2. **Freeze the corpus** — before anything else, and before any attacker touches the
   machine.
3. Create the evaluation lock over that frozen corpus.
4. `python -m tools.enrollment activate`:
   - trains on TRAIN through the ADR-013 gate, which additionally enforces the
     TRAIN-scoped `config/ml.*.yaml -> enrollment.min_train_windows /
     min_train_distinct_days` policy;
   - **measures FRR** on the held-out VALIDATION partition at the operating point the
     live system uses: `backend/app/models/service.py` maps a percentile to risk as
     `1 - percentile/100`, so the percentile threshold is
     `(1 - risk.medium_threshold) * 100`. The reported FRR is the fraction of the
     participant's own held-out windows that the live risk engine would place at or
     above MEDIUM;
   - **records FAR as `None` — UNMEASURED**, with reason code
     `ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT` and a
     `ValidationReport.operating_point` string that states plainly why. `0.0` is never
     written;
   - refuses outright with `ENROLLMENT_VALIDATION_NO_GENUINE` if the VALIDATION
     partition yields no scorable window — that is a data problem, not a cohort problem,
     and it must stop activation.
5. Observe the shadow period (`CALIBRATING`), then enable enforcement.
6. Run the live attacker drill (`docs/pilot/attack-drill-protocol.md`) — the impostor
   evidence for this build.

### Reporting rules

Report window and decision FRR, every eligible user, window counts, distinct days,
hardware, configuration checksums, code revision, exclusions, and limitations adjacent
to each claim. Random splits are diagnostics only; headline results are day-disjoint.
Public keyboard data and synthetic traces validate plumbing but are never project
accuracy.

Report FAR/EER, ROC/DET, per-user spreads, and bootstrap confidence intervals **only
when a cohort supplied them**. For the delivered single-participant build:

- **Cohort size is 1.** State it in those words next to every headline metric.
- **FAR is UNMEASURED, with the reason stated.** Never `0.0`, never "approximately
  zero", never silently omitted. Quote the stored `operating_point` string.
- **Per-user distributions and cross-user confidence intervals are not computable and
  must not be presented**, in any form.
- **The drill is reported as live security evidence, not as a FAR**: attack session id,
  duration, scored-window count, risk trajectory, highest risk, which of
  challenge / interruption / lockout / reauthentication fired, time and window count to
  each escalation, and the final enforcement outcome — each carrying N.
- **TRAIN composition is disclosed**, partial days included.
- **The participant is the author** — a self-collected, non-blind sample of one.

Runtime reports must include collector/backend CPU and memory, throughput, drops, and
p50/p95/p99 processing latency. The benchmark tool records hardware context and raw
samples; write its output under ignored `local-evidence/`. Real hardware collection and
soak tests remain pending until humans execute the approved protocols. Published
accuracy is never an expected system result.

Under ADR-014 the following are **not pending — they are not run for this build**, and
must be reported as such rather than as forthcoming: I1 zero-effort cross-evaluation and
I2 informed mimicry (both need a cohort member), and E1 and E2 (both need the update
pipeline, which is disabled — see `docs/update-manager.md`). The live hijack drill *is*
run, as a single-arm drill rather than an A/B, and is the impostor evidence for this
build.

```powershell
python -m tools.benchmarks --pid PROCESS_ID `
  --config config/benchmark.development.yaml `
  --output local-evidence/runtime/backend.json
```
