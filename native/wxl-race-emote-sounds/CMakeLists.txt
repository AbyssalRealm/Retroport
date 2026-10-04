cmake_minimum_required(VERSION 3.20)
project(wxl-race-emote-sounds CXX)

# The 3.3.5a client is a 32-bit process: configure with -A Win32.
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "wxl-race-emote-sounds must be built 32-bit: cmake -A Win32 ...")
endif()

# Only the public extension header is needed (include/wxl/PluginApi.h, API v1).
set(WXL_CORE_DIR "I:/WoW Modding/wxl-core-1.1" CACHE PATH "wxl-core 1.1 checkout")
if(NOT EXISTS "${WXL_CORE_DIR}/include/wxl/PluginApi.h")
    message(FATAL_ERROR "PluginApi.h not found under WXL_CORE_DIR=${WXL_CORE_DIR}")
endif()

# Static CRT: the DLL carries no Visual C++ runtime dependency into the client folder.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(wxl-race-emote-sounds SHARED src/RaceEmoteSounds.cpp)
target_include_directories(wxl-race-emote-sounds PRIVATE "${WXL_CORE_DIR}/include")
target_compile_features(wxl-race-emote-sounds PRIVATE cxx_std_17)
target_compile_options(wxl-race-emote-sounds PRIVATE /W4 /permissive- /utf-8)

# Harness: loads the real DLL against fake client memory (see tests/HarnessTest.cpp).
add_executable(wxl-race-emote-sounds-test tests/HarnessTest.cpp)
target_include_directories(wxl-race-emote-sounds-test PRIVATE "${WXL_CORE_DIR}/include")
target_compile_features(wxl-race-emote-sounds-test PRIVATE cxx_std_17)
# keep the test image away from the client addresses it maps (0x00AD0000)
target_link_options(wxl-race-emote-sounds-test PRIVATE /BASE:0x20000000 /DYNAMICBASE:NO /FIXED:NO)
add_dependencies(wxl-race-emote-sounds-test wxl-race-emote-sounds)
