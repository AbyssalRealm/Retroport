# wxl-race-emote-sounds

A [WarcraftXL](https://github.com/WarcraftXL/wxl-core) extension (API v1, client build 12340) that
gives player races with id **22 and up** their text-emote sounds (/laugh, /cheer, /roar, ...).

## Why

The 3.3.5a client keeps text-emote sounds in a table it builds at startup with room for races
0-21, and both lookups refuse anything higher (`cmp race, 16h`):

| Function | Address | Used by |
|---|---|---|
| `SI2::GetTextEmoteSoundID(emote, race, sex)` | `0x004CEB30` | `CGUnit_C::PlayTextEmoteSound` - what you hear |
| `SI2::GetTextEmoteSoundIndex(emote, race, sex)` | `0x004CEB70` | `CGPlayer_C::SendTextEmote` - the recording variant sent along |

Every id below 22 belongs to an NPC race (CreatureDisplayInfoExtra uses all of them), so a new
playable race sits at 22+ and is silent whatever `EmotesTextSound.dbc` says. A scan of Wow.exe for
other `cmp reg, 16h` checks found no other race-limited lookup (vocal error sounds have no limit).

## What it does

Detours both functions. Races below 22 go straight to the client. For races 22+ the sound comes
from the `EmotesTextSound` rows the client already loaded (`g_emotesTextSoundDB`), and the variant
is chosen by the client's own code through a native race with the same sound (a race whose voices
were cloned from Human uses Human's). A sound no native race shares is sent as variant 0.

The race data itself (the `EmotesTextSound` rows for the new race) comes from `retroport race`.

## Build

Visual Studio 2022 + CMake, 32-bit; only `include/wxl/PluginApi.h` from wxl-core 1.1 is needed:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A Win32 -DWXL_CORE_DIR="I:/WoW Modding/wxl-core-1.1"
cmake --build build --config Release
build\Release\wxl-race-emote-sounds-test.exe build\Release\wxl-race-emote-sounds.dll
```

The test loads the real DLL in a 32-bit process, maps fake client memory at the addresses it reads
and drives its detours through a fake WarcraftXL API.

## Install

Copy `wxl-race-emote-sounds.dll` to `<WoW>\Extensions\wxl-race-emote-sounds\`. Remove the folder to
uninstall.

## License

GPL-3.0-or-later (it builds against WarcraftXL's GPL-3.0 headers).
