#pragma once

#include "continuous_auth/collector/bounded_buffer.hpp"
#include "continuous_auth/collector/clock.hpp"
#include "contracts.hpp"

#include <atomic>
#include <cstdint>
#include <optional>
#include <variant>

namespace continuous_auth::collector {

struct CapturedKeyboardEvent {
  bool is_down;
  std::int64_t t_capture_us;
  continuous_auth::protocol::v1::KeyClass key_class;
  bool is_repeat;
  continuous_auth::protocol::v1::InputDeviceClass device_class;
  std::int64_t app_id;
  std::int64_t seq;
};

enum class CapturedMouseType { move, button_down, button_up, scroll };

struct CapturedMouseEvent {
  CapturedMouseType type;
  std::int64_t t_capture_us;
  std::int64_t x;
  std::int64_t y;
  std::optional<continuous_auth::protocol::v1::MouseButton> button;
  std::int64_t scroll_dx;
  std::int64_t scroll_dy;
  continuous_auth::protocol::v1::InputDeviceClass device_class;
  std::int64_t app_id;
  std::int64_t seq;
};

using BufferedEvent = std::variant<CapturedKeyboardEvent, CapturedMouseEvent,
                                   continuous_auth::protocol::v1::EventFrame>;

class EventPublisher {
public:
  EventPublisher(MonotonicClock& clock, std::size_t capacity, OverloadPolicy policy);

  std::uint64_t capture_time_us() noexcept;
  std::int64_t next_sequence() noexcept;
  bool publish(continuous_auth::protocol::v1::EventFrame event) noexcept;
  bool publish_keyboard(CapturedKeyboardEvent event) noexcept;
  bool publish_mouse(CapturedMouseEvent event) noexcept;
  std::optional<continuous_auth::protocol::v1::EventFrame> next();

  void set_paused(bool value) noexcept;
  bool paused() const noexcept;
  void set_current_app(std::int64_t value) noexcept;
  std::int64_t current_app() const noexcept;
  bool clock_failed() const noexcept;
  std::uint64_t dropped() const noexcept;
  std::size_t high_water() const noexcept;

private:
  MonotonicClock& clock_;
  BoundedBuffer<BufferedEvent> buffer_;
  std::atomic<std::int64_t> sequence_{};
  std::atomic<std::int64_t> current_app_{};
  std::atomic_bool paused_{};
  std::atomic_bool clock_failed_{};
};

}  // namespace continuous_auth::collector
