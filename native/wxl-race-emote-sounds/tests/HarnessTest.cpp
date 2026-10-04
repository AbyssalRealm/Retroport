// Loads the real wxl-race-emote-sounds.dll in a 32-bit process, maps fake client memory at the
// addresses it reads, and drives its detours through a fake WarcraftXL API.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <wxl/PluginApi.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace
{
    struct Rec { uint32_t id, emote, race, sex, sound; };

    void* g_detourId = nullptr;
    void* g_detourIndex = nullptr;
    int g_failures = 0;

    // The "client": Human (1) laughs with sound 500, Orc (2) with 600; variants 7 and 9.
    uint32_t __cdecl NativeSoundId(uint32_t emote, uint32_t race, uint32_t sex)
    {
        if (race >= 22 || sex >= 2) return 0;
        if (emote == 11 && race == 1 && sex == 0) return 500;
        if (emote == 11 && race == 2 && sex == 0) return 600;
        return 0;
    }
    int32_t __cdecl NativeSoundIndex(uint32_t emote, uint32_t race, uint32_t sex)
    {
        const uint32_t s = NativeSoundId(emote, race, sex);
        return s == 500 ? 7 : s == 600 ? 9 : -1;
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

    int __cdecl HookAttach(const char* name, uintptr_t target, void* detour, void** original, int)
    {
        std::printf("hook %s at 0x%08X\n", name, static_cast<unsigned>(target));
        if (target == 0x004CEB30) { g_detourId = detour; *original = reinterpret_cast<void*>(&NativeSoundId); return 1; }
        if (target == 0x004CEB70) { g_detourIndex = detour; *original = reinterpret_cast<void*>(&NativeSoundIndex); return 1; }
        return 0;
    }

    void Expect(const char* what, long long got, long long want)
    {
        const bool ok = got == want;
        if (!ok) ++g_failures;
        std::printf("%s %-58s got %lld want %lld\n", ok ? "PASS" : "FAIL", what, got, want);
    }
}

int main(int argc, char** argv)
{
    const char* dll = argc > 1 ? argv[1] : "wxl-race-emote-sounds.dll";

    // fake g_emotesTextSoundDB: count at 0x00AD37B4, record pointer at 0x00AD37C8
    void* page = VirtualAlloc(reinterpret_cast<void*>(0x00AD0000), 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (page != reinterpret_cast<void*>(0x00AD0000)) { std::printf("cannot map 0x00AD0000\n"); return 2; }
    static Rec rows[] = {
        { 1, 11, 1, 0, 500 },    // Human male /laugh
        { 2, 11, 22, 0, 500 },   // Kul Tiran (Alliance) male /laugh = Human's
        { 3, 11, 23, 0, 999 },   // Kul Tiran (Horde) male /laugh: a sound no native race uses
        { 4, 12, 22, 1, 0 },     // a row without a sound
    };
    *reinterpret_cast<uint32_t*>(0x00AD37B4) = sizeof(rows) / sizeof(rows[0]);
    *reinterpret_cast<Rec**>(0x00AD37C8) = rows;

    HMODULE mod = LoadLibraryA(dll);
    if (!mod) { std::printf("LoadLibrary(%s) failed: %lu\n", dll, GetLastError()); return 2; }
    auto query = reinterpret_cast<WXL_QueryFn>(GetProcAddress(mod, "WXL_Query"));
    auto load = reinterpret_cast<WXL_LoadFn>(GetProcAddress(mod, "WXL_Load"));
    if (!query || !load) { std::printf("missing exports\n"); return 2; }

    const WXL_PluginInfo* info = query();
    Expect("info.apiVersion", info->apiVersion, WXL_API_VERSION);
    Expect("info.clientBuild", info->clientBuild, 12340);
    Expect("info.structSize", info->structSize, sizeof(WXL_PluginInfo));
    std::printf("name: %s\n", info->name);

    WXL_Api api;
    std::memset(&api, 0, sizeof(api));
    api.structSize = sizeof(WXL_Api);
    api.apiVersion = WXL_API_VERSION;
    api.Log = &Log;
    api.HookAttach = &HookAttach;
    Expect("WXL_Load succeeds", load(&api), 1);
    if (!g_detourId || !g_detourIndex) { std::printf("detours not attached\n"); return 1; }

    auto id = reinterpret_cast<uint32_t(__cdecl*)(uint32_t, uint32_t, uint32_t)>(g_detourId);
    auto index = reinterpret_cast<int32_t(__cdecl*)(uint32_t, uint32_t, uint32_t)>(g_detourIndex);
    Expect("native race passes through: Human /laugh sound", id(11, 1, 0), 500);
    Expect("native race passes through: Human /laugh variant", index(11, 1, 0), 7);
    Expect("race 22 /laugh sound from EmotesTextSound", id(11, 22, 0), 500);
    Expect("race 22 /laugh variant via a stand-in race (Human)", index(11, 22, 0), 7);
    Expect("race 23 unshared sound", id(11, 23, 0), 999);
    Expect("race 23 unshared sound -> variant 0", index(11, 23, 0), 0);
    Expect("race 23 female: no row -> no sound", id(11, 23, 1), 0);
    Expect("race 23 female: no row -> index -1", index(11, 23, 1), -1);
    Expect("row without a sound stays silent", id(12, 22, 1), 0);
    Expect("bad sex value", id(11, 22, 5), 0);

    std::printf(g_failures ? "%d FAILURE(S)\n" : "ALL PASSED\n", g_failures);
    return g_failures ? 1 : 0;
}
