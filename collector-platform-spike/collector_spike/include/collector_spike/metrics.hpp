#pragma once

#include "collector_spike/clock.hpp"

#include <atomic>
#include <cstdint>

namespace continuous_auth::collector::spike {

struct CaptureSnapshot {
    std::uint64_t keyboard_events{};
    std::uint64_t mouse_events{};
    std::uint64_t first_capture_us{};
    std::uint64_t last_capture_us{};
    std::uint64_t monotonicity_violations{};
};

/**
 * Hook callbacks use this bounded, allocation-free recorder.  It retains only
 * aggregate counts and capture timestamps: no input identity or payload can
 * escape the callback boundary.
 */
class CaptureMetrics final {
public:
    explicit CaptureMetrics(const MonotonicClock& clock) noexcept;

    void record_keyboard() noexcept;
    void record_mouse() noexcept;
    CaptureSnapshot snapshot() const noexcept;

private:
    void record(std::atomic<std::uint64_t>& counter) noexcept;

    const MonotonicClock& clock_;
    std::atomic<std::uint64_t> keyboard_events_{0};
    std::atomic<std::uint64_t> mouse_events_{0};
    std::atomic<std::uint64_t> first_capture_us_{0};
    std::atomic<std::uint64_t> last_capture_us_{0};
    std::atomic<std::uint64_t> monotonicity_violations_{0};
};

}  // namespace continuous_auth::collector::spike
