import { FormEvent, useState } from "react";

import { saveChallenge } from "../api/client";
import {
  ANSWER_INPUT_TYPE,
  ChallengeSetupErrors,
  validateChallengeSetup,
} from "../challenge";

/**
 * The one challenge form, used for first-run setup and for later rotation.
 *
 * Every answer field is bound to ANSWER_INPUT_TYPE, so an answer is never
 * rendered in clear text. Answers live in component state only for as long as
 * the submit takes and are cleared immediately afterwards.
 */
export function ChallengeForm({
  rotating,
  submitLabel,
  onSaved,
}: {
  readonly rotating: boolean;
  readonly submitLabel: string;
  readonly onSaved: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [confirmAnswer, setConfirmAnswer] = useState("");
  const [currentAnswer, setCurrentAnswer] = useState("");
  const [errors, setErrors] = useState<ChallengeSetupErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function clearAnswers() {
    setAnswer("");
    setConfirmAnswer("");
    setCurrentAnswer("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFailure(null);
    const validation = validateChallengeSetup(
      { question, answer, confirmAnswer, currentAnswer },
      { rotating },
    );
    setErrors(validation.errors);
    if (!validation.valid) return;
    setBusy(true);
    try {
      await saveChallenge({
        question,
        answer,
        confirmAnswer,
        currentAnswer: rotating ? currentAnswer : undefined,
      });
      clearAnswers();
      setQuestion("");
      onSaved();
    } catch {
      clearAnswers();
      setFailure(
        rotating
          ? "Could not change the challenge. Check your current answer."
          : "Could not save the security challenge.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="challenge-form" onSubmit={submit} noValidate>
      {rotating ? (
        <>
          <label htmlFor="challenge-current-answer">Current Security Answer</label>
          <input
            id="challenge-current-answer"
            type={ANSWER_INPUT_TYPE}
            autoComplete="off"
            value={currentAnswer}
            onChange={(event) => setCurrentAnswer(event.target.value)}
          />
          {errors.currentAnswer ? (
            <p className="form-error" role="alert">
              {errors.currentAnswer}
            </p>
          ) : null}
        </>
      ) : null}

      <label htmlFor="challenge-question">Security Question</label>
      <input
        id="challenge-question"
        type="text"
        autoComplete="off"
        placeholder="What is your project code?"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      {errors.question ? (
        <p className="form-error" role="alert">
          {errors.question}
        </p>
      ) : null}

      <label htmlFor="challenge-answer">Security Answer</label>
      <input
        id="challenge-answer"
        type={ANSWER_INPUT_TYPE}
        autoComplete="new-password"
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
      />
      {errors.answer ? (
        <p className="form-error" role="alert">
          {errors.answer}
        </p>
      ) : null}

      <label htmlFor="challenge-confirm-answer">Confirm Security Answer</label>
      <input
        id="challenge-confirm-answer"
        type={ANSWER_INPUT_TYPE}
        autoComplete="new-password"
        value={confirmAnswer}
        onChange={(event) => setConfirmAnswer(event.target.value)}
      />
      {errors.confirmAnswer ? (
        <p className="form-error" role="alert">
          {errors.confirmAnswer}
        </p>
      ) : null}

      {failure ? (
        <p className="form-error" role="alert">
          {failure}
        </p>
      ) : null}

      <button type="submit" disabled={busy}>
        {busy ? "Saving…" : submitLabel}
      </button>
    </form>
  );
}
