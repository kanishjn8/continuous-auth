#pragma once

#include "collector_spike/hook_backend.hpp"
#include "collector_spike/metrics.hpp"

#include <chrono>
#include <memory>
#include <string_view>

namespace continuous_auth::collector::spike {

#if defined(__linux__)
class LinuxX11HookBackend final : public HookBackend {
public:
    explicit LinuxX11HookBackend(CaptureMetrics& metrics) noexcept;
    ~LinuxX11HookBackend() override;

    LinuxX11HookBackend(const LinuxX11HookBackend&) = delete;
    LinuxX11HookBackend& operator=(const LinuxX11HookBackend&) = delete;

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
