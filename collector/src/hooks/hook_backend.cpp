#include "continuous_auth/collector/hook_backend.hpp"

#if defined(_WIN32)
#include <windows.h>

#include <array>
#include <cstdio>
#include <limits>
#include <sstream>
#elif defined(__APPLE__)
#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>

#include <cmath>
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
#elif defined(__APPLE__)
KeyClass classify_platform_identifier(CGKeyCode value) noexcept {
  switch (value) {
    case 12: case 13: case 14: case 15: case 17:
      return KeyClass::kAlphaLUpper;
    case 16: case 32: case 34: case 31: case 35:
      return KeyClass::kAlphaRUpper;
    case 0: case 1: case 2: case 3: case 5:
      return KeyClass::kAlphaLHome;
    case 4: case 38: case 40: case 37:
      return KeyClass::kAlphaRHome;
    case 6: case 7: case 8: case 9: case 11:
      return KeyClass::kAlphaLLower;
    case 45: case 46:
      return KeyClass::kAlphaRLower;
    case 49: return KeyClass::kSpace;
    case 51: return KeyClass::kBackspace;
    case 117: return KeyClass::kDelete;
    case 36: case 76: return KeyClass::kEnter;
    case 54: case 55: case 56: case 57: case 58: case 59: case 60: case 61: case 62:
    case 63:
      return KeyClass::kModifier;
    case 48: case 53: case 114: case 115: case 116: case 119: case 121: case 123:
    case 124: case 125: case 126:
      return KeyClass::kNavigation;
    case 10: case 24: case 27: case 30: case 33: case 39: case 41: case 42: case 43:
    case 44: case 47: case 50:
      return KeyClass::kPunct;
    case 18: case 19: case 20: case 21: case 22: case 23: case 25: case 26: case 28:
    case 29: case 65: case 67: case 69: case 75: case 78: case 81: case 82: case 83:
    case 84: case 85: case 86: case 87: case 88: case 89: case 91: case 92:
      return KeyClass::kDigit;
    case 64: case 79: case 80: case 90: case 96: case 97: case 98: case 99: case 100:
    case 101: case 103: case 105: case 106: case 107: case 109: case 111: case 113:
    case 118: case 120: case 122:
      return KeyClass::kFunction;
    default: return KeyClass::kOther;
  }
}

class MacOSHookBackend final : public HookBackend {
public:
  explicit MacOSHookBackend(EventPublisher& publisher) : publisher_(publisher) {}
  ~MacOSHookBackend() override { stop(); }

  bool start(std::string& error) override {
    if (event_tap_ != nullptr) {
      error = "another collector hook backend is active";
      return false;
    }
    if (!CGPreflightListenEventAccess()) {
      error = "macOS Input Monitoring permission is not granted";
      return false;
    }
    const auto keyboard = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp) |
                          CGEventMaskBit(kCGEventFlagsChanged);
    const auto mouse = CGEventMaskBit(kCGEventLeftMouseDown) |
                       CGEventMaskBit(kCGEventLeftMouseUp) |
                       CGEventMaskBit(kCGEventRightMouseDown) |
                       CGEventMaskBit(kCGEventRightMouseUp) |
                       CGEventMaskBit(kCGEventOtherMouseDown) |
                       CGEventMaskBit(kCGEventOtherMouseUp) |
                       CGEventMaskBit(kCGEventMouseMoved) |
                       CGEventMaskBit(kCGEventLeftMouseDragged) |
                       CGEventMaskBit(kCGEventRightMouseDragged) |
                       CGEventMaskBit(kCGEventOtherMouseDragged) |
                       CGEventMaskBit(kCGEventScrollWheel);
    event_tap_ = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                                  kCGEventTapOptionListenOnly, keyboard | mouse, callback, this);
    if (event_tap_ == nullptr) {
      error = "macOS global input event tap creation failed";
      return false;
    }
    run_loop_source_ = CFMachPortCreateRunLoopSource(nullptr, event_tap_, 0);
    if (run_loop_source_ == nullptr) {
      CFRelease(event_tap_);
      event_tap_ = nullptr;
      error = "macOS input run-loop source creation failed";
      return false;
    }
    const auto main_display = CGMainDisplayID();
    const auto bounds = CGDisplayBounds(main_display);
    if (bounds.size.width > 0.0 && bounds.size.height > 0.0) {
      scale_x_ = static_cast<double>(CGDisplayPixelsWide(main_display)) / bounds.size.width;
      scale_y_ = static_cast<double>(CGDisplayPixelsHigh(main_display)) / bounds.size.height;
    }
    run_loop_ = CFRunLoopGetCurrent();
    CFRetain(run_loop_);
    CFRunLoopAddSource(run_loop_, run_loop_source_, kCFRunLoopCommonModes);
    CGEventTapEnable(event_tap_, true);
    return true;
  }

  PollStatus poll_for(std::chrono::milliseconds duration, std::string& error) override {
    if (event_tap_ == nullptr || run_loop_source_ == nullptr) {
      error = "macOS input event tap is not active";
      return PollStatus::failure;
    }
    const auto seconds = static_cast<CFTimeInterval>(duration.count()) / 1'000.0;
    const auto result =
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds < 0.0 ? 0.0 : seconds, true);
    if (result == kCFRunLoopRunStopped) return PollStatus::stop_requested;
    if (result == kCFRunLoopRunFinished) {
      error = "macOS input event run loop stopped unexpectedly";
      return PollStatus::failure;
    }
    return PollStatus::continue_running;
  }

  void stop() noexcept override {
    if (run_loop_ != nullptr && run_loop_source_ != nullptr) {
      CFRunLoopRemoveSource(run_loop_, run_loop_source_, kCFRunLoopCommonModes);
    }
    if (event_tap_ != nullptr) CGEventTapEnable(event_tap_, false);
    if (run_loop_source_ != nullptr) {
      CFRelease(run_loop_source_);
      run_loop_source_ = nullptr;
    }
    if (event_tap_ != nullptr) {
      CFRelease(event_tap_);
      event_tap_ = nullptr;
    }
    if (run_loop_ != nullptr) {
      CFRelease(run_loop_);
      run_loop_ = nullptr;
    }
  }

