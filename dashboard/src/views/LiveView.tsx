import { RiskTimeline } from "../components/RiskTimeline";
import type { DashboardState } from "../state/dashboard";

export function LiveView({ state }: { readonly state: DashboardState }) {
  const latest = state.decisions.at(-1);
  return (
    <section aria-labelledby="live-heading">
      <h2 id="live-heading">Live monitoring</h2>
      <RiskTimeline decisions={state.decisions} />
      <div className="card-grid compact">
        <article className="metric-card"><span>Evidence</span><strong>{latest?.quality_label ?? "NONE"}</strong></article>
        <article className="metric-card"><span>Fused risk</span><strong>{latest?.fused_score?.toFixed(3) ?? "HELD"}</strong></article>
        <article className="metric-card"><span>Confidence</span><strong>{latest?.context_confidence.toFixed(3) ?? "N/A"}</strong></article>
        <article className="metric-card"><span>Reason</span><strong>{latest?.reason_code ?? "No decision"}</strong></article>
      </div>
    </section>
  );
}
