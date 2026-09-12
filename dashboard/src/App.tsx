import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  acknowledgeAlert,
  loadChallengeStatus,
  loadCollectionProvenance,
  loadEnforcementStatus,
  login,
  logout,
  rollbackProfile,
} from "./api/client";
import type {
  ChallengeStatus,
  CollectionProvenance,
  EnforcementStatus,
} from "./challenge";
import { blocksConsole, isScheduledVerification } from "./challenge";
import { ProtectionBanner } from "./components/ProtectionBanner";
import { dashboardConfig } from "./config";
import { useDashboard } from "./hooks/useDashboard";
import type { RiskDecision, WebSocketEnvelope } from "./protocol";
import type { DashboardAction } from "./state/dashboard";
import { AlertsView } from "./views/AlertsView";
import { ChallengeResponseView } from "./views/ChallengeResponseView";
import { ChallengeSetupView } from "./views/ChallengeSetupView";
import { HealthView } from "./views/HealthView";
import { HistoryView } from "./views/HistoryView";
import { LiveView } from "./views/LiveView";
import { OverviewView } from "./views/OverviewView";
import { ProfilesView } from "./views/ProfilesView";
import { SettingsView } from "./views/SettingsView";

type View =
  | "overview"
  | "live"
  | "alerts"
  | "history"
  | "health"
  | "profiles"
  | "settings";

const views: readonly { readonly id: View; readonly label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "live", label: "Live" },
  { id: "alerts", label: "Alerts" },
  { id: "history", label: "History" },
  { id: "health", label: "System health" },
  { id: "profiles", label: "Profiles" },
  { id: "settings", label: "Settings" },
];

function replayTrace(dispatch: React.Dispatch<DashboardAction>): () => void {
  dispatch({ type: "REPLAY" });
  const trace = [
    { score: 0.12, level: "LOW", action: "CONTINUE" },
    { score: 0.18, level: "LOW", action: "CONTINUE" },
    { score: 0.31, level: "LOW", action: "CONTINUE" },
    { score: 0.58, level: "MEDIUM", action: "SOFT_CHALLENGE" },
    { score: 0.79, level: "HIGH", action: "REAUTH" },
    { score: 0.91, level: "HIGH", action: "REAUTH" },
  ] as const;
  const timers = trace.map(({ score, level, action }, index) =>
    window.setTimeout(() => {
      const decision: RiskDecision = {
        schema_version: "1.0.0",
        decision_id: `synthetic-replay-${index}`,
        user_id: "synthetic-demo-user",
        session_id: "synthetic-demo-session",
        segment_id: "synthetic-demo-segment",
        window_id: `synthetic-window-${index}`,
        t_decision_us: index,
        quality_label: "FULL",
        fused_score: score,
        context_confidence: 1,
        confidence_source: "NEUTRAL_FALLBACK",
        smoothed_score: score,
        risk_level: level,
        user_state: "ACTIVE",
        action,
        reason_code: "SYNTHETIC_DEMO_REPLAY",
        threshold_config_version: "synthetic-replay",
        config_checksum: "0".repeat(64),
        shadow_mode: true,
        enforcement_applied: false,
      };
      const envelope: WebSocketEnvelope = {
        schema_version: "1.0.0",
        stream_seq: index,
        event_type: "RISK",
        emitted_at: new Date().toISOString(),
        payload: decision,
      };
      dispatch({ type: "STREAM", envelope, receivedAt: Date.now() });
      dispatch({ type: "REPLAY" });
    }, index * dashboardConfig.replay_step_ms),
  );
  return () => timers.forEach((timer) => window.clearTimeout(timer));
}

function LoginView({
  onAuthenticated,
}: {
  readonly onAuthenticated: () => void;
}) {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(secret);
      setSecret("");
      onAuthenticated();
    } catch {
      setError("Authentication failed. Check the local dashboard secret.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark">CA</div>
        <h1>Continuous Authentication</h1>
        <p>This console is local to the monitored endpoint.</p>
        <label htmlFor="local-secret">Local dashboard secret</label>
        <input
          id="local-secret"
          type="password"
          autoComplete="current-password"
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
          required
        />
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" disabled={busy}>
          {busy ? "Authenticating…" : "Open console"}
        </button>
      </form>
    </main>
  );
}