private:
  static CGEventRef callback(CGEventTapProxy, CGEventType type, CGEventRef event,
                             void* context) noexcept {
    auto* backend = static_cast<MacOSHookBackend*>(context);
    if (backend == nullptr) return event;
    if (type == kCGEventTapDisabledByTimeout || type == kCGEventTapDisabledByUserInput) {
      if (backend->event_tap_ != nullptr) CGEventTapEnable(backend->event_tap_, true);
      return event;
    }
    if (backend->publisher_.paused()) return event;
    const auto captured = backend->publisher_.capture_time_us();
    if (captured == 0) return event;

    if (type == kCGEventKeyDown || type == kCGEventKeyUp || type == kCGEventFlagsChanged) {
      const auto platform_identifier = static_cast<CGKeyCode>(
          CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode));
      const auto classified = classify_platform_identifier(platform_identifier);
      const auto down = type == kCGEventKeyDown ||
                        (type == kCGEventFlagsChanged &&
                         CGEventSourceKeyState(kCGEventSourceStateCombinedSessionState,
                                               platform_identifier));
      const auto repeated = type == kCGEventKeyDown &&
                            CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat) != 0;
      backend->publisher_.publish_keyboard(CapturedKeyboardEvent{
          down, static_cast<std::int64_t>(captured), classified, repeated,
          InputDeviceClass::kUnknown, backend->publisher_.current_app(),
          backend->publisher_.next_sequence()});
      return event;
    }

    std::optional<CapturedMouseType> captured_type;
    std::optional<MouseButton> button;
    switch (type) {
      case kCGEventMouseMoved:
      case kCGEventLeftMouseDragged:
      case kCGEventRightMouseDragged:
      case kCGEventOtherMouseDragged: captured_type = CapturedMouseType::move; break;
      case kCGEventLeftMouseDown:
        captured_type = CapturedMouseType::button_down;
        button = MouseButton::kLeft;
        break;
      case kCGEventLeftMouseUp:
        captured_type = CapturedMouseType::button_up;
        button = MouseButton::kLeft;
        break;
      case kCGEventRightMouseDown:
        captured_type = CapturedMouseType::button_down;
        button = MouseButton::kRight;
        break;
      case kCGEventRightMouseUp:
        captured_type = CapturedMouseType::button_up;
        button = MouseButton::kRight;
        break;
      case kCGEventOtherMouseDown:
      case kCGEventOtherMouseUp:
        if (CGEventGetIntegerValueField(event, kCGMouseEventButtonNumber) == 2) {
          captured_type = type == kCGEventOtherMouseDown ? CapturedMouseType::button_down
                                                        : CapturedMouseType::button_up;
          button = MouseButton::kMiddle;
        }
        break;
      case kCGEventScrollWheel: captured_type = CapturedMouseType::scroll; break;
      default: break;
    }
    if (!captured_type.has_value()) return event;
    const auto location = CGEventGetLocation(event);
    const auto horizontal =
        CGEventGetIntegerValueField(event, kCGScrollWheelEventPointDeltaAxis2);
    const auto vertical = CGEventGetIntegerValueField(event, kCGScrollWheelEventPointDeltaAxis1);
    backend->publisher_.publish_mouse(CapturedMouseEvent{
        *captured_type, static_cast<std::int64_t>(captured),
        static_cast<std::int64_t>(std::llround(location.x * backend->scale_x_)),
        static_cast<std::int64_t>(std::llround(location.y * backend->scale_y_)), button,
        horizontal, vertical, InputDeviceClass::kUnknown, backend->publisher_.current_app(),
        backend->publisher_.next_sequence()});
    return event;
  }

  EventPublisher& publisher_;
  CFMachPortRef event_tap_{};
  CFRunLoopSourceRef run_loop_source_{};
  CFRunLoopRef run_loop_{};
  double scale_x_{1.0};
  double scale_y_{1.0};
};
#else
class UnsupportedHookBackend final : public HookBackend {
public:
  explicit UnsupportedHookBackend(EventPublisher&) {}
  bool start(std::string& error) override {
    error = "production collector requires Windows or macOS";
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
#elif defined(__APPLE__)
  return std::make_unique<MacOSHookBackend>(publisher);
#else
  return std::make_unique<UnsupportedHookBackend>(publisher);
#endif
}

}  // namespace continuous_auth::collector
