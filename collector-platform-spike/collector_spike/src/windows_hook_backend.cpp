#include "collector_spike/windows_hook_backend.hpp"

#if defined(_WIN32)

#include <windows.h>

#include <limits>
#include <sstream>

namespace continuous_auth::collector::spike {

struct WindowsHookBackend::State {
    explicit State(CaptureMetrics& recorder) noexcept : metrics(recorder) {}

    CaptureMetrics& metrics;
    HHOOK keyboard_hook{};
    HHOOK mouse_hook{};
};

namespace {

CaptureMetrics* active_metrics = nullptr;

LRESULT CALLBACK keyboard_callback(int code, WPARAM message, LPARAM native_event) {
    if (code == HC_ACTION && active_metrics != nullptr) {
        active_metrics->record_keyboard();
    }
    return CallNextHookEx(nullptr, code, message, native_event);
}

LRESULT CALLBACK mouse_callback(int code, WPARAM message, LPARAM native_event) {
    if (code == HC_ACTION && active_metrics != nullptr) {
        active_metrics->record_mouse();
    }
    return CallNextHookEx(nullptr, code, message, native_event);
}

std::string windows_error(const char* action) {
    std::ostringstream stream;
    stream << action << " failed; Windows error=" << GetLastError();
    return stream.str();
}

}  // namespace

WindowsHookBackend::WindowsHookBackend(CaptureMetrics& metrics) noexcept
    : state_(std::make_unique<State>(metrics)) {}

WindowsHookBackend::~WindowsHookBackend() {
    stop();
}

std::string_view WindowsHookBackend::name() const noexcept {
    return "windows-low-level-hooks";
}

bool WindowsHookBackend::start(std::string& error) {
    if (active_metrics != nullptr) {
        error = "A collector hook backend is already active in this process.";
        return false;
    }

    const auto module = GetModuleHandleW(nullptr);
    if (module == nullptr) {
        error = windows_error("GetModuleHandleW");
        return false;
    }

    state_->keyboard_hook = SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_callback, module, 0);
    if (state_->keyboard_hook == nullptr) {
        error = windows_error("SetWindowsHookExW keyboard hook");
        return false;
    }

    state_->mouse_hook = SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, module, 0);
    if (state_->mouse_hook == nullptr) {
        error = windows_error("SetWindowsHookExW mouse hook");
        UnhookWindowsHookEx(state_->keyboard_hook);
        state_->keyboard_hook = nullptr;
        return false;
    }

    active_metrics = &state_->metrics;
    return true;
}

PollStatus WindowsHookBackend::poll_for(std::chrono::milliseconds timeout, std::string& error) {
    const auto timeout_count = timeout.count();
    const auto maximum_timeout =
        static_cast<decltype(timeout_count)>((std::numeric_limits<DWORD>::max)());
    const auto timeout_ms = timeout_count < 0
                                ? DWORD{0}
                                : static_cast<DWORD>(
                                      timeout_count > maximum_timeout ? maximum_timeout : timeout_count);
    const auto wait_result = MsgWaitForMultipleObjects(0, nullptr, FALSE, timeout_ms, QS_ALLINPUT);
    if (wait_result == WAIT_TIMEOUT) {
        return PollStatus::continue_running;
    }
    if (wait_result == WAIT_FAILED) {
        error = windows_error("MsgWaitForMultipleObjects");
        return PollStatus::failure;
    }

    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE) != 0) {
        if (message.message == WM_QUIT) {
            return PollStatus::stop_requested;
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return PollStatus::continue_running;
}

void WindowsHookBackend::stop() noexcept {
    if (!state_) {
        return;
    }
    if (active_metrics == &state_->metrics) {
        active_metrics = nullptr;
    }
    if (state_->mouse_hook != nullptr) {
        UnhookWindowsHookEx(state_->mouse_hook);
        state_->mouse_hook = nullptr;
    }
    if (state_->keyboard_hook != nullptr) {
        UnhookWindowsHookEx(state_->keyboard_hook);
        state_->keyboard_hook = nullptr;
    }
}

}  // namespace continuous_auth::collector::spike

#endif
