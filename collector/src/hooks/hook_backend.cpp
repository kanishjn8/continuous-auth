#include "continuous_auth/collector/hook_backend.hpp"

#if defined(_WIN32)
#include <windows.h>

#include <array>
#include <cstdio>
#include <limits>
#include <sstream>
#endif

#include <utility>

namespace continuous_auth::collector {
namespace {
using namespace continuous_auth::protocol::v1;

#if defined(_WIN32)
class WindowsHookBackend;
WindowsHookBackend* active_backend = nullptr;

KeyClass classify_platform_identifier(DWORD value) noexcept {
  switch (value) {
    case 0x51: case 0x57: case 0x45: case 0x52: case 0x54:
      return KeyClass::kAlphaLUpper;
    case 0x59: case 0x55: case 0x49: case 0x4f: case 0x50:
      return KeyClass::kAlphaRUpper;
    case 0x41: case 0x53: case 0x44: case 0x46: case 0x47:
      return KeyClass::kAlphaLHome;
    case 0x48: case 0x4a: case 0x4b: case 0x4c:
      return KeyClass::kAlphaRHome;
    case 0x5a: case 0x58: case 0x43: case 0x56: case 0x42:
      return KeyClass::kAlphaLLower;
    case 0x4e: case 0x4d:
      return KeyClass::kAlphaRLower;
    case VK_SPACE: return KeyClass::kSpace;
    case VK_BACK: return KeyClass::kBackspace;
    case VK_DELETE: return KeyClass::kDelete;
    case VK_RETURN: return KeyClass::kEnter;
    case VK_SHIFT: case VK_LSHIFT: case VK_RSHIFT: case VK_CONTROL: case VK_LCONTROL:
    case VK_RCONTROL: case VK_MENU: case VK_LMENU: case VK_RMENU: case VK_LWIN: case VK_RWIN:
    case VK_CAPITAL:
      return KeyClass::kModifier;
    case VK_LEFT: case VK_RIGHT: case VK_UP: case VK_DOWN: case VK_HOME: case VK_END:
    case VK_PRIOR: case VK_NEXT: case VK_TAB: case VK_INSERT: case VK_ESCAPE:
      return KeyClass::kNavigation;
    case VK_OEM_1: case VK_OEM_PLUS: case VK_OEM_COMMA: case VK_OEM_MINUS:
    case VK_OEM_PERIOD: case VK_OEM_2: case VK_OEM_3: case VK_OEM_4: case VK_OEM_5:
    case VK_OEM_6: case VK_OEM_7: case VK_OEM_8: case VK_OEM_102:
      return KeyClass::kPunct;
    default:
      if ((value >= 0x30 && value <= 0x39) || (value >= VK_NUMPAD0 && value <= VK_NUMPAD9))
        return KeyClass::kDigit;
      if (value >= VK_F1 && value <= VK_F24) return KeyClass::kFunction;
      return KeyClass::kOther;
  }
}

class WindowsHookBackend final : public HookBackend {
public:
  explicit WindowsHookBackend(EventPublisher& publisher) : publisher_(publisher) {}
  ~WindowsHookBackend() override { stop(); }

  bool start(std::string& error) override {
    if (active_backend != nullptr) {
      error = "another collector hook backend is active";
      return false;
    }
    const auto module = GetModuleHandleW(nullptr);
    if (module == nullptr) {
      error = "collector module lookup failed";
      return false;
    }
    active_backend = this;
    keyboard_hook_ = SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_callback, module, 0);
    if (keyboard_hook_ == nullptr) {
      active_backend = nullptr;
      error = "global keyboard hook installation failed";
      return false;
    }
    mouse_hook_ = SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, module, 0);
    if (mouse_hook_ == nullptr) {
      UnhookWindowsHookEx(keyboard_hook_);
      keyboard_hook_ = nullptr;
      active_backend = nullptr;
      error = "global mouse hook installation failed";
      return false;
    }
    return true;
  }

  PollStatus poll_for(std::chrono::milliseconds duration, std::string& error) override {
    const auto count = duration.count();
    const auto maximum = static_cast<decltype(count)>((std::numeric_limits<DWORD>::max)());
    const auto timeout = static_cast<DWORD>(count < 0 ? 0 : count > maximum ? maximum : count);
    const auto wait = MsgWaitForMultipleObjects(0, nullptr, FALSE, timeout, QS_ALLINPUT);
    if (wait == WAIT_TIMEOUT) return PollStatus::continue_running;
    if (wait == WAIT_FAILED) {
      error = "collector message wait failed";
      return PollStatus::failure;
    }
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE) != 0) {
      if (message.message == WM_QUIT) return PollStatus::stop_requested;
      TranslateMessage(&message);
      DispatchMessageW(&message);
    }
    return PollStatus::continue_running;
  }

  void stop() noexcept override {
    if (active_backend == this) active_backend = nullptr;
    if (mouse_hook_ != nullptr) {
      UnhookWindowsHookEx(mouse_hook_);
      mouse_hook_ = nullptr;
    }
    if (keyboard_hook_ != nullptr) {
      UnhookWindowsHookEx(keyboard_hook_);
      keyboard_hook_ = nullptr;
    }
  }

