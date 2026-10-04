// Loads the real wxl-wide-skin-geosets.dll in a 32-bit process and drives its detour through a fake
// WarcraftXL API. The fake "client" SetGeometryVisible behaves like the real one: it matches
// submeshes on the full DWORD (level << 16 | id), queues the call when the instance isn't initialized.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <wxl/PluginApi.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
    struct Submesh { uint16_t id, level; uint8_t pad[0x30 - 4]; };
    static_assert(sizeof(Submesh) == 0x30, "submesh stride");

    struct Skin { uint8_t pad0[0x0C]; uint32_t indexCount; uint8_t pad1[0x1C - 0x10]; uint32_t submeshCount; Submesh* submeshes; };
    struct Shared { uint8_t pad[0x170]; Skin* skin; };
    struct Instance { uint8_t pad0[0x10]; uint32_t initFlags; uint8_t pad1[0x2C - 0x14]; Shared* shared; };

    std::vector<uint32_t> g_visible;          // the instance's visibility array (all visible at init)
    int g_calls = 0, g_queued = 0, g_failures = 0;
    void* g_detour = nullptr;

    void __fastcall FakeSetGeometryVisible(void* instance, void*, uint32_t first, uint32_t last, uint32_t visible)
    {
        ++g_calls;
        auto* inst = static_cast<Instance*>(instance);
        if (!(inst->initFlags & 1)) { ++g_queued; return; }
        Skin* skin = inst->shared->skin;
        for (uint32_t i = 0; i < skin->submeshCount; ++i)
        {
            const uint32_t key = *reinterpret_cast<const uint32_t*>(&skin->submeshes[i]);   // the client's DWORD read
            if (key >= first && key <= last) g_visible[i] = visible;
        }
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
        if (target != 0x0082C7C0) return 0;
        g_detour = detour;
        *original = reinterpret_cast<void*>(&FakeSetGeometryVisible);
        return 1;
    }

    void Expect(const char* what, long long got, long long want)
    {
        const bool ok = got == want;
        if (!ok) ++g_failures;
        std::printf("%s %-62s got %lld want %lld\n", ok ? "PASS" : "FAIL", what, got, want);
    }
}

int main(int argc, char** argv)
{
    const char* dll = argc > 1 ? argv[1] : "wxl-wide-skin-geosets.dll";
    HMODULE mod = LoadLibraryA(dll);
    if (!mod) { std::printf("LoadLibrary(%s) failed: %lu\n", dll, GetLastError()); return 2; }
    auto query = reinterpret_cast<WXL_QueryFn>(GetProcAddress(mod, "WXL_Query"));
    auto load = reinterpret_cast<WXL_LoadFn>(GetProcAddress(mod, "WXL_Load"));
    if (!query || !load) { std::printf("missing exports\n"); return 2; }
    const WXL_PluginInfo* info = query();
    Expect("info.apiVersion", info->apiVersion, WXL_API_VERSION);
    Expect("info.clientBuild", info->clientBuild, 12340);

    WXL_Api api;
    std::memset(&api, 0, sizeof(api));
    api.structSize = sizeof(WXL_Api);
    api.apiVersion = WXL_API_VERSION;
    api.Log = &Log;
    api.HookAttach = &HookAttach;
    Expect("WXL_Load succeeds", load(&api), 1);
    if (!g_detour) { std::printf("detour not attached\n"); return 1; }
    auto set = reinterpret_cast<void(__fastcall*)(void*, void*, uint32_t, uint32_t, uint32_t)>(g_detour);

    // a Kul Tiran female-like skin: body and an eye below the 16-bit line, the rest past it
    Submesh subs[6] = {};
    const uint16_t ids[6] = {0, 1702, 1502, 1703, 0, 1802};
    const uint16_t levels[6] = {0, 0, 1, 1, 1, 1};
    for (int i = 0; i < 6; ++i) { subs[i].id = ids[i]; subs[i].level = levels[i]; }
    Skin skin{};
    skin.indexCount = 101223;
    skin.submeshCount = 6;
    skin.submeshes = subs;
    Shared shared{};
    shared.skin = &skin;
    Instance inst{};
    inst.shared = &shared;

    // not initialized: one call, queued by the client, nothing added
    inst.initFlags = 0;
    g_visible.assign(6, 1);
    set(&inst, nullptr, 0, 2000, 0);
    Expect("before init: the client's call only (it queues it)", g_calls, 1);
    Expect("before init: queued once", g_queued, 1);

    // initialized: GeosRenderPrep's sequence - hide 0..2000, show 0, show the chosen geosets
    inst.initFlags = 1;
    g_calls = 0;
    set(&inst, nullptr, 0, 2000, 0);
    set(&inst, nullptr, 0, 0, 1);
    set(&inst, nullptr, 1702, 1702, 1);
    Expect("each call also made for level 1", g_calls, 6);
    Expect("body below the line shown", g_visible[0], 1);
    Expect("eye (1702) shown", g_visible[1], 1);
    Expect("cape past the line (1502) hidden", g_visible[2], 0);
    Expect("death-knight eyes past the line (1703) hidden", g_visible[3], 0);
    Expect("body past the line shown again", g_visible[4], 1);
    Expect("belt band past the line (1802) hidden", g_visible[5], 0);
    set(&inst, nullptr, 1802, 1802, 1);
    Expect("belt band past the line shown on request", g_visible[5], 1);

    // a stock skin (65,535 indices or fewer): level is an LOD marker there - client behaviour untouched
    skin.indexCount = 40000;
    g_calls = 0;
    g_visible.assign(6, 1);
    set(&inst, nullptr, 0, 2000, 0);
    Expect("small skin: one call only", g_calls, 1);
    Expect("small skin: a level-1 submesh keeps the client's behaviour", g_visible[2], 1);

    std::printf(g_failures ? "%d FAILURE(S)\n" : "ALL PASSED\n", g_failures);
    return g_failures ? 1 : 0;
}
