"""Belt buckles: the model BfA hangs on a belt, drawn on 3.3.5 as part of the wearer's body.

BfA draws a belt's model (ItemDisplayInfo.ModelResourcesID) on the wearer's BeltBuckle attachment
(53), in the belt's own texture (ModelMaterialResourcesID). Every Pandaren belt is a rope band - a
belt geoset of the body (1803), painted from the belt's component textures - and one knot,
buckle_panstart_a_01 (a jade disc in coils of rope, a tassel), in the belt's colours. 3.3.5 draws
no model for a belt: the band showed holes where the knot goes (client test).

What 3.3.5 does draw for a belt is a belt geoset of the body: 1801 + the display's first geoset
group, any value (0x4EE088 hides 1800-1899, then shows that one). So each knot a race's belts wear,
in each texture, becomes a belt geoset of its bodies, values from ``FIRST_VALUE``: the belt's own
band (BfA's geoset for it, its triangles again) and the model's triangles, carried to the
attachment and weighted to its bone, drawn in the belt's texture through a type-0 slot naming it.
The reflection BfA adds over it (a second texture, shader 0x8002) is left out, as WarcraftXL's
helmets leave a reflection 3.3.5 can't combine. The belt's display then names that geoset.

The bake only appends - vertices, texture slots and lookup entries in copies of those arrays at the
end of the model; skin vertices, bone indices, triangles, submeshes and batches the same way at the
end of each skin - and points the headers at the copies. ``unbake`` (from ``record``) restores a
body exactly, its length and header, before a rebuild bakes it again. Each new submesh gets a
window of vertices of its own, as the body's own submeshes have (extras.py).
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field

from ..formats import structs as S

ATTACHMENT = 53                     # BeltBuckle
BELT = 1801                         # a belt geoset: BELT + the display's first geoset group
FIRST_VALUE = 10                    # BfA's own belt values (a Pandaren band: 2) stay its own
WAIST = 6                           # InventoryType
ITEM_TEXTURE = 2                    # M2Texture type: the item's own texture
BODY_TEXTURE = 1
VERTEX, SUBMESH, BATCH, TEXTURE, ATTACH, BONE = 48, 48, 24, 16, 40, 0x58
HDR_MATERIALS, HDR_BONE_COMBOS = 0x70, 0x78
SKIN_BONES = 0x14
M2_HEAD, SKIN_HEAD = 0x140, 0x30    # header bytes ``record`` keeps (more than either header)
MAX_WINDOW = 0xFFFF                 # a submesh's first vertex is a uint16


class BucklesError(ValueError):
    pass


@dataclass
class Buckle:
    model: int                      # BfA's model (FileDataID)
    texture: int                    # the belt's texture for it (FileDataID)
    band: int                       # BfA's belt geoset value for the belt, its rope band; 0: none
    value: int = 0                  # the belt geoset it becomes: BELT + value
    texture_name: str = ""          # the client file its texture slot names
    items: list[int] = field(default_factory=list)          # the items wearing it


@dataclass
class Mesh:
    """A model's triangles drawn in its item texture, each vertex once (VERTEX bytes each)."""
    vertices: bytes
    indices: list[int]
    flags: int = 0                  # the item texture slot's (wrapping)

    @property
    def count(self) -> int:
        return len(self.vertices) // VERTEX


def plan(pieces: list[tuple[int, int, int, int]]) -> list[Buckle]:
    """Each (model, texture, band) the pieces (item id, model, texture, band) wear, a belt geoset
    value each from FIRST_VALUE, in item id order."""
    out: dict[tuple[int, int, int], Buckle] = {}
    for item, model, texture, band in sorted(pieces):
        key = (model, texture, band)
        if key not in out:
            out[key] = Buckle(model, texture, band, FIRST_VALUE + len(out))
        out[key].items.append(item)
    return list(out.values())


