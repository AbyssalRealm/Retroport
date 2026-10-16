// wxl-water-line: draw a faded model whole at the water line instead of cutting it with a clip plane.
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
// Why: when a unit stands above liquid, the client sets a horizontal plane at the liquid's surface on
// it (0x007C10C0), and CM2Scene's element build tests each model's header bounding sphere against it
// (0x00821CBF). A model whose sphere straddles the plane is, for its TRANSPARENT batches, drawn in both
// water passes with a user clip plane at the surface (element flag 2, set up at 0x0081FD61) - if the
// scene's split bit is set, `test byte ptr [edx+4], 2` at 0x00821DAF. A player model turns transparent
// at the closest camera zoom (the client fades it, invisibly at first), so standing near water and
// zoomed in, the clip plane applies - and in this client it cuts through the model: a character's face,
// belt buckle and fingertips vanished and shoulder pads opened onto their own flames at certain camera
// angles. Models with large bounds (a ported race's, shoulders with big particle effects) straddle
// water they are nowhere near.
//
// What: clear that test's immediate, so a straddling model is never split; the client's own other
// branch then draws it whole on one side of the water (the scene's +0x140 picks which), as it already
// does for every opaque batch. The overlay (F9) gets a checkbox that puts the client's split back,
// for comparing the two live.

#define WXL_EXTENSION
#include <wxl/PluginApi.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace
{
    constexpr uintptr_t kSplitTest = 0x00821DAF;            // test byte ptr [edx+4], 2  (client 12340)
    constexpr uint8_t   kSplitTestBytes[3] = {0xF6, 0x42, 0x04};
    constexpr uintptr_t kSplitBit = kSplitTest + 3;          // the test's immediate
    constexpr uint8_t   kClientSplit = 0x02;
    constexpr uint8_t   kNoSplit = 0x00;
    constexpr const char* kTag = "water-line";

    const WXL_Api* g_api = nullptr;
    int g_split = 0;                                         // overlay checkbox: 1 = the client's own split

    bool Readable(uintptr_t at, size_t size)
    {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(reinterpret_cast<const void*>(at), &mbi, sizeof mbi) || mbi.State != MEM_COMMIT)
            return false;
        const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE;
        const uintptr_t end = reinterpret_cast<uintptr_t>(mbi.BaseAddress) + mbi.RegionSize;
        return (mbi.Protect & readable) && !(mbi.Protect & PAGE_GUARD) && at + size <= end;
    }

    bool WriteByte(uintptr_t at, uint8_t value)
    {
        DWORD old = 0;
        if (!VirtualProtect(reinterpret_cast<void*>(at), 1, PAGE_EXECUTE_READWRITE, &old))
            return false;
        *reinterpret_cast<volatile uint8_t*>(at) = value;
        DWORD ignored = 0;
        VirtualProtect(reinterpret_cast<void*>(at), 1, old, &ignored);
        FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(at), 1);
        return true;
    }

    void __cdecl Panel(void*)
    {
        g_api->UiText("Models crossing a water surface: with the client's split, a faded model");
        g_api->UiText("(your character, zoomed in close) is cut by a clip plane at the water line.");
        if (g_api->UiCheckbox("Split models at the water line (client default)", &g_split))
        {
            WriteByte(kSplitBit, g_split ? kClientSplit : kNoSplit);
            g_api->Log(WXL_LOG_INFO, kTag, g_split ? "client's water-line split restored"
                                                   : "models crossing water are drawn whole again");
        }
    }

    bool HasPanels(const WXL_Api* api)
    {
        return api->structSize >= offsetof(WXL_Api, UiCheckbox) + sizeof(api->UiCheckbox) && api->UiAddPanel
               && api->UiText && api->UiCheckbox;
    }
}

extern "C"
{
    __declspec(dllexport) const WXL_PluginInfo* __cdecl WXL_Query(void)
    {
        static const WXL_PluginInfo info = {
            sizeof(WXL_PluginInfo), WXL_API_VERSION, kTag, 1, WXL_CLIENT_BUILD,
        };
        return &info;
    }

    __declspec(dllexport) int __cdecl WXL_Load(const WXL_Api* api)
    {
        if (!api || api->structSize < offsetof(WXL_Api, Log) + sizeof(api->Log) || !api->Log)
            return 0;
        g_api = api;
        if (!Readable(kSplitTest, 4) || std::memcmp(reinterpret_cast<const void*>(kSplitTest), kSplitTestBytes, 3) != 0)
        {
            api->Log(WXL_LOG_ERROR, kTag, "0x%08X is not the element build's split test this extension knows - "
                                          "not a 12340 client, or patched by something else; nothing changed",
                     static_cast<unsigned>(kSplitTest));
            return 0;
        }
        const uint8_t current = *reinterpret_cast<const uint8_t*>(kSplitBit);
        if (current != kClientSplit && current != kNoSplit)
        {
            api->Log(WXL_LOG_ERROR, kTag, "the split test at 0x%08X tests 0x%02X, not the client's 0x02; nothing changed",
                     static_cast<unsigned>(kSplitTest), current);
            return 0;
        }
        if (!WriteByte(kSplitBit, kNoSplit))
        {
            api->Log(WXL_LOG_ERROR, kTag, "could not write 0x%08X", static_cast<unsigned>(kSplitBit));
            return 0;
        }
        if (HasPanels(api))
            api->UiAddPanel("Water line (retroport)", &Panel, nullptr);
        api->Log(WXL_LOG_INFO, kTag, "models crossing a water surface are drawn whole, not cut at the water line%s",
                 HasPanels(api) ? " (F9 overlay: 'Water line' to compare)" : "");
        return 1;
    }
}