export function App() {
  const [authenticated, setAuthenticated] = useState(true);
  const [view, setView] = useState<View>("overview");
  const [replay, setReplay] = useState(false);
  const [challenge, setChallenge] = useState<ChallengeStatus | null>(null);
  const [enforcement, setEnforcement] = useState<EnforcementStatus | null>(
    null,
  );
  const [provenance, setProvenance] = useState<CollectionProvenance | null>(
    null,
  );
  const stopReplay = useRef<(() => void) | null>(null);
  const onUnauthorized = useCallback(() => setAuthenticated(false), []);
  // Threaded into useDashboard rather than only into the render, so that
  // clearing the posture tears the hook's effect down and back up: the
  // stream reconnects and protected state is re-read the moment the backend
  // starts answering again. Without this, a recovered session would show a
  // permanently disconnected console until the page was reloaded.
  const blocked = blocksConsole(enforcement);
  const { state, dispatch } = useDashboard(
    authenticated && !replay && !blocked,
    onUnauthorized,
  );

  const refreshChallenge = useCallback(async () => {
    try {
      // Both of these stay reachable while enforcement is blocking -- they
      // are how a blocked session learns what it must do. Everything else is
      // fetched separately below, because the backend refuses protected
      // routes once the posture blocks and a combined Promise.all would then
      // throw away the enforcement status too.
      const [status, enforcementStatus] = await Promise.all([
        loadChallengeStatus(),
        loadEnforcementStatus(),
      ]);
      setChallenge(status);
      setEnforcement(enforcementStatus);
    } catch {
      // A challenge surface that cannot be read must not blank the console;
      // the setup gate below stays closed until a status actually arrives.
      setChallenge(null);
      return;
    }
    try {
      setProvenance(await loadCollectionProvenance());
    } catch {
      // A protected read; expected to fail while enforcement blocks.
    }
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    void refreshChallenge();
    // Enforcement state is owned by the backend and changes without any
    // action from this console -- the risk engine escalates on its own, and
    // a challenge can expire while nobody is looking. Polling is what makes
    // a dispatched challenge appear at all; the WebSocket carries risk and
    // alert events, not the enforcement posture, and is itself closed once
    // the posture blocks.
    const timer = window.setInterval(
      () => void refreshChallenge(),
      dashboardConfig.enforcement_poll_ms,
    );
    return () => window.clearInterval(timer);
  }, [authenticated, refreshChallenge]);

  if (!authenticated)
    return <LoginView onAuthenticated={() => setAuthenticated(true)} />;
  if (challenge !== null && !challenge.configured && !replay)
    return <ChallengeSetupView onConfigured={() => void refreshChallenge()} />;
  // A blocking posture replaces the console rather than decorating it. This
  // is presentation only: the backend is already refusing every protected
  // route and has closed the live stream, so nothing here is what keeps the
  // session blocked, and dismissing it would reveal nothing.
  if (!replay && blocked && enforcement !== null)
    return (
      <main className="login-shell">
        <ChallengeResponseView
          enforcement={enforcement}
          onResolved={() => void refreshChallenge()}
        />
      </main>
    );
  async function signOut() {
    try {
      await logout();
    } finally {
      setAuthenticated(false);
    }
  }
  function toggleReplay() {
    stopReplay.current?.();
    if (replay) {
      setReplay(false);
      return;
    }
    setReplay(true);
    stopReplay.current = replayTrace(dispatch);
    setView("live");
  }
  async function acknowledge(alertId: string) {
    if (!replay) await acknowledgeAlert(alertId);
    dispatch({ type: "ACKNOWLEDGED", alertId });
  }
  async function rollback(userId: string) {
    if (
      window.confirm(
        "Activate the immediately previous retained model profile?",
      )
    )
      await rollbackProfile(userId);
  }
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <span className="brand-mark small">CA</span>
          <div>
            <h1>Continuous Authentication</h1>
            <p>Local assurance console</p>
          </div>
        </div>
        <div className="header-actions">
          {provenance && (
            <span
              className={
                provenance.collection_provenance === "PILOT"
                  ? "provenance-badge provenance-badge--pilot"
                  : "provenance-badge provenance-badge--synthetic"
              }
            >
              Recording: {provenance.collection_provenance}
            </span>
          )}
          <button
            className={replay ? "active" : "secondary"}
            onClick={toggleReplay}
          >
            {replay ? "Exit demo replay" : "Demo replay"}
          </button>
          <button className="secondary" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <ProtectionBanner state={state} />
      {enforcement?.pending_challenge ? (
        <>
          <p
            className={
              isScheduledVerification(enforcement.pending_challenge.decision_id)
                ? "challenge-notice challenge-notice--scheduled"
                : "challenge-notice"
            }
            role="status"
          >
            {isScheduledVerification(
              enforcement.pending_challenge.decision_id,
            ) ? (
              <>
                <strong>Routine verification</strong> · A periodic check that
                confirms it is you. Nothing is wrong.
              </>
            ) : (
              <>
                Identity verification in progress ·{" "}
                {enforcement.pending_challenge.action} · answer it here, or on
                the native prompt on this desktop. Both reach the same backend
                check.
              </>
            )}
          </p>
          <ChallengeResponseView
            enforcement={enforcement}
            onResolved={() => void refreshChallenge()}
          />
        </>
      ) : null}
      <div className="workspace">
        <nav aria-label="Dashboard views">
          {views.map((item) => (
            <button
              key={item.id}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <main className="view-panel">
          {view === "overview" ? <OverviewView state={state} /> : null}
          {view === "live" ? <LiveView state={state} /> : null}
          {view === "alerts" ? (
            <AlertsView state={state} onAcknowledge={acknowledge} />
          ) : null}
          {view === "history" ? <HistoryView state={state} /> : null}
          {view === "health" ? <HealthView state={state} /> : null}
          {view === "profiles" ? (
            <ProfilesView state={state} onRollback={rollback} />
          ) : null}
          {view === "settings" ? (
            <SettingsView
              challenge={challenge}
              enforcement={enforcement}
              onChanged={() => void refreshChallenge()}
            />
          ) : null}
        </main>
      </div>
      <footer>
        Protocol 1.0.0 · Dashboard co-located with the monitored endpoint;
        production consoles should be separated.
      </footer>
    </div>
  );
}
