# Pre-drill verification checklist

Everything here must be green **before a person is involved** in an attacker drill.
The drill itself is `attack-drill-protocol.md`; this is the gate in front of it.

## 1. The repository is sound

```powershell
python protocol/codegen/generate.py --check
python tools/guardrails/check.py          # must print: guardrails passed: G01-G12
python -m pytest
```

`G12_DRILL_EXCLUSION` failing means a corpus loader stopped filtering
`drill_sessions`. Stop and fix it — that guardrail is the last automated thing
standing between an attacker session and the training corpus.

## 2. The drill isolation tests specifically

```powershell
python -m pytest backend/tests/test_attack_drill.py -v
```

These assert **both** halves of the invariant, and the second half matters as much
as the first:

| Assertion | Why it is here |
| --- | --- |
| Drill windows are scored and stored | A suppressed drill measures nothing |
| Live risk-state transitions still happen during a drill | The drill exists to exercise them |
| Drill windows are excluded from `load_window_summaries` | Closes health, freeze, verify, corpus |
| Genuine windows still pass the same filter | The filter is not over-broad |
| Drill segments never become update candidates | Not even `REJECTED` ones |
| A genuine session still does submit one | The suppression is drill-specific |
| No login anchor for a drill session | G2 withholds exactly this evidence |
| An existing genuine anchor survives a drill | Takeover cannot redefine the anchor |
| Drill windows do not advance enrollment progress | Attacker data is not enrollment evidence |
| A genuine session does advance it | Again: not a disabled code path |
| Context learning is suspended during a drill | The attacker must not become the baseline |
| Un-migrated database raises `DRILL_TABLE_MISSING` | Fail-safe, not fail-quiet |

## 3. The corpus and profile are in the right state

- [ ] The database has been opened by the backend at least once since storage
      migration `0004_attack_drill` landed (otherwise the corpus tools refuse).
- [ ] `python -m tools.collection ... health` reports no blocking reason.
- [ ] **The corpus is already frozen**, and the manifest's timestamp precedes today.
- [ ] `python -m tools.enrollment activate` has run and reported
      `"validation_code": "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"` with
      `"validation_far": null` and a real `validation_frr`.
- [ ] `"validation_far"` is **not** `0.0`. If it is, stop — that number would be
      fabricated and something has regressed.
- [ ] The dashboard shows user state `ACTIVE` (not `CALIBRATING`, not `DEGRADED`).
- [ ] The shadow period was actually observed, and the legitimate user's own normal
      work was **not** repeatedly reaching MEDIUM. If it was, the operating point is
      wrong and a drill against it would prove nothing.
- [ ] `config/enforcement.development.yaml` has `enabled: true`.

## 4. Practicalities before the attacker sits down

- [ ] The attacker has been briefed on what is recorded, that the machine may lock,
      and that they can stop at any time.
- [ ] The legitimate user's credentials are to hand, because a successful drill may
      end in a locked workstation.
- [ ] The drill label for this run is chosen and unique (`drill-01`, `drill-02`, …).
- [ ] The wall-clock takeover moment will be noted.

## 5. After the drill

- [ ] Risk trace exported and the per-drill fields in `attack-drill-protocol.md`
      §Step 8 recorded.
- [ ] **No new freeze created.** If a later round needs one, use a new version name
      and verify from the manifest that no drill `window_id` appears in it.
- [ ] Every reported drill figure carries N — the number of drills and the number of
      scored windows — and is described as live security evidence, never as a FAR.
