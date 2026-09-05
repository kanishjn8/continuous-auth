# macOS deployment and verification

The macOS runtime uses the same C1 protocol, feature extractor, models, risk policy, and
storage gates as Windows. Platform selection changes only native capture, local IPC,
context/device discovery, pause control, and workstation enforcement.

## Prerequisites

- macOS and Xcode command-line tools
- CMake 3.20+, Python 3.11+, Node.js 22, and npm
- Input Monitoring permission for the terminal or stable collector executable
- Automation permission for `System Events` only if OS-level workstation locking is
  explicitly enabled for a reviewed enforcement drill

## Build and run

Follow the ordered macOS procedure in `startup.md`. The Python runtime detects
`sys.platform == "darwin"` and selects the macOS storage profile and Unix-socket server.
The C++ collector detects `__APPLE__` and selects the event tap, AppKit context resolver,
POSIX pause signal, and Unix-socket client.

The collector and backend must run as the same account. The backend creates
`/tmp/continuous-auth-<uid>` with mode `0700` and the socket with mode `0600`. A second
server is rejected, and a stale socket left by an unclean exit is replaced only after
the endpoint is confirmed inactive.

## Human acceptance matrix

Record only aggregate evidence beneath ignored `local-evidence/`. Verify permission
denial, keyboard and mouse coverage across application focus changes, modifier/repeat
semantics, button and scroll semantics, Retina and multi-display coordinates, context
changes without titles or paths, pause/resume with continued heartbeat, reconnect,
clean shutdown/restart, sleep/wake behavior, resource use, and an enforcement drill if
that feature has been approved.

Automated compilation does not grant Input Monitoring permission and does not replace
real-hardware validation. Do not describe macOS collection or enforcement as validated
until this matrix and the existing ADR review process are complete.
