import { ChallengeForm } from "./ChallengeForm";

/**
 * First-run gate. Collection must not begin before a challenge exists: an
 * escalation that cannot ask the participant anything has no way to
 * distinguish them from whoever has taken over the session.
 */
export function ChallengeSetupView({
  onConfigured,
}: {
  readonly onConfigured: () => void;
}) {
  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="challenge-setup-heading">
        <div className="brand-mark">CA</div>
        <h1 id="challenge-setup-heading">Security Challenge Setup</h1>
        <p>
          You&rsquo;ll be asked this question if suspicious activity is
          detected. It is stored only on this machine, and only as a
          irreversible hash &mdash; nobody, including this console, can read
          your answer back.
        </p>
        <ChallengeForm
          rotating={false}
          submitLabel="Save &amp; Start Collection"
          onSaved={onConfigured}
        />
      </section>
    </main>
  );
}
