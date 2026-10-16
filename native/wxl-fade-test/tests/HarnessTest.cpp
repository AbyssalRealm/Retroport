// Loads the real wxl-fade-test.dll in a 32-bit process with the client's code page mapped at its own
// address (0x00820000) holding the element build's see-through threshold load and fade test and the
// batch draw's palette-skip jump, and checks: the ini it writes with the defaults (and over an older
// one), that saving the ini changes the threshold and flips the switches while the game runs, that the
// overlay goes through the same file, and that a site with unexpected bytes is left alone.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <wxl/PluginApi.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace
{
    constexpr uintptr_t kPage = 0x00820000;          // reservations are 64 KB aligned; every site is inside
    constexpr uintptr_t kSkip = 0x00820451;
    constexpr uintptr_t kLoad = 0x00821F52;
    constexpr uintptr_t kOperand = 0x00821F54;
    constexpr uintptr_t kFade = 0x00821F66;
    constexpr uint32_t  kClientThreshold = 0x00A45528;
    const uint8_t kSkipClient[6] = {0x0F, 0x84, 0xE8, 0x00, 0x00, 0x00};
    const uint8_t kSkipNone[6] = {0x0F, 0x84, 0x00, 0x00, 0x00, 0x00};   // je +0: both ways upload
    const uint8_t kLoadClient[6] = {0xD9, 0x05, 0x28, 0x55, 0xA4, 0x00}; // fld dword ptr ds:[0x00A45528]
    const uint8_t kFadeClient[2] = {0x7B, 0x03};
    const uint8_t kFadeJump[2] = {0xEB, 0x03};
    int g_failures = 0;
    WXL_PanelFn g_panel = nullptr;
    const char* g_click = nullptr;       // label prefix of the checkbox the fake overlay clicks
    float g_slide = -1.0f;               // a value the fake slider moves to, once
    std::string g_ini;

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

    int __cdecl UiCheckbox(const char* label, int* value)
    {
        if (!g_click || std::strncmp(label, g_click, std::strlen(g_click)) != 0)
            return 0;
        g_click = nullptr;
        *value = !*value;
        return 1;
    }

    int __cdecl UiSliderFloat(const char*, float* value, float, float)
    {
        if (g_slide < 0.0f)
            return 0;
        *value = g_slide;
        g_slide = -1.0f;
        return 1;
    }

    bool Holds(uintptr_t at, const uint8_t* bytes, size_t size)
    {
        return std::memcmp(reinterpret_cast<const void*>(at), bytes, size) == 0;
    }

    uint32_t Operand() { return *reinterpret_cast<const volatile uint32_t*>(kOperand); }

    /// The value the client's load now reads (through the operand).
    float Threshold()
    {
        const uint32_t at = Operand();
        return at == kClientThreshold ? 0.99999f : *reinterpret_cast<const volatile float*>(static_cast<uintptr_t>(at));
    }

    /// Waits up to two seconds for the watcher to make `done` true.
    template <class F>
    bool Becomes(F done)
    {
        for (int i = 0; i < 40 && !done(); ++i)
            Sleep(50);
        return done();
    }

    void Poke(void* page, uintptr_t at, const uint8_t* bytes, size_t size)
    {
        DWORD old = 0;
        VirtualProtect(page, 0x10000, PAGE_EXECUTE_READWRITE, &old);
        std::memcpy(reinterpret_cast<void*>(at), bytes, size);
        VirtualProtect(page, 0x10000, PAGE_EXECUTE_READ, &old);       // like the client's .text
    }

    int Load(HMODULE dll, const WXL_Api& api)
    {
        auto load = reinterpret_cast<WXL_LoadFn>(GetProcAddress(dll, "WXL_Load"));
        return load ? load(&api) : -1;
    }

    void Save(const char* key, const char* value)
    {
        Sleep(20);                                   // a fresh write time on coarse file systems
        WritePrivateProfileStringA("FadeTest", key, value, g_ini.c_str());
    }

    int Ini(const char* key) { return GetPrivateProfileIntA("FadeTest", key, -1, g_ini.c_str()); }
}

