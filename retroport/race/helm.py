"""Put a ported race's helmet attachment where the look template's helmets expect it.

A 3.3.5 race wears the look template's helmet models (ChrRaces.ClientPrefix, ``Hu`` for
Human): each hangs down from attachment 11, modelled for that race's head. Human's
attachment 11 sits at the top of the skull; BfA's Kul Tiran models keep theirs on the head
bone's pivot - the neck joint, 0.21 lower - because BfA gives Kul Tirans their own helmets.
Human helmets on that point sink into the neck and chest.

The attachment is moved to the same spot relative to the new race's skull as the template's
is to its own: the skull being the body mesh (geoset 0) skinned mostly to the head key bone
or its children, which leaves hair and beards out.

M2 (12340): 0x2C bones (0x58 each: +8 parent, +0x4C pivot), 0x34 key-bone lookup (6 = head),
0x3C vertices (48: +12 weights[4], +16 bone indices[4]), 0xF0 attachments (0x28: +0 id,
+4 bone, +8 position), 0xF8 attachment lookup. Skin: 0x04 vertex lookup, 0x0C indices,
0x1C submeshes (48: +0 id, +2 level, +8 index start, +10 index count).
"""

from __future__ import annotations

import struct
from collections import deque

HELM = 11
HEAD_KEY_BONE = 6


class HelmError(ValueError):
    pass


def _head_bones(m2: bytes) -> set[int]:
    n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
    n_k, o_k = struct.unpack_from("<II", m2, 0x34)
    if n_k <= HEAD_KEY_BONE:
        raise HelmError("model has no head key bone")
    head = struct.unpack_from("<h", m2, o_k + HEAD_KEY_BONE * 2)[0]
    if not 0 <= head < n_b:
        raise HelmError("model has no head key bone")
    children: dict[int, list[int]] = {}
    for i in range(n_b):
        children.setdefault(struct.unpack_from("<h", m2, o_b + i * 0x58 + 8)[0], []).append(i)
    members, todo = {head}, deque([head])
    while todo:
        for child in children.get(todo.popleft(), []):
            if child not in members:
                members.add(child)
                todo.append(child)
    return members


def skull_box(m2: bytes, skin: bytes) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(min, max) of the body mesh's vertices skinned mostly to the head or its children."""
    head = _head_bones(m2)
    n_l, o_l = struct.unpack_from("<II", skin, 0x04)
    n_t, o_t = struct.unpack_from("<II", skin, 0x0C)
    n_s, o_s = struct.unpack_from("<II", skin, 0x1C)
    lookup = struct.unpack_from(f"<{n_l}H", skin, o_l)
    tris = struct.unpack_from(f"<{n_t}H", skin, o_t)
    body: set[int] = set()
    for i in range(n_s):
        sid, level, _vs, _vc, start, count = struct.unpack_from("<HHHHHH", skin, o_s + i * 48)
        if sid == 0:
            first = (level << 16) | start
            body.update(lookup[tris[k]] for k in range(first, min(first + count, n_t)))
    n_v, o_v = struct.unpack_from("<II", m2, 0x3C)
    points = []
    for v in body:
        if v >= n_v:
            continue
        rec = o_v + v * 48
        weights, bones = m2[rec + 12:rec + 16], m2[rec + 16:rec + 20]
        dominant = max(range(4), key=lambda k: weights[k])
        if weights[dominant] and bones[dominant] in head:
            points.append(struct.unpack_from("<3f", m2, rec))
    if not points:
        raise HelmError("no body-mesh vertex is skinned to the head")
    return tuple(map(min, zip(*points))), tuple(map(max, zip(*points)))


def helm_attachment(m2: bytes) -> tuple[int, tuple[float, float, float]]:
    """(file offset of its position, position) of the attachment the client uses for helmets."""
    n_a, o_a = struct.unpack_from("<II", m2, 0xF0)
    n_l, o_l = struct.unpack_from("<II", m2, 0xF8)
    index = struct.unpack_from("<h", m2, o_l + HELM * 2)[0] if n_l > HELM else -1
    if not 0 <= index < n_a or struct.unpack_from("<I", m2, o_a + index * 0x28)[0] != HELM:
        raise HelmError("model has no helmet attachment (11)")
    pos = o_a + index * 0x28 + 8
    return pos, struct.unpack_from("<3f", m2, pos)


def place_helm_attachment(m2: bytes, skin: bytes, template_m2: bytes, template_skin: bytes
                          ) -> tuple[bytes, tuple[float, ...], tuple[float, ...]]:
    """``m2`` with attachment 11 where the template race has it relative to its skull: the
    same height against the top of the skull and the same offset from its front-to-back
    centre. Returns (model, old position, new position)."""
    t_lo, t_hi = skull_box(template_m2, template_skin)
    _off, t_att = helm_attachment(template_m2)
    lo, hi = skull_box(m2, skin)
    off, old = helm_attachment(m2)
    new = ((lo[0] + hi[0]) / 2 + t_att[0] - (t_lo[0] + t_hi[0]) / 2, old[1], hi[2] + t_att[2] - t_hi[2])
    out = bytearray(m2)
    struct.pack_into("<3f", out, off, *new)
    return bytes(out), old, new
