#pragma once

#include <cstdint>

namespace continuous_auth::collector::spike {

/** A monotonic clock expressed in microseconds. */
class MonotonicClock {
public:
    virtual ~MonotonicClock() = default;
    virtual std::uint64_t now_us() const noexcept = 0;
};

/** Native monotonic capture clock with microsecond output on every supported OS. */
class PlatformMonotonicClock final : public MonotonicClock {
public:
    PlatformMonotonicClock() noexcept;

    std::uint64_t now_us() const noexcept override;
    bool valid() const noexcept;

private:
    std::uint64_t scale_numerator_{};
    std::uint64_t scale_denominator_{};
};

}  // namespace continuous_auth::collector::spike
