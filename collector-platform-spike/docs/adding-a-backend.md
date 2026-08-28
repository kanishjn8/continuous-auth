# Adding a collector backend

Use this guide when adding another operating-system capture implementation.

## Contract

Implement `HookBackend` from `collector_spike/include/collector_spike/hook_backend.hpp`:

- `name()` returns a stable diagnostic identifier.
- `start(error)` acquires all native resources or returns `false` with an actionable, content-free message.
- `poll_for(timeout, error)` services the native event loop for a bounded interval. It must never spin indefinitely.
- `stop()` releases every native resource, is safe after partial startup, and is safe to call more than once.

Register the backend in `hook_backend_factory.cpp` and add only its native source and system libraries in `CMakeLists.txt`.

## Privacy and callback rules

Native callbacks may inspect only the event kind needed to choose keyboard versus mouse accounting. They must not retain, transmit, print, or return native payload fields. In particular, do not expose typed input, ordered reconstructable input, titles, paths, URLs, clipboard data, UI trees, or screen data.

Callbacks must remain allocation-free, non-blocking, and free of disk or IPC work. Timestamping occurs through `CaptureMetrics` inside the callback. Shared reporting and cleanup stay in `main.cpp`.

## Required tests and evidence

1. Add the platform to the factory-selection unit test.
2. Compile with warnings treated as errors.
3. Test denied permissions, unavailable display/session, double start, partial-start cleanup, runtime poll failure, and repeated stop.
4. Run the focus-switch matrix and 30-minute resource observation in `platform-spike.md` on real hardware.
5. Confirm output contains aggregate diagnostics only and `monotonicity_violations=0`.
6. Record the OS, toolchain, hardware, commands, and results in an untracked local evidence file.

Do not describe a backend as supported until both automated checks and real-hardware validation pass.
