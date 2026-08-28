#include <type_traits>
#include <variant>

#include "contracts.hpp"

int main() {
  using namespace continuous_auth::protocol::v1;
  static_assert(std::is_same_v<EventFrame,
                               std::variant<KeyboardEvent, MouseEvent, ContextEvent, Heartbeat,
                                            AppRegistryEvent, DeviceMetadataEvent>>);
  static_assert(std::is_enum_v<KeyClass>);
  static_assert(kProtocolVersion == "1.0.0");
  return 0;
}
