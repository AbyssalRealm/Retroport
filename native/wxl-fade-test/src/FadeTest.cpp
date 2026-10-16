// wxl-fade-test: settings for why a faded character mis-draws when the camera is zoomed in close.
// Copyright (C) 2026 retroport contributors
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.
//
// Why: the client fades a unit's model as the camera comes within half its radius of it (0x006FFCD0:
// a smoothstep of distance over radius into the instance's alpha factor), and the element build
// (0x00821C50) sends every opaque or alpha-keyed batch of a model whose alpha is below 0.99999 to the
// transparent pass (`fld [0x00A45528]` at 0x00821F52, `jnp` at 0x00821F66). There a model is drawn far
// to near without its own depth holding its parts in place: inner and far surfaces paint over near
// ones - a Kul Tiran's face gave way to the hair behind it, and its helmet, shoulders and weapon
// turned inside out. A Kul Tiran's camera at the two closest zoom steps sits on the edge of that
// fade, so standing still, its idle animations flip it between 1.0 (drawn right) and 0.99 (drawn
// wrong) every few seconds.
//
// What, as settings in wxl-fade-test.ini beside the DLL, read again within half a second of every save
// (no key needed: F-keys don't always reach the WarcraftXL overlay), and shown in the overlay's (F9)
// 'Fade test (retroport)' panel:
//   FadeThreshold - below this alpha a batch goes see-through (the client's own: 0.99999). 0.9 keeps a
//                   model the camera barely fades in the opaque pass, drawn whole; a real fade still
//                   goes see-through. The load's address operand is pointed at this extension's own
//                   value (one aligned dword: safe while the game draws).
//   SolidFade     - 1: faded models are never see-through (the jnp made a jmp).
//   UploadBones   - 1: every batch uploads its own bone palette (the batch draw's skip at 0x00820451
//                   made a je +0) - ruled out as the cause; kept for comparison.
// Each site is checked first: one whose instruction is not the client's own is left alone and says so.

