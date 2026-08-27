#include "continuous_auth/collector/publisher.hpp"

#include <limits>

namespace continuous_auth::collector {

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
  const auto value = sequence_.fetch_add(1, std::memory_order_relaxed);
  if (value == (std::numeric_limits<std::int64_t>::max)()) {
    clock_failed_.store(true, std::memory_order_relaxed);
  }
  return value;
}

bool EventPublisher::publish(continuous_auth::protocol::v1::EventFrame event) noexcept {
  return buffer_.try_push(std::move(event));
}

std::optional<continuous_auth::protocol::v1::EventFrame> EventPublisher::next() {
  return buffer_.try_pop();
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

std::uint64_t EventPublisher::dropped() const noexcept { return buffer_.dropped(); }

std::size_t EventPublisher::high_water() const noexcept { return buffer_.high_water(); }

}  // namespace continuous_auth::collector
