import type { DashboardState } from "../state/dashboard";

export function HistoryView({ state }: { readonly state: DashboardState }) {
  return (
    <section aria-labelledby="history-heading">
      <h2 id="history-heading">Decision history</h2>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Window</th><th>State</th><th>Risk</th><th>Score</th><th>Action</th><th>Reason</th></tr></thead>
          <tbody>
            {[...state.decisions].reverse().map((decision) => (
              <tr key={decision.decision_id}>
                <td>{decision.window_id}</td><td>{decision.user_state}</td><td>{decision.risk_level}</td>
                <td>{decision.smoothed_score?.toFixed(3) ?? "held"}</td><td>{decision.action}</td><td>{decision.reason_code}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
