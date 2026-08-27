#include "continuous_auth/collector/context.hpp"

#if defined(_WIN32)
#include <windows.h>
#include <psapi.h>
#endif

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <regex>
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

ApplicationCategory parse_category(const std::string& value) {
  if (value == "PRODUCTIVITY") return ApplicationCategory::kProductivity;
  if (value == "BROWSING") return ApplicationCategory::kBrowsing;
  if (value == "DEVELOPMENT") return ApplicationCategory::kDevelopment;
  if (value == "CREATIVE") return ApplicationCategory::kCreative;
  if (value == "GAMING") return ApplicationCategory::kGaming;
  if (value == "SYSTEM") return ApplicationCategory::kSystem;
  return ApplicationCategory::kUnknown;
}

#if defined(_WIN32)
std::int64_t stable_app_id(const std::string& value) noexcept {
  std::uint64_t hash = 1469598103934665603ULL;
  for (const auto ch : lowercase(value)) {
    hash ^= static_cast<unsigned char>(ch);
    hash *= 1099511628211ULL;
  }
  return static_cast<std::int64_t>(hash & 0x7fffffffffffffffULL);
}

std::string utf8_process_name(const wchar_t* value, int length) noexcept {
  if (length <= 0) return "unknown";
  const auto required = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, length, nullptr, 0,
                                             nullptr, nullptr);
  if (required <= 0) return "unknown";
  std::string result(static_cast<std::size_t>(required), '\0');
  if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, length, result.data(), required,
                          nullptr, nullptr) <= 0) {
    return "unknown";
  }
  if (result.find_first_of("\\/:") != std::string::npos) return "unknown";
  return result;
}

std::string foreground_process_name() noexcept {
  const auto foreground = GetForegroundWindow();
  if (foreground == nullptr) return "unknown";
  DWORD process_id{};
  GetWindowThreadProcessId(foreground, &process_id);
  if (process_id == 0) return "unknown";
  const auto process = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, process_id);
  if (process == nullptr) return "unknown";
  wchar_t name[260]{};
  const auto length = GetModuleBaseNameW(process, nullptr, name, 260);
  CloseHandle(process);
  return utf8_process_name(name, static_cast<int>(length));
}

class WindowsContextResolver final : public ContextResolver {
public:
  explicit WindowsContextResolver(ApplicationCategories categories)
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
    auto dpi = 96;
    const auto user32 = GetModuleHandleW(L"user32.dll");
    if (user32 != nullptr) {
      using GetDpi = UINT(WINAPI*)();
      const auto function = reinterpret_cast<GetDpi>(GetProcAddress(user32, "GetDpiForSystem"));
      if (function != nullptr) dpi = static_cast<int>(function());
    }
    const auto width = (std::max)(GetSystemMetrics(SM_CXSCREEN), 1);
    const auto height = (std::max)(GetSystemMetrics(SM_CYSCREEN), 1);
    publisher.publish(DeviceMetadataEvent{
        std::string(kProtocolVersion), "DEVICE_METADATA", static_cast<std::int64_t>(captured),
        InputDeviceClass::kUnknown, InputDeviceClass::kUnknown, width, height, dpi,
        publisher.next_sequence()});
  }

private:
  ApplicationCategories categories_;
  std::int64_t current_{-1};
  std::unordered_set<std::int64_t> registered_;
};

class WindowsPauseSignal final : public PauseSignal {
public:
  explicit WindowsPauseSignal(const std::string& name) {
    std::wstring wide(name.begin(), name.end());
    handle_ = CreateEventW(nullptr, TRUE, FALSE, wide.c_str());
  }
  ~WindowsPauseSignal() override {
    if (handle_ != nullptr) CloseHandle(handle_);
  }
  bool paused() noexcept override {
    return handle_ != nullptr && WaitForSingleObject(handle_, 0) == WAIT_OBJECT_0;
  }

private:
  HANDLE handle_{};
};
#else
class NoopContextResolver final : public ContextResolver {
public:
  void emit_focus_if_changed(EventPublisher&) noexcept override {}
  void emit_device_metadata(EventPublisher&) noexcept override {}
};
class NoopPauseSignal final : public PauseSignal {
public:
  bool paused() noexcept override { return false; }
};
#endif

}  // namespace

ApplicationCategories ApplicationCategories::load(std::istream& input) {
  ApplicationCategories result;
  const std::regex entry(R"regex("([^"\\/:]+)"\s*:\s*"([A-Z_]+)")regex");
  std::string line;
  while (std::getline(input, line)) {
    std::smatch match;
    if (std::regex_search(line, match, entry) && match.size() == 3) {
      const auto category = parse_category(match[2].str());
      if (category != ApplicationCategory::kUnknown || match[2].str() == "UNKNOWN") {
        result.values_[lowercase(match[1].str())] = category;
      }
    }
  }
  return result;
}

ApplicationCategory ApplicationCategories::category_for(
    const std::string& process_name) const noexcept {
  const auto found = values_.find(lowercase(process_name));
  return found == values_.end() ? ApplicationCategory::kUnknown : found->second;
}

std::unique_ptr<ContextResolver> make_platform_context_resolver(
    ApplicationCategories categories) {
#if defined(_WIN32)
  return std::make_unique<WindowsContextResolver>(std::move(categories));
#else
  static_cast<void>(categories);
  return std::make_unique<NoopContextResolver>();
#endif
}

std::unique_ptr<PauseSignal> make_pause_signal(const std::string& event_name) {
#if defined(_WIN32)
  return std::make_unique<WindowsPauseSignal>(event_name);
#else
  static_cast<void>(event_name);
  return std::make_unique<NoopPauseSignal>();
#endif
}

}  // namespace continuous_auth::collector
