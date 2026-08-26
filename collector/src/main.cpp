#include <iostream>

#include "contracts.hpp"

int main() {
  using continuous_auth::protocol::v1::UserState;
  const auto initial_state = UserState::kEnrolling;
  static_cast<void>(initial_state);
  std::cout << "continuous-authentication collector substrate protocol="
            << continuous_auth::protocol::v1::kProtocolVersion << '\n';
  return 0;
}