def read_mesh(model: bytes, skin: bytes) -> Mesh:
    """A BfA model's triangles that its batches draw in the item's texture (type 2), from a skin."""
    from wotlkconv.m2.model import parse_m2
    from wotlkconv.m2.skin import parse_skin

    m = parse_m2(model)
    s = parse_skin(skin)
    item = {i for i, t in enumerate(m.textures) if t["type"] == ITEM_TEXTURE}
    drawn = set()
    for b in s.batches:
        section, = struct.unpack_from("<H", b, 0x04)
        combo, = struct.unpack_from("<H", b, 0x10)
        if combo < len(m.texture_combos) and m.texture_combos[combo] in item:
            drawn.add(section)
    if not drawn:
        raise BucklesError("the model draws nothing in the item's texture")
    order: list[int] = []
    local: dict[int, int] = {}
    indices: list[int] = []
    for k in sorted(drawn):
        _sid, level, _vs, _vc, start, count = struct.unpack_from("<6H", s.submeshes[k])
        first = (level << 16) | start
        for i in s.indices[first:first + count]:
            g = s.vertices[i]
            if g not in local:
                local[g] = len(order)
                order.append(g)
            indices.append(local[g])
    vertices = b"".join(m.vertices[g * VERTEX:(g + 1) * VERTEX] for g in order)
    return Mesh(vertices, indices, next((m.textures[i]["flags"] for i in sorted(item)), 0))


def attachment(m2: bytes, aid: int = ATTACHMENT) -> tuple[int, tuple[float, float, float]] | None:
    """(bone, position) of an MD20 model's attachment ``aid``."""
    n, off = S.read_m2array(m2, S.HDR_ATTACHMENTS)
    for i in range(n):
        got, bone = struct.unpack_from("<IH", m2, off + i * ATTACH)
        if got == aid:
            return bone, struct.unpack_from("<3f", m2, off + i * ATTACH + 8)
    return None


def _append(buf: bytearray, payload: bytes) -> int:
    buf.extend(bytes(-len(buf) % 16))
    at = len(buf)
    buf.extend(payload)
    return at


