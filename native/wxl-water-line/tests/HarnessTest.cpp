// Loads the real wxl-water-line.dll in a 32-bit process with the client's code page mapped at its own
// address (0x00821000) holding the element build's split test, and checks that loading clears the
// test's bit, that the overlay checkbox puts it back and takes it away, and that unexpected bytes are
// left alone.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <wxl/PluginApi.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace
{
    constexpr uintptr_t kPage = 0x00820000;          // reservations are 64 KB aligned; 0x00821DAF is inside
    constexpr uintptr_t kSplitTest = 0x00821DAF;
    int g_failures = 0;
    WXL_PanelFn g_panel = nullptr;
    int g_toggle = 0;                    // set to make the fake UiCheckbox report a click

    void Check(bool ok, const char* what)
    {
        std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
        g_failures += !ok;
    }

    void __cdecl Log(int level, const char* tag, const char* fmt, ...)
    {
        va_list ap;
        va_start(ap, fmt);
        std::printf("[log %d %s] ", level, tag);
        std::vprintf(fmt, ap);
        std::printf("\n");
        va_end(ap);
    }

    void __cdecl UiAddPanel(const char* title, WXL_PanelFn fn, void*)
    {
        std::printf("panel '%s'\n", title);
        g_panel = fn;
    }

    void __cdecl UiText(const char*) {}

    int __cdecl UiCheckbox(const char*, int* value)
    {
        if (!g_toggle)
            return 0;
        g_toggle = 0;
        *value = !*value;
        return 1;
    }

    uint8_t* Code() { return reinterpret_cast<uint8_t*>(kSplitTest); }

    int Load(HMODULE dll, const WXL_Api& api)
    {
        auto load = reinterpret_cast<WXL_LoadFn>(GetProcAddress(dll, "WXL_Load"));
        return load ? load(&api) : -1;
    }
}

int main(int argc, char** argv)
{
    const char* path = argc > 1 ? argv[1] : "wxl-water-line.dll";
    void* page = VirtualAlloc(reinterpret_cast<void*>(kPage), 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE);
    if (page != reinterpret_cast<void*>(kPage))
    {
        std::printf("FAIL could not map 0x%08X for the fake client code\n", static_cast<unsigned>(kPage));
        return 1;
    }
    const uint8_t splitTest[4] = {0xF6, 0x42, 0x04, 0x02};
    std::memcpy(Code(), splitTest, 4);
    DWORD old = 0;
    VirtualProtect(page, 0x10000, PAGE_EXECUTE_READ, &old);              // like the client's .text

    WXL_Api api{};
    api.structSize = sizeof(WXL_Api);
    api.apiVersion = WXL_API_VERSION;
    api.Log = &Log;
    api.UiAddPanel = &UiAddPanel;
    api.UiText = &UiText;
    api.UiCheckbox = &UiCheckbox;

    HMODULE dll = LoadLibraryA(path);
    if (!dll)
    {
        std::printf("FAIL could not load %s (%lu)\n", path, GetLastError());
        return 1;
    }
    Check(Load(dll, api) == 1, "loads on the client's split test");
    Check(Code()[3] == 0x00, "the test's bit is cleared: straddling models are drawn whole");
    Check(Code()[0] == 0xF6 && Code()[1] == 0x42 && Code()[2] == 0x04, "only the immediate changed");
    Check(g_panel != nullptr, "an overlay panel is registered");
    if (g_panel)
    {
        g_toggle = 1;
        g_panel(nullptr);
        Check(Code()[3] == 0x02, "the checkbox puts the client's split back");
        g_toggle = 1;
        g_panel(nullptr);
        Check(Code()[3] == 0x00, "and takes it away again");
    }
    MEMORY_BASIC_INFORMATION mbi{};
    VirtualQuery(Code(), &mbi, sizeof mbi);
    Check(mbi.Protect == PAGE_EXECUTE_READ, "the page keeps its protection");
    Check(Load(dll, api) == 1, "loading over its own patch is fine");

    VirtualProtect(page, 0x10000, PAGE_EXECUTE_READWRITE, &old);
    Code()[0] = 0x90;                                                    // someone else's code there
    VirtualProtect(page, 0x10000, PAGE_EXECUTE_READ, &old);
    Check(Load(dll, api) == 0, "refuses bytes it does not know");
    Check(Code()[0] == 0x90 && Code()[3] == 0x00, "and leaves them alone");

    std::printf("%s\n", g_failures ? "FAILED" : "all passed");
    return g_failures ? 1 : 0;
}
