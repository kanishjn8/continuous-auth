# Continuous Authentication Using Behavioral Biometrics

## Master Implementation Plan

**Institution:** Mukesh Patel School of Technology Management & Engineering, NMIMS — Computer Engineering Department, Mumbai Campus
**Academic Year:** 2026–2027
**Guide:** Miss Anuja Narvekar
**Document:** `PLAN.md`
**Plan Version:** v2.0 — Working Baseline (supersedes the `V1_plan` draft)

---

## Document Control

| Version | Date | Status | Summary of Change |
| --- | --- | --- | --- |
| v1.0 | — | Superseded | Initial rough draft. Eight-phase structure with per-member delegation. |
| v2.0 | Current | Active Baseline | Restructured to phase-wise delegation only (no per-member allocation). Adds: formal architectural decision records, privacy-by-construction data model, staged data-collection programme, system state machine, evaluation methodology with impostor taxonomy, threat model, and AI-agent execution model. |

**Status of this document:** This is the team's current working baseline. It is **not final**. Sections marked `[OPEN]` are deliberately unresolved and are to be closed by experiment, not by assumption. Architectural decisions are recorded as ADRs (Section 6) and may be revised through the change process in Section 20.

**Delegation notice:** This plan intentionally allocates work **by phase, not by person**. Individual task assignment within each phase will be decided separately by the team and is out of scope for this document.

---

## Table of Contents

1. Executive Summary
2. Problem Statement and Research Gap
3. Aim, Objectives and Scope
4. Core Architectural Principles
5. End-to-End System Architecture
6. Architectural Decision Records (ADRs)
7. Data Specification
8. Privacy by Construction
9. Data Collection Programme
10. Machine Learning Methodology
11. Risk Engine and Decision Layer
12. Model Update Manager
13. Evaluation and Validation Plan
14. Security and Threat Model
15. Technology Stack
16. Repository Structure
17. Phase-Wise Implementation Plan
18. Milestones
19. AI-Agent Execution Model
20. Risk Register and Change Management
21. Open Decisions
22. Glossary

---

# 1. Executive Summary

Authentication on desktop systems is a single event. A user proves identity once at login, and the system trusts that session until it is explicitly ended. This leaves a structural gap: an unattended unlocked workstation, or a hijacked active session, is indistinguishable from a legitimate one.

This project builds a **lightweight, OS-native continuous authentication system** that runs quietly in the background and repeatedly verifies whether the person currently operating the machine still behaves like the person who logged in. Verification is based on **how** the user types and moves the mouse — never on **what** they type.

The system is deliberately **system-wide and application-agnostic**. It observes input at the operating-system level and continues to function regardless of which application holds focus. Application context is consumed as a *supporting signal* that modulates confidence in the risk score, never as a substitute for the behavioral identity model and never as a reason to disable monitoring.

Identity modelling is **per-user and independent**. Each enrolled person has their own anomaly-detection model trained only on their own genuine behavior. There is no shared multi-class classifier attempting to identify "who is this among N people."

The contribution is not a demonstration that behavior can identify a person — that is established in the literature. The contribution is a **machine-wide continuous authentication pipeline that is evaluated under live conditions, with measured runtime cost, against both zero-effort and informed impostors, with a security-gated adaptation mechanism.**

---

# 2. Problem Statement and Research Gap

## 2.1 Problem Statement

Existing authentication verifies identity at a single point in time and then assumes continuity. Session hijacking, unattended workstations, and insider misuse all exploit that assumption. Behavioral biometrics offer a way to close the gap, but current systems fall short in three consistent ways:

1. **Scope.** Most systems observe a single application and lose the subject the moment window focus changes. Authentication that stops at the boundary of one program does not protect a workstation.
2. **Deployment cost.** Published work rarely reports what it actually costs to run continuously on a real machine — CPU, memory, latency, event throughput. A method that is accurate but imposes noticeable overhead will not be deployed.
3. **Evaluation realism.** Most evaluation occurs offline, on controlled or scripted data, against zero-effort impostors only. This does not establish that the system works live, or that it resists an attacker who has observed the legitimate user.

## 2.2 Research Gap Addressed

Derived from the project literature review:

| Gap Identified in Literature | How This Project Addresses It |
| --- | --- |
| Limited behavioral modalities; single-modality systems | Fused keystroke **and** mouse dynamics with per-modality availability handling |
| Real-world deployment remains limited; evaluated offline | Live end-to-end deployment on real machines during natural daily work |
| High computational cost; no reported runtime cost | Explicit runtime-cost measurement as a first-class result (Phase 10) |
| Long-term behavioral drift degrades accuracy | Security-gated Model Update Manager, with drift measured across the collection period |
| Evaluated only against basic impostors; no mimicry testing | Explicit impostor taxonomy including an informed-impostor experiment |
| No standard evaluation framework | Documented, reproducible evaluation protocol with day-disjoint splits |
| Application-bound systems that lose the user on focus switch | OS-level collector that is application-aware but not application-specific |

## 2.3 What This Project Is Not

To keep scope defensible, the following are explicitly **out of scope**:

- It is **not** a user-identification system ("who among N people is this?"). It is a verification system ("is the current operator still the enrolled user?").
- It is **not** a replacement for primary authentication. It is a continuous assurance layer above it.
- It does **not** perform content inspection, screen capture, or application-widget introspection.
- It does **not** use time-of-day or day-of-week as an authentication signal. (Explicit team decision — see ADR-010.)

---

# 3. Aim, Objectives and Scope

## 3.1 Aim

To build and rigorously evaluate a lightweight, OS-native continuous authentication system that verifies users in real time through keyboard and mouse behavior, detecting unauthorized session takeover without imposing meaningful cost on system performance.

## 3.2 Objectives

1. Capture keyboard and mouse behavior through a background, OS-level native event collector that operates system-wide.
2. Transform raw events into content-free statistical feature windows.
3. Perform continuous verification using independent per-user anomaly-detection models.
4. Convert model output into a graded, temporally smoothed, context-aware risk assessment.
5. Respond proportionally: continue, soft challenge, forced reauthentication, or session termination.
6. Adapt user profiles to genuine behavioral drift without allowing an attacker to contaminate them.
7. Provide real-time monitoring, alerting, and auditable logs.
8. Evaluate the system against genuine users and multiple classes of impostor, reporting FAR, FRR, EER, detection latency, and runtime cost.

## 3.3 In Scope

- Desktop/laptop operating systems (primary target confirmed in ADR-001).
- Keystroke dynamics, mouse dynamics, and generically-obtainable OS/application context.
- Local-first deployment: collector, backend, models, storage, and dashboard on the monitored machine.
- A pilot study on a role-homogeneous cohort for evaluation purposes.

## 3.4 Out of Scope

- Mobile and touch modalities.
- Network-level or cloud-hosted multi-endpoint deployment.
- Application-specific models, plugins, or GUI/widget-level interaction analysis.
- Enterprise directory integration (AD/LDAP/SSO).
- Production-grade hardening of the dashboard as a security console (see Section 14.4).

---

# 4. Core Architectural Principles

These are **non-negotiable constraints**. Any proposed change that violates one of these requires a major plan revision (Section 20), not an in-phase decision. They are also encoded as machine-readable guardrails for automated development (Section 19.3).

| # | Principle | Implication |
| --- | --- | --- |
| P1 | **OS-wide, not application-bound** | The collector hooks input at OS level. It must not depend on any application's API, accessibility tree, widget hierarchy, or DOM. |
| P2 | **Application-aware, not application-specific** | Foreground application/category may be used as a *risk confidence modifier* only. No per-application authentication models. No application-specific integrations. |
| P3 | **Per-user independent models** | One profile per enrolled user, trained only on that user's own data. Enrolling a new user must never require retraining any existing user's model. |
| P4 | **Content-free by construction** | Typed content must be *impossible* to reconstruct from persisted data or the IPC stream — enforced structurally, not by policy. See Section 8. |
| P5 | **Low runtime overhead** | Runtime cost is a measured deliverable, not an afterthought. Budget defined in Section 13.4. |
| P6 | **Graded, smoothed response** | No single anomalous window may trigger a punitive action. Escalation requires sustained evidence. |
| P7 | **Adaptation must be earned** | Model updates only from independently verified, low-risk, quarantined data. Never automatic learning from arbitrary sessions. |
| P8 | **Never disable authentication** | High-variance activity reduces *confidence in the score*; it never switches monitoring off. |
| P9 | **Evaluation over assertion** | Every performance claim must be backed by a measured, reproducible experiment on day-disjoint data. |
| P10 | **No temporal context as identity** | Time-of-day and day-of-week are excluded from the authentication signal by decision. |

---

# 5. End-to-End System Architecture

## 5.1 Pipeline Overview

The canonical pipeline is unchanged from the project reference:

```
OS Events → Feature Extraction → Per-User Model → Risk Engine → Decision
```

The detailed realisation of that pipeline is below.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          MONITORED MACHINE                               │
│                                                                          │
│   Physical Keyboard / Mouse Input                                        │
│              │                                                           │
│              ▼                                                           │
│   ┌──────────────────────────────────────────┐                           │
│   │  NATIVE OS COLLECTOR            (C/C++)  │                           │
│   │  ─────────────────────────────────────   │                           │
│   │  • Global input hook (libuiohook)        │                           │
│   │  • MONOTONIC capture-time timestamping   │  ◄── ADR-003              │
│   │  • Key → KEY-CLASS tokenisation          │  ◄── ADR-004 (privacy)    │
│   │  • Foreground app + category resolution  │                           │
│   │  • Input device class detection          │  ◄── ADR-009              │
│   │  • Heartbeat emitter (tamper detection)  │                           │
│   │  • Bounded ring buffer + backpressure    │                           │
│   └────────────────────┬─────────────────────┘                           │
│                        │                                                 │
│                        ▼                                                 │
│   ┌──────────────────────────────────────────┐                           │
│   │  IPC TRANSPORT                           │  ◄── ADR-002              │
│   │  Named pipe / UDS, length-prefixed       │                           │
│   │  binary frames. Shared-memory ring       │                           │
│   │  buffer only if profiling demands it.    │                           │
│   └────────────────────┬─────────────────────┘                           │
│                        │                                                 │
│  ══════════════════════╪═══════════════ process boundary ═══════════════ │
│                        ▼                                                 │
│   ┌──────────────────────────────────────────┐                           │
│   │  INGESTION SERVICE          (Python)     │                           │
│   │  • Frame decode + schema validation      │                           │
│   │  • Ordering / gap / duplicate detection  │                           │
│   │  • Session + segment attribution         │  ◄── ADR-007              │
│   │  • Raw event ring (in-memory only)       │                           │
│   └────────────────────┬─────────────────────┘                           │
│                        ▼                                                 │
│   ┌──────────────────────────────────────────┐                           │
│   │  FEATURE EXTRACTION                      │                           │
│   │  • Windowing: 30 s OR 100 keystrokes     │                           │
│   │  • Quality gate per modality             │  ◄── ADR-005              │
│   │  • Keyboard feature block                │                           │
│   │  • Mouse feature block                   │                           │
│   │  • Context block (NOT identity features) │                           │
│   │  • Per-user baseline normalisation       │                           │
│   └────────────────────┬─────────────────────┘                           │
│                        ▼                                                 │
│   ┌──────────────────────────────────────────┐                           │
│   │  PER-USER PROFILE                        │  ◄── ADR-006              │
│   │  ┌────────────────┐  ┌────────────────┐  │                           │
│   │  │ Keyboard model │  │  Mouse model   │  │                           │
│   │  │ IsolationForest│  │ IsolationForest│  │                           │
│   │  └───────┬────────┘  └───────┬────────┘  │                           │
│   │          │ s_kbd             │ s_mouse   │                           │
│   │          └────────┬──────────┘           │                           │
│   │        Per-user score calibration        │                           │
│   │        (percentile vs own baseline)      │                           │
│   └────────────────────┬─────────────────────┘                           │
│                        ▼                                                 │
│   ┌──────────────────────────────────────────┐                           │
│   │  RISK ENGINE                             │                           │
│   │  • Availability-weighted score fusion    │                           │
│   │  • Context confidence modifier           │  ◄── ADR-008              │
│   │  • Temporal smoothing (EWMA + K-of-N)    │                           │
│   │  • Hysteresis + cooldown                 │                           │
│   │  • State machine gating                  │  ◄── ADR-005              │
│   └────────────────────┬─────────────────────┘                           │
│                        ▼                                                 │
│   ┌──────────────────────────────────────────┐                           │
│   │  DECISION LAYER                          │                           │
│   │  CONTINUE │ SOFT_CHALLENGE │ REAUTH │    │                           │
│   │  TERMINATE                               │                           │
│   │  Fail-open policy on component failure   │  ◄── ADR-011              │
│   └────┬─────────────────────────┬───────────┘                           │
│        │                         │                                       │
│        ▼                         ▼                                       │
│  ┌──────────────┐        ┌──────────────────┐                            │
│  │ MODEL UPDATE │        │  PERSISTENCE     │                            │
│  │ MANAGER      │◄───────┤  SQLite (WAL)    │                            │
│  │ • verify     │        │  JSONL audit log │                            │
│  │ • quarantine │        │  Retention policy│                            │
│  │ • promote    │        └────────┬─────────┘                            │
│  └──────────────┘                 │                                      │
│                                   ▼                                      │
│                        ┌──────────────────────┐                          │
│                        │  FastAPI + WebSocket │                          │
│                        └──────────┬───────────┘                          │
│                                   ▼                                      │
│                        ┌──────────────────────┐                          │
│                        │  REACT DASHBOARD     │                          │
│                        │  Risk / Alerts / Logs│                          │
│                        └──────────────────────┘                          │
└──────────────────────────────────────────────────────────────────────────┘
```

## 5.2 Component Responsibilities

| Component | Responsibility | Must Not |
| --- | --- | --- |
| Native Collector | Hook global input, timestamp at capture, tokenise keys to classes, resolve foreground app, emit heartbeat | Persist anything to disk; transmit raw keycodes; read window contents |
| IPC Transport | Deliver ordered, framed events across the process boundary with bounded buffering and backpressure | Reorder or re-timestamp events |
| Ingestion Service | Validate, order, attribute to session/segment, buffer in memory | Persist raw per-event streams beyond the debug retention window |
| Feature Extraction | Window, quality-gate, compute content-free statistics, normalise | Emit any ordered key sequence |
| Per-User Profile | Score a window against that user's own baseline | Use any other user's data at inference or training time |
| Risk Engine | Fuse, contextualise, smooth, classify risk level | Use raw uncalibrated model scores directly for decisions |
| Decision Layer | Apply escalation ladder with hysteresis and cooldown | React punitively to a single window |
| Model Update Manager | Gate, quarantine, and promote trusted data; retrain on schedule | Learn from unverified or elevated-risk data |
| Persistence | Store features, scores, decisions, model metadata, audit trail | Store content, screenshots, or raw ordered keystrokes |
| API/WebSocket | Serve state and stream live updates | Expose raw feature vectors without authentication |
| Dashboard | Visualise risk, alerts, history, system health | Be the only enforcement path |

## 5.3 System State Machine

The system operates one state machine **per enrolled user**. This resolves the cold-start problem, which the v1 draft did not address.

```
        ┌──────────────┐
        │   ENROLLING  │  Collecting only. No scoring decisions.
        │              │  No punitive action possible.
        └──────┬───────┘
               │ min_windows reached AND min_distinct_days reached
               ▼
        ┌──────────────┐
        │ CALIBRATING  │  Model trained. Scoring runs in SHADOW MODE.
        │              │  Decisions computed and logged, NOT enforced.
        └──────┬───────┘  Thresholds derived from own-score distribution.
               │ calibration window complete AND FRR within budget
               ▼
        ┌──────────────┐
        │    ACTIVE    │  Full enforcement of the escalation ladder.
        └──┬────────┬──┘
           │        │
           │        │ component failure / model unavailable /
           │        │ sustained INSUFFICIENT_DATA
           │        ▼
           │  ┌──────────────┐
           │  │   DEGRADED   │  Fail-open. Monitoring continues,
           │  │              │  enforcement suspended, HIGH-severity
           │  └──────┬───────┘  availability alert raised.
           │         │ recovery
           │◄────────┘
           │
           │ user unenrolled / profile invalidated
           ▼
        ┌──────────────┐
        │  SUSPENDED   │
        └──────────────┘
