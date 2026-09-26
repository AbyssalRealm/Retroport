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
| Check        | WotLK render-blocker diagnostics                          | built |

Re-inline auto-skips a model with no `.skel` (it passes straight through).
MultiConverter is attempted via CLI argument; if a build ignores it, the log says
so and you run that one step by hand.

**Register** - make a converted model usable in-game. Point it at an extracted DBC
folder + the in-game model path; it appends `CreatureModelData` +
`CreatureDisplayInfo`, writes patched DBCs, and prints the `displayId` (for
`creature_template.modelid`) plus a `creature_model_info` SQL line.

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
