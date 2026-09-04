# Setup and Collection Guide

A step-by-step guide for running the continuous-authentication system on your own
Windows PC and collecting data.

**Time needed:** about 30–45 minutes the first time. After that, roughly one minute
each morning to start it.

---

## What your data is tagged as

A normal run now records **real participant data (`PILOT`)**. You do not need to pass
any special flag — it is the default.

This matters because the evaluation tooling only accepts `TEAM` or `PILOT` data and
permanently discards anything marked synthetic. Everything you collect by following
this guide counts.

Your data is written to `%LOCALAPPDATA%\ContinuousAuthentication\Pilot`, kept
completely separate from the old synthetic test data.

> One caveat, for Manas rather than participants: collected `PILOT` data is stored and
> is evaluation-eligible, but *training a model on it* additionally requires passing
> through an admission gate — the ADR-013 enrollment gate for a participant's first
> profile, the Model Update Manager promotion gate for every update after that. See
> the note at the end of this guide.

---

## What this system records (tell every participant)

Please be straight with anyone you ask to run this. It records:

- **How** you type and move the mouse — timing, rhythm, speed, distances.
- The **name of the app** in the foreground (e.g. `chrome.exe`), and its category.
- Health counters — how many events, whether anything was dropped.

It does **not** record:

- What you type. Not passwords, not messages, not anything. Keys are converted into
  content-free classes (like "letter" or "backspace") inside the capture callback and
  the actual key never leaves it.
- Window titles, document names, file paths, URLs, or clipboard contents.
- Screenshots or anything on screen.

Nobody can reconstruct what you wrote from this data. If someone is not comfortable,
they should not run it — this needs to be genuinely voluntary.

---

## Before you start

You need:

- **Windows 10 or 11.**
- Permission to install global keyboard/mouse hooks. Some corporate laptops and some
  antivirus software block this. If your machine blocks it, the collector will refuse
  to start and say so — you cannot work around it, and you should not try.
- About 2 GB of free disk space.

Then pick your path:

| Your situation | Use |
|---|---|
| You just want to run it and collect data | **Path A** — the packaged installer |
| You are a teammate who also needs the code | **Path B** — the full repository |

---

## Path A — Packaged installer (easiest)

No compiler, no Node.js, no Git needed.

### A1. Get the package

Manas builds it once, from the repository:

```powershell
.\deployment\windows\package.ps1 -Version v1
```

This produces `out/continuous-authentication-v1.zip` and a `.sha256` checksum file.
He sends you both.

### A2. Check the file arrived intact

```powershell
Get-FileHash .\continuous-authentication-v1.zip -Algorithm SHA256
```

Compare it to the contents of the `.sha256` file. If they differ, re-download; do not
install it.

### A3. Unzip and install

```powershell
Expand-Archive .\continuous-authentication-v1.zip -DestinationPath .\ca-package
Set-Location .\ca-package
.\install.ps1
```

This creates a private install under
`%LOCALAPPDATA%\ContinuousAuthentication\Application`, builds an isolated Python
environment, and installs everything offline from the bundled wheels.

If it says the destination already exists, you have installed before — either use the
existing install or delete that folder first.

### A4. Start it

```powershell
$env:CA_DASHBOARD_SECRET = Read-Host "Choose a dashboard password"
.\run-development.ps1 -ParticipantId your-pseudonym
```

- **Pick your pseudonym once and never change it.** Use something like `manas-01`,
  `joel-02`. All your data is grouped under this name. Changing it mid-week splits
  your data into two people and ruins it.
- The password is just for opening the dashboard on your own machine. Pick anything
  you will remember. You will type it again every time you start.

This starts the backend and the collector together. Leave the window open — closing it
stops collection.

Now skip to **"First run: set your security challenge."**

---

## Path B — Full repository (teammates)

You additionally need:

- **Git**
- **Python 3.11 or newer**
- **Node.js 22** and npm
- **CMake 3.20+**
- **Visual Studio Build Tools** with the "Desktop development with C++" workload

### B1. Get the code

```powershell
git clone <repository-url>
Set-Location manas_ml-pipeline
```

