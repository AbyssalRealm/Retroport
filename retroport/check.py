"""WotLK compatibility checker.

Reads a *converted* (MD20) model and its .skin files and reports the things that
make a model fail or render wrong on a 3.3.5a client - turning "it's black and
missing parts" into a concrete checklist. This runs on MultiConverter output, not
the modern source.

What it surfaces:
  * nViews (how many .skin files the client will load) vs. which are present,
  * the texture-combiner-combos flag (0x08) - the usual cause of all-black
    meshes, since the client then reads M2Batch.shader as a combo-table index,
  * bone count vs. WotLK's practical ceiling,
  * texture list with type (type != 0 = filled at runtime, normal for character
    skin/hair; type 0 with an empty path = a real problem),
  * per-skin geoset list (skinSectionId) - directly explains missing body parts,
  * batch shader_ids (0x8000 = not rendered).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from .models import Finding, Severity, ValidationReport
from .formats import structs as S

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


@dataclass
class SkinInfo:
    path: str
    present: bool = True
    magic_ok: bool = True
    geosets: list[int] = field(default_factory=list)
    vertices: int = 0
    triangles: int = 0
    batches: int = 0
    shaders: list[int] = field(default_factory=list)
    bone_count_max: int = 0


@dataclass
class CheckResult:
    asset: str
    version: int | None = None
    global_flags: int = 0
    combiner_combos: bool = False
    combiner_combo_count: int = 0
    n_views: int = 0
    bones: int = 0
    vertices: int = 0
    materials: int = 0
    textures: list[tuple[int, str]] = field(default_factory=list)  # (type, path)
    skins: list[SkinInfo] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def report(self) -> ValidationReport:
        return ValidationReport(asset=self.asset, findings=self.findings)


def _data_block(data: bytes) -> bytes:
    """Return the MD20-layout bytes whether the file is raw MD20 or MD21-wrapped."""
    if data[:4] == b"MD20":
        return data
    if data[:4] == b"MD21":
        (size,) = struct.unpack_from("<I", data, 4)
        return data[8:8 + size]
    # maybe MD21 isn't first; scan
    pos = 0
    while pos + 8 <= len(data):
        magic = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if magic == b"MD21":
            return data[pos + 8:pos + 8 + size]
        pos += 8 + size
    raise ValueError("not an M2 (no MD20/MD21)")


def _read_textures(block: bytes) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    cnt, off = S.read_m2array(block, S.HDR_TEXTURES)
    for i in range(cnt):
        rec = off + i * S.M2TEXTURE
        if rec + S.M2TEXTURE > len(block):
            break
        ttype = S.read_u32(block, rec + S.TEXTURE_TYPE)
        fn_cnt, fn_off = S.read_m2array(block, rec + S.TEXTURE_FILENAME)
        path = ""
        if fn_cnt and fn_off + fn_cnt <= len(block):
            path = block[fn_off:fn_off + fn_cnt].split(b"\x00", 1)[0].decode("ascii", "replace")
        out.append((ttype, path))
    return out


def analyze_m2(path: str) -> CheckResult:
    with open(path, "rb") as fh:
        block = _data_block(fh.read())
    res = CheckResult(asset=path)
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

    # findings
    if res.version not in (256, 257, 260, 261, 262, 263, 264):
        res.findings.append(Finding(check="version", severity=Severity.WARN,
                                    detail=f"version {res.version} is not a WotLK-era value (expected 264); did MultiConverter run?"))
    if res.combiner_combos:
        res.findings.append(Finding(check="combiner_combos", severity=Severity.WARN,
            detail=f"global flag 0x08 set: M2Batch.shader is read as a combiner-combo index ({res.combiner_combo_count} combos). "
                   "If these are wrong/empty, meshes render black. Clearing 0x08 and resetting batch shaders to 0 is the usual fix."))
    if res.bones > _WOTLK_BONE_CEILING:
        res.findings.append(Finding(check="bone_count", severity=Severity.WARN,
            detail=f"{res.bones} bones exceeds WotLK's ~{_WOTLK_BONE_CEILING} palette ceiling; may need bone reduction in Blender."))
    bad_tex = [p for t, p in res.textures if t == 0 and not p]
    if bad_tex:
        res.findings.append(Finding(check="texture_path", severity=Severity.WARN,
            detail=f"{len(bad_tex)} hardcoded (type 0) texture(s) have no path; texture references unresolved."))
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
    v_cnt, _ = S.read_m2array(data, S.SKIN_VERTICES)
    i_cnt, _ = S.read_m2array(data, S.SKIN_INDICES)
    info.vertices = v_cnt
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
        else:
            runtime_shaders = [s for s in primary.shaders if not (s & 0x8000)]
            if runtime_shaders:
                res.findings.append(Finding(check="shaders", severity=Severity.WARN,
                    detail=f"batch shader values {sorted(set(primary.shaders))} are combiner-combo indices (flag 0x08); "
                           "verify they're in range or the batches go black."))
    return res


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
