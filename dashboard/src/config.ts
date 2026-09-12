import source from "../../config/dashboard.development.json";

export interface DashboardConfig {
  readonly config_version: string;
  readonly reconnect_initial_ms: number;
  readonly reconnect_max_ms: number;
  readonly stale_after_ms: number;
  readonly timeline_limit: number;
  readonly replay_step_ms: number;
  /** How often the console re-reads the backend enforcement posture. */
  readonly enforcement_poll_ms: number;
}

export const dashboardConfig: DashboardConfig = source;