int main(int argc, char** argv)
{
    const char* path = argc > 1 ? argv[1] : "wxl-fade-test.dll";
    void* page = VirtualAlloc(reinterpret_cast<void*>(kPage), 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE);
    if (page != reinterpret_cast<void*>(kPage))
    {
        MEMORY_BASIC_INFORMATION mbi{};
        VirtualQuery(reinterpret_cast<void*>(kPage), &mbi, sizeof mbi);
        std::printf("FAIL could not map 0x%08X for the fake client code (error %lu; there: state %lx type %lx base %p)\n",
                    static_cast<unsigned>(kPage), GetLastError(), mbi.State, mbi.Type, mbi.AllocationBase);
        return 1;
    }
    Poke(page, kSkip, kSkipClient, 6);
    Poke(page, kLoad, kLoadClient, 6);
    Poke(page, kFade, kFadeClient, 2);

    char full[MAX_PATH] = {};
    GetFullPathNameA(path, MAX_PATH, full, nullptr);
    g_ini = full;
    g_ini = g_ini.substr(0, g_ini.find_last_of('\\') + 1) + "wxl-fade-test.ini";
    // an ini from the version before the threshold, as a tester's install has one
    WritePrivateProfileStringA("FadeTest", "UploadBones", "1", g_ini.c_str());
    WritePrivateProfileStringA("FadeTest", "SolidFade", "0", g_ini.c_str());

    WXL_Api api{};
    api.structSize = sizeof(WXL_Api);
    api.apiVersion = WXL_API_VERSION;
    api.Log = &Log;
    api.UiAddPanel = &UiAddPanel;
    api.UiText = &UiText;
    api.UiCheckbox = &UiCheckbox;
    api.UiSliderFloat = &UiSliderFloat;

    HMODULE dll = LoadLibraryA(full);
    if (!dll)
    {
        std::printf("FAIL could not load %s (%lu)\n", full, GetLastError());
        return 1;
    }
    Check(Load(dll, api) == 1, "loads on the client's code");
    char text[32] = {};
    GetPrivateProfileStringA("FadeTest", "FadeThreshold", "", text, sizeof text, g_ini.c_str());
    Check(std::strcmp(text, "0.9") == 0 && Ini("UploadBones") == 0 && Ini("SolidFade") == 0,
          "an ini from before the threshold is rewritten with the new defaults");
    Check(Operand() != kClientThreshold && Threshold() == 0.9f, "the load reads 0.9 instead of 0.99999");
    Check(Holds(kLoad, kLoadClient, 2), "only the load's operand changed");
    Check(Holds(kSkip, kSkipClient, 6) && Holds(kFade, kFadeClient, 2), "the two switches start off");

    Save("FadeThreshold", "0.5");
    Check(Becomes([] { return Threshold() == 0.5f; }), "saving FadeThreshold=0.5 takes effect while running");
    Save("FadeThreshold", "1");
    Check(Becomes([] { return Operand() == kClientThreshold; }), "FadeThreshold=1 puts the client's own load back");
    Save("FadeThreshold", "garbage");
    Check(Becomes([] { return Threshold() == 0.9f; }), "an unreadable value means the default");
    Save("SolidFade", "1");
    Check(Becomes([] { return Holds(kFade, kFadeJump, 2); }), "SolidFade=1 draws faded models solid");
    Save("UploadBones", "1");
    Check(Becomes([] { return Holds(kSkip, kSkipNone, 6); }), "UploadBones=1 uploads bones on every draw");
    Save("SolidFade", "0");
    Save("UploadBones", "0");
    Check(Becomes([] { return Holds(kFade, kFadeClient, 2) && Holds(kSkip, kSkipClient, 6); }), "and both go back");

    Check(g_panel != nullptr, "an overlay panel is registered");
    if (g_panel)
    {
        g_slide = 0.75f;
        g_panel(nullptr);
        Check(Becomes([] { return Threshold() == 0.75f; }), "the slider writes the ini, which the watcher applies");
        g_click = "Draw faded";
        g_panel(nullptr);
        Check(Becomes([] { return Holds(kFade, kFadeJump, 2); }) && Ini("SolidFade") == 1, "so does a checkbox");
        Save("SolidFade", "0");
        Check(Becomes([] { return Holds(kFade, kFadeClient, 2); }), "and saving it back undoes it");
    }
    MEMORY_BASIC_INFORMATION mbi{};
    VirtualQuery(reinterpret_cast<void*>(kLoad), &mbi, sizeof mbi);
    Check(mbi.Protect == PAGE_EXECUTE_READ, "the page keeps its protection");
    Check(Load(dll, api) == 1 && Threshold() == 0.75f, "loading over its own patch is fine");

    const uint8_t foreign[2] = {0xE9, 0x00};                             // someone else's jump there
    Poke(page, kLoad, foreign, 2);
    Check(Load(dll, api) == 1, "a foreign threshold load does not stop the switches");
    Save("FadeThreshold", "0.3");
    Sleep(1200);
    Check(Holds(kLoad, foreign, 2), "and is left alone, whatever the ini says");
    Poke(page, kSkip, foreign, 2);
    Poke(page, kFade, foreign, 2);
    Check(Load(dll, api) == 0, "with no site its own, it declines to load");

    DeleteFileA(g_ini.c_str());
    std::printf("%s\n", g_failures ? "FAILED" : "all passed");
    std::fflush(stdout);
    TerminateProcess(GetCurrentProcess(), g_failures ? 1 : 0);      // the watcher thread never ends
    return g_failures ? 1 : 0;
}
