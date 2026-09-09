# Live attacker drill — operator protocol

The drill is the impostor evidence for this build (ADR-014, `PLAN.md` §13.3). It
replaces the cross-participant I1 evaluation that a single-participant corpus cannot
produce.

The attacker is a **classmate who has never contributed data**. They are never enrolled:
no `user_id` of their own, no consent record, no enrollment record, and no window in any
corpus. They sit at the legitimate user's already-`ACTIVE` machine and work normally.

---

## Non-negotiable ordering

```
1. Stop collection.
2. FREEZE THE CORPUS.            ← BEFORE any attacker touches the machine
3. Activate the profile.
4. Observe the shadow period (CALIBRATING).
5. Enable enforcement.
6. Start the backend WITH --drill-label.
7. Attacker operates.
8. Stop; export the risk trace.
9. NEVER create a freeze that includes drill sessions.
```

`tools/collection/repository.py::load_window_summaries` excludes declared drill sessions
and guardrail G12 protects that filter — but the ordering above is what makes the
guarantee **auditable after the fact** rather than merely enforced at the time. A
reviewer can check step 2 preceded step 7 from timestamps alone, without reading any
code.

If you find yourself needing to freeze again *after* a drill has run, stop and think
about why. The filter will exclude the drill windows, but a corpus frozen after a
takeover is a corpus whose provenance now needs explaining.

---

## Step 1 — Stop collection

`Ctrl+C` the collector, then the backend. Confirm the dashboard shows no live
heartbeat.

## Step 2 — Freeze the corpus

> **First run only:** the corpus tools refuse a database that predates storage
> migration `0004_attack_drill` with `DRILL_TABLE_MISSING` — an un-migrated database
> cannot prove drill sessions were excluded, and silently answering "no drills here"
> is exactly the failure the filter exists to prevent. Migrations apply when the
> backend opens the database, so start the backend once (any normal run) and stop it
> again before freezing.

```powershell
python -m tools.collection --config config/collection.pilot.yaml health `
  --database $env:CA_STORAGE_DB --administration data/collection/administration.json

python -m tools.collection --config config/collection.pilot.yaml freeze `
  --database $env:CA_STORAGE_DB --administration data/collection/administration.json `
  --version pilot-v1 --output data/frozen/pilot-v1/manifest.json
```

The freeze is **write-once** and the manifest file is written `0o400`. Read the health
report first; a freeze made under the wrong configuration cannot be redone under that
version name.

Record the manifest checksum and the freeze timestamp. They are the evidence that this
corpus predates the drill.

## Step 3 — Activate the profile

```powershell
python -m tools.enrollment activate `
  --participant-id manas-01 `
  --database $env:CA_STORAGE_DB `
  --manifest data/frozen/pilot-v1/manifest.json `
  --administration data/collection/administration.json `
  --artifact-root $env:CA_ARTIFACT_ROOT `
  --ml-config config/ml.development.yaml `
  --risk-config config/risk.development.yaml
```

Expected output for a single-participant corpus:

```json
{"validation_code": "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT",
 "validation_far": null,
 "validation_frr": <a real number in [0, 1]>}
```

`"validation_far": null` is the **correct** result and means *not measured*. If you ever
see `0.0`, something has regressed — stop and investigate; that number would be
fabricated.

Record the reported FRR and the `operating_point` string. Both belong in the report,
adjacent to each other.

## Step 4 — Observe the shadow period

Restart the backend normally (no `--drill-label`) and let the legitimate user work.

The state machine moves `ENROLLING → CALIBRATING → ACTIVE`. Only windows scored *by the
newly activated profile*, with at least one modality actually available, count toward
calibration — pre-activation rows carry `profile_version = "unavailable"` and are
excluded, and `INSUFFICIENT_DATA` windows do not count, so an idle period cannot age the
user into `ACTIVE`.

Review the live score distribution on the dashboard before proceeding. If the legitimate
user's own normal work is repeatedly reaching MEDIUM, the operating point is wrong and
running a drill against it would prove nothing.

## Step 5 — Enable enforcement

Set `enabled: true` in `config/enforcement.development.yaml` only after step 4 looks
sane. Restart the backend. Confirm the dashboard reports state `ACTIVE`.

