import type { RiskDecision } from "../protocol";

export function RiskTimeline({
  decisions,
}: {
  readonly decisions: readonly RiskDecision[];
}) {
  const scored = decisions.filter((item) => item.smoothed_score !== null);
  if (scored.length === 0)
    return <p className="empty">No scored windows yet.</p>;
  const points = scored
    .map((item, index) => {
      const x = scored.length === 1 ? 50 : (index / (scored.length - 1)) * 100;
      const y = 100 - Number(item.smoothed_score) * 100;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <figure className="timeline" aria-label="Smoothed behavioral risk timeline">
      <svg
        viewBox="0 0 100 100"
        role="img"
        aria-label="Risk from low to high over recent windows"
      >
        <line x1="0" y1="25" x2="100" y2="25" className="risk-guide high" />
        <line x1="0" y1="55" x2="100" y2="55" className="risk-guide medium" />
        <polyline points={points} vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption>
        Recent smoothed risk; higher on the chart means greater anomaly.
      </figcaption>
    </figure>
  );
}
