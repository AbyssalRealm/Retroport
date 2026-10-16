# Retroport Workbench - internals

A developer's reference: how the Workbench reads its data, builds its catalog, draws its previews
and drives the backport kit. For using it, see the [README](../README.md).

## How it's built

An application for anyone with a 3.3.5a client: `Retroport Workbench.exe` opens it in a window of
its own (Edge's app mode - no console, no browser tab; the server behind it listens on
`http://127.0.0.1:8335/` only). Closing the window closes it - after an Install that's running
has finished; started again while it runs, it opens another window on it. Its log is
`%USERPROFILE%\.retroport\workbench\workbench.log`.

- **Game data from wago.tools by default** - nothing to install: retail's tables (as wago's CSV)
  and its files by FileDataID at the newest live build, and BfA 8.3.7's for the bodies the
  character view dresses (`retroport/workbench/wago.py`; checked row-for-row and byte-for-byte
  against local installs at 12.1.5.70391 and 8.3.7.35435 - wago's tables also carry hotfixes).
  Everything fetched is kept on disk, so it opens again while wago is down.
- **Or your own installs** (Settings): a retail and a BfA install read directly - faster, and
  independent of wago; they need WoWDBDefs' definitions and the community listfile.
- **Settings** (the first run opens it): the 3.3.5a client folder, the server's dbc folder, where
  the items come from, the kit's project. Each Browse button opens Windows' own folder picker,
  and each field says what it found there.
- **The kit and Equip Plus are fetched, never bundled**: the first Install downloads the retail
  backport kit (it carries no license, so it can't be passed on) and Equip Plus's tools from their
  authors' GitHub at the commits this workbench is checked with (`retroport/workbench/deps.py`).
  The kit is always run through `kitlocal`, which shows it WarcraftXL's open-folder patches (as
  Equip Plus installs its pieces) when its own client reader doesn't know them - in that process
  only, its files untouched.

From a checkout: `python packaging/build_release.py --launcher-only` builds `Retroport Workbench.exe`
next to the code (it runs the `pythonw` on PATH), or run `retroport workbench` / `Workbench.bat` in a
console - with `--bfa` / `--retail <install> --dbd ... --listfile ...` to read local installs:

```
retroport workbench --bfa "<BfA install>" --retail "<retail install>" --client "<3.3.5 client>" ^
    --dbd "<WoWDBDefs\definitions>" --listfile "<listfile.csv>"
```

Options are remembered in `%USERPROFILE%\.retroport\workbench.json`. **A release** -
`python packaging/build_release.py` - is a folder and its zip: the launcher (built with the C#
compiler every Windows has), Python's official embeddable package with retroport's dependencies
and numpy, and the code (retroport, wotlkconv); it imports and serves the page with its own Python
before it's zipped. It holds nothing of Blizzard's, the kit's or Equip Plus's.

Each source's catalog is built from its own tables once per build (seconds; then under a second
from the cache) - every item with a look and a search name (89,490 in BfA 8.3.7, 99,076 in retail
12.1.5), its appearances and display ids, the transmog sets as families of versions
(Normal/Heroic/Mythic, a PvP set's seasons) and the classic item sets. A partial local retail
install fetches what it doesn't store from Blizzard's CDN, verified and kept in the cache.

- **Items**: search by name or by item, appearance or display id; filter by slot, armour or weapon
  type, expansion, quality, class; *one per look* folds items that share an appearance. Items your
  3.3.5 client already has (same id) are marked; Blizzard's test items are hidden unless asked.
- **Sets & collections**: one card per family with its pieces (a class set names its class: one PvP
  name covers a Hunter's, a Shaman's and an Evoker's looks); the detail lists every version and
  piece, one per look - items that look the same (a Helm and a Faceguard for two armour types) under
  the one the set marks primary, "+1 same look". A set that is only a fragment of another of the
  same name - no version label or group, every look of it in the other (retail sells a PvP season's
  Elite looks two slots at a time as their own sets) - is folded into that one (717 in retail
  12.1.5; still found by id). Ids are the game's own - the ones the retail backport kit's
  `select --set/--item` takes.
- **Basket**: kept in the workbench folder; what the next Install builds, to look over first. Each
  entry shows the looks it brings as the kit selects them - one per model: a set's pieces, every
  version of an item (an item set: of each of its items) - and opens in the drawer from its name or
  any look; one whose looks all come with another entry already says so, one that shares some
  says with which. Against the install in your client (the basket it was made from is recorded in
  its `install.json`) each entry is *new* or *installed*, and what was removed since is listed with
  *put back*. × removes (with Undo); Empty the basket empties it.
- **Preview** (in an item's panel, at the top, its versions under it beside the race and sex -
  Normal / Heroic / Mythic, a PvP piece's Gladiator / Elite, named by the set that uses them, one
  per look - each a click away; a set's versions sit there too): its models
  in 3D (WebGL2: left-drag turns, right-drag moves, the wheel zooms, double-click resets), playing
  their Stand animation - bones, scrolling and turning textures, colour and transparency tracks,
  particle emitters - for a race and sex of the ones your 3.3.5 client plays (helms and shoulders
  differ; a race without its own model wears ChrRaces' fallback race's: a Nightborne the Night
  Elf's; heritage armour made for one race shows as theirs, and says so). A piece with no model -
  a cloth cloak (ModelType 5: its texture on the body's cape), armour drawn on the body - shows on
  a character wearing only it, a cloak from behind. Each batch is drawn with the client's
  own combiner for its shader id (the table for 0x8000 ids, the bits for older ones): how its up
  to three textures mix - a glow added over an environment map, a pattern moving under a mask -
  and whether each reads the first UVs, the second or the environment. Shoulders sit as worn; a
  held item stands up facing you: weapons and shields are modelled from the grip along +X, so +X
  goes up (an axe's head, a staff's crown, a shield's top) and you face the flat side (a shield's
  front is +Y; one modelled leaning, as it sits on the forearm, has its long axis stood up); guns,
  bows and crossbows stay level. Armour drawn on the body shows its section textures. Icons and
  textures a source can't open (BfA 8.3.7 keeps its last sets' art encrypted) come from the other
  source.
- **A set of one kind** - weapons, masks, cloaks, shoulders - shows its pieces one at a time in that
  preview, ◀ ▶ (or the arrow keys) in the row under it to step through, the piece shown marked in
  the list.
- **On a character** (in any other set's panel, above its pieces): the set's first piece of each slot on
  BfA's body of the race and sex - `<race><sex>_hd.m2` for the classic races (the plain one is the
  pre-WoD model on the 512x512 layout), Kul Tiran and Pandaren their own - animated. Its skin is
  composited as the client does (CharSections skin, face, scalp, underwear on the race's layout -
  the HD rows, BaseSection 5-9, where the race has them - then each piece's section textures in
  dressing order); geosets from the race's hair and facial
  hair, then each piece's GeosetGroup values (a value the BfA body has no geoset for - retail has
  more boot shapes - takes the nearest below; retail's boots say -1, no feet, where its newer
  bodies' boots cover them, and BfA's body keeps its feet for the boot's foot texture, as BfA's own
  data has those boots), what a helm hides (HelmetGeosetData). ModelType 0
  models hang on body points (helm 11, shoulders 6/5, buckle 53...); ModelType 1 *collections* are
  skinned to the body's bones by bone name hash - one collection is shared by many items, and each
  shows only the parts of the body groups its slot controls (at 1 + its AttachmentGeosetGroup
  value). A piece opens on its own with a Back button.
- **Backport**: one **Install** puts the basket into the client and the server - it fetches the kit
  and Equip Plus the first time, creates the kit's project if there's none, has the kit `select` the
  basket when it changed since the last selection
  (which un-ticks every later step in the kit's ledger; an unchanged basket isn't selected or built
  again), runs every step that hasn't passed (scan when needed, with the kit's own patch out of Data
  meanwhile, then fetch ... sheet), then installs; its progress is ticked off step by step, the log
  is folded below, and a step that fails stops it before anything is installed. The folders are in
  Settings; the kit's own steps - *redo from here*, *Build only* to look at the preview pictures
  before installing - are folded away. Install / Undo work the Backport
  Helper's way (backups under
  `<project>\backups\<time>\install.json`, the helper's `last_backup` updated, so either tool undoes
  the other's): Equip Plus's 3D-piece patch, the kit's patches into Data, the client's Cache cleared,
  the server's Item/ItemDisplayInfo.dbc merged when a server dbc folder is set. The item SQL is never
  run - its `SOURCE` line is shown to apply yourself.

**Retail from your own install.** With `--retail`, a kit project pinned to that install's build
reads retail locally: `retroport/workbench/kitlocal.py` runs the kit unchanged and answers its
downloads (`wago._get`) from the install - tables as wago's CSV (checked text-identical to wago's
on ChrRaces, ParticleColor, ItemNameDescription, HelmetGeosetData at 12.1.5.70391), files by
FileDataID (byte-identical). What the install can't give - another build, a table its listfile
doesn't name, an encrypted file, the community listfile - still goes to wago.tools; each run
ends with a count of what came from where. wago's tables also carry Blizzard's hotfixes, which an
install's own tables don't (2 of ParticleColor's 2,122 rows at 12.1.5).
