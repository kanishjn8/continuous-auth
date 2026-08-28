# Native collector platform spike

This module implements T-001 as a single cross-platform collector boundary with native backends for Windows, macOS, and Linux/X11. It is deliberately isolated from the future production `collector/` because protocol C1 has not yet been frozen by T-002.

The executable collects only aggregate keyboard/mouse counts, monotonic capture timestamps, and process-resource samples. It never emits input identity or content. Native event payloads are reduced inside the platform callback and cannot cross the `HookBackend` interface.

## Platform support

| Platform | Backend | Capture clock | Status |
| --- | --- | --- | --- |
| Windows 10/11 | Low-level `SetWindowsHookExW` hooks | `QueryPerformanceCounter` | Implemented; requires Windows validation |
| macOS | Listen-only `CGEventTap` | `mach_continuous_time` | Implemented and compiled on macOS; Input Monitoring permission required |
| Linux/X11 | XInput2 raw events | `CLOCK_MONOTONIC_RAW` | Implemented; requires X11/XInput2 validation |
| Linux/Wayland | None | — | Explicitly unsupported; unrestricted global capture is unavailable by design |

Windows remains the plan's primary evaluation target. The additional backends provide cross-OS engineering support without claiming that ADR-001 or the required real-hardware milestone has been accepted.

## Architecture

`HookBackend` is the only runtime-facing native capture interface:

```text
shared main / clock / metrics / resource sampler
                      |
               HookBackend factory
              /          |          \
 Windows low-level   macOS event   Linux XInput2
      hooks              tap          raw events
```

Every backend implements `name`, `start`, bounded `poll_for`, and idempotent `stop`. The shared runtime owns reporting, signal handling, error behavior, and cleanup. Adding a platform does not change callback metrics or downstream behavior; see [Adding a backend](docs/adding-a-backend.md).

## Build and test

### macOS

```sh
cmake -S collector-platform-spike -B build/collector-platform-spike
cmake --build build/collector-platform-spike
ctest --test-dir build/collector-platform-spike --output-on-failure
./build/collector-platform-spike/native_collector_spike --report-interval-ms 5000
```

Grant the terminal or built executable access under **System Settings → Privacy & Security → Input Monitoring**, then restart it. Without permission, startup fails explicitly and no partial collector remains active.

### Windows

```powershell
cmake -S collector-platform-spike -B build/collector-platform-spike -G "Visual Studio 17 2022" -A x64
cmake --build build/collector-platform-spike --config Release
ctest --test-dir build/collector-platform-spike -C Release --output-on-failure
.\build\collector-platform-spike\Release\native_collector_spike.exe --report-interval-ms 5000
```

### Linux/X11

Install a C++17 compiler, CMake, Xlib development headers, and XInput2 development headers. For Debian/Ubuntu the native packages are `build-essential`, `cmake`, `libx11-dev`, and `libxi-dev`.

```sh
cmake -S collector-platform-spike -B build/collector-platform-spike
cmake --build build/collector-platform-spike
ctest --test-dir build/collector-platform-spike --output-on-failure
./build/collector-platform-spike/native_collector_spike --report-interval-ms 5000
```

The Linux backend rejects a Wayland session instead of presenting incomplete XWayland capture as system-wide monitoring.

## Test coverage

The automated suite covers:

- exact keyboard/mouse accounting and timestamp boundaries;
- visible monotonic-clock regression handling;
- native clock initialization and repeated monotonic reads;
- concurrent callback accounting using bounded atomic state;
- compile-time platform-backend selection;
- process CPU and resident-memory sampling;
- strict command-line interval validation;
- a source-level privacy denylist for prohibited collector APIs and fields.

On the current macOS development machine, a local startup check also confirmed the explicit Input Monitoring permission-denied path. Global capture across application focus changes and successful permission behavior require human execution on each OS. Follow [the validation protocol](docs/platform-spike.md); automated tests cannot substitute for that evidence.

## Scope boundaries

- This module does not invent C1, IPC framing, a ring buffer, context capture, or production configuration.
- It does not persist events or write evidence files.
- Report cadence is provided explicitly at runtime and is not a production threshold.
- T-003 remains blocked on approved C1/C2 fixtures; T-004/T-005 remain blocked on C1 v1 and reviewed feasibility evidence.
