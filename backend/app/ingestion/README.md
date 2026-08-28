# T-007 ingestion boundary

The ingestion service accepts the portable T-003/T-005 frame shape: a four-byte
unsigned network-order length followed by canonical UTF-8 JSON. It incrementally
decodes arbitrary transport chunks, validates each payload against generated C1
models, detects sequence gaps/duplicates/late records and capture-time regressions,
then attributes accepted events to an authenticated session and idle-bounded segment.

Important invariants:

- `t_capture_us` is never replaced or adjusted. The wall-clock anchor exists only on
  the session lifecycle event for audit correlation.
- Raw C1 objects live only in a bounded in-memory `deque`; it is cleared between
  authenticated sessions and has no persistence method.
- A sequence reset/wrap is rejected as out of order. A new authenticated session
  explicitly resets the expected sequence.
- Invalid frames are counted and surfaced as availability events. They do not crash
  or deactivate the authenticated session.
- An idle gap splits a segment only when it strictly exceeds the configured value.

Local configuration is in `config/ingestion.development.yaml`. Run the focused suite:

```powershell
python -m pytest backend/tests/test_ingestion.py -q
```
