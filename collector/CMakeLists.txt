cmake_minimum_required(VERSION 3.20)
project(continuous_auth_collector VERSION 0.2.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(continuous_auth_protocol INTERFACE)
target_include_directories(
  continuous_auth_protocol
  INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/../protocol/generated/cpp"
)

add_library(continuous_auth_collector_core
  src/buffer/publisher.cpp
  src/config/config.cpp
  src/context/context.cpp
  src/hooks/hook_backend.cpp
  src/ipc/frame_encoder.cpp
  src/ipc/named_pipe_transport.cpp
  src/timing/clock.cpp
)
target_include_directories(
  continuous_auth_collector_core
  PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include"
)
target_link_libraries(continuous_auth_collector_core PUBLIC continuous_auth_protocol)

if(WIN32)
  target_link_libraries(continuous_auth_collector_core PRIVATE User32 Psapi)
elseif(APPLE)
  enable_language(OBJCXX)
  find_library(APPKIT_FRAMEWORK AppKit REQUIRED)
  find_library(APPLICATION_SERVICES_FRAMEWORK ApplicationServices REQUIRED)
  target_sources(continuous_auth_collector_core PRIVATE src/context/macos_context.mm)
  target_link_libraries(
    continuous_auth_collector_core
    PRIVATE ${APPKIT_FRAMEWORK} ${APPLICATION_SERVICES_FRAMEWORK}
  )
endif()

add_executable(continuous_auth_collector src/main.cpp)
target_link_libraries(continuous_auth_collector PRIVATE continuous_auth_collector_core)

include(CTest)
if(BUILD_TESTING)
  add_executable(collector_contract_smoke tests/contract_smoke.cpp)
  target_link_libraries(collector_contract_smoke PRIVATE continuous_auth_protocol)
  add_test(NAME collector_contract_smoke COMMAND collector_contract_smoke)

  add_executable(collector_core_tests tests/collector_core_tests.cpp)
  target_link_libraries(collector_core_tests PRIVATE continuous_auth_collector_core)
  add_test(NAME collector_core_tests COMMAND collector_core_tests)
endif()

if(MSVC)
  target_compile_options(continuous_auth_collector_core PRIVATE /W4 /WX /permissive-)
  target_compile_options(continuous_auth_collector PRIVATE /W4 /WX /permissive-)
else()
  target_compile_options(continuous_auth_collector_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
  target_compile_options(continuous_auth_collector PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
