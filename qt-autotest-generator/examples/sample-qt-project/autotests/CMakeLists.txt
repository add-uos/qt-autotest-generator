cmake_minimum_required(VERSION 3.16)
project(autotests VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_MODULE_PATH ${CMAKE_MODULE_PATH} "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(UnitTestUtils)

find_package(GTest REQUIRED)
include(GoogleTest)

find_package(Qt5 COMPONENTS Core REQUIRED)

add_subdirectory(core)

message(STATUS "UT: Unit tests configuration complete")
