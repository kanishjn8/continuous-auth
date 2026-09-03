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

export interface EnforcementStatus {
  readonly enforcement_enabled: boolean;
  readonly configured: boolean;
  readonly pending_challenge: PendingChallenge | null;
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
