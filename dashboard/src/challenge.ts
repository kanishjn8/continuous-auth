/**
 * Security challenge setup rules, kept free of React so they are directly
 * testable and so the dashboard and the backend cannot drift apart on what
 * counts as a valid challenge.
 *
 * The bounds here mirror `backend/app/decisions/challenge.py`. The backend
 * remains the authority: it re-validates every submission, because a browser
 * check is a convenience, never a control.
 */

export const MIN_QUESTION_LENGTH = 4;
export const MIN_ANSWER_LENGTH = 4;

/**
 * The answer is a credential and is never rendered in clear text. The setup
 * and rotation forms bind their answer fields to this type.
 */
export const ANSWER_INPUT_TYPE = "password" as const;

export interface ChallengeStatus {
  readonly configured: boolean;
  readonly question: string | null;
}

export interface PendingChallenge {
  readonly decision_id: string;
  readonly action: "SOFT_CHALLENGE" | "REAUTH";
  readonly question: string;
  readonly blocking: boolean;
  readonly opened_at: string;
  readonly expires_at: string;
}

/**
 * Where the backend says this session sits on the escalation ladder. The
 * backend is the authority: these values change what the console renders,
 * never whether the API will answer. `REAUTH_REQUIRED` and `LOCKED_OUT` are
 * already being enforced server-side by the time the dashboard sees them.
 */
export type EnforcementPosture =
  "NORMAL" | "SOFT_CHALLENGE" | "REAUTH_REQUIRED" | "LOCKED_OUT";

export interface SessionEnforcement {
  readonly enforcement_enabled: boolean;
  readonly posture: EnforcementPosture;
  readonly blocks_protected_access: boolean;
  readonly locked_out: boolean;
  readonly triggering_decision_id: string | null;
  readonly since: string | null;
  readonly failed_responses: number;
}

export interface EnforcementStatus {
  readonly enforcement_enabled: boolean;
  readonly configured: boolean;
  readonly session: SessionEnforcement;
  readonly pending_challenge: PendingChallenge | null;
}

export type ChallengeOutcome = "ACCEPTED" | "REJECTED" | "EXPIRED";

/**
 * True when the console must be replaced by the verification screen rather
 * than merely annotated. A soft challenge is non-blocking by design, so it
 * gets a banner and an inline form; a reauthentication or a lockout takes
 * over, because the backend is already refusing every protected route.
 */
export function blocksConsole(status: EnforcementStatus | null): boolean {
  return status !== null && status.session.blocks_protected_access;
}

/**
 * What the participant is told. Deliberately specific about lockout: a
 * console that says "something went wrong" during a drill teaches the
 * operator nothing and teaches the participant less.
 */
export function enforcementHeadline(posture: EnforcementPosture): string {
  if (posture === "LOCKED_OUT") return "Session locked";
  if (posture === "REAUTH_REQUIRED") return "Identity verification required";
  if (posture === "SOFT_CHALLENGE") return "Quick identity check";
  return "Protection active";
}

export function enforcementDetail(posture: EnforcementPosture): string {
  if (posture === "LOCKED_OUT")
    return "Access is blocked after a failed identity verification. Answer the security challenge to restore this session.";
  if (posture === "REAUTH_REQUIRED")
    return "Unusual interaction behaviour was detected. Answer the security challenge to continue.";
  if (posture === "SOFT_CHALLENGE")
    return "Please confirm it is still you. You can keep working while you answer.";
  return "Continuous verification is running normally.";
}

/**
 * Which storage profile is live, as reported by `GET
 * /v1/collection/provenance`. The dashboard renders this as a persistent
 * badge (PLAN.md pilot-readiness): an operator must never be left guessing
 * whether a session is landing in a synthetic store or a real participant's
 * pilot data.
 */
export type CollectionProvenance = {
  readonly environment: string;
  readonly data_policy: string;
  readonly collection_provenance: string;
  readonly config_version: string;
};

const SCHEDULED_PREFIX = "scheduled-anchor:";
const RECOVERY_PREFIX = "recovery:";

/**
 * A scheduled A3 verification prompt arrives through the same challenge
 * status surface as an enforcement challenge, but it is routine, not a risk
 * response. The dashboard says so, so a participant is not alarmed by a
 * periodic prompt during a multi-day study.
 */
export function isScheduledVerification(decisionId: string): boolean {
  return decisionId.startsWith(SCHEDULED_PREFIX);
}

/**
 * A reauthentication the participant asked for in order to clear an
 * enforcement posture, rather than one the risk engine dispatched. Answering
 * it correctly is what restores a blocked or locked-out session.
 */
export function isRecoveryChallenge(decisionId: string): boolean {
  return decisionId.startsWith(RECOVERY_PREFIX);
}

export interface ChallengeSetupInput {
  readonly question: string;
  readonly answer: string;
  readonly confirmAnswer: string;
  /** Required only when replacing an existing challenge. */
  readonly currentAnswer?: string;
}

export interface ChallengeSetupErrors {
  readonly question?: string;
  readonly answer?: string;
  readonly confirmAnswer?: string;
  readonly currentAnswer?: string;
}

export interface ChallengeValidation {
  readonly valid: boolean;
  readonly errors: ChallengeSetupErrors;
}

/**
 * Validate first-run setup, or a rotation when `rotating` is true.
 *
 * Rotation requires the current answer: the dashboard sits on the machine
 * being monitored, so without it anyone holding the session could swap the
 * challenge and walk through the next escalation.
 */
export function validateChallengeSetup(
  input: ChallengeSetupInput,
  { rotating = false }: { readonly rotating?: boolean } = {},
): ChallengeValidation {
  const errors: {
    question?: string;
    answer?: string;
    confirmAnswer?: string;
    currentAnswer?: string;
  } = {};

  if (input.question.trim().length < MIN_QUESTION_LENGTH) {
    errors.question = `Enter a question of at least ${MIN_QUESTION_LENGTH} characters.`;
  }
  if (input.answer.length < MIN_ANSWER_LENGTH) {
    errors.answer = `Enter an answer of at least ${MIN_ANSWER_LENGTH} characters.`;
  }
  if (input.confirmAnswer !== input.answer) {
    errors.confirmAnswer = "The answers do not match.";
  }
  if (rotating && !input.currentAnswer) {
    errors.currentAnswer = "Enter your current answer to change the challenge.";
  }

  return { valid: Object.keys(errors).length === 0, errors };
}
