#include "collector_spike/metrics.hpp"

namespace continuous_auth::collector::spike {

CaptureMetrics::CaptureMetrics(const MonotonicClock& clock) noexcept : clock_(clock) {}

void CaptureMetrics::record_keyboard() noexcept {
    record(keyboard_events_);
}

void CaptureMetrics::record_mouse() noexcept {
    record(mouse_events_);
}

void CaptureMetrics::record(std::atomic<std::uint64_t>& counter) noexcept {
    const auto captured_us = clock_.now_us();
    counter.fetch_add(1, std::memory_order_relaxed);

    std::uint64_t expected = 0;
    first_capture_us_.compare_exchange_strong(
        expected, captured_us, std::memory_order_relaxed, std::memory_order_relaxed);

    auto prior = last_capture_us_.load(std::memory_order_relaxed);
    while (true) {
        if (captured_us < prior) {
            monotonicity_violations_.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        if (last_capture_us_.compare_exchange_weak(
                prior, captured_us, std::memory_order_relaxed, std::memory_order_relaxed)) {
            return;
        }
    }
}

CaptureSnapshot CaptureMetrics::snapshot() const noexcept {
    return {
        keyboard_events_.load(std::memory_order_relaxed),
        mouse_events_.load(std::memory_order_relaxed),
        first_capture_us_.load(std::memory_order_relaxed),
        last_capture_us_.load(std::memory_order_relaxed),
        monotonicity_violations_.load(std::memory_order_relaxed),
    };
}

}  // namespace continuous_auth::collector::spike
