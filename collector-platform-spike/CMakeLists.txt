cmake_minimum_required(VERSION 3.20)

project(native_collector_spike VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(collector_spike_core
    collector_spike/src/clock.cpp
    collector_spike/src/hook_backend_factory.cpp
    collector_spike/src/metrics.cpp
    collector_spike/src/process_resource_sampler.cpp
)

target_include_directories(collector_spike_core
    PUBLIC collector_spike/include
)

target_compile_features(collector_spike_core PUBLIC cxx_std_17)

if(MSVC)
    target_compile_options(collector_spike_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(collector_spike_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

if(WIN32)
    target_sources(collector_spike_core PRIVATE
        collector_spike/src/windows_hook_backend.cpp
    )
    target_link_libraries(collector_spike_core PRIVATE Psapi)
elseif(APPLE)
    find_library(APPLICATION_SERVICES_FRAMEWORK ApplicationServices REQUIRED)
    find_library(CORE_FOUNDATION_FRAMEWORK CoreFoundation REQUIRED)
    target_sources(collector_spike_core PRIVATE
        collector_spike/src/macos_hook_backend.cpp
    )
    target_link_libraries(collector_spike_core PRIVATE
        ${APPLICATION_SERVICES_FRAMEWORK}
        ${CORE_FOUNDATION_FRAMEWORK}
    )
elseif(UNIX)
    find_package(X11 REQUIRED)
    if(NOT TARGET X11::Xi)
        message(FATAL_ERROR "Linux collector requires the XInput2 development library")
    endif()
    target_sources(collector_spike_core PRIVATE
        collector_spike/src/linux_x11_hook_backend.cpp
    )
    target_link_libraries(collector_spike_core PRIVATE X11::X11 X11::Xi)
else()
    message(FATAL_ERROR "No native collector backend is available for this platform")
endif()

add_executable(native_collector_spike collector_spike/src/main.cpp)
target_link_libraries(native_collector_spike PRIVATE collector_spike_core)

add_executable(native_collector_spike_tests collector_spike/tests/collector_spike_tests.cpp)
target_link_libraries(native_collector_spike_tests PRIVATE collector_spike_core)

enable_testing()
add_test(NAME native_collector_spike_tests COMMAND native_collector_spike_tests)
add_test(
    NAME collector_privacy_guard
    COMMAND ${CMAKE_COMMAND}
        -DSOURCE_ROOT=${CMAKE_CURRENT_SOURCE_DIR}/collector_spike
        -P ${CMAKE_CURRENT_SOURCE_DIR}/collector_spike/tests/privacy_guard.cmake
)
add_test(
    NAME collector_cli_validation
    COMMAND ${CMAKE_COMMAND}
        -DCOLLECTOR_EXECUTABLE=$<TARGET_FILE:native_collector_spike>
        -P ${CMAKE_CURRENT_SOURCE_DIR}/collector_spike/tests/cli_validation.cmake
)
