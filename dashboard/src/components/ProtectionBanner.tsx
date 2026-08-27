import type { Connectivity, DashboardState } from "../state/dashboard";

function connectionLabel(value: Connectivity): string {
  if (value === "ONLINE") return "Live stream connected";
  if (value === "REPLAY") return "DEMO REPLAY — not live protection";
  if (value === "STALE") return "Live state is stale";
  if (value === "CONNECTING") return "Reconnecting to local backend";
  return "Backend unavailable";
}

export function ProtectionBanner({ state }: { readonly state: DashboardState }) {
  const protectedNow =
    state.connectivity === "ONLINE" &&
    state.current?.protection_available === true &&
    state.health?.status === "HEALTHY";
  return (
    <section
      className={`protection-banner ${protectedNow ? "protected" : "unavailable"}`}
      role="status"
      aria-live="polite"
    >
      <strong>{protectedNow ? "Protection active" : "Protection unavailable or unverified"}</strong>
      <span>{connectionLabel(state.connectivity)}</span>
      {state.error ? <span>{state.error}</span> : null}
    </section>
  );
}
