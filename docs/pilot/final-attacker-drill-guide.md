# Final Attacker Drill Guide

**Audit date:** 2026-09-12. **Drill type:** Path B — session hijack / walk-away takeover of an already-authenticated, already-`ACTIVE` Windows PC. **Repository commit at audit time:** `da9b2d44948cc20df488adb3f95828cbaafb1068` (branch `pilot-default-workflow-spec`), plus the uncommitted working-tree changes listed in §2.1 (the enforcement-session machinery this drill exercises).

This document was produced by a read-only audit: no config, threshold, code, or database value was changed to produce it. Every fact below was verified against the actual repository, the actual test suite, and the actual pilot database at `%LOCALAPPDATA%\ContinuousAuthentication\Pilot\continuous-auth.db` — not assumed from documentation.

---

## 0. READ THIS FIRST — two blocking findings

### Finding A — enforcement is OFF in the config that tomorrow's run will load

`config/enforcement.development.yaml` line 22: **`enabled: false`**. This is the file `load_enforcement_settings()` reads by default, and it is what `backend/app/runtime/cli.py` loads unless you pass `--enforcement-config` pointing somewhere else. With `enabled: false`, the entire drill will run with decisions computed and recorded but **no native prompt, no workstation lock, and the backend-side posture gate (`EnforcementSessionState`) permanently disabled** (`enabled=False` is threaded straight into it — see §2.3). You will see `SOFT_CHALLENGE` / `REAUTH` / `TERMINATE` in the decision stream, but every one will carry `outcome_code = ENFORCEMENT_DISABLED` and nothing will actually happen. This is exactly what the pilot database shows happened on every past run to date (§2.4).

**You must decide to flip this before tomorrow. This guide does not do it for you.** See §2.2 for the exact one-line change and how to verify it took effect.

### Finding B — the legitimate user's own genuine behavior is currently saturating MEDIUM/HIGH under 0.80/0.90

This is a data finding, not a config bug, and it is more important than Finding A. Querying the pilot database directly:

The most recent completed session (`session-ed161d47-d7fa-4b01-9c26-e40fa8cce6b2`, 2026-09-10 03:47–09:24 UTC — the session that ran immediately after the current profile (`20260910T034634.521619`) was activated, i.e. exactly the "shadow period" `attack-drill-protocol.md` Step 4 tells you to review before enabling enforcement) has this **state-level** risk-level distribution:

| risk_level | count | % of session |
|---|---|---|
| LOW | 2 | 0.7% |
| MEDIUM | 120 | 44.8% |
| HIGH | 140 | 52.2% |
| UNAVAILABLE | 6 | 2.2% |

That is the legitimate user, working normally, on their own `ACTIVE` profile, under the currently configured `medium_threshold=0.80` / `high_threshold=0.90`. Only 2 of 268 scored windows were LOW. `avg(smoothed_score)` for the session is **0.71**. Separately, the all-time `decisions` table (spanning multiple historical runs, not just this one) shows the policy has reached `TERMINATE` (the sustained-HIGH, skip-`REAUTH` rung) **16 times** and `REAUTH` **70 times** for requested actions — all suppressed only because enforcement was disabled at the time.

The project's own `docs/pilot/attack-drill-protocol.md` (Step 4) and `docs/pilot/pre-drill-checklist.md` (§3) both say, in effect: *if the legitimate user's own normal work repeatedly reaches MEDIUM, the operating point is wrong and a drill against it proves nothing.* The live evidence above is worse than that condition — it shows the genuine baseline reaching **HIGH** more often than not. If you flip Finding A on without addressing this, expect the legitimate user's own presence to trigger `SOFT_CHALLENGE`/`REAUTH`/lockout before the attacker ever sits down, which would make it impossible to attribute any escalation during the drill specifically to the attacker's behavior rather than to the model's baseline false-positive rate against its own enrolled user.

**This guide does not change the thresholds — that is explicitly out of scope and you told me not to.** What you do with this finding (proceed anyway and note the caveat, spend part of tomorrow re-observing the shadow period live on the dashboard before enabling enforcement, or postpone) is your call. §7 and §19 build this into the genuine-baseline and abort-condition procedures so it cannot be silently missed tomorrow.

---

## 1. Purpose

Evaluate continuous authentication under a **session-hijacking / walk-away attack** (Path B): the legitimate user (`manas-01`) is already authenticated on the Windows PC and steps away; a teammate who does **not** know the legitimate user's security-challenge answer or Windows password takes over the same authenticated session and behaves as an attacker. The drill measures whether, and how fast, the live risk engine and enforcement ladder detect the takeover — it is **not** a FAR/EER measurement (§15), and it must be fully isolated from every training/enrollment/calibration corpus (§17), per ADR-014.

Selected operating point (not to be changed by this guide or by anyone executing it): `medium_threshold = 0.80`, `high_threshold = 0.90` (`config/risk.development.yaml`).

---

## 2. Current System Configuration

### 2.1 What is actually loaded, and by what

