# Native Windows collector

The C++17 collector installs `WH_KEYBOARD_LL` and `WH_MOUSE_LL` hooks, captures QPC time
inside each callback, immediately maps a callback-local platform key identifier to the
ADR-004 class taxonomy, and never retains raw identity. Callbacks perform only atomic/
bounded-buffer work. App context uses foreground process name only—never title/path—and
device metadata contains class, resolution, and DPI.

```powershell
cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector --config Release
ctest --test-dir build/collector -C Release --output-on-failure
build/collector/Release/continuous_auth_collector.exe `
  --config config/collector.development.yaml `
  --categories config/app_categories.yaml
```

Frames are four-byte big-endian length-prefixed JSON over a local Windows named pipe.
The queue is preallocated/bounded, overload follows config and increments drops, and a
failed pipe reconnects with bounded backoff while preserving the pending frame. The
named pause event suppresses keyboard, mouse, app-focus, and device capture but keeps an
explicit paused heartbeat flowing. Real Windows timing, device semantics, stress, and
multi-hour evidence remain a human acceptance gate.

