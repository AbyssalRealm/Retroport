cmake_minimum_required(VERSION 3.20)
project(wxl-wide-skin-geosets CXX)

# The 3.3.5a client is a 32-bit process: configure with -A Win32.
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "wxl-wide-skin-geosets must be built 32-bit: cmake -A Win32 ...")
endif()

# Only the public extension header is needed (include/wxl/PluginApi.h, API v1).
set(WXL_CORE_DIR "I:/WoW Modding/wxl-core-1.1" CACHE PATH "wxl-core 1.1 checkout")
if(NOT EXISTS "${WXL_CORE_DIR}/include/wxl/PluginApi.h")
    message(FATAL_ERROR "PluginApi.h not found under WXL_CORE_DIR=${WXL_CORE_DIR}")
endif()

# Static CRT: the DLL carries no Visual C++ runtime dependency into the client folder.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(wxl-wide-skin-geosets SHARED src/WideSkinGeosets.cpp)
target_include_directories(wxl-wide-skin-geosets PRIVATE "${WXL_CORE_DIR}/include")
target_compile_features(wxl-wide-skin-geosets PRIVATE cxx_std_17)
target_compile_options(wxl-wide-skin-geosets PRIVATE /W4 /permissive- /utf-8)

# Harness: loads the real DLL and drives its detour against a fake client (see tests/HarnessTest.cpp).
add_executable(wxl-wide-skin-geosets-test tests/HarnessTest.cpp)
target_include_directories(wxl-wide-skin-geosets-test PRIVATE "${WXL_CORE_DIR}/include")
target_compile_features(wxl-wide-skin-geosets-test PRIVATE cxx_std_17)

add_dependencies(wxl-wide-skin-geosets-test wxl-wide-skin-geosets)
