// wxl-wide-skin-geosets: let geosets past a skin's 65,535th index be shown and hidden.
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
// Why: a character skin with more than 65,535 indices (a ported BfA race: 25k-34k triangles) keeps
// the high 16 bits of each submesh's index start in the submesh's `level` field, and the client
// draws those submeshes in the right place. But CM2Model::SetGeometryVisible (0x0082C7C0), the one
// function every geoset show/hide goes through, matches submeshes by reading a full DWORD at the
// submesh's offset 0 - `(level << 16) | id` - so every submesh past the 16-bit line answers to
// 65536 + id, falls outside every range anyone asks for, and is drawn for the rest of the model's
// life: every cape length at once, death-knight eye glow on every class, all belt and robe pieces.
//
// What: after the client's own call, the same call is made again for each level the skin uses:
// [idStart + (level << 16), idEnd + (level << 16)]. The client's code does all the work (its
// deferred queue before the model is initialized, its draw-list rebuild after a change); nothing of
// it is reimplemented. Only skins with more than 65,535 indices are touched - on any other skin
// `level` keeps its legacy meaning (an LOD/sub-batch marker) and the client's behaviour is unchanged.

#define WXL_EXTENSION
#include <wxl/PluginApi.h>

#include <cstddef>
#include <cstdint>

namespace
{
    // --- client 12340 addresses and layouts (wxl-core src/offsets/game/M2.hpp, src/game/M2.hpp) ------
    constexpr uintptr_t kSetGeometryVisible = 0x0082C7C0; // __thiscall (idStart, idEnd, visible)
    constexpr size_t    kOffInstInitFlags   = 0x10;       // bit 0: initialized (else the call is queued and replayed)
    constexpr size_t    kOffInstShared      = 0x2C;       // -> shared model
    constexpr size_t    kOffModelSkin       = 0x170;      // shared model -> parsed skin profile
    constexpr size_t    kOffSkinIndexCount  = 0x0C;       // skin profile: uint32 index count
    constexpr size_t    kOffSkinSubCount    = 0x1C;       // skin profile: uint32 submesh count
    constexpr size_t    kOffSkinSubmeshes   = 0x20;       // skin profile: -> submeshes (stride 0x30)
    constexpr size_t    kSubmeshStride      = 0x30;
    constexpr size_t    kOffSubmeshLevel    = 0x02;       // uint16: high 16 bits of the index start
    constexpr uint32_t  kU16                = 0xFFFF;
    constexpr uint32_t  kMaxSubmeshes       = 0x4000;     // past this the skin is not one the client built

    using SetGeometryVisibleFn = void(__fastcall*)(void* instance, void* edx, uint32_t idStart, uint32_t idEnd,
                                                   uint32_t visible);

    SetGeometryVisibleFn g_next = nullptr;
    constexpr const char* kTag = "wide-skin-geosets";

    template <typename T>
    T Read(const void* base, size_t offset)
    {
        return *reinterpret_cast<const T*>(static_cast<const uint8_t*>(base) + offset);
    }

    /// The highest `level` any submesh of the instance's skin carries - 0 unless the skin really has
    /// more than 65,535 indices (only then is `level` an index-start high word).
    uint32_t HighestLevel(const void* instance)
    {
        const void* shared = Read<const void*>(instance, kOffInstShared);
        const void* skin = shared ? Read<const void*>(shared, kOffModelSkin) : nullptr;
        if (!skin || Read<uint32_t>(skin, kOffSkinIndexCount) <= kU16)
            return 0;
        const uint32_t count = Read<uint32_t>(skin, kOffSkinSubCount);
        const auto* submeshes = Read<const uint8_t*>(skin, kOffSkinSubmeshes);
        if (!submeshes || count > kMaxSubmeshes)
            return 0;
        uint32_t top = 0;
        for (uint32_t i = 0; i < count; ++i)
        {
            const uint32_t level = Read<uint16_t>(submeshes + i * kSubmeshStride, kOffSubmeshLevel);
            if (level > top) top = level;
        }
        return top;
    }

    void __fastcall SetGeometryVisibleDetour(void* instance, void* edx, uint32_t idStart, uint32_t idEnd,
                                             uint32_t visible)
    {
        if (!g_next) return;
        g_next(instance, edx, idStart, idEnd, visible);
        // not initialized yet: the client queued the call and replays it through here once it is
        if (!instance || idEnd > kU16 || idStart > idEnd || !(Read<uint32_t>(instance, kOffInstInitFlags) & 1))
            return;
        const uint32_t top = HighestLevel(instance);
        for (uint32_t level = 1; level <= top; ++level)
            g_next(instance, edx, idStart + (level << 16), idEnd + (level << 16), visible);
    }
}

extern "C"
{
    __declspec(dllexport) const WXL_PluginInfo* __cdecl WXL_Query(void)
    {
        static const WXL_PluginInfo info = {
            sizeof(WXL_PluginInfo), WXL_API_VERSION, kTag, 2, WXL_CLIENT_BUILD,
        };
        return &info;
    }

    __declspec(dllexport) int __cdecl WXL_Load(const WXL_Api* api)
    {
        if (!api || api->structSize < offsetof(WXL_Api, HookAttach) + sizeof(api->HookAttach) || !api->HookAttach)
            return 0;
        const int ok = api->HookAttach("CM2Model::SetGeometryVisible", kSetGeometryVisible,
                                       reinterpret_cast<void*>(&SetGeometryVisibleDetour),
                                       reinterpret_cast<void**>(&g_next), WXL_HOOK_DEFAULT_PRIORITY);
        // HookAttach's result is the verdict: WarcraftXL fills g_next in later, when it enables the
        // hook chain, so it is still null here
        if (!ok)
        {
            api->Log(WXL_LOG_ERROR, kTag, "could not detour CM2Model::SetGeometryVisible at 0x%08X", kSetGeometryVisible);
            return 0;
        }
        api->Log(WXL_LOG_INFO, kTag, "geosets past a skin's 65,535th index can be shown and hidden");
        return 1;
    }
}
