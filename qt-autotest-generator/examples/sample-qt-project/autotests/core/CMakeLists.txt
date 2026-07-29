cmake_minimum_required(VERSION 3.16)

set(QT_VERSION "5")
set(PROJECT_LIBRARIES "sample-qt-project")

file(GLOB TEST_SOURCES "test_*.cpp")

set(STUB_SHADOW "${CMAKE_SOURCE_DIR}/autotests/3rdparty/stub/stub-shadow.cpp")

if(NOT TEST_SOURCES)
    message(WARNING "No test files found in ${CMAKE_CURRENT_SOURCE_DIR}")
    return()
endif()

add_executable(test_core ${TEST_SOURCES} ${STUB_SHADOW})

target_link_libraries(test_core
    PRIVATE
    GTest::gtest
    GTest::gtest_main
    Qt5::Core
    
)

if(PROJECT_LIBRARIES)
    target_link_libraries(test_core
        PRIVATE
        ${PROJECT_LIBRARIES}
    )
endif()

target_include_directories(test_core
    PRIVATE
    ${CMAKE_SOURCE_DIR}/autotests/3rdparty/stub
    ${CMAKE_SOURCE_DIR}/src
)

gtest_discover_tests(test_core PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

message(STATUS "UT: test_core configured with ${TEST_SOURCES}")
