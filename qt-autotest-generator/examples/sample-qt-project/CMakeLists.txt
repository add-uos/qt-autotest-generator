cmake_minimum_required(VERSION 3.16)
project(sample-qt-project LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt5 COMPONENTS Core REQUIRED)

add_library(sample-qt-project STATIC
    src/calculator.cpp
)

target_include_directories(sample-qt-project PUBLIC src)
target_link_libraries(sample-qt-project PUBLIC Qt5::Core)
