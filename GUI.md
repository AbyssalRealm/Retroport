# Retroport GUI

A thin PyQt6 desktop shell over `retroport`'s headless pipeline. The logic lives
in the library (`retroport.pipeline`), so the GUI, the CLI, and Warcrafter all
drive the same code.

## Install & launch

```bash
pip install -e ".[gui]"     # adds PyQt6
retroport-gui               # or:  python -m gui
```

## Tabs

**Convert** - the end-to-end run.
1. Pick the modern `.m2` (as extracted by wow.export, with its `.manifest.json`,
   `.skel`, and `.anim` files beside it).
2. Pick an output folder (defaults to `<model>/converted`).
3. Toggle stages, set the submesh clamp (59) and optional nViews, hit **Run**.
4. Logs stream live; a summary lists each stage's result.

The stages, in order:

| Stage        | What it does                                             | Needs |
|--------------|----------------------------------------------------------|-------|
| Triage       | classify the model (M2 vs M3, version, uses `.skel`)     | built |
| Re-inline    | fold `.skel` + `.anim` into a self-contained M2          | built |
| MultiConverter | MD21 → MD20, version 264, resolve TXID/SFID            | exe   |
| Fixup        | clear stale anim flags, clamp submeshes, set nViews      | built |
| Bake textures | fill empty type-0 texture slots from TXID + listfile    | listfile (Settings) |
| Check        | WotLK render-blocker diagnostics (+ LOD gate, .anim files) | built |

The output folder must not be the model's own folder (the run refuses it), and the
converted model + skins are copied to `<out>/_backup/` before fixup/bake edit them.

Re-inline auto-skips a model with no `.skel` (it passes straight through).
MultiConverter is attempted via CLI argument; if a build ignores it, the log says
so and you run that one step by hand.

**Register** - make a converted model usable in-game. Point it at an extracted DBC
folder + the in-game model path; it appends `CreatureModelData` +
`CreatureDisplayInfo` and writes patched DBCs (original names, ready to pack) into a
separate output folder. Optional: the converted `.m2` (fills the GeoBox from its bounds),
model scale, ID floors (remembered; 0 = next free), TextureVariation1-3 + CreatureGeosetData
for models with type 11/12/13 texture slots, and creature entries - which turn the SQL into
a full AzerothCore `creature_template_model` repoint block with a display-sharing check.
The DBC engine refuses non-3.3.5 layouts and verifies every write (adapted from Conjure).

**Settings** - the external tool exes, saved with QSettings: MultiConverter, M2Mod,
BLPConverter, MPQ Editor, Blender, and the listfile. These flow into the pipeline
as `PipelineConfig.tools`.

## Using the pipeline without the GUI

```python
from retroport import Pipeline, PipelineConfig

cfg = PipelineConfig(
    model=r"I:/extract/creature/foo/foo.m2",
    out_dir=r"I:/converted/foo",
    tools={"multiconverter": r"I:/tools/MultiConverter.exe"},
    clamp_submeshes_to=59,
)
result = Pipeline(cfg, on_log=print).run()
print(result.ok, result.output_m2)
```

## What still needs a real tool / more work

- **Poly reduction** (the `triangle_limit` FAIL): needs Blender + the M2↔M2i bridge
  (M2Mod). Not yet wired - the one genuinely non-Python gap.
- **BLP conversion / MPQ packing**: exe paths are captured in Settings but the
  stages aren't wired into the pipeline yet.
- The submesh clamp is a first implementation; validate against RaceBuilder's
  proven `clamp_skin_submeshes` on real files.
