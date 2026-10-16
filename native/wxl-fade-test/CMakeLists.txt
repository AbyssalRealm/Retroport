cmake_minimum_required(VERSION 3.20)
project(wxl-fade-test CXX)

# The 3.3.5a client is a 32-bit process: configure with -A Win32.
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "wxl-fade-test must be built 32-bit: cmake -A Win32 ...")
endif()

# Only the public extension header is needed (include/wxl/PluginApi.h, API v1).
set(WXL_CORE_DIR "I:/WoW Modding/wxl-core-1.1" CACHE PATH "wxl-core 1.1 checkout")
if(NOT EXISTS "${WXL_CORE_DIR}/include/wxl/PluginApi.h")
    message(FATAL_ERROR "PluginApi.h not found under WXL_CORE_DIR=${WXL_CORE_DIR}")
endif()

# Static CRT: the DLL carries no Visual C++ runtime dependency into the client folder.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(wxl-fade-test SHARED src/FadeTest.cpp)
target_include_directories(wxl-fade-test PRIVATE "${WXL_CORE_DIR}/include")
target_compile_features(wxl-fade-test PRIVATE cxx_std_17)
target_compile_options(wxl-fade-test PRIVATE /W4 /permissive- /utf-8)

# Harness: maps the client's code page at its address, loads the real DLL and checks the patches.
add_executable(wxl-fade-test-test tests/HarnessTest.cpp)
target_include_directories(wxl-fade-test-test PRIVATE "${WXL_CORE_DIR}/include")
target_compile_features(wxl-fade-test-test PRIVATE cxx_std_17)
# keep the harness image low so the client's code addresses (0x0082xxxx) are free for the fake page
target_link_options(wxl-fade-test-test PRIVATE /FIXED /BASE:0x00400000 /DYNAMICBASE:NO)

add_dependencies(wxl-fade-test-test wxl-fade-test)
