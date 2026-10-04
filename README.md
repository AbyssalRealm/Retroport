# retroport

Automate porting modern WoW character/creature models (Shadowlands → The War Within)
down to the 3.3.5a (WotLK) client.

## What this actually is

**retroport is a conductor, not a converter.** The MD21 → MD20 format work is done by a
conversion engine — by default **[wotlkconv](https://github.com/Bar3b0n3s/Converter)**
(MIT, pure Python, used as a library), or the MultiConverter exe. retroport runs what
neither does — WotLK render-blocker fixes, texture baking and resizing, diagnostics,
DBC registration — and drives M2Mod + Blender for poly reduction.

Division of labor:

| Job | Who does it |
|-----|-------------|
| **MD21 → MD20, .skel/.anim merge, TXID resolution, `.skin`/`.anim` conversion + 3.3.5 names** | **wotlkconv** (default engine) |
| Fold `.skel` (bones) + `.anim` (animations) into the `.m2`, drop SKID | **retroport** (re-inline, MultiConverter engine) |
| Copy/rename `.skin` files to `<model>NN.skin` next to the `.m2` | **retroport** (stage, MultiConverter engine) |
| Restore the data MultiConverter overwrites with the combiner-combos header | **retroport** (fixup) |
| Clear a combiner table WotLK can't use + reset batch shaders (black-mesh fix) | **retroport** (fixup) |
| Clamp submeshes to 64, set nViews, clear stale anim flags | **retroport** (fixup) |
| Bake in-game paths into type-0 texture slots MultiConverter left empty | **retroport** (texbake) |
| Diagnose triangles / submeshes / textures / bones / skins / LODs / `.anim` files | **retroport** (check) |
| Write CreatureModelData + CreatureDisplayInfo rows, repoint SQL | **retroport** (register) |
| Write ItemDisplayInfo rows (weapons/armor), repoint Item.dbc, item SQL + pack paths | **retroport** (register-item) |
| Convert every 3.3.5 race/gender variant of a helm (+ `-fixhelm` for classic models) | **retroport** (helms) |
| Resize armor textures to their body region, shrink/re-encode object textures (BLP2) | **retroport** (textures) |
| **MD21 → MD20, version → 264, TXID/SFID texture resolution** (alternative engine) | **MultiConverter** (external exe) |
| **`.m2` ↔ `.m2i` + mesh decimation** | **M2Mod** (external exe) **+ Blender** |

Two things retroport does **not** do, despite common assumption: it does not *reduce*
bone count (it only flags it), and it does not fold skins into the `.m2` (only the
skeleton and animations are inlined — skins stay as separate `.skin` files that the
client loads alongside the model, exactly as in modern WoW).

## The pipeline

```
wotlkconv engine:       triage -> [poly-reduce] -> convert (wotlkconv) -> fixup -> texbake -> check
MultiConverter engine:  triage -> [poly-reduce] -> stage skins -> re-inline -> MultiConverter -> fixup -> texbake -> check
```

**Engines.** `PipelineConfig.engine` / GUI Settings → *Conversion engine*:

- **`wotlkconv`** (default) converts the model *and* its companions in one step: merges
  the `.skel` (parent chains too) and its `.anim` data, flattens chunked external `.anim`
  files, rewrites modern `.skin` headers (dropping shadow batches), drops unused LOD
  geometry, resolves TXID textures, and writes everything under the names 3.3.5 globs
  for. Its full listfile load costs ~19 s / 400 MB, so retroport hands it a *scoped*
  listfile: the FileDataIDs the model and its skeleton chain reference, streamed out of
  your listfile in one pass. Its per-file loss notes (`[lossy] m2.texture.type ...`) go
  into the log. Unresolved textures come out as `unknown\<fdid>.blp`; texbake and check
  treat those as unresolved, so `data/overrides.csv` can still fix them.
- **`multiconverter`** — the exe route below (stage + re-inline + MultiConverter). Still
  needed for one thing: MultiConverter's `-fixhelm` refit of helms for *classic* models.

1. **triage** — classify the model (M2 vs M3, version, uses `.skel`).
2. **poly-reduce** *(optional; needs M2Mod + Blender)* — M2Mod exports `.m2`->`.m2i`,
   Blender decimates the `.m2i`, M2Mod compiles it back. This is the standard backport
   route (Blender writes `.m2i`, not a WotLK `.m2` binary, so M2Mod does the compile).
   Runs *before* conversion, on the modern model. M2Mod rejects a reduction that's still
   over the WotLK vertex/index caps, so it fails loudly rather than shipping a broken model.
3. **stage** *(MultiConverter engine)* — copy the model's `.skin` files into the output
   folder as `<model>NN.skin`.
4. **re-inline** *(MultiConverter engine)* — fold `.skel` + `.anim` into a self-contained
   `.m2`, drop SKID (skipped automatically for models with no `.skel`).
5. **convert** — wotlkconv, or MultiConverter. Either way retroport verifies the result
   really became MD20 (`post-convert: chunked=False version=264`).
6. **fixup** — combiner header repair, combiner-flag/shader fix, submesh clamp, nViews,
   stale-flag cleanup.
   - *Combiner header repair:* when MultiConverter adds texture-combiner combos to a model
     that had none, it sets global flag 0x08 and writes the combos field at 0x130 without
     moving the data already there — the first array after the header: on a real 2H axe and
     on hozumonkey the global-loop timestamps, and on a model without global loops the
     sequence table, so the first animation (Stand) loses its id and duration. Verified by
     running MultiConverter on both: flag 0x08 newly set, combos `[1, 4, 1, 1]` / `[1, 4]`
     written over the loops, and the repair restores them byte for byte. retroport keeps the model as MultiConverter received
     it and restores that data from it. `retroport repair` does the same for models converted
     before this fix.
   - *Combiner flag:* a valid table is kept (Blizzard's own 3.3.5 models use one in about one
     model in eight, most often `[1, 4]`: a texture plus an env-mapped reflection). Only a
     table WotLK can't use — empty, out of bounds, values above 7, or batch shaders indexing
     past it — is cleared, with batch shaders reset to 0.
7. **texbake** — the modern model names textures by FileDataID (TXID chunk); MultiConverter
   resolves the ones its listfile knows. For any type-0 slot still empty, retroport reads
   the TXID captured *before* conversion, looks the id up in your listfile (+
   `data/overrides.csv`), and bakes the in-game path into the MD20. Explicit per-slot
   paths (`PipelineConfig.texture_paths` / `retroport bake --slot`) win. Paths that look
   like a location on your disk are refused.
8. **check** — WotLK render-blocker report, including (for converted MD20 models) the
   multi-LOD gate and the external `.anim` check.

### Safety rules (adapted from Conjure)

- The input model's folder is never written to: the pipeline refuses an output folder
  that is the model's own folder, and `register` refuses one equal to the DBC folder.
- Every library edit (fixup, texbake, DBC writes) goes to a temp file, is re-read and
  checked for structural problems the input didn't already have, and only then replaces
  the target. A rejected edit leaves the file untouched and is logged as `REJECTED ...`.
- The converted model + skins are copied to `<out>/_backup/` before fixup/texbake edit them.

## External tools you provide (in GUI Settings)

- **MultiConverter** — the *console* build (e.g. `MultiConverter_Console.exe`); the GUI
  build ignores command-line arguments.
- **M2Mod** — the CLI build (`--m2-to-m2i`, `--m2i-to-m2`, `--txid-remove`). Needs a
  `--mappings-dir` (a folder with the converted listfile) and, for models that share a
  skeleton (a `.skel` with a parent), a `--working-dir` pointed at your extraction root
  so it can resolve the parent `.skel` by its in-game path.
- **Blender + a `.m2i` addon** — WoWTools (the M2Mod Blender plugin, Blender 2.79). The
  decimate script drives its `import.m2i` / `export.m2i` operators.
- **Extraction** is done by **wow.export** (writes the `.manifest.json` retroport reads).

## Install & run

```bash
pip install -e .            # library + `retroport` CLI (pydantic, typer, rich, Pillow)
pip install -e "I:\WoW Modding\Converter"   # the wotlkconv engine (or: pip install -e ".[engine]")
pip install -e ".[gui]"     # + PyQt6 desktop app
pip install -e ".[dev]"     # + pytest

retroport-gui               # the desktop app (Convert / Register / Settings tabs)
```

CLI:

```bash
retroport scan  I:\extract --report          # inventory + per-file blockers; --skel-only lists .skel models
retroport inspect  I:\extract\...\foo.m2      # one file, full detail
retroport deps  I:\extract\...\foo.m2         # a model's .skel/.anim deps and whether they're present
retroport reinline  ...\foo.m2 --out I:\conv --json
retroport check  I:\conv\foo.m2               # WotLK render-blocker diagnostics on a converted model
retroport bake  I:\conv\foo.m2 --slot 0=Creature\Foo\foo_skin.blp       # manual texture paths
retroport bake  I:\conv\foo.m2 --source-m2 I:\extract\foo.m2 --listfile listfile.csv   # auto from TXID
retroport repair  I:\conv\foo.m2 --source-m2 I:\extract\foo.m2   # restore data MultiConverter overwrote at 0x130
retroport register --dbc-dir I:\dbc --out I:\patch\DBFilesClient --path Creature\Foo\foo.m2 --m2 I:\conv\foo.m2 --entries 1234
retroport helms I:\extract\item\objectcomponents\head\helm_foo_hum.m2 --out I:\conv\helms --client classic --multiconverter I:\tools\MultiConverter_Console.exe
retroport textures I:\extract\item\texturecomponents --out I:\patch --target classic --max-object 512
retroport register-item --dbc-dir I:\dbc --out I:\patch\DBFilesClient --like-display 782 --from-model I:\extract\axe_foo.m2 --icon INV_Axe_04 --entries 90001 --item-like 1391
```

### Items (`register-item`)

Adds an `ItemDisplayInfo` row for a converted weapon/armor model. Cloning an existing
display of the same kind (`--like-display`, e.g. a Blizzard 2H axe) inherits its sound
group and flags; every value you give overrides it. Names follow Blizzard's rows: model =
bare filename with `.mdx`, textures/icon = bare names (the client picks the folder from the
item type). `--from-model` fills the model name and the texture for the model's type-2
(item skin) slot from the wow.export manifest. Armor uses `--component TorsoUpper=Name`
etc. for the eight body-texture fields.

Why `Item.dbc` is touched too: AzerothCore overwrites `item_template.displayid` with
`Item.dbc`'s DisplayInfoID for any item listed there (`DBC.EnforceItemAttributes`, on by
default), and `creature_equip_template` drops items missing from `Item.dbc`. So `--entries`
repoints existing Item.dbc rows, and new entries get a row copied from `--item-like`.
The result lists the in-MPQ paths to pack (model, `00.skin`, texture, icon).