```

**Rationale.** Isolation Forest scores are meaningless until a stable baseline exists. Enforcing decisions during enrollment would produce a high false-rejection rate that looks like a broken system even when the pipeline is correct. `CALIBRATING` additionally provides a shadow-mode period in which threshold selection happens against real data with zero user impact.

`[OPEN]` `min_windows`, `min_distinct_days`, and calibration duration are to be determined empirically in Phase 4 (Section 10.5), not fixed here.

---

# 6. Architectural Decision Records (ADRs)

Each ADR records a decision that the v1 draft either left implicit or did not address. Decisions marked **Provisional** are expected to be confirmed or revised by experiment.

---

### ADR-001 — Target Operating System

**Status:** Provisional — confirm in Phase 0 spike.

**Decision:** **Windows** is the primary target platform. Linux/X11 is a secondary target if effort permits. macOS and Wayland are explicitly out of scope for the initial build.

**Rationale:** Global input hooking behaves very differently across platforms. Wayland deliberately restricts global input capture as a security design goal, and macOS requires an explicit Accessibility permission grant per binary. Windows offers the most reliable global hook surface, and it matches the intended pilot cohort (enterprise data-analysis workflows on Excel/Power BI).

**Trade-off:** Reduces the "cross-platform" claim. This is acceptable and should be stated honestly as a scope boundary rather than overclaimed. The collector is nonetheless structured behind a platform abstraction interface so a second backend can be added without touching downstream layers.

**Consequence:** Phase 0 must include a hard feasibility spike (Section 17, Phase 0) that proves global capture works on the actual target before Phase 1 commits three weeks to it.

---

### ADR-002 — IPC Mechanism

**Status:** Provisional.

**Decision:** Start with **length-prefixed binary frames over a named pipe (Windows) / Unix domain socket (Linux)**. Adopt a shared-memory ring buffer only if measurement shows the transport is a bottleneck.

**Rationale:** The reference architecture lists "asynchronous queues and shared memory." Shared memory is the higher-performance option but is substantially harder to implement correctly, harder to debug, and harder to test. Realistic human input rates (order 10–100 events/second) are far below the point where socket/pipe transport becomes a constraint. Starting simple removes a large schedule risk from the critical path.

**Trade-off:** If the throughput budget is later exceeded, a transport swap is required — but the transport is isolated behind an interface, so the change is contained to one module.

**Consequence:** The IPC layer must be defined as a swappable interface from day one, and a throughput benchmark must be part of Phase 1's exit criteria so the decision is data-driven.

---

### ADR-003 — Timestamp Authority

**Status:** **Firm. Non-negotiable.**

**Decision:** Every event timestamp is assigned **inside the native collector's hook callback, at the moment of capture**, using a **monotonic** high-resolution clock:

- Windows: `QueryPerformanceCounter`
- Linux: `clock_gettime(CLOCK_MONOTONIC_RAW)`

Resolution: microseconds. Each session additionally records one wall-clock anchor at start, for correlation only.

**No downstream component may re-timestamp an event or derive timing features from arrival time.**

**Rationale:** Every discriminative feature in this project — dwell time, flight time, digraph latency, mouse velocity — is a *timing* feature. If timestamps are assigned when an event arrives at the Python backend, then IPC queuing delay, scheduler jitter, and GC pauses are baked directly into the biometric signal as noise. This failure mode does not crash anything and produces no error. It silently degrades data quality for the entire collection period and only becomes visible as unexplainably poor FAR/FRR at evaluation time, when there is no schedule left to fix it.

A monotonic clock is mandated rather than wall-clock because NTP correction can step the system clock backwards, producing negative or corrupted intervals.

**Consequence:** Phase 1 must include an explicit **timestamp fidelity test**: inject a synthetic event stream with known inter-event intervals and assert that the intervals reconstructed at the feature layer match within a defined tolerance. This test is a gating exit criterion.

---

### ADR-004 — Content-Free Key Representation

**Status:** **Firm. Non-negotiable.**

**Decision:** The collector **never transmits or persists key identity**. It transmits a **key-class token** plus timing. Raw keycodes exist only transiently inside the hook callback and are discarded immediately after classification.

Key classes (initial taxonomy):

| Class | Description |
| --- | --- |
| `ALPHA_L_HOME` / `ALPHA_L_UPPER` / `ALPHA_L_LOWER` | Left-hand alphabetic keys by keyboard row |
| `ALPHA_R_HOME` / `ALPHA_R_UPPER` / `ALPHA_R_LOWER` | Right-hand alphabetic keys by keyboard row |
| `DIGIT` | Number row / numpad |
| `PUNCT` | Punctuation group |
| `SPACE` | Spacebar |
| `BACKSPACE` / `DELETE` | Correction keys |
| `ENTER` | Enter/Return |
| `MODIFIER` | Shift, Ctrl, Alt, Meta |
| `NAVIGATION` | Arrows, Home, End, PgUp/PgDn, Tab |
| `FUNCTION` | F-keys |
| `OTHER` | Anything unclassified |

**Rationale — and why this is an architectural problem, not a policy problem.** The project requires digraph/trigraph timing features, which conventionally require knowing *which* key followed which. But storing ordered keycode sequences **is** storing typed content — the privacy guarantee would then rest purely on a promise not to look, which is not a guarantee at all.

Class-based transitions resolve this. Features such as *cross-hand transition latency*, *same-row transition latency*, and *home-row dwell ratio* preserve most of the discriminative motor-timing structure of digraph analysis, while making text reconstruction structurally impossible: an 11-way class token carries far too little information to recover language.

**Trade-off:** Some discriminative power is lost relative to true keycode-level digraph analysis. This is an accepted cost, and the resulting privacy property is *provable by inspection of the IPC schema* rather than asserted. This is a defensible strength in the report, not merely a limitation.

**Consequence:** A test must assert that no field in the IPC schema or database schema can carry a keycode. This is encoded as an automated guardrail (Section 19.3).

---

### ADR-005 — Window Quality Gating and Modality Availability

**Status:** Firm.

**Decision:** A feature window is scored per modality only if it meets a minimum evidence threshold. Windows are labelled:

| Label | Condition | Handling |
| --- | --- | --- |
| `FULL` | Both keyboard and mouse thresholds met | Fused score |
| `KBD_ONLY` | Keyboard threshold met, mouse not | Keyboard score only, fusion weight adjusted |
| `MOUSE_ONLY` | Mouse threshold met, keyboard not | Mouse score only, fusion weight adjusted |
| `INSUFFICIENT_DATA` | Neither threshold met | **No score produced.** Risk state holds; does not decay toward either extreme. |

**Rationale:** Without this gate, a window in which the user simply paused (a phone call, reading a document, a meeting) contains near-zero events. Fed into a model as a feature vector of zeros or NaN-imputed values, that window looks maximally anomalous — the system would flag idleness as an intruder. The v1 draft tested "user stops interacting temporarily" as an integration case but never defined the intended behavior.

**Consequence:** `INSUFFICIENT_DATA` windows are logged and counted (they are useful diagnostics) but never contribute to risk escalation, and never enter training data.

`[OPEN]` Exact thresholds (`min_keystrokes`, `min_mouse_samples`) to be set in Phase 2 from observed distributions.

---

### ADR-006 — Feature Fusion Strategy

**Status:** Provisional — confirm by ablation in Phase 4.

**Decision:** Each user's profile contains **two Isolation Forest models — one keyboard, one mouse — fused at the score level** by the risk engine, rather than a single model over one concatenated feature vector.

The per-user profile remains a single logical entity (`Profile(user_id) = {kbd_model, mouse_model, calibration, metadata}`), so the canonical pipeline stage "Per-User Model" is preserved.

**Rationale:** The v1 draft never specified this, yet Phase 2 and Phase 4 both silently assume an answer. The decisive factor is the missing-modality problem from ADR-005: with a single fused vector, a keyboard-only window forces you to impute mouse features, and an Isolation Forest will frequently read imputed values as anomalous — flagging the *absence* of a modality rather than any unusual *behavior*. With separate models, an unavailable modality is simply not scored, which is both cleaner and more honest.

Secondary benefits:
- Enables reporting keyboard-only, mouse-only, and fused performance separately — a **fusion ablation**, which is a genuine and easily-defended research result rather than a single opaque number.
- Allows the keystroke-only public dataset (Section 9.1) to validate the keyboard model directly.
- Keeps each model's input space homogeneous in scale and semantics.

**Trade-off:** Loses cross-modal features that only exist in a joint representation — for example, the characteristic latency when a user transitions from typing to reaching for the mouse. **Mitigation:** a third small feature group, *interaction dynamics* (typing↔mouse transition latency, modality alternation rate), may be added later as a joint block if Phase 4 ablation shows value. This is a Phase 7+ stretch item, not baseline.

**Confirmation criterion:** Phase 4 must run a direct comparison — fused-vector single model vs dual-model score fusion — on the same day-disjoint split, and record the result. If the single fused model wins decisively *and* handles missing modality acceptably, this ADR is revised.

---

### ADR-007 — Session and Segment Definition

**Status:** Firm.

**Decision:**

- A **session** begins at an authenticated entry event (login, unlock, explicit session start) and ends at logout, lock, or shutdown.
- A session is divided into **segments**. A segment ends when idle time exceeds `IDLE_SPLIT_THRESHOLD` (initial value: 15 minutes with zero input events) and a new segment opens on resumption.
- Model-update eligibility is evaluated at **segment** granularity, not session granularity.

**Rationale:** The Model Update Manager's core gate is "the session stayed low-risk throughout." That is meaningless without a definition of "session." An eight-hour workday containing a two-hour lunch break during which the machine sat unlocked and unattended is exactly the window an attacker would exploit; treating it as one trusted unit would defeat the gate. Segmenting on idle boundaries confines each trusted unit to a period of continuous observed presence.

**Consequence:** `session_id` and `segment_id` are mandatory fields on every persisted feature window and decision record.

---

### ADR-008 — Context Confidence: Declared Map plus Empirical Variance

**Status:** Provisional.

**Decision:** Context confidence is computed in two layers:

1. **Bootstrap layer (Phase 7 baseline):** a declared application-category map held in **configuration, not code** (`config/app_categories.yaml`), mapping process names to categories such as `PRODUCTIVITY`, `BROWSING`, `DEVELOPMENT`, `CREATIVE`, `GAMING`, `SYSTEM`, `UNKNOWN`.
2. **Empirical layer (preferred, once data allows):** for each user and each category, compute the **observed variance of that user's own genuine risk scores** while in that category during enrollment. Confidence weight is derived from that measured variance.

The empirical layer supersedes the bootstrap layer for any (user, category) pair with sufficient observations. `UNKNOWN` applications default to the neutral bootstrap weight.

**Rationale:** A hardcoded "games and Photoshop are noisy" list needs perpetual maintenance, does not generalise to unseen applications, and is a weaker claim to defend as *generic architecture*. Deriving variance from the user's own history is self-maintaining and per-user correct — some people are genuinely more consistent in creative tools than others.

**Trade-off:** The empirical layer requires enough per-category data per user, which small pilots may not supply for rare categories. Hence the two-layer design with graceful fallback.

**Constraint (P8):** Context modulates *confidence in the score*. It must never be able to reduce enforcement to zero, and there must be no application for which monitoring stops. A floor on effective confidence is mandatory, so that opening a "noisy" application can never be used as an evasion technique.

---

### ADR-009 — Input Device Metadata

**Status:** Firm.

**Decision:** Every event carries an `input_device_class` field (e.g. `INTERNAL_KEYBOARD`, `EXTERNAL_KEYBOARD`, `TRACKPAD`, `EXTERNAL_MOUSE`, `UNKNOWN`). Mouse spatial features are normalised by screen resolution and DPI where obtainable. Pilot participants are asked to maintain a consistent primary input setup; device changes are logged and surfaced in analysis.

**Rationale:** Switching from a trackpad to an external mouse changes velocity, acceleration, and curvature distributions dramatically — a difference that has nothing to do with identity. Without device metadata, the model cannot distinguish "different device" from "different person," and this confound would silently inflate FRR and corrupt reported results.

**Consequence:** Evaluation must report whether device changes occurred within the dataset, and if so, analyse their effect. If time permits, a per-device-class sub-profile is a reasonable future extension — noted, not baseline.

---

### ADR-010 — Exclusion of Temporal Context

**Status:** Firm (existing team decision, recorded here for completeness).

**Decision:** Time-of-day and day-of-week are **not** used as authentication signals or model features.

**Rationale:** Team decision. Such features tend to encode schedule rather than identity, penalise legitimate schedule variation, and are trivially observable by an attacker.

**Consequence:** Timestamps are retained for sequencing, session attribution, and audit only. Any feature derived from absolute wall-clock time is prohibited by guardrail.

---

### ADR-011 — Failure Policy: Fail-Open with Loud Alerting

**Status:** Firm.

**Decision:** On infrastructure failure — backend unreachable, model missing or corrupt, feature pipeline exception, collector crash — the system **fails open** at the authentication layer: the user is not challenged or logged out. Simultaneously it raises a **HIGH-severity availability alert**, transitions the user to `DEGRADED`, and writes an audit record.

**Exception — tamper signal:** if the collector heartbeat stops while the session remains active (indicating the collector was deliberately terminated), this is treated as a **security event**, not merely an availability event: risk is elevated and an alert is raised. It still does not force termination by default, but the policy is configurable.

**Rationale:** The v1 draft listed "backend unavailable" and "model unavailable" as integration tests without ever stating the intended behavior — meaning the actual behavior would have been whatever the exception handler happened to do. Continuous authentication here is an *assurance and detection* layer above primary authentication, not the primary access-control mechanism. Failing closed would convert an ordinary software bug into a denial of service against a legitimate user, which is both worse for the user and worse for the demo. Failing open is standard practice for this class of system, but it must be **loud** — a silent fail-open is a genuine security hole.

**Consequence:** Availability alerts must be visually distinct from behavioral risk alerts on the dashboard. "System is not currently protecting you" must never look like "everything is fine."

---

### ADR-012 — Log Retention and Rotation

**Status:** Firm.

**Decision:** JSONL audit logs rotate daily and are compressed after `N` days. SQLite runs in WAL mode with a configured retention window for feature windows and score records. Raw event debug capture is **off by default**, time-boxed when enabled, and never enabled during pilot collection.

**Rationale:** Continuous logging across weeks of a 16-week project produces unbounded growth, which contradicts the "lightweight" objective and can itself become a performance problem.

---

# 7. Data Specification

## 7.1 What Is and Is Not Collected

| Collected | Never Collected |
| --- | --- |
| Key press/release timestamps (monotonic, µs) | Keycodes, characters, words, or any typed content |
| Key **class** tokens (ADR-004) | Passwords or credentials |
| Mouse coordinates, movement, velocity, acceleration | Screenshots or screen contents |
| Click press/release timing, double-click intervals | Clipboard contents |
| Scroll events and magnitudes | File paths, URLs, document titles |
| Foreground process name and derived category | Application widget/control state |
| Input device class, screen resolution/DPI | Network traffic |
| Session/segment identifiers | Absolute time-of-day as a feature (ADR-010) |

## 7.2 IPC Event Schema

Canonical schema, defined once in `protocol/` and used to generate both C++ structs and Python models. This is the **single source of truth** for the collector/backend contract.

```jsonc
// KeyboardEvent
{
  "type": "KEY_DOWN" | "KEY_UP",
  "t_capture_us": 1234567890123,   // monotonic, assigned in hook callback (ADR-003)
  "key_class": "ALPHA_L_HOME",     // ADR-004 — never a keycode
  "is_repeat": false,
  "device_class": "EXTERNAL_KEYBOARD",
  "app_id": 17,                    // index into app registry, not a title string
  "seq": 884213                    // monotonic sequence for gap/duplicate detection
}