private:
  static LRESULT CALLBACK keyboard_callback(int code, WPARAM message, LPARAM payload) {
    if (code == HC_ACTION && active_backend != nullptr && !active_backend->publisher_.paused()) {
      const auto captured = active_backend->publisher_.capture_time_us();
      const auto* native_event = reinterpret_cast<const KBDLLHOOKSTRUCT*>(payload);
      const auto platform_identifier = native_event->vkCode;
      const auto classified = classify_platform_identifier(platform_identifier);
      const auto down = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
      const auto up = message == WM_KEYUP || message == WM_SYSKEYUP;
      if (captured != 0 && (down || up)) {
        const auto index = static_cast<std::size_t>(classified);
        const auto repeated = down && active_backend->class_down_[index];
        active_backend->class_down_[index] = down;
        active_backend->publisher_.publish_keyboard(CapturedKeyboardEvent{
            down, static_cast<std::int64_t>(captured), classified, repeated,
            InputDeviceClass::kUnknown, active_backend->publisher_.current_app(),
            active_backend->publisher_.next_sequence()});
        std::fprintf(stderr, "[COLLECTOR] keyboard %s (class=%d)\n", down ? "DOWN" : "UP",
                     static_cast<int>(classified));
        std::fflush(stderr);
      }
    }
    return CallNextHookEx(nullptr, code, message, payload);
  }

  static LRESULT CALLBACK mouse_callback(int code, WPARAM message, LPARAM payload) {
    if (code == HC_ACTION && active_backend != nullptr && !active_backend->publisher_.paused()) {
      const auto captured = active_backend->publisher_.capture_time_us();
      const auto* native_event = reinterpret_cast<const MSLLHOOKSTRUCT*>(payload);
      std::optional<CapturedMouseType> type;
      std::optional<MouseButton> button;
      std::int64_t horizontal{};
      std::int64_t vertical{};
      switch (message) {
        case WM_MOUSEMOVE: type = CapturedMouseType::move; break;
        case WM_LBUTTONDOWN:
          type = CapturedMouseType::button_down;
          button = MouseButton::kLeft;
          break;
        case WM_LBUTTONUP:
          type = CapturedMouseType::button_up;
          button = MouseButton::kLeft;
          break;
        case WM_RBUTTONDOWN:
          type = CapturedMouseType::button_down;
          button = MouseButton::kRight;
          break;
        case WM_RBUTTONUP:
          type = CapturedMouseType::button_up;
          button = MouseButton::kRight;
          break;
        case WM_MBUTTONDOWN:
          type = CapturedMouseType::button_down;
          button = MouseButton::kMiddle;
          break;
        case WM_MBUTTONUP:
          type = CapturedMouseType::button_up;
          button = MouseButton::kMiddle;
          break;
        case WM_MOUSEWHEEL:
          type = CapturedMouseType::scroll;
          vertical = static_cast<short>((native_event->mouseData >> 16U) & 0xffffU);
          break;
        case WM_MOUSEHWHEEL:
          type = CapturedMouseType::scroll;
          horizontal = static_cast<short>((native_event->mouseData >> 16U) & 0xffffU);
          break;
        default: break;
      }
      if (captured != 0 && type.has_value()) {
        active_backend->publisher_.publish_mouse(CapturedMouseEvent{
            *type, static_cast<std::int64_t>(captured), native_event->pt.x,
            native_event->pt.y, button, horizontal, vertical,
            InputDeviceClass::kUnknown, active_backend->publisher_.current_app(),
            active_backend->publisher_.next_sequence()});
        std::fprintf(stderr, "[COLLECTOR] mouse type=%d at (%ld,%ld)\n", static_cast<int>(*type),
                     native_event->pt.x, native_event->pt.y);
        std::fflush(stderr);
      }
    }
    return CallNextHookEx(nullptr, code, message, payload);
  }

  EventPublisher& publisher_;
  HHOOK keyboard_hook_{};
  HHOOK mouse_hook_{};
  std::array<bool, 16> class_down_{};
};
#else
class UnsupportedHookBackend final : public HookBackend {
public:
  explicit UnsupportedHookBackend(EventPublisher&) {}
  bool start(std::string& error) override {
    error = "production collector currently requires Windows";
    return false;
  }
  PollStatus poll_for(std::chrono::milliseconds, std::string&) override {
    return PollStatus::failure;
  }
  void stop() noexcept override {}
};
#endif

}  // namespace

std::unique_ptr<HookBackend> make_platform_hook_backend(EventPublisher& publisher) {
#if defined(_WIN32)
  return std::make_unique<WindowsHookBackend>(publisher);
#else
  return std::make_unique<UnsupportedHookBackend>(publisher);
#endif
}

}  // namespace continuous_auth::collector
