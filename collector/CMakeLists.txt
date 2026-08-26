cmake_minimum_required(VERSION 3.20)
project(continuous_auth_collector VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(continuous_auth_protocol INTERFACE)
target_include_directories(
  continuous_auth_protocol
  INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/../protocol/generated/cpp"
)

add_executable(continuous_auth_collector src/main.cpp)
target_link_libraries(continuous_auth_collector PRIVATE continuous_auth_protocol)

include(CTest)
if(BUILD_TESTING)
  add_executable(collector_contract_smoke tests/contract_smoke.cpp)
  target_link_libraries(collector_contract_smoke PRIVATE continuous_auth_protocol)
  add_test(NAME collector_contract_smoke COMMAND collector_contract_smoke)
endif()

if(MSVC)
  target_compile_options(continuous_auth_collector PRIVATE /W4 /WX)
  if(BUILD_TESTING)
    target_compile_options(collector_contract_smoke PRIVATE /W4 /WX)
  endif()
else()
  target_compile_options(continuous_auth_collector PRIVATE -Wall -Wextra -Wpedantic -Werror)
  if(BUILD_TESTING)
    target_compile_options(collector_contract_smoke PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
endif()



