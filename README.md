# retroport

Automate porting modern WoW character/creature models (Shadowlands → The War Within)
down to the 3.3.5a (WotLK) client.

## What this actually is

**retroport is a conductor, not a converter.** It does *not* reimplement MultiConverter
or M2Mod. It runs the novel glue that no maintained tool does — folding `.skel`/`.anim`
into the model, fixing WotLK-specific render blockers, staging skins, and diagnosing the
result — and it *drives the existing tools* for the heavy format work.

Division of labor:

| Job | Who does it |
|-----|-------------|
| Fold `.skel` (bones) + `.anim` (animations) into the `.m2`, drop SKID | **retroport** (re-inline) |
| Copy/rename `.skin` files to `<model>NN.skin` next to the `.m2` | **retroport** (stage) |
| Clear the combiner flag + reset batch shaders (black-mesh fix) | **retroport** (fixup) |
| Clamp submeshes to ~59, set nViews, clear stale anim flags | **retroport** (fixup) |
| Bake in-game paths into type-0 texture slots MultiConverter left empty | **retroport** (texbake) |
| Diagnose triangles / submeshes / textures / bones / skins / LODs / `.anim` files | **retroport** (check) |
| Write CreatureModelData + CreatureDisplayInfo rows, repoint SQL | **retroport** (register) |
| Write ItemDisplayInfo rows (weapons/armor), repoint Item.dbc, item SQL + pack paths | **retroport** (register-item) |
| Convert every 3.3.5 race/gender variant of a helm (+ `-fixhelm` for classic models) | **retroport** (helms) |
| Resize armor textures to their body region, shrink/re-encode object textures (BLP2) | **retroport** (textures) |
| **MD21 → MD20, version → 264, TXID/SFID texture resolution** | **MultiConverter** (external exe) |
| **`.m2` ↔ `.m2i` + mesh decimation** | **M2Mod** (external exe) **+ Blender** |

Two things retroport does **not** do, despite common assumption: it does not *reduce*
bone count (it only flags it), and it does not fold skins into the `.m2` (only the
skeleton and animations are inlined — skins stay as separate `.skin` files that the
client loads alongside the model, exactly as in modern WoW).

## The pipeline

```
triage -> [poly-reduce] -> stage skins -> re-inline -> MultiConverter -> fixup -> texbake -> check
```

1. **triage** — classify the model (M2 vs M3, version, uses `.skel`).
2. **poly-reduce** *(optional; needs M2Mod + Blender)* — M2Mod exports `.m2`->`.m2i`,
   Blender decimates the `.m2i`, M2Mod compiles it back. This is the standard backport
   route (Blender writes `.m2i`, not a WotLK `.m2` binary, so M2Mod does the compile).
   Runs *before* conversion, on the modern model. M2Mod rejects a reduction that's still
   over the WotLK vertex/index caps, so it fails loudly rather than shipping a broken model.
3. **stage** — copy the model's `.skin` files into the output folder as `<model>NN.skin`.
4. **re-inline** — fold `.skel` + `.anim` into a self-contained `.m2`, drop SKID
   (skipped automatically for models with no `.skel`).
5. **MultiConverter** — the MD21->MD20 conversion. retroport verifies the result really
   became MD20 (`post-convert: chunked=False version=264`).
6. **fixup** — combiner-flag/shader fix, submesh clamp, nViews, stale-flag cleanup.
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
pip install -e .            # library + `retroport` CLI (pydantic, typer, rich)
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

## Status - validated vs. needs in-client testing

**Validated end-to-end (real files):** re-inline (self-contained `.m2` incl. the parent-
skeleton/child case), MultiConverter conversion (`-> MD20 v264`), combiner-flag fix,
submesh clamp, staging, check.

**Not yet confirmed in a real 3.3.5 client** (WMV loading != client accepting - error 132
is a client-side crash):
- whether MultiConverter's flag-stripping is sufficient for a clean load (test a plain
  converted model first);
- whether the staged/clamped modern `.skin` files are WotLK-compatible as-is or need an
  explicit conversion step;
- the Blender decimation round-trip (the write-side of Blender is the known weak link;
  M2Mod's cap check is the safety net).

**Validated on a real model (hozumonkey, non-skel):** texbake resolved the empty slot's
FileDataID through the full community listfile and baked it; the new `.anim` check found
that MultiConverter leaves external `.anim` files in Legion's chunked AFM2 format.

**Needs in-client confirmation:** the multi-LOD gate (`lod_unmerged`, from Conjure) fires
when the M2 carries more than 1.5x the vertices skin00 uses. Models that ship a separate
`_lod` skin trip it; whether that actually breaks a 3.3.5 client is untested.

**Gaps:** MPQ packaging; converting / inlining the external `.anim` files of non-skel
models; WMOs and doodads; bone pruning over the ~256 ceiling. Helms and resized armor
textures still need an in-client check (helm fit with/without `-fixhelm`, texture layout).

**Tool quirk:** MultiConverter reports "No files to convert" when the model path is over
260 characters (Windows MAX_PATH) - keep output folders short.

## Layout

```
retroport/
  models.py            AssetInfo, Finding, ValidationReport, ConversionResult
  pipeline.py          the headless conductor (Pipeline / PipelineConfig)
  check.py             WotLK render-blocker diagnostics (+ LOD gate, .anim checks)
  dbc.py               WDBC engine, CreatureModelData/DisplayInfo registration, TextureVariations
  itemdbc.py           ItemDisplayInfo + Item.dbc writer, pack paths, prefill from a model
  helms.py             helm race/gender variant discovery + batch conversion
  textures.py          armor-region / object texture resize for 3.3.5
  sql.py               AzerothCore creature_template_model repoint SQL
  safeio.py            atomic write-verify-replace, backups
  scan/                chunk-walk classifier
  formats/             binary layer: structs, m2 (MD21), md20 (textures/sequences/bake),
                       skel, anim, verify (structural problem detectors), blp (BLP2 codec)
  convert/
    skel_reinliner.py  the .skel re-inliner  <- the novel native work
    multiconverter.py  MultiConverter wrapper (console build, output verification)
    m2mod.py           M2Mod wrappers (m2<->m2i, txid-remove) + reduce_model chain
    blender.py         Blender decimate wrapper
    blender_decimate.py  headless Blender script (WoWTools .m2i / WBS .m2 / obj)
    fixup.py           combiner fix, submesh clamp, nViews, stale-flag cleanup
    texbake.py         TXID + listfile -> baked texture paths
    loaders.py         FileDataID loaders over a wow.export extraction
gui/                   PyQt6 shell over pipeline.py
```

See `GUI.md` for the desktop app and `SKEL_REINLINE.md` for the re-inline algorithm.
Parts are adapted from Conjure (MIT) - see `THIRD_PARTY_NOTICES.md`.
