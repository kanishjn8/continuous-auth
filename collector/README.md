# Native Windows and macOS collector

The C++17 collector selects its native implementation at build time. Windows uses
`WH_KEYBOARD_LL`/`WH_MOUSE_LL`, QPC, and a local named pipe. macOS uses a listen-only
`CGEventTap`, `mach_continuous_time`, and a private per-user Unix socket. Both immediately
map a callback-local platform key identifier to the ADR-004 class taxonomy and never
retain raw identity. App context uses the foreground process/application name only—never
title/path—and device metadata contains class, resolution, and DPI.

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

On macOS, build with the ordinary single-configuration CMake workflow and grant Input
Monitoring permission to the terminal or stable collector executable before starting it:

```bash
cmake -S collector -B build/collector -DBUILD_TESTING=ON
cmake --build build/collector
ctest --test-dir build/collector --output-on-failure
./build/collector/continuous_auth_collector \
  --config config/collector.development.yaml \
  --categories config/app_categories.yaml
```

The macOS transport uses `/tmp/continuous-auth-<uid>/continuous-auth-v1.sock`; its
directory and socket are restricted to the current account. Framing, buffering, reconnect,
heartbeat, and downstream behavior are identical to Windows.
# Capture confirmation

The collector prints a ready message after native hooks start, then prints
`[collector] Events are being captured.` once after the first keyboard or mouse
event. No event details are logged and no additional flag is needed. This confirms
native capture, independently of the backend connection; check the dashboard for
ingestion status.
