# ADR-001 status — platform feasibility

**Status:** Provisional; awaiting real-hardware evidence.

The plan names Windows as the primary target and originally excludes macOS from the initial build. At the user's explicit direction, this module now implements a shared collector boundary with Windows, macOS, and Linux/X11 backends. This is implementation evidence, not an undocumented ADR revision.

Windows uses `SetWindowsHookExW` with `QueryPerformanceCounter`; macOS uses a listen-only `CGEventTap` with `mach_continuous_time`; Linux/X11 uses XInput2 raw event kinds with `CLOCK_MONOTONIC_RAW`. Every backend reduces native events to aggregate diagnostics inside its callback.

This file does not confirm or revise ADR-001. That requires the human validation procedure in [platform-spike.md](platform-spike.md), privacy inspection, retained aggregate evidence, and the plan's human review/change process. Wayland remains unsupported because its security model does not expose unrestricted global input capture.
