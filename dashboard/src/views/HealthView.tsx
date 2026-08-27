import type { DashboardState } from "../state/dashboard";

export function HealthView({ state }: { readonly state: DashboardState }) {
  const metrics = state.metrics;
  return (
    <section aria-labelledby="health-heading">
      <h2 id="health-heading">System health</h2>
      <div className="card-grid">
        <article className="metric-card"><span>Health</span><strong>{state.health?.status ?? "UNAVAILABLE"}</strong></article>
        <article className="metric-card"><span>Collector CPU</span><strong>{metrics?.collector_cpu_percent == null ? "N/A" : `${metrics.collector_cpu_percent.toFixed(2)}%`}</strong></article>
        <article className="metric-card"><span>Collector memory</span><strong>{metrics?.collector_memory_bytes == null ? "N/A" : `${(metrics.collector_memory_bytes / 1_048_576).toFixed(1)} MiB`}</strong></article>
        <article className="metric-card"><span>Dropped events</span><strong>{metrics?.dropped_events ?? "N/A"}</strong></article>
      </div>
      <article className="detail-card">
        <h3>Components</h3>
        <ul className="component-list">
          {Object.entries(state.health?.components ?? {}).map(([name, value]) => <li key={name}><span>{name}</span><strong>{value}</strong></li>)}
        </ul>
      </article>
    </section>
  );
}
