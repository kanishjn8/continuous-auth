import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  ANSWER_INPUT_TYPE,
  blocksConsole,
  enforcementDetail,
  enforcementHeadline,
  isRecoveryChallenge,
  isScheduledVerification,
  MIN_ANSWER_LENGTH,
  MIN_QUESTION_LENGTH,
  validateChallengeSetup,
} from "../src/challenge.ts";
import type {
  CollectionProvenance,
  EnforcementPosture,
  EnforcementStatus,
} from "../src/challenge.ts";

const QUESTION = "What was the name of your first pet?";
const ANSWER = "wellington-the-third";

function source(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(`../src/${relativePath}`, import.meta.url)),
    "utf8",
  );
}

test("a complete first-run setup validates", () => {
  const result = validateChallengeSetup({
    question: QUESTION,
    answer: ANSWER,
    confirmAnswer: ANSWER,
  });
  assert.equal(result.valid, true);
  assert.deepEqual(result.errors, {});
});

test("the confirmation field must match the answer", () => {
  const result = validateChallengeSetup({
    question: QUESTION,
    answer: ANSWER,
    confirmAnswer: "something-else",
  });
  assert.equal(result.valid, false);
  assert.ok(result.errors.confirmAnswer);
});

test("short questions and answers are rejected", () => {
  const result = validateChallengeSetup({
    question: "a".repeat(MIN_QUESTION_LENGTH - 1),
    answer: "b".repeat(MIN_ANSWER_LENGTH - 1),
    confirmAnswer: "b".repeat(MIN_ANSWER_LENGTH - 1),
  });
  assert.equal(result.valid, false);
  assert.ok(result.errors.question);
  assert.ok(result.errors.answer);
});

test("a whitespace-only question is rejected", () => {
  const result = validateChallengeSetup({
    question: "       ",
    answer: ANSWER,
    confirmAnswer: ANSWER,
  });
  assert.equal(result.valid, false);
  assert.ok(result.errors.question);
});

test("first-run setup does not ask for a current answer", () => {
  const result = validateChallengeSetup(
    { question: QUESTION, answer: ANSWER, confirmAnswer: ANSWER },
    { rotating: false },
  );
  assert.equal(result.valid, true);
});

test("changing the challenge later requires the current answer", () => {
  const withoutCurrent = validateChallengeSetup(
    { question: QUESTION, answer: ANSWER, confirmAnswer: ANSWER },
    { rotating: true },
  );
  assert.equal(withoutCurrent.valid, false);
  assert.ok(withoutCurrent.errors.currentAnswer);

  const withCurrent = validateChallengeSetup(
    {
      question: QUESTION,
      answer: ANSWER,
      confirmAnswer: ANSWER,
      currentAnswer: "the-previous-answer",
    },
    { rotating: true },
  );
  assert.equal(withCurrent.valid, true);
});

test("every answer field is masked, never plain text", () => {
  assert.equal(ANSWER_INPUT_TYPE, "password");
  const form = source("views/ChallengeForm.tsx");
  const answerFields = [
    "challenge-answer",
    "challenge-confirm-answer",
    "challenge-current-answer",
  ];
  for (const field of answerFields) {
    assert.ok(form.includes(`id="${field}"`), `${field} is missing`);
  }
  // Three answer inputs, each bound to the masked input type.
  assert.equal(form.split("type={ANSWER_INPUT_TYPE}").length - 1, 3);
  assert.ok(!form.includes('type="text"\n        autoComplete="new-password"'));
});

