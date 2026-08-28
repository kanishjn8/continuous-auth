#pragma once

#include "contracts.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace continuous_auth::collector {

std::string encode_event_json(const continuous_auth::protocol::v1::EventFrame& event);
std::vector<std::uint8_t> length_prefix(std::string payload);

}  // namespace continuous_auth::collector
