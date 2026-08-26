import assert from "node:assert/strict";
import test from "node:test";

import { foundationStatus, PROTOCOL_VERSION } from "../src/protocol.ts";

test("foundation status never implies active protection", () => {
  assert.equal(PROTOCOL_VERSION, "1.0.0");
  assert.deepEqual(foundationStatus(), {
    protocolVersion: "1.0.0",
    protectionStatus: "INITIALIZING",
  });
});


