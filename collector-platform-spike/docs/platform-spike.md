# T-001 cross-platform validation protocol

## Purpose

This protocol collects the human evidence needed to assess each native backend without collecting behavioral content. Run it separately on Windows, macOS, and Linux/X11. Store results in `local-evidence/`, which the root `.gitignore` excludes.

## Platform prerequisites

| Platform | Prerequisites | Expected backend |
| --- | --- | --- |
| Windows 10/11 | Visual Studio 2022 C++ tools and CMake | `windows-low-level-hooks` |
| macOS | Xcode command-line tools, CMake, Input Monitoring permission | `macos-cgeventtap` |
| Linux/X11 | C++17, CMake, Xlib and XInput2 development packages | `linux-x11-xinput2` |

Wayland is not a validation target. The executable must reject a Wayland session clearly.

## Procedure

1. Build and run the platform commands in [the module README](../README.md).
2. Confirm the startup line names the expected backend.
3. Confirm a `capture_snapshot` appears at the selected interval and both event counts rise during normal input.
4. Complete the focus-switch matrix. Retain only aggregate snapshots and pass/fail notes.
5. Run for at least 30 minutes during benign local use. Do not retain typed input, screenshots, titles, paths, URLs, or per-event data.
6. Press Ctrl+C and confirm a final snapshot and clean exit. Start the executable again to ensure native resources were released.
7. Record OS/toolchain/hardware details, duration, count totals, maximum CPU percentage, maximum resident bytes, permission behavior, and any missed-capture symptoms.

## Focus-switch matrix

| Focus target | Keyboard count rises | Mouse count rises | Notes |
| --- | --- | --- | --- |
| Desktop / native file manager | | | |
| Browser | | | |
| Office or editor application | | | |
| Terminal | | | |
| Repeated switching between two applications | | | |
| Lock then unlock, if local policy permits | | | |

## Platform-specific checks

### Windows

- Run once as a standard user; elevated privileges should not be assumed.
- Record any endpoint-security or Windows error code shown by startup.

### macOS

- First verify the denied-permission path is actionable and leaves no running process.
- Grant Input Monitoring permission, restart the terminal/executable, and repeat the matrix.
- Revoke permission after validation if the machine should not retain it.

### Linux/X11

- Record `XDG_SESSION_TYPE`, display server, and XInput version.
- Confirm `XDG_SESSION_TYPE=wayland` produces an explicit unsupported-session error.

## Pass criteria

- Capture continues across focus changes for both modalities.
- `monotonicity_violations=0` throughout the run.
- Permission/display failures are explicit and leave no active hook.
- Ctrl+C exits cleanly and immediate restart succeeds.
- CPU and resident memory remain bounded during the observation.
- Output and evidence contain aggregate diagnostics only.

Passing on one OS does not validate another. Windows remains the formal ADR-001 target until the plan is revised through its change process.

## Evidence template

```text
Platform / OS build:
Backend name:
Toolchain:
Hardware:
Command:
Run duration:
Focus matrix result:
First / last aggregate snapshots:
Maximum CPU percentage (one core):
Maximum resident bytes:
Permission / display behavior:
Shutdown and restart result:
Privacy inspection result:
Reviewer:
Platform result: passed / failed / blocked
ADR-001 decision impact: none / confirm / propose revision
```
