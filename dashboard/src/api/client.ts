import type {
  ChallengeOutcome,
  ChallengeStatus,
  CollectionProvenance,
  EnforcementStatus,
} from "../challenge";
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

export async function loadChallengeStatus(): Promise<ChallengeStatus> {
  return request<ChallengeStatus>("/v1/enforcement/challenge");
}

export async function loadEnforcementStatus(): Promise<EnforcementStatus> {
  return request<EnforcementStatus>("/v1/enforcement/status");
}

export async function loadCollectionProvenance(): Promise<CollectionProvenance> {
  return request<CollectionProvenance>("/v1/collection/provenance");
}

/**
 * Answer a dispatched challenge.
 *
 * The answer is a credential: it travels in the request body, never in the
 * URL, and the response carries only an outcome. The backend decides what
 * that outcome means for the session -- this call never changes enforcement
 * state on its own, and the caller must re-read the enforcement status
 * rather than assume success unblocked anything.
 */
export async function respondToChallenge(
  decisionId: string,
  answer: string,
): Promise<ChallengeOutcome> {
  const body = await request<{ readonly outcome: ChallengeOutcome }>(
    `/v1/enforcement/challenge/${encodeURIComponent(decisionId)}/respond`,
    { method: "POST", body: JSON.stringify({ answer }) },
  );
  return body.outcome;
}

/**
 * Ask the backend for the reauthentication that clears a blocking posture.
 * Refused (409) unless enforcement is actually blocking, so this cannot be
 * used to mint verification evidence during a quiet session.
 */
export async function requestReauthentication(): Promise<{
  readonly decision_id: string;
  readonly question: string;
  readonly expires_at: string;
}> {
  return request("/v1/enforcement/reauthenticate", { method: "POST" });
}

/**
 * Save the first challenge, or rotate an existing one by supplying the
 * current answer. Answers travel in the request body only; nothing here ever
 * puts one in a URL, where it would reach server and proxy logs.
 */
export async function saveChallenge(input: {
  readonly question: string;
  readonly answer: string;
  readonly confirmAnswer: string;
  readonly currentAnswer?: string;
}): Promise<void> {
  await request("/v1/enforcement/challenge", {
    method: "PUT",
    body: JSON.stringify({
      question: input.question,
      answer: input.answer,
      confirm_answer: input.confirmAnswer,
      current_answer: input.currentAnswer ?? null,
    }),
  });
}
