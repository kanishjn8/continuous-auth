#include "collector_spike/macos_hook_backend.hpp"

#if defined(__APPLE__)

#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>

namespace continuous_auth::collector::spike {

struct MacOSHookBackend::State {
    explicit State(CaptureMetrics& recorder) noexcept : metrics(recorder) {}

    CaptureMetrics& metrics;
    CFMachPortRef event_tap{};
    CFRunLoopSourceRef run_loop_source{};
    CFRunLoopRef run_loop{};
};

namespace {

CGEventRef event_callback(
    CGEventTapProxy,
    CGEventType type,
    CGEventRef event,
    void* context) {
    auto* state = static_cast<MacOSHookBackend::State*>(context);
    if (state == nullptr) {
        return event;
    }

    if (type == kCGEventTapDisabledByTimeout || type == kCGEventTapDisabledByUserInput) {
        if (state->event_tap != nullptr) {
            CGEventTapEnable(state->event_tap, true);
        }
        return event;
    }

    switch (type) {
        case kCGEventKeyDown:
        case kCGEventKeyUp:
        case kCGEventFlagsChanged:
            state->metrics.record_keyboard();
            break;
        case kCGEventLeftMouseDown:
        case kCGEventLeftMouseUp:
        case kCGEventRightMouseDown:
        case kCGEventRightMouseUp:
        case kCGEventMouseMoved:
        case kCGEventLeftMouseDragged:
        case kCGEventRightMouseDragged:
        case kCGEventOtherMouseDown:
        case kCGEventOtherMouseUp:
        case kCGEventOtherMouseDragged:
        case kCGEventScrollWheel:
            state->metrics.record_mouse();
            break;
        default:
            break;
    }
    return event;
}

CGEventMask capture_mask() noexcept {
    return CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp) |
           CGEventMaskBit(kCGEventFlagsChanged) | CGEventMaskBit(kCGEventLeftMouseDown) |
           CGEventMaskBit(kCGEventLeftMouseUp) | CGEventMaskBit(kCGEventRightMouseDown) |
           CGEventMaskBit(kCGEventRightMouseUp) | CGEventMaskBit(kCGEventMouseMoved) |
           CGEventMaskBit(kCGEventLeftMouseDragged) | CGEventMaskBit(kCGEventRightMouseDragged) |
           CGEventMaskBit(kCGEventOtherMouseDown) | CGEventMaskBit(kCGEventOtherMouseUp) |
           CGEventMaskBit(kCGEventOtherMouseDragged) | CGEventMaskBit(kCGEventScrollWheel);
}

}  // namespace

MacOSHookBackend::MacOSHookBackend(CaptureMetrics& metrics) noexcept
    : state_(std::make_unique<State>(metrics)) {}

MacOSHookBackend::~MacOSHookBackend() {
    stop();
}

std::string_view MacOSHookBackend::name() const noexcept {
    return "macos-cgeventtap";
}

bool MacOSHookBackend::start(std::string& error) {
    if (state_->event_tap != nullptr) {
        error = "The macOS event-tap backend is already active.";
        return false;
    }
    if (!CGPreflightListenEventAccess()) {
        error = "macOS Input Monitoring permission is not granted. Enable it for the terminal or "
                "collector executable in System Settings > Privacy & Security > Input Monitoring, "
                "then restart the process.";
        return false;
    }

    state_->event_tap = CGEventTapCreate(
        kCGSessionEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionListenOnly,
        capture_mask(),
        event_callback,
        state_.get());
    if (state_->event_tap == nullptr) {
        error = "CGEventTapCreate failed despite permission preflight; the current session may deny "
                "global event monitoring.";
        return false;
    }

    state_->run_loop_source = CFMachPortCreateRunLoopSource(nullptr, state_->event_tap, 0);
    if (state_->run_loop_source == nullptr) {
        error = "CFMachPortCreateRunLoopSource failed for the macOS event tap.";
        CFRelease(state_->event_tap);
        state_->event_tap = nullptr;
        return false;
    }

    state_->run_loop = CFRunLoopGetCurrent();
    CFRetain(state_->run_loop);
    CFRunLoopAddSource(state_->run_loop, state_->run_loop_source, kCFRunLoopCommonModes);
    CGEventTapEnable(state_->event_tap, true);
    return true;
}

PollStatus MacOSHookBackend::poll_for(std::chrono::milliseconds timeout, std::string& error) {
    if (state_->event_tap == nullptr || state_->run_loop_source == nullptr) {
        error = "The macOS event-tap backend is not active.";
        return PollStatus::failure;
    }
    const auto seconds = static_cast<CFTimeInterval>(timeout.count()) / 1'000.0;
    const auto result = CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds < 0.0 ? 0.0 : seconds, true);
    if (result == kCFRunLoopRunStopped) {
        return PollStatus::stop_requested;
    }
    if (result == kCFRunLoopRunFinished) {
        error = "The macOS event run loop finished unexpectedly.";
        return PollStatus::failure;
    }
    return PollStatus::continue_running;
}

void MacOSHookBackend::stop() noexcept {
    if (!state_) {
        return;
    }
    if (state_->run_loop != nullptr && state_->run_loop_source != nullptr) {
        CFRunLoopRemoveSource(state_->run_loop, state_->run_loop_source, kCFRunLoopCommonModes);
    }
    if (state_->event_tap != nullptr) {
        CGEventTapEnable(state_->event_tap, false);
    }
    if (state_->run_loop_source != nullptr) {
        CFRelease(state_->run_loop_source);
        state_->run_loop_source = nullptr;
    }
    if (state_->event_tap != nullptr) {
        CFRelease(state_->event_tap);
        state_->event_tap = nullptr;
    }
    if (state_->run_loop != nullptr) {
        CFRelease(state_->run_loop);
        state_->run_loop = nullptr;
    }
}

}  // namespace continuous_auth::collector::spike

#endif
