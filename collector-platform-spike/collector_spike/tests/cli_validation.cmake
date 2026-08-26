if(NOT DEFINED COLLECTOR_EXECUTABLE)
    message(FATAL_ERROR "COLLECTOR_EXECUTABLE is required")
endif()

foreach(INVALID_VALUE IN ITEMS "not-a-number" "999" "60001" "1000trailing")
    execute_process(
        COMMAND "${COLLECTOR_EXECUTABLE}" --report-interval-ms "${INVALID_VALUE}"
        RESULT_VARIABLE RESULT
        OUTPUT_QUIET
        ERROR_QUIET
    )
    if(NOT RESULT EQUAL 2)
        message(FATAL_ERROR
            "Expected CLI status 2 for '${INVALID_VALUE}', received '${RESULT}'")
    endif()
endforeach()

message(STATUS "Collector CLI validation passed")
