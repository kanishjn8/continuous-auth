# Continuous Authentication Using Behavioral Biometrics

Local-first, Windows-primary continuous verification based on content-free keyboard
and mouse dynamics. The authoritative architecture is in [PLAN.md](PLAN.md), and team
ownership is in [TASK_DELEGATION.md](TASK_DELEGATION.md).

## Current status

T-002 foundation work is in progress. Protocol v1 schemas, deterministic Python/C++
binding generation, and privacy/architecture guardrails are the first implementation
slice. The Windows global-hook feasibility evidence in T-001 remains a parallel human
and Kanish review gate; it does not block these platform-independent contracts.

## Local checks

Requires Python 3.11+.

```powershell
python protocol/codegen/generate.py --check
python tools/guardrails/check.py
python -m pytest
```

The native collector and React dashboard skeletons have their own build instructions
in their directories once their toolchains are installed.

## Privacy boundary

The IPC contract carries key classes and capture-time monotonic timestamps, never key
identity or typed content. Participant data, databases, credentials, logs, and model
artifacts are excluded from version control.



