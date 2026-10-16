# Retroport Workbench

Browse every retail World of Warcraft item and transmog set in 3D, pick the ones you want, and
backport them to your **3.3.5a (Wrath of the Lich King, 12340)** client and your **AzerothCore**
server - in one click, with a backup of everything it changes and an Undo.

## What it does

- **Browse** all of retail's items (about 99,000) and transmog sets (about 5,000). Search by name or
  id; filter by slot, armour or weapon type, expansion, quality and class. A set comes as one card
  with all its versions (Normal, Heroic, Mythic, a PvP season's Gladiator and Elite), and items your
  client already has are marked.
- **See it in 3D** before you port it, animated, with its glows and moving textures: an item on its
  own, a set dressed on a character of any race your client plays (male or female), weapons and
  other one-kind sets one at a time with ◀ ▶. Every version is a click away under the preview.
- **Collect** what you like in the **Basket**. It shows exactly what will be built - every look each
  entry brings, entries that overlap, and what's new or removed since your last install - so a
  mistake is easy to spot and remove (with Undo).
- **Install** with one button. It converts the basket for your client, installs the patch, adds the
  new items to your server's `Item.dbc` and `ItemDisplayInfo.dbc`, backs up everything it replaces,
  and can undo the whole install. It never touches your database: it shows the item SQL for you to
  apply.

## Download and run

1. Download `Retroport-Workbench-<version>-win64.zip` from the [Releases](../../releases) page.
2. Unpack the **whole** folder anywhere. Keep `runtime\` and `app\` next to the exe.
3. Run **Retroport Workbench.exe**. It opens in a window of its own; closing the window closes it.
   Windows may warn that the program is from an unknown publisher (it isn't code-signed): choose
   *More info*, then *Run anyway*.
4. The first time, **Settings** opens. Choose your 3.3.5a client folder (the one with `Wow.exe`), and
   optionally your server's dbc folder. That's all the setup there is.
5. Find items and sets, add them with **+**, look them over in **Basket**, then **Backport >
   Install**. When it's done: apply the item SQL it shows to your world database, restart the
   worldserver, and log in.

## What you need

- **Windows 10 or 11.** The app's window is Microsoft Edge's app mode (Edge comes with Windows).
- **The internet.** Retail's items, models and textures come from [wago.tools](https://wago.tools).
  The first Install also downloads the two programs that do the conversion from their authors'
  GitHub pages (see [Credits](#credits)).
- **A 3.3.5a client.** A stock client shows ported weapons, helms, shoulders and the armour drawn on
  the body. For the 3D pieces modern armour adds to chests, belts, legs, boots, gloves and cloaks,
  install [WarcraftXL](https://github.com/WarcraftXL/wxl-core) and
  [Equip Plus](https://github.com/TheDaveCalaz/wxl-equip-plus) in your client.
- **Optionally, an AzerothCore server** whose dbc folder you point it at, so the new items exist on
  the server too.

## Settings

- **3.3.5a client folder** and **server dbc folder** - each *Browse...* opens Windows' folder picker,
  and each field says what it found there.
- **Where the items come from** - *wago.tools* (the default; nothing to install) or *your own retail
  install* (faster, and works while wago.tools is down; it needs the
  [WoWDBDefs](https://github.com/wowdev/WoWDBDefs) definitions folder and the
  [community listfile](https://github.com/wowdev/wow-listfile)). The bodies the character view
  dresses come from BfA 8.3.7, the same way.
- **Backport kit** - the project folder the conversion works in, and where its programs live (they
  are fetched for you; point them elsewhere only to use your own copies).

Everything the app keeps is in your user folder:

| | |
|---|---|
| `%USERPROFILE%\.retroport\workbench.json` | your settings |
| `%USERPROFILE%\.retroport\workbench\` | the cache, your basket, the conversion project, the log (`workbench.log`) |

Everything it downloads is kept, so what you've already looked at still opens while wago.tools is
down.

## How Install works

1. The first time, it downloads the conversion kit and Equip Plus's tools.
2. It converts the basket - only what changed since the last build. Progress is ticked off step by
   step, and a step that fails stops it before anything is installed (the log says why).
3. It installs: the patch and Equip Plus's pieces into your client's `Data` folder, the new rows into
   your server's DBCs, and clears the client's cache. Everything it replaces is backed up first.
4. You apply the item SQL it shows (a `SOURCE` line with a copy button) to your world database, and
   restart the worldserver.

**Undo last install** puts back everything that install replaced and removes what it added. The
basket remembers what's installed, so the next Install adds what's new and drops what you removed.

## Troubleshooting

- **The window doesn't appear.** Look at `%USERPROFILE%\.retroport\workbench\workbench.log`. Without
  Edge, the app opens in your default browser instead.
- **"Port 8335 is taken"** - another program uses the port the app listens on (only on your own
  machine, `127.0.0.1`). Close it and start the app again.
- **The first look at things is slow** - models and textures are being downloaded; they're kept
  for next time.
- **wago.tools is down** - Settings > *Where the items come from* > *My own install*.
- **Install stopped** - open the log under the Install button; nothing was installed by that run.
  Close World of Warcraft before installing: it holds the files Install replaces.
- **No 3D preview** - the preview needs WebGL 2; update your graphics driver.

## Building from source

Python 3.11 or newer (3.14 is what it's tested with):

```
git clone https://github.com/kbtoy/Retroport
cd Retroport
python -m pip install -e ".[dev]"
python -m pytest -q tests

python -m retroport.cli workbench                      # run it in a console (opens your browser)
python packaging/build_release.py --launcher-only      # or: Retroport Workbench.exe next to the code
python packaging/build_release.py                      # the release folder and zip, in dist\
```

A release bundles Python's official embeddable package, the dependencies and the code, and checks
that it starts before it's zipped. Pushing a version tag (`v0.2.0`) builds it on GitHub and attaches
the zip to that release.

The Workbench's code is `retroport/workbench/` (the page in `static/`), its game-file readers are
`wotlkconv/` (an unchanged copy of the Converter, see below), and how it all works is in
[docs/workbench-internals.md](docs/workbench-internals.md). The rest of `retroport/` is shared code and
older command-line tools ([docs/command-line-tools.md](docs/command-line-tools.md)).

## Credits

Retroport Workbench is a front end to other people's work:

- **[wow-retail-backport](https://github.com/Kirazul/wow-retail-backport)** by **Kirazul** - the kit
  that converts retail items for 3.3.5a: everything Install builds.
- **[Equip Plus](https://github.com/TheDaveCalaz/wxl-equip-plus)** by **TheDaveCalaz** - the 3D armour
  pieces, and the tools that prepare them and merge the server's DBCs.
- **[WarcraftXL](https://github.com/WarcraftXL/wxl-core)** - the client framework Equip Plus runs on.
- **[wotlkconv (Converter)](https://github.com/Bar3b0n3s/Converter)** by the Converter contributors -
  the readers for the game's files.
- **[wago.tools](https://wago.tools)** - the game data; **[wowdev](https://wowdev.wiki)** -
  WoWDBDefs, the community listfile and the file-format documentation;
  **[WoW Blender Studio](https://gitlab.com/skarnproject/blender-wow-studio)** by **Skarn** - the
  shader tables the 3D preview follows; **[Conjure](https://github.com/TheDaveCalaz/Conjure)** by
  **TheDaveCalaz** - code retroport adapts.

The kit and Equip Plus are downloaded from their authors on first use and never bundled. The full
list, with every bundled library and its license, is [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

MIT - see [LICENSE](LICENSE): use it, change it, share it, sell it, as you like. A few files in this
repository are under other terms; [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) says which.

Retroport Workbench is a fan project, not affiliated with or endorsed by Blizzard Entertainment. It
ships no Blizzard art, models or game data: they're read at run time from wago.tools or your own
installs, for your own use. World of Warcraft and its assets are trademarks and copyright of
Blizzard Entertainment.