### B2. Python environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[backend,dev]"
```

### B3. Build the dashboard

```powershell
Set-Location dashboard
npm ci
npm run build
Set-Location ..
```

### B4. Build the collector

```powershell
cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector --config Release
ctest --test-dir build/collector -C Release --output-on-failure
```

If the tests pass, your machine can run the collector.

### B5. Check everything works

```powershell
python protocol/codegen/generate.py --check
python tools/guardrails/check.py
python -m pytest
```

All three should pass before you collect anything.

### B6. Start it (two terminals)

Create the folders and set your password once:

```powershell
$runtimeRoot = Join-Path $env:LOCALAPPDATA "ContinuousAuthentication\Pilot"
$artifactRoot = Join-Path $runtimeRoot "models"
New-Item -ItemType Directory -Force $artifactRoot | Out-Null
$env:CA_DASHBOARD_SECRET = Read-Host "Choose a dashboard password"
```

**Terminal 1 — backend (start this first):**

```powershell
.\.venv\Scripts\Activate.ps1
python -m backend.app.runtime.cli `
  --participant-id your-pseudonym `
  --artifact-root $artifactRoot `
  --dashboard-directory dashboard/dist
```

**Terminal 2 — collector:**

```powershell
.\build\collector\Release\continuous_auth_collector.exe `
  --config config/collector.development.yaml `
  --categories config/app_categories.yaml
```

Start the backend first — it creates the pipe the collector connects to. The collector
retries on its own if you get the order wrong.

---

## First run: set your security challenge

Open <http://127.0.0.1:8765> and sign in with the password you chose.

The very first time, you will see a **Security Challenge Setup** screen. It asks for:

- **Security Question** — anything only you would know the answer to
  (e.g. "What was my first pet's name?")
- **Security Answer**
- **Confirm Security Answer**

Both answer boxes are masked, like a password field.

**Why:** if the system later thinks someone else has taken over your machine, it pops
up a window asking this question. It is stored only on your own PC, and only as an
irreversible hash — nobody can read your answer back, not even from the database.

You only do this once. To change it later, go to **Settings** in the dashboard; you
will need your current answer.

> Note: the pop-up prompt and the screen-lock are **switched off by default**, so this
> will never interrupt you during normal collection. Manas turns it on only for the
> controlled demo.

---

## Your daily routine

**Every morning:**

1. Open PowerShell.
2. Start the system (Path A: `run-development.ps1`; Path B: the two terminals).
3. Sign in to the dashboard once to confirm it is alive.
4. Minimise everything and **use your computer completely normally.**

**Every evening:** press `Ctrl+C` in the terminal(s) to stop cleanly.

### What "normally" means

Genuinely normally. Do not perform, do not type carefully, do not avoid anything. The
whole point is to capture how you actually behave. Browsing, coding, writing, gaming,
messaging — all of it is useful.

### The one thing that really matters

**Run it on as many separate days as you can.** Not hours — *days*.

The evaluation trains on some days and tests on others, so it needs at least
**2 different calendar days** to work at all, and 4–5 is much better. Ten hours on one
single day is worth less than one hour on each of five days. You cannot make up missed
days later.

### Checking it is actually working

In the dashboard, open **System health**. You should see:

- Collector heartbeats arriving.
- Dropped events at zero.
- Activity appearing after you type or move the mouse for a while.

Do not worry if the risk score says `INSUFFICIENT_DATA` when you are idle. That is
correct behaviour, not a fault.

---

## Sending your data back

At the end of the collection period:

**1. Stop the system.** Press `Ctrl+C` in the terminal(s). Do not skip this — copying
the database while it is running can produce a corrupt file.

**2. Zip your data folder:**

```powershell
Compress-Archive `
  -Path "$env:LOCALAPPDATA\ContinuousAuthentication\Pilot\*" `
  -DestinationPath "$env:USERPROFILE\Desktop\ca-data-your-pseudonym.zip"
```

Replace `your-pseudonym` with the same name you have been using all week.

**3. Send it to Manas** — any normal file transfer is fine (Drive, WeTransfer, USB).

The zip has no typed content, no titles, no URLs and no screen data in it, so it is safe
to send this way. It is timing statistics and app names.

**4. Also tell him:**

