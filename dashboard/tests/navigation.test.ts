import assert from "node:assert/strict";
import test from "node:test";
import { persistView, restoreView, views } from "../src/state/navigation.ts";

test("every dashboard view survives recreation using session storage", () => {
  const values = new Map<string, string>();
  const storage = () => ({
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  });
  assert.equal(restoreView(storage), "overview");
  for (const view of views) {
    persistView(view.id, storage);
    assert.equal(restoreView(storage), view.id);
  }
  assert.equal(values.size, 1);
});

test("unknown stored values safely fall back to overview", () => {
  for (const value of [null, "", "deleted-view", "toString", "__proto__"]) {
    assert.equal(
      restoreView(() => ({ getItem: () => value, setItem: () => {} })),
      "overview",
    );
  }
});

test("blocked storage does not prevent dashboard navigation", () => {
  const blocked = () => {
    throw new Error("Storage denied");
  };
  assert.equal(restoreView(blocked), "overview");
  assert.doesNotThrow(() => persistView("settings", blocked));
});
