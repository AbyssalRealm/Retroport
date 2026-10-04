# wxl-wide-skin-geosets

A [WarcraftXL](https://github.com/WarcraftXL/wxl-core) extension (API v1, client build 12340) that
lets the client show and hide geosets stored past a skin's 65,535th index.

## Why

A ported modern character model (a BfA Kul Tiran is 25k-34k triangles) has more than 65,535 skin
indices. Its `.skin` keeps the high 16 bits of each submesh's index start in the submesh's `level`
field, and the client draws those submeshes in the right place. But the one function every geoset
show/hide goes through matches submeshes by reading a full DWORD at the submesh's offset 0:

| Function | Address | |
|---|---|---|
| `CM2Model::SetGeometryVisible(idStart, idEnd, visible)` | `0x0082C7C0` | `__thiscall`; compares `(level << 16) \| id` against the range |

So every submesh past the 16-bit line answers to `65536 + id`, falls outside every range the
character code asks for, and stays drawn: every cape length at once (a white, untextured cape
with no cloak equipped), death-knight eye glow on every class, the belt band and sash, robe and
tabard pieces. WarcraftXL's own `src/offsets/game/M2.hpp` documents the same behaviour.

## What it does

Detours `SetGeometryVisible`. After the client's own call, the same call is made again for each
`level` the skin uses, with the range shifted: `[idStart + (level << 16), idEnd + (level << 16)]`.
The client's code does everything else - queuing the call while the model loads (the queue replays
through this detour), and rebuilding the draw list after a change.

Only skins with more than 65,535 indices are affected: on any other skin `level` keeps its legacy
meaning (an LOD/sub-batch marker) and nothing changes.

`retroport race` also orders a character's skin so its switchable geosets come first (below the
line) when they fit: the Kul Tiran male then needs no extension, the female (85,947 switchable
indices) still does.

## Build

Visual Studio 2022 + CMake, 32-bit; only `include/wxl/PluginApi.h` from wxl-core 1.1 is needed:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A Win32 -DWXL_CORE_DIR="I:/WoW Modding/wxl-core-1.1"
cmake --build build --config Release
build\Release\wxl-wide-skin-geosets-test.exe build\Release\wxl-wide-skin-geosets.dll
```

The test loads the real DLL in a 32-bit process and drives its detour against a fake client
`SetGeometryVisible` that matches on the full DWORD and queues calls before initialization, like
the real one.

## Install

Copy `wxl-wide-skin-geosets.dll` to `<WoW>\Extensions\wxl-wide-skin-geosets\`. Remove the folder
to uninstall.

## License

GPL-3.0-or-later (it builds against WarcraftXL's GPL-3.0 headers).
