import { dashboardConfig } from "../config";
import type {
  AlertRecord,
  CurrentState,
  Health,
  Metrics,
  Profile,
  RiskDecision,
  StreamSnapshot,
  UpdateCandidate,
  WebSocketEnvelope,
} from "../protocol";

export type Connectivity =
  "CONNECTING" | "ONLINE" | "STALE" | "OFFLINE" | "REPLAY";

export interface DashboardState {
  readonly current: CurrentState | null;
  readonly alerts: readonly AlertRecord[];
  readonly decisions: readonly RiskDecision[];
  readonly health: Health | null;
  readonly metrics: Metrics | null;
  readonly profiles: readonly Profile[];
  readonly updates: readonly UpdateCandidate[];
  readonly connectivity: Connectivity;
  readonly lastSequence: number;
  readonly lastMessageAt: number | null;
  readonly error: string | null;
}

export const initialDashboardState: DashboardState = {
  current: null,
  alerts: [],
  decisions: [],
  health: null,
  metrics: null,
  profiles: [],
  updates: [],
  connectivity: "CONNECTING",
  lastSequence: -1,
  lastMessageAt: null,
  error: null,
};

export type DashboardAction =
  | {
      readonly type: "LOADED";
      readonly value: Omit<
        DashboardState,
        "connectivity" | "lastSequence" | "lastMessageAt" | "error"
      >;
    }
  | {
      readonly type: "STREAM";
      readonly envelope: WebSocketEnvelope;
      readonly receivedAt: number;
    }
  | { readonly type: "CONNECTING" }
  | { readonly type: "OFFLINE"; readonly error: string }
  | { readonly type: "STALE" }
  | { readonly type: "REPLAY" }
  | { readonly type: "ACKNOWLEDGED"; readonly alertId: string };

function appendDecision(
  decisions: readonly RiskDecision[],
  decision: RiskDecision,
): readonly RiskDecision[] {
  return [...decisions, decision].slice(-dashboardConfig.timeline_limit);
}

export function dashboardReducer(
  state: DashboardState,
  action: DashboardAction,
): DashboardState {
  switch (action.type) {
    case "LOADED":
      return { ...state, ...action.value, error: null };
    case "CONNECTING":
      return { ...state, connectivity: "CONNECTING" };
    case "OFFLINE":
      return { ...state, connectivity: "OFFLINE", error: action.error };
    case "STALE":
      return state.connectivity === "ONLINE"
        ? { ...state, connectivity: "STALE" }
        : state;
    case "REPLAY":
      return state.connectivity === "REPLAY"
        ? state
        : {
            ...state,
            connectivity: "REPLAY",
            decisions: [],
            lastSequence: -1,
            lastMessageAt: null,
            error: null,
          };
    case "ACKNOWLEDGED":
      return {
        ...state,
        alerts: state.alerts.map((alert) =>
          alert.alert_id === action.alertId
            ? { ...alert, acknowledged: true }
            : alert,
        ),
      };
    case "STREAM": {
      const { envelope } = action;
      if (envelope.stream_seq <= state.lastSequence) return state;
      if (
        state.lastSequence >= 0 &&
        envelope.stream_seq > state.lastSequence + 1
      ) {
        return {
          ...state,
          connectivity: "STALE",
          error: "Live update gap detected; resynchronizing.",
        };
      }
      const base: DashboardState = {
        ...state,
        connectivity: state.connectivity === "REPLAY" ? "REPLAY" : "ONLINE",
        lastSequence: envelope.stream_seq,
        lastMessageAt: action.receivedAt,
        error: null,
      };
      if (envelope.event_type === "SNAPSHOT") {
        const snapshot = envelope.payload as StreamSnapshot;
        return {
          ...base,
          current: snapshot.current_state,
          alerts: snapshot.recent_alerts,
          health: snapshot.health,
          lastSequence: snapshot.last_stream_seq,
        };
      }
      if (envelope.event_type === "RISK") {
        const decision = envelope.payload as RiskDecision;
        return {
          ...base,
          decisions: appendDecision(base.decisions, decision),
          current: base.current
            ? {
                ...base.current,
                user_id: decision.user_id,
                user_state: decision.user_state,
                risk_level: decision.risk_level,
                protection_available:
                  decision.user_state === "ACTIVE" &&
                  decision.risk_level !== "UNAVAILABLE" &&
                  !decision.shadow_mode,
              }
            : base.current,
        };
      }
      if (envelope.event_type === "ALERT") {
        return {
          ...base,
          alerts: [envelope.payload as AlertRecord, ...base.alerts],
        };
      }
      if (envelope.event_type === "STATE") {
        return { ...base, current: envelope.payload as CurrentState };
      }
      if (envelope.event_type === "HEALTH") {
        return { ...base, health: envelope.payload as Health };
      }
      return base;
    }
  }
}
