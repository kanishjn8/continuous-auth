import { FormEvent, useCallback, useEffect, useState } from "react";

import { requestReauthentication, respondToChallenge } from "../api/client";
import {
  ANSWER_INPUT_TYPE,
  enforcementDetail,
  enforcementHeadline,
  isRecoveryChallenge,
  isScheduledVerification,
} from "../challenge";
import type { EnforcementStatus } from "../challenge";

/**
 * The interactive half of the escalation ladder.
 *
 * `NativeChallengeAdapter` draws an always-on-top prompt on the monitored
 * desktop, but only on Windows and only when enforcement is enabled. This is
 * the surface for every other case, and the one the operator can watch during
 * a drill. It is not a second mechanism: the answer goes to the same
 * `/v1/enforcement/challenge/{id}/respond` endpoint, is verified against the
 * same PBKDF2 credential, and produces the same audit record and A2 anchor.
 *
 * Nothing here decides anything. A correct answer does not unblock the
 * console; it causes the backend to change its posture, and the parent
 * re-reads that posture. If the risk engine still sees an impostor, the next
 * window escalates again -- continuous authentication is not weakened by
 * having passed a challenge once.
 */
export function ChallengeResponseView({
  enforcement,
  onResolved,
}: {
  readonly enforcement: EnforcementStatus;
  readonly onResolved: () => void;
}) {
  const pending = enforcement.pending_challenge;
  const posture = enforcement.session.posture;
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);

  // A blocking posture with no challenge to answer is the normal state after
  // a wrong answer or an expiry consumed the dispatched one. Ask the backend
  // to issue the reauthentication that clears it; without this the
  // participant would be locked out with nothing to type into.
  const openRecovery = useCallback(async () => {
    setOpening(true);
    setFailure(null);
    try {
      await requestReauthentication();
      onResolved();
    } catch {
      setFailure(
        "A verification challenge could not be issued. The security challenge may not be configured on this machine.",
      );
    } finally {
      setOpening(false);
    }
  }, [onResolved]);

  useEffect(() => {
    if (enforcement.session.blocks_protected_access && pending === null)
      void openRecovery();
  }, [enforcement.session.blocks_protected_access, pending, openRecovery]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending === null) return;
    setBusy(true);
    setFailure(null);
    try {
      const outcome = await respondToChallenge(pending.decision_id, answer);
      setAnswer("");
      if (outcome === "REJECTED")
        setFailure("That answer was not correct. This attempt has been used.");
      if (outcome === "EXPIRED")
        setFailure("This challenge expired before it was answered.");
      onResolved();
    } catch {
      setAnswer("");
      setFailure("The verification service could not be reached.");
    } finally {
      setBusy(false);
    }
  }

  const scheduled =
    pending !== null && isScheduledVerification(pending.decision_id);
  const heading = scheduled
    ? "Routine verification"
    : enforcementHeadline(posture);
  const detail = scheduled
    ? "A periodic check that confirms it is you. Nothing is wrong."
    : enforcementDetail(posture);

  return (
    <section
      className={`challenge-response challenge-response--${posture.toLowerCase()}`}
      role="alertdialog"
      aria-labelledby="challenge-response-heading"
    >
      <h2 id="challenge-response-heading">{heading}</h2>
      <p>{detail}</p>
      {pending !== null && isRecoveryChallenge(pending.decision_id) ? (
        <p className="challenge-origin">
          Requested by you to restore this session.
        </p>
      ) : null}
      {pending === null ? (
        <p role="status">
          {opening
            ? "Issuing a verification challenge…"
            : "Waiting for a verification challenge."}
        </p>
      ) : (
        <form onSubmit={submit} noValidate>
          <p className="challenge-question">{pending.question}</p>
          <label htmlFor="challenge-response-answer">Security answer</label>
          <input
            id="challenge-response-answer"
            type={ANSWER_INPUT_TYPE}
            autoComplete="off"
            autoFocus
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
          />
          <button type="submit" disabled={busy || answer.length === 0}>
            {busy ? "Verifying…" : "Verify"}
          </button>
        </form>
      )}
      {failure ? (
        <p className="form-error" role="alert">
          {failure}
        </p>
      ) : null}
      {enforcement.session.blocks_protected_access && pending !== null ? (
        <button className="secondary" onClick={openRecovery} disabled={opening}>
          Request a new challenge
        </button>
      ) : null}
    </section>
  );
}
