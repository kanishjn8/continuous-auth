import type { DashboardState } from "../state/dashboard";

export function ProfilesView({
  state,
  onRollback,
}: {
  readonly state: DashboardState;
  readonly onRollback: (userId: string) => void;
}) {
  return (
    <section aria-labelledby="profiles-heading">
      <h2 id="profiles-heading">Profiles and updates</h2>
      <div className="profile-grid">
        {state.profiles.map((profile) => (
          <article className="detail-card" key={profile.user_id}>
            <h3>{profile.user_id}</h3>
            <p>{profile.user_state} · {profile.enrollment_windows} windows · {profile.distinct_days} days</p>
            <p>Model: {profile.model_version ?? "not activated"}</p>
            <button disabled={!profile.model_version} onClick={() => onRollback(profile.user_id)}>Roll back one version</button>
          </article>
        ))}
      </div>
      <h3>Update candidate queue</h3>
      <ul className="update-list">
        {state.updates.map((item) => <li key={item.candidate_id}><strong>{item.disposition}</strong><span>{item.user_id} / {item.segment_id}</span><small>{item.reason_code}</small></li>)}
      </ul>
    </section>
  );
}