### Helms (`helms`, GUI: Convert > Helm set)

A 3.3.5 helm is one model per race/gender (`<Base>_HuM.m2`, `<Base>_BeF.m2`...);
ItemDisplayInfo names only `<Base>.mdx`. Modern helms use the same suffixes for the
classic races, plus separately-named files for races 3.3.5 lacks (skipped). Point it at
any one variant: it converts all 20 playable-race variants it finds (plus NPC goblin),
reports the missing ones (those characters show no helm), and prints the model name for
`register-item`. Export every race variant from wow.export first.

`--client classic` adds MultiConverter's `-fixhelm` (refits modern helms to the classic
heads); `--client hd` leaves it off for HD character models such as the 3.3.5 HD patch.

### Textures (`textures`, GUI: Textures tab)

- **Armor component textures** (`..._tu_m.blp`, or anything in a `<Section>Texture` folder)
  are resized to the body region they're composited into and written palettized with
  8-bit alpha and full mips, the way Blizzard and the HD patch ship them. Classic
  3.3.5 models use a 256x256 skin (ArmUpper/ArmLower/TorsoUpper/LegUpper/LegLower 128x64,
  Hand/TorsoLower/Foot 128x32); `--target hd` doubles them for 512x512 HD skins. Output goes
  to `Item\TextureComponents\<Section>Texture\` with the name to use in ItemDisplayInfo.
- **Object textures** (weapons, helms...) are copied unchanged when already
  client-compatible; `--max-object N` shrinks larger ones (re-encoded DXT1/DXT5), and
  non-power-of-two sizes are fixed.

The BLP writer is checked against real Blizzard headers, and its output against
BLPConverter. Note that Pillow mis-decodes palettized BLP alpha (it reads the palette's
4th byte; Blizzard stores alpha as a separate plane), so retroport decodes those itself.

### Playable races (`character`)

```bash
retroport character --bfa "I:\WoW Modding\Clients\WorldofWarcraft BFA" --race KulTiran --sex male \
    --out I:\port\KulTiranMale --race-id 33 --listfile listfile.csv --dbd I:\WoW Modding\WoWDBDefs\definitions \
    --dbc-dir I:\WotLK3.3.5_New_HD_Patch_2025-ft-2.0.6\DBFilesClient
