#include "collector_spike/hook_backend.hpp"

#if defined(_WIN32)
#include "collector_spike/windows_hook_backend.hpp"
#elif defined(__APPLE__)
#include "collector_spike/macos_hook_backend.hpp"
#elif defined(__linux__)
#include "collector_spike/linux_x11_hook_backend.hpp"
#endif

namespace continuous_auth::collector::spike {

std::unique_ptr<HookBackend> make_platform_hook_backend(CaptureMetrics& metrics) {
#if defined(_WIN32)
    return std::make_unique<WindowsHookBackend>(metrics);
#elif defined(__APPLE__)
    return std::make_unique<MacOSHookBackend>(metrics);
#elif defined(__linux__)
    return std::make_unique<LinuxX11HookBackend>(metrics);
#else
    static_cast<void>(metrics);
    return nullptr;
#endif
}

}  // namespace continuous_auth::collector::spike
