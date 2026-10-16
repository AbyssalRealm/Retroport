# wxl-fade-test

A [WarcraftXL](https://github.com/WarcraftXL/wxl-core) extension (API v1, client build 12340) with
settings for a character the camera fades when it is zoomed in close.

## Why

The client fades a unit's model as the camera comes within half its radius of it (`0x006FFCD0`: a
smoothstep of distance over radius into the instance's alpha factor), and the element build
(`0x00821C50`) sends every opaque or alpha-keyed batch of a model whose alpha is below 0.99999 to the
transparent pass. There the model is drawn far to near without its own depth holding its parts in
place: inner and far surfaces paint over near ones. A ported Kul Tiran's face gave way to the hair
behind it, and its helmet, shoulders and weapon turned inside out. Its camera at the two closest zoom
steps sits on the edge of that fade, so standing still, its idle animations flip it between 1.0
(drawn right) and 0.99 (drawn wrong) every few seconds.

| Setting | Address | |
|---|---|---|
| `FadeThreshold` | `0x00821F52` | below this alpha a batch goes see-through (the client's own: 0.99999, `fld dword ptr ds:[0x00A45528]`). At 0.9 a model the camera barely fades stays in the opaque pass, drawn whole; a real fade still goes see-through. The load's address operand is pointed at this extension's own value; 1 puts the client's back. Default 0.9. |
| `SolidFade` | `0x00821F66` | 1: faded models are never see-through (`jnp 0x00821F6B` -> `jmp`). Default 0. |
| `UploadBones` | `0x00820451` | 1: every batch uploads its own bone palette (the batch draw's palette-repeat skip `je 0x0082053F` -> `je +0`). Ruled out as the cause in game; kept for comparison. Default 0. |

The settings live in `wxl-fade-test.ini` beside the DLL, written with their defaults on first load
(and over an ini from before `FadeThreshold`), and read again within half a second of every save, so
no key is needed (F-keys don't always reach the WarcraftXL overlay). Each change is one byte or one
aligned dword, so it is safe while the game draws. A site whose instruction is not the client's own
is left alone and says so in `Logs\wxl-core.log`. The overlay (F9) panel **Fade test (retroport)**
shows and sets the same file.

## Use

Zoom in to where the character mis-draws. With `FadeThreshold=0.9` it should stay whole; to compare,
alt-tab to `Extensions\wxl-fade-test\wxl-fade-test.ini`, set `FadeThreshold=1`, save, and look again.
If it still mis-draws at 0.9, try 0.8, or `SolidFade=1`.

## Build

Visual Studio 2022 + CMake, 32-bit; only `include/wxl/PluginApi.h` from wxl-core 1.1 is needed:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A Win32 -DWXL_CORE_DIR="I:/WoW Modding/wxl-core-1.1"
cmake --build build --config Release
build\Release\wxl-fade-test-test.exe build\Release\wxl-fade-test.dll
```

The test maps the client's code page at its own address in a 32-bit process, loads the real DLL,
and checks the ini it writes (and over an older one), that saving it changes the threshold and
flips the switches while running, the overlay slider and checkboxes, and the refusal of unknown bytes.

## Install

Copy `wxl-fade-test.dll` to `<WoW>\Extensions\wxl-fade-test\`. Remove the folder to uninstall.

## License

GPL-3.0-or-later (it builds against WarcraftXL's GPL-3.0 headers).
