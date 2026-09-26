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
```

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
models; items (ItemDisplayInfo), WMOs and doodads; bone pruning over the ~256 ceiling.

**Tool quirk:** MultiConverter reports "No files to convert" when the model path is over
260 characters (Windows MAX_PATH) - keep output folders short.

## Layout

```
retroport/
  models.py            AssetInfo, Finding, ValidationReport, ConversionResult
  pipeline.py          the headless conductor (Pipeline / PipelineConfig)
  check.py             WotLK render-blocker diagnostics (+ LOD gate, .anim checks)
  dbc.py               WDBC engine, CreatureModelData/DisplayInfo registration, TextureVariations
  sql.py               AzerothCore creature_template_model repoint SQL
  safeio.py            atomic write-verify-replace, backups
  scan/                chunk-walk classifier
  formats/             binary layer: structs, m2 (MD21), md20 (textures/sequences/bake),
                       skel, anim, verify (structural problem detectors)
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
