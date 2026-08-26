#include "collector_spike/linux_x11_hook_backend.hpp"

#if defined(__linux__)

#include <X11/Xlib.h>
#include <X11/extensions/XInput2.h>

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <poll.h>

namespace continuous_auth::collector::spike {

struct LinuxX11HookBackend::State {
    explicit State(CaptureMetrics& recorder) noexcept : metrics(recorder) {}

    CaptureMetrics& metrics;
    Display* display{};
    int xi_opcode{};
};

LinuxX11HookBackend::LinuxX11HookBackend(CaptureMetrics& metrics) noexcept
    : state_(std::make_unique<State>(metrics)) {}

LinuxX11HookBackend::~LinuxX11HookBackend() {
    stop();
}

std::string_view LinuxX11HookBackend::name() const noexcept {
    return "linux-x11-xinput2";
}

bool LinuxX11HookBackend::start(std::string& error) {
    if (state_->display != nullptr) {
        error = "The Linux X11 backend is already active.";
        return false;
    }

    const auto* session_type = std::getenv("XDG_SESSION_TYPE");
    if (session_type != nullptr && std::strcmp(session_type, "wayland") == 0) {
        error = "Wayland does not expose unrestricted global input capture. Use an X11 session; "
                "XWayland is not treated as a complete system-wide collector backend.";
        return false;
    }

    state_->display = XOpenDisplay(nullptr);
    if (state_->display == nullptr) {
        error = "XOpenDisplay failed. Confirm DISPLAY is set and an X11 session is available.";
        return false;
    }

    int event = 0;
    int error_code = 0;
    if (XQueryExtension(
            state_->display, "XInputExtension", &state_->xi_opcode, &event, &error_code) == 0) {
        error = "The XInput extension is unavailable on this X server.";
        stop();
        return false;
    }

    int major = 2;
    int minor = 0;
    if (XIQueryVersion(state_->display, &major, &minor) != Success) {
        error = "XInput 2.0 or newer is required for raw global input events.";
        stop();
        return false;
    }

    unsigned char mask_data[XIMaskLen(XI_LASTEVENT)]{};
    XISetMask(mask_data, XI_RawKeyPress);
    XISetMask(mask_data, XI_RawKeyRelease);
    XISetMask(mask_data, XI_RawButtonPress);
    XISetMask(mask_data, XI_RawButtonRelease);
    XISetMask(mask_data, XI_RawMotion);

    XIEventMask mask{};
    mask.deviceid = XIAllMasterDevices;
    mask.mask_len = static_cast<int>(sizeof(mask_data));
    mask.mask = mask_data;
    if (XISelectEvents(state_->display, DefaultRootWindow(state_->display), &mask, 1) != Success) {
        error = "XISelectEvents failed while registering raw global input events.";
        stop();
        return false;
    }
    XSync(state_->display, False);
    return true;
}

PollStatus LinuxX11HookBackend::poll_for(std::chrono::milliseconds timeout, std::string& error) {
    if (state_->display == nullptr) {
        error = "The Linux X11 backend is not active.";
        return PollStatus::failure;
    }

    pollfd descriptor{};
    descriptor.fd = ConnectionNumber(state_->display);
    descriptor.events = POLLIN;
    const auto timeout_count = timeout.count();
    const auto timeout_ms = timeout_count < 0
                                ? 0
                                : static_cast<int>(timeout_count > 2'147'483'647LL
                                                       ? 2'147'483'647LL
                                                       : timeout_count);
    const auto poll_result = ::poll(&descriptor, 1, timeout_ms);
    if (poll_result < 0) {
        if (errno == EINTR) {
            return PollStatus::continue_running;
        }
        error = std::string("poll failed for the X11 connection: ") + std::strerror(errno);
        return PollStatus::failure;
    }

    while (XPending(state_->display) > 0) {
        XEvent native_event{};
        XNextEvent(state_->display, &native_event);
        if (native_event.type != GenericEvent || native_event.xcookie.extension != state_->xi_opcode ||
            XGetEventData(state_->display, &native_event.xcookie) == 0) {
            continue;
        }

        switch (native_event.xcookie.evtype) {
            case XI_RawKeyPress:
            case XI_RawKeyRelease:
                state_->metrics.record_keyboard();
                break;
            case XI_RawButtonPress:
            case XI_RawButtonRelease:
            case XI_RawMotion:
                state_->metrics.record_mouse();
                break;
            default:
                break;
        }
        XFreeEventData(state_->display, &native_event.xcookie);
    }
    return PollStatus::continue_running;
}

void LinuxX11HookBackend::stop() noexcept {
    if (state_ && state_->display != nullptr) {
        XCloseDisplay(state_->display);
        state_->display = nullptr;
    }
}

}  // namespace continuous_auth::collector::spike

#endif
