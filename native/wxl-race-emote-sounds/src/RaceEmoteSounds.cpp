// wxl-race-emote-sounds: text-emote sounds (/laugh, /cheer, ...) for player races with id 22 and up.
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
// Why: the 3.3.5a client (build 12340) answers "which sound does this race/sex make for this text
// emote" from a table it builds at startup with room for races 0-21 only. Both lookups refuse
// anything higher outright:
//
//   SI2::GetTextEmoteSoundID(emote, race, sex)     0x004CEB30   cmp race, 16h / jae -> 0
//   SI2::GetTextEmoteSoundIndex(emote, race, sex)  0x004CEB70   cmp race, 16h / jae -> -1
//
// The first is what CGUnit_C::PlayTextEmoteSound asks when a unit emotes; the second is what
// CGPlayer_C::SendTextEmote asks to pick the recording variant it sends along, so every client
// plays the same one. A race added at id 22+ (all lower ids are taken by NPC races) is silent,
// whatever EmotesTextSound.dbc says.
//
// What: both functions are detoured. Races below 22 go straight to the client. For races 22+ the
// sound comes from the EmotesTextSound rows the client already loaded (g_emotesTextSoundDB), and
// the variant is picked by the client's own code through a native race that uses the same sound -
// a race cloned from Human voices, for example - so nothing of the client's sound logic is
// reimplemented. A sound no native race shares is sent with variant 0.

#define WXL_EXTENSION
#include <wxl/PluginApi.h>

#include <cstddef>
#include <cstdint>
#include <unordered_map>

namespace
{
    // --- client 12340 addresses ------------------------------------------------------------------
    constexpr uintptr_t kGetTextEmoteSoundID    = 0x004CEB30; // uint32 __cdecl (emote, race, sex)
    constexpr uintptr_t kGetTextEmoteSoundIndex = 0x004CEB70; // int32  __cdecl (emote, race, sex)
    constexpr uintptr_t kEmotesTextSoundCount   = 0x00AD37B4; // g_emotesTextSoundDB + 0x08: record count
    constexpr uintptr_t kEmotesTextSoundRecords = 0x00AD37C8; // g_emotesTextSoundDB + 0x1C: record array
    constexpr uint32_t  kNativeRaceLimit        = 22;         // the client's table holds races 0-21

    /// One EmotesTextSound.dbc row as the client keeps it in memory (5 x uint32, stride 0x14).
    struct EmotesTextSoundRec
    {
        uint32_t id;
        uint32_t emotesTextId;
        uint32_t raceId;
        uint32_t sexId;
        uint32_t soundId;
    };
    static_assert(sizeof(EmotesTextSoundRec) == 0x14, "EmotesTextSound record stride is 0x14 in 12340");

    using SoundIdFn    = uint32_t(__cdecl*)(uint32_t emote, uint32_t race, uint32_t sex);
    using SoundIndexFn = int32_t(__cdecl*)(uint32_t emote, uint32_t race, uint32_t sex);

    const WXL_Api* g_api = nullptr;
    SoundIdFn      g_nextSoundId = nullptr;    // next link of the detour chain (ends at the client)
    SoundIndexFn   g_nextSoundIndex = nullptr;

    std::unordered_map<uint64_t, uint32_t> g_sounds; // (emote, race, sex) -> SoundEntries id, races 22+
    bool g_built = false;

    constexpr const char* kTag = "race-emote-sounds";

    uint64_t Key(uint32_t emote, uint32_t race, uint32_t sex)
    {
        return (static_cast<uint64_t>(emote) << 32) | (static_cast<uint64_t>(race) << 1) | (sex & 1u);
    }