```

Ports one race/sex from a local **BfA (8.x) install** - the last expansion whose
characters use 3.3.5's customization system (CharSections textures composited into one
body texture, CharHairGeosets, CharacterFacialHairStyles). Shadowlands+ models add a
separate face mesh and an eye texture type (19) that 3.3.5 can't fill - the "green faces".
Tables are read with WoWDBDefs definitions; files straight from the install (wotlkconv's
CASC reader).

1. **Model** - converted by wotlkconv from the install, fixed up (submesh clamp 64, combiner
   table check, stale anim flags) and written as `Character\<Race>\<Sex>\<Race><Sex>.m2`.
2. **Layout remap** - BfA layout 2 (1024x512: every allied race, Goblin, Worgen, Pandaren)
   keeps the body sections at layout 1's pixel positions and puts a 512x512 face across the
   right half; 3.3.5 (and BfA layout 1) composites the face bottom-left. Exact section
   rectangles come from `CharComponentTextureSections`. Only vertices drawn with the body
   texture (type 1) move; a vertex shared with another texture, or a triangle spanning two
   regions that move differently, is reported, never guessed at. Layout-1 races skip this.
3. **Textures** - every CharSections texture for the race/sex, re-laid with the *same* map
   as the UVs (so features stay aligned): the skin composite re-assembled, the face and
   facial-hair/scalp overlays squeezed into the FaceUpper+FaceLower block and split.
   All written palettized like Blizzard's (facial hair/scalp with 8-bit alpha - never DXT,
   which those overlay slots don't accept). Every texture goes in `Character\<Race>\<Sex>\`
   with a `<Race><Sex>` prefix: BfA's male and female hair/scalp textures differ, so
   Blizzard-style shared race-folder names (`Hair01_00.blp`) would collide between runs.
4. **DBC rows** - CharSections, CharHairGeosets and CharacterFacialHairStyles rows for
   `--race-id`, appended to your client's DBCs (refused if that race/sex already has rows).
   Pass one run's `DBFilesClient` as the next run's `--dbc-dir` to collect both sexes.

`--target classic` writes the 256x256 layout instead of the HD 512x512 one. To make the
race playable, use `race` (below), which runs this for both sexes.

Verified on BfA Kul Tiran male and female: the head now samples 3.3.5's face region, a
client-style composite (skin + FaceLower + FaceUpper) with the model's UV wireframe on top
lines eyes, mouth and ears up exactly, 0 skin vertices shared or outside, 0 straddling
triangles. Still needed: poly reduction (25,226 / 33,741 triangles vs the 21,845 ceiling)
and an in-client test.

### Making it playable (`race`)

```bash
retroport race KulTiran --name "Kul Tiran" --bfa "I:\WoW Modding\Clients\WorldofWarcraft BFA" \
    --client "F:\Wrath of the Lich King 3.3.5a (wod models)" --server-dbc D:\AC\Build\Data\dbc \
    --out I:\port\KulTiranRace --listfile listfile.csv --dbd I:\WoW Modding\WoWDBDefs\definitions
