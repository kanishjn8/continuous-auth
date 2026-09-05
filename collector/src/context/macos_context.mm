#include "continuous_auth/collector/macos_context.hpp"

#if defined(__APPLE__)

#import <AppKit/AppKit.h>
#include <ApplicationServices/ApplicationServices.h>

#include <fcntl.h>
#include <semaphore.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <string>
#include <unordered_set>
#include <utility>

namespace continuous_auth::collector {
namespace {
using namespace continuous_auth::protocol::v1;

std::string lowercase(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
  return value;
}

std::int64_t stable_app_id(const std::string& value) noexcept {
  std::uint64_t hash = 1469598103934665603ULL;
  for (const auto ch : lowercase(value)) {
    hash ^= static_cast<unsigned char>(ch);
    hash *= 1099511628211ULL;
  }
  return static_cast<std::int64_t>(hash & 0x7fffffffffffffffULL);
}

std::string foreground_process_name() noexcept {
  @autoreleasepool {
    const auto* application = [[NSWorkspace sharedWorkspace] frontmostApplication];
    if (application == nil || application.localizedName == nil) return "unknown";
    const auto* utf8 = [application.localizedName UTF8String];
    if (utf8 == nullptr) return "unknown";
    std::string result(utf8);
    if (result.empty() || result.find_first_of("\\/:") != std::string::npos) return "unknown";
    if (std::any_of(result.begin(), result.end(),
                    [](unsigned char value) { return value < 0x20; })) {
      return "unknown";
    }
    return result;
  }
}

class MacOSContextResolver final : public ContextResolver {
public:
  explicit MacOSContextResolver(ApplicationCategories categories)
      : categories_(std::move(categories)) {}

  void emit_focus_if_changed(EventPublisher& publisher) noexcept override {
    const auto process = foreground_process_name();
    const auto app_id = stable_app_id(process);
    if (app_id == current_) return;
    const auto captured = publisher.capture_time_us();
    if (captured == 0) return;
    const auto category = categories_.category_for(process);
    publisher.set_current_app(app_id);
    if (registered_.insert(app_id).second) {
      publisher.publish(AppRegistryEvent{std::string(kProtocolVersion), "APP_REGISTRY",
                                         static_cast<std::int64_t>(captured), app_id, process,
                                         category, publisher.next_sequence()});
    }
    publisher.publish(ContextEvent{std::string(kProtocolVersion), "APP_FOCUS_CHANGE",
                                   static_cast<std::int64_t>(captured), app_id, category,
                                   publisher.next_sequence()});
    current_ = app_id;
  }

  void emit_device_metadata(EventPublisher& publisher) noexcept override {
    const auto captured = publisher.capture_time_us();
    if (captured == 0) return;
    const auto display = CGMainDisplayID();
    const auto width = (std::max)(static_cast<std::size_t>(1), CGDisplayPixelsWide(display));
    const auto height = (std::max)(static_cast<std::size_t>(1), CGDisplayPixelsHigh(display));
    const auto millimetres = CGDisplayScreenSize(display);
    auto dpi = 1;
    if (millimetres.width > 0.0) {
      dpi = (std::max)(1, static_cast<int>(std::lround(
                              static_cast<double>(width) * 25.4 / millimetres.width)));
    }
    publisher.publish(DeviceMetadataEvent{
        std::string(kProtocolVersion), "DEVICE_METADATA", static_cast<std::int64_t>(captured),
        InputDeviceClass::kUnknown, InputDeviceClass::kUnknown,
        static_cast<std::int64_t>(width), static_cast<std::int64_t>(height), dpi,
        publisher.next_sequence()});
  }

private:
  ApplicationCategories categories_;
  std::int64_t current_{-1};
  std::unordered_set<std::int64_t> registered_;
};

class MacOSPauseSignal final : public PauseSignal {
public:
  explicit MacOSPauseSignal(const std::string& name) : name_("/" + name) {
    sem_unlink(name_.c_str());
    semaphore_ = sem_open(name_.c_str(), O_CREAT | O_EXCL, 0600, 0);
  }

  ~MacOSPauseSignal() override {
    if (semaphore_ != SEM_FAILED) sem_close(semaphore_);
    sem_unlink(name_.c_str());
  }

  bool paused() noexcept override {
    if (semaphore_ == SEM_FAILED) return false;
    if (sem_trywait(semaphore_) == 0) {
      return sem_post(semaphore_) == 0;
    }
    return false;
  }

private:
  std::string name_;
  sem_t* semaphore_{SEM_FAILED};
};

}  // namespace

std::unique_ptr<ContextResolver> make_macos_context_resolver(
    ApplicationCategories categories) {
  return std::make_unique<MacOSContextResolver>(std::move(categories));
}

std::unique_ptr<PauseSignal> make_macos_pause_signal(const std::string& event_name) {
  return std::make_unique<MacOSPauseSignal>(event_name);
}

}  // namespace continuous_auth::collector

#endif
