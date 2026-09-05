#include "continuous_auth/collector/bounded_buffer.hpp"
#include "continuous_auth/collector/clock.hpp"
#include "continuous_auth/collector/config.hpp"
#include "continuous_auth/collector/context.hpp"
#include "continuous_auth/collector/frame_encoder.hpp"
#include "continuous_auth/collector/publisher.hpp"
#include "continuous_auth/collector/transport.hpp"
#include "contracts.hpp"

#include <cstdint>
#include <sstream>
#include <string>

#if defined(__APPLE__)
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <semaphore.h>
#include <thread>
#include <vector>
#endif

namespace {

class FakeClock final : public continuous_auth::collector::MonotonicClock {
public:
  std::uint64_t now_us() const noexcept override { return value; }
  std::uint64_t value{123456};
};

bool capture_confirmation_test() {
  using namespace continuous_auth::collector;
  using namespace continuous_auth::protocol::v1;
  FakeClock clock;
  EventPublisher keyboard(clock, 2, OverloadPolicy::drop_newest);
  EventPublisher mouse(clock, 2, OverloadPolicy::drop_newest);
  if (keyboard.input_captured() || mouse.input_captured()) return false;
  keyboard.publish(EventFrame{Heartbeat{}});
  if (keyboard.input_captured()) return false;
  keyboard.publish_keyboard(CapturedKeyboardEvent{});
  mouse.publish_mouse(CapturedMouseEvent{});
  if (!keyboard.input_captured() || !mouse.input_captured()) return false;
  keyboard.next();
  keyboard.next();
  mouse.next();
  return keyboard.input_captured() && mouse.input_captured();
}

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

bool config_rejects_unknown_fields() {
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
  raw_capture: true
)");
  try {
    static_cast<void>(continuous_auth::collector::load_collector_settings(source));
    return false;
  } catch (const continuous_auth::collector::ConfigError&) {
    return true;
  }
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

bool callback_event_conversion_test() {
  using namespace continuous_auth::protocol::v1;
  FakeClock clock;
  continuous_auth::collector::EventPublisher publisher(
      clock, 2, continuous_auth::collector::OverloadPolicy::drop_newest);
  if (!publisher.publish_keyboard(continuous_auth::collector::CapturedKeyboardEvent{
          false, 10, KeyClass::kEnter, false, InputDeviceClass::kUnknown, 2, 3})) {
    return false;
  }
  const auto keyboard_frame = publisher.next();
  const auto* keyboard =
      keyboard_frame.has_value() ? std::get_if<KeyboardEvent>(&*keyboard_frame) : nullptr;
  if (keyboard == nullptr || keyboard->type != "KEY_UP" || keyboard->key_class != KeyClass::kEnter) {
    return false;
  }
  if (!publisher.publish_mouse(continuous_auth::collector::CapturedMouseEvent{
          continuous_auth::collector::CapturedMouseType::scroll, 11, 20, 30, std::nullopt,
          -120, 0, InputDeviceClass::kUnknown, 2, 4})) {
    return false;
  }
  const auto mouse_frame = publisher.next();
  const auto* mouse = mouse_frame.has_value() ? std::get_if<MouseEvent>(&*mouse_frame) : nullptr;
  return mouse != nullptr && mouse->type == "SCROLL" && mouse->scroll_dx == -120 &&
         mouse->scroll_dy == 0;
}

bool category_test() {
  using continuous_auth::protocol::v1::ApplicationCategory;
  std::istringstream source("{\n\"synthetic.exe\": \"DEVELOPMENT\"\n}\n");
  const auto categories = continuous_auth::collector::ApplicationCategories::load(source);
  return categories.category_for("SYNTHETIC.EXE") == ApplicationCategory::kDevelopment &&
         categories.category_for("unseen.exe") == ApplicationCategory::kUnknown;
}

#if defined(__APPLE__)
bool macos_transport_test() {
  const auto name = "synthetic-transport-" + std::to_string(static_cast<unsigned long>(getpid()));
  const auto directory =
      "/tmp/continuous-auth-" + std::to_string(static_cast<unsigned long>(getuid()));
  const auto endpoint = directory + "/" + name + ".sock";
  if (mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST) return false;
  if (chmod(directory.c_str(), 0700) != 0) return false;
  unlink(endpoint.c_str());

  const auto listener = socket(AF_UNIX, SOCK_STREAM, 0);
  if (listener < 0) return false;
  sockaddr_un address{};
  if (endpoint.size() >= sizeof(address.sun_path)) {
    close(listener);
    return false;
  }
  address.sun_family = AF_UNIX;
  std::memcpy(address.sun_path, endpoint.c_str(), endpoint.size() + 1);
  if (bind(listener, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0 ||
      listen(listener, 1) != 0) {
    close(listener);
    unlink(endpoint.c_str());
    return false;
  }

  const std::vector<std::uint8_t> expected{0, 0, 0, 3, 'C', '1', '!'};
  std::vector<std::uint8_t> received(expected.size());
  auto transport = continuous_auth::collector::make_named_pipe_transport(name);
  if (!transport->connect()) {
    close(listener);
    unlink(endpoint.c_str());
    return false;
  }
  std::thread receiver([&]() {
    const auto connection = accept(listener, nullptr, nullptr);
    if (connection < 0) return;
    std::size_t offset = 0;
    while (offset < received.size()) {
      const auto count = recv(connection, received.data() + offset, received.size() - offset, 0);
      if (count <= 0) break;
      offset += static_cast<std::size_t>(count);
    }
    close(connection);
  });
  const auto sent = transport->send(expected);
  transport->close();
  receiver.join();
  close(listener);
  unlink(endpoint.c_str());
  return sent && received == expected;
}

bool macos_pause_signal_test() {
  const auto name =
      "synthetic-pause-" + std::to_string(static_cast<unsigned long>(getpid()));
  auto pause = continuous_auth::collector::make_pause_signal(name);
  const auto semaphore_name = "/" + name;
  auto* semaphore = sem_open(semaphore_name.c_str(), 0);
  if (semaphore == SEM_FAILED) return false;
  const auto initially_active = !pause->paused();
  const auto posted = sem_post(semaphore) == 0;
  const auto paused = pause->paused();
  const auto reset = sem_trywait(semaphore) == 0;
  const auto resumed = !pause->paused();
  sem_close(semaphore);
  return initially_active && posted && paused && reset && resumed;
}
#endif

}  // namespace

int main() {
  continuous_auth::collector::PlatformMonotonicClock clock;
  if (!clock.valid() || clock.now_us() == 0) return 1;
  if (!buffer_test()) return 2;
  if (!config_test()) return 3;
  if (!config_rejects_unknown_fields()) return 4;
  if (!publisher_and_frame_test()) return 5;
  if (!category_test()) return 6;
  if (!callback_event_conversion_test()) return 7;
  if (!capture_confirmation_test()) return 10;
#if defined(__APPLE__)
  if (!macos_transport_test()) return 8;
  if (!macos_pause_signal_test()) return 9;
#endif
  return 0;
}