#define WXL_EXTENSION
#include <wxl/PluginApi.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace
{
    constexpr const char* kTag = "fade-test";
    constexpr const char* kSection = "FadeTest";

    struct Switch
    {
        const char* key;          // ini key
        const char* label;        // what it does
        uintptr_t   at;           // the instruction
        uint8_t     code[6];      // its bytes as the client has them (12340)
        size_t      size;
        size_t      byte;         // the one byte the switch changes, from `at`
        uint8_t     changed;      // its value with the switch on
        int         fallback;     // the default written to a new ini
        volatile LONG on;
        bool        known;        // the instruction was the client's (or ours) at load
    };

    Switch g_switches[] = {
        // jnp 0x00821F6B (keep the batch opaque unless its alpha is below the threshold) -> jmp
        {"SolidFade", "Draw faded models solid (never see-through)", 0x00821F66,
         {0x7B, 0x03}, 2, 0, 0xEB, 0, 0, false},
        // je 0x0082053F (skip the upload when the palette start repeats) -> je +0
        {"UploadBones", "Upload a batch's bones on every draw", 0x00820451,
         {0x0F, 0x84, 0xE8, 0x00, 0x00, 0x00}, 6, 2, 0x00, 0, 0, false},
    };

    // fld dword ptr ds:[0x00A45528]: the element build's see-through threshold, 0.99999
    constexpr uintptr_t kThresholdLoad = 0x00821F52;
    constexpr uintptr_t kThresholdOperand = kThresholdLoad + 2;          // dword aligned
    constexpr uint32_t  kClientThreshold = 0x00A45528;
    constexpr float     kClientThresholdValue = 0.99999f;
    constexpr const char* kThresholdKey = "FadeThreshold";
    constexpr const char* kThresholdDefault = "0.9";
    alignas(4) volatile float g_threshold = kClientThresholdValue;     // what the load reads when pointed here
    bool g_thresholdKnown = false;

    const WXL_Api* g_api = nullptr;
    char g_ini[MAX_PATH] = {};
    FILETIME g_iniTime = {};
    HANDLE g_watcher = nullptr;

    bool Readable(uintptr_t at, size_t size)
    {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(reinterpret_cast<const void*>(at), &mbi, sizeof mbi) || mbi.State != MEM_COMMIT)
            return false;
        const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE;
        const uintptr_t end = reinterpret_cast<uintptr_t>(mbi.BaseAddress) + mbi.RegionSize;
        return (mbi.Protect & readable) && !(mbi.Protect & PAGE_GUARD) && at + size <= end;
    }

    /// The client's instruction, or ours: equal to `code` everywhere but the switch's byte, which holds either value.
    bool Known(const Switch& s)
    {
        if (!Readable(s.at, s.size))
            return false;
        const auto* p = reinterpret_cast<const uint8_t*>(s.at);
        for (size_t i = 0; i < s.size; ++i)
            if (p[i] != s.code[i] && !(i == s.byte && p[i] == s.changed))
                return false;
        return true;
    }

    uint32_t Ours() { return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&g_threshold)); }

    bool ThresholdKnown()
    {
        if (!Readable(kThresholdLoad, 6))
            return false;
        const auto* p = reinterpret_cast<const uint8_t*>(kThresholdLoad);
        const uint32_t operand = *reinterpret_cast<const uint32_t*>(kThresholdOperand);
        return p[0] == 0xD9 && p[1] == 0x05 && (operand == kClientThreshold || operand == Ours());
    }

    /// Writes one aligned dword (or byte) of code: the CPU sees either the old or the new instruction.
    template <class T>
    bool Patch(uintptr_t at, T value)
    {
        if (*reinterpret_cast<const volatile T*>(at) == value)
            return true;
        DWORD old = 0;
        if (!VirtualProtect(reinterpret_cast<void*>(at), sizeof(T), PAGE_EXECUTE_READWRITE, &old))
            return false;
        *reinterpret_cast<volatile T*>(at) = value;
        DWORD ignored = 0;
        VirtualProtect(reinterpret_cast<void*>(at), sizeof(T), old, &ignored);
        FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(at), sizeof(T));
        return true;
    }

    bool Apply(const Switch& s)
    {
        return Patch<uint8_t>(s.at + s.byte, s.on ? s.changed : s.code[s.byte]);
    }

    /// Below the client's own, the load reads this extension's value (set first); at or above it, the client's.
    bool ApplyThreshold(float value)
    {
        if (value < kClientThresholdValue)
        {
            g_threshold = value;
            return Patch<uint32_t>(kThresholdOperand, Ours());
        }
        return Patch<uint32_t>(kThresholdOperand, kClientThreshold);
    }

    float ReadThreshold()
    {
        char text[32] = {};
        GetPrivateProfileStringA(kSection, kThresholdKey, kThresholdDefault, text, sizeof text, g_ini);
        char* end = nullptr;
        float value = std::strtof(text, &end);
        if (end == text || !(value >= 0.0f))
            value = std::strtof(kThresholdDefault, nullptr);
        return value > 1.0f ? 1.0f : value;
    }

    bool IniTime(FILETIME& time)
    {
        WIN32_FILE_ATTRIBUTE_DATA data{};
        if (!GetFileAttributesExA(g_ini, GetFileExInfoStandard, &data))
            return false;
        time = data.ftLastWriteTime;
        return true;
    }

    void WriteDefaults()
    {
        FILE* f = nullptr;
        if (fopen_s(&f, g_ini, "w") || !f)
            return;
        std::fputs("; wxl-fade-test: settings for the close-camera fade. Edit and save while the game runs;\n"
                   "; changes apply within a second.\n"
                   "[FadeTest]\n"
                   "; Below this alpha a model is drawn see-through (the client's own: 0.99999). 0.9 draws a\n"
                   "; character the camera barely fades whole; 1 puts the client's own back.\n", f);
        std::fprintf(f, "%s=%s\n", kThresholdKey, kThresholdDefault);
        for (const Switch& s : g_switches)
            std::fprintf(f, "; 1 = %s\n%s=%d\n", s.label, s.key, s.fallback);
        std::fclose(f);
    }

    /// Reads every setting from the ini and applies the ones that changed.
    void Load()
    {
        for (Switch& s : g_switches)
        {
            const LONG want = GetPrivateProfileIntA(kSection, s.key, s.fallback, g_ini) ? 1 : 0;
            const LONG was = InterlockedExchange(&s.on, want);
            if (!s.known)
                continue;
            if (!Apply(s))
                g_api->Log(WXL_LOG_ERROR, kTag, "'%s': could not write 0x%08X", s.label, static_cast<unsigned>(s.at + s.byte));
            else if (want != was)
                g_api->Log(WXL_LOG_INFO, kTag, "%s: %s", s.label, want ? "on" : "off");
        }
        if (!g_thresholdKnown)
            return;
        const float value = ReadThreshold();
        const bool client = *reinterpret_cast<const volatile uint32_t*>(kThresholdOperand) == kClientThreshold;
        const float was = client ? kClientThresholdValue : g_threshold;
        if (!ApplyThreshold(value))
            g_api->Log(WXL_LOG_ERROR, kTag, "could not write 0x%08X", static_cast<unsigned>(kThresholdOperand));
        else if (value != was)
            g_api->Log(WXL_LOG_INFO, kTag, "see-through below alpha %.5f%s", value < kClientThresholdValue ? value : kClientThresholdValue,
                       value < kClientThresholdValue ? "" : " (the client's own)");
    }

    DWORD WINAPI Watch(void*)
    {
        for (;;)
        {
            Sleep(500);
            FILETIME now{};
            if (IniTime(now) && CompareFileTime(&now, &g_iniTime) != 0)
            {
                g_iniTime = now;
                Load();
            }
        }
    }

    bool HasSlider(const WXL_Api* api)
    {
        return api->structSize >= offsetof(WXL_Api, UiSliderFloat) + sizeof(api->UiSliderFloat) && api->UiSliderFloat;
    }

    void __cdecl Panel(void*)
    {
        g_api->UiText("Zoomed in close, the camera fades your character; below the threshold it is drawn see-through.");
        if (g_thresholdKnown && HasSlider(g_api))
        {
            const bool client = *reinterpret_cast<const volatile uint32_t*>(kThresholdOperand) == kClientThreshold;
            float value = client ? kClientThresholdValue : g_threshold;
            if (g_api->UiSliderFloat("See-through below alpha", &value, 0.0f, 1.0f))
            {
                char text[32] = {};
                std::snprintf(text, sizeof text, "%.5f", value);
                WritePrivateProfileStringA(kSection, kThresholdKey, text, g_ini);    // the watcher applies it
            }
        }
        for (Switch& s : g_switches)
        {
            if (!s.known)
            {
                g_api->UiText(s.label);
                g_api->UiText("  (unavailable: not the client's own code there)");
                continue;
            }
            int on = s.on;
            if (g_api->UiCheckbox(s.label, &on))
                WritePrivateProfileStringA(kSection, s.key, on ? "1" : "0", g_ini);
        }
        g_api->UiText(g_ini);
    }

    bool HasPanels(const WXL_Api* api)
    {
        return api->structSize >= offsetof(WXL_Api, UiCheckbox) + sizeof(api->UiCheckbox) && api->UiAddPanel
               && api->UiText && api->UiCheckbox;
    }

    bool FindIni()
    {
        HMODULE self = nullptr;
        if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                reinterpret_cast<LPCSTR>(&FindIni), &self))
            return false;
        const DWORD n = GetModuleFileNameA(self, g_ini, MAX_PATH);
        char* slash = n && n < MAX_PATH ? std::strrchr(g_ini, '\\') : nullptr;
        if (!slash)
            return false;
        return strcpy_s(slash + 1, MAX_PATH - static_cast<size_t>(slash + 1 - g_ini), "wxl-fade-test.ini") == 0;
    }
}

