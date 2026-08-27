# Evaluation and reproducibility

Final evaluation starts only after `data/frozen/.../manifest.json` exists. Create an
immutable configuration/code/corpus lock with `tools.evaluation.create_evaluation_freeze`.
Every run must verify that lock before reading evaluation records. Post-freeze tuning
invalidates the result.

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
