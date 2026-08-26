#include "collector_spike/clock.hpp"
#include "collector_spike/hook_backend.hpp"
#include "collector_spike/metrics.hpp"
#include "collector_spike/process_resource_sampler.hpp"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>

#if defined(_WIN32)
#include <windows.h>
#endif

namespace {

std::atomic<bool> shutdown_requested{false};

#if defined(_WIN32)
BOOL WINAPI console_handler(DWORD signal) {
    if (signal == CTRL_C_EVENT || signal == CTRL_BREAK_EVENT || signal == CTRL_CLOSE_EVENT) {
        shutdown_requested.store(true, std::memory_order_relaxed);
        return TRUE;
    }
    return FALSE;
}
#else
void signal_handler(int) {
    shutdown_requested.store(true, std::memory_order_relaxed);
}
#endif

bool install_shutdown_handlers(std::string& error) {
#if defined(_WIN32)
    if (SetConsoleCtrlHandler(console_handler, TRUE) == 0) {
        error = "SetConsoleCtrlHandler failed; Windows error=" + std::to_string(GetLastError());
        return false;
    }
#else
    if (std::signal(SIGINT, signal_handler) == SIG_ERR || std::signal(SIGTERM, signal_handler) == SIG_ERR) {
        error = "Unable to install POSIX shutdown signal handlers.";
        return false;
    }
#endif
    return true;
}

void uninstall_shutdown_handlers() noexcept {
#if defined(_WIN32)
    SetConsoleCtrlHandler(console_handler, FALSE);
#else
    std::signal(SIGINT, SIG_DFL);
    std::signal(SIGTERM, SIG_DFL);
#endif
}

bool parse_report_interval(int argc, char** argv, std::chrono::milliseconds& interval) {
    if (argc != 3 || std::string(argv[1]) != "--report-interval-ms") {
        return false;
    }

    errno = 0;
    char* end = nullptr;
    const auto parsed = std::strtoull(argv[2], &end, 10);
    if (errno != 0 || end == argv[2] || *end != '\0' || parsed < 1'000ULL || parsed > 60'000ULL ||
        parsed >
            static_cast<unsigned long long>((std::numeric_limits<std::int64_t>::max)())) {
        return false;
    }
    interval = std::chrono::milliseconds(parsed);
    return true;
}

void print_sample(
    const continuous_auth::collector::spike::CaptureMetrics& metrics,
    continuous_auth::collector::spike::ProcessResourceSampler& resources) {
    const auto captures = metrics.snapshot();
    const auto resource = resources.sample();
    std::cout << "capture_snapshot"
              << " keyboard_events=" << captures.keyboard_events
              << " mouse_events=" << captures.mouse_events
              << " first_capture_us=" << captures.first_capture_us
              << " last_capture_us=" << captures.last_capture_us
              << " monotonicity_violations=" << captures.monotonicity_violations
              << " cpu_percent_one_core=" << std::fixed << std::setprecision(3)
              << resource.cpu_percent_one_core
              << " resident_bytes=" << resource.resident_bytes << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    std::chrono::milliseconds report_interval{};
    if (!parse_report_interval(argc, argv, report_interval)) {
        std::cerr << "Usage: native_collector_spike --report-interval-ms 1000..60000\n";
        return 2;
    }

    std::string error;
    if (!install_shutdown_handlers(error)) {
        std::cerr << "collector startup failed: " << error << '\n';
        return 1;
    }

    continuous_auth::collector::spike::PlatformMonotonicClock clock;
    if (!clock.valid()) {
        std::cerr << "collector startup failed: native monotonic clock initialization failed\n";
        uninstall_shutdown_handlers();
        return 1;
    }

    continuous_auth::collector::spike::CaptureMetrics metrics(clock);
    auto hooks = continuous_auth::collector::spike::make_platform_hook_backend(metrics);
    if (!hooks) {
        std::cerr << "collector startup failed: this operating system has no supported capture backend\n";
        uninstall_shutdown_handlers();
        return 3;
    }
    if (!hooks->start(error)) {
        std::cerr << "collector startup failed: backend=" << hooks->name() << " error=" << error << '\n';
        uninstall_shutdown_handlers();
        return 1;
    }

    continuous_auth::collector::spike::ProcessResourceSampler resources(clock);
    auto next_report_us = clock.now_us() + static_cast<std::uint64_t>(report_interval.count()) * 1'000ULL;
    constexpr auto poll_interval = std::chrono::milliseconds(100);
    int exit_code = 0;

    std::cout << "collector spike active; backend=" << hooks->name()
              << "; only aggregate keyboard/mouse counts and monotonic timestamps are emitted. "
                 "Press Ctrl+C to stop.\n";

    while (!shutdown_requested.load(std::memory_order_relaxed)) {
        error.clear();
        const auto poll_status = hooks->poll_for(poll_interval, error);
        if (poll_status == continuous_auth::collector::spike::PollStatus::failure) {
            std::cerr << "collector runtime failed: backend=" << hooks->name() << " error=" << error << '\n';
            exit_code = 1;
            break;
        }
        if (poll_status == continuous_auth::collector::spike::PollStatus::stop_requested) {
            break;
        }

        const auto now_us = clock.now_us();
        if (now_us == 0) {
            std::cerr << "collector runtime failed: native monotonic clock read failed\n";
            exit_code = 1;
            break;
        }
        if (now_us >= next_report_us) {
            print_sample(metrics, resources);
            next_report_us = now_us + static_cast<std::uint64_t>(report_interval.count()) * 1'000ULL;
        }
    }

    hooks->stop();
    print_sample(metrics, resources);
    uninstall_shutdown_handlers();
    return exit_code;
}