def _grow(buf: bytearray, header: int, size: int, payload: bytes) -> int:
    """Copy an array to the end with ``payload`` after it; the index of its first new entry."""
    n, off = S.read_m2array(buf, header)
    S.write_m2array(buf, header, n + len(payload) // size, _append(buf, bytes(buf[off:off + n * size]) + payload))
    return n


def _carried(mesh: Mesh, bone: int, at: tuple[float, float, float]) -> tuple[bytes, list[tuple[float, ...]]]:
    """The mesh's vertices at the attachment (bind pose: its position), wholly on its bone."""
    out = bytearray()
    points = []
    for k in range(mesh.count):
        v = bytearray(mesh.vertices[k * VERTEX:(k + 1) * VERTEX])
        p = tuple(a + b for a, b in zip(struct.unpack_from("<3f", v, 0), at))
        struct.pack_into("<3f4B4B", v, 0, *p, 255, 0, 0, 0, bone, 0, 0, 0)
        out += v
        points.append(p)
    return bytes(out), points


def _texture_slots(m2: bytearray, slots: list[tuple[str, int]]) -> int:
    """Type-0 texture slots naming files (name, flags); the index of the first."""
    entries = bytearray()
    for name, flags in slots:
        raw = name.encode("latin-1") + b"\0"
        entries += struct.pack("<IIII", 0, flags, len(raw), _append(m2, raw))
    return _grow(m2, S.HDR_TEXTURES, TEXTURE, bytes(entries))


def _material(m2: bytearray, flags: int = 0, blend: int = 0) -> int:
    n, off = S.read_m2array(m2, HDR_MATERIALS)
    for i in range(n):
        if struct.unpack_from("<HH", m2, off + 4 * i) == (flags, blend):
            return i
    return _grow(m2, HDR_MATERIALS, 4, struct.pack("<HH", flags, blend))


def _body_batch(m2: bytes, batches: list[bytes]) -> bytes:
    """A batch drawing the body texture alone: its texture coordinate, weight and transform
    lookups are the ones a buckle's batch uses."""
    nt, ot = S.read_m2array(m2, S.HDR_TEXTURES)
    body = {i for i in range(nt) if struct.unpack_from("<I", m2, ot + i * TEXTURE)[0] == BODY_TEXTURE}
    nl, ol = S.read_m2array(m2, S.HDR_TEXTURE_LOOKUP)
    lookup = struct.unpack_from(f"<{nl}H", m2, ol)
    for b in batches:
        count, combo = struct.unpack_from("<HH", b, 0x0E)
        if count == 1 and combo < nl and lookup[combo] in body:
            return b
    raise BucklesError("no batch draws the body texture")


def _bake_skin(skin: bytes, buckles: list[Buckle], meshes: dict[int, Mesh], first: dict[int, int],
               centres: dict[int, tuple[tuple[float, ...], float]], bone: int, palette: int, textures: int,
               material: int, template: bytes) -> bytes:
    out = bytearray(skin)
    nv, ov = S.read_m2array(out, S.SKIN_VERTICES)
    ni, oi = S.read_m2array(out, S.SKIN_INDICES)
    _nb, ob = S.read_m2array(out, SKIN_BONES)
    ns, os_ = S.read_m2array(out, S.SKIN_SUBMESHES)
    nt, ot = S.read_m2array(out, S.SKIN_BATCHES)
    lookup = list(struct.unpack_from(f"<{nv}H", out, ov))
    bones = bytearray(out[ob:ob + 4 * nv])
    indices = list(struct.unpack_from(f"<{ni}H", out, oi))
    subs = [bytes(out[os_ + i * SUBMESH:os_ + (i + 1) * SUBMESH]) for i in range(ns)]
    batches = [bytes(out[ot + i * BATCH:ot + (i + 1) * BATCH]) for i in range(nt)]
    new_subs: list[bytes] = []
    new_batches: list[bytes] = []

    def window(local: list[int], bone_ix: bytes, tris: list[int]) -> tuple[int, int]:
        """New vertices (global ids, their skin bone indices) and triangles over them, numbered
        from 0: (first vertex, first index)."""
        vs, start = len(lookup), len(indices)
        if vs + len(local) > MAX_WINDOW:
            raise BucklesError(f"the skin would pass {MAX_WINDOW} vertices")
        lookup.extend(local)
        bones.extend(bone_ix)
        indices.extend(vs + i for i in tris)
        return vs, start

    for k, b in enumerate(buckles):
        geoset = BELT + b.value
        if b.band:
            for si in range(ns):
                sid, level, vs, vc, start, count = struct.unpack_from("<6H", subs[si])
                if sid != BELT + b.band:
                    continue
                at = (level << 16) | start
                tris = [i - vs for i in indices[at:at + count]]
                if any(not 0 <= i < vc for i in tris):
                    raise BucklesError(f"the band's submesh {si} draws vertices outside its window")
                nvs, nstart = window(lookup[vs:vs + vc], bytes(bones[4 * vs:4 * (vs + vc)]), tris)
                sub = bytearray(subs[si])
                struct.pack_into("<6H", sub, 0, geoset, nstart >> 16, nvs, vc, nstart & 0xFFFF, count)
                copy_of = ns + len(new_subs)
                new_subs.append(bytes(sub))
                for bt in batches:
                    section, geo = struct.unpack_from("<HH", bt, 0x04)
                    if section == si:
                        bt = bytearray(bt)
                        struct.pack_into("<HH", bt, 0x04, copy_of, copy_of if geo == si else geo)
                        new_batches.append(bytes(bt))
        mesh = meshes[b.model]
        nvs, nstart = window([first[b.model] + i for i in range(mesh.count)], bytes(4 * mesh.count), mesh.indices)
        centre, radius = centres[b.model]
        here = ns + len(new_subs)
        new_subs.append(struct.pack("<10H3f3ff", geoset, nstart >> 16, nvs, mesh.count, nstart & 0xFFFF,
                                    len(mesh.indices), 1, palette, 1, bone, *centre, *centre, radius))
        bt = bytearray(template)
        struct.pack_into("<BbHHHhHHHH", bt, 0, 0, 0, 0, here, here, -1, material, 0, 1, textures + k)
        new_batches.append(bytes(bt))

    S.write_m2array(out, S.SKIN_VERTICES, len(lookup), _append(out, struct.pack(f"<{len(lookup)}H", *lookup)))
    S.write_m2array(out, S.SKIN_INDICES, len(indices), _append(out, struct.pack(f"<{len(indices)}H", *indices)))
    S.write_m2array(out, SKIN_BONES, len(lookup), _append(out, bytes(bones)))
    S.write_m2array(out, S.SKIN_SUBMESHES, ns + len(new_subs), _append(out, b"".join(subs + new_subs)))
    S.write_m2array(out, S.SKIN_BATCHES, nt + len(new_batches), _append(out, b"".join(batches + new_batches)))
    return bytes(out)


def bake(m2: bytes, skins: list[bytes], buckles: list[Buckle], meshes: dict[int, Mesh]) -> tuple[bytes, list[bytes]]:
    """A body (MD20 model and skins) wearing each buckle as a belt geoset of its own (module doc)."""
    if not buckles:
        return m2, list(skins)
    found = attachment(m2)
    if found is None:
        raise BucklesError(f"the model has no BeltBuckle attachment ({ATTACHMENT})")
    bone, at = found
    if bone > 0xFF:
        raise BucklesError(f"the attachment's bone {bone} doesn't fit a vertex's bone index")
    taken = set().union(*(baked_values(s) for s in skins))
    if taken:
        raise BucklesError(f"the model already wears buckles (belt geoset values {sorted(taken)})")
    out = bytearray(m2)
    vc, vo = S.read_m2array(out, S.HDR_VERTICES)
    first: dict[int, int] = {}
    centres: dict[int, tuple[tuple[float, ...], float]] = {}
    new = bytearray()
    for model in dict.fromkeys(b.model for b in buckles):
        first[model] = vc + len(new) // VERTEX
        data, points = _carried(meshes[model], bone, at)
        new += data
        centre = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
        centres[model] = centre, max(math.dist(p, centre) for p in points)
    S.write_m2array(out, S.HDR_VERTICES, vc + len(new) // VERTEX,
                    _append(out, bytes(out[vo:vo + vc * VERTEX]) + bytes(new)))
    palette = _grow(out, HDR_BONE_COMBOS, 2, struct.pack("<H", bone))
    slot = _texture_slots(out, [(b.texture_name, meshes[b.model].flags) for b in buckles])
    textures = _grow(out, S.HDR_TEXTURE_LOOKUP, 2, struct.pack(f"<{len(buckles)}H",
                                                                *range(slot, slot + len(buckles))))
    material = _material(out)
    new_skins = []
    for skin in skins:
        nt, ot = S.read_m2array(skin, S.SKIN_BATCHES)
        template = _body_batch(bytes(out), [skin[ot + i * BATCH:ot + (i + 1) * BATCH] for i in range(nt)])
        new_skins.append(_bake_skin(skin, buckles, meshes, first, centres, bone, palette, textures, material,
                                    template))
    return bytes(out), new_skins


def baked_values(skin: bytes) -> set[int]:
    """The belt geoset values at or past FIRST_VALUE a skin has: a bake's."""
    ns, os_ = S.read_m2array(skin, S.SKIN_SUBMESHES)
    ids = {struct.unpack_from("<H", skin, os_ + i * SUBMESH)[0] for i in range(ns)}
    return {g - BELT for g in ids if BELT + FIRST_VALUE <= g < BELT + 99}


def record(before: bytes, after: bytes, head: int) -> dict:
    """What ``unbake`` needs to give back ``before`` from ``after``."""
    return {"length": len(before), "head": before[:head].hex(), "sha256": hashlib.sha256(after).hexdigest()}


def unbake(data: bytes, rec: dict) -> bytes | None:
    """The file as it was before the bake ``rec`` records, or None when ``data`` isn't that bake's."""
    if hashlib.sha256(data).hexdigest() != rec.get("sha256"):
        return None
    head = bytes.fromhex(rec["head"])
    out = bytearray(data[:rec["length"]])
    out[:len(head)] = head
    return bytes(out)
