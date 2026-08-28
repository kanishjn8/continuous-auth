_e# Repository Instructions

This repository implements the architecture and privacy boundaries in `PLAN.md`.
`protocol/` is the only source of shared contracts, `ml/features/` is the only
feature implementation, and all tunable values belong in `config/`.

## Non-negotiable constraints

1. Never introduce an application-specific model, integration, or monitoring path.
2. Never transmit, persist, log, or expose typed content, keycodes, window titles,
   document names, file paths, URLs, clipboard data, screenshots, or screen data.
3. A raw platform key identifier may exist only as a callback-local value used for
   immediate key-class conversion. It must not cross that callback boundary.
4. Never add time-of-day or day-of-week as an authentication feature.
5. Never create a path that disables monitoring or reduces effective confidence to zero.
6. Never add training data outside the complete Model Update Manager promotion gate.
7. Never hardcode a tunable threshold, weight, cadence, window size, or quality limit
   outside `config/`.
8. Never commit participant data, credentials, databases, audit logs, or sensitive
   model artifacts. Everything below `data/` stays ignored.
9. Never load a model whose feature-schema version differs from the running extractor.
10. Never present published accuracy as an expected result for this system.
11. Never change an ADR or close an item marked `[OPEN]` without explicit human approval
    and the experiment required by `PLAN.md`.

## Working rules

- Read `PLAN.md`, `TASK_DELEGATION.md`, and this file before editing.
- Follow ownership in `TASK_DELEGATION.md`; cross-boundary contract changes require the
  producer and consumer reviewers named there.
- Change a shared interface in `protocol/schemas/` first, run the generator, and commit
  the generated Python/C++ bindings in the same change.
- Generated files carry a generated-file marker and must not be edited manually.
- Write tests for validation and failure behavior, not only the successful path.
- Use synthetic fixtures only. Real participant records are never test fixtures.
- Run `python tools/guardrails/check.py` and the affected test suites before handoff.
- Treat skipped required tests as failures unless the reason, owner, follow-up issue,
  and expiry are recorded.