```

Converts both sexes (as `character`) and adds everything a selectable race needs, as an
Alliance race (`--alliance-id`, default 22) and a Horde twin (`--horde-id`, default 23; `0`
leaves a side out) that share the models and textures - 3.3.5 fixes a race's faction in
ChrRaces, so "either faction" means two race ids. Race ids must be 1-32: client and
AzerothCore race masks are 32-bit (a race 53 would silently share race 21's bit).

The client is only read. Every DBC is built on the copy the client actually loads, found
in the order the client reads archives (`patch*.MPQ` folders first, then lettered/numbered
patches, then lettered/numbered locale patches, then the stock chain); `INSTALL.txt` lists
each source.

- **ChrRaces** - copies the faction template (`--alliance-template 1` Human,
  `--horde-template 2` Orc: faction, language, intro); name, `ClientFileString` = the BfA
  folder name; helmets (`ClientPrefix`) and customization labels from `--look-template`.
- **CreatureDisplayInfo / CreatureModelData** - base displays cloned from the look
  template's (sound, collision), pointing at the new models.
- **CharBaseInfo** - every class any race has (all ten with ARAC); **CharStartOutfit** -
  the template's outfits.
- **SkillRaceClassInfo, SkillLineAbility, Faction** - wherever a race mask includes the
  template, the new race is added (languages, racials, weapon skills, reputations).
- **Voice** - the race's own sounds from BfA (`race/sounds.py`): each BfA sound kit becomes a
  `SoundEntries` row with its `.ogg` files, as they are (the 3.3.5 client's FMOD plays Ogg
  Vorbis; BfA's volumes and distances are on 3.3.5's scale - Human's are identical in both).
  **EmotesTextSound** gets the race's emote sounds (emotes 3.3.5 lacks, like /forthealliance,
  are skipped), **VocalUISounds** its spoken errors ("My inventory is full"; the enums match),
  and a new **CreatureSoundData** row per sex its attack, wound and death sounds, set on the
  new models (a 3.3.5 player's come from `CreatureModelData.SoundID`). Without BfA data the
  look template's voice is cloned instead. Race ids 22+ also need the client extension
  [`native/wxl-race-emote-sounds`](native/wxl-race-emote-sounds): the client plays emote
  sounds for races 0-21 only.
- **NameGen** - random names from the look template.
- **BarberShopStyle** - the race's hair-style and facial-hair rows from BfA's own table (real
  names, e.g. "Swabbed", "Clydesdale"), limited to the converted variations; the same ids on
  client and server (the server validates a barber request by row id, race and sex).
- **CharSections, CharHairGeosets, CharacterFacialHairStyles** - the converted rows, once per id.
- **Character-create UI** - `MAX_RACES` and race buttons; buttons laid out by faction at
  runtime (rows tighten for a 6th race per side); the race icons from BfA's atlas spliced
  into `UI-CharacterCreate-Races.blp` (existing icons stay byte-identical); BfA's race
  description; the template's racial text per faction; the look template's create/select
  scene and its music (character select plays it by the race's own name). Patches sit in
  tagged `-- [retroport]` blocks.
- **Server** - the same DBC edits on the server's own DBCs, and `<Race>_race.sql`
  (+ `_undo.sql`): `playercreateinfo` (+ action, item), `player_race_stats`, and the new
  race's bit wherever the template's is set in skills, spells, quests, items, `spell_area`,
  `mail_level_reward` and race conditions; `creature_model_info` for the new displays.

Rebuild after changes with `--reuse` (skips the character conversion) and, once installed,
`--replace`, which first removes the previous build of those race ids from the base DBCs.
A rebuild over an installed package reproduces it exactly (rows, UI files, atlas).

**Installing (`install`)** - the build records a SHA-256 of every file it was built on.

```bash
retroport install I:\port\KulTiranRace --client "F:\Wrath of the Lich King 3.3.5a (wod models)" \
    --patch patch-H.MPQ --server-dbc D:\AC\Build\Data\dbc --clean          # plan only
