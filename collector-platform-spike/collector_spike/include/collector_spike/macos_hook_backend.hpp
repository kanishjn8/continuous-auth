#pragma once

#include "collector_spike/hook_backend.hpp"
#include "collector_spike/metrics.hpp"

#include <chrono>
#include <memory>
#include <string_view>

namespace continuous_auth::collector::spike {

#if defined(__APPLE__)
class MacOSHookBackend final : public HookBackend {
public:
    struct State;

    explicit MacOSHookBackend(CaptureMetrics& metrics) noexcept;
    ~MacOSHookBackend() override;

    MacOSHookBackend(const MacOSHookBackend&) = delete;
    MacOSHookBackend& operator=(const MacOSHookBackend&) = delete;

    std::string_view name() const noexcept override;
    bool start(std::string& error) override;
    PollStatus poll_for(std::chrono::milliseconds timeout, std::string& error) override;
    void stop() noexcept override;

private:
    std::unique_ptr<State> state_;
};
#endif

}  // namespace continuous_auth::collector::spike
