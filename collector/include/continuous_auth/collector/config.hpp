#pragma once

#include <cstddef>
#include <cstdint>
#include <istream>
#include <stdexcept>
#include <string>

namespace continuous_auth::collector {

enum class OverloadPolicy { drop_oldest, drop_newest };

struct CollectorSettings {
  std::string config_version;
  std::string protocol_version;
  double heartbeat_interval_seconds{};
  std::size_t event_capacity{};
  double reconnect_initial_seconds{};
  double reconnect_max_seconds{};
  double reconnect_multiplier{};
  std::uint64_t poll_interval_ms{};
  std::uint64_t context_refresh_ms{};
  std::uint64_t device_refresh_seconds{};
  std::string pipe_name;
  std::string pause_event_name;
  OverloadPolicy overload_policy{OverloadPolicy::drop_newest};
};

class ConfigError : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};

CollectorSettings load_collector_settings(std::istream& input);

}  // namespace continuous_auth::collector