    /// Indexes the loaded EmotesTextSound rows of races 22+. Runs once, on the first emote: the
    /// client loads its tables at startup, long before anyone can emote.
    void Build()
    {
        g_built = true;
        const uint32_t count = *reinterpret_cast<const uint32_t*>(kEmotesTextSoundCount);
        const auto* rows = *reinterpret_cast<const EmotesTextSoundRec* const*>(kEmotesTextSoundRecords);
        if (!rows || count == 0 || count > 1000000)
        {
            if (g_api) g_api->Log(WXL_LOG_WARN, kTag, "EmotesTextSound is not loaded (count %u); races 22+ stay silent", count);
            return;
        }
        for (uint32_t i = 0; i < count; ++i)
        {
            const EmotesTextSoundRec& r = rows[i];
            if (r.raceId >= kNativeRaceLimit && r.sexId < 2 && r.soundId != 0)
                g_sounds[Key(r.emotesTextId, r.raceId, r.sexId)] = r.soundId;
        }
        if (g_api)
            g_api->Log(WXL_LOG_INFO, kTag, "%u emote sound(s) for races %u+ indexed from %u EmotesTextSound rows",
                       static_cast<uint32_t>(g_sounds.size()), kNativeRaceLimit, count);
    }

    uint32_t Lookup(uint32_t emote, uint32_t race, uint32_t sex)
    {
        if (!g_built) Build();
        const auto it = g_sounds.find(Key(emote, race, sex));
        return it == g_sounds.end() ? 0u : it->second;
    }

    uint32_t __cdecl GetTextEmoteSoundIDDetour(uint32_t emote, uint32_t race, uint32_t sex)
    {
        if (race < kNativeRaceLimit) return g_nextSoundId(emote, race, sex);
        return sex < 2 ? Lookup(emote, race, sex) : 0u;
    }

    int32_t __cdecl GetTextEmoteSoundIndexDetour(uint32_t emote, uint32_t race, uint32_t sex)
    {
        if (race < kNativeRaceLimit) return g_nextSoundIndex(emote, race, sex);
        const uint32_t sound = sex < 2 ? Lookup(emote, race, sex) : 0u;
        if (sound == 0) return -1;                       // the client's own "no sound" answer
        // The variant is the client's choice for that sound: ask it through a native race that
        // makes the same sound for this emote and sex.
        for (uint32_t standIn = 1; standIn < kNativeRaceLimit; ++standIn)
            if (g_nextSoundId(emote, standIn, sex) == sound)
                return g_nextSoundIndex(emote, standIn, sex);
        return 0;
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
        if (!api || api->structSize < offsetof(WXL_Api, HookAttach) + sizeof(api->HookAttach) || !api->HookAttach)
            return 0;
        g_api = api;
        const int idOk = api->HookAttach("SI2::GetTextEmoteSoundID", kGetTextEmoteSoundID,
                                         reinterpret_cast<void*>(&GetTextEmoteSoundIDDetour),
                                         reinterpret_cast<void**>(&g_nextSoundId), WXL_HOOK_DEFAULT_PRIORITY);
        if (!idOk || !g_nextSoundId)
        {
            api->Log(WXL_LOG_ERROR, kTag, "could not detour SI2::GetTextEmoteSoundID at 0x%08X", kGetTextEmoteSoundID);
            return 0;
        }
        const int indexOk = api->HookAttach("SI2::GetTextEmoteSoundIndex", kGetTextEmoteSoundIndex,
                                            reinterpret_cast<void*>(&GetTextEmoteSoundIndexDetour),
                                            reinterpret_cast<void**>(&g_nextSoundIndex), WXL_HOOK_DEFAULT_PRIORITY);
        if (!indexOk || !g_nextSoundIndex)
            api->Log(WXL_LOG_WARN, kTag, "could not detour SI2::GetTextEmoteSoundIndex at 0x%08X: races 22+ hear "
                                         "emotes but send no variant", kGetTextEmoteSoundIndex);
        api->Log(WXL_LOG_INFO, kTag, "text-emote sounds enabled for races %u and up", kNativeRaceLimit);
        return 1;
    }
}