| Config file | Loaded by | Default path baked into code | Currently |
|---|---|---|---|
| `config/enforcement.development.yaml` | `backend/app/decisions/config.py::load_enforcement_settings`, called from both `runtime/application.py::create_collection_application` and `main.py::create_runtime_app` | `DEFAULT_ENFORCEMENT_CONFIG = <repo>/config/enforcement.development.yaml` | `enabled: false` (Finding A) |
| `config/risk.development.yaml` | `backend/app/risk/config.py::load_risk_settings`, called from `runtime/cli.py` via `--risk-config` (defaults to this file) | same pattern | `medium_threshold=0.80`, `high_threshold=0.90`, confirmed correct |
| `config/ml.development.yaml` | `ml/features/config.py::load_config`, via `--ml-config` | same pattern | `window_seconds=30.0`, `window_keystrokes=100` |
| `config/storage.pilot.yaml` | `backend/app/storage/config.py::load_storage_settings`, via `--storage-config` (this is the **default** in `runtime/cli.py`) | `root_directory: "${LOCALAPPDATA}/ContinuousAuthentication/Pilot"` | Real `PILOT` provenance; database confirmed present at `%LOCALAPPDATA%\ContinuousAuthentication\Pilot\continuous-auth.db` |
| `config/api.development.yaml` | `backend/app/api/config.py::load_api_settings`, via `--api-config` | — | `bind_host=127.0.0.1`, `port=8765`, `session_ttl_seconds=1800` |
| `config/orchestration.development.yaml` | `runtime/config.py::load_orchestration_settings` | — | `heartbeat_timeout_seconds=5.0` |
| `config/collector.development.yaml` | native collector + `runtime/collector_config.py` | — | `heartbeat_interval_seconds=1.0`, pipe name `continuous-auth-v1` |
| `config/context.development.yaml` | `backend/app/risk/context_config.py::load_context_config` | — | context-confidence layer params (not part of this drill's scope to change) |

`backend/app/runtime/cli.py` (`python -m backend.app.runtime.cli`) is the single entry point that wires all of the above together via `create_collection_application` (`backend/app/runtime/application.py`). This is what you will run tomorrow — not `main.py::create_runtime_app`, which is a control-plane-only stub with no risk engine (used by the Compose/API-dev-only path, not the full pipeline).

**Working tree note:** `git status` shows uncommitted changes to `backend/app/decisions/{__init__.py,adapters.py,challenge.py}`, `backend/app/api/{backend.py,routes.py}`, `backend/app/main.py`, `backend/app/runtime/{application.py,orchestrator.py}`, three new untracked files (`backend/app/decisions/session.py`, `backend/tests/test_enforcement_session.py`, `dashboard/src/views/ChallengeResponseView.tsx`), and dashboard/doc files. **These uncommitted changes are exactly the enforcement-session machinery this drill depends on** (`EnforcementSessionState`, the C7 `/v1/enforcement/*` gate, the recovery-reauthentication flow). They are live in your working directory right now, so running `python -m backend.app.runtime.cli` tomorrow from this same working tree will use them. If you `git stash`, `git checkout .`, or otherwise revert before tomorrow, this whole drill becomes impossible — the drill depends on code that is not yet committed.

### 2.2 Is enforcement enabled? Exact fix if you choose to enable it

**Currently: `EnforcementSettings.enabled = False`.** Verified by reading `config/enforcement.development.yaml` line 22 and confirmed against the database: the `decisions` table's most recent rows all show `outcome = ENFORCEMENT_DISABLED` for `REAUTH`/`SOFT_CHALLENGE`/`TERMINATE` actions.

Minimal change, if you decide to proceed (do this yourself — this guide does not do it):

```yaml
# config/enforcement.development.yaml, line 22
enforcement:
  enabled: true   # was: false
```

Effect of this one line:
- `NativeChallengeAdapter` will actually spawn the native always-on-top prompt process for `SOFT_CHALLENGE`/`REAUTH` (subject also to `native_prompt: true`, already set).
- `WindowsLockAdapter` will actually call `LockWorkStation()` on `TERMINATE` (subject also to `lock_workstation: true`, already set).
- `EnforcementSessionState(enabled=...)` (constructed in `RuntimeOrchestrator.__init__`, `backend/app/runtime/orchestrator.py:116`) will actually track posture and the C7 gate (`require_unenforced` in `backend/app/api/routes.py:206`) will actually start returning `403` once posture reaches `REAUTH_REQUIRED`/`LOCKED_OUT`.
- Nothing else changes: decisions are computed and recorded identically whether this flag is true or false (confirmed by reading `RiskEngine.process`, which never reads `EnforcementSettings`).

**How to verify the change took effect, before the attacker sits down:**
1. Restart the backend (`enforcement.development.yaml` is only read at process start — there is no hot-reload).
2. Sign in to the dashboard → **Settings** tab. It renders `enforcement?.enforcement_enabled ? "enabled" : "disabled"` directly from the API (`dashboard/src/views/SettingsView.tsx:30`) — it will now say **"enabled"**.
3. Or query directly: `GET /v1/enforcement/status` while signed in → `"enforcement_enabled": true`.

Do **not** just trust that you edited the file; the dashboard/API check above is the actual runtime value, not the file on disk (a stale process, a wrong `--enforcement-config` path, or a YAML typo that silently fails validation would all disagree with the file).

### 2.3 Runtime start commands (exact)

Backend + ML engine + risk engine + API/WebSocket + built dashboard, in one process (`127.0.0.1:8765`):

```powershell
# One-time per terminal session:
.\.venv\Scripts\Activate.ps1
$env:CA_DASHBOARD_SECRET = Read-Host "Dashboard password"
$env:CA_ARTIFACT_ROOT = "$env:LOCALAPPDATA\ContinuousAuthentication\Pilot\models"
$env:CA_STORAGE_DB   = "$env:LOCALAPPDATA\ContinuousAuthentication\Pilot\continuous-auth.db"

# Normal (non-drill) run — use this for Phase 0/1 genuine baseline:
python -m backend.app.runtime.cli `
  --participant-id manas-01 `
  --artifact-root $env:CA_ARTIFACT_ROOT `
  --dashboard-directory dashboard\dist

# Drill run — use this ONLY once the attacker is about to take over (Phase 2+):
python -m backend.app.runtime.cli `
  --participant-id manas-01 `
  --artifact-root $env:CA_ARTIFACT_ROOT `
  --dashboard-directory dashboard\dist `
  --drill-label drill-01
```

`--storage-config` defaults to `config/storage.pilot.yaml` (real `PILOT` data) — do not pass `config/storage.development.yaml`, that would silently switch to disposable synthetic storage and the drill would not run against the real profile.

The collector (separate terminal, from repo root, no venv needed — it is a native `.exe`, already built at `build/collector/Release/continuous_auth_collector.exe`):

```powershell
.\build\collector\Release\continuous_auth_collector.exe `
  --config config/collector.development.yaml `
  --categories config/app_categories.yaml
```

Start the backend **before** the collector (the collector reconnects with backoff if the named pipe isn't up yet).

**Dashboard build command** (already built — `dashboard/dist/index.html` exists in the working tree — but rebuild if you touch any dashboard source between now and tomorrow, since the enforcement-session dashboard code above is currently uncommitted, unbuilt-into-`dist` source):

```powershell
Set-Location dashboard
npm ci
npm run typecheck
npm test
npm run build
Set-Location ..
```

**There is no separate dashboard dev server command** — `dashboard/package.json` has no `dev` script. The backend serves `dashboard/dist` as static files via `StaticFiles` mount (`backend/app/runtime/application.py:150-152`). If you change any `dashboard/src/*` file (including the already-modified `challenge.ts`, `useDashboard.ts`, `App.tsx`, and the new `ChallengeResponseView.tsx`), you must `npm run build` again before the backend will serve the new behavior — the currently built `dashboard/dist` was almost certainly built before some of today's uncommitted dashboard changes. **Rebuild it before tomorrow and confirm `npm test` still shows 22/22 passing** (confirmed passing as of this audit).

### 2.4 Environment variables that influence enforcement enablement

None. `EnforcementSettings.enabled` comes exclusively from the YAML file resolved by `--enforcement-config` (default `config/enforcement.development.yaml`). There is no environment-variable override anywhere in `backend/app/decisions/config.py`. The only enforcement-adjacent environment variables in the codebase are `CA_CHALLENGE_ENDPOINT` / `CA_CHALLENGE_DECISION_ID` / `CA_CHALLENGE_QUESTION` / `CA_CHALLENGE_TOKEN` / `CA_CHALLENGE_BLOCKING`, which `backend/app/decisions/native.py::spawn_prompt` sets **for the spawned native-prompt child process only** — these are outputs of the running system, not inputs you set yourself.

### 2.5 Current live-system state (as audited; re-verify tomorrow, this changes over time)

Queried directly from `%LOCALAPPDATA%\ContinuousAuthentication\Pilot\continuous-auth.db` (read-only, `mode=ro`) at audit time:

| Question | Answer | Evidence |
|---|---|---|
| 9. Clean NORMAL posture? | Yes, trivially — `EnforcementSessionState` is in-memory only and no backend process is currently running, so posture cannot be anything but `NORMAL` right now. This tells you nothing about tomorrow; re-check after starting the backend (`GET /v1/enforcement/status` → `session.posture` should read `NORMAL`). | §2.7 (posture is process-local) |
| 10. Pending challenges? | None possible right now — `ChallengeService._pending` is an in-memory dict, never persisted. With no backend process running, there are zero. | `backend/app/decisions/challenge.py:201` |
| 11. Existing `REAUTH_REQUIRED`/`LOCKED_OUT`? | Same as above — impossible while no process is running. | same |
| 12. Stale drill/session state? | `drill_sessions` table exists (migration `0004_attack_drill`, applied 2026-09-10T03:42:44Z) and is currently **empty** — zero drills have ever been run against this database. No contamination risk from a prior drill. | direct query: `SELECT * FROM drill_sessions` → `[]` |
| User state | `manas-01` → `ACTIVE` as of 2026-09-10T09:23:59Z | `SELECT state FROM users` |
| Active profile | `20260910T034634.521619`, status `ACTIVE` | `model_profiles` table |
| Security challenge configured? | **Yes** — `storage_metadata` has a `security_challenge` row (188 bytes), last set 2026-09-05T06:35:08Z | `SELECT metadata_key, length(metadata_value) FROM storage_metadata` (value itself never read) |

### 2.6 Repository soundness (all commands run during this audit, read-only)

| Check | Command | Result |
|---|---|---|
| Generated protocol bindings up to date | `python protocol/codegen/generate.py --check` | Clean (no output = no drift) |
| All guardrails | `python tools/guardrails/check.py` | `guardrails passed: G01-G12` |
| Full backend test suite | `python -m pytest` | **510 passed**, 0 failed, 204.6s |
| Enforcement/challenge/drill tests specifically | `python -m pytest backend/tests/test_enforcement_session.py backend/tests/test_challenge.py backend/tests/test_challenge_api.py backend/tests/test_attack_drill.py -q` | **95 passed** |
| Dashboard typecheck | `npm run typecheck` (in `dashboard/`) | Clean |
| Dashboard tests | `npm test` (in `dashboard/`) | **22 passed** |

**The repository, including the uncommitted enforcement-session code, is sound and ready.** The only blockers are the two findings in §0, both of which are operator decisions, not code defects.

### 2.7 Does a backend restart clear enforcement posture? What must NOT restart?

**Yes — a backend restart silently clears everything.** `EnforcementSessionState` (`backend/app/decisions/session.py`) is constructed fresh in `RuntimeOrchestrator.__init__` on every process start, with `self._posture = EnforcementPosture.NORMAL` hardcoded as the initial value (line 111). It is never persisted to the database. Likewise `ChallengeService._pending` (in-memory dict of outstanding challenges) starts empty on every process start.

**Consequence: do not restart the backend during the drill.** Doing so — even to "fix" something — silently resets `LOCKED_OUT`/`REAUTH_REQUIRED` back to `NORMAL` and discards any outstanding challenge, which invalidates the enforcement half of the experiment (the escalation you were measuring simply vanishes, with no record that it was cleared by a restart rather than a legitimate recovery). If the backend crashes or is restarted mid-drill, treat that specific drill run as compromised for enforcement-ladder purposes (the scoring/risk data up to that point in `risk_events`/`scores` is still valid and still in the database) and log it as an anomaly (§20.10).

**What is safe to restart:** the **collector**. It only feeds heartbeats and raw events over the named pipe; restarting it does not touch `EnforcementSessionState`. If the collector stops (heartbeat timeout is 5.0s, `config/orchestration.development.yaml`), the risk engine degrades (`UserState.DEGRADED`, fail-open — enforcement is suspended, not tightened, while degraded) and automatically recovers once heartbeats resume (`RuntimeOrchestrator._handle_heartbeat` → `RiskEngine.component_recovered()`, added in commit `e5c3142`). A collector restart during the drill is a recoverable, loggable event, not a fatal one — but note the gap in the timeline when it happens.

---

## 3. Runtime Commands

See §2.3 for the exact, complete commands. Summary:

| Component | Command | Notes |
|---|---|---|
| Backend (genuine run) | `python -m backend.app.runtime.cli --participant-id manas-01 --artifact-root $env:CA_ARTIFACT_ROOT --dashboard-directory dashboard\dist` | No `--drill-label` |
| Backend (drill run) | same + `--drill-label drill-01` | Use a fresh label per drill attempt (`drill-01`, `drill-02`, …) |
| Collector | `.\build\collector\Release\continuous_auth_collector.exe --config config\collector.development.yaml --categories config\app_categories.yaml` | Start after the backend |
| Dashboard build | `npm ci && npm run typecheck && npm test && npm run build` in `dashboard/` | No separate dev server exists |
| Liveness check | `Invoke-RestMethod http://127.0.0.1:8765/healthz` | Process-alive only, not protection health |
| Dashboard URL | `http://127.0.0.1:8765` | Sign in with `CA_DASHBOARD_SECRET` |

---

## 4. Challenge Credential Setup & Verification

### 4.1 The three distinct credentials — do not confuse them

| Credential | What it is | Configured where | Needed by the attacker? | Needed at which drill stage |
|---|---|---|---|---|
| **Normal login credential** | `CA_DASHBOARD_SECRET` — a local shell env var, hashed with `secret_hash_iterations=200000` (`config/api.development.yaml`), used only to authenticate to the dashboard/API (`POST /v1/auth/login`) | Set by whoever starts the backend process | **The attacker already has this by construction of Path B** — they are sitting at the already-unlocked, already-signed-in machine. They never need to type it. | Never re-entered during the drill unless the dashboard session (`session_ttl_seconds=1800`, i.e. 30 min) expires and someone has to sign back in — that is a dashboard-login event, unrelated to enforcement posture. |
| **Challenge credential** (question + answer) | The participant-chosen security question, stored as a PBKDF2-HMAC-SHA256 digest (`backend/app/decisions/challenge.py`) | `storage_metadata.security_challenge` (already configured, §2.5) | **No — the attacker must never be given it.** This is the entire point of the drill. | Answers a dispatched `SOFT_CHALLENGE` or `REAUTH` |
| **"Reauthentication credential"** | There is no separate mechanism. `open_reauthentication()` (`/v1/enforcement/reauthenticate`) mints a `REAUTH`-type `PendingChallenge` using the **same** stored challenge credential. Answering it correctly is what the code calls a "reauthentication" and what clears `REAUTH_REQUIRED`/`LOCKED_OUT`. | Same as challenge credential | **No — same secret, same rule.** | Clears `REAUTH_REQUIRED` or `LOCKED_OUT` |

**In short: there are two credentials in this system, not three.** "Reauthentication" is just the name for answering the same security-challenge credential when it's being used to clear a blocked posture rather than to satisfy a routine `SOFT_CHALLENGE`. The Windows password is irrelevant to enforcement — `WindowsLockAdapter` calls the OS `LockWorkStation()`, and clearing that lock is a Windows login, not an API call; the API-side posture (`LOCKED_OUT`) is cleared independently, through the console/native prompt, using the security-challenge answer.

### 4.2 Where it's configured, and whether one exists now

`ChallengeService.is_configured()` (`backend/app/decisions/challenge.py:217`) is simply `self._credential_or_none() is not None`, i.e. whether `storage_metadata.security_challenge` has a row. **Confirmed present** (§2.5) — value length 188 bytes, last written 2026-09-05T06:35:08Z. You do **not** need to configure one before tomorrow; you need to **verify** it, and decide whether to rotate it.

### 4.3 Exact verification steps (no secret ever leaves the process)

1. **Dashboard, without touching enforcement:** sign in → **Settings** tab. It calls `GET /v1/enforcement/challenge` (`challenge_status()`, `backend/app/api/backend.py:322`) and renders only `{"configured": true, "question": "<your question text>"}`. The answer is never returned by any endpoint, ever — confirmed by reading every response path in `challenge.py`/`backend.py`/`routes.py`; the digest never leaves `ChallengeService`.
2. **Database, read-only, without the dashboard running:**
   ```powershell
   # Confirms a challenge exists and when it was last changed — never reads metadata_value's content.
   python -c "import sqlite3; c=sqlite3.connect('file:' + r'$env:CA_STORAGE_DB' + '?mode=ro', uri=True); print(c.execute(\"SELECT length(metadata_value), updated_at_utc FROM storage_metadata WHERE metadata_key='security_challenge'\").fetchall())"
   ```
   A non-empty result confirms configuration without printing the question or answer.
3. **Repository health tool** (`docs/pilot/pre-drill-checklist.md` already prescribes this, confirmed still valid): `python -m tools.collection --config config/collection.pilot.yaml health --database $env:CA_STORAGE_DB --administration data/collection/administration.json` — surfaces blocking reasons if any exist.

### 4.4 If you need to configure or rotate it

**API (PUT), while signed in and while enforcement is not currently blocking** — `require_unenforced` gates this endpoint deliberately (`routes.py:460-484`), so rotation is impossible while a posture is already escalated (an attacker mid-lockout cannot swap the credential out from under you):

```powershell
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8765/v1/enforcement/challenge `
  -WebSession $session `
  -Body (@{ question = "<new question>"; answer = "<new answer>"; confirm_answer = "<new answer>"; current_answer = "<existing answer, required only if one is already configured>" } | ConvertTo-Json) `
  -ContentType "application/json"
```

**Dashboard UI (equivalent, and the intended path):** Settings tab → "Security challenge" card → the rotation form (`ChallengeForm` with `rotating` prop, `dashboard/src/views/SettingsView.tsx:54-58`) → requires the current answer, a new question (≥4 chars), and a new answer (≥4 chars) entered twice. Answer fields are `type="password"` (`ANSWER_INPUT_TYPE`, `dashboard/src/challenge.ts:18`), never rendered in clear text.

Since one is already configured, you do not need first-run setup (`ChallengeSetupView`, which only appears automatically when `challenge.configured === false`).

### 4.5 Testing the challenge flow before tomorrow without contaminating the drill

- The 95 backend tests in §2.6 (`test_challenge.py`, `test_challenge_api.py`, `test_enforcement_session.py`) already exercise the full open/respond/expire/accept/reject state machine against an isolated temporary database — this is the safe way to verify the *code path* works, and it already passed.
- If you want to see the live native prompt fire once, do it against `config/storage.development.yaml` (disposable `SYNTHETIC` data) rather than the pilot database, and do **not** use `--drill-label` for that (a synthetic smoke test is not a drill). This keeps it fully out of `PILOT` provenance and out of `drill_sessions`.
- **Do not** test the live challenge flow against the real `manas-01` pilot database with enforcement enabled before tomorrow unless you also account for it in your drill-day evidence (it would create real alert/decision rows in the same database the drill reads from — harmless to corpus integrity per se, since it isn't training data anyway, but it pollutes the alert timeline you'll be reading tomorrow).

### 4.6 Does configuring/rotating the credential touch model/risk data?

No. `ChallengeService.configure()` writes only to `storage_metadata.security_challenge` (a PBKDF2 digest + salt + question, JSON-serialized). It never touches `feature_windows`, `scores`, `model_profiles`, or `risk_events`. Rotating it does not invalidate past verification anchors (`SettingsView.tsx` says this explicitly and it matches the storage code — no cascading update to `verification_anchors`).

### 4.7 What to record in the pre-drill checklist after verifying it

- [ ] `GET /v1/enforcement/challenge` (or Settings tab) shows `configured: true` and displays the expected question text.
- [ ] The legitimate user confirms, out loud, that they know the current answer (do not have them type it into a shared screen while briefing the attacker).
- [ ] Timestamp of this verification.
- [ ] Whether you rotated it today — if so, record the new rotation timestamp (from `storage_metadata.updated_at_utc` or the `SECURITY_CHALLENGE_CHANGED` audit event) so it's clear which credential was live during the drill.

---

## 5. Windowing and Sample Size

All values below are read directly from `config/ml.development.yaml`, `config/risk.development.yaml`, `config/enforcement.development.yaml`, and `backend/app/risk/engine.py` / `backend/app/decisions/policy.py` — not assumed.

| Parameter | Actual value | Source | Meaning for tomorrow |
|---|---|---|---|
| Window duration | 30.0 s | `config/ml.development.yaml: windowing.window_seconds` | A window closes at 30s of wall-clock activity, **or** earlier (see next row) |
| Window keystroke cap | 100 keystrokes | `config/ml.development.yaml: windowing.window_keystrokes` | Heavy typing closes a window early — `ml/features/windowing.py:308` closes on whichever limit is hit first |
| Min keystrokes for a scorable window | 5 | `config/ml.development.yaml: quality_gate.min_keystrokes` | Below this, the window is `INSUFFICIENT_DATA` and produces no risk decision |
| Min mouse samples for a scorable window | 10 | `config/ml.development.yaml: quality_gate.min_mouse_samples` | Same effect for mouse-only windows |
| EWMA smoothing factor (α) | 0.4 | `config/risk.development.yaml: risk.ewma_alpha` | `smoothed = 0.4·adjusted + 0.6·previous_smoothed`; first window in a session has no damping (`smoothed_1 = adjusted_1`) |
| Medium threshold | 0.80 | `config/risk.development.yaml: risk.medium_threshold` | On the smoothed-score scale |
| High threshold | 0.90 | `config/risk.development.yaml: risk.high_threshold` | Same scale |
| K-of-N window (N) | 5 | `config/risk.development.yaml: risk.breach_n` | `RiskEngine._history` is a `deque(maxlen=5)` of the 5 most recent smoothed scores |
| K-of-N breach count (K) | 3 | `config/risk.development.yaml: risk.breach_k` | Need ≥3 of the last ≤5 smoothed scores over a threshold to enter that level |
| Hysteresis exit rule | ALL of the last 5 below the boundary | `RiskEngine._with_hysteresis`, `backend/app/risk/engine.py:123-136` | To drop from HIGH back down, every one of the last 5 smoothed scores must be `< 0.90`; to drop out of MEDIUM, all 5 must be `< 0.80` |
| Cooldown | 60 s | `config/risk.development.yaml: risk.cooldown_seconds` | Per-action-type: the **same** action (e.g. two `SOFT_CHALLENGE`s) cannot both apply within 60s of each other |
| Action budget | 2 | `config/risk.development.yaml: risk.action_budget` | At most 2 enforcement actions of **any** type may apply within any rolling 60s window (`DecisionPolicy.decide`, `backend/app/decisions/policy.py:59-66`); a 3rd is suppressed with `ACTION_BUDGET` and an `ACTION_BUDGET_EXHAUSTED` alert, not applied |
| Challenge timeout | 120.0 s | `config/enforcement.development.yaml: enforcement.challenge_timeout_seconds` | An unanswered `SOFT_CHALLENGE`/`REAUTH` expires after 2 minutes and is treated as a failed response |
| Reauthentication timeout | Same 120.0 s | Same field — reauthentication uses the identical `PendingChallenge` mechanism | No separate timeout exists |
| Lockout trigger | Failed `REAUTH` response (wrong answer or expiry), **or** 5-of-5 smoothed scores ≥ 0.90 | `backend/app/decisions/session.py:255-263` (failed-response path) and `policy.py:36-38` (sustained-HIGH path skips straight to `TERMINATE`) | Two independent roads to `LOCKED_OUT` |
| Heartbeat timeout | 5.0 s | `config/orchestration.development.yaml` | Collector silence >5s → `DEGRADED` (fail-open, enforcement suspended, not tightened) |

### 5.1 Windows required to reach each rung (best case — extreme, immediately-high attacker score; realistic cases take longer because of EWMA damping and natural variance)

| Milestone | Minimum windows | Minimum wall-clock (at 30s/window) | Why |
|---|---|---|---|
| First `MEDIUM` | 3 | ~90 s | `breach_k=3` of `≤5`-window history ≥ 0.80 |
| First `HIGH` | 3 | ~90 s | Same K-of-N test against 0.90 — can happen at the same window as first MEDIUM if the raw score is high enough to clear both thresholds at once, or later if the attacker's score climbs gradually |
| First `REAUTH` request | Same window as first HIGH, if `high_count < 5` | ~90 s | `DecisionPolicy._requested`: HIGH with `high_count < breach_n(5)` → `REAUTH` |
| Sustained HIGH → `TERMINATE` directly (skips `REAUTH`) | 5 | ~150 s (2.5 min) | Requires **all 5** of the last 5 smoothed scores ≥ 0.90 (`high_count == breach_n`) |
| `REAUTH_REQUIRED` (posture) | Same window as the first *applied* `REAUTH` action, subject to cooldown/budget | ~90 s+ | Also reachable via a **failed `SOFT_CHALLENGE` response** advancing one rung (`session.py:258-263`), independent of the engine's own HIGH/REAUTH decision |
| `LOCKED_OUT` (posture) | As soon as a `REAUTH` is answered wrong/expires, or a `TERMINATE` is applied | ~90 s + up to 120 s challenge timeout if the attacker never answers | Two paths, see table above |

**Action-budget interaction to expect:** if escalation is very fast (e.g. a `SOFT_CHALLENGE` at window 3 and a `REAUTH` at window 4, both within the same 60s), that already consumes the budget of 2; a third action required within that same 60s window (e.g. an immediate `TERMINATE`) will be suppressed as `ACTION_BUDGET` and **not applied** until the rolling 60s window frees a slot. Do not read a "missing" escalation step during a very fast attacker ramp-up as a bug — check `alerts` for `ACTION_BUDGET_EXHAUSTED` first.

---

## 6. Pre-Drill Checklist

See §9 for the full, printable version. Everything below must be green before the attacker is involved.

- [ ] Correct commit + uncommitted enforcement-session changes still present (`git status` matches §2.1 — do **not** stash or revert before tomorrow)
- [ ] `medium_threshold=0.80`, `high_threshold=0.90` confirmed in `config/risk.development.yaml` (unchanged)
- [ ] `config/enforcement.development.yaml: enabled` set to `true` (decision made — Finding A) and verified live via Settings/`/v1/enforcement/status`
- [ ] Finding B (genuine baseline saturating MEDIUM/HIGH) reviewed and a decision made about how to proceed (§0, §7)
- [ ] Security challenge confirmed configured (§4.3) and the legitimate user confirms they know the answer
- [ ] `python -m pytest`, `python tools/guardrails/check.py`, `python protocol/codegen/generate.py --check` all green (already confirmed at audit time — rerun if you touch any code)
- [ ] `dashboard/dist` rebuilt from current `dashboard/src` (§2.3) if any dashboard file changed since the last build
- [ ] User state is `ACTIVE` (`manas-01`, confirmed) and not `DEGRADED`/`CALIBRATING`
- [ ] `drill_sessions` table confirmed empty (confirmed) — no stale drill state
- [ ] No pending challenge / no stale `REAUTH_REQUIRED`/`LOCKED_OUT` — trivially true with no backend process running (§2.5); re-confirm via `GET /v1/enforcement/status` → `posture: NORMAL` immediately after starting the backend, **before** the attacker sits down
- [ ] Corpus already frozen (per `docs/pilot/attack-drill-protocol.md` non-negotiable ordering) — **verify this yourself**: `data/frozen/pilot-v1/manifest.json` exists in the repo, confirm its timestamp precedes tomorrow
- [ ] Drill label chosen and unique (`drill-01`, or `drill-02` etc. if this is a repeat)
- [ ] Legitimate user's Windows password and security-challenge answer are to hand (not given to the attacker)

---

## 7. Genuine Baseline Procedure

1. Start the backend **without** `--drill-label` (§2.3, "genuine run"), start the collector.
2. Confirm via dashboard: user state `ACTIVE`, `GET /v1/enforcement/status` → `posture: NORMAL`, `configured: true`.
3. **Given Finding B, do this before deciding to enable enforcement**: watch the live score/decision stream (Overview/Live views) for at least 10–15 minutes (≈20–30 windows) of the legitimate user's actual, normal work — not a synthetic "act normal" performance, their real ordinary tasks. Note the fraction reaching MEDIUM/HIGH. The last recorded shadow-period session had that fraction at ~97% (only 2/268 LOW); if you observe something similarly skewed live, this is your last checkpoint to decide whether tomorrow's drill will produce interpretable results, before the irreversible step of enabling enforcement (§0, Finding B).
4. Only after that review, flip `enforcement.development.yaml: enabled: true` and restart the backend (§2.2).
5. Continue the genuine baseline for a further clearly-delimited period immediately before handoff — **target 20–40 additional scored windows (10–20 minutes)** of uninterrupted genuine use with enforcement now live, so you have a clean, contiguous, enforcement-on genuine segment to contrast against the attacker segment. Do not mix attacker actions into this window under any circumstances.
6. Record the wall-clock start and end of this baseline segment and the session_id (`GET /v1/state` or the dashboard header shows the active session).

---

## 8. Attacker Handoff

- **Takeover moment (`t_attack_start`):** the instant the legitimate user physically leaves the keyboard/mouse and the attacker takes over — a manually recorded wall-clock timestamp (say it out loud, write it down, or post a marker via `POST /v1/alerts/{alert_id}/acknowledge` is not appropriate for this — just log the timestamp in your notes/spreadsheet). The system has **no explicit "handoff" event or API** — there is no user re-authentication at this point (that is the entire premise of a session-hijack drill: no new login occurs). The only system-observable proxy for the handoff is the first feature window whose behavioral pattern is the attacker's, which you will only be able to identify after the fact from the risk trajectory — the operator-recorded wall-clock timestamp is the ground truth.
- **What the attacker is allowed to do:** operate the machine normally at ordinary tasks (typing, browsing, mouse use) as themselves — see §9 for concrete blocks. They must not attempt to guess or brute-force the challenge answer, must not be told it, and must not attempt to bypass the API directly with credentials they don't have (that's a separate, legitimate thing to test once — see §11's bypass check — but it's not "the attack").
- **What the attacker must NOT do:** be told the security-challenge answer or the Windows password; deliberately mimic the legitimate user's typing/mouse style; deliberately act erratically purely to force detection (the point is a realistic, zero-effort unseen attacker, not an adversarial worst case — `attack-drill-protocol.md`'s own framing).
- **Does the attacker know they're being evaluated?** Yes — per the safety/consent requirements in `attack-drill-protocol.md` and `pre-drill-checklist.md` (§4), they must be briefed beforehand: what's recorded (content-free keystroke/mouse dynamics only), that the machine may lock, that they may stop at any time, and that their behavior is scored against the legitimate user's profile but never enrolled or trained on. This is a consent requirement, not optional realism.
- **May the attacker answer prompts?** Only with an honest wrong-or-no-answer. If a `SOFT_CHALLENGE`/`REAUTH` native prompt appears, the attacker may make one reasonable, plausible-looking incorrect attempt (not a string of obviously nonsense keystrokes) or simply let it expire (120s) — do not have them guess repeatedly to "test" the credential's strength; that is not the scenario and burns your action budget/cooldown window on retries that teach nothing.
- **First soft challenge:** treat it exactly like any other challenge in §10 — non-blocking, work continues, record everything.

---

## 9. Attacker Behavior Protocol

Realistic session-hijack behavior, not adversarial mimicry or deliberately erratic input. Adapt durations to reach the window target you choose in §5.1/§0's target discussion (150–200 windows recommended → ~75–100 minutes total attacker time at 30s/window, minus any early window closures from heavy typing).

| Block | Approx. duration | Approx. windows (@30s) | What the attacker does | What to record |
|---|---|---|---|---|
| A. Light browsing | 10 min | ~20 | Read pages, scroll, click links, no heavy typing | Window count, quality label distribution (`FULL`/`KBD_ONLY`/`MOUSE_ONLY`/`INSUFFICIENT_DATA`) |
| B. Typing-heavy activity | 15 min | ~30 (some windows may close early at the 100-keystroke cap) | Compose an email/document/notes in their own natural style | Number of windows closed by the keystroke cap vs. the 30s timer |
| C. Mouse-heavy activity | 10 min | ~20 | File management, dragging windows, using a design/spreadsheet tool with heavy pointer use | Mouse sample counts per window |
| D. Application switching | 10 min | ~20 | Alt-tab between 3–4 applications of different categories (per `config/app_categories.yaml` / `context.development.yaml` bootstrap weights: e.g. PRODUCTIVITY, BROWSING, DEVELOPMENT) | `context_confidence` and `confidence_source` fields in `risk_events` for this block |
| E. Different typing rhythm | 10 min | ~20 | Continue typing naturally in their own rhythm — do not attempt to imitate the legitimate user's cadence | Fused/smoothed score trend across the block |
| F. Sustained normal-looking interaction | 15–20 min | ~30–40 | A longer uninterrupted stretch of ordinary mixed use, to observe steady-state behavior once any initial escalation has resolved (post-challenge/post-recovery, if applicable) | Whether risk re-escalates after a successful legitimate-user recovery — see §13 |
| G. Varied behavior | 10 min | ~20 | Natural mix of A–F as the session winds down | — |

**Total: ~80–100 minutes, ~160–200 windows** — within the recommended target in §0/§5.1. Record, per block: start/end wall-clock time, block label, and note any system prompts or lock events that interrupted it (which block they occurred in and at what point).

Do not require unnatural random behavior to force a failure — the objective is realistic session hijacking, and the ladder's own K-of-N/EWMA design means realistic attacker behavior against a well-separated model should still escalate within the windows above if the model actually distinguishes the two users; if it doesn't, that is itself the finding, not a reason to make the attacker behave more artificially.

---

## 10. Soft Challenge Procedure

When the risk level reaches `MEDIUM` and the policy applies (subject to cooldown/budget), the system dispatches a `SOFT_CHALLENGE` — **non-blocking**: the attacker keeps working while it's outstanding (`PendingChallenge.blocking` is `False` for `SOFT_CHALLENGE`, `challenge.py:170-173`). With enforcement enabled, the console shows an inline banner (`enforcementHeadline`/`enforcementDetail` for `SOFT_CHALLENGE`: *"Quick identity check" / "Please confirm it is still you. You can keep working while you answer."*) and a native always-on-top prompt process spawns (`NativeChallengeAdapter.execute`).

Record, for every soft challenge:

| Field | Where to find it |
|---|---|
| Timestamp | `alerts.occurred_at_utc` where `code` starts `ENFORCEMENT_` or is `CHALLENGE_DISPATCHED`; also `decisions.outcome_at_utc` |
| Risk score at dispatch | `risk_events.fused_score` / `smoothed_score` for the triggering `decision_id` |
| Decision | `risk_events.risk_level` = `MEDIUM`, `risk_events.action` = `SOFT_CHALLENGE` |
| Challenge dispatched? | `decisions.outcome` = `CHALLENGE_DISPATCHED` (native prompt spawned) or `ENFORCEMENT_DISABLED`/`CHALLENGE_NOT_CONFIGURED`/`NATIVE_PROMPT_UNAVAILABLE` if something prevented it |
| Challenge ID | `decision_id` from the triggering `risk_events` row (a `SOFT_CHALLENGE`/`REAUTH` challenge's id equals the risk decision's own `decision_id` — not one of the `scheduled-anchor:`/`recovery:` prefixes) |
| Attacker's response | Your own notes — what they typed/whether they let it expire |
| Response result | `POST /v1/enforcement/challenge/{decision_id}/respond` return value: `ACCEPTED`/`REJECTED`/`EXPIRED` |
| Resulting posture | `GET /v1/enforcement/status` → `session.posture` |
| Resulting decision | Next `risk_events` row's `action`/`risk_level` |

**After a soft challenge:**
- **A. Correct answer** — should not happen; the attacker doesn't know it. If it somehow did, `session.py:243-253` clears posture back to `NORMAL` (only if current posture is at or below `SOFT_CHALLENGE` — a later escalation is not undone by a stale correct answer) and records nothing else special.
- **B. Incorrect answer** — `ResponseOutcome.REJECTED`. Advances the posture exactly one rung, to `REAUTH_REQUIRED` (`session.py:255-263`) — this is a deliberate design choice ("a mistyped answer" reads the same as a wrong-guess attacker; the ladder does not distinguish intent).
- **C. Timeout/expiry (120s, unanswered)** — `ResponseOutcome.EXPIRED`, same effect as B: advances to `REAUTH_REQUIRED`. Expiry is swept lazily, on the next API request or WebSocket connection attempt (`sweep_expired_challenges`, called from `require_unenforced` and the WebSocket handler) — so if nobody touches the API for a while, the expiry won't visibly "happen" until something does. Don't be alarmed if the posture doesn't flip the instant the clock hits 120s; it flips on the next request.
- **D. No response at all** — identical to C; there is no separate "ignored" outcome.

---

## 11. Reauthentication Procedure

Triggered either by the engine directly (HIGH risk, `high_count < 5` → `REAUTH` requested) or by a failed/expired `SOFT_CHALLENGE` (§10.B/C). Reauthentication **blocks**: `PendingChallenge.blocking` is `True` for `REAUTH`, and once posture reaches `REAUTH_REQUIRED`, `require_unenforced` (`routes.py:206-230`) returns `403 REAUTHENTICATION_REQUIRED` on every protected route, and the WebSocket stream closes with code `4403` on the next connection attempt.

The attacker must **not** be given the answer. If they ask for a way to continue, the honest system behavior is: they cannot, until the legitimate user answers.

Record:

| Field | Where |
|---|---|
| Timestamp of `REAUTH_REQUIRED` | `PostureTransition.occurred_at`, surfaced as an `alerts` row with `code = ENFORCEMENT_REAUTH_REQUIRED` |
| Reason | `alerts.code` (`ENFORCEMENT_REAUTH_REQUIRED` from a direct engine decision, or `ENFORCEMENT_CHALLENGE_REJECTED`/`ENFORCEMENT_CHALLENGE_EXPIRED` if it arrived via a failed soft challenge) |
| Risk score | `risk_events.fused_score`/`smoothed_score` for the triggering decision |
| Reauthentication attempt | `POST /v1/enforcement/challenge/{decision_id}/respond`, or the legitimate user answering via the native prompt or the console's verification screen (`ChallengeResponseView.tsx`, which requests a **fresh** reauthentication via `POST /v1/enforcement/reauthenticate` if the originally dispatched challenge has already been consumed/expired) |
| Valid/invalid result | `ACCEPTED` clears to `NORMAL` (`session.py:244-247`) and records an `A2_REAUTH` verification anchor; anything else escalates to `LOCKED_OUT` |
| Timeout | Same 120s `challenge_timeout_seconds`; a `REAUTH` that expires unanswered → `LOCKED_OUT` directly (this is the "failed reauth ⇒ TERMINATE" rule from the ladder) |
| Resulting posture | `GET /v1/enforcement/status` |

**Verifying protected access is actually blocked (do this at least once):**
1. While posture is `REAUTH_REQUIRED`, from a **second** terminal or `curl`/`Invoke-RestMethod`, call any protected route directly with a **valid, currently-signed-in session cookie** (not a stale/unauthenticated one — the point is to prove the block is not just "you're logged out"): e.g. `GET /v1/history/decisions`. Expect `403` with body code `REAUTHENTICATION_REQUIRED`.
2. Try refreshing the dashboard page, opening a new browser tab to the same URL, and reconnecting the WebSocket (just reload the page). All three should show the same blocked state — this is the point of `require_unenforced` living server-side rather than in the browser (`routes.py:209-224`'s own docstring: *"a page refresh, a WebSocket reconnect, a second browser tab, or a direct curl cannot walk past an outstanding reauthentication"*).
3. Do **not** attempt this by modifying code, environment variables, or the database to force a bypass — that is out of scope for "verify safely without modifying the system." A same-session direct API call with the existing valid cookie, as above, is sufficient and non-destructive.

---

## 12. Lockout Procedure

`LOCKED_OUT` (the **application-level posture** — dashboard/API blocked) is reached by exactly two paths (`backend/app/decisions/session.py`):
1. A `REAUTH` challenge is answered incorrectly or expires (`observe_challenge_outcome`, lines 255–263).
2. The risk engine's own policy requests `TERMINATE` directly, without an intervening `REAUTH`, when **all 5** of the last 5 smoothed scores are ≥ `high_threshold` (`policy.py:36-38`).

**These two paths are not equivalent, and this distinction matters.** `WindowsLockAdapter` — the only code that calls the real Windows `LockWorkStation()` — is registered **exclusively** for `DecisionAction.TERMINATE` (`runtime/application.py:111`) and is invoked **only** from `EnforcementCoordinator.execute(decision)`, which runs only for decisions the risk engine itself produces (`orchestrator.py::_process_window_inner`). Path 1 (a failed/expired `REAUTH` answer) is handled entirely inside `backend/app/api/backend.py::respond_to_challenge` / `sweep_expired_challenges`, which call `EnforcementCoordinator.record_challenge_response` — a bookkeeping method that records an `ActionOutcome` directly and **never calls any `ActionAdapter`, including `WindowsLockAdapter`**. So:

- **Path 2 (sustained HIGH, 5-of-5 ≥0.90) → real Windows lock.** This is the only way the actual desktop gets locked.
- **Path 1 (failed/expired REAUTH) → `LOCKED_OUT` posture only.** The dashboard/API is blocked (403 on every protected route, verification screen shown), but the real Windows session is **not** locked by this path alone — the participant could, in principle, still use the rest of the unlocked machine outside the browser tab. Only a subsequent risk-engine `TERMINATE` (path 2) or the operator manually locking the machine would actually lock Windows in this case.

When `TERMINATE` does fire, `WindowsLockAdapter.execute` calls the real Windows `LockWorkStation()` (subject to `enabled=true` **and** `lock_workstation=true`, both currently `true`).

Record:

| Field | Where |
|---|---|
| Timestamp | `PostureTransition.occurred_at` → `alerts` row, `code = ENFORCEMENT_LOCKED_OUT` (from `TERMINATE`) or `ENFORCEMENT_CHALLENGE_REJECTED`/`ENFORCEMENT_CHALLENGE_EXPIRED` (from a failed `REAUTH`) |
| Triggering decision | The `decisions_id`/`risk_events` row whose `action=TERMINATE`, or the `decision_id` of the failed `REAUTH` response |
| Triggering risk | `fused_score`/`smoothed_score` at that point (expect ≥0.90, likely close to the max of the recent history if it's the sustained-HIGH path) |
| Failed reauth / termination reason | `decisions.outcome` = `CHALLENGE_REJECTED` / `CHALLENGE_EXPIRED`, or `outcome = WORKSTATION_LOCKED` for the direct-TERMINATE path |
| Application enforcement state | `GET /v1/enforcement/status` → `posture: LOCKED_OUT`, `locked_out: true` |
| Windows workstation lock | Only expected if this `LOCKED_OUT` was reached via path 2 above (direct sustained-HIGH `TERMINATE`) — observe directly, the actual Windows lock screen should appear. If it doesn't in that case, check `decisions.outcome` for `WORKSTATION_LOCK_FAILED` (fail-open: `ActionStatus.FAILED_OPEN`) or `WORKSTATION_LOCK_UNSUPPORTED_PLATFORM`/`ENFORCEMENT_DISABLED`. If `LOCKED_OUT` was reached via path 1 (failed/expired `REAUTH`), **no real Windows lock is expected at all** — only the dashboard/API block; do not treat an unlocked desktop as a failure in that case. |
| Audit event | `alerts` row as above, plus the `audit_outbox`/on-disk `audit/audit-YYYY-MM-DD.jsonl` chain (`%LOCALAPPDATA%\ContinuousAuthentication\Pilot\audit\`) |
| Final posture | `LOCKED_OUT` until legitimate-user recovery (§13) |

**Do not restart the backend to clear the lockout.** As established in §2.7, `EnforcementSessionState` is process-local, in-memory state — a restart silently resets posture to `NORMAL` with no audit trail explaining why, which both (a) looks indistinguishable from a legitimate recovery in your evidence trail, and (b) means the lockout you're trying to measure the recovery time for never actually gets a measured recovery — the "recovery" would be an operator action outside the system's own security model, not the reauthentication flow the drill exists to test. Clear it only via §13.

---

## 13. Recovery Procedure

1. The legitimate user regains physical access to the machine (attacker steps away).
2. If the Windows workstation is locked (real `LockWorkStation()` call), they log in with their **Windows password** first — this is separate from and prior to anything API-level; it only restores desktop access, it does not touch `EnforcementSessionState`.
3. Once at the console/dashboard again: if posture is `REAUTH_REQUIRED` or `LOCKED_OUT`, the console is already showing the verification screen (`ChallengeResponseView.tsx`) or the native prompt is still up. If the originally dispatched challenge already expired, the user (or the console automatically) calls `POST /v1/enforcement/reauthenticate` to mint a fresh one (`open_reauthentication`, `backend.py:479-503` — refuses if posture isn't actually blocking, so this can't be used to manufacture evidence on a quiet session).
4. The legitimate user answers with the **correct** security-challenge answer.
5. `ACCEPTED` + `action == REAUTH` → `EnforcementSessionState` moves to `NORMAL` (`session.py:244-247`), **regardless of whether the posture was `REAUTH_REQUIRED` or `LOCKED_OUT`** — a correct `REAUTH` answer is the system's only recovery path from either.
6. **Evidence created:** an `A2_REAUTH` verification anchor is recorded (`EnforcementCoordinator.record_challenge_response` or `record_recovery_reauthentication`, depending on whether it went through the engine-dispatched challenge or the manually-opened recovery one — both produce an identical `A2_REAUTH` anchor type). This is **not suppressed by drill labeling** (`orchestrator.py:646-661`'s own comment: *"Drill sessions are excluded from every corpus loader regardless, so the anchor cannot promote attacker windows into training data"* — recording it is simply true, since a real correct answer was produced).
7. **Preserve before recovering:** export/record the full risk trajectory and alert timeline up to this point (§16) before you let the legitimate user resolve the lockout, in case anything about the export process is easier while the session is still in its escalated state (e.g., screenshots of the lock/verification screen for the report).
8. **Verify session health after recovery:** `GET /v1/enforcement/status` → `posture: NORMAL`, `locked_out: false`; reload the dashboard and confirm protected routes serve normally again; confirm the WebSocket reconnects without the `4403` close code.
9. Note that risk-state itself (the EWMA history, the K-of-N deque) is **not** reset by a recovery — if the legitimate user's own subsequent behavior still resembles the recent high-risk window (unlikely, but the deque still holds up to 5 recent smoothed values), the very next window or two could re-escalate. This is expected engine behavior, not a bug — the docstring in `attack-drill-protocol.md` says exactly this: *"if the attacker is still at the keyboard the next windows escalate again, subject to the unchanged cooldown and action budget."*

---

## 14. Metrics to Collect

### A. Dataset metrics
- Genuine windows (baseline segment, §7) and attacker windows (§9), counted from `feature_windows` filtered by `session_id`/timestamp range.
- Total windows; drill wall-clock duration (attacker segment start→end); attacker activity duration per block (§9).
- Valid (scored) vs. `INSUFFICIENT_DATA` windows: `risk_events.risk_level = 'UNAVAILABLE'` with `reason_code = 'INSUFFICIENT_EVIDENCE_HOLD'`, vs. everything else.
- Excluded windows and reason: none should be excluded from `feature_windows`/`scores` themselves (drill windows are scored and stored — only *corpus* queries exclude them, see §17); if you exclude any manually (e.g. a segment interrupted by a collector restart), record why.

### B. Score metrics
- Raw per-modality calibrated score: `scores.score_json` (`keyboard.calibrated_score`, `mouse.calibrated_score`).
- Fused risk: `risk_events.fused_score`. Smoothed (post-EWMA) risk: `risk_events.smoothed_score`.
- Median, mean, standard deviation, percentiles (p05/p25/p75/p95), min, max — compute over the attacker segment's `fused_score` and separately `smoothed_score`, using a read-only query/export (§16); no built-in endpoint aggregates this for you.

### C. Threshold metrics (attacker segment)
- Count and % of attacker windows with `fused_score >= 0.80`, and separately `>= 0.90` — per-window, pre-smoothing (this is the same unit `docs/threshold-pair-analysis-2026-09-10.md` reports for the genuine baseline, so it's directly comparable).

### D. Decision metrics
- `LOW`/`MEDIUM`/`HIGH` counts (`risk_events.risk_level`), `TERMINATE` count (`risk_events.action = 'TERMINATE'`).
- State transitions: every row where `risk_level` differs from the previous row for that session, ordered by `t_decision_us`.
- K-of-N activations: every point where `medium_count`/`high_count` crossed `breach_k=3` (reconstructable from consecutive `smoothed_score` values against the thresholds — not stored directly, must be derived).
- Hysteresis behavior: any window where the *candidate* level (recomputable from the smoothed-score history) was lower than the *resulting* `risk_level` — evidence hysteresis held the level up.

### E. Enforcement metrics
- Soft challenges issued/succeeded/failed/expired; reauthentication requests/succeeded/failed/expired; lockouts — all countable from `decisions.action` + `decisions.outcome` (§10–§12 tables) plus `alerts.code LIKE 'ENFORCEMENT_%'`.
- Protected requests blocked: your manual bypass-check calls (§11) plus any incidental `403`s during the drill (visible only in your own client logs / browser network tab — the backend does not log successful-403 counts anywhere separate from the `alerts`/posture already captured).
- WebSocket behavior: note every `4401`/`4403`/`4408` close code observed (unauthenticated / enforcement-blocked / slow-client-disconnect respectively — `routes.py`).

### F. Timing/latency metrics
- `t_attack_start` (your recorded wall-clock, §8) → first `MEDIUM`, first `HIGH`, first `REAUTH_REQUIRED`, `LOCKED_OUT` — subtract your recorded timestamp from the relevant `risk_events.t_decision_us`/`stored_at_utc`.
- `MEDIUM`→challenge dispatch, challenge→reauth, reauth→lockout — same, using `alerts.occurred_at_utc` and `decisions.outcome_at_utc`.
- If multiple escalation cycles occur (escalate → recover → re-escalate), report the distribution (e.g. list each cycle's latency, not just one number).

### G. Security metrics
- Attacker acceptance rate = fraction of attacker windows that stayed `LOW` (never contributed to a breach) — descriptive only, **not a FAR** (§15).
- Detection: whether `MEDIUM`/`HIGH`/`REAUTH_REQUIRED`/`LOCKED_OUT` was reached at all during the attacker segment, and how fast (F above).
- Attack session outcome: did it end in `LOCKED_OUT`, in a sustained `REAUTH_REQUIRED` the attacker never cleared, or did the drill end (time-boxed) while still `LOW`/`NORMAL`? All three are valid, reportable outcomes.
- Whether the attacker ever accessed a protected route after `REAUTH_REQUIRED`/`LOCKED_OUT` (§11's bypass check — should always be "no").

### Evidence/audit metrics
- `risk_events`, `decisions`, `alerts`, `verification_anchors` row counts and full dumps for the drill's `session_id`; `drill_sessions` row for the label; correlation via `session_id`/`segment_id`/`decision_id` throughout.

---

## 15. FAR/FRR/ROC/DET/EER Methodology

**Be precise about what one drill can and cannot produce — this section follows `docs/pilot/attack-drill-protocol.md`'s own explicit position, verified against the code, not a general statistics lecture.**

- The system **does** store the raw ingredient you'd need for a per-window ROC-style sweep: `scores.score_json` (per-modality calibrated scores) and `risk_events.fused_score` (fused, pre-smoothing) are both persisted per window, for both the genuine baseline and the attacker segment, queryable by `session_id`. `docs/threshold-pair-analysis-2026-09-10.md` demonstrates exactly this kind of export/sweep already, for the genuine side.
- **What you can calculate from tomorrow's single drill:**
  1. **Per-window attacker "acceptance" at the current operating point**: the fraction of attacker windows with `fused_score < 0.80` (stayed under MEDIUM) — a descriptive statistic about this one attacker, this one day, this one operating point. Report it as exactly that, with N (number of scored attacker windows).
  2. **Detection latency** (§14F) — a real, meaningful measurement; this is what the drill is actually for.
  3. **Enforcement-ladder demonstration** — did `REAUTH_REQUIRED`/`LOCKED_OUT` actually fire, and was protected access actually blocked (§11). Binary, but real and directly observed.
- **What you cannot calculate, and must not report as calculated:**
  - **A population FAR.** One attacker, one session, is N=1 at the session level. `docs/pilot/attack-drill-protocol.md` states this explicitly: *"It is not a FAR. A handful of live sessions does not satisfy the statistical requirements for a false-acceptance rate, and reporting it as one would be the same fabrication ADR-014 exists to prevent."* The activation report for this profile already correctly records `validation_far: null` (not-measured) for exactly this reason (`docs/pilot/attack-drill-protocol.md` Step 3) — a real number would be fabricated. Do not compute a per-window "acceptance rate" tomorrow and call it FAR; call it what §15's item 1 above calls it.
  - **A proper ROC/DET curve or EER.** These require a real impostor cohort (multiple distinct attackers, ideally many sessions each) paired against the same genuine distribution, at a range of thresholds. One attacker's one session gives you one point's worth of qualitative evidence at the single already-selected operating point (0.80/0.90) — not a curve. `threshold-pair-analysis-2026-09-10.md` §5 makes the same point about the genuine side alone: *"No FAR/impostor column exists, in either unit, at any pair. ... A pair cannot be chosen from this table alone."*
  - **Session-level false acceptance** (the attacker's whole session judged "accepted" because it never reached `HIGH`) vs. **per-window false acceptance** (some windows scored low even if the session overall escalated) are different things — report both counts separately if relevant, and never collapse one into the other.
  - Do **not** conflate "a window's `fused_score` crossed 0.90" with "the attacker was actually locked out" — the former is a per-window score-threshold crossing; the latter requires the full K-of-N/EWMA/cooldown/budget pipeline to actually apply a `TERMINATE` (or a failed `REAUTH`) and have `WindowsLockAdapter` succeed. Report both, distinctly (§14C vs §14D/E).
- **Which data source for which metric:** score-distribution descriptive stats (§14B/C) use **live drill data** for the attacker side and can be cross-referenced against the **frozen dataset** (`data/frozen/pilot-v1/manifest.json`) for the genuine side, exactly as `threshold-pair-analysis-2026-09-10.md` already did. Enforcement-ladder metrics (§14D/E/F/G) only make sense against **live drill data** — the frozen corpus has no enforcement events in it.
- **What additional data a real ROC/EER would need:** multiple distinct attackers (ideally several people, not repeated sessions from the same one, to average out one person's idiosyncratic behavior), each contributing a comparable number of scored windows to the genuine baseline's, run at multiple operating points (or with raw scores swept post-hoc the way `threshold-pair-analysis-2026-09-10.md` did for the genuine side) — this is explicitly out of scope for a single-participant pilot and is called out as such in ADR-014 and `PLAN.md` §13.2 ("Formal FAR/EER/ROC remains an optional cohort-evaluation capability... it simply has no second participant to run against").

---

## 16. Evidence and Audit Collection

| Evidence | Table/location | Notes |
|---|---|---|
| Feature windows | `feature_windows` | Scored regardless of drill label |
| Scores | `scores` (joined via `window_id`) | `score_json` has per-modality detail |
| Risk decisions | `risk_events` | `decision_json` has the full `RiskDecision` |
| Enforcement action outcomes | `decisions` | `ActionOutcome` records — action, status, code, timestamp |
| Alerts (availability, behavioral, tamper) | `alerts` | `code LIKE 'ENFORCEMENT_%'` for posture transitions specifically |
| Verification anchors | `verification_anchors` | `A1_LOGIN_UNLOCK` (suppressed for the drill session itself, §17), `A2_REAUTH`, `A3_SCHEDULED_PROMPT` |
| Drill label | `drill_sessions` | `session_id` → `drill_label` mapping |
| Audit chain (append-only, on-disk) | `%LOCALAPPDATA%\ContinuousAuthentication\Pilot\audit\audit-YYYY-MM-DD.jsonl` | Confirmed present, files exist for 2026-09-05 through 2026-09-10 |
| Sessions/segments | `sessions`, `segments` | Start/end timestamps, `reason` for segment boundaries |

**Export approach:** since `sqlite3` CLI is not installed on this machine (confirmed during this audit — use `python`'s built-in `sqlite3` module instead, exactly as this audit did), export with a short read-only Python script (`sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`) rather than installing new tooling. Query by the drill's `session_id` (from `drill_sessions` or `sessions.started_at_utc` matching your recorded takeover time) to scope every export to the correct segment. Always open with `mode=ro` — never write to the live pilot database with an ad hoc script.

For the genuine baseline, `python -m tools.collection --config config/collection.pilot.yaml health --database $env:CA_STORAGE_DB --administration data/collection/administration.json` gives an aggregate, reviewed summary without hand-rolled SQL.

---

## 17. Data Contamination Protection

Verified directly from code (`backend/app/storage/drill.py`, `backend/app/runtime/orchestrator.py`) and from the passing `backend/tests/test_attack_drill.py` suite (part of the 510 passing tests, §2.6):

- **Labeling mechanism:** `--drill-label <label>` on `runtime.cli` → `RuntimeOrchestrator._drill_label` → `storage.declare_drill_session(session_id, drill_label)` writes one row to `drill_sessions` at session start (`orchestrator.py:247-248`). This is **opt-in and per-process** — a normal run cannot accidentally be labeled, and a drill cannot accidentally be unlabeled, because they are different commands (§2.3).
- **Exclusion mechanism:** every corpus-purpose reader of `feature_windows` applies `DRILL_EXCLUSION_PREDICATE = "session_id NOT IN (SELECT session_id FROM drill_sessions)"` (`storage/drill.py:49`). The five call sites are documented in that module's own docstring: `tools/collection/repository.py` (health/freeze/verify), `tools/collection/corpus.py` (training/enrollment/validation/evaluation), `backend/app/runtime/orchestrator.py` (enrollment progress counts), `ml/experiments/app_usage_study.py` (dataset statistics), `tools/demo/bootstrap_first_model.py` (synthetic demo training).
- **Guardrail G12** (`tools/guardrails/check.py`) fails the build if any corpus loader stops filtering — confirmed passing (§2.6).
- **What is suppressed for a drill session** (from `orchestrator.py` comments, verified against the code they annotate): update-candidate submission (`_submit_segment_candidate` returns early), the automatic `A1_LOGIN_UNLOCK` anchor (`start_authenticated_session` branches on `self._drill_label is not None`), context-confidence learning (`context_layer.suspend_learning(True)`), enrollment/calibration progress (`_progress_enrollment_and_calibration` returns early).
- **What is NOT suppressed** (and must not be, or the drill tests nothing): scoring, fusion, EWMA, K-of-N, live risk-state transitions, the escalation ladder, challenges, reauthentication, lockout, alerts, the audit chain, the WebSocket stream, and — deliberately — the `A2_REAUTH` recovery anchor (§13.6), because a real correct answer is real evidence regardless of the session's drill label, and the drill-session exclusion at the corpus layer already prevents it from ever reaching training data.
- **Before the drill:** confirm `drill_sessions` is empty (confirmed, §2.5) and the corpus is already frozen (`data/frozen/pilot-v1/manifest.json`, confirm its timestamp precedes tomorrow — `attack-drill-protocol.md`'s non-negotiable ordering: freeze **before** any attacker touches the machine).
- **After the drill:** confirm the new drill session appears in `drill_sessions` with the correct label; **do not re-freeze** the corpus (a new freeze after a drill would need to prove, from the manifest, that no drill `window_id` is included — simplest is to just not do it; if a later genuine round needs a new freeze, use a new version name and verify against the manifest).
- **The one honest caveat:** the exclusion is a *corpus-read-time* filter, not a separate storage location — drill windows live in the same `feature_windows` table as everything else, distinguished only by `session_id` membership in `drill_sessions`. This is fine and is exactly what `require_drill_table`'s fail-safe (`DrillTableMissingError`) exists to protect against being silently wrong about — but it means the guarantee depends on `drill_sessions` never being deleted/edited by hand. Don't touch that table directly.

---

## 18. Expected Results

| Stage | Expected risk/decision | Expected enforcement | What you should observe | Evidence to verify |
|---|---|---|---|---|
| LOW | `risk_level=LOW`, `action=CONTINUE` | None | Normal dashboard, no banners | `risk_events.risk_level='LOW'` |
| MEDIUM | `risk_level=MEDIUM` after ≥3-of-5 smoothed scores ≥0.80 | `SOFT_CHALLENGE` dispatched (native prompt + console banner), non-blocking | Work continues uninterrupted; banner/prompt appears | `alerts.code` incl. `CHALLENGE_DISPATCHED`; `decisions.action='SOFT_CHALLENGE'` |
| SOFT CHALLENGE SUCCESS | (should not occur — attacker doesn't know answer) | Posture → `NORMAL` if at/below `SOFT_CHALLENGE` | Banner clears | `session.posture='NORMAL'`; `A2_REAUTH`? No — only `REAUTH` acceptance creates an anchor; a soft-challenge accept creates none |
| SOFT CHALLENGE FAILURE | wrong answer or 120s expiry | Posture → `REAUTH_REQUIRED` | Console/native prompt escalates to blocking verification | `alerts.code='ENFORCEMENT_CHALLENGE_REJECTED'` or `..._EXPIRED`; posture flips on next request/sweep |
| HIGH | ≥3-of-5 smoothed scores ≥0.90 | `REAUTH` requested (if `high_count<5`) or `TERMINATE` (if `high_count==5`) | Console replaced by verification screen, or lock, depending on which | `risk_events.risk_level='HIGH'`; `decisions.action` |
| REAUTH_REQUIRED | as above | All protected routes `403`; WebSocket closes `4403` | Full console replaced by `ChallengeResponseView` | `GET /v1/enforcement/status`; manual bypass check (§11) returns 403 |
| REAUTH SUCCESS | correct answer from legitimate user | Posture → `NORMAL` from either `REAUTH_REQUIRED` or `LOCKED_OUT` | Console restores fully | `A2_REAUTH` verification anchor created; `session.posture='NORMAL'` |
| REAUTH FAILURE | wrong answer | Posture → `LOCKED_OUT` | Console/native prompt shows lockout messaging | `alerts.code='ENFORCEMENT_CHALLENGE_REJECTED'`; `decisions.outcome='CHALLENGE_REJECTED'` |
| REAUTH EXPIRY | 120s unanswered | Posture → `LOCKED_OUT` | Same as failure | `alerts.code='ENFORCEMENT_CHALLENGE_EXPIRED'` |
| LOCKED_OUT | failed/expired `REAUTH` (API/dashboard blocked only — **no real Windows lock**), or direct sustained-HIGH `TERMINATE` (API blocked **and** real Windows lock via `LockWorkStation`) | See left — the two triggers have different real-world effects, not just different reason codes | Verification screen + 403s always; an actual Windows lock screen **only** for the `TERMINATE` trigger | `decisions.outcome='WORKSTATION_LOCKED'` (TERMINATE path only) or `'CHALLENGE_REJECTED'`/`'CHALLENGE_EXPIRED'` (failed-reauth path); `GET /v1/enforcement/status` → `locked_out:true` in both cases |
| RECOVERY | legitimate user answers correctly | Posture → `NORMAL`; risk state (EWMA/K-of-N history) is **not** reset | Full access restored; may re-escalate quickly if genuine behavior right after recovery still resembles recent high-risk windows | `A2_REAUTH` anchor; `session.posture='NORMAL'`; watch next few `risk_events` rows |

**If you observe the legitimate user's own baseline (Phase 1/§7) independently walking this same ladder before the attacker ever sits down — expected, given Finding B, unless you've mitigated it — that is not a bug in the code. It is the pre-existing operating-point condition documented in §0.**

---

## 19. Abort Conditions

Stop the drill (do not continue collecting attacker data) if any of the following occur:

- **Enforcement unexpectedly reports disabled** (`GET /v1/enforcement/status` → `enforcement_enabled: false`) after you believed you'd enabled it — config didn't take effect, wrong `--enforcement-config` path, or the backend wasn't actually restarted.
- **Challenge credential reports `configured: false`** at any point — should be impossible given §2.5/§4, but if it happens mid-drill, every challenge dispatch will fail with `CHALLENGE_NOT_CONFIGURED` and the ladder has nothing to escalate with.
- **Protected routes remain accessible during `REAUTH_REQUIRED`/`LOCKED_OUT`** (§11's bypass check fails, i.e. returns `200` instead of `403`) — this is a security-model failure, not a data-quality issue; stop and investigate rather than continuing to collect data against a broken gate.
- **Attacker data appears to be entering a corpus/training path** — check `drill_sessions` still has the row, and that the drill label was actually passed (a backend started without `--drill-label` by mistake mid-drill would silently record the rest of the session as if it were genuine `manas-01` data, including the `A1_LOGIN_UNLOCK` anchor). If this happens, stop immediately — the corpus contamination risk is exactly what ADR-014/G12 exist to prevent, and it cannot be fixed after the fact by relabeling.
- **Database/audit logging fails** — `StorageError`/`503 STORAGE_UNAVAILABLE` responses, or the `audit/audit-YYYY-MM-DD.jsonl` file stops growing.
- **Timestamps cannot be trusted** — e.g. system clock changed mid-drill, or the machine slept/hibernated (which would also silently kill the collector's heartbeat and the WAL-mode DB's assumptions about monotonic capture time).
- **An unplanned backend restart occurs** (crash, Windows update, accidental Ctrl+C) — per §2.7/§12, this silently clears posture; treat the enforcement portion of that specific run as invalid from that point and restart the drill under a new `--drill-label`.
- **The system crashes** (backend process dies, collector hook installation fails, BSOD) — obviously stop; do not attempt to resume a native-collector session that lost Windows-wide hooks without restarting the collector cleanly.
- **Unexpected credential exposure** — the security-challenge answer is spoken aloud, typed on a shared/recorded screen, or otherwise becomes visible to the attacker. If this happens, the drill's central premise (attacker does not know the credential) is violated; stop, rotate the credential (§4.4) once posture is `NORMAL`, and restart with a new drill label.
- **The attacker asks to stop** — per the consent requirements in `attack-drill-protocol.md`, stop immediately, no exceptions.
- **The genuine-baseline concern in Finding B (§0) is observed live and unaddressed** — if, during Phase 1 (§7), the legitimate user's own normal work is already reaching `REAUTH_REQUIRED`/`LOCKED_OUT` before the attacker takes over, stop before handoff and re-decide whether to proceed; running the attacker segment on top of an already-escalated genuine baseline produces uninterpretable results, not evidence.

---

## 20. Post-Drill Procedure

1. **Stop the experiment:** `Ctrl+C` the collector first, then the backend (`startup.md`'s own shutdown order) — this closes the runtime, named pipe, database, and audit resources cleanly.
2. **Preserve evidence before touching anything else:** export §16's tables for the drill's `session_id` (and the immediately preceding genuine-baseline `session_id`) to local files before doing any cleanup.
3. **Record final timestamps:** drill end (last collector heartbeat / backend shutdown time), and the final posture at the moment of stopping.
4. **Export attacker data:** `feature_windows`/`scores`/`risk_events`/`decisions`/`alerts`/`verification_anchors` filtered to the drill `session_id`, via the read-only script pattern in §16.
5. **Export genuine baseline data:** same tables, filtered to the Phase 1 (§7) `session_id`(s).
6. **Calculate metrics:** per §14/§15 — descriptive statistics and timing latencies, explicitly not FAR/ROC/EER.
7. **Verify drill data did not enter training:** re-run `python tools/guardrails/check.py` (still expect `G01-G12` passing), and directly re-check `drill_sessions` contains the new row with the correct label; if you plan any future freeze, verify its manifest excludes this session's `window_id`s.
8. **Restore the system:** ensure posture is `NORMAL` (§13), stop the drill-labeled backend process, and if continuing genuine collection afterward, restart the backend **without** `--drill-label`.
9. **Recover the legitimate session:** per §13, before step 8 if the machine is still locked/blocked.
10. **Document anomalies:** any unplanned restart, collector disconnect, credential exposure, or abort-condition trigger (§19), with timestamps, in your own drill report — these are as important to the record as the successful path.

---

## 21. Results Table Template

Fill in per drill run:

| Field | Value |
|---|---|
| Drill label | |
| Date / operator | |
| Attacker (initials only, per consent) | |
| Genuine-baseline session_id | |
| Genuine-baseline window count / LOW / MEDIUM / HIGH split | |
| `t_attack_start` (wall clock) | |
| Drill session_id | |
| Attacker segment duration | |
| Attacker scored windows (total / by block A–G) | |
| Attacker fused_score: min / p25 / median / mean / p75 / max / stdev | |
| % attacker windows ≥0.80 / ≥0.90 (per-window, pre-smoothing) | |
| First MEDIUM at (timestamp / window #) | |
| First HIGH at | |
| First SOFT_CHALLENGE dispatched at / outcome | |
| First REAUTH_REQUIRED at / trigger | |
| REAUTH outcome (accepted/rejected/expired) | |
| LOCKED_OUT reached? at / trigger | |
| Windows workstation actually locked? (Y/N) | |
| Recovery timestamp / A2_REAUTH anchor id | |
| Detection latency: t_attack_start → first MEDIUM / HIGH / REAUTH_REQUIRED / LOCKED_OUT | |
| Bypass check result (§11) | |
| Anomalies / abort conditions triggered | |
| drill_sessions row confirmed post-drill | |

---

## 22. Final Sign-Off Checklist

- [ ] Git commit / working tree state matches §2.1, nothing stashed or reverted
- [ ] `medium_threshold=0.80` / `high_threshold=0.90` unchanged in `config/risk.development.yaml`
- [ ] Decision made and executed on Finding A (`enforcement.development.yaml: enabled`) — verified live via `/v1/enforcement/status`
- [ ] Decision made on Finding B (genuine baseline saturation) — documented, not silently ignored
- [ ] Challenge credential verified configured (§4.3); legitimate user confirms they know the answer
- [ ] `python -m pytest`, guardrails, codegen check all green same-day
- [ ] `dashboard/dist` rebuilt if any dashboard source changed
- [ ] System confirmed `ACTIVE`, `NORMAL` posture, no pending challenges, `drill_sessions` empty, immediately before attacker handoff
- [ ] Corpus frozen, manifest timestamp precedes today
- [ ] Drill isolation confirmed (`--drill-label` used, table row appears after start)
- [ ] Logging/audit confirmed active (`audit/` directory growing)
- [ ] Database writable (confirm via a successful decision/score write early in the run, e.g. the very first genuine window)
- [ ] Dashboard reachable and Settings tab checked
- [ ] Attacker briefed and consenting; does not know either credential
- [ ] Genuine baseline collected and recorded (§7)
- [ ] `t_attack_start` recorded
- [ ] Attacker-window target planned (~150–200, §5.1/§9) and blocks scheduled
- [ ] Recovery procedure rehearsed/understood by the legitimate user
- [ ] Commitment: no backend restart during the drill (collector restart is fine)
- [ ] Evidence export procedure (§16/§20) understood and ready to run immediately after stopping