retroport install ... --apply                                               # do it
retroport uninstall I:\port\KulTiranRace\installs\<time>.json               # undo
```

A modified DBC/UI file is installed only while the copy the client (or server) loads is
still the one the package was built on; if you changed it since, the plan marks it `stale`
and nothing is copied - rebuild with `--reuse --replace` to fold your changes in. Model
and texture files replace only copies in the race's own folder; a different file anywhere
else is a `conflict`. Replaced files (and, with `--clean`, leftovers in the race folder the
package doesn't contain) go to `<package>\backups\<time>\` first; `uninstall` restores them
and refuses if you edited an installed file afterwards.

**Geosets 3.3.5 doesn't switch** - 3.3.5 switches groups 0-17 and draws every variant of the
rest. Per group the converter keeps the piece that fills a hole in the default body (its open
edges meet the body's: Kul Tiran 2001 feet, 2201 torso) and hides the others; a group none of
whose pieces does is hidden whole - the Kul Tiran belt band (1802) and sash (1803) lie on a
closed body and belong only under belts that call for them, so belts show as texture.

**Cloaks** - BfA characters draw one cloak variant (geoset 1507) as cape texture + guild
emblem layer (texture types 16-18, which 3.3.5 lacks); the converter drops that empty second
layer, leaving the cape texture (the same repair as the released Mag'har port). The other
cloak geosets use the ordinary cape texture (type 2) and work as in 3.3.5.

**Triangle count** - the 21,845 figure is 65,535 indices / 3. Skins past it store the high
bits of each submesh's index start in its `level` field; independently released ports that
were confirmed in a live (WarcraftXL) client do the same with 23k-35k triangles (Earthen,
Haranir), so `check`'s triangle failure on these models should be confirmed in-game before
reducing anything.

## Status - validated vs. needs in-client testing

**Validated end-to-end (real files):** re-inline (self-contained `.m2` incl. the parent-
skeleton/child case), MultiConverter conversion (`-> MD20 v264`), combiner-flag fix,
submesh clamp, staging, check.

**Not yet confirmed in a real 3.3.5 client** (WMV loading != client accepting - error 132
is a client-side crash):
- whether MultiConverter's flag-stripping is sufficient for a clean load (test a plain
  converted model first);
- with the MultiConverter engine, whether the staged/clamped modern `.skin` files are
  WotLK-compatible as-is (the wotlkconv engine rewrites them to the 48-byte 3.3.5 header);
- the Blender decimation round-trip (the write-side of Blender is the known weak link;
  M2Mod's cap check is the safety net).

**Validated on a real model (hozumonkey, non-skel):** texbake resolved the empty slot's
FileDataID through the full community listfile and baked it; the new `.anim` check found
that MultiConverter leaves external `.anim` files in Legion's chunked AFM2 format.

**Needs in-client confirmation:** the multi-LOD gate (`lod_unmerged`, from Conjure) fires
when the M2 carries more than 1.5x the vertices skin00 uses. Models that ship a separate
`_lod` skin trip it; whether that actually breaks a 3.3.5 client is untested.

**wotlkconv engine on real models:** a 2H axe (clean check), hozumonkey (its 18 external
`.anim` files now come out flat, no AFM2 warning), and the `.skel`-based Kul Tiran male
(skeleton merged; 276 inline + 57 external animations) all convert to MD20 v264; the
remaining blocker on the last two is the triangle ceiling (poly-reduce).

**Gaps:** MPQ packaging; WMOs and doodads (wotlkconv converts both - not wired into the
pipeline yet); character models (eyes use texture type 19, which wotlkconv empties, and
modern face UVs don't match the 3.3.5 skin layout); bone pruning over the ~256 ceiling. Helms and resized armor
textures still need an in-client check (helm fit with/without `-fixhelm`, texture layout).

**Tool quirk:** MultiConverter reports "No files to convert" when the model path is over
260 characters (Windows MAX_PATH) - keep output folders short. (The wotlkconv engine
doesn't have this limit.)

## Layout

```
retroport/
  models.py            AssetInfo, Finding, ValidationReport, ConversionResult
  pipeline.py          the headless conductor (Pipeline / PipelineConfig)
  check.py             WotLK render-blocker diagnostics (+ LOD gate, .anim checks)
  dbc.py               WDBC engine, CreatureModelData/DisplayInfo registration, TextureVariations
  itemdbc.py           ItemDisplayInfo + Item.dbc writer, pack paths, prefill from a model
  helms.py             helm race/gender variant discovery + batch conversion
  character/           BfA playable-race port: layouts (section remap), model (UV remap),
                       textures (composite/region conversion), convert (orchestration + DBC rows)
  race/                playable race: tables (DBC edits + removal), glue (create-screen UI),
                       icons (DXT atlas splice), sql (AzerothCore), build (orchestration)
  mpq.py               read-only MPQ reader + the client's archive priority
  textures.py          armor-region / object texture resize for 3.3.5
  sql.py               AzerothCore creature_template_model repoint SQL
  safeio.py            atomic write-verify-replace, backups
  scan/                chunk-walk classifier
  formats/             binary layer: structs, m2 (MD21), md20 (textures/sequences/bake),
                       skel, anim, verify (structural problem detectors), blp (BLP2 codec)
  convert/
    skel_reinliner.py  the .skel re-inliner  <- the novel native work
    engine.py          wotlkconv engine: scoped listfile, conversion, verified writes
    multiconverter.py  MultiConverter wrapper (console build, output verification)
    m2mod.py           M2Mod wrappers (m2<->m2i, txid-remove) + reduce_model chain
    blender.py         Blender decimate wrapper
    blender_decimate.py  headless Blender script (WoWTools .m2i / WBS .m2 / obj)
    fixup.py           combiner header repair, combiner fix, submesh clamp, nViews, stale-flag cleanup
    texbake.py         TXID + listfile -> baked texture paths
    loaders.py         FileDataID loaders over a wow.export extraction
gui/                   PyQt6 shell over pipeline.py
```

See `GUI.md` for the desktop app and `SKEL_REINLINE.md` for the re-inline algorithm.
Parts are adapted from Conjure (MIT) - see `THIRD_PARTY_NOTICES.md`.
