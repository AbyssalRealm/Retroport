"""Close the seams between a ported character's geosets.

Where two geosets meet - the body and a chest, pants or belt piece, a scalp and a hairstyle -
their edge vertices sit at the same rest position, but a BfA model doesn't always give the two
the same bone weights. At rest they coincide; animated they drift apart and the seam opens: a
Kul Tiran male showed a hairline of the ground through his waist where the shirt meets the
trousers, the body and the chest, pants and belt pieces 0.002-0.003 apart in the stand pose.

Every vertex that shares a rest position with a vertex of a lower geoset id takes that
vertex's weights - the body's (geoset 0) where it is one of them - so the pieces move as one.
Only geosets that are shown together are joined: the body goes with everything, any other
geoset with every group but its own, whose ids are alternatives (one hairstyle, one beard).
A vertex only takes bones its submesh's palette has, with no more of them than the submesh's
bone influences, in every skin profile; its palette slots there are rewritten to match. One
that would need more is left as it is and counted.

M2 (12340): 0x3C vertices (48 each: +0 position, +12 weights[4], +16 bone indices[4]), 0x78
bone lookup (the palettes). Skin: 0x04 vertex lookup (skin vertex -> model vertex), 0x14 palette
slots (4 bytes per skin vertex, one per weight), 0x1C submeshes (48 each: +0 id, +4 vertex
start, +6 vertex count, +0xC bone count, +0xE bone combo index = palette start, +0x10 bone
influences).
"""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass

VERTEX_SIZE = 48
DECIMALS = 4            # rest positions equal to this many decimals are one seam point


@dataclass
class SeamReport:
    welded: int = 0      # vertices given their neighbour's weights
    blocked: int = 0     # vertices whose palette lacks the neighbour's bones


def _shown_together(a: int, b: int) -> bool:
    return a == 0 or b == 0 or a // 100 != b // 100


def _weights(m2: bytes | bytearray, at: int) -> dict[int, int]:
    w, b = m2[at + 12:at + 16], m2[at + 16:at + 20]
    return {b[k]: w[k] for k in range(4) if w[k]}


def weld_seams(m2: bytes, skins: list[bytes]) -> tuple[bytes, list[bytes], SeamReport]:
    """(model, skins, report): coincident vertices of different geosets given the weights of the
    lowest geoset's vertex among them. Running it again changes nothing."""
    if m2[:4] != b"MD20":
        raise ValueError("not an MD20 model")
    n_v, o_v = struct.unpack_from("<II", m2, 0x3C)
    n_bl, o_bl = struct.unpack_from("<II", m2, 0x78)
    bone_lookup = struct.unpack_from(f"<{n_bl}H", m2, o_bl)

    geoset: dict[int, int] = {}
    # model vertex -> every (skin, palette-slot offset, palette, influences) it is drawn through
    uses: dict[int, list[tuple[int, int, tuple[int, ...], int]]] = defaultdict(list)
    for si, skin in enumerate(skins):
        n_l, o_l = struct.unpack_from("<II", skin, 0x04)
        n_p, o_p = struct.unpack_from("<II", skin, 0x14)
        n_s, o_s = struct.unpack_from("<II", skin, 0x1C)
        if n_l > 0xFFFF or n_p < n_l:
            raise ValueError(f"skin {si:02d}: {n_l} vertices with {n_p} palette-slot entries")
        lookup = struct.unpack_from(f"<{n_l}H", skin, o_l)
        for i in range(n_s):
            sid, _level, start, count = struct.unpack_from("<4H", skin, o_s + i * 48)
            bone_count, combo, influences = struct.unpack_from("<3H", skin, o_s + i * 48 + 0xC)
            palette = bone_lookup[combo:combo + bone_count]
            for skin_vertex in range(start, min(start + count, n_l)):
                v = lookup[skin_vertex]
                if v >= n_v:
                    continue
                if si == 0:
                    geoset.setdefault(v, sid)
                uses[v].append((si, o_p + skin_vertex * 4, palette, influences))

    points: dict[tuple[float, ...], list[int]] = defaultdict(list)
    for v in geoset:
        at = o_v + v * VERTEX_SIZE
        points[tuple(round(c, DECIMALS) for c in struct.unpack_from("<3f", m2, at))].append(v)

    out, outs, report = bytearray(m2), [bytearray(s) for s in skins], SeamReport()
    for group in points.values():
        if len({geoset[v] for v in group}) < 2:
            continue
        anchor = min(group, key=lambda v: (geoset[v], v))
        pairs = sorted(_weights(out, o_v + anchor * VERTEX_SIZE).items(), key=lambda bw: (-bw[1], bw[0]))
        bones = [b for b, _w in pairs]
        for v in group:
            at = o_v + v * VERTEX_SIZE
            if (geoset[v] == geoset[anchor] or not _shown_together(geoset[v], geoset[anchor])
                    or _weights(out, at) == dict(pairs)):
                continue
            if not all(set(bones) <= set(palette) and len(bones) <= influences
                       for _si, _slot, palette, influences in uses[v]):
                report.blocked += 1
                continue
            padded = pairs + [(0, 0)] * (4 - len(pairs))
            out[at + 12:at + 20] = bytes(w for _b, w in padded) + bytes(b for b, _w in padded)
            for si, slot, palette, _influences in uses[v]:
                outs[si][slot:slot + 4] = bytes(palette.index(b) if w else 0 for b, w in padded)
            report.welded += 1
    return bytes(out), [bytes(s) for s in outs], report
