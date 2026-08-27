export const PROTOCOL_VERSION = "1.0.0" as const;

export type UserState = "ENROLLING" | "CALIBRATING" | "ACTIVE" | "DEGRADED" | "SUSPENDED";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "UNAVAILABLE";
export type AlertType = "BEHAVIORAL" | "AVAILABILITY" | "TAMPER";
export type StreamEventType = "RISK" | "ALERT" | "STATE" | "HEALTH" | "SNAPSHOT";

export interface CurrentState {
  readonly schema_version: typeof PROTOCOL_VERSION;
  readonly user_id: string | null;
  readonly user_state: UserState;
  readonly risk_level: RiskLevel;
  readonly protection_available: boolean;
  readonly last_check_at: string;
}

export interface RiskDecision {
  readonly decision_id: string;
  readonly user_id: string;
  readonly window_id: string;
  readonly t_decision_us: number;
  readonly quality_label: "FULL" | "KBD_ONLY" | "MOUSE_ONLY" | "INSUFFICIENT_DATA";
  readonly fused_score: number | null;
  readonly context_confidence: number;
  readonly smoothed_score: number | null;
  readonly risk_level: RiskLevel;
  readonly user_state: UserState;
  readonly action: "CONTINUE" | "SOFT_CHALLENGE" | "REAUTH" | "TERMINATE" | "NONE";
  readonly reason_code: string;
  readonly enforcement_applied: boolean;
}

export interface AlertRecord {
  readonly alert_id: string;
  readonly alert_type: AlertType;
  readonly severity: "LOW" | "MEDIUM" | "HIGH";
  readonly code: string;
  readonly occurred_at: string;
  readonly acknowledged: boolean;
}

export interface Health {
  readonly status: "HEALTHY" | "DEGRADED" | "UNAVAILABLE";
  readonly components: Readonly<Record<string, "HEALTHY" | "DEGRADED" | "UNAVAILABLE">>;
  readonly heartbeat_age_ms: number | null;
  readonly collection_paused: boolean;
}

export interface Metrics {
  readonly collector_cpu_percent: number | null;
  readonly collector_memory_bytes: number | null;
  readonly dropped_events: number;
  readonly latency_us: Readonly<Record<string, number>>;
}

export interface Profile {
  readonly user_id: string;
  readonly user_state: UserState;
  readonly enrollment_windows: number;
  readonly distinct_days: number;
  readonly model_version: string | null;
}

export interface UpdateCandidate {
  readonly candidate_id: string;
  readonly user_id: string;
  readonly segment_id: string;
  readonly verification_anchor: "A1_LOGIN_UNLOCK" | "A2_REAUTH" | "A3_SCHEDULED_PROMPT" | null;
  readonly quarantined_at: string;
  readonly incident_recorded: boolean;
  readonly disposition: "QUARANTINED" | "ELIGIBLE" | "REJECTED" | "PROMOTED" | "INVALIDATED";
  readonly reason_code: string;
}

export interface StreamSnapshot {
  readonly current_state: CurrentState;
  readonly recent_alerts: readonly AlertRecord[];
  readonly health: Health;
  readonly last_stream_seq: number;
}

export interface WebSocketEnvelope {
  readonly schema_version: typeof PROTOCOL_VERSION;
  readonly stream_seq: number;
  readonly event_type: StreamEventType;
  readonly emitted_at: string;
  readonly payload: RiskDecision | AlertRecord | CurrentState | Health | StreamSnapshot;
}

export interface Page<T> {
  readonly items: readonly T[];
  readonly page: { readonly next_cursor: string | null; readonly has_more: boolean };
}

export interface ApiError {
  readonly code: string;
  readonly message: string;
  readonly correlation_id: string;
}

export interface FoundationStatus {
  readonly protocolVersion: typeof PROTOCOL_VERSION;
  readonly protectionStatus: "INITIALIZING";
}

export function foundationStatus(): FoundationStatus {
  return { protocolVersion: PROTOCOL_VERSION, protectionStatus: "INITIALIZING" };
}

export function isEnvelope(value: unknown): value is WebSocketEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return (
    item.schema_version === PROTOCOL_VERSION &&
    typeof item.stream_seq === "number" &&
    typeof item.event_type === "string" &&
    typeof item.payload === "object" &&
    item.payload !== null
  );
}
