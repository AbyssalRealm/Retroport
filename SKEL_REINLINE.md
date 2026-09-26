# `.skel` Re-Inliner — Design Note

## The problem

Shadowlands → The War Within models externalise their skeleton. A `.skel`-based
M2 has **empty** bone / attachment / sequence arrays in its MD20 header and a
`SKID` chunk pointing at a separate `.skel` file that holds the real data, split
across chunks: `SKB1` (bones + key-bone lookup), `SKA1` (attachments + lookup),
`SKS1` (global loops + sequences + lookup), `SKL1` (name), `SKPD` (parent link),
`AFID`/`BFID` (anim / bone file IDs). Per-animation keyframe data is split again
into the `.anim` files' `AFSB` (bone tracks) / `AFSA` (attachment tracks) chunks.

MultiConverter — the tool that does the MD21→MD20 downgrade — explicitly does
**not** support `.skel` models, because the bone/attachment/sequence data simply
isn't in the M2. Even JM2Converter errors out for the same reason. So nothing
off-the-shelf retroports these.

## The strategy: re-inline, then hand off

We don't reimplement the MD21→MD20 downgrade. We make the `.skel` model look
like a normal Legion-style self-contained model, then let MultiConverter do the
version downgrade. Division of labour:

```
.skel model ──[ our re-inliner ]──> self-contained Legion-style M2 ──[ MultiConverter ]──> WotLK MD20
                (Milestone A/B)                                        (existing tool)
```

The re-inliner keeps every record byte-identical and only changes **locations**
(offsets) and populates the header arrays. It never converts struct versions —
that's MultiConverter's job.

## The rebasing rule (why it's exact)

Every `M2Array` offset inside a `.skel` chunk is relative to that chunk's content
start. We append a chunk's **entire content verbatim** into the MD21 data block
at `base`. Therefore every offset `O` that was relative to that chunk maps to
`base + O` — a single constant delta for the whole region. We apply `+= base` to:

* the MD20 header arrays we populate (bones, key_bone_lookup, attachments,
  attachment_lookup, sequences, global_loops, sequence_lookups, name);
* inside every bone, each `M2Track`'s outer arrays (timestamps, values), which
  always point at the per-animation inner-array list (always in-region);
* each inner array's leaf offset **if it lands inside the source region**.

The only judgement is that last one. An inner array is one of:

* **inline** — leaf offset within `[0, region_len)` → keyframes are in the
  `.skel` data → rebase `+= base`;
* **external** — leaf offset outside the region → keyframes are in a `.anim`
  file (`AFSB`/`AFSA`) → leave untouched and count it.

A verification pass re-reads the header arrays and confirms each resolves inside
the data block, so a wrong struct size fails loudly instead of corrupting.

## Milestone A (built) vs Milestone B (built)

**Milestone A — structural re-inline.** Bones, attachments, sequences, global
loops, lookup tables, and all *inline* track data are relocated and rebased;
`SKID` is dropped; `BFID` is kept; the name is regenerated. Result: a model with
a correct skeleton, attachments, and full sequence list.

**Milestone B — animation pass.** When an `anim_loader` is supplied, every
sequence with an external `.anim` (an AFID entry) has its `AFSB` (bone) / `AFSA`
(attachment) keyframe data pulled into the M2 and its track offsets rebased to
point at it. Each inlined sequence is then flagged `0x20` (internally-stored) on
its `M2Sequence` record — required, because the client otherwise tries to load
the now-removed `.anim` file *and* won't parse the inline bone animation blocks
at all (per wowdev: "-1 animationblocks in bones won't get parsed if 0x20 is not
set"). Result: a **fully self-contained** model with no `.anim`/`.skel`
dependency, ready for MultiConverter's MD20 downgrade. Inline-vs-external is
decided per sequence by **AFID membership** (authoritative), not by a range
heuristic — a synthetic test confirms an external array with a small offset is
still treated as external because its sequence has an AFID entry.

Both milestones are unit-tested on synthetic fixtures that match the documented
layouts; the round-trip tests follow `bone → M2Track → per-animation array →
keyframe leaf` and recover the exact bytes. **Real Blizzard-file validation is
still the pending step** (see below).

Remaining edges, deliberately deferred: event tracks (`AFM2`) are not yet rebased
(low visual priority); `SKPD` parent links are detected and reported but their
shared `.anim` files must be supplied via the same `anim_loader` (resolve the
parent's AFID); global-sequence tracks are treated as inline. High-poly
decimation (the Blender stage) is a separate pipeline step.

## CLI & Warcrafter integration

Everything is a library function with a thin CLI veneer and a stable JSON
contract, so the conversion can plug straight into Warcrafter's UI.

```
retroport reinline <model.m2> --source <wow.export dir> --out <dir> [--json] [--no-anim]
```

`--source` is a wow.export extraction; the tool resolves the model's `SKID` →
`<id>.skel` and `AFID` → `<id>.anim` from it via a FileDataID loader. With
`--json` it prints one envelope to stdout and sets the exit code:

```json
{ "ok": true, "command": "reinline", "error": null,
  "result": { "source": "...", "output": "...", "success": true,
              "report": { "findings": [ {"check": "...", "severity": "...", "detail": "..."} ] } } }
```

Two integration paths, both supported by the same core:

* **Shell out** from a Tauri command to `retroport reinline ... --json`, parse
  stdout, render `result.report.findings` in the UI.
* **Import** in the FastAPI sidecar: call `reinline_file(model, skel_loader,
  out_dir, anim_loader=...)`, which returns a pydantic `ConversionResult`
  (`make_file_id_loader(source, ".skel")` / `".anim"` build the loaders). The
  loaders are `Callable[[int], bytes | None]`, so a CASC-backed loader can drop
  in later without touching the re-inliner.

## Layout sources

Byte layouts are centralised in `formats/structs.py` with the sizes used here:
`M2Array` 8, `M2Track` 20, `M2CompBone` 88, `M2Attachment` 40, `M2Sequence` 64;
SKB1/SKA1 headers 16 bytes, SKS1 32, SKPD 16. From `wowdev.wiki/M2` and
`wowdev.wiki/M2/.skel`, cross-checked against an independent M2 binary parser.
A correction to any layout is a one-line edit there.

> Cross-reference worth a look for validation: the `wow-m2` Rust crate
> (a parser/validator/converter for M2 with animation support) is a useful
> independent implementation to diff behaviour against on real files.

## Validate against real files next

The tests use synthetic fixtures that match the documented structures and prove
the rebasing arithmetic exactly. The next step before trusting output is to run
a real `.skel` creature (extracted via wow.export) through `reinline_file`, load
it in WMV r252 / the 3.3.5a client, and confirm the skeleton and attachments are
correct — then feed it to MultiConverter for the MD20 downgrade.
