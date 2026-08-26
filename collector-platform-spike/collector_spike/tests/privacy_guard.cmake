if(NOT DEFINED SOURCE_ROOT)
    message(FATAL_ERROR "SOURCE_ROOT is required")
endif()

file(GLOB_RECURSE COLLECTOR_SOURCE
    "${SOURCE_ROOT}/*.cpp"
    "${SOURCE_ROOT}/*.hpp"
)

set(FORBIDDEN_PATTERNS
    "GetWindowText"
    "CGWindowList"
    "XFetchName"
    "clipboard"
    "Clipboard"
    "screenshot"
    "Screenshot"
    "keycode"
    "Keycode"
    "Accessibility API"
    "AXUIElement"
)

foreach(SOURCE_FILE IN LISTS COLLECTOR_SOURCE)
    file(READ "${SOURCE_FILE}" SOURCE_TEXT)
    foreach(PATTERN IN LISTS FORBIDDEN_PATTERNS)
        string(FIND "${SOURCE_TEXT}" "${PATTERN}" MATCH_INDEX)
        if(NOT MATCH_INDEX EQUAL -1)
            message(FATAL_ERROR
                "Privacy guard rejected forbidden collector token '${PATTERN}' in ${SOURCE_FILE}")
        endif()
    endforeach()
endforeach()

message(STATUS "Collector privacy guard passed")
