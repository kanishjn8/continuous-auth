#pragma once

#include "continuous_auth/collector/bounded_buffer.hpp"
#include "continuous_auth/collector/clock.hpp"
#include "contracts.hpp"

#include <atomic>
#include <cstdint>
#include <optional>

namespace continuous_auth::collector {

class EventPublisher {
public:
  EventPublisher(MonotonicClock& clock, std::size_t capacity, OverloadPolicy policy);

  std::uint64_t capture_time_us() noexcept;
  std::int64_t next_sequence() noexcept;
  bool publish(continuous_auth::protocol::v1::EventFrame event) noexcept;
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
  BoundedBuffer<continuous_auth::protocol::v1::EventFrame> buffer_;
  std::atomic<std::int64_t> sequence_{};
  std::atomic<std::int64_t> current_app_{};
  std::atomic_bool paused_{};
  std::atomic_bool clock_failed_{};
};

}  // namespace continuous_auth::collector
