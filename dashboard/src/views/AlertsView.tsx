import { AlertCard } from "../components/AlertCard";
import type { DashboardState } from "../state/dashboard";

export function AlertsView({
  state,
  onAcknowledge,
}: {
  readonly state: DashboardState;
  readonly onAcknowledge: (alertId: string) => void;
}) {
  return (
    <section aria-labelledby="alerts-heading">
      <h2 id="alerts-heading">Alerts</h2>
      <p className="view-note">
        Red striped cards mean protection availability or tamper. Amber cards
        mean behavioral risk.
      </p>
      <div className="alerts-list">
        {state.alerts.length ? (
          state.alerts.map((alert) => (
            <AlertCard
              key={alert.alert_id}
              alert={alert}
              onAcknowledge={onAcknowledge}
            />
          ))
        ) : (
          <p className="empty">No alerts recorded.</p>
        )}
      </div>
    </section>
  );
}
