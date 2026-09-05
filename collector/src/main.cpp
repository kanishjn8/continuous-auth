#include "continuous_auth/collector/clock.hpp"
#include "continuous_auth/collector/config.hpp"
#include "continuous_auth/collector/context.hpp"
#include "continuous_auth/collector/frame_encoder.hpp"
#include "continuous_auth/collector/hook_backend.hpp"
#include "continuous_auth/collector/publisher.hpp"
#include "continuous_auth/collector/transport.hpp"
#include "contracts.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>

namespace {
using continuous_auth::protocol::v1::EventFrame;

std::atomic_bool stop_requested{};

void request_stop(int) { stop_requested.store(true, std::memory_order_relaxed); }

struct Arguments {
  std::string config;
  std::string categories;
};

std::optional<Arguments> parse_arguments(int argc, char** argv) {
  Arguments result;
  for (int index = 1; index < argc; ++index) {
    const std::string option(argv[index]);
    if (index + 1 >= argc) return std::nullopt;
    if (option == "--config") result.config = argv[++index];
    else if (option == "--categories") result.categories = argv[++index];
    else return std::nullopt;
  }
  if (result.config.empty() || result.categories.empty()) return std::nullopt;
  return result;
}

std::uint64_t seconds_to_us(double value) {
  return static_cast<std::uint64_t>(value * 1'000'000.0);
}

}  // namespace

int main(int argc, char** argv) {
  const auto arguments = parse_arguments(argc, argv);
  if (!arguments.has_value()) {
    std::cerr << "usage: continuous_auth_collector --config <config> --categories <categories>\n";
    return 2;
  }
  std::ifstream config_input(arguments->config);
  std::ifstream category_input(arguments->categories);
  if (!config_input || !category_input) {
    std::cerr << "collector configuration could not be opened\n";
    return 2;
  }

  continuous_auth::collector::CollectorSettings settings;
  continuous_auth::collector::ApplicationCategories categories;
  try {
    settings = continuous_auth::collector::load_collector_settings(config_input);
    categories = continuous_auth::collector::ApplicationCategories::load(category_input);
  } catch (const std::exception& error) {
    std::cerr << "collector configuration rejected: " << error.what() << '\n';
    return 2;
  }
  if (settings.protocol_version != continuous_auth::protocol::v1::kProtocolVersion) {
    std::cerr << "collector protocol version mismatch\n";
    return 2;
  }

  continuous_auth::collector::PlatformMonotonicClock clock;
  if (!clock.valid()) {
    std::cerr << "capture-time monotonic clock is unavailable\n";
    return 3;
  }
  continuous_auth::collector::EventPublisher publisher(
      clock, settings.event_capacity, settings.overload_policy);
  auto hooks = continuous_auth::collector::make_platform_hook_backend(publisher);
  auto context = continuous_auth::collector::make_platform_context_resolver(std::move(categories));
  auto pause = continuous_auth::collector::make_pause_signal(settings.pause_event_name);
  auto transport = continuous_auth::collector::make_named_pipe_transport(settings.pipe_name);

  std::string hook_error;
  if (!hooks->start(hook_error)) {
    std::cerr << "collector hook startup failed: " << hook_error << '\n';
    return 4;
  }
  std::signal(SIGINT, request_stop);
  std::signal(SIGTERM, request_stop);
  std::cout << "[collector] Ready; waiting for keyboard or mouse activity.\n" << std::flush;

  const auto started_us = clock.now_us();
  auto next_context_us = started_us;
  auto next_device_us = started_us;
  auto next_heartbeat_us = started_us;
  auto next_connect_us = started_us;
  auto reconnect_us = seconds_to_us(settings.reconnect_initial_seconds);
  const auto reconnect_max_us = seconds_to_us(settings.reconnect_max_seconds);
  const auto context_period_us = settings.context_refresh_ms * 1'000ULL;
  const auto device_period_us = settings.device_refresh_seconds * 1'000'000ULL;
  const auto heartbeat_period_us = seconds_to_us(settings.heartbeat_interval_seconds);
  std::optional<EventFrame> pending;
  int exit_code = 0;
  bool capture_confirmed = false;

  while (!stop_requested.load(std::memory_order_relaxed)) {
    if (!capture_confirmed && publisher.input_captured()) {
      std::cout << "[collector] Events are being captured.\n" << std::flush;
      capture_confirmed = true;
    }
    const auto now_us = clock.now_us();
    if (now_us == 0 || publisher.clock_failed()) {
      std::cerr << "capture-time monotonic clock failed\n";
      exit_code = 5;
      break;
    }

    publisher.set_paused(pause->paused());
    if (!publisher.paused() && now_us >= next_context_us) {
      context->emit_focus_if_changed(publisher);
      next_context_us = now_us + context_period_us;
    }
    if (!publisher.paused() && now_us >= next_device_us) {
      context->emit_device_metadata(publisher);
      next_device_us = now_us + device_period_us;
    }
    if (now_us >= next_heartbeat_us) {
      publisher.publish(continuous_auth::protocol::v1::Heartbeat{
          std::string(continuous_auth::protocol::v1::kProtocolVersion), "HEARTBEAT",
          static_cast<std::int64_t>(now_us), static_cast<std::int64_t>((now_us - started_us) / 1'000ULL),
          static_cast<std::int64_t>(publisher.dropped()),
          static_cast<std::int64_t>(publisher.high_water()), publisher.paused(),
          publisher.next_sequence()});
      next_heartbeat_us = now_us + heartbeat_period_us;
    }

    if (!transport->connected() && now_us >= next_connect_us) {
      if (transport->connect()) {
        reconnect_us = seconds_to_us(settings.reconnect_initial_seconds);
      } else {
        next_connect_us = now_us + reconnect_us;
        reconnect_us = (std::min)(
            static_cast<std::uint64_t>(reconnect_us * settings.reconnect_multiplier),
            reconnect_max_us);
      }
    }
    while (transport->connected()) {
      if (!pending.has_value()) pending = publisher.next();
      if (!pending.has_value()) break;
      const auto frame = continuous_auth::collector::length_prefix(
          continuous_auth::collector::encode_event_json(*pending));
      if (!transport->send(frame)) {
        next_connect_us = now_us + reconnect_us;
        reconnect_us = (std::min)(
            static_cast<std::uint64_t>(reconnect_us * settings.reconnect_multiplier),
            reconnect_max_us);
        break;
      }
      pending.reset();
    }

    const auto poll = hooks->poll_for(std::chrono::milliseconds(settings.poll_interval_ms), hook_error);
    if (poll == continuous_auth::collector::PollStatus::stop_requested) break;
    if (poll == continuous_auth::collector::PollStatus::failure) {
      std::cerr << "collector hook processing failed: " << hook_error << '\n';
      exit_code = 6;
      break;
    }
  }

  hooks->stop();
  transport->close();
  return exit_code;
}
