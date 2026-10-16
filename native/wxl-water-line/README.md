# wxl-water-line

A [WarcraftXL](https://github.com/WarcraftXL/wxl-core) extension (API v1, client build 12340) that
draws a model crossing a water surface whole, instead of cutting it with a clip plane at the water
line.

## Why

When a unit stands above liquid, the client gives it a horizontal plane at the liquid's surface and
tests each of its models' header bounding sphere against it. A model whose sphere straddles the
plane has its **transparent** batches drawn in both water passes with a user clip plane at the
surface:

| Address | |
|---|---|
| `0x007C10C0` | sets the plane (normal 0,0,1 at the liquid height) from the unit's liquid query |
| `0x00821CBF` | element build: header box centre and radius x scale against the plane |
| `0x00821DAF` | `test byte ptr [edx+4], 2` - split (both passes + clip plane) or draw on one side |
| `0x0082208F` | element flag 2 (clip plane), transparent batches only |
| `0x0081FD61` | the batch draw sets the clip plane for flag 2 |

The player model turns transparent at the closest camera zoom (the client starts fading it), so
standing near water and zoomed in, the clip plane applies - and in this client it cuts through the
model: a Kul Tiran's face, belt buckle and fingertips vanished and her shoulder pads opened onto
their own flames at certain camera angles, only at the two closest zoom levels. Opaque batches are
never split, which is why it never happens zoomed out. Models with large bounds - a ported race's,
shoulders with big particle effects (header radii near 10) - straddle water they are nowhere near.

## What it does

Clears the immediate of the test at `0x00821DAF` (`02` -> `00`), so a straddling model is never
split; the client's own other branch draws it whole on one side of the water, as it already does
for every opaque batch. The bytes are checked first: anything other than the client's own
instruction is left alone and the extension declines to load.

The WarcraftXL overlay (F9) gets a **Water line (retroport)** panel whose checkbox puts the
client's split back, for comparing the two live.

## Build

Visual Studio 2022 + CMake, 32-bit; only `include/wxl/PluginApi.h` from wxl-core 1.1 is needed:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A Win32 -DWXL_CORE_DIR="I:/WoW Modding/wxl-core-1.1"
cmake --build build --config Release
build\Release\wxl-water-line-test.exe build\Release\wxl-water-line.dll
```

The test maps the client's code page at its own address in a 32-bit process, loads the real DLL,
and checks the patch, the overlay toggle and the refusal of unknown bytes.

## Install

Copy `wxl-water-line.dll` to `<WoW>\Extensions\wxl-water-line\`. Remove the folder to uninstall.

## License

GPL-3.0-or-later (it builds against WarcraftXL's GPL-3.0 headers).