- Your pseudonym.
- Roughly which days you collected on, and anything unusual ("laptop was in for repair
  Wednesday", "used an external keyboard Thursday").
- Whether you changed keyboard or mouse at any point — this genuinely affects the
  results and needs to be written down, not hidden.

> **Retention:** pilot data is kept for 120 days, so a normal collection round is in no
> danger of ageing out. Still send it promptly — it is only on your machine until you do.

---

## Troubleshooting

**"collector hook startup failed"**
Your antivirus or company policy is blocking global input hooks. Nothing in this guide
can bypass that, and you should not try. Tell Manas — your machine cannot be used for
collection.

**The dashboard will not open / login fails**
- Is the backend terminal still running?
- Are you using the same password you set *before* starting the backend?
- Is something else already using port 8765?

**The collector keeps reconnecting**
Start the backend first, then the collector. Both must run as the same Windows user.

**"production named-pipe ingestion requires Windows"**
You are trying to run the full pipeline on macOS, Linux, WSL, or in Docker. It only
works directly on Windows.

**The dashboard says protection is unavailable**
Expected until enough data exists to build your profile. It is being honest, not broken.

---

## Quick reference

| | |
|---|---|
| Dashboard | <http://127.0.0.1:8765> |
| Your data lives in | `%LOCALAPPDATA%\ContinuousAuthentication\Pilot` |
| Start (Path A) | `.\run-development.ps1 -ParticipantId your-pseudonym` |
| Stop | `Ctrl+C` in the terminal |
| Minimum useful | 2 different days |
| Good target | 4–5 different days |

If anything is confusing or breaks, message Manas rather than guessing — a machine set
up slightly wrong for five days is five days wasted.

---

## Note for Manas: how a profile gets trained

Collected `PILOT` data is stored correctly, is readable by the collection tooling, and
passes the `eligible_provenance: [TEAM, PILOT]` check, so freezing a corpus works.

Training a model on it goes through one of two reviewed boundaries — never neither,
never both (`ml/training/common.py` raises either way). Which one applies depends on
whether the participant already has an active profile:

- **First profile for a participant:** ADR-013 (accepted 2026-09-03) added a dedicated
  enrollment admission gate for exactly this case —
  `ml/training/enrollment.py::require_enrollment_admission`. Gate G3 of the promotion
  gate counts scored windows, which requires an existing model to score against, so a
  first profile could never satisfy it; the enrollment gate uses different evidence
  instead: eligible provenance, active consent, a recorded enrollment, membership of a
  checksum-verified freeze manifest, a recorded observation time per window, the
  configured minimum windows and distinct days, and — critically — no existing active
  profile for that participant. `python -m tools.enrollment activate` runs this end to
  end from the frozen corpus: it trains on the TRAIN partition, measures FRR on the
  held-out VALIDATION partition and FAR by zero-effort cross-evaluation against other
  participants, and activates the profile. The EVALUATION partition is never touched
  by this step — it stays reserved for the headline result.
- **Every update after that:** `ml/training/gate.py`'s six-gate promotion gate (G1-G6)
  is unchanged and still governs it. Every `TEAM`/`PILOT` segment must carry a
  promoted `UpdateCandidate` with all six gates passed; `python -m tools.updates run`
  performs one scheduled update cycle over the frozen corpus through that same gate.

`quarantine_days: 7` and `retraining_cadence_days: 7` in
`config/updates.development.yaml` are unchanged and are longer than a five-day
collection round, so a five-day round produces no live *promotion* by design — only
the first-profile enrollment above is available that early. In practice: do not
schedule the first update run earlier than **day 12** of a collection round, so that
every day collected has cleared the 7-day quarantine before that run executes.

Two things this does *not* resolve yet, so do not claim otherwise:

- **E1 (drift benefit) and E2 (poisoning resistance)** are implemented
  (`python -m tools.experiments e1|e2`) but have only been run against synthetic
  fixtures so far. Neither is evidence until run on the frozen eligible corpus with
  the evaluation freeze verified — both remain pending.
- **The runtime configuration is still development-only.** `config/updates.development.yaml`
  and the other five runtime loaders (`api`, `decisions`, `risk`, `risk` context,
  `runtime`) all require `development_only: true` and are not yet approved for pilot
  use. Only the storage and collection profiles have been reviewed. The synthetic path
  through the demo bootstrap script remains supported as an explicit opt-in for
  development — this does not replace it.
