#include "continuous_auth/collector/bounded_buffer.hpp"
#include "continuous_auth/collector/clock.hpp"
#include "continuous_auth/collector/config.hpp"
#include "continuous_auth/collector/context.hpp"
#include "continuous_auth/collector/frame_encoder.hpp"
#include "continuous_auth/collector/publisher.hpp"
#include "contracts.hpp"

#include <cstdint>
#include <sstream>
#include <string>

namespace {

class FakeClock final : public continuous_auth::collector::MonotonicClock {
public:
  std::uint64_t now_us() const noexcept override { return value; }
  std::uint64_t value{123456};
};

bool buffer_test() {
  using continuous_auth::collector::BoundedBuffer;
  using continuous_auth::collector::OverloadPolicy;
  BoundedBuffer<int> buffer(2, OverloadPolicy::drop_oldest);
  return buffer.try_push(1) && buffer.try_push(2) && buffer.try_push(3) &&
         buffer.dropped() == 1 && buffer.high_water() == 2 && buffer.try_pop() == 2 &&
         buffer.try_pop() == 3 && !buffer.try_pop().has_value();
}

bool config_test() {
  std::istringstream source(R"(config_version: test
protocol_version: 1.0.0
collector:
  heartbeat_interval_seconds: 1.0
  buffer_capacity: 4
  reconnect_initial_seconds: 0.5
  reconnect_max_seconds: 2.0
  reconnect_multiplier: 2.0
  poll_interval_ms: 5
  context_refresh_ms: 10
  device_refresh_seconds: 20
  pipe_name: synthetic-pipe
  pause_event_name: synthetic-pause
  overload_policy: DROP_NEWEST
)");
  const auto config = continuous_auth::collector::load_collector_settings(source);
  return config.protocol_version == "1.0.0" && config.event_capacity == 4 &&
         config.overload_policy == continuous_auth::collector::OverloadPolicy::drop_newest;
}

bool publisher_and_frame_test() {
  using namespace continuous_auth::protocol::v1;
  FakeClock clock;
  continuous_auth::collector::EventPublisher publisher(
      clock, 2, continuous_auth::collector::OverloadPolicy::drop_newest);
  KeyboardEvent event{std::string(kProtocolVersion), "KEY_DOWN",
                      static_cast<std::int64_t>(publisher.capture_time_us()),
                      KeyClass::kAlphaLHome, false, InputDeviceClass::kUnknown, 7,
                      publisher.next_sequence()};
  const auto json = continuous_auth::collector::encode_event_json(EventFrame(event));
  const auto framed = continuous_auth::collector::length_prefix(json);
  const auto length = (static_cast<std::uint32_t>(framed[0]) << 24U) |
                      (static_cast<std::uint32_t>(framed[1]) << 16U) |
                      (static_cast<std::uint32_t>(framed[2]) << 8U) |
                      static_cast<std::uint32_t>(framed[3]);
  return json.find("ALPHA_L_HOME") != std::string::npos &&
         json.find("keycode") == std::string::npos &&
         length == json.size() && publisher.publish(EventFrame(std::move(event))) &&
         publisher.next().has_value();
}

bool category_test() {
  using continuous_auth::protocol::v1::ApplicationCategory;
  std::istringstream source("{\n\"synthetic.exe\": \"DEVELOPMENT\"\n}\n");
  const auto categories = continuous_auth::collector::ApplicationCategories::load(source);
  return categories.category_for("SYNTHETIC.EXE") == ApplicationCategory::kDevelopment &&
         categories.category_for("unseen.exe") == ApplicationCategory::kUnknown;
}

}  // namespace

int main() {
  continuous_auth::collector::PlatformMonotonicClock clock;
  if (!clock.valid() || clock.now_us() == 0) return 1;
  if (!buffer_test()) return 2;
  if (!config_test()) return 3;
  if (!publisher_and_frame_test()) return 4;
  if (!category_test()) return 5;
  return 0;
}