// MouseEvent
{
  "type": "MOVE" | "BUTTON_DOWN" | "BUTTON_UP" | "SCROLL",
  "t_capture_us": 1234567890456,
  "x": 1042, "y": 733,             // raw; normalised downstream by resolution/DPI
  "button": "LEFT" | "RIGHT" | "MIDDLE" | null,
  "scroll_dx": 0, "scroll_dy": -3,
  "device_class": "EXTERNAL_MOUSE",
  "app_id": 17,
  "seq": 884214
}

// ContextEvent
{
  "type": "APP_FOCUS_CHANGE",
  "t_capture_us": 1234567891000,
  "app_id": 22,
  "category": "PRODUCTIVITY",
  "seq": 884215
}

// Heartbeat
{
  "type": "HEARTBEAT",
  "t_capture_us": 1234567892000,
  "collector_uptime_ms": 84213000,
  "dropped_events": 0,
  "buffer_high_water": 128,
  "seq": 884216
}
```

**Schema invariant (guardrail-enforced):** no field of any event type may carry a character, keycode, string of user-generated text, or window title.

## 7.3 Feature Window Schema

Windows close on **whichever comes first**: 30 seconds elapsed, or 100 keystrokes. Both are configurable.

### Keyboard feature block

| Feature | Description |
| --- | --- |
| `dwell_mean`, `dwell_std`, `dwell_median`, `dwell_p90` | Key hold duration statistics |
| `flight_mean`, `flight_std` | Key-up → next key-down interval |
| `dd_latency_mean`, `dd_latency_std` | Key-down → next key-down interval |
| `typing_rate` | Keystrokes per second over active portion of window |
| `burst_rate`, `burst_mean_length` | Rapid-sequence typing bursts |
| `pause_ratio` | Fraction of inter-key gaps exceeding a pause threshold |
| `iki_cv` | Coefficient of variation of inter-key intervals (rhythm regularity) |
| `backspace_ratio` | Correction keys as a fraction of total keys |
| `correction_burst_rate` | Frequency of consecutive-correction runs |
| `modifier_ratio` | Modifier key usage rate |
| `rollover_ratio` | Fraction of overlapping key presses (next down before previous up) |
| `xhand_latency_mean/std` | Cross-hand class-transition latency (ADR-004 digraph surrogate) |
| `samehand_latency_mean/std` | Same-hand class-transition latency |
| `samerow_latency_mean/std` | Same-row class-transition latency |
| `homerow_dwell_ratio` | Home-row dwell relative to overall dwell |

### Mouse feature block

| Feature | Description |
| --- | --- |
| `velocity_mean/std/max` | Movement speed statistics (resolution-normalised) |
| `accel_mean/std` | Acceleration statistics |
| `jerk_mean` | Rate of change of acceleration |
| `straightness_ratio` | Direct distance ÷ actual path length |
| `curvature_mean/std` | Path curvature |
| `direction_change_rate` | Direction reversals per unit distance |
| `angular_velocity_mean` | Mean angular velocity along path |
| `segment_duration_mean/std` | Duration of continuous movement segments |
| `pause_count_per_segment` | Micro-pauses within a movement |
| `click_duration_mean/std` | Button press → release duration |
| `double_click_interval_mean/std` | Inter-click interval for double clicks |
| `click_rate` | Clicks per minute |
| `move_to_click_ratio` | Movement events per click |
| `scroll_rate`, `scroll_burst_mean` | Scroll behavior |

### Context block — **supporting signal only, not identity features**

| Field | Description |
| --- | --- |
| `dominant_category` | Category holding focus for the largest share of the window |
| `category_fractions` | Time fraction per category within the window |
| `app_switch_rate` | Focus changes per minute |
| `device_class` | Active input device class |

**Critical constraint:** context features are **not** inputs to the Isolation Forest identity models. They are consumed only by the risk engine (ADR-008). Feeding them into the identity model would teach the model "which applications this person uses" as a proxy for identity — which breaks P2, generalises poorly, and would be trivially spoofable.

### Window metadata

`user_id`, `session_id`, `segment_id`, `window_id`, `t_start_us`, `t_end_us`, `quality_label` (ADR-005), `key_event_count`, `mouse_event_count`, `collection_day` (date only, for day-disjoint splitting — **not** a feature).

## 7.4 Storage Schema (SQLite)

| Table | Purpose |
| --- | --- |
| `users` | Enrolled user registry, current state (ADR-005 state machine) |
| `sessions` | Session records with entry-authentication evidence |
| `segments` | Segment records with idle-split boundaries |
| `feature_windows` | One row per window: metadata + feature vector |
| `scores` | Per-window model scores, calibrated scores, fusion output |
| `risk_events` | Smoothed risk level, context confidence, decision taken |
| `decisions` | Enforcement actions with outcome |
| `alerts` | Behavioral alerts and availability alerts (distinct types) |
| `models` | Model artifacts metadata: version, trained-on data range, hyperparameters, metrics at training time |
| `update_candidates` | Quarantined segments awaiting promotion (Section 12) |
| `app_registry` | `app_id` → process name → category |
| `system_metrics` | CPU, memory, latency samples for runtime-cost reporting |

---

# 8. Privacy by Construction

This section exists because "we promise not to store what you type" is a policy claim, and policy claims are not verifiable. The design makes content capture **structurally impossible**, which is verifiable.

| Layer | Guarantee | How It Is Enforced |
| --- | --- | --- |
| Hook callback | Raw keycode exists only in a local variable, discarded after classification | Code review + guardrail test that no keycode variable escapes scope |
| IPC schema | No field can carry a character, keycode, or text | Schema test asserting field types and names against a denylist |
| Feature extraction | Only aggregate statistics per window are computed | No ordered key sequence is retained past window close |
| Persistence | Database schema has no column capable of holding content | Schema assertion test in CI |
| Application context | Process name only; never window titles, document names, or URLs | Explicit exclusion in collector; guardrail test |

**Additional measures:**
- Pilot participants receive a written plain-language description of exactly what is captured, before any collection begins.
- A local "pause collection" control is available to participants at all times.
- Collected data remains on the participant's machine or an agreed local store; no cloud transmission.
- Data is used only for this academic project and is deleted at project completion unless a participant separately consents to retention.

This should be presented in the final report as a **design contribution**, not a compliance footnote. Most comparable systems assert content-freedom; this design demonstrates it.

---

# 9. Data Collection Programme

This is a **continuous parallel track**, not a phase that happens once. The v1 draft addressed data acquisition in a single line ("provide realistic collected behavioral data"), which is the plan's most serious structural gap: per-user models cannot be trained, and day-disjoint evaluation cannot be performed, without a deliberate multi-week collection effort started early.

## 9.1 Stage 0 — Public Dataset as Pipeline Smoke Test

**When:** Weeks 2–4. **Purpose:** validate pipeline *mechanics* only.

Answers exactly one question: does the feature extraction plus Isolation Forest pipeline mechanically separate one person's typing from another's?

**Dataset selection guidance:** prefer a **free-text** keystroke dataset over a fixed-text one. Fixed-text benchmarks (where participants type the same short string hundreds of times) are convenient but are the opposite of what this project claims to solve — natural, continuous, unconstrained behavior. A free-typing corpus is far more representative.

**Explicit limitations to state in the report:**
- Public keystroke datasets contain **no mouse data** and **no application context** — they can validate at most half of this architecture.
- They are not this project's results. They are a plumbing check.

`[OPEN]` Specific dataset choice — to be decided in Phase 0/1 after reviewing licensing and format.

## 9.2 Stage 1 — Team Self-Collection

**When:** begins as soon as the collector passes its Phase 1 milestone (~Week 3–4), runs continuously.

The four team members run the real collector on their own machines during genuine daily work. This is the first source of true multimodal data (keyboard + mouse + context) and it starts the multi-day clock immediately, without waiting for pilot recruitment.

Serves simultaneously as: first real data, continuous integration test, and dogfooding for stability and overhead.

## 9.3 Stage 2 — Pilot Cohort Collection

**When:** recruitment from Week 2; collection from ~Week 4–5 through ~Week 11.

**Cohort design:** role-homogeneous — participants performing similar enterprise/data-analysis workflows. This is an **evaluation-scoping decision, not an architectural restriction** (Principle P2 stands). A homogeneous cohort is the *harder* test: people doing similar work with similar tools are the most difficult case for a behavioral system to distinguish, so strong results here are more convincing than strong results across an artificially diverse group.

**Target parameters (working values, to be confirmed):**

| Parameter | Working Target | Rationale |
| --- | --- | --- |
| Participants beyond the team | 8–15 | Each additional participant supplies impostor data for every other participant's model, so FAR estimates strengthen quadratically with cohort size |
| Distinct calendar days per participant | ≥ 5–10 | Day-disjoint splitting is meaningless without genuinely separate days |
| Daily collection | Natural use, unscripted | Scripted typing tasks would reproduce the exact "controlled offline conditions" gap this project criticises |
| Minimum usable windows per participant | To be derived in Phase 4 | Determined by the enrollment-length experiment (Section 10.5) |

**Protocol requirements:**
- Written informed consent before any collection (Section 8) — a tracked project deliverable, not an informality.
- Consistent primary input device setup for the collection period; changes logged (ADR-009).
- Participants may pause collection at any time.
- No scripted tasks. Natural work only.

**Human-only activity.** Recruitment, consent, and distribution cannot be automated and must be scheduled explicitly (Section 19.4).

## 9.4 Stage 3 — Evaluation Dataset Freeze

**When:** ~Week 11.

The collected corpus is **frozen** and split into day-disjoint training and evaluation partitions. All headline results in Phase 10 come from this frozen dataset. Freezing prevents the common failure of continuously re-tuning against the same data until results look good — which produces numbers that cannot be defended.

Any data collected after the freeze may be used for supplementary drift analysis, clearly labelled as such.

## 9.5 Synthetic Data Generator

**When:** Phase 0. **Purpose:** unblock development.

A synthetic event-stream generator with configurable, deterministic timing parameters is required early. It allows every downstream component — feature extraction, models, risk engine, dashboard — to be developed and tested **before real data exists**, and provides ground-truth intervals for the ADR-003 timestamp fidelity test.

This is essential for automated development (Section 19): an agent cannot be blocked waiting for human pilot data, and must never be given real participant data as a development fixture.

## 9.6 Data Provenance Rules

| Data Source | May Train Models | May Produce Headline Results | Notes |
| --- | --- | --- | --- |
| Synthetic | No | No | Development and testing only |
| Public dataset | Yes (pipeline validation only) | No | Keyboard-only; report as mechanics check |
| Team self-collected | Yes | Yes, with disclosure | Team members are not blind participants |
| Pilot cohort | Yes | Yes | Primary evaluation corpus |

Every persisted window records its provenance. Mixing provenances within a reported metric without disclosure is prohibited.

---

# 10. Machine Learning Methodology

## 10.1 Modelling Approach

**Per-user, one-class anomaly detection.** For each enrolled user, models are trained **only** on that user's own genuine behavior. No impostor data is used in training. No other user's data enters any model.

This is a deliberate architectural choice, not a convenience:
- Enrolling a new user never requires retraining anyone else's profile.
- The system never has to answer "who is this?", only "is this still the enrolled user?".
- It reflects the real deployment constraint: at enrollment you have the genuine user's data and nobody else's.

**Baseline model:** Isolation Forest (scikit-learn), one per modality per user (ADR-006).

**Why Isolation Forest as baseline:** it learns the region occupied by normal behavior rather than a boundary between two classes, which suits the one-class setting; it is inexpensive at inference, which matters for the low-overhead objective; and it has few hyperparameters, which limits overfitting risk on modest per-user datasets.

## 10.2 Preprocessing

1. **Quality gating** (ADR-005) — drop or label windows lacking sufficient evidence.
2. **Per-user normalisation** — each user's features are scaled against that user's own baseline distribution, because absolute typing speed varies enormously between individuals and is not the discriminative signal.
3. **Outlier handling** — windows corresponding to obvious interruptions are labelled, not silently dropped, so their prevalence can be reported.
4. **Resolution/DPI normalisation** for all mouse spatial features (ADR-009).

## 10.3 Train/Test Splitting — Day-Disjoint, Mandatory

**Random splitting of windows is prohibited for any reported result.**

Windows drawn from the same session are highly correlated. A random split places near-identical neighbouring windows into both training and test sets, which produces excellent scores that reflect memorisation, not generalisation. This is the single most common way a project like this produces impressive-looking numbers that collapse in a live demo.

**Required protocol:**
- Train on data from one set of calendar days; evaluate on **entirely different days**.
- Report the day counts for both partitions.
- Where data allows, use leave-one-day-out cross-validation across a user's collection days.
- Random-split results may be computed as a *diagnostic contrast* — showing the gap between random-split and day-disjoint performance is itself a useful, honest result — but must never be presented as the headline figure.

## 10.4 Baseline Comparison — Required

Isolation Forest must be compared against at least:

1. A **simple statistical baseline** — e.g. Mahalanobis distance to the user's own feature centroid, or per-feature z-score aggregation.
2. At least one **alternative one-class model** — e.g. One-Class SVM, Local Outlier Factor, or Elliptic Envelope.

**Rationale:** "Isolation Forest achieved FAR = x%, FRR = y%" is uninterpretable in isolation. Compared to what? A statistical baseline establishes the floor; an alternative one-class model establishes whether the choice of Isolation Forest is doing real work. Without this, the model selection is an assertion.

The comparison must use identical features, identical day-disjoint splits, and identical calibration procedure.

## 10.5 Enrollment Length Experiment — Required

The reference document lists enrollment duration as an open question. It should be **answered by experiment, not chosen**.

**Protocol:** for each user with sufficient data, train profiles on 1 day, 2 days, 3 days, 5 days, and 7+ days of enrollment data, holding the evaluation partition constant. Plot FAR/FRR/EER against enrollment length and identify where performance stabilises.

**Output:** an evidence-based recommended minimum enrollment period, plus the `min_windows` and `min_distinct_days` thresholds that gate the `ENROLLING → CALIBRATING` transition (Section 5.3). This converts an open question into a documented finding.

## 10.6 Score Calibration

Raw Isolation Forest scores are not comparable across users or across models. Before any decision logic consumes them:

- Each raw score is converted to a **percentile against that user's own enrollment score distribution**.
- This yields a bounded, per-user-meaningful score, which is what makes a shared threshold policy sensible across users.

Calibration parameters are stored as part of the user's profile and versioned with the model.

## 10.7 Fusion

Given calibrated per-modality scores and modality availability (ADR-005):

```
risk_raw = w_kbd·s_kbd + w_mouse·s_mouse      (weights renormalised over available modalities)
```

Initial weights are equal; final weights are derived from per-modality validation performance in Phase 4. The fusion ablation (keyboard-only vs mouse-only vs fused) is a required reported result.

## 10.8 Model Artifact Management

Every trained model is persisted with: model version, user id, training data date range, provenance mix, feature schema version, hyperparameters, calibration parameters, and the evaluation metrics achieved at training time. A model whose feature schema version does not match the running feature extractor must be refused at load time, not silently used.

## 10.9 Anti-Overfitting Measures Summary

| Risk | Mitigation |
| --- | --- |
| Session correlation inflating scores | Day-disjoint splitting (10.3) |
| Memorising individual quirks | One-class model + limited hyperparameters + per-user normalisation |
| Tuning against the test set | Evaluation dataset freeze (9.4); thresholds set on a validation partition |
| Modality absence read as anomaly | Quality gating (ADR-005) + dual-model fusion (ADR-006) |
| Device change read as identity change | Device metadata + normalisation (ADR-009) |
| Application usage learned as identity | Context excluded from identity model (7.3) |
| Small-N overconfidence | Per-user metric distributions and CIs, not just means (13.5) |

---

# 11. Risk Engine and Decision Layer

## 11.1 Processing Chain

```
calibrated scores → availability-weighted fusion → context confidence modifier
                  → temporal smoothing → risk level → decision (with hysteresis + cooldown)
