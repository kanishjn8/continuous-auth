#include "collector_spike/process_resource_sampler.hpp"

#if defined(_WIN32)
#include <windows.h>
#include <psapi.h>
#elif defined(__APPLE__)
#include <mach/mach.h>
#include <sys/resource.h>
#elif defined(__linux__)
#include <sys/resource.h>
#include <unistd.h>

#include <fstream>
#endif

namespace continuous_auth::collector::spike {

namespace {

#if defined(_WIN32)
std::uint64_t file_time_to_u64(FILETIME value) noexcept {
    ULARGE_INTEGER raw{};
    raw.LowPart = value.dwLowDateTime;
    raw.HighPart = value.dwHighDateTime;
    return raw.QuadPart;
}

std::uint64_t process_cpu_us() noexcept {
    FILETIME created{};
    FILETIME exited{};
    FILETIME kernel{};
    FILETIME user{};
    if (GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user) == 0) {
        return 0;
    }
    return (file_time_to_u64(kernel) + file_time_to_u64(user)) / 10ULL;
}

std::uint64_t resident_bytes() noexcept {
    PROCESS_MEMORY_COUNTERS_EX memory{};
    if (GetProcessMemoryInfo(
            GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&memory), sizeof(memory)) == 0) {
        return 0;
    }
    return static_cast<std::uint64_t>(memory.WorkingSetSize);
}
#elif defined(__APPLE__) || defined(__linux__)
std::uint64_t timeval_us(const timeval& value) noexcept {
    return static_cast<std::uint64_t>(value.tv_sec) * 1'000'000ULL +
           static_cast<std::uint64_t>(value.tv_usec);
}

std::uint64_t process_cpu_us() noexcept {
    rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) != 0) {
        return 0;
    }
    return timeval_us(usage.ru_utime) + timeval_us(usage.ru_stime);
}

#if defined(__APPLE__)
std::uint64_t resident_bytes() noexcept {
    mach_task_basic_info_data_t info{};
    mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
    if (task_info(
            mach_task_self(),
            MACH_TASK_BASIC_INFO,
            reinterpret_cast<task_info_t>(&info),
            &count) != KERN_SUCCESS) {
        return 0;
    }
    return static_cast<std::uint64_t>(info.resident_size);
}
#else
std::uint64_t resident_bytes() noexcept {
    std::ifstream statm("/proc/self/statm");
    std::uint64_t total_pages = 0;
    std::uint64_t resident_pages = 0;
    if (!(statm >> total_pages >> resident_pages)) {
        return 0;
    }
    static_cast<void>(total_pages);
    const auto page_size = sysconf(_SC_PAGESIZE);
    return page_size > 0 ? resident_pages * static_cast<std::uint64_t>(page_size) : 0;
}
#endif
#else
std::uint64_t process_cpu_us() noexcept {
    return 0;
}

std::uint64_t resident_bytes() noexcept {
    return 0;
}
#endif

}  // namespace

ProcessResourceSampler::ProcessResourceSampler(const MonotonicClock& clock) noexcept
    : clock_(clock), last_capture_us_(clock.now_us()), last_process_cpu_us_(process_cpu_us()) {}

ResourceSample ProcessResourceSampler::sample() noexcept {
    ResourceSample result{};
    result.resident_bytes = resident_bytes();

    const auto now_capture_us = clock_.now_us();
    const auto now_process_cpu_us = process_cpu_us();
    if (now_capture_us > last_capture_us_ && now_process_cpu_us >= last_process_cpu_us_) {
        const auto elapsed_us = now_capture_us - last_capture_us_;
        const auto cpu_us = now_process_cpu_us - last_process_cpu_us_;
        result.cpu_percent_one_core =
            (static_cast<double>(cpu_us) * 100.0) / static_cast<double>(elapsed_us);
    }

    last_capture_us_ = now_capture_us;
    last_process_cpu_us_ = now_process_cpu_us;
    return result;
}

}  // namespace continuous_auth::collector::spike
