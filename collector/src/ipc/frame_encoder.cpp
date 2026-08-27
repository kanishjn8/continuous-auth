#include "continuous_auth/collector/frame_encoder.hpp"

#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <type_traits>

namespace continuous_auth::collector {
namespace {
using namespace continuous_auth::protocol::v1;

const char* key_class(KeyClass value) {
  switch (value) {
    case KeyClass::kAlphaLHome: return "ALPHA_L_HOME";
    case KeyClass::kAlphaLUpper: return "ALPHA_L_UPPER";
    case KeyClass::kAlphaLLower: return "ALPHA_L_LOWER";
    case KeyClass::kAlphaRHome: return "ALPHA_R_HOME";
    case KeyClass::kAlphaRUpper: return "ALPHA_R_UPPER";
    case KeyClass::kAlphaRLower: return "ALPHA_R_LOWER";
    case KeyClass::kDigit: return "DIGIT";
    case KeyClass::kPunct: return "PUNCT";
    case KeyClass::kSpace: return "SPACE";
    case KeyClass::kBackspace: return "BACKSPACE";
    case KeyClass::kDelete: return "DELETE";
    case KeyClass::kEnter: return "ENTER";
    case KeyClass::kModifier: return "MODIFIER";
    case KeyClass::kNavigation: return "NAVIGATION";
    case KeyClass::kFunction: return "FUNCTION";
    case KeyClass::kOther: return "OTHER";
  }
  return "OTHER";
}

const char* device_class(InputDeviceClass value) {
  switch (value) {
    case InputDeviceClass::kInternalKeyboard: return "INTERNAL_KEYBOARD";
    case InputDeviceClass::kExternalKeyboard: return "EXTERNAL_KEYBOARD";
    case InputDeviceClass::kTrackpad: return "TRACKPAD";
    case InputDeviceClass::kExternalMouse: return "EXTERNAL_MOUSE";
    case InputDeviceClass::kUnknown: return "UNKNOWN";
  }
  return "UNKNOWN";
}

const char* category(ApplicationCategory value) {
  switch (value) {
    case ApplicationCategory::kProductivity: return "PRODUCTIVITY";
    case ApplicationCategory::kBrowsing: return "BROWSING";
    case ApplicationCategory::kDevelopment: return "DEVELOPMENT";
    case ApplicationCategory::kCreative: return "CREATIVE";
    case ApplicationCategory::kGaming: return "GAMING";
    case ApplicationCategory::kSystem: return "SYSTEM";
    case ApplicationCategory::kUnknown: return "UNKNOWN";
  }
  return "UNKNOWN";
}

const char* mouse_button(MouseButton value) {
  switch (value) {
    case MouseButton::kLeft: return "LEFT";
    case MouseButton::kRight: return "RIGHT";
    case MouseButton::kMiddle: return "MIDDLE";
  }
  return "LEFT";
}

std::string escaped(const std::string& value) {
  std::ostringstream output;
  for (const auto ch : value) {
    const auto byte = static_cast<unsigned char>(ch);
    switch (ch) {
      case '"': output << "\\\""; break;
      case '\\': output << "\\\\"; break;
      case '\b': output << "\\b"; break;
      case '\f': output << "\\f"; break;
      case '\n': output << "\\n"; break;
      case '\r': output << "\\r"; break;
      case '\t': output << "\\t"; break;
      default:
        if (byte < 0x20U) {
          output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                 << static_cast<unsigned int>(byte) << std::dec;
        } else {
          output << ch;
        }
    }
  }
  return output.str();
}

template <typename T> void common(std::ostringstream& out, const T& value) {
  out << "\"schema_version\":\"" << escaped(value.schema_version) << "\","
      << "\"type\":\"" << escaped(value.type) << "\","
      << "\"t_capture_us\":" << value.t_capture_us << ',';
}

}  // namespace

std::string encode_event_json(const EventFrame& event) {
  return std::visit(
      [](const auto& value) {
        using T = std::decay_t<decltype(value)>;
        std::ostringstream out;
        out << '{';
        common(out, value);
        if constexpr (std::is_same_v<T, KeyboardEvent>) {
          out << "\"key_class\":\"" << key_class(value.key_class) << "\","
              << "\"is_repeat\":" << (value.is_repeat ? "true" : "false") << ','
              << "\"device_class\":\"" << device_class(value.device_class) << "\","
              << "\"app_id\":" << value.app_id << ',';
        } else if constexpr (std::is_same_v<T, MouseEvent>) {
          out << "\"x\":" << value.x << ",\"y\":" << value.y << ',';
          if (value.button.has_value()) {
            out << "\"button\":\"" << mouse_button(*value.button) << "\",";
          } else {
            out << "\"button\":null,";
          }
          out << "\"scroll_dx\":" << value.scroll_dx << ",\"scroll_dy\":"
              << value.scroll_dy << ",\"device_class\":\""
              << device_class(value.device_class) << "\",\"app_id\":" << value.app_id << ',';
        } else if constexpr (std::is_same_v<T, ContextEvent>) {
          out << "\"app_id\":" << value.app_id << ",\"category\":\""
              << category(value.category) << "\",";
        } else if constexpr (std::is_same_v<T, Heartbeat>) {
          out << "\"collector_uptime_ms\":" << value.collector_uptime_ms
              << ",\"dropped_events\":" << value.dropped_events
              << ",\"buffer_high_water\":" << value.buffer_high_water
              << ",\"collection_paused\":" << (value.collection_paused ? "true" : "false")
              << ',';
        } else if constexpr (std::is_same_v<T, AppRegistryEvent>) {
          out << "\"app_id\":" << value.app_id << ",\"process_name\":\""
              << escaped(value.process_name) << "\",\"category\":\""
              << category(value.category) << "\",";
        } else if constexpr (std::is_same_v<T, DeviceMetadataEvent>) {
          out << "\"keyboard_device_class\":\""
              << device_class(value.keyboard_device_class) << "\","
              << "\"mouse_device_class\":\"" << device_class(value.mouse_device_class)
              << "\",\"screen_width_px\":" << value.screen_width_px
              << ",\"screen_height_px\":" << value.screen_height_px << ",\"dpi\":"
              << value.dpi << ',';
        }
        out << "\"seq\":" << value.seq << '}';
        return out.str();
      },
      event);
}

std::vector<std::uint8_t> length_prefix(std::string payload) {
  if (payload.size() > 0xffffffffULL) {
    throw std::length_error("event frame exceeds the transport length prefix");
  }
  const auto size = static_cast<std::uint32_t>(payload.size());
  std::vector<std::uint8_t> framed;
  framed.reserve(payload.size() + 4);
  framed.push_back(static_cast<std::uint8_t>((size >> 24U) & 0xffU));
  framed.push_back(static_cast<std::uint8_t>((size >> 16U) & 0xffU));
  framed.push_back(static_cast<std::uint8_t>((size >> 8U) & 0xffU));
  framed.push_back(static_cast<std::uint8_t>(size & 0xffU));
  framed.insert(framed.end(), payload.begin(), payload.end());
  return framed;
}

}  // namespace continuous_auth::collector
