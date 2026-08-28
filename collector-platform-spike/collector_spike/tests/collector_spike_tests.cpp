#include "collector_spike/clock.hpp"
#include "collector_spike/hook_backend.hpp"
#include "collector_spike/metrics.hpp"
#include "collector_spike/process_resource_sampler.hpp"

#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <thread>
#include <vector>

namespace {

class ManualClock final : public continuous_auth::collector::spike::MonotonicClock {
public:
    std::uint64_t now_us() const noexcept override { return current_us_; }
    void set(std::uint64_t value) noexcept { current_us_ = value; }

private:
    std::uint64_t current_us_{};
};

class ConstantClock final : public continuous_auth::collector::spike::MonotonicClock {
public:
    std::uint64_t now_us() const noexcept override { return 1'000; }
};

void test_aggregate_counts_and_timestamps() {
    ManualClock clock;
    continuous_auth::collector::spike::CaptureMetrics metrics(clock);

    clock.set(100);
    metrics.record_keyboard();
    clock.set(175);
    metrics.record_mouse();
    clock.set(250);
    metrics.record_keyboard();

    const auto snapshot = metrics.snapshot();
    assert(snapshot.keyboard_events == 2);
    assert(snapshot.mouse_events == 1);
    assert(snapshot.first_capture_us == 100);
    assert(snapshot.last_capture_us == 250);
    assert(snapshot.monotonicity_violations == 0);
}

void test_time_regression_is_visible_without_rewriting_capture_time() {
    ManualClock clock;
    continuous_auth::collector::spike::CaptureMetrics metrics(clock);

    clock.set(200);
    metrics.record_keyboard();
    clock.set(150);
    metrics.record_mouse();

    const auto snapshot = metrics.snapshot();
    assert(snapshot.keyboard_events == 1);
    assert(snapshot.mouse_events == 1);
    assert(snapshot.last_capture_us == 200);
    assert(snapshot.monotonicity_violations == 1);
}

void test_platform_clock_does_not_move_backwards() {
    continuous_auth::collector::spike::PlatformMonotonicClock clock;
    assert(clock.valid());
    auto previous = clock.now_us();
    for (int index = 0; index < 1'000; ++index) {
        const auto current = clock.now_us();
        assert(current >= previous);
        previous = current;
    }
}

void test_concurrent_capture_accounting_is_bounded_and_exact() {
    ConstantClock clock;
    continuous_auth::collector::spike::CaptureMetrics metrics(clock);
    constexpr int worker_count = 4;
    constexpr int events_per_worker = 5'000;
    std::vector<std::thread> workers;
    workers.reserve(worker_count);

    for (int worker = 0; worker < worker_count; ++worker) {
        workers.emplace_back([&metrics, worker, events_per_worker]() {
            for (int event = 0; event < events_per_worker; ++event) {
                if ((worker + event) % 2 == 0) {
                    metrics.record_keyboard();
                } else {
                    metrics.record_mouse();
                }
            }
        });
    }
    for (auto& worker : workers) {
        worker.join();
    }

    const auto snapshot = metrics.snapshot();
    assert(snapshot.keyboard_events + snapshot.mouse_events ==
           static_cast<std::uint64_t>(worker_count * events_per_worker));
    assert(snapshot.keyboard_events == snapshot.mouse_events);
    assert(snapshot.first_capture_us == 1'000);
    assert(snapshot.last_capture_us == 1'000);
    assert(snapshot.monotonicity_violations == 0);
}

void test_platform_backend_factory_and_resource_sampler() {
    continuous_auth::collector::spike::PlatformMonotonicClock clock;
    continuous_auth::collector::spike::CaptureMetrics metrics(clock);
    auto backend = continuous_auth::collector::spike::make_platform_hook_backend(metrics);
    assert(backend != nullptr);
#if defined(_WIN32)
    assert(backend->name() == std::string_view("windows-low-level-hooks"));
#elif defined(__APPLE__)
    assert(backend->name() == std::string_view("macos-cgeventtap"));
#elif defined(__linux__)
    assert(backend->name() == std::string_view("linux-x11-xinput2"));
#endif

    continuous_auth::collector::spike::ProcessResourceSampler resources(clock);
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    const auto sample = resources.sample();
    assert(sample.cpu_percent_one_core >= 0.0);
    assert(sample.resident_bytes > 0);

    backend->stop();
    backend->stop();
}

}  // namespace

int main() {
    test_aggregate_counts_and_timestamps();
    test_time_regression_is_visible_without_rewriting_capture_time();
    test_platform_clock_does_not_move_backwards();
    test_concurrent_capture_accounting_is_bounded_and_exact();
    test_platform_backend_factory_and_resource_sampler();
    std::cout << "native collector spike tests passed\n";
    return 0;
}
