#pragma once

#include "continuous_auth/collector/publisher.hpp"
#include "contracts.hpp"

#include <istream>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>

namespace continuous_auth::collector {

class ApplicationCategories {
public:
  static ApplicationCategories load(std::istream& input);
  continuous_auth::protocol::v1::ApplicationCategory category_for(
      const std::string& process_name) const noexcept;

private:
  std::unordered_map<std::string, continuous_auth::protocol::v1::ApplicationCategory> values_;
};

class ContextResolver {
public:
  virtual ~ContextResolver() = default;
  virtual void emit_focus_if_changed(EventPublisher& publisher) noexcept = 0;
  virtual void emit_device_metadata(EventPublisher& publisher) noexcept = 0;
};

std::unique_ptr<ContextResolver> make_platform_context_resolver(ApplicationCategories categories);

class PauseSignal {
public:
  virtual ~PauseSignal() = default;
  virtual bool paused() noexcept = 0;
};

std::unique_ptr<PauseSignal> make_pause_signal(const std::string& event_name);

}  // namespace continuous_auth::collector
