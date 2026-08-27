import type { AlertRecord } from "../protocol";

export function AlertCard({
  alert,
  onAcknowledge,
}: {
  readonly alert: AlertRecord;
  readonly onAcknowledge: (alertId: string) => void;
}) {
  const availability = alert.alert_type !== "BEHAVIORAL";
  return (
    <article
      className={`alert-card ${availability ? "availability" : "behavioral"}`}
    >
      <div>
        <strong>
          {availability ? "SYSTEM PROTECTION ALERT" : "BEHAVIORAL RISK ALERT"}
        </strong>
        <span className="pill">{alert.alert_type}</span>
      </div>
      <p>{alert.code.replaceAll("_", " ")}</p>
      <small>{new Date(alert.occurred_at).toLocaleString()}</small>
      <button
        disabled={alert.acknowledged}
        onClick={() => onAcknowledge(alert.alert_id)}
      >
        {alert.acknowledged ? "Acknowledged" : "Acknowledge"}
      </button>
    </article>
  );
}
