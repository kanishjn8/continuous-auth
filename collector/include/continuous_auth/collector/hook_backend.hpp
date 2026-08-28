#pragma once

#include "continuous_auth/collector/publisher.hpp"

#include <chrono>
#include <memory>
#include <string>

namespace continuous_auth::collector {

enum class PollStatus { continue_running, stop_requested, failure };

class HookBackend {
public:
  virtual ~HookBackend() = default;
  virtual bool start(std::string& error) = 0;
  virtual PollStatus poll_for(std::chrono::milliseconds duration, std::string& error) = 0;
  virtual void stop() noexcept = 0;
};

std::unique_ptr<HookBackend> make_platform_hook_backend(EventPublisher& publisher);

}  // namespace continuous_auth::collector
