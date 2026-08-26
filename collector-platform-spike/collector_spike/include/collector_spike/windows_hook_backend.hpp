#pragma once

#include "collector_spike/hook_backend.hpp"
#include "collector_spike/metrics.hpp"

#include <chrono>
#include <memory>
#include <string_view>

namespace continuous_auth::collector::spike {

#if defined(_WIN32)
class WindowsHookBackend final : public HookBackend {
public:
    explicit WindowsHookBackend(CaptureMetrics& metrics) noexcept;
    ~WindowsHookBackend() override;

    WindowsHookBackend(const WindowsHookBackend&) = delete;
    WindowsHookBackend& operator=(const WindowsHookBackend&) = delete;

    std::string_view name() const noexcept override;
    bool start(std::string& error) override;
    PollStatus poll_for(std::chrono::milliseconds timeout, std::string& error) override;
    void stop() noexcept override;

private:
    struct State;
    std::unique_ptr<State> state_;
};
#endif

}  // namespace continuous_auth::collector::spike
