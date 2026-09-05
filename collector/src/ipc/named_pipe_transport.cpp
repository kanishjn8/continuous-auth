#include "continuous_auth/collector/transport.hpp"

#if defined(_WIN32)
#include <windows.h>
#elif defined(__APPLE__)
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstring>
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
#elif defined(__APPLE__)
std::string runtime_directory() {
  return "/tmp/continuous-auth-" + std::to_string(static_cast<unsigned long>(getuid()));
}

class MacOSUnixSocketTransport final : public Transport {
public:
  explicit MacOSUnixSocketTransport(std::string name)
      : endpoint_(runtime_directory() + "/" + std::move(name) + ".sock") {}
  ~MacOSUnixSocketTransport() override { close(); }

  bool connect() noexcept override {
    close();
    sockaddr_un address{};
    if (endpoint_.size() >= sizeof(address.sun_path)) return false;
    descriptor_ = socket(AF_UNIX, SOCK_STREAM, 0);
    if (descriptor_ < 0) return false;
    int enabled = 1;
    if (setsockopt(descriptor_, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) != 0) {
      close();
      return false;
    }
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, endpoint_.c_str(), endpoint_.size() + 1);
    if (::connect(descriptor_, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0) {
      close();
      return false;
    }
    return true;
  }

  bool send(const std::vector<std::uint8_t>& frame) noexcept override {
    if (!connected()) return false;
    std::size_t offset = 0;
    while (offset < frame.size()) {
      const auto written = ::send(descriptor_, frame.data() + offset, frame.size() - offset, 0);
      if (written <= 0) {
        close();
        return false;
      }
      offset += static_cast<std::size_t>(written);
    }
    return true;
  }

  void close() noexcept override {
    if (descriptor_ >= 0) {
      ::close(descriptor_);
      descriptor_ = -1;
    }
  }

  bool connected() const noexcept override { return descriptor_ >= 0; }

private:
  std::string endpoint_;
  int descriptor_{-1};
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
#elif defined(__APPLE__)
  return std::make_unique<MacOSUnixSocketTransport>(pipe_name);
#else
  static_cast<void>(pipe_name);
  return std::make_unique<UnavailableTransport>();
#endif
}

}  // namespace continuous_auth::collector
