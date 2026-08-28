#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace continuous_auth::collector {

class Transport {
public:
  virtual ~Transport() = default;
  virtual bool connect() noexcept = 0;
  virtual bool send(const std::vector<std::uint8_t>& frame) noexcept = 0;
  virtual void close() noexcept = 0;
  virtual bool connected() const noexcept = 0;
};

std::unique_ptr<Transport> make_named_pipe_transport(const std::string& pipe_name);

}  // namespace continuous_auth::collector