## Step 6 — Start the backend for the drill

```powershell
python -m backend.app.runtime.cli `
  --participant-id manas-01 `
  --artifact-root $env:CA_ARTIFACT_ROOT `
  --drill-label drill-01
```

Then start the collector as usual.

`--drill-label` is opt-in and per-process. A normal collection run cannot accidentally
be labelled, and a drill run cannot accidentally be unlabelled, because the operator
types a different command. Use a distinct label per drill (`drill-01`, `drill-02`, …).

**What the label suppresses, for the whole session:**

| Suppressed | Why |
| --- | --- |
| Update-candidate submission | An attacker segment must never become a candidate, not even a rejected one |
| The automatic A1 login anchor | The person at the keyboard did not authenticate; granting A1 would hand the attacker the one anchor G2 exists to withhold |
| Context-confidence learning | Attacker behaviour must not become the new "genuine" baseline, even in memory, because it would distort the drill's own measurement |
| Enrollment / calibration progress | Attacker windows must not advance the legitimate user's enrollment bookkeeping |
| Inclusion in every corpus loader | Training, calibration, validation, enrollment, freeze, verification, health, and evaluation |

**What the label suppresses nothing about:** scoring, context assessment, risk fusion,
EWMA smoothing, K-of-N breach counting, **live risk-state transitions**, the escalation
ladder, soft challenges, reauthentication, lockout, enforcement actions, alerts, the
audit log, or the WebSocket stream. Suppressing any of those would mean the drill was
not testing the live system.

## Step 7 — The attacker operates

Brief the attacker to work naturally at ordinary tasks — typing and mouse use — not to
imitate anyone. This is a zero-effort unseen attacker, and the point is what the system
does with unfamiliar behaviour, not whether mimicry can defeat it.

Note the wall-clock takeover moment. Let the session run long enough to produce a
meaningful number of scored windows.

If enforcement locks the workstation, that is a successful drill outcome, not a failure.
Have the legitimate user's credentials to hand before you start.

## Step 8 — Stop and export

`Ctrl+C` the collector, then the backend.

Record, per drill:

| Field | Source |
| --- | --- |
| Attack session identifier | `drill_sessions.session_id` |
| Drill label | `drill_sessions.drill_label` |
| Attack duration | session start → end |
| Number of scored windows | `scores` rows for that session |
| Full risk trajectory | `risk_events` / decision stream across the transition |
| Highest risk reached | max over the trajectory |
| Soft challenge triggered? | `decisions.action` |
| Interruption triggered? | `decisions.action` |
| Lockout triggered? | `decisions.action` + `enforcement_applied` |
| Reauthentication triggered? | `decisions.action` + `outcome` |
| Time and window count to each escalation | first occurrence of each action |
| Final enforcement outcome | `decisions.outcome` |

## Step 9 — Do not re-freeze

Do not create a new freeze that includes drill sessions. If a second collection round
happens later, freeze it under a **new version name** and verify from the manifest that
no drill `window_id` appears in it.

---

## What this drill is, and is not

**It is** live security and robustness evidence: a demonstration that an unseen person
operating an `ACTIVE` machine is detected, and how quickly.

**It is not a FAR.** A handful of live sessions does not satisfy the statistical
requirements for a false-acceptance rate, and reporting it as one would be the same
fabrication ADR-014 exists to prevent. Report the trajectory and the latency, always
with N — the number of drills and the number of scored windows.

Formal FAR/EER/ROC remains an optional cohort-evaluation capability
(`PLAN.md` §13.2). The code for it is present and unchanged; it simply has no second
participant to run against.

---

## Safety and consent

The attacker is a person. Before the drill:

- Explain what is recorded — content-free keystroke and mouse *dynamics*, never typed
  content, titles, paths, or screen data — and get their agreement.
- Explain that the machine may lock mid-session.
- Their behaviour is recorded under the **legitimate user's** `user_id`, because they
  are being scored against that profile. They are not enrolled, are not a participant,
  and their windows never enter any training corpus. Say so explicitly.
- Stop immediately if they ask to stop.
