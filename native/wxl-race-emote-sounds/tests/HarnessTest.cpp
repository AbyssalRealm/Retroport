// Loads the real wxl-race-emote-sounds.dll in a 32-bit process, puts fake client data and code at
// the addresses it uses, and drives its detours through a fake WarcraftXL API.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <wxl/PluginApi.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>

// The client addresses the module uses (code at 0x004CBAC0 / 0x004CC180, data at 0x00AD37B4) lie
// inside this image: a 7.5 MB uninitialised section, linked readable, writable and executable,
// at the default exe base (see CMakeLists.txt). It is mapped with the image, before Windows maps
// anything else into the process - its locale tables land around 0x004C0000.
#pragma bss_seg(".client")
static uint8_t g_clientImage[0x780000];
#pragma bss_seg()

namespace
{
    struct Rec { uint32_t id, emote, race, sex, sound; };

    /// A client sound kit as far as the module reads it: the file count at +0x40.
    struct FakeKit
    {
        uint8_t pad[0x40];
        int32_t fileCount;
        int32_t variant;          // what the fake chooser picks
    };

    void* g_detourId = nullptr;
    void* g_detourIndex = nullptr;
    int g_failures = 0;

    FakeKit g_kit500{{}, 3, 2};   // Kul Tiran /laugh: three files, the chooser picks the third
    FakeKit g_kit999{{}, 1, 0};
    FakeKit g_kit777{{}, 0, 0};   // a kit without files
    int32_t g_chooseMode = 0, g_chooseForced = 0, g_chooseUnknown = 0;

    void* __cdecl FakeSoundKitById(uint32_t sound)
    {
        return sound == 500 ? &g_kit500 : sound == 999 ? &g_kit999 : sound == 777 ? &g_kit777 : nullptr;
    }

    // the client's chooser is __thiscall (ecx = kit, 4 stack arguments, callee cleans):
    // __fastcall with an unused edx has the same shape
    const char* __fastcall FakeChooseSoundFile(void* kit, void* /*edx*/, int32_t mode, int32_t* index,
                                               int32_t forced, int32_t unknown)
    {
        g_chooseMode = mode;
        g_chooseForced = forced;
        g_chooseUnknown = unknown;
        *index = static_cast<FakeKit*>(kit)->variant;
        return "file.ogg";
    }

    /// Writes `jmp target` at a client address.
    void Thunk(uintptr_t at, void* target)
    {
        auto* p = reinterpret_cast<uint8_t*>(at);
        p[0] = 0xE9;
        const int32_t rel = static_cast<int32_t>(reinterpret_cast<uintptr_t>(target) - (at + 5));
        std::memcpy(p + 1, &rel, sizeof(rel));
    }

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

    // like WarcraftXL: HookAttach only registers the detour; the chain's "original" pointers are
    // filled in later, when the core enables the hooks (EnableHooks below)
    void** g_slotId = nullptr;
    void** g_slotIndex = nullptr;

    int __cdecl HookAttach(const char* name, uintptr_t target, void* detour, void** original, int)
    {
        std::printf("hook %s at 0x%08X\n", name, static_cast<unsigned>(target));
        if (target == 0x004CEB30) { g_detourId = detour; g_slotId = original; return 1; }
        if (target == 0x004CEB70) { g_detourIndex = detour; g_slotIndex = original; return 1; }
        return 0;
    }

    void EnableHooks()
    {
        if (g_slotId) *g_slotId = reinterpret_cast<void*>(&NativeSoundId);
        if (g_slotIndex) *g_slotIndex = reinterpret_cast<void*>(&NativeSoundIndex);
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

    const auto lo = reinterpret_cast<uintptr_t>(g_clientImage), hi = lo + sizeof(g_clientImage);
    if (lo > 0x004CB000 || hi < 0x00AD4000)
    {
        std::printf("the test image does not cover the client addresses (0x%08X-0x%08X)\n",
                    static_cast<unsigned>(lo), static_cast<unsigned>(hi));
        return 2;
    }

    // fake g_emotesTextSoundDB: count at 0x00AD37B4, record pointer at 0x00AD37C8
    static Rec rows[] = {
        { 1, 11, 1, 0, 500 },    // Human male /laugh
        { 2, 11, 22, 0, 500 },   // Kul Tiran (Alliance) male /laugh
        { 3, 11, 23, 0, 999 },   // Kul Tiran (Horde) male /laugh: a sound no native race uses
        { 4, 12, 22, 1, 0 },     // a row without a sound
        { 5, 13, 22, 0, 777 },   // a sound whose kit has no files
        { 6, 14, 22, 0, 888 },   // a sound the client has no kit for
    };
    *reinterpret_cast<uint32_t*>(0x00AD37B4) = sizeof(rows) / sizeof(rows[0]);
    *reinterpret_cast<Rec**>(0x00AD37C8) = rows;

    // fake client code: SoundKitById at 0x004CC180, the file chooser at 0x004CBAC0
    Thunk(0x004CC180, reinterpret_cast<void*>(&FakeSoundKitById));
    Thunk(0x004CBAC0, reinterpret_cast<void*>(&FakeChooseSoundFile));
    FlushInstructionCache(GetCurrentProcess(), g_clientImage, sizeof(g_clientImage));

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
    Expect("WXL_Load succeeds before the chain is enabled", load(&api), 1);
    if (!g_detourId || !g_detourIndex) { std::printf("detours not attached\n"); return 1; }
    EnableHooks();

    auto id = reinterpret_cast<uint32_t(__cdecl*)(uint32_t, uint32_t, uint32_t)>(g_detourId);
    auto index = reinterpret_cast<int32_t(__cdecl*)(uint32_t, uint32_t, uint32_t)>(g_detourIndex);
    Expect("native race passes through: Human /laugh sound", id(11, 1, 0), 500);
    Expect("native race passes through: Human /laugh variant", index(11, 1, 0), 7);
    Expect("race 22 /laugh sound from EmotesTextSound", id(11, 22, 0), 500);
    Expect("race 22 /laugh variant from the client's chooser", index(11, 22, 0), 2);
    Expect("  chooser asked like the client does: mode 2", g_chooseMode, 2);
    Expect("  ... nothing forced (-1)", g_chooseForced, -1);
    Expect("  ... last argument 5", g_chooseUnknown, 5);
    Expect("race 23 unshared sound", id(11, 23, 0), 999);
    Expect("race 23 unshared sound -> its only file", index(11, 23, 0), 0);
    Expect("race 23 female: no row -> no sound", id(11, 23, 1), 0);
    Expect("race 23 female: no row -> index -1", index(11, 23, 1), -1);
    Expect("row without a sound stays silent", id(12, 22, 1), 0);
    Expect("kit without files -> index -1", index(13, 22, 0), -1);
    Expect("sound without a kit -> index -1", index(14, 22, 0), -1);
    Expect("bad sex value", id(11, 22, 5), 0);
    Expect("bad sex value -> index -1", index(11, 22, 5), -1);

    std::printf(g_failures ? "%d FAILURE(S)\n" : "ALL PASSED\n", g_failures);
    return g_failures ? 1 : 0;
}
