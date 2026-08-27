import type { DashboardState } from "../state/dashboard";

export function OverviewView({ state }: { readonly state: DashboardState }) {
  const current = state.current;
  const latest = state.decisions.at(-1);
  return (
    <section aria-labelledby="overview-heading">
      <h2 id="overview-heading">Overview</h2>
      <div className="card-grid">
        <article className="metric-card">
          <span>Current profile</span>
          <strong>{current?.user_id ?? "No active user"}</strong>
        </article>
        <article className="metric-card">
          <span>System state</span>
          <strong>{current?.user_state ?? "UNAVAILABLE"}</strong>
        </article>
        <article className={`metric-card risk-${current?.risk_level.toLowerCase() ?? "unavailable"}`}>
          <span>Current risk</span>
          <strong>{current?.risk_level ?? "UNAVAILABLE"}</strong>
        </article>
        <article className="metric-card">
          <span>Last action</span>
          <strong>{latest?.action ?? "NONE"}</strong>
        </article>
      </div>
      <article className="detail-card">
        <h3>Latest authentication check</h3>
        <dl>
          <div><dt>Smoothed risk</dt><dd>{latest?.smoothed_score?.toFixed(3) ?? "No score"}</dd></div>
          <div><dt>Context confidence</dt><dd>{latest?.context_confidence.toFixed(3) ?? "Unavailable"}</dd></div>
          <div><dt>Modalities</dt><dd>{latest?.quality_label ?? "No evidence"}</dd></div>
          <div><dt>Enforcement</dt><dd>{latest?.enforcement_applied ? "Applied" : "Not applied"}</dd></div>
        </dl>
      </article>
    </section>
  );
}
