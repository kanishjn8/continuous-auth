#include "continuous_auth/collector/clock.hpp"

#if defined(_WIN32)
#include <windows.h>
#elif defined(__APPLE__)
#include <mach/mach_time.h>
#elif defined(__linux__)
#include <time.h>
#endif

namespace continuous_auth::collector {
namespace {

std::uint64_t scale_ticks(std::uint64_t ticks, std::uint64_t numerator,
                          std::uint64_t denominator) noexcept {
  if (denominator == 0) {
    return 0;
  }
  return (ticks / denominator) * numerator + ((ticks % denominator) * numerator) / denominator;
}

}  // namespace

PlatformMonotonicClock::PlatformMonotonicClock() noexcept {
#if defined(_WIN32)
  LARGE_INTEGER frequency{};
  if (QueryPerformanceFrequency(&frequency) != 0 && frequency.QuadPart > 0) {
    scale_numerator_ = 1'000'000ULL;
    scale_denominator_ = static_cast<std::uint64_t>(frequency.QuadPart);
  }
#elif defined(__APPLE__)
  mach_timebase_info_data_t scale{};
  if (mach_timebase_info(&scale) == KERN_SUCCESS && scale.denom != 0) {
    scale_numerator_ = static_cast<std::uint64_t>(scale.numer);
    scale_denominator_ = static_cast<std::uint64_t>(scale.denom) * 1'000ULL;
  }
#elif defined(__linux__)
  scale_numerator_ = 1;
  scale_denominator_ = 1;
#endif
}

std::uint64_t PlatformMonotonicClock::now_us() const noexcept {
#if defined(_WIN32)
  LARGE_INTEGER counter{};
  if (!valid() || QueryPerformanceCounter(&counter) == 0 || counter.QuadPart < 0) {
    return 0;
  }
  return scale_ticks(static_cast<std::uint64_t>(counter.QuadPart), scale_numerator_,
                     scale_denominator_);
#elif defined(__APPLE__)
  return valid() ? scale_ticks(mach_continuous_time(), scale_numerator_, scale_denominator_) : 0;
#elif defined(__linux__)
  timespec value{};
  if (!valid() || clock_gettime(CLOCK_MONOTONIC_RAW, &value) != 0 || value.tv_sec < 0 ||
      value.tv_nsec < 0) {
    return 0;
  }
  return static_cast<std::uint64_t>(value.tv_sec) * 1'000'000ULL +
         static_cast<std::uint64_t>(value.tv_nsec) / 1'000ULL;
#else
  return 0;
#endif
}

bool PlatformMonotonicClock::valid() const noexcept {
  return scale_numerator_ != 0 && scale_denominator_ != 0;
}

}  // namespace continuous_auth::collector
