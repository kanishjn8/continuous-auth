#pragma once

#include "continuous_auth/collector/context.hpp"

#include <memory>
#include <string>

namespace continuous_auth::collector {

#if defined(__APPLE__)
std::unique_ptr<ContextResolver> make_macos_context_resolver(ApplicationCategories categories);
std::unique_ptr<PauseSignal> make_macos_pause_signal(const std::string& event_name);
#endif

}  // namespace continuous_auth::collector
