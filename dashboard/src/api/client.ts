import type {
  AlertRecord,
  ApiError,
  CurrentState,
  Health,
  Metrics,
  Page,
  Profile,
  RiskDecision,
  UpdateCandidate,
} from "../protocol";

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly correlationId: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const response = await fetch(endpoint, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  const body = (await response.json()) as T | ApiError;
  if (!response.ok) {
    const error = body as ApiError;
    throw new ApiFailure(
      response.status,
      error.code,
      error.correlation_id,
      error.message,
    );
  }
  return body as T;
}

export async function login(localSecret: string): Promise<void> {
  await request("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ local_secret: localSecret }),
  });
}

export async function logout(): Promise<void> {
  await request("/v1/auth/logout", { method: "POST" });
}

export async function loadDashboard() {
  const [state, alerts, decisions, health, metrics, profiles, updates] =
    await Promise.all([
      request<CurrentState>("/v1/state"),
      request<Page<AlertRecord>>("/v1/alerts"),
      request<Page<RiskDecision>>("/v1/history/decisions"),
      request<Health>("/v1/health"),
      request<Metrics>("/v1/metrics"),
      request<Page<Profile>>("/v1/profiles"),
      request<Page<UpdateCandidate>>("/v1/updates"),
    ]);
  return {
    state,
    alerts: [...alerts.items],
    decisions: [...decisions.items].reverse(),
    health,
    metrics,
    profiles: [...profiles.items],
    updates: [...updates.items],
  };
}

export async function acknowledgeAlert(alertId: string): Promise<void> {
  await request(`/v1/alerts/${encodeURIComponent(alertId)}/acknowledge`, {
    method: "POST",
  });
}

export async function rollbackProfile(userId: string): Promise<void> {
  await request(`/v1/admin/models/${encodeURIComponent(userId)}/rollback`, {
    method: "POST",
  });
}