```

## 11.2 Temporal Smoothing

Two mechanisms operate together:

1. **EWMA** over the fused risk score, providing a continuously updated smoothed estimate.
2. **K-of-N consecutive breach counter** — an escalation requires the smoothed score to exceed a level in at least K of the last N windows.

**Purpose:** a single anomalous window — a phone call mid-sentence, a sneeze, an awkward reach for a coffee cup — must never trigger enforcement (Principle P6). Sustained anomaly must escalate promptly.

`INSUFFICIENT_DATA` windows (ADR-005) neither advance nor reset the breach counter; they hold state.

## 11.3 Escalation Ladder

| Risk Level | Condition | Action |
| --- | --- | --- |
| `LOW` | Smoothed score within the user's normal band | `CONTINUE` |
| `MEDIUM` | Sustained moderate deviation | `SOFT_CHALLENGE` — lightweight prompt |
| `HIGH` | Sustained strong deviation | `REAUTH` — forced reauthentication |
| `HIGH` sustained beyond escalation window, or failed reauth | Persistent anomaly | `TERMINATE` — session ended, alert raised |

**Hysteresis:** de-escalation requires a longer sustained period of normal behavior than escalation required, preventing rapid oscillation around a threshold.

**Cooldown / action budget:** a cap on enforcement actions per unit time, preventing a challenge loop from making the machine unusable. Exceeding the budget escalates to alert-and-log rather than repeated prompts.

`[OPEN]` All numeric thresholds — `LOW/MEDIUM/HIGH` boundaries, K, N, EWMA α, escalation and cooldown windows — are deliberately **not fixed in this plan**. They are set in Phase 5 and tuned in Phase 10 against measured FAR/FRR on a validation partition, then held fixed for the frozen evaluation.

## 11.4 Context Confidence Modifier

Per ADR-008, the confidence weight derived from application category and per-user empirical variance scales how aggressively the ladder responds. Constraints:

- Confidence has a **hard floor**. No application may reduce effective monitoring to zero.
- Confidence adjustment is **logged** for every decision, so any decision can be reconstructed and audited.
- The identity model is untouched — only the interpretation of its output changes.

## 11.5 State Machine Gating

Enforcement is applied only in the `ACTIVE` state. In `ENROLLING` no decisions are computed; in `CALIBRATING` decisions are computed and logged but not enforced (shadow mode); in `DEGRADED` enforcement is suspended per ADR-011 with a loud availability alert.

Shadow mode is operationally valuable: it produces the decision trace needed for threshold tuning without exposing any user to a false lockout.

---

# 12. Model Update Manager

Behavior genuinely drifts — new keyboard, more practice, changed posture, fatigue. Profiles must adapt. But an adaptation mechanism is also the most attractive attack surface in the entire system: an attacker who can get their behavior into the training set converts the system into one that recognises *them* as legitimate.

## 12.1 Promotion Gate

A segment (ADR-007) becomes eligible for promotion into training data only if **all** of the following hold:

| Gate | Condition |
| --- | --- |
| G1 — Risk | No window in the segment exceeded `MEDIUM`; no enforcement action was triggered |
| G2 — Verification | The segment is anchored to an **independent verification event** (see 12.2) |
| G3 — Volume | Segment contains at least `min_promotable_windows` scored windows |
| G4 — Continuity | No unexplained collection gap within the segment |
| G5 — Quarantine | The segment has aged at least `quarantine_days` without any subsequent incident attributed to it |
| G6 — Schedule | Retraining occurs on a fixed cadence, never reactively after a session |

## 12.2 Defining "Independent Verification" — Resolving the Gap

The reference document requires that the user "separately verified themselves during or after that session." This is underspecified in a way that matters: **if the system works well, a genuine user's session stays low-risk and never triggers a challenge — so no verification event ever occurs.** Taken literally, almost no session would ever qualify and the profile would never adapt. Loosening the definition ad hoc would reopen the poisoning hole.

**Resolution — an ordered list of accepted verification anchors:**

| Anchor | Strength | Notes |
| --- | --- | --- |
| A1 — OS unlock or login at segment start | Strong | The segment begins immediately after a credential-based entry |
| A2 — Successful explicit reauthentication during the segment | Strong | Includes reauth triggered by the system |
| A3 — Scheduled periodic verification prompt | Moderate | A deliberate low-frequency prompt (e.g. once per N hours) issued during the pilot, specifically to create verification anchors for otherwise uneventful sessions |
| A4 — Continuous low risk alone | **Insufficient** | Explicitly rejected as an anchor: it is exactly the condition a successful attacker also satisfies |

**A3 is the key addition.** It directly solves the "uneventful sessions never qualify" problem without weakening the gate, at the cost of a small, predictable, infrequent user-experience burden. This is a defensible engineering trade-off and should be presented as such.

## 12.3 Quarantine and Rollback

- Promoted candidates are held in `update_candidates` for a quarantine period before being merged into training data.
- If an incident is later attributed to a quarantined segment, that segment is discarded.
- Every model version is retained with its metadata, so rollback to a previous profile is always possible.
- After retraining, the new model is **evaluated against a held-out genuine partition and against impostor data before replacing the active model**. A retrained model that degrades FAR or FRR beyond a tolerance is rejected automatically, not deployed.

## 12.4 Required Efficacy Experiment

The v1 draft builds this component but never demonstrates its value. Two experiments are required in Phase 10:

**E1 — Drift benefit.** Using the pilot corpus ordered by date: compare a frozen profile trained on early days against a profile updated through the Model Update Manager, both evaluated on the latest days. Measure whether FRR degrades over time without updates, and whether updating recovers it **without degrading FAR**.

**E2 — Poisoning resistance.** Inject impostor segments into the update candidate stream and verify that the promotion gate rejects them. Then deliberately bypass the gate to demonstrate the profile *would* have been contaminated — quantifying what the gate is actually protecting against.

Together these convert the Model Update Manager from an asserted feature into a measured contribution.

---

# 13. Evaluation and Validation Plan

## 13.1 Accuracy Metrics

| Metric | Definition |
| --- | --- |
| FAR | Fraction of impostor windows/decisions accepted as genuine |
| FRR | Fraction of genuine windows/decisions rejected |
| EER | Operating point where FAR = FRR |
| ROC / DET curves | Full operating characteristic, per user and aggregate |
| Detection latency | Windows and wall-clock seconds from takeover to first `HIGH` decision |
| Per-user metric spread | Distribution of each metric across users, not just the mean |

Metrics are reported at **both** window level (raw model performance) and **decision level** (post-smoothing system performance). These differ substantially, and reporting only one is misleading — smoothing intentionally trades window-level sensitivity for decision-level stability.

## 13.2 Impostor Taxonomy — Required

The project's own literature review criticises prior work for testing only against basic impostors. Evaluating only zero-effort impostors would reproduce the exact gap being claimed.

| Class | Definition | Method |
| --- | --- | --- |
| **I1 — Zero-effort** | Another enrolled user behaving naturally, not attempting imitation | Cross-evaluate every user's sessions against every other user's model. Free from existing data. |
| **I2 — Informed impostor** | An attacker who has observed the genuine user and attempts to mimic pace and rhythm | Small controlled experiment: participant observes a target user working, then attempts to operate the machine in that style. Report N explicitly. |
| **I3 — Replay / synthetic** *(stretch)* | Programmatically generated or replayed event streams | Feed recorded or synthetic streams to test whether non-human timing signatures are detected |

I1 is mandatory and effectively free. **I2 is mandatory and is the differentiating result** — even a small-N mimicry experiment directly addresses a gap identified in the project's own literature review, and is a far stronger claim than "we tested against other users." I3 is optional.

## 13.3 Live Session Hijack Drill — Required

Offline cross-user scoring proves statistical separability. It does not demonstrate the system doing its actual job.

**Protocol:** Participant A is logged in and working normally with the system in `ACTIVE` state. Mid-session, A steps away and Participant B takes over and continues working. The full pipeline runs live. Recorded outcomes:

- Time from takeover to first `MEDIUM` risk
- Time from takeover to first `HIGH` risk
- Time from takeover to enforcement action
- Number of windows required
- Full risk-score trace across the transition

Repeat across multiple A/B pairs and both context types (productivity and high-variance).

This directly enacts the stated threat model, produces the project's most compelling single figure (a risk trace with a visible inflection at the takeover point), and is a far better demonstration than a metrics table.

## 13.4 Runtime Cost — A First-Class Result

The problem statement asserts that prior work does not report deployment cost. This project must.

| Measurement | Target Budget (working) |
| --- | --- |
| Collector CPU (sustained, typical input rate) | < 1–2% of one core |
| Collector resident memory | Bounded, no growth over multi-hour runs |
| Backend CPU (idle / scoring) | Low single-digit percent |
| Event ingestion throughput | Comfortably above peak human input rate, with headroom |
| Feature extraction latency | Per window, p50 / p95 / p99 |
| Model inference latency | Per window, p50 / p95 / p99 |
| End-to-end latency (event → decision available) | p50 / p95 / p99 |
| WebSocket delivery latency | p50 / p95 |
| Dropped events | Zero under normal load; measured under stress |

Latency must be reported as **percentiles, not means** — the tail is what determines whether the system feels lightweight. Measurements are taken on real machines during real use, and machine specifications are reported alongside the numbers.

## 13.5 Statistical Honesty Requirements

With a cohort of this size, results carry real uncertainty. The report must:

- State cohort size, per-user data volumes, and number of collection days explicitly, adjacent to every headline metric.
- Report **per-user metric distributions**, not only aggregate means — a mean FRR conceals the possibility that the system works well for most users and poorly for one.
- Provide confidence intervals (bootstrap is sufficient) where feasible.
- Disclose that team members are non-blind participants.
- Frame results as **indicative of a realistic enterprise workflow**, not as general-population validation. The defensible claim is *"validated on a realistic enterprise workflow,"* not *"works for everyone."*

## 13.6 Robustness and Failure Testing

| Scenario | Expected Behavior |
| --- | --- |
| Genuine user, normal behavior | Sustained `LOW`; no enforcement |
| Genuine user, brief unusual burst | No escalation beyond `MEDIUM`; no enforcement |
| Genuine user, sustained unusual behavior | Graded escalation; recovery on return to normal |
| User idle / away | `INSUFFICIENT_DATA`; state held; no false escalation |
| Rapid application switching | Context confidence adjusts; monitoring continues |
| High-variance application (gaming/creative) | Reduced confidence, floor enforced, monitoring never disabled |
| Impostor takeover | Escalation within measured detection latency |
| Multiple enrolled users on one machine | Correct profile selection; no cross-contamination |
| Collector process killed mid-session | Heartbeat loss → tamper signal + `DEGRADED` (ADR-011) |
| Backend unavailable | Fail-open + HIGH availability alert |
| Model missing or schema-mismatched | Refuse to load; `DEGRADED`; alert |
| Malformed or out-of-order events | Rejected and counted; pipeline continues |
| Dashboard disconnected | No effect on enforcement; reconnect restores state |
| Device change mid-session | Logged; analysed for confound |

---

# 14. Security and Threat Model

## 14.1 Threats Addressed

| Threat | Mechanism |
| --- | --- |
| Session hijack of an unlocked workstation | Continuous behavioral verification with graded escalation |
| Insider misuse of a colleague's session | Per-user profiles; cross-user impostor rejection |
| Gradual profile poisoning | Model Update Manager gates, quarantine, rollback (Section 12) |
| Evasion by opening a "noisy" application | Confidence floor; monitoring never disabled (P8) |
| Collector tampering / termination | Heartbeat monitoring; loss treated as a security event (ADR-011) |
| Automated/replayed input | Optional I3 replay testing (13.2) |

## 14.2 Threats Explicitly Not Addressed

- Remote network-based session hijacking (no network-layer visibility).
- A privileged attacker with administrative control of the machine, who can disable or subvert the collector.
- Hardware-level input injection.
- An attacker who compromises the model store directly.
- Sophisticated adversarial mimicry beyond the scope of the I2 experiment.

Stating these explicitly is a strength. Overclaiming coverage is the fastest way to lose credibility in review.

## 14.3 Data Protection

Local-first storage, content-free by construction (Section 8), retention limits (ADR-012), model artifacts and databases access-restricted on the host, and no cloud transmission.

## 14.4 Known Limitation — Dashboard Placement

In this build the dashboard runs on the **same machine being monitored**. An attacker occupying a hijacked session could therefore observe their own risk score rising and modulate behavior to remain below threshold.

In a production deployment the security console would be separated from the monitored endpoint — the operator being evaluated should not see their own live evaluation. Implementing that separation is out of scope for this project, but the limitation must be **stated explicitly in the report and mitigations noted** (dashboard authentication; alerts also written to an append-only audit log independent of the dashboard).

Documenting this demonstrates threat-model rigour. Omitting it invites the question in review with no answer prepared.

---

# 15. Technology Stack

## 15.1 By Layer

| Layer | Technology | Purpose |
| --- | --- | --- |
| Native collector | C / C++17, CMake | Global input hooking, capture-time timestamping, key-class tokenisation |
| Input hooking | libuiohook (or platform-native fallback: `SetWindowsHookEx` / X11 XInput2) | Cross-platform global hook abstraction |
| Timing | `QueryPerformanceCounter` (Win) / `clock_gettime(CLOCK_MONOTONIC_RAW)` (Linux) | Monotonic microsecond timestamps (ADR-003) |
| IPC | Named pipe (Win) / Unix domain socket (Linux), length-prefixed binary frames | Collector → backend transport (ADR-002) |
| Schema / contracts | JSON Schema in `protocol/`, code-generated to C++ structs and Pydantic models | Single source of truth for the interface |
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 | Ingestion, orchestration, API, WebSocket |
| Numerics | NumPy, Pandas | Feature computation and analysis |
| ML | scikit-learn (IsolationForest, OneClassSVM, LocalOutlierFactor, EllipticEnvelope), joblib | Per-user models, baseline comparison, persistence |
| Storage | SQLite (WAL mode), JSONL | Structured records; append-only audit log |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS | Dashboard |
| Charting | Recharts (or equivalent) | Risk timeline and metric visualisation |
| Realtime | Native WebSocket | Live risk streaming and alerts |
| Testing (Python) | pytest, pytest-asyncio, hypothesis | Unit, integration, property-based tests |
| Testing (C++) | GoogleTest or Catch2 | Collector unit tests |
| Testing (frontend) | Vitest, React Testing Library | Component tests |
| Quality | ruff, black, mypy, clang-format, clang-tidy, pre-commit | Enforced style and typing |
| CI | GitHub Actions | Tests, lint, and **guardrail checks** on every PR |
| Analysis | Jupyter, Matplotlib | Experiments and figures |
| Docs | Markdown, Mermaid/ASCII diagrams, MkDocs (optional) | Architecture and experiment records |

## 15.2 Technology Introduction by Phase

| Phase | Technologies First Introduced |
| --- | --- |
| 0 | CMake, libuiohook spike, Python env, FastAPI skeleton, React/Vite skeleton, Git + CI, JSON Schema contracts, synthetic generator |
| 1 | C++17 collector, platform hook APIs, monotonic clock APIs, IPC transport, GoogleTest |
| 2 | NumPy/Pandas feature pipeline, SQLite schema, hypothesis property tests |
| 3 | Collection tooling, consent/enrollment workflow, provenance tagging |
| 4 | scikit-learn, joblib, Jupyter, evaluation harness, Matplotlib |
| 5 | Risk engine, decision layer, state machine, FastAPI API surface |
| 6 | WebSocket, React dashboard, Recharts, Vitest |
| 7 | Context configuration (`app_categories.yaml`), empirical variance computation |
| 8 | Model Update Manager, quarantine store, model versioning/rollback |
| 9 | End-to-end integration harness, fault-injection tooling |
| 10 | Profiling and benchmarking tooling, statistical analysis (bootstrap CIs) |
| 11 | Documentation build, final report and presentation assets |

## 15.3 Explicitly Rejected Technologies

| Rejected | Reason |
| --- | --- |
| Deep learning frameworks (baseline) | Contradicts the low-overhead objective; disproportionate to available per-user data. May be evaluated as a comparison model only if time permits. |
| Cloud databases / remote telemetry | Violates local-first privacy design |
| Application plugins or extensions (Excel add-in, browser extension) | Violates P1/P2 — application-specific by definition |
| Accessibility-API / UI-automation frameworks | Would read window contents and widget state; violates P4 |
| Screen capture / OCR | Violates P4 |

---

# 16. Repository Structure

```
continuous-authentication/
│
├── AGENTS.md                     # Automated-development guardrails (Section 19.3)
├── PLAN.md                       # This document
├── README.md
│
├── protocol/                     # SINGLE SOURCE OF TRUTH for all interfaces
│   ├── schemas/
│   │   ├── event.schema.json
│   │   ├── feature_window.schema.json
│   │   ├── score.schema.json
│   │   ├── risk_decision.schema.json
│   │   └── api.openapi.yaml
│   ├── codegen/                  # Generates C++ structs + Pydantic models
│   └── VERSION
│
├── collector/                    # Native OS collector (C/C++)
│   ├── src/
│   │   ├── hooks/                # Platform-specific hook backends
│   │   ├── timing/               # Monotonic clock abstraction (ADR-003)
│   │   ├── classify/             # Key → key-class tokenisation (ADR-004)
│   │   ├── context/              # Foreground app + device class
│   │   ├── ipc/                  # Transport (ADR-002)
│   │   └── buffer/               # Bounded ring buffer, backpressure
│   ├── include/
│   ├── tests/
│   └── CMakeLists.txt
│
├── backend/
│   ├── app/
│   │   ├── ingestion/            # Frame decode, validation, ordering, sessions
│   │   ├── features/             # Windowing, quality gate, feature blocks
│   │   ├── models/               # Profile load/score/calibration
│   │   ├── risk/                 # Fusion, context, smoothing, state machine
│   │   ├── decisions/            # Escalation ladder, hysteresis, cooldown
│   │   ├── updates/              # Model Update Manager, quarantine
│   │   ├── storage/              # SQLite + JSONL
│   │   ├── api/                  # REST endpoints
│   │   └── websocket/            # Live stream
│   ├── tests/
│   └── pyproject.toml
│
├── ml/
│   ├── datasets/                 # Loaders; NO participant data committed
│   ├── features/                 # Shared feature logic (imported by backend)
│   ├── training/                 # Per-user training pipelines
│   ├── calibration/
│   ├── evaluation/               # FAR/FRR/EER, ROC/DET, splits, ablations
│   ├── baselines/                # Alternative one-class models (10.4)
│   ├── experiments/              # Numbered, reproducible experiment scripts
│   └── notebooks/
│
├── dashboard/
│   ├── src/
│   │   ├── components/
│   │   ├── views/                # Overview, Live, Alerts, History, System Health
│   │   ├── hooks/
│   │   └── api/
│   └── tests/
│
├── tools/
│   ├── synthetic/                # Synthetic event generator (9.5)
│   ├── faultinjection/           # Component failure simulation
│   ├── benchmarks/               # Runtime-cost measurement harness
│   └── guardrails/               # Automated principle-violation checks (19.3)
│
├── config/
│   ├── app_categories.yaml       # ADR-008 bootstrap category map
│   ├── thresholds.yaml           # All tunable parameters — never hardcoded
│   └── collection.yaml
│
├── data/                         # GIT-IGNORED. Never committed.
│   ├── raw/
│   ├── processed/
│   └── frozen/                   # Evaluation freeze (9.4)
│
├── docs/
│   ├── adr/                      # One file per ADR
│   ├── architecture/
│   ├── experiments/              # One record per experiment run
│   ├── protocol/
│   ├── pilot/                    # Consent form, participant brief
│   └── meetings/
│
└── .github/workflows/            # CI: tests, lint, guardrails
```

**Structural rules:**

1. `protocol/` is authoritative. Interface changes happen there first, then propagate by code generation. No component defines its own copy of a shared schema.
2. `config/` holds every tunable parameter. **No threshold, window size, or weight may be hardcoded in source.** This is what makes empirical tuning possible without code changes.
3. `data/` is git-ignored without exception. No participant data ever enters version control.
4. Feature logic lives in one place (`ml/features/`) and is imported by the backend, so training and inference cannot drift apart — a classic and hard-to-diagnose source of degraded live performance.

---

# 17. Phase-Wise Implementation Plan

**Delegation model:** work is defined **per phase**, not per person. Task allocation among team members is decided separately.

**Automation model:** each phase separates **Automatable** work (specification-driven implementation, tests, analysis — suitable for AI-agent execution under review) from **Human-Required** work (physical hardware validation, participant recruitment and consent, live drills, judgement calls that change architecture). See Section 19.

Phases overlap deliberately. This is not a waterfall.

---

## PHASE 0 — Foundation, Contracts and Feasibility Spikes

**Weeks:** 1
**Objective:** Establish the development substrate and **de-risk the single largest technical unknown before committing schedule to it.**

### Entry Criteria
Project context and architecture agreed (this document).

### Scope of Work

**0.1 — Platform feasibility spike (highest priority, gating).**
Prove global input capture actually works on the intended target OS before Phase 1 commits three weeks to it. Deliver a minimal program that hooks global keyboard and mouse events system-wide, verifies capture continues across application focus changes, records the permission model required, and measures baseline hook overhead. Resolve ADR-001 with evidence.

*Why this is first:* if global hooking is blocked or degraded on the chosen platform, every downstream estimate is invalid. Discovering this in Week 5 is a project-threatening failure; discovering it in Week 1 is a routine platform decision.

**0.2 — Interface contracts.** Define all schemas in `protocol/`: event, feature window, score, risk decision, API. Set up code generation to C++ structs and Pydantic models. **Freeze interfaces, not implementations.**

**0.3 — Repository and CI.** Repository structure (Section 16), branch/PR conventions, linting, type checking, test harnesses for all three languages, CI pipeline.

**0.4 — Guardrail harness.** Implement the automated principle-violation checks (Section 19.3) in CI from day one, so no privacy or architecture violation can be merged even accidentally.

**0.5 — Synthetic event generator.** Deterministic, parameterised event stream generator with configurable timing (Section 9.5). Unblocks all downstream development before real data exists.

**0.6 — Component skeletons.** Collector, backend, ML package, and dashboard each build, run, and pass a trivial test.

**0.7 — Pilot planning begins.** Draft consent form and participant brief; identify candidate cohort; begin recruitment conversations. Started now because recruitment has the longest human lead time in the project.

### Key Decisions Resolved
ADR-001 (target OS), ADR-002 (initial IPC mechanism), public dataset shortlist.

### Deliverables
Working spike binary; frozen `protocol/` v1; CI with guardrails; synthetic generator; four building skeletons; draft consent form; ADR-001 record.

### Exit Criteria
- Global input capture demonstrated on the target OS, across focus changes.
- All schemas defined and generating code for both languages.
- CI green on all components, including guardrail checks.
- Synthetic generator produces streams with verifiable known timing.

### Automation Split
- **Automatable:** contracts, codegen, repository scaffolding, CI, guardrails, synthetic generator, skeletons.
- **Human-required:** running the spike on real hardware, granting OS permissions, judging platform viability, recruitment conversations, consent form review.

### Risks
Platform hook restrictions (mitigated by this phase existing); over-engineering contracts before implementation experience (mitigate by keeping schemas minimal and versioned).

---

## PHASE 1 — Native Event Collection and IPC

**Weeks:** 2–4
**Objective:** Produce a trustworthy, content-free, correctly-timestamped event stream. Everything downstream inherits this layer's quality.

### Entry Criteria
Phase 0 spike successful; `protocol/` frozen.

### Scope of Work

**1.1 — Collector core.** Global keyboard and mouse hooks behind a platform abstraction interface; event capture with **capture-time monotonic timestamping** (ADR-003).

**1.2 — Key-class tokenisation (ADR-004).** Implement the key-class taxonomy. Raw keycodes must be discarded within the hook callback and must never reach the IPC boundary.

**1.3 — Context resolution.** Foreground process name → `app_id` via the app registry; category lookup; input device class detection (ADR-009); screen resolution/DPI capture.

**1.4 — Buffering and backpressure.** Bounded ring buffer; defined drop policy under overload; dropped-event counters exposed via heartbeat.

**1.5 — Heartbeat.** Periodic heartbeat carrying uptime, dropped-event count, and buffer high-water mark — the basis for tamper and availability detection (ADR-011).

**1.6 — IPC transport.** Length-prefixed binary framing over the chosen transport, behind a swappable interface.

**1.7 — Ingestion service.** Frame decode, schema validation, sequence-gap and duplicate detection, session/segment attribution (ADR-007), in-memory raw event ring.

**1.8 — Timestamp fidelity test (gating).** Inject a synthetic stream with known inter-event intervals; assert intervals reconstructed at the ingestion boundary match within tolerance. This test protects the integrity of every biometric feature in the project.

**1.9 — Throughput benchmark.** Measure sustained event throughput and latency to validate or revise ADR-002 with data.

**1.10 — Developer debug view.** Minimal display of event counts, last-event timestamp, current application, collector status, dropped events.

### Key Decisions Resolved
ADR-002 confirmed or revised by measurement; drop policy under overload.

### Deliverables
Collector binary; IPC transport; ingestion service; timestamp fidelity test suite; throughput benchmark report; debug view.

### Exit Criteria
- Real keyboard and mouse activity produces validated structured events in the backend, continuously and across focus changes.
- Timestamp fidelity test passes within defined tolerance.
- Zero dropped events at typical human input rates; drop behavior defined and measured under stress.
- Guardrail test confirms no keycode can traverse the IPC boundary.
- Collector runs for a multi-hour session with bounded, non-growing memory.

### Automation Split
- **Automatable:** collector implementation against the frozen schema, tokenisation, buffering, IPC, ingestion, all tests, benchmark harness, debug view.
- **Human-required:** running on real hardware with real input, OS permission grants, multi-hour stability observation, judging whether measured overhead is acceptable.

### Risks
Platform hook quirks; clock resolution differences across machines; **timestamp assignment defects (highest-impact risk in the project — silent, and only visible at evaluation time)**. Mitigated by 1.8 being a gating criterion.

---

## PHASE 2 — Feature Extraction and Data Pipeline

**Weeks:** 3–6 *(overlaps Phase 1)*
**Objective:** Convert the event stream into reliable, content-free feature windows.

### Entry Criteria
A stable event stream exists (synthetic is sufficient to begin).

### Scope of Work

**2.1 — Windowing.** Dual trigger: 30 seconds or 100 keystrokes, whichever first; both configurable in `config/`.

**2.2 — Quality gating (ADR-005).** Implement `FULL` / `KBD_ONLY` / `MOUSE_ONLY` / `INSUFFICIENT_DATA` labelling with configurable thresholds. Derive initial thresholds from observed distributions rather than guessing.

**2.3 — Keyboard feature block.** All features in Section 7.3, including class-transition latency features.

**2.4 — Mouse feature block.** All features in Section 7.3, with resolution/DPI normalisation.

**2.5 — Context block.** Computed and stored, but structurally kept **out** of the identity model's feature space.

**2.6 — Per-user normalisation.** Baseline statistics per user; normalisation applied consistently at training and inference from shared code.

**2.7 — Storage layer.** SQLite schema (Section 7.4) in WAL mode; JSONL audit log; retention and rotation (ADR-012).

**2.8 — Provenance tagging.** Every window records its data source per Section 9.6.

**2.9 — Feature validation suite.** Property-based tests: feature values within plausible ranges; deterministic for identical inputs; correct behavior on empty, sparse, and single-event windows; no NaN leakage into stored vectors.

**2.10 — Feature distribution analysis.** Empirical distributions from early self-collected data, used to set quality-gate thresholds and identify degenerate or constant features worth removing.

### Key Decisions Resolved
Quality-gate thresholds; window parameter defaults; final baseline feature list.

### Deliverables
Feature extraction module; storage layer with retention; validation suite; feature distribution report; documented feature dictionary.

### Exit Criteria
- A raw event stream produces a validated feature matrix end to end.
- Every feature in the dictionary is implemented, tested, and documented.
- Identical inputs produce identical feature vectors (determinism verified).
- Idle and sparse windows are correctly labelled `INSUFFICIENT_DATA` and never produce spurious values.
- Storage retention and rotation verified.

### Automation Split
- **Automatable:** all feature implementation, storage, tests, distribution analysis, documentation.
- **Human-required:** reviewing whether the feature set is behaviorally sensible; approving threshold values.

### Risks
Features that are degenerate in practice; normalisation implemented differently in training vs inference (mitigated by the shared-module rule in Section 16).

---

## PHASE 3 — Data Collection Programme *(Continuous Track)*

**Weeks:** 2–11, running in parallel with all other phases
**Objective:** Acquire a multi-day, multimodal, ethically-collected corpus sufficient for day-disjoint evaluation.

This is presented as a phase for planning visibility, but it is a **continuous track**. Treating data acquisition as a one-off task is the single largest structural risk to the evaluation.

### Entry Criteria
Collector passes Phase 1 milestone (for Stage 1); consent materials approved (for Stage 2).

### Scope of Work

**3.1 — Stage 0: public dataset validation** (Weeks 2–4). Select and integrate a free-text keystroke dataset; validate pipeline mechanics; document explicitly that this is keyboard-only and is not a project result.

**3.2 — Stage 1: team self-collection** (from ~Week 3–4, continuous). Team members run the real collector during genuine daily work. Serves as first real data, continuous integration test, and stability dogfooding.

**3.3 — Stage 2: pilot recruitment and consent** (Weeks 2–5). Finalise consent form and participant brief; recruit the role-homogeneous cohort; obtain written informed consent; brief participants on the pause control and device-consistency request.

**3.4 — Stage 2: pilot collection** (Weeks 4/5–11). Distribute the collector; support installation; natural unscripted use; monitor collection health (days collected, windows per participant, device changes, gaps).

**3.5 — Verification anchor generation.** Enable the scheduled periodic verification prompt (anchor A3, Section 12.2) during pilot collection so that uneventful low-risk segments become eligible for the Model Update Manager experiments.

**3.6 — Collection health dashboard.** Per-participant coverage: distinct days, total windows, modality balance, quality-label distribution, device changes, gaps. Makes shortfalls visible early enough to correct.

**3.7 — Stage 3: evaluation dataset freeze** (~Week 11). Freeze the corpus; construct day-disjoint partitions; version and document the frozen dataset.

### Key Decisions Resolved
Public dataset choice; final cohort size; minimum days per participant; freeze date.

### Deliverables
Signed consent records; distributed collector build; collection health dashboard; per-participant coverage report; frozen, versioned evaluation dataset with documented splits.

### Exit Criteria
- Target participant count enrolled with valid consent.
- Minimum distinct collection days achieved per participant (or shortfall explicitly documented and accounted for in analysis).
- Corpus frozen with reproducible day-disjoint splits.
- No content, credentials, or screen data present anywhere in the corpus (verified by audit).

### Automation Split
- **Automatable:** dataset integration, collection health dashboard, coverage reporting, split construction, freeze tooling, audit scripts.
- **Human-required:** *all participant-facing activity* — recruitment, consent, installation support, device-consistency briefing, troubleshooting on participant machines. **None of this can be automated and it has the longest lead time in the project.**

### Risks
**Late start is the dominant risk** — insufficient distinct days by freeze date cannot be recovered later. Participant attrition; inconsistent collection; device changes mid-study; installation friction on participant machines. Mitigations: start Week 2, over-recruit, monitor coverage weekly, keep installation trivially simple.

---

## PHASE 4 — Per-User Modelling and ML Baseline

**Weeks:** 4–8
**Objective:** Produce trained, calibrated per-user profiles with credible, honestly-evaluated baseline performance.

### Entry Criteria
Feature pipeline stable; Stage 0/1 data available.

### Scope of Work

**4.1 — Per-user training pipeline.** Train independent keyboard and mouse Isolation Forests per user (ADR-006), strictly on that user's own data.

**4.2 — Score calibration.** Percentile calibration against each user's own enrollment score distribution (Section 10.6); calibration parameters versioned with the model.

**4.3 — Day-disjoint evaluation harness.** Splitting utilities, leave-one-day-out cross-validation, FAR/FRR/EER computation, ROC/DET curves, per-user metric distributions. **Random splitting available only as a labelled diagnostic contrast.**

**4.4 — Zero-effort impostor evaluation (I1).** Cross-evaluate every user's data against every other user's models. Produces the core FAR estimates at no additional collection cost.

**4.5 — Fusion ablation (resolves ADR-006).** Compare keyboard-only, mouse-only, dual-model score fusion, and single fused-vector model on identical splits. Confirm or revise ADR-006 with evidence, including missing-modality behavior.

**4.6 — Baseline model comparison (Section 10.4).** Isolation Forest vs a statistical baseline vs at least one alternative one-class model, on identical features, splits, and calibration.

**4.7 — Enrollment length experiment (Section 10.5).** FAR/FRR/EER vs enrollment duration; derive `min_windows` and `min_distinct_days` for the `ENROLLING → CALIBRATING` transition.

**4.8 — Feature importance and reduction.** Identify low-value features; assess whether a reduced set maintains performance at lower cost.

**4.9 — Model artifact management.** Versioning, metadata, schema-version compatibility enforcement at load (Section 10.8).

**4.10 — Experiment records.** Every experiment recorded in `docs/experiments/` with configuration, data version, and results — reproducibility is a graded outcome, not a nicety.

### Key Decisions Resolved
ADR-006 confirmed or revised; fusion weights; enrollment thresholds; final feature set; baseline model selection justified by evidence.

### Deliverables
Training pipeline; calibration module; evaluation harness; ablation, baseline comparison, and enrollment-length reports; versioned model artifacts; experiment records.

### Exit Criteria
- A trained per-user profile scores a new behavioral window through the production code path.
- Day-disjoint FAR/FRR/EER reported for every user with sufficient data, with per-user spread.
- Fusion ablation and baseline comparison complete, with ADR-006 explicitly confirmed or revised.
- Enrollment-length curve produced and thresholds derived.
- All results reproducible from recorded configuration.

### Automation Split
- **Automatable:** essentially all of it — training, calibration, evaluation harness, all four experiments, reporting, artifact management.
- **Human-required:** interpreting results; deciding whether performance is adequate to proceed; approving ADR revisions.

### Risks
Insufficient data volume early (mitigated by synthetic and public data for pipeline work, real data for reported results only); tuning against evaluation data (mitigated by validation partitions and the Phase 3 freeze).

---

## PHASE 5 — Risk Engine, Decision Layer and Backend

**Weeks:** 5–9
**Objective:** Convert model scores into stable, auditable, graded security decisions.

### Entry Criteria
Scoring available through a stable interface.

### Scope of Work

**5.1 — Availability-weighted fusion.** Combine calibrated per-modality scores respecting quality labels (ADR-005).

**5.2 — Temporal smoothing.** EWMA plus K-of-N consecutive breach logic; `INSUFFICIENT_DATA` holds state without advancing or resetting counters.

**5.3 — Escalation ladder.** `CONTINUE` / `SOFT_CHALLENGE` / `REAUTH` / `TERMINATE` with hysteresis and cooldown/action budget (Section 11.3).

**5.4 — State machine (Section 5.3).** `ENROLLING` / `CALIBRATING` / `ACTIVE` / `DEGRADED` / `SUSPENDED`, with transition logic and shadow-mode decision logging in `CALIBRATING`.

**5.5 — Failure policy (ADR-011).** Fail-open with HIGH-severity availability alerting; heartbeat-loss tamper signal; alert types structurally distinct from behavioral alerts.

**5.6 — Session and segment management (ADR-007).** Idle-split logic; verification anchor recording.

**5.7 — Decision audit trail.** Every decision persisted with its full input trace — scores, availability, context confidence, smoothed value, state, thresholds in effect. Any decision must be reconstructible after the fact.

**5.8 — API surface.** REST endpoints for state, history, profiles, metrics, and administration, per the OpenAPI contract.

**5.9 — Configuration externalisation.** Every threshold, weight, and window in `config/thresholds.yaml`. Verified by test that no tunable value is hardcoded.

**5.10 — Risk engine test suite.** Scenario tests for every row of Section 13.6, driven by synthetic score sequences.

### Key Decisions Resolved
Initial threshold values (provisional, to be tuned in Phase 10); smoothing parameters; cooldown policy.

### Deliverables
Risk engine; decision layer; state machine; failure handling; session management; audit trail; API; configuration schema; scenario test suite.

### Exit Criteria
- Live behavioral input produces a graded, smoothed, audited decision.
- A single anomalous window provably cannot trigger enforcement.
- Sustained anomaly escalates through the ladder as specified.
- Shadow mode produces complete decision traces without enforcement.
- All failure scenarios produce fail-open plus loud alerting.
- No tunable parameter is hardcoded.

### Automation Split
- **Automatable:** the entire engine, state machine, API, audit trail, and scenario tests.
- **Human-required:** approving the escalation policy as *acceptable user experience*; confirming the fail-open decision.

### Risks
Over-tuned thresholds on insufficient data (mitigate: shadow mode first, tune in Phase 10); smoothing so aggressive that detection latency becomes unacceptable (mitigate: measure the sensitivity/latency trade-off explicitly).

---

## PHASE 6 — Dashboard and Real-Time Monitoring

**Weeks:** 7–11
**Objective:** Make system state observable, auditable, and demonstrable in real time.

### Entry Criteria
Decisions available via API; WebSocket contract defined.

### Scope of Work

**6.1 — WebSocket streaming.** Backend channel for risk updates, alerts, state transitions, and system health; reconnection with state resynchronisation.

**6.2 — Overview view.** Current user, session state, system state (`ENROLLING`/`CALIBRATING`/`ACTIVE`/`DEGRADED`), current risk score and level, last check time.

**6.3 — Live monitoring view.** Risk timeline chart, current application and category, active context confidence, modality availability, recent windows and decisions.

**6.4 — Alerts view.** **Behavioral alerts and availability alerts visually distinct** (ADR-011). "The system is not currently protecting you" must never resemble "everything is normal."

**6.5 — History view.** Searchable decision history with full context, and an exportable audit trail.

**6.6 — System health view.** Collector status, heartbeat, dropped events, throughput, CPU/memory, end-to-end latency — the live surface of the runtime-cost story.

**6.7 — Profile status view.** Per-user state, enrollment progress, model version, last update, update-candidate queue.

**6.8 — Dashboard authentication.** Access control on the dashboard, and documentation of the co-location limitation (Section 14.4).

**6.9 — Demo mode.** Replay a recorded risk trace for presentation, clearly labelled as replay.

### Key Decisions Resolved
Alert taxonomy and visual language; dashboard access control approach.

### Deliverables
All views; WebSocket client with resync; authentication; demo mode; component tests.

### Exit Criteria
- Dashboard reflects live authentication state end to end with acceptable latency.
- Availability and behavioral alerts are unmistakably distinguishable.
- Disconnect and reconnect restores correct state without affecting enforcement.
- Enrollment progress and system state are clearly communicated.

### Automation Split
- **Automatable:** all components, views, streaming client, tests, demo mode.
- **Human-required:** visual design judgement; confirming that alert distinction is genuinely unmistakable to a viewer.

### Risks
Effort drifting toward visual polish ahead of core pipeline work (mitigate: Section 17 priority ordering); dashboard becoming a hidden dependency of enforcement (mitigate: enforcement must be provably independent of dashboard state).

---

## PHASE 7 — Context-Aware Risk Engine

**Weeks:** 10–12
**Objective:** Handle naturally high-variance activity without ever disabling authentication.

### Entry Criteria
Risk engine operational; sufficient per-category data from Phase 3.

### Scope of Work

**7.1 — Bootstrap category map.** `config/app_categories.yaml` mapping processes to categories, with `UNKNOWN` defaulting to neutral.

**7.2 — Empirical variance layer (ADR-008).** Per-user, per-category variance of genuine risk scores computed from enrollment data; confidence weights derived from measured variance.

**7.3 — Layer arbitration.** Empirical layer supersedes bootstrap where sufficient observations exist; graceful fallback otherwise.

**7.4 — Confidence floor.** Hard minimum effective confidence — no application can reduce monitoring to zero (P8). Explicitly tested as an anti-evasion property.

**7.5 — Efficacy evaluation.** Measure whether context awareness reduces false positives in high-variance contexts **without increasing FAR**. If it does not, the added complexity must be reconsidered rather than retained on principle.

**7.6 — Dashboard integration.** Surface current category, applied confidence, and its effect on the decision.

### Key Decisions Resolved
ADR-008 layer weighting; confidence floor value; whether context awareness is retained (evidence-based).

### Deliverables
Category configuration; empirical variance module; arbitration logic; floor enforcement; efficacy report; dashboard integration.

### Exit Criteria
- The system handles context shifts without disabling authentication anywhere.
- Confidence floor verified by test — opening a high-variance application cannot suppress detection.
- Measured effect on FAR and FRR reported, with retention justified by evidence.

### Automation Split
- **Automatable:** implementation, variance computation, evaluation, dashboard integration.
- **Human-required:** curating the initial category map; deciding retention if the efficacy result is marginal.

### Risks
Insufficient per-category data for the empirical layer (mitigated by two-layer fallback); context becoming an evasion vector (mitigated by the floor and its explicit test).

---

## PHASE 8 — Model Update Manager

**Weeks:** 11–13
**Objective:** Allow profiles to adapt to genuine drift while making profile poisoning demonstrably hard.

### Entry Criteria
Sufficient multi-week data; verification anchors present in the corpus.

### Scope of Work

**8.1 — Promotion gate.** Implement gates G1–G6 (Section 12.1) at segment granularity.

**8.2 — Verification anchors.** Implement anchors A1–A3 and enforce the explicit rejection of A4 (Section 12.2).

**8.3 — Quarantine store.** `update_candidates` with ageing, incident-triggered discard, and audit trail.

**8.4 — Scheduled retraining.** Fixed-cadence retraining; never reactive to an individual session.

**8.5 — Pre-deployment validation.** Retrained models evaluated against held-out genuine and impostor data **before** replacing the active model; automatic rejection on regression beyond tolerance.

**8.6 — Versioning and rollback.** All model versions retained with metadata; one-step rollback.

**8.7 — Efficacy experiment E1 (Section 12.4).** Frozen vs updated profile performance over the collection period. Does FRR drift without updates, and does updating recover it without degrading FAR?

**8.8 — Poisoning experiment E2 (Section 12.4).** Verify the gate rejects injected impostor segments; then bypass the gate deliberately to quantify the contamination it prevents.

**8.9 — Dashboard integration.** Profile status, candidate queue, update history, rejected updates with reasons.

### Key Decisions Resolved
Retraining cadence; quarantine duration; `min_promotable_windows`; regression tolerance for automatic rejection.

### Deliverables
Promotion gate; anchor logic; quarantine store; scheduled retraining; pre-deployment validation; versioning and rollback; E1 and E2 experiment reports; dashboard integration.

### Exit Criteria
- Adaptation demonstrably works on genuine drift.
- Gate demonstrably rejects impostor data (E2).
- Measured benefit of updating quantified (E1) — the component's value is *shown*, not asserted.
- Rollback verified.

### Automation Split
- **Automatable:** entire implementation, both experiments, reporting, dashboard integration.
- **Human-required:** approving retraining cadence and quarantine policy as a security judgement.

### Risks
Too few qualifying segments to demonstrate anything (mitigated by anchor A3 during Phase 3 collection — **this is why A3 must be enabled early, not retrofitted**); insufficient longitudinal span to observe real drift (mitigated by starting collection in Week 2).

---

## PHASE 9 — Full System Integration

**Weeks:** 12–13
**Objective:** Operate as one continuous system under realistic and adverse conditions.

### Entry Criteria
All components individually complete and tested.

### Scope of Work

**9.1 — End-to-end assembly.** Full path: input → collector → IPC → ingestion → features → per-user models → fusion → context → smoothing → decision → persistence → WebSocket → dashboard.

**9.2 — Startup, shutdown and recovery.** Clean lifecycle; state recovery after restart of any component.

**9.3 — Multi-user validation.** Multiple enrolled profiles on one machine; correct profile selection; verified absence of cross-contamination.

**9.4 — Fault injection.** Systematic execution of every scenario in Section 13.6 using `tools/faultinjection/`.

**9.5 — Long-run stability.** Multi-hour and multi-day continuous operation; monitor for memory growth, clock drift, buffer accumulation, database growth, and log rotation correctness.

**9.6 — Configuration consolidation.** All parameters in `config/`; deployment documented and reproducible.

**9.7 — Installation package.** Reproducible setup for a clean machine — required for the pilot, the demo, and the evaluation.

### Deliverables
Integrated system; fault-injection results; long-run stability report; deployment documentation; installation package.

### Exit Criteria
- End-to-end MVP operates continuously and unattended on a real machine.
- All Section 13.6 scenarios produce specified behavior.
- Multi-day run shows no resource growth or degradation.
- System installs and runs on a clean machine from documentation alone.

### Automation Split
- **Automatable:** integration wiring, fault injection tooling and execution, monitoring instrumentation, packaging, documentation.
- **Human-required:** real-machine multi-day operation; observing subjective performance impact; clean-machine install verification.

### Risks
Integration defects surfacing late (mitigated by continuous integration from Phase 1 and self-collection dogfooding since Week 3).

---

## PHASE 10 — Evaluation: Accuracy, Performance and Security

**Weeks:** 13–15
**Objective:** Produce the measured evidence that constitutes the project's actual contribution.

### Entry Criteria
Frozen dataset (Phase 3.7); integrated system (Phase 9).

### Scope of Work

**10.1 — Threshold tuning.** Tune all thresholds on a **validation partition**, then **freeze them** for the evaluation. Tuning against evaluation data invalidates every number produced.

**10.2 — Accuracy evaluation.** FAR, FRR, EER, ROC/DET, at window level and decision level, per user and aggregate, on day-disjoint splits.

**10.3 — Zero-effort impostor evaluation (I1).** Complete cross-user matrix.

**10.4 — Informed impostor experiment (I2).** Controlled mimicry experiment with observation phase, executed live. Report N explicitly and frame results as indicative. **This is the differentiating result relative to the cited literature.**

**10.5 — Live hijack drill (Section 13.3).** Multiple A/B pairs across context types. Record detection latency and full risk traces. Produces the project's strongest single figure.

**10.6 — Runtime cost measurement (Section 13.4).** All metrics on real hardware during real use, reported as percentiles with machine specifications.

**10.7 — Model Update Manager experiments.** Execute E1 and E2 (Section 12.4) against the frozen corpus.

**10.8 — Robustness matrix.** Full Section 13.6 execution against the integrated system.

**10.9 — Statistical analysis (Section 13.5).** Per-user distributions, bootstrap confidence intervals, explicit statements of cohort size and limitations.

**10.10 — Comparison to literature.** Position results against the reviewed papers — including honest acknowledgement where published figures are higher, with reasons (dataset scale, controlled conditions, fixed-text protocols, different threat assumptions). **Published accuracy from a paper must never be presented as an expectation for this implementation.**

**10.11 — Results consolidation.** All figures, tables, and traces produced reproducibly from recorded configuration.

### Deliverables
Complete evaluation report: accuracy, impostor taxonomy results, hijack drill traces, runtime cost, update-manager efficacy, robustness matrix, statistical analysis, literature comparison, and a stated limitations section.

### Exit Criteria
- Every claim in the final report traces to a reproducible experiment.
- Thresholds frozen before evaluation and unchanged afterwards.
- All three required experiment classes complete: I1, I2, live hijack drill.
- Runtime cost reported with percentiles and hardware context.
- Limitations stated explicitly, including cohort size and dashboard co-location.

### Automation Split
- **Automatable:** tuning harness, all offline evaluation, statistical analysis, figure generation, report assembly.
- **Human-required:** **the live hijack drill and the informed-impostor experiment** — both require human participants performing physical takeover and mimicry; runtime measurement on real hardware; interpretation and framing of results.

### Risks
Results weaker than hoped (mitigate: honest reporting with analysis of *why* is a legitimate and defensible outcome — an overclaimed result is not); insufficient time for live experiments (mitigate: schedule them first in Phase 10, since they cannot be compressed).

---

## PHASE 11 — Optimisation and Academic Deliverables

**Weeks:** 16
**Objective:** Consolidate into a defensible, reproducible, well-documented project.

### Scope of Work

**11.1 — Targeted optimisation.** Address measured bottlenecks only. No speculative optimisation.
**11.2 — Code and documentation finalisation.** Clean codebase, complete READMEs, ADR set, protocol documentation, deployment guide.
**11.3 — Architecture diagrams.** Final pipeline, component, data-flow, and state-machine diagrams.
**11.4 — Project report.** Introduction, literature review, problem statement, methodology, architecture, implementation, evaluation, results, discussion, limitations, future work, references.
**11.5 — Final presentation.** Narrative built around: the gap, the architecture, the live demonstration, the measured results, honest limitations.
**11.6 — Live demo preparation.** Rehearsed end-to-end demonstration including a live or recorded hijack drill, with fallback if live conditions fail.
**11.7 — Reproducibility package.** Configuration, experiment records, and instructions sufficient for another party to reproduce reported results.
**11.8 — Individual contribution records.**

### Exit Criteria
Report complete; demo rehearsed with fallback; every result reproducible; limitations documented; repository clean and navigable.

### Automation Split
- **Automatable:** documentation generation, diagrams, figures, report drafting, reproducibility packaging.
- **Human-required:** narrative and framing decisions; demo rehearsal; contribution records; final academic judgement.

---

## 17.1 Priority Ordering Under Schedule Pressure

If the project falls behind, protect capability in this order. The core contribution is the **live, machine-wide pipeline and its measured evaluation** — not visual polish.

```
1. Reliable, correctly-timestamped event collection      (nothing works without this)
2. Reliable content-free feature extraction
3. Data collection volume and day coverage               (cannot be recovered later)
4. Working per-user models with day-disjoint evaluation
5. Working risk engine with graded, smoothed decisions
6. End-to-end integration
7. Core evaluation: FAR/FRR, runtime cost, hijack drill
8. Informed impostor experiment
9. Context-aware risk engine
10. Model Update Manager
11. Dashboard completeness
12. Dashboard polish and stretch features
```

Items 1–3 are irrecoverable if deferred: a timestamping defect corrupts everything downstream, and collection days cannot be manufactured retroactively. Items 11–12 can be reduced without weakening the research claim.

---

# 18. Milestones

Track milestones, not calendar weeks. Each has a **verifiable** criterion.

| ID | Milestone | Verifiable Criterion |
| --- | --- | --- |
| M0 | Platform Feasibility Proven | Global input capture demonstrated on the target OS across focus changes; ADR-001 resolved |
| M1 | Foundation Complete | All components build and pass CI including guardrail checks; `protocol/` frozen; synthetic generator working |
| M2 | Event Collection Trustworthy | Real input → validated structured events; **timestamp fidelity test passes**; zero drops at typical rates; no keycode can cross IPC |
| M3 | Feature Pipeline Complete | Events → validated deterministic feature matrix; idle windows correctly gated; retention working |
| M4 | Collection Underway | Team self-collection running continuously; pilot consent obtained; first participants collecting |
| M5 | ML Baseline Complete | Per-user profiles score live windows; day-disjoint FAR/FRR reported; fusion ablation, baseline comparison, and enrollment-length experiment complete |
| M6 | Decision Layer Complete | Live input → graded, smoothed, audited decision; single anomalous window provably cannot enforce; shadow mode operational |
| M7 | Live Monitoring Complete | Dashboard reflects live state; availability vs behavioral alerts unmistakably distinct |
| M8 | End-to-End MVP | Full pipeline runs continuously and unattended on a real machine through a multi-hour session |
| M9 | Adaptation and Context Complete | Context confidence with enforced floor; Model Update Manager with demonstrated poisoning rejection |
| M10 | Data Freeze | Corpus frozen with documented day-disjoint splits and per-participant coverage |
| M11 | Evaluation Complete | FAR/FRR/EER, I1 and I2 impostor results, live hijack traces, runtime-cost percentiles, update-manager efficacy — all reproducible |
| M12 | Final System | Report, rehearsed demo with fallback, reproducibility package, clean repository |

---

# 19. AI-Agent Execution Model

This project is executed with automated agents performing the large majority of implementation work under human direction and review. The plan is structured accordingly: **specification-first, contract-driven, and guardrailed**, so that automated implementation is fast where it is safe and structurally prevented where it is not.

## 19.1 Working Method

**Specification before implementation.** Every phase entry requires the relevant contracts (`protocol/`), configuration schema, and acceptance criteria to exist first. Agents implement against a specification; they do not infer architecture from prose.

**Contract-driven.** `protocol/` is the single source of truth. Interface changes happen there and propagate by code generation. This prevents the most common multi-agent failure mode — two components drifting apart because each independently interpreted an interface.

**Test-first for critical paths.** For the collector, feature extraction, risk engine, and update manager, acceptance tests are written before implementation. These four components are where silent defects propagate furthest, and a passing test suite is the only reliable signal that generated code is correct.

**Small, verifiable units of work.** Tasks are scoped so success is machine-checkable: "implement feature X per the dictionary and pass its property tests," not "improve feature extraction."

**Deterministic acceptance criteria.** Each phase's exit criteria in Section 17 are written to be objectively verifiable, precisely so that completion is not a matter of opinion.

**Synthetic data first.** Every component is developed and tested against the synthetic generator (Section 9.5). Agents are never blocked on human data collection, and **real participant data is never used as a development fixture.**

## 19.2 Review Gates

Automated implementation does not remove the need for human judgement. Human review is mandatory at:

| Gate | What Requires Human Sign-Off |
| --- | --- |
| Architectural | Any change touching Section 4 principles or any ADR |
| Privacy | Any change to the event schema, storage schema, or collector data handling |
| Security | Risk thresholds, escalation policy, failure policy, promotion gate |
| Data | Consent materials, collection protocol, dataset freeze |
| Results | Interpretation and framing of every reported metric |
| Academic | Report content, claims, and limitations |

Generated code is reviewed by at least one team member before merge. For the four critical components, review by someone other than the task's requester is required.

## 19.3 Automated Guardrails

These run in CI on every change and **fail the build on violation**. They encode the Section 4 principles as executable checks, so that a principle cannot be violated by an inattentive change — human or automated.

| Guardrail | Enforces |
| --- | --- |
| No keycode or character field in any schema, model, or database column | P4, ADR-004 |
| No window title, document name, URL, or clipboard access in collector source | P4 |
| No screen-capture, OCR, or accessibility-API dependency anywhere | P1, P4 |
| No feature derived from absolute wall-clock time-of-day or day-of-week | P10, ADR-010 |
| No application-conditional branch in model selection or training code | P2, P3 |
| No code path that disables monitoring or sets effective confidence to zero | P8 |
| No training data path that bypasses the promotion gate | P7 |
| Feature extraction imported from a single shared module by both training and inference | Training/inference parity |
| No hardcoded threshold, weight, or window size outside `config/` | Empirical tunability |
| No file under `data/` tracked by git | Participant privacy |
| Model refuses to load on feature-schema version mismatch | Silent-drift prevention |

Guardrails are implemented in Phase 0 — **before** the code they protect exists.

## 19.4 Work That Cannot Be Automated

These require human execution and must be explicitly scheduled. They are the project's true critical path, because they cannot be accelerated by adding automation.

| Activity | Phase | Note |
| --- | --- | --- |
| Running the collector on real hardware; granting OS permissions | 0, 1 | Physical machine access required |
| Judging platform viability from the spike | 0 | Architectural judgement |
| Participant recruitment | 0, 3 | **Longest lead time in the project** |
| Informed consent and participant briefing | 3 | Ethical requirement; cannot be delegated |
| Installation support on participant machines | 3 | Human troubleshooting |
| Natural-use data collection | 3 | The data *is* human behavior |
| Live session hijack drill | 10 | Requires physical takeover by a second person |
| Informed impostor experiment | 10 | Requires human observation and mimicry |
| Subjective performance impact assessment | 9, 10 | "Does it feel slow?" is not measurable in CI |
| Result interpretation and framing | 10, 11 | Academic judgement |
| Demo rehearsal | 11 | Human performance |
| Faculty review and guide consultation | All | — |

## 19.5 Agent Constraints

Recorded in `AGENTS.md` at the repository root:

1. Never introduce an application-specific model, integration, or code path.
2. Never persist or transmit typed content, keycodes, window titles, or screen data.
3. Never add time-of-day or day-of-week as an authentication feature.
4. Never create a code path that disables monitoring.
5. Never add training data outside the promotion gate.
6. Never hardcode a tunable parameter outside `config/`.
7. Never commit participant data.
8. Never present published accuracy from a paper as an expected result for this system.
9. Never modify an ADR without explicit human instruction.
10. When a requirement is ambiguous, stop and ask rather than assume — an assumption silently embedded in generated code is far more expensive to find than a question asked up front.

---

# 20. Risk Register and Change Management

## 20.1 Risk Register

| ID | Risk | Impact | Likelihood | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Global input hooking restricted on target platform | Critical | Medium | Phase 0 gating spike; ADR-001; platform abstraction interface |
| R2 | **Timestamps assigned downstream, silently corrupting all timing features** | **Critical** | Medium | ADR-003; gating timestamp fidelity test in Phase 1 |
| R3 | Insufficient distinct collection days by freeze date | Critical | **High** | Start collection Week 2; over-recruit; weekly coverage monitoring |
| R4 | Participant attrition or inconsistent collection | High | High | Over-recruit; trivial installation; pause control; coverage dashboard |
| R5 | Overfitting concealed by random splitting | High | Medium | Mandatory day-disjoint protocol; random split only as labelled contrast |
| R6 | Missing modality read as anomaly | High | Medium | ADR-005 quality gating; ADR-006 dual-model fusion |
| R7 | Device change confounding identity | Medium | Medium | ADR-009 metadata + normalisation; consistency request |
| R8 | Too few qualifying segments for update manager experiments | Medium | Medium | Verification anchor A3 enabled during Phase 3 collection |
| R9 | Thresholds tuned against evaluation data | High | Medium | Validation partition; Phase 3 freeze; thresholds frozen before Phase 10 |
| R10 | Results weaker than published literature | Medium | High | Framed as expected; honest comparison with reasons (Section 10.10) |
| R11 | Integration defects surfacing late | High | Low | Continuous integration from Phase 1; self-collection dogfooding from Week 3 |
| R12 | Effort drifting to dashboard polish | Medium | Medium | Priority ordering (17.1) |
| R13 | Small-N results overstated | Medium | Medium | Statistical honesty requirements (13.5) |
| R14 | Automated implementation silently violating a core principle | High | Medium | CI guardrails (19.3) implemented in Phase 0 |
| R15 | Context awareness becoming an evasion vector | High | Low | Confidence floor with explicit anti-evasion test (7.4) |

## 20.2 Change Management

```
PLAN v2.0  →  v2.1  →  v2.2  →  PLAN v3.0
```

**Minor revision (v2.x):** adding or removing a feature; adjusting a threshold; shifting a phase boundary; refining a UI decision; confirming a Provisional ADR through experiment.

**Major revision (v3.0):** replacing the baseline model family; changing the IPC architecture; altering any Section 4 principle; adding a behavioral modality; changing target platform; changing the evaluation methodology.

**Every change record must state:** current assumption → new evidence → decision → resulting plan update. Recorded in the Document Control table and, for architectural changes, in `docs/adr/`.

**Weekly review cadence:** completed work; blockers; next tasks; **integration status** ("can the components still talk to each other?"); **collection health** ("are we on track for the freeze?"); plan changes. Collection health is a standing agenda item because it is the one thing that cannot be recovered by working harder later.

---

# 21. Open Decisions

Deliberately unresolved. Each is to be closed by evidence, and each has a named phase where it will be answered.

| ID | Open Question | Resolved In |
| --- | --- | --- |
| O1 | Public dataset selection (free-text preferred) | Phase 0/3 |
| O2 | Final pilot cohort size and composition | Phase 3 |
| O3 | Minimum enrollment duration and `min_windows` / `min_distinct_days` | Phase 4 (experiment 10.5) |
| O4 | Quality-gate thresholds (`min_keystrokes`, `min_mouse_samples`) | Phase 2 |
| O5 | Final feature set after importance analysis | Phase 4 |
| O6 | Fusion strategy confirmation (ADR-006) | Phase 4 ablation |
| O7 | Fusion weights between modalities | Phase 4 |
| O8 | Risk thresholds for LOW/MEDIUM/HIGH | Phase 5 provisional, Phase 10 final |
| O9 | Smoothing parameters (EWMA α, K, N) | Phase 5, tuned Phase 10 |
| O10 | Retraining cadence and quarantine duration | Phase 8 |
| O11 | Whether context awareness is retained | Phase 7 efficacy result |
| O12 | Whether an alternative model replaces Isolation Forest | Phase 4 baseline comparison |
| O13 | Secondary platform support (Linux/X11) | Phase 9, effort permitting |
| O14 | Whether interaction-dynamics joint features are added | Phase 7+, if ablation justifies |

**Nothing in this table should be answered by assumption, by a published paper's value, or by convenience.** Each is answerable by an experiment defined in this plan.

---

# 22. Glossary

| Term | Definition |
| --- | --- |
| **Continuous authentication** | Ongoing verification that the current operator remains the enrolled user, as opposed to one-time login authentication |
| **Behavioral biometrics** | Identification based on patterns of behavior (typing rhythm, mouse motion) rather than physiological traits |
| **Dwell time** | Duration a key is held down |
| **Flight time** | Interval from key release to the next key press |
| **Digraph latency** | Timing between two consecutive keys; here realised as class-transition latency (ADR-004) |
| **Feature window** | A short interval of activity summarised as a fixed-length vector of statistics |
| **Quality label** | Classification of a window by available modality evidence (ADR-005) |
| **Isolation Forest** | Tree-based anomaly detection algorithm that isolates outliers; used one-class per user |
| **One-class model** | A model trained only on normal data, without counter-examples |
| **Calibration** | Conversion of raw model scores into a per-user comparable scale |
| **FAR** | False Acceptance Rate — impostor activity wrongly accepted |
| **FRR** | False Rejection Rate — genuine activity wrongly rejected |
| **EER** | Equal Error Rate — the operating point where FAR equals FRR |
| **Day-disjoint split** | Training and evaluation drawn from entirely different calendar days |
| **Zero-effort impostor** | An impostor behaving naturally, making no attempt to imitate the target |
| **Informed impostor** | An impostor who has observed the target and attempts mimicry |
| **Temporal smoothing** | Requiring sustained evidence across windows before escalating |
| **Hysteresis** | Asymmetric escalation/de-escalation thresholds preventing oscillation |
| **Context confidence** | A modifier scaling trust in the risk score based on activity type (ADR-008) |
| **Behavioral drift** | Gradual genuine change in a user's behavior over time |
| **Profile poisoning** | An attack in which impostor behavior is absorbed into the legitimate profile |
| **Promotion gate** | The set of conditions data must satisfy before entering training (Section 12.1) |
| **Verification anchor** | An independent identity confirmation event supporting promotion (Section 12.2) |
| **Shadow mode** | Decisions computed and logged but not enforced (`CALIBRATING` state) |
| **Fail-open** | On component failure, do not restrict the user; alert loudly instead (ADR-011) |
| **Session / Segment** | A login-bounded period; segments split on idle boundaries (ADR-007) |
| **Provenance** | The origin of a data record: synthetic, public, team, or pilot (Section 9.6) |
| **Guardrail** | An automated CI check enforcing a core architectural principle (Section 19.3) |

---

## Closing Note

This plan is a **working baseline**, not a contract. Its purpose is to make the project's assumptions explicit enough to be tested, and its unknowns explicit enough to be answered by evidence rather than closed by convenience.

The measure of a good outcome is not that every number in the final report is impressive. It is that every number is **reproducible, honestly bounded, and produced by a system that actually ran continuously on a real machine.**

**Plan v2.0 — Working Baseline. Subject to revision through the process in Section 20.**
