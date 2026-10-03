"""WotLK compatibility checker.

Reads a *converted* (MD20) model and its .skin files and reports the things that
make a model fail or render wrong on a 3.3.5a client - turning "it's black and
missing parts" into a concrete checklist. This runs on MultiConverter output, not
the modern source.

What it surfaces:
  * nViews (how many .skin files the client will load) vs. which are present,
  * the texture-combiner-combos flag (0x08) - the client then reads M2Batch.shader
    as a combo-table index, so a table that doesn't fit renders all-black meshes;
    and data MultiConverter overwrote with that table's header field,
  * bone count vs. WotLK's practical ceiling,
  * texture list with type (type != 0 = filled at runtime, normal for character
    skin/hair; type 0 with an empty path = a real problem),
  * per-skin geoset list (skinSectionId) - directly explains missing body parts,
  * batch shader_ids (0x8000 = not rendered),
  * (MD20 only) an un-merged multi-LOD export: the M2 declares far more vertices
    than skin00 actually spans,
  * (MD20 only) the animation table - inline / alias / external - and whether each
    external sequence's <model><id>-<sub>.anim is present and in WotLK format.

The LOD gate and animation classification are adapted from Conjure (MIT, TheDaveCalaz).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from .models import Finding, Severity, ValidationReport
from .formats import structs as S
from .formats.md20 import read_sequences, read_texture_slots
from .formats.verify import combiner_combos_problem, combiner_header_overlaps, m2_data_block

# character geoset groups (skinSectionId // 100); a guide, not exhaustive
_GEOSET_GROUPS = {
    0: "body/skin", 1: "hair", 2: "facial1 (beard)", 3: "facial2", 4: "facial3 (sideburns)",
    5: "gloves/hands", 6: "boots/feet", 7: "shirt", 8: "ears", 9: "sleeves",
    10: "legcuffs", 11: "chest", 12: "pants/legs", 13: "tabard", 14: "robe",
    15: "cloak", 17: "eyeglow", 18: "belt", 19: "tail", 20: "toes/feet",
}

# M2Texture.type (0 = hardcoded path; others are runtime-filled)
_TEXTURE_TYPES = {
    0: "hardcoded", 1: "body skin", 2: "object/cape", 6: "hair", 8: "skin extra",
    11: "monster1", 12: "monster2", 13: "monster3",
}

# WotLK bone palette ceiling (per wowdev; bone lookups max ~256)
_WOTLK_BONE_CEILING = 256
# WotLK index ceiling: uint16 triangle indices -> 65535/3 triangles per the retroport tutorials
_WOTLK_MAX_TRIANGLES = 21845
# WotLK practical submesh ceiling (RaceBuilder clamps to this)
_WOTLK_MAX_SUBMESHES = 59
# M2 vertices beyond this multiple of skin00's span = an un-merged multi-LOD export
_LOD_SPAN_RATIO = 1.5


@dataclass
class SkinInfo:
    path: str
    present: bool = True
    magic_ok: bool = True
    geosets: list[int] = field(default_factory=list)
    vertices: int = 0
    vertex_span: int = 0          # max global vertex index referenced + 1
    triangles: int = 0
    batches: int = 0
    shaders: list[int] = field(default_factory=list)
    bone_count_max: int = 0


@dataclass
class CheckResult:
    asset: str
    version: int | None = None
    chunked: bool = False         # MD21 (modern, unconverted) vs MD20
    global_flags: int = 0
    combiner_combos: bool = False
    combiner_combo_count: int = 0
    n_views: int = 0
    bones: int = 0
    vertices: int = 0
    materials: int = 0
    textures: list[tuple[int, str]] = field(default_factory=list)  # (type, path)
    anim_inline: int = 0
    anim_alias: list[str] = field(default_factory=list)      # NNNN-SS tags
    anim_external: list[str] = field(default_factory=list)   # NNNN-SS tags (need .anim files)
    skins: list[SkinInfo] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def report(self) -> ValidationReport:
        return ValidationReport(asset=self.asset, findings=self.findings)


_data_block = m2_data_block   # kept for callers that import the old private name


def _read_textures(block: bytes) -> list[tuple[int, str]]:
    return [(t.type, t.name) for t in read_texture_slots(block)]


def analyze_m2(path: str) -> CheckResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    block = m2_data_block(raw)
    res = CheckResult(asset=path)
    res.chunked = raw[:4] != b"MD20"
    res.version = S.read_u32(block, S.MD20_VERSION)
    res.global_flags = S.read_u32(block, S.HDR_GLOBAL_FLAGS)
    res.combiner_combos = bool(res.global_flags & S.FLAG_USE_COMBINER_COMBOS)
    res.n_views = S.read_u32(block, S.HDR_NUM_SKIN_PROFILES)
    res.bones, _ = S.read_m2array(block, S.HDR_BONES)
    res.vertices, _ = S.read_m2array(block, S.HDR_VERTICES)
    res.materials, _ = S.read_m2array(block, S.HDR_MATERIALS)
    res.textures = _read_textures(block)
    if res.combiner_combos:
        res.combiner_combo_count, _ = S.read_m2array(block, S.HDR_TEXTURE_COMBINER_COMBOS)
    for seq in read_sequences(block):
        if seq.kind == "inline":
            res.anim_inline += 1
        elif seq.kind == "alias":
            res.anim_alias.append(seq.tag)
        else:
            res.anim_external.append(seq.tag)

    # findings
    if res.version != 264 and (res.version & 0xFFFF) == 264:
        res.findings.append(Finding(check="version", severity=Severity.WARN,
            detail=f"version word is 0x{res.version:08X}: the low 16 bits are 264 but high bits are set "
                   "(a converter artefact). Set it to 264 if the client rejects the model."))
    elif res.version not in (256, 257, 260, 261, 262, 263, 264):
        res.findings.append(Finding(check="version", severity=Severity.WARN,
                                    detail=f"version {res.version} is not a WotLK-era value (expected 264); did MultiConverter run?"))
    overlaps = combiner_header_overlaps(block)
    if overlaps:
        res.findings.append(Finding(check="combiner_header", severity=Severity.FAIL,
            detail=f"{', '.join(overlaps)} data starts inside the combiner-combos header field (0x130-0x137), so its "
                   "first 8 bytes are lost (global-loop timestamps, or for the sequence table the Stand animation). "
                   "MultiConverter does this "
                   "when it adds combos; re-run the pipeline, or `retroport repair <m2> --source-m2 <original>`."))
    if res.bones > _WOTLK_BONE_CEILING:
        res.findings.append(Finding(check="bone_count", severity=Severity.WARN,
            detail=f"{res.bones} bones exceeds WotLK's ~{_WOTLK_BONE_CEILING} palette ceiling; may need bone reduction in Blender."))
    bad_tex = [p for t, p in res.textures if t == 0 and not p]
    if bad_tex:
        res.findings.append(Finding(check="texture_path", severity=Severity.WARN,
            detail=f"{len(bad_tex)} hardcoded (type 0) texture(s) have no path; texture references unresolved."))
    placeholders = [p for t, p in res.textures if t == 0 and p.lower().startswith(("unknown\\", "unknown/"))]
    if placeholders:
        res.findings.append(Finding(check="texture_placeholder", severity=Severity.WARN,
            detail=f"{len(placeholders)} texture(s) point at a stand-in path for an unresolved FileDataID "
                   f"({', '.join(placeholders[:3])}): add the id to data/overrides.csv or a newer listfile "
                   "and re-run, or pack a .blp at that path."))
    return res


def analyze_skin(path: str) -> SkinInfo:
    info = SkinInfo(path=path)
    if not os.path.isfile(path):
        info.present = False
        return info
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != S.SKIN_MAGIC:
        info.magic_ok = False
        return info
    s_cnt, s_off = S.read_m2array(data, S.SKIN_SUBMESHES)
    for i in range(s_cnt):
        rec = s_off + i * S.M2SKINSECTION
        if rec + 2 <= len(data):
            info.geosets.append(struct.unpack_from("<H", data, rec + S.SKINSECTION_ID)[0])
    v_cnt, v_off = S.read_m2array(data, S.SKIN_VERTICES)
    i_cnt, _ = S.read_m2array(data, S.SKIN_INDICES)
    info.vertices = v_cnt
    if v_cnt and v_off + v_cnt * 2 <= len(data):
        info.vertex_span = max(struct.unpack_from(f"<{v_cnt}H", data, v_off)) + 1
    info.triangles = i_cnt // 3
    b_cnt, b_off = S.read_m2array(data, S.SKIN_BATCHES)
    info.batches = b_cnt
    for i in range(b_cnt):
        rec = b_off + i * S.M2BATCH
        if rec + 4 <= len(data):
            info.shaders.append(struct.unpack_from("<H", data, rec + S.BATCH_SHADER)[0])
    if S.SKIN_BONE_COUNT_MAX + 4 <= len(data):
        info.bone_count_max = S.read_u32(data, S.SKIN_BONE_COUNT_MAX)
    return info


def check_model(m2_path: str, skin_dir: str | None = None) -> CheckResult:
    res = analyze_m2(m2_path)
    folder = skin_dir or os.path.dirname(m2_path) or "."
    stem = os.path.splitext(os.path.basename(m2_path))[0]

    # The client loads nViews skins named <stem>NN.skin (00 = highest detail).
    expected = max(res.n_views, 1)
    for i in range(expected):
        skin_path = os.path.join(folder, f"{stem}{i:02d}.skin")
        res.skins.append(analyze_skin(skin_path))

    missing = [s for s in res.skins if not s.present]
    if missing:
        res.findings.append(Finding(check="skin_missing", severity=Severity.FAIL,
            detail=f"{len(missing)}/{expected} expected .skin file(s) not found next to the model "
                   f"(client loads {expected}: {stem}00.skin .. {stem}{expected-1:02d}.skin). "
                   "Rename/convert the LODs or fix nViews."))
    bad_magic = [s for s in res.skins if s.present and not s.magic_ok]
    if bad_magic:
        res.findings.append(Finding(check="skin_format", severity=Severity.FAIL,
            detail=f"{len(bad_magic)} .skin file(s) lack the SKIN magic; not WotLK format (convert them)."))

    if res.combiner_combos:
        with open(m2_path, "rb") as fh:
            raw = fh.read()
        skins = []
        for s in res.skins:
            if s.present and s.magic_ok:
                with open(s.path, "rb") as fh:
                    skins.append(fh.read())
        problem = combiner_combos_problem(raw, skins)
        if problem:
            res.findings.append(Finding(check="combiner_combos", severity=Severity.WARN,
                detail=f"global flag 0x08 set, but its combiner-combos table can't work on WotLK: {problem}. "
                       "Batches that use it render black; fixup clears the flag and resets batch shaders to 0."))
        else:
            res.findings.append(Finding(check="combiner_combos", severity=Severity.PASS,
                detail=f"global flag 0x08 with a valid {res.combiner_combo_count}-entry combiner-combos table: "
                       "blends textures (e.g. env-mapped reflections) the way Blizzard's own 3.3.5 models do."))

    primary = res.skins[0] if res.skins else None
    if primary and primary.present and primary.magic_ok:
        groups = sorted({g // 100 for g in primary.geosets})
        labels = ", ".join(f"{g}:{_GEOSET_GROUPS.get(g, '?')}" for g in groups)
        res.findings.append(Finding(check="geosets", severity=Severity.PASS,
            detail=f"skin00 has {len(primary.geosets)} geoset(s) across groups [{labels}]. "
                   "Missing body parts = missing groups here."))

        # Poly ceiling: uint16 indices cap triangles; the retroport tutorials cite 21845.
        if primary.triangles > _WOTLK_MAX_TRIANGLES:
            res.findings.append(Finding(check="triangle_limit", severity=Severity.FAIL,
                detail=f"skin00 has {primary.triangles} triangles ({primary.vertices} verts), over WotLK's "
                       f"{_WOTLK_MAX_TRIANGLES} ceiling. Reduce in Blender (M2Mod M2i round-trip) before converting "
                       "or geometry corrupts/drops."))
        # Submesh ceiling (RaceBuilder clamps to 59).
        if len(primary.geosets) > _WOTLK_MAX_SUBMESHES:
            res.findings.append(Finding(check="submesh_limit", severity=Severity.WARN,
                detail=f"skin00 has {len(primary.geosets)} submeshes, over WotLK's ~{_WOTLK_MAX_SUBMESHES}. "
                       "Clamp/merge the extra (Shadowlands) geosets or they overflow and render wrong."))

        # Non-WotLK shader values: on disk WotLK batches are 0 or small; high bits
        # (0x4000/0x8000) usually mean MultiConverter didn't normalise them.
        if not res.combiner_combos:
            odd = sorted({s for s in primary.shaders if s and (s & 0x7FFF) > 7 and not (s & 0x8000)})
            if odd:
                res.findings.append(Finding(check="shaders", severity=Severity.WARN,
                    detail=f"batch shader values {odd} look non-WotLK (expected 0 or small on disk); "
                           "those batches may render black until reset to 0."))

        if not res.chunked and primary.vertex_span and res.vertices > primary.vertex_span:
            _check_lod_vertices(res, primary, folder, stem)

    if not res.chunked:
        _check_anims(res, folder, stem)
    return res


def _check_lod_vertices(res: CheckResult, primary: SkinInfo, folder: str, stem: str) -> None:
    """The M2 holds more vertices than skin00 uses. Two very different causes:

    * stacked LOD blocks (the normal modern layout): the vertex array is skin00's
      block followed by one block per LOD skin, and each modern LOD skin numbers
      its vertices from 0 *within its own block*. WotLK has no per-skin base, so
      those skins index into skin00's block instead. Harmless while the client
      doesn't load them (nViews), garbled geometry if it does.
    * anything else past the Conjure ratio: an un-merged multi-LOD export.
    """
    skins = []                      # (index, SkinInfo) for every <stem>NN.skin on disk
    for i in range(16):
        s = res.skins[i] if i < len(res.skins) else analyze_skin(os.path.join(folder, f"{stem}{i:02d}.skin"))
        if s.present and s.magic_ok:
            skins.append((i, s))
    lod = [(i, s) for i, s in skins if i > 0 and 0 < s.vertex_span < primary.vertex_span]
    extra = res.vertices - primary.vertex_span

    if lod and sum(s.vertex_span for _, s in lod) == extra:
        names = ", ".join(f"{stem}{i:02d}.skin" for i, _ in lod)
        loaded = [i for i, _ in lod if i < res.n_views]
        res.findings.append(Finding(check="lod_blocks", severity=Severity.WARN,
            detail=f"the M2 stores {res.vertices} vertices: skin00's {primary.vertex_span} plus "
                   f"{len(lod)} modern LOD block(s) ({' + '.join(str(s.vertex_span) for _, s in lod)}) "
                   f"used by {names}. Those skins still number vertices from 0, so on 3.3.5 they point "
                   f"into skin00's block. With nViews={res.n_views} the client doesn't load them and the "
                   f"{extra} extra vertices are simply unused - don't pack those skins or raise nViews "
                   "to include them."))
        if loaded:
            res.findings.append(Finding(check="lod_skin_misaligned", severity=Severity.FAIL,
                detail=f"nViews={res.n_views} makes the client load "
                       f"{', '.join(f'{stem}{i:02d}.skin' for i in loaded)}, a modern LOD skin whose "
                       "vertex numbers point into skin00's block - that LOD renders garbled. "
                       f"Set nViews to {min(loaded)} or less."))
        return

    if res.vertices > primary.vertex_span * _LOD_SPAN_RATIO:
        res.findings.append(Finding(check="lod_unmerged", severity=Severity.FAIL,
            detail=f"the M2 declares {res.vertices} vertices but skin00 only spans {primary.vertex_span}, "
                   "and the other skins don't account for the rest: an un-merged multi-LOD export. "
                   "Re-convert the RAW wow.export model with MultiConverter (Shadowlands-Wotlk if it has "
                   "a .skel, Legion-Wotlk if not) and check again; if it still mismatches, the LODs need "
                   "collapsing by hand."))


def _check_anims(res: CheckResult, folder: str, stem: str) -> None:
    """External sequences load <stem><id>-<sub>.anim at runtime; a missing one
    crashes the client the moment that animation plays."""
    if not res.anim_external:
        if res.anim_inline or res.anim_alias:
            res.findings.append(Finding(check="animations", severity=Severity.PASS,
                detail=f"{res.anim_inline} inline, {len(res.anim_alias)} alias, 0 external - "
                       "no .anim files to pack."))
        return
    missing, chunked = [], []
    for tag in sorted(res.anim_external):
        p = os.path.join(folder, f"{stem}{tag}.anim")
        if not os.path.isfile(p):
            missing.append(tag)
            continue
        with open(p, "rb") as fh:
            if fh.read(4) == b"AFM2":
                chunked.append(tag)
    if missing:
        res.findings.append(Finding(check="anim_missing", severity=Severity.FAIL,
            detail=f"{len(missing)} external .anim file(s) not found next to the model: "
                   f"{', '.join(missing)} (expected {stem}<id>-<sub>.anim). The client crashes when "
                   "one of those animations plays."))
    if chunked:
        res.findings.append(Finding(check="anim_format", severity=Severity.WARN,
            detail=f".anim file(s) {', '.join(chunked)} are Legion+ chunked (AFM2); a 3.3.5 client "
                   "expects raw pre-Legion .anim data. Convert them, or re-inline the animations."))
    present = len(res.anim_external) - len(missing)
    if present:
        res.findings.append(Finding(check="animations", severity=Severity.WARN,
            detail=f"{res.anim_inline} inline, {len(res.anim_alias)} alias, {len(res.anim_external)} external - "
                   f"pack the {present} .anim file(s) alongside the model."))


def format_report(res: CheckResult) -> str:
    """Plain-text summary (used by the CLI human view)."""
    lines = [
        f"version {res.version}   flags 0x{res.global_flags:X}   nViews {res.n_views}",
        f"bones {res.bones}   vertices {res.vertices}   materials {res.materials}   textures {len(res.textures)}",
    ]
    for t, p in res.textures:
        lines.append(f"  texture type {t} ({_TEXTURE_TYPES.get(t, '?')}): {p or '(runtime-filled)'}")
    for s in res.skins:
        if not s.present:
            lines.append(f"  {os.path.basename(s.path)}: MISSING")
        elif not s.magic_ok:
            lines.append(f"  {os.path.basename(s.path)}: not SKIN format")
        else:
            lines.append(f"  {os.path.basename(s.path)}: {len(s.geosets)} geosets, {s.triangles} tris, {s.vertices} verts  "
                         f"| {s.batches} batches shaders {sorted(set(s.shaders))}  | boneMax {s.bone_count_max}")
    return "\n".join(lines)