extern "C"
{
    __declspec(dllexport) const WXL_PluginInfo* __cdecl WXL_Query(void)
    {
        static const WXL_PluginInfo info = {
            sizeof(WXL_PluginInfo), WXL_API_VERSION, kTag, 3, WXL_CLIENT_BUILD,
        };
        return &info;
    }

    __declspec(dllexport) int __cdecl WXL_Load(const WXL_Api* api)
    {
        if (!api || api->structSize < offsetof(WXL_Api, Log) + sizeof(api->Log) || !api->Log)
            return 0;
        g_api = api;
        if (!FindIni())
        {
            api->Log(WXL_LOG_ERROR, kTag, "could not find its own folder for wxl-fade-test.ini");
            return 0;
        }
        int usable = 0;
        for (Switch& s : g_switches)
        {
            s.known = Known(s);
            if (s.known)
                ++usable;
            else
                api->Log(WXL_LOG_ERROR, kTag, "'%s': 0x%08X is not the client's own code - left alone",
                         s.label, static_cast<unsigned>(s.at));
        }
        g_thresholdKnown = ThresholdKnown();
        if (g_thresholdKnown)
            ++usable;
        else
            api->Log(WXL_LOG_ERROR, kTag, "0x%08X is not the client's see-through threshold load - left alone",
                     static_cast<unsigned>(kThresholdLoad));
        if (!usable)
            return 0;
        char probe[8] = {};
        if (GetFileAttributesA(g_ini) == INVALID_FILE_ATTRIBUTES
            || !GetPrivateProfileStringA(kSection, kThresholdKey, "", probe, sizeof probe, g_ini))
            WriteDefaults();                       // new, or from the version before the threshold
        IniTime(g_iniTime);
        for (Switch& s : g_switches)
            s.on = !GetPrivateProfileIntA(kSection, s.key, s.fallback, g_ini);   // so Load logs each one
        g_threshold = -1.0f;
        Load();
        if (!g_watcher)
            g_watcher = CreateThread(nullptr, 0, &Watch, nullptr, 0, nullptr);
        if (HasPanels(api))
            api->UiAddPanel("Fade test (retroport)", &Panel, nullptr);
        api->Log(WXL_LOG_INFO, kTag, "settings in %s (read within a second of every save)", g_ini);
        return 1;
    }
}
