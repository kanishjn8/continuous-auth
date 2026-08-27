#include "continuous_auth/collector/config.hpp"

#include <algorithm>
#include <cctype>
#include <limits>
#include <sstream>
#include <unordered_map>

namespace continuous_auth::collector {
namespace {

std::string trim(std::string value) {
  const auto not_space = [](unsigned char ch) { return std::isspace(ch) == 0; };
  value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
  value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
  return value;
}

std::string required(const std::unordered_map<std::string, std::string>& values,
                     const std::string& name) {
  const auto item = values.find(name);
  if (item == values.end() || item->second.empty()) {
    throw ConfigError("collector configuration is missing " + name);
  }
  return item->second;
}

template <typename T>
T positive_integer(const std::unordered_map<std::string, std::string>& values,
                   const std::string& name) {
  try {
    const auto parsed = std::stoull(required(values, name));
    if (parsed == 0 || parsed > static_cast<unsigned long long>((std::numeric_limits<T>::max)())) {
      throw ConfigError("collector configuration has invalid " + name);
    }
    return static_cast<T>(parsed);
  } catch (const ConfigError&) {
    throw;
  } catch (const std::exception&) {
    throw ConfigError("collector configuration has invalid " + name);
  }
}

double positive_number(const std::unordered_map<std::string, std::string>& values,
                       const std::string& name) {
  try {
    const auto parsed = std::stod(required(values, name));
    if (!(parsed > 0)) {
      throw ConfigError("collector configuration has invalid " + name);
    }
    return parsed;
  } catch (const ConfigError&) {
    throw;
  } catch (const std::exception&) {
    throw ConfigError("collector configuration has invalid " + name);
  }
}

}  // namespace

CollectorSettings load_collector_settings(std::istream& input) {
  std::unordered_map<std::string, std::string> values;
  std::string line;
  while (std::getline(input, line)) {
    const auto comment = line.find('#');
    if (comment != std::string::npos) {
      line.erase(comment);
    }
    const auto separator = line.find(':');
    if (separator == std::string::npos) {
      continue;
    }
    const auto key = trim(line.substr(0, separator));
    const auto value = trim(line.substr(separator + 1));
    if (!key.empty() && !value.empty()) {
      values[key] = value;
    }
  }

  CollectorSettings settings;
  settings.config_version = required(values, "config_version");
  settings.protocol_version = required(values, "protocol_version");
  settings.heartbeat_interval_seconds = positive_number(values, "heartbeat_interval_seconds");
  settings.event_capacity = positive_integer<std::size_t>(values, "buffer_capacity");
  settings.reconnect_initial_seconds = positive_number(values, "reconnect_initial_seconds");
  settings.reconnect_max_seconds = positive_number(values, "reconnect_max_seconds");
  settings.reconnect_multiplier = positive_number(values, "reconnect_multiplier");
  settings.poll_interval_ms = positive_integer<std::uint64_t>(values, "poll_interval_ms");
  settings.context_refresh_ms = positive_integer<std::uint64_t>(values, "context_refresh_ms");
  settings.device_refresh_seconds =
      positive_integer<std::uint64_t>(values, "device_refresh_seconds");
  settings.pipe_name = required(values, "pipe_name");
  settings.pause_event_name = required(values, "pause_event_name");
  const auto overload = required(values, "overload_policy");
  if (overload == "DROP_OLDEST") {
    settings.overload_policy = OverloadPolicy::drop_oldest;
  } else if (overload == "DROP_NEWEST") {
    settings.overload_policy = OverloadPolicy::drop_newest;
  } else {
    throw ConfigError("collector configuration has invalid overload_policy");
  }
  if (settings.reconnect_initial_seconds > settings.reconnect_max_seconds) {
    throw ConfigError("collector reconnect settings are inconsistent");
  }
  if (settings.reconnect_multiplier <= 1) {
    throw ConfigError("collector reconnect multiplier must exceed one");
  }
  return settings;
}

}  // namespace continuous_auth::collector
