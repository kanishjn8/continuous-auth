#pragma once

#include "collector_spike/clock.hpp"

#include <cstdint>

namespace continuous_auth::collector::spike {

struct ResourceSample {
    double cpu_percent_one_core{};
    std::uint64_t resident_bytes{};
};

/** Samples current-process resource cost outside the capture callback. */
class ProcessResourceSampler final {
public:
    explicit ProcessResourceSampler(const MonotonicClock& clock) noexcept;
    ResourceSample sample() noexcept;

private:
    const MonotonicClock& clock_;
    std::uint64_t last_capture_us_{};
    std::uint64_t last_process_cpu_us_{};
};

}  // namespace continuous_auth::collector::spike
