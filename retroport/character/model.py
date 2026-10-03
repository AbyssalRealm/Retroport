"""Remapping a converted (MD20) character model's body-texture UVs between layouts.

Only vertices drawn with the composited body texture (texture type 1) move; hair
(type 6) and every other texture keep their UVs. Two things would make the remap
wrong, and both are counted rather than guessed at:

* a vertex shared by a skin-textured and a non-skin-textured submesh (moving it
  would shift the other texture) - left unchanged;
* a skin triangle whose corners fall in different remap groups (its interior would
  stretch across the seam) - reported.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import structs as S
from ..formats.md20 import read_texture_slots
from ..formats.verify import m2_problems, no_new_problems
from ..safeio import write_verified
from .layouts import Layout, RemapGroup, find_group, remap_uv

HDR_TEXTURE_LOOKUP = 0x80
VERTEX_SIZE = 48
VERTEX_UV0 = 32
SKIN_TEXTURE_TYPE = 1


@dataclass
class UVRemapReport:
    remapped: int = 0                 # skin-textured vertices moved
    outside: int = 0                  # skin vertices in no mapped section (unchanged)
    shared: int = 0                   # vertices also used by non-skin textures (unchanged)
    straddling: int = 0               # skin triangles spanning regions that move differently (e.g. body/face)
    per_group: dict[str, int] = field(default_factory=dict)


def _skin_usage(m2: bytes, skins: list[bytes]) -> tuple[set[int], set[int], list[tuple[int, int, int]]]:
    """(vertices sampled with the body texture, vertices sampled with anything else,
    skin-textured triangles as global vertex triples)."""
    slots = read_texture_slots(m2)
    lc, lo = S.read_m2array(m2, HDR_TEXTURE_LOOKUP)
    lookup = struct.unpack_from(f"<{lc}H", m2, lo) if lc else ()
    skin_v: set[int] = set()
    other_v: set[int] = set()
    tris: list[tuple[int, int, int]] = []
    for skin in skins:
        vc, vo = S.read_m2array(skin, S.SKIN_VERTICES)
        verts = struct.unpack_from(f"<{vc}H", skin, vo) if vc else ()
        ic, io = S.read_m2array(skin, S.SKIN_INDICES)
        idx = struct.unpack_from(f"<{ic}H", skin, io) if ic else ()
        sc, so = S.read_m2array(skin, S.SKIN_SUBMESHES)
        subs = [struct.unpack_from("<HHHHHH", skin, so + i * S.M2SKINSECTION) for i in range(sc)]
        bc, bo = S.read_m2array(skin, S.SKIN_BATCHES)
        kinds: dict[int, set[str]] = {}
        for i in range(bc):
            rec = bo + i * S.M2BATCH
            sub, = struct.unpack_from("<H", skin, rec + 0x04)
            tcount, tcombo = struct.unpack_from("<HH", skin, rec + 0x0E)
            for t in range(tcount):
                if tcombo + t < len(lookup) and lookup[tcombo + t] < len(slots):
                    is_skin = slots[lookup[tcombo + t]].type == SKIN_TEXTURE_TYPE
                    kinds.setdefault(sub, set()).add("skin" if is_skin else "other")
        for sub, k in kinds.items():
            if sub >= len(subs):
                continue
            _id, level, vstart, vcount, istart, icount = subs[sub]
            gverts = {verts[j] for j in range(vstart, min(vstart + vcount, len(verts)))}
            if "skin" in k:
                skin_v |= gverts
                start = istart + (level << 16)
                for j in range(start, min(start + icount, len(idx)) - 2, 3):
                    tris.append((verts[idx[j]], verts[idx[j + 1]], verts[idx[j + 2]]))
            if "other" in k:
                other_v |= gverts
    return skin_v, other_v, tris


def remap_model_uvs(m2_path: str, skin_paths: list[str], src: Layout, dst: Layout,
                    groups: list[RemapGroup]) -> UVRemapReport:
    """Rewrite UV0 of the body-textured vertices in an MD20 model (verified write)."""
    with open(m2_path, "rb") as fh:
        before = fh.read()
    if before[:4] != b"MD20":
        raise ValueError("UV remap needs a converted (MD20) model")
    skins = []
    for p in skin_paths:
        with open(p, "rb") as fh:
            skins.append(fh.read())
    skin_v, other_v, tris = _skin_usage(before, skins)
    data = bytearray(before)
    vc, vo = S.read_m2array(data, S.HDR_VERTICES)
    rep = UVRemapReport()
    uv = lambda g: struct.unpack_from("<2f", before, vo + g * VERTEX_SIZE + VERTEX_UV0)   # noqa: E731

    def transform(grp):
        # groups that move points the same way (e.g. layout 2's body sections, which keep
        # their pixel positions) form one continuous region; only crossings between
        # different transforms distort a triangle
        if grp is None:
            return None
        sx, sy = grp.dst.w / grp.src.w, grp.dst.h / grp.src.h
        return (round(sx, 6), round(sy, 6), round(grp.dst.x - grp.src.x * sx, 3), round(grp.dst.y - grp.src.y * sy, 3))

    for t in tris:
        if len({transform(find_group(*uv(g), src, groups)) for g in t if g < vc}) > 1:
            rep.straddling += 1
    for g in sorted(skin_v):
        if g >= vc:
            continue
        if g in other_v:
            rep.shared += 1
            continue
        u, v = uv(g)
        new = remap_uv(u, v, src, dst, groups)
        if new is None:
            rep.outside += 1
            continue
        grp = find_group(u, v, src, groups)
        name = "+".join(str(s) for s in grp.section_types)
        rep.per_group[name] = rep.per_group.get(name, 0) + 1
        struct.pack_into("<2f", data, vo + g * VERTEX_SIZE + VERTEX_UV0, *new)
        rep.remapped += 1
    write_verified(m2_path, bytes(data), no_new_problems(before, m2_problems))
    return rep
