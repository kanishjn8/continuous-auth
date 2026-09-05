#include "continuous_auth/collector/publisher.hpp"

#include <limits>
#include <string>
#include <utility>

namespace continuous_auth::collector {
namespace {
using namespace continuous_auth::protocol::v1;

const char* mouse_type(CapturedMouseType value) noexcept {
  switch (value) {
    case CapturedMouseType::move: return "MOVE";
    case CapturedMouseType::button_down: return "BUTTON_DOWN";
    case CapturedMouseType::button_up: return "BUTTON_UP";
    case CapturedMouseType::scroll: return "SCROLL";
  }
  return "MOVE";
}
}  // namespace

EventPublisher::EventPublisher(MonotonicClock& clock, std::size_t capacity,
                               OverloadPolicy policy)
    : clock_(clock), buffer_(capacity, policy) {}

std::uint64_t EventPublisher::capture_time_us() noexcept {
  const auto captured = clock_.now_us();
  if (captured == 0) {
    clock_failed_.store(true, std::memory_order_relaxed);
  }
  return captured;
}

std::int64_t EventPublisher::next_sequence() noexcept {
  auto value = sequence_.load(std::memory_order_relaxed);
  while (true) {
    if (value == (std::numeric_limits<std::int64_t>::max)()) {
      clock_failed_.store(true, std::memory_order_relaxed);
      return value;
    }
    const auto next = value + 1;
    if (sequence_.compare_exchange_weak(value, next, std::memory_order_relaxed,
                                       std::memory_order_relaxed)) {
      return value;
    }
  }
}

bool EventPublisher::publish(continuous_auth::protocol::v1::EventFrame event) noexcept {
  return buffer_.try_push(BufferedEvent(std::move(event)));
}

bool EventPublisher::publish_keyboard(CapturedKeyboardEvent event) noexcept {
  input_captured_.store(true, std::memory_order_relaxed);
  return buffer_.try_push(BufferedEvent(event));
}

bool EventPublisher::publish_mouse(CapturedMouseEvent event) noexcept {
  input_captured_.store(true, std::memory_order_relaxed);
  return buffer_.try_push(BufferedEvent(event));
}

std::optional<continuous_auth::protocol::v1::EventFrame> EventPublisher::next() {
  auto buffered = buffer_.try_pop();
  if (!buffered.has_value()) return std::nullopt;
  if (auto* event = std::get_if<EventFrame>(&*buffered)) return std::move(*event);
  if (const auto* event = std::get_if<CapturedKeyboardEvent>(&*buffered)) {
    return EventFrame(KeyboardEvent{
        std::string(kProtocolVersion), event->is_down ? "KEY_DOWN" : "KEY_UP",
        event->t_capture_us, event->key_class, event->is_repeat, event->device_class,
        event->app_id, event->seq});
  }
  const auto& event = std::get<CapturedMouseEvent>(*buffered);
  return EventFrame(MouseEvent{
      std::string(kProtocolVersion), mouse_type(event.type), event.t_capture_us, event.x,
      event.y, event.button, event.scroll_dx, event.scroll_dy, event.device_class,
      event.app_id, event.seq});
}

void EventPublisher::set_paused(bool value) noexcept {
  paused_.store(value, std::memory_order_relaxed);
}

bool EventPublisher::paused() const noexcept {
  return paused_.load(std::memory_order_relaxed);
}

void EventPublisher::set_current_app(std::int64_t value) noexcept {
  current_app_.store(value, std::memory_order_relaxed);
}

std::int64_t EventPublisher::current_app() const noexcept {
  return current_app_.load(std::memory_order_relaxed);
}

bool EventPublisher::clock_failed() const noexcept {
  return clock_failed_.load(std::memory_order_relaxed);
}

bool EventPublisher::input_captured() const noexcept {
  return input_captured_.load(std::memory_order_relaxed);
}

std::uint64_t EventPublisher::dropped() const noexcept { return buffer_.dropped(); }

std::size_t EventPublisher::high_water() const noexcept { return buffer_.high_water(); }

}  // namespace continuous_auth::collector
