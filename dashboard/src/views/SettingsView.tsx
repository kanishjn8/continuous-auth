import type { ChallengeStatus, EnforcementStatus } from "../challenge";
import { ChallengeForm } from "./ChallengeForm";

/**
 * Settings surface for changing the stored challenge, plus read-only
 * enforcement state. Enforcement itself is configured in `config/` and
 * deliberately cannot be switched on from here: the dashboard is co-located
 * with the monitored endpoint (PLAN.md 14.4), so arming or disarming
 * enforcement from this console would be reachable by whoever holds the
 * session.
 */
export function SettingsView({
  challenge,
  enforcement,
  onChanged,
}: {
  readonly challenge: ChallengeStatus | null;
  readonly enforcement: EnforcementStatus | null;
  readonly onChanged: () => void;
}) {
  return (
    <section aria-labelledby="settings-heading">
      <h2 id="settings-heading">Settings</h2>

      <article className="detail-card">
        <h3>Enforcement</h3>
        <p>
          Real enforcement is{" "}
          <strong>
            {enforcement?.enforcement_enabled ? "enabled" : "disabled"}
          </strong>
          . While disabled, decisions are still computed, recorded, and
          streamed, but no native prompt is shown and the workstation is never
          locked.
        </p>
        <p>
          This is set in <code>config/enforcement.development.yaml</code> and is
          off by default so that a false positive cannot lock a participant out
          during ordinary collection.
        </p>
      </article>

      <article className="detail-card">
        <h3>Security challenge</h3>
        <p>
          Current question:{" "}
          <strong>{challenge?.question ?? "not configured"}</strong>
        </p>
        <p>
          Changing it requires your current answer. Existing verification
          records are unaffected &mdash; past anchors stay valid, and the new
          challenge applies from the next escalation onward.
        </p>
        <ChallengeForm
          rotating
          submitLabel="Change security challenge"
          onSaved={onChanged}
        />
      </article>
    </section>
  );
}
