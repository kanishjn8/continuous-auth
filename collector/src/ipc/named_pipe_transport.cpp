#include "continuous_auth/collector/transport.hpp"

#if defined(_WIN32)
#include <windows.h>
#endif

#include <limits>
#include <utility>

namespace continuous_auth::collector {
namespace {

#if defined(_WIN32)
class WindowsNamedPipeTransport final : public Transport {
public:
  explicit WindowsNamedPipeTransport(std::string name)
      : name_(name.begin(), name.end()) {}
  ~WindowsNamedPipeTransport() override { close(); }

  bool connect() noexcept override {
    close();
    std::wstring endpoint = L"\\\\.\\pipe\\";
    endpoint.append(name_.begin(), name_.end());
    handle_ = CreateFileW(endpoint.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                          FILE_ATTRIBUTE_NORMAL, nullptr);
    return handle_ != INVALID_HANDLE_VALUE;
  }

  bool send(const std::vector<std::uint8_t>& frame) noexcept override {
    if (!connected()) {
      return false;
    }
    std::size_t offset = 0;
    while (offset < frame.size()) {
      const auto remaining = frame.size() - offset;
      const auto request = static_cast<DWORD>(
          remaining > (std::numeric_limits<DWORD>::max)()
              ? (std::numeric_limits<DWORD>::max)()
              : remaining);
      DWORD written{};
      if (WriteFile(handle_, frame.data() + offset, request, &written, nullptr) == 0 ||
          written == 0) {
        close();
        return false;
      }
      offset += written;
    }
    return true;
  }

  void close() noexcept override {
    if (handle_ != INVALID_HANDLE_VALUE) {
      CloseHandle(handle_);
      handle_ = INVALID_HANDLE_VALUE;
    }
  }

  bool connected() const noexcept override { return handle_ != INVALID_HANDLE_VALUE; }

private:
  std::wstring name_;
  HANDLE handle_{INVALID_HANDLE_VALUE};
};
#else
class UnavailableTransport final : public Transport {
public:
  bool connect() noexcept override { return false; }
  bool send(const std::vector<std::uint8_t>&) noexcept override { return false; }
  void close() noexcept override {}
  bool connected() const noexcept override { return false; }
};
#endif

}  // namespace

std::unique_ptr<Transport> make_named_pipe_transport(const std::string& pipe_name) {
#if defined(_WIN32)
  return std::make_unique<WindowsNamedPipeTransport>(pipe_name);
#else
  static_cast<void>(pipe_name);
  return std::make_unique<UnavailableTransport>();
#endif
}

}  // namespace continuous_auth::collector
