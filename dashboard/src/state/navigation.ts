export const views = [
  { id: "overview", label: "Overview" },
  { id: "live", label: "Live" },
  { id: "alerts", label: "Alerts" },
  { id: "history", label: "History" },
  { id: "health", label: "System health" },
  { id: "profiles", label: "Profiles" },
  { id: "settings", label: "Settings" },
] as const;

export type View = (typeof views)[number]["id"];
const STORAGE_KEY = "ca.dashboard.active-view";
type NavigationStorage = Pick<Storage, "getItem" | "setItem">;
const browserStorage = () => window.sessionStorage;

export function restoreView(
  getStorage: () => NavigationStorage = browserStorage,
): View {
  try {
    const saved = getStorage().getItem(STORAGE_KEY);
    return views.find((view) => view.id === saved)?.id ?? "overview";
  } catch {
    return "overview";
  }
}

export function persistView(
  view: View,
  getStorage: () => NavigationStorage = browserStorage,
): void {
  try {
    getStorage().setItem(STORAGE_KEY, view);
  } catch {
    // Navigation remains usable if browser storage is unavailable.
  }
}
