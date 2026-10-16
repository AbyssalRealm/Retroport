"""Skin texture BfA paints outside every section of its layout, and 3.3.5's layout has no room for.

BfA's layout 2 (1024x512) leaves its bottom-left corner, under the arms and hands, to no section:
a Pandaren paints its toes and claws, paw pads and tail there, and (in the face half) its teeth
and tongue. The model samples it with the body texture (type 1), but 3.3.5's layout - every pixel
of it some section's - has nowhere to put it, and left where they were those UVs land in 3.3.5's
face: a Pandaren's toes showed its eyes.

So those triangles get a texture of their own, the one per-skin-colour texture 3.3.5 draws on a
character directly: the skin row's second, "extra" one (texture type 8, a Tauren's fur). Their
triangles move to a submesh of their own (same geoset, its own window of vertices right after
the rest, as WarcraftXL's wide indices and the client's per-submesh vertex compaction expect),
drawn by a copy of the body batch on a new type-8 texture slot, their UVs mapped into the corner
(``ExtraRegion.rect``), which the texture conversion cuts out of every skin colour's texture.

A race that already draws something with type 8 can give it up only when that texture is its hair
(a Pandaren's hair batches use the skin row's extra, which is each colour's hair texture): those
slots become hair (type 6), drawn from the hair row - the same textures, chosen by Hair Color.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import structs as S
from ..formats.md20 import read_texture_slots
from ..formats.verify import m2_problems, no_new_problems, skin_problems
from ..safeio import write_verified
from .layouts import Layout, Rect, RemapGroup, find_group
from .model import SKIN_TEXTURE_TYPE, VERTEX_SIZE, VERTEX_UV0, _skin_usage

EXTRA_TEXTURE_TYPE = 8
HAIR_TEXTURE_TYPE = 6
HDR_REPLACEABLE_LOOKUP = 0x68
SKIN_BONES = 0x14
SNAP = 64                       # the corner, in source-layout pixels, rounded out to this grid
BATCH_SECTION, BATCH_GEOSET, BATCH_TEX_COUNT, BATCH_TEX_COMBO = 0x04, 0x06, 0x0E, 0x10
# M2SkinSection: id, level (the index start's high half), vertexStart, vertexCount, indexStart, indexCount
SEC_LEVEL, SEC_VERTEX_COUNT, SEC_INDEX_COUNT = 0x02, 0x06, 0x0A


class ExtrasError(ValueError):
    pass


@dataclass
class ExtraRegion:
    rect: Rect | None = None            # source-layout pixels the extra texture is cut from
    vertices: int = 0
    triangles: int = 0
    submeshes: int = 0                  # submeshes split off (or turned over whole)
    hair_slots: list[int] = field(default_factory=list)     # type-8 slots that became hair
    skipped: int = 0                    # triangles left on the body texture (mixed or out of window)

    @property
    def found(self) -> bool:
        return self.rect is not None


def outside_vertices(m2: bytes, skins: list[bytes], src: Layout, groups: list[RemapGroup]) -> dict[int, tuple[float, float]]:
    """Body-textured vertices in no section of ``src``: global vertex -> its UV in source pixels."""
    skin_v, _other, _tris = _skin_usage(m2, skins)
    vc, vo = S.read_m2array(m2, S.HDR_VERTICES)
    out = {}
    for g in sorted(skin_v):
        if g >= vc:
            continue
        u, v = struct.unpack_from("<2f", m2, vo + g * VERTEX_SIZE + VERTEX_UV0)
        if find_group(u, v, src, groups) is None:
            out[g] = (u * src.width, v * src.height)
    return out


def region_rect(points: list[tuple[float, float]], src: Layout, snap: int = SNAP) -> Rect:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0 = int(min(xs) // snap * snap), int(min(ys) // snap * snap)
    x1, y1 = -int(-max(xs) // snap * snap), -int(-max(ys) // snap * snap)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(src.width, max(x1, x0 + snap)), min(src.height, max(y1, y0 + snap))
    return Rect(x0, y0, x1 - x0, y1 - y0)


def _append(buf: bytearray, payload: bytes) -> int:
    buf.extend(bytes(-len(buf) % 16))
    at = len(buf)
    buf.extend(payload)
    return at


def _texture_slots(m2: bytearray, hair_ok: bool, region: ExtraRegion) -> tuple[int, int]:
    """Turn the type-8 slots into hair (when ``hair_ok``) and add the extra-texture slot and its
    texture-lookup entry: (new slot, its texture-lookup index)."""
    slots = read_texture_slots(bytes(m2))
    extra = [s.index for s in slots if s.type == EXTRA_TEXTURE_TYPE]
    if extra and not hair_ok:
        raise ExtrasError(f"the model already draws texture type {EXTRA_TEXTURE_TYPE} (slots {extra}), and not "
                          "with its hair textures")
    n, off = S.read_m2array(m2, S.HDR_TEXTURES)
    for i in extra:
        struct.pack_into("<I", m2, off + i * S.M2TEXTURE, HAIR_TEXTURE_TYPE)
    region.hair_slots = extra
    body = next((s for s in slots if s.type == SKIN_TEXTURE_TYPE), None)
    if body is None:
        raise ExtrasError("the model has no body texture slot")
    entry = struct.pack("<II", EXTRA_TEXTURE_TYPE, body.flags) + bytes(S.M2TEXTURE - 8)
    new_slot = n
    S.write_m2array(m2, S.HDR_TEXTURES, n + 1, _append(m2, bytes(m2[off:off + n * S.M2TEXTURE]) + entry))
    lc, lo = S.read_m2array(m2, S.HDR_TEXTURE_LOOKUP)
    combos = list(struct.unpack_from(f"<{lc}H", m2, lo)) + [new_slot]
    S.write_m2array(m2, S.HDR_TEXTURE_LOOKUP, lc + 1, _append(m2, struct.pack(f"<{lc + 1}H", *combos)))
    # replaceable-texture lookup: type -> the slot the client fills
    rc, ro = S.read_m2array(m2, HDR_REPLACEABLE_LOOKUP)
    lookup = list(struct.unpack_from(f"<{rc}h", m2, ro)) if rc else []
    lookup += [-1] * (EXTRA_TEXTURE_TYPE + 1 - len(lookup))
    if extra:
        lookup[HAIR_TEXTURE_TYPE] = extra[0]
    lookup[EXTRA_TEXTURE_TYPE] = new_slot
    if len(lookup) == rc:
        struct.pack_into(f"<{rc}h", m2, ro, *lookup)
    else:
        S.write_m2array(m2, HDR_REPLACEABLE_LOOKUP, len(lookup), _append(m2, struct.pack(f"<{len(lookup)}h", *lookup)))
    return new_slot, lc


def split_skin(skin: bytes, outside: set[int], body_combos: set[int], extra_combo: int,
               region: ExtraRegion) -> bytes:
    """The triangles of ``outside`` vertices onto submeshes of their own, right after their own,
    drawn by copies of the batches that drew them with the body texture (``body_combos``: texture
    lookup indices of the body slot) on ``extra_combo``."""
    data = bytearray(skin)
    nv, ov = S.read_m2array(data, S.SKIN_VERTICES)
    ni, oi = S.read_m2array(data, S.SKIN_INDICES)
    nb_, ob_ = S.read_m2array(data, SKIN_BONES)
    ns, os_ = S.read_m2array(data, S.SKIN_SUBMESHES)
    nb, obt = S.read_m2array(data, S.SKIN_BATCHES)
    if nv > 0xFFFF:
        raise ExtrasError("a skin with more than 65,535 vertices: its submeshes' first vertices can't be moved")
    lookup = list(struct.unpack_from(f"<{nv}H", data, ov))
    indices = list(struct.unpack_from(f"<{ni}H", data, oi))
    bones = [bytes(data[ob_ + 4 * k:ob_ + 4 * k + 4]) for k in range(nb_)]
    subs = [bytearray(data[os_ + i * S.M2SKINSECTION:os_ + (i + 1) * S.M2SKINSECTION]) for i in range(ns)]
    batches = [bytearray(data[obt + i * S.M2BATCH:obt + (i + 1) * S.M2BATCH]) for i in range(nb)]

    new_subs: list[bytearray] = []
    moved: dict[int, int] = {}              # old submesh index -> new index
    extra_of: dict[int, int] = {}           # old submesh index -> new index of its extra part
    whole: set[int] = set()                 # submeshes drawn from the extra texture whole
    for i, sub in enumerate(subs):
        _sid, level, vs, vcount, istart, icount = struct.unpack_from("<6H", sub)
        first = (level << 16) | istart
        tris = [tuple(indices[first + k:first + k + 3]) for k in range(0, icount - icount % 3, 3)]
        ins, outs = [], []
        for t in tris:
            n_out = sum(1 for x in t if x < nv and lookup[x] in outside)
            (outs if n_out == 3 else ins).append(t)
            if 0 < n_out < 3:
                region.skipped += 1
        moved[i] = len(new_subs)
        new_subs.append(sub)
        if not outs:
            continue
        region.triangles += len(outs)
        if not ins:
            whole.add(i)
            region.submeshes += 1
            continue
        window = range(vs, vs + vcount)
        used_in = {x for t in ins for x in t}
        used_out = {x for t in outs for x in t}
        if used_in & used_out or not all(x in window for x in used_in | used_out):
            region.skipped += len(outs)
            region.triangles -= len(outs)
            continue
        # the window's vertices: the body's first, the extra part's after, each in its own order
        order = [x for x in window if x not in used_out] + [x for x in window if x in used_out]
        remap = {old: vs + k for k, old in enumerate(order)}
        lookup[vs:vs + vcount] = [lookup[x] for x in order]
        if nb_ == nv:
            bones[vs:vs + vcount] = [bones[x] for x in order]
        for k, x in enumerate(indices):
            if x in remap:
                indices[k] = remap[x]
        flat = [x for t in ins + outs for x in t]
        flat = [remap[x] for x in flat]
        indices[first:first + 3 * len(ins) + 3 * len(outs)] = flat
        n_body = vcount - len(used_out)
        part = bytearray(sub)
        struct.pack_into("<H", sub, SEC_VERTEX_COUNT, n_body)
        struct.pack_into("<H", sub, SEC_INDEX_COUNT, 3 * len(ins))
        start = first + 3 * len(ins)
        struct.pack_into("<HHHHH", part, SEC_LEVEL, start >> 16, vs + n_body, len(used_out), start & 0xFFFF,
                         3 * len(outs))
        extra_of[i] = len(new_subs)
        new_subs.append(part)
        region.submeshes += 1

    new_batches: list[bytearray] = []
    for b in batches:
        sec = struct.unpack_from("<H", b, BATCH_SECTION)[0]
        geo = struct.unpack_from("<H", b, BATCH_GEOSET)[0]
        count, combo = struct.unpack_from("<HH", b, BATCH_TEX_COUNT)
        is_body = combo in body_combos
        if sec in whole and is_body:
            struct.pack_into("<HH", b, BATCH_TEX_COUNT, 1, extra_combo)
        struct.pack_into("<H", b, BATCH_SECTION, moved.get(sec, sec))
        struct.pack_into("<H", b, BATCH_GEOSET, moved.get(geo, geo))
        new_batches.append(b)
        if sec in extra_of and is_body:
            copy = bytearray(b)
            struct.pack_into("<H", copy, BATCH_SECTION, extra_of[sec])
            struct.pack_into("<H", copy, BATCH_GEOSET, extra_of[sec])
            struct.pack_into("<HH", copy, BATCH_TEX_COUNT, 1, extra_combo)
            new_batches.append(copy)

    struct.pack_into(f"<{nv}H", data, ov, *lookup)
    struct.pack_into(f"<{ni}H", data, oi, *indices)
    for k, b4 in enumerate(bones):
        data[ob_ + 4 * k:ob_ + 4 * k + 4] = b4
    S.write_m2array(data, S.SKIN_SUBMESHES, len(new_subs), _append(data, b"".join(new_subs)))
    S.write_m2array(data, S.SKIN_BATCHES, len(new_batches), _append(data, b"".join(new_batches)))
    return bytes(data)


def split_extra_region(m2_path: str, skin_paths: list[str], src: Layout, groups: list[RemapGroup], *,
                       hair_ok: bool) -> ExtraRegion:
    """Give the triangles BfA draws from outside every section of ``src`` the skin row's extra
    texture (module doc). Writes the model and skins (verified); returns what it did - nothing
    (``found`` False) for a model with no such triangles."""
    with open(m2_path, "rb") as fh:
        before = fh.read()
    skins = []
    for p in skin_paths:
        with open(p, "rb") as fh:
            skins.append(fh.read())
    region = ExtraRegion()
    points = outside_vertices(before, skins, src, groups)
    if not points:
        return region
    rect = region_rect(list(points.values()), src)
    m2 = bytearray(before)
    body_slots = {s.index for s in read_texture_slots(before) if s.type == SKIN_TEXTURE_TYPE}
    lc, lo = S.read_m2array(before, S.HDR_TEXTURE_LOOKUP)
    body_combos = {k for k, slot in enumerate(struct.unpack_from(f"<{lc}H", before, lo)) if slot in body_slots}
    _slot, extra_combo = _texture_slots(m2, hair_ok, region)
    vc, vo = S.read_m2array(before, S.HDR_VERTICES)
    for g, (px, py) in points.items():
        struct.pack_into("<2f", m2, vo + g * VERTEX_SIZE + VERTEX_UV0,
                         (px - rect.x) / rect.w, (py - rect.y) / rect.h)
    outside = set(points)
    new_skins = []
    for skin in skins:
        new_skins.append(split_skin(skin, outside, body_combos, extra_combo, region))
    region.rect, region.vertices = rect, len(points)
    write_verified(m2_path, bytes(m2), no_new_problems(before, m2_problems))
    for p, old, new in zip(skin_paths, skins, new_skins):
        if new != old:
            write_verified(p, new, no_new_problems(old, skin_problems))
    return region