test("no challenge answer is ever placed in a URL", () => {
  const client = source("api/client.ts");
  const [, saveChallengeBody = ""] = client.split(
    "export async function saveChallenge",
  );
  assert.ok(saveChallengeBody.includes("JSON.stringify"));
  assert.ok(!/\/v1\/enforcement\/challenge[^"]*\$\{/.test(saveChallengeBody));
});

test("an approved-collection store is marked as recording real data", () => {
  const provenance: CollectionProvenance = {
    environment: "PILOT",
    data_policy: "APPROVED_COLLECTION",
    collection_provenance: "PILOT",
    config_version: "storage-pilot-1",
  };
  assert.equal(provenance.collection_provenance, "PILOT");
  assert.equal(provenance.data_policy, "APPROVED_COLLECTION");
});

test("a synthetic development store is distinguished from a real one", () => {
  const provenance: CollectionProvenance = {
    environment: "DEVELOPMENT",
    data_policy: "SYNTHETIC_ONLY",
    collection_provenance: "SYNTHETIC",
    config_version: "storage-development-1",
  };
  assert.equal(provenance.collection_provenance, "SYNTHETIC");
});

test("a scheduled anchor prompt is recognised by its decision id", () => {
  assert.equal(isScheduledVerification("scheduled-anchor:abc123"), true);
});

test("an enforcement challenge is not mistaken for a scheduled prompt", () => {
  assert.equal(isScheduledVerification("decision-9f2c"), false);
});

// -- enforcement posture ----------------------------------------------------
//
// The backend is the authority on all of this; these helpers only decide what
// the console renders. They are tested here for the same reason the setup
// rules are: so the two sides cannot drift apart on what a posture means.

function status(
  posture: EnforcementPosture,
  pending: EnforcementStatus["pending_challenge"] = null,
): EnforcementStatus {
  return {
    enforcement_enabled: true,
    configured: true,
    session: {
      enforcement_enabled: true,
      posture,
      blocks_protected_access:
        posture === "REAUTH_REQUIRED" || posture === "LOCKED_OUT",
      locked_out: posture === "LOCKED_OUT",
      triggering_decision_id: "decision-1",
      since: "2026-01-01T00:00:00Z",
      failed_responses: 0,
    },
    pending_challenge: pending,
  };
}

test("a normal session is not interrupted", () => {
  assert.equal(blocksConsole(status("NORMAL")), false);
});

test("a soft challenge does not take over the console", () => {
  // MEDIUM is deliberately non-blocking; the participant keeps working.
  assert.equal(blocksConsole(status("SOFT_CHALLENGE")), false);
});

test("a required reauthentication takes over the console", () => {
  assert.equal(blocksConsole(status("REAUTH_REQUIRED")), true);
});

test("a lockout takes over the console", () => {
  assert.equal(blocksConsole(status("LOCKED_OUT")), true);
});

test("an unread enforcement status never claims the session is blocked", () => {
  // Failing closed here would lock the operator out of their own console
  // whenever the status endpoint hiccups.
  assert.equal(blocksConsole(null), false);
});

test("each posture is described to the participant in its own terms", () => {
  assert.equal(enforcementHeadline("LOCKED_OUT"), "Session locked");
  assert.equal(
    enforcementHeadline("REAUTH_REQUIRED"),
    "Identity verification required",
  );
  assert.notEqual(
    enforcementDetail("LOCKED_OUT"),
    enforcementDetail("REAUTH_REQUIRED"),
  );
});

test("a recovery reauthentication is recognised by its decision id", () => {
  assert.equal(isRecoveryChallenge("recovery:abc123"), true);
  assert.equal(isRecoveryChallenge("decision-abc123"), false);
  assert.equal(isScheduledVerification("recovery:abc123"), false);
});

test("the challenge response form never puts an answer in a URL", () => {
  // Mirrors the existing guard on the setup form. An answer in a path or a
  // query string reaches server and proxy logs.
  const text = source("views/ChallengeResponseView.tsx");
  assert.ok(!/[?&]answer=/.test(text));
  assert.ok(text.includes("ANSWER_INPUT_TYPE"));
});

test("the client posts a challenge answer in the request body only", () => {
  const text = source("api/client.ts");
  const call = text.slice(
    text.indexOf("export async function respondToChallenge"),
  );
  assert.ok(call.includes("JSON.stringify({ answer })"));
  assert.ok(!/encodeURIComponent\(answer\)/.test(call));
});
