"""retroport race: seams between geosets get one set of weights (seams.py)."""

import struct

from retroport.race import seams

# bone lookup (the palettes): body [10, 11] at 0, pants [12, 10, 11] at 2, belt [12] at 5
BONE_LOOKUP = (10, 11, 12, 10, 11, 12)
SEAM, OTHER = (0.0, 0.0, 1.0), (1.0, 0.0, 1.0)
VERTICES = [(SEAM, {10: 255}),                   # 0 body, on the seam
            (OTHER, {11: 255}),                  # 1 body
            (SEAM, {10: 200, 12: 55}),           # 2 pants, on the seam with other weights
            (OTHER, {11: 255}),                  # 3 pants, already like the body
            (SEAM, {12: 255})]                   # 4 belt, on the seam - its palette has no bone 10
BODY, PANTS, BELT = (0, [0, 1], 0, 2, 4), (1102, [2, 3], 2, 3, 4), (1802, [4], 5, 1, 4)


def _model() -> bytes:
    m2 = bytearray(0x130)
    m2[0:4] = b"MD20"

    def array(slot: int, payload: bytes, count: int) -> None:
        struct.pack_into("<II", m2, slot, count, len(m2))
        m2.extend(payload)
    vrec = b""
    for pos, weights in VERTICES:
        rec = bytearray(48)
        struct.pack_into("<3f", rec, 0, *pos)
        for k, (bone, w) in enumerate(weights.items()):
            rec[12 + k], rec[16 + k] = w, bone
        vrec += bytes(rec)
    array(0x3C, vrec, len(VERTICES))
    array(0x78, struct.pack(f"<{len(BONE_LOOKUP)}H", *BONE_LOOKUP), len(BONE_LOOKUP))
    return bytes(m2)


def _skin(m2: bytes, submeshes) -> bytes:
    """submeshes: (geoset id, model vertices, palette start, palette size, influences)."""
    skin = bytearray(0x30)
    skin[0:4] = b"SKIN"

    def array(slot: int, payload: bytes, count: int) -> None:
        struct.pack_into("<II", skin, slot, count, len(skin))
        skin.extend(payload)
    lookup, slots, records = [], b"", b""
    for sid, verts, combo, size, influences in submeshes:
        palette = BONE_LOOKUP[combo:combo + size]
        rec = bytearray(48)
        struct.pack_into("<6H", rec, 0, sid, 0, len(lookup), len(verts), 0, 0)
        struct.pack_into("<3H", rec, 0xC, size, combo, influences)
        records += bytes(rec)
        for v in verts:
            at = struct.unpack_from("<I", m2, 0x40)[0] + v * 48
            w, b = m2[at + 12:at + 16], m2[at + 16:at + 20]
            slots += bytes(palette.index(b[k]) if w[k] and b[k] in palette else 0 for k in range(4))
            lookup.append(v)
    array(0x04, struct.pack(f"<{len(lookup)}H", *lookup), len(lookup))
    array(0x14, slots, len(lookup))
    array(0x1C, records, len(submeshes))
    return bytes(skin)


def _weights(m2: bytes, v: int) -> dict[int, int]:
    at = struct.unpack_from("<I", m2, 0x40)[0] + v * 48
    return {m2[at + 16 + k]: m2[at + 12 + k] for k in range(4) if m2[at + 12 + k]}


def test_a_seam_vertex_takes_the_body_vertex_weights_and_its_palette_slots_follow():
    m2 = _model()
    skin = _skin(m2, [BODY, PANTS, BELT])
    out, (out_skin,), report = seams.weld_seams(m2, [skin])
    assert (report.welded, report.blocked) == (1, 1)
    assert _weights(out, 2) == {10: 255}                  # the pants vertex moves with the body now
    assert _weights(out, 3) == {11: 255} and _weights(out, 4) == {12: 255}
    _n, o_slots = struct.unpack_from("<II", out_skin, 0x14)
    assert out_skin[o_slots + 2 * 4:o_slots + 3 * 4] == bytes((1, 0, 0, 0))   # bone 10 is the pants palette's slot 1
    assert out_skin[:o_slots + 2 * 4] == skin[:o_slots + 2 * 4]
    again, (again_skin,), report2 = seams.weld_seams(out, [out_skin])
    assert again == out and again_skin == out_skin and report2.welded == 0


def test_alternatives_of_one_geoset_group_are_not_joined():
    m2 = _model()
    # two styles of pants (1102, 1103) meeting at the seam point: never shown together
    skin = _skin(m2, [(1102, [0, 1], 0, 2, 4), (1103, [2, 3], 2, 3, 4)])
    out, (out_skin,), report = seams.weld_seams(m2, [skin])
    assert (report.welded, report.blocked) == (0, 0) and out == m2 and out_skin == skin


def test_a_vertex_one_skin_cannot_express_is_left_alone():
    m2 = _model()
    skins = [_skin(m2, [BODY, PANTS, BELT]),
             _skin(m2, [BODY, (1102, [2, 3], 5, 1, 4), BELT])]     # a lower detail: the pants palette is [12]
    out, out_skins, report = seams.weld_seams(m2, skins)
    assert (report.welded, report.blocked) == (0, 2)
    assert out == m2 and out_skins == skins
