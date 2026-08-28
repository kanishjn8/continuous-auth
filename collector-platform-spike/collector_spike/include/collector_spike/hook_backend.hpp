#pragma once

#include "collector_spike/metrics.hpp"

#include <chrono>
#include <memory>
#include <string>
#include <string_view>

namespace continuous_auth::collector::spike {

enum class PollStatus {
    continue_running,
    stop_requested,
    failure,
};

/**
 * Cross-platform boundary for native global-input capture.
 *
 * Platform implementations may receive native input payloads, but they must
 * reduce them to content-free metrics inside their callbacks. Native payloads
 * may never be retained or returned through this interface.
 */
class HookBackend {
public:
    virtual ~HookBackend() = default;

    virtual std::string_view name() const noexcept = 0;
    virtual bool start(std::string& error) = 0;
    virtual PollStatus poll_for(std::chrono::milliseconds timeout, std::string& error) = 0;
    virtual void stop() noexcept = 0;
};

/** Selects the native implementation at compile time. */
std::unique_ptr<HookBackend> make_platform_hook_backend(CaptureMetrics& metrics);

}  // namespace continuous_auth::collector::spike
