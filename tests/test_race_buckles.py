"""A belt's model drawn as part of the body (race/buckles.py)."""

import struct

import pytest

from retroport.formats import structs as S
from retroport.formats.md20 import read_texture_slots
from retroport.race import buckles

ATTACH_AT = (0.5, 0.0, 1.0)
BONE = 1


def _arrays(head: bytearray, arrays: dict[int, tuple[int, bytes]]) -> bytes:
    for field, (count, payload) in arrays.items():
        head.extend(bytes(-len(head) % 16))
        struct.pack_into("<II", head, field, count, len(head))
        head.extend(payload)
    return bytes(head)


def _vertex(pos, bone=0, uv=(0.0, 0.0)) -> bytes:
    return struct.pack("<3f4B4B3f2f2f", *pos, 255, 0, 0, 0, bone, 0, 0, 0, 0, 0, 1, *uv, 0, 0)


def _model(attachments=((53, BONE, ATTACH_AT),)) -> bytes:
    """Three body vertices, three of a rope band; the body texture; one material (alpha); bones 0
    and 1, the BeltBuckle attachment on bone 1."""
    head = bytearray(0x130)
    head[0:8] = b"MD20" + struct.pack("<I", 264)
    struct.pack_into("<I", head, S.HDR_NUM_SKIN_PROFILES, 1)
    verts = b"".join(_vertex(p) for p in ((0, 0, 0), (0, 1, 0), (0, 0, 1), (0.4, -1, 1), (0.4, 1, 1), (0.6, 0, 1)))
    atts = b"".join(struct.pack("<IHH3f", a, b, 0, *p) + bytes(20) for a, b, p in attachments)
    return _arrays(head, {S.HDR_VERTICES: (6, verts),
                          S.HDR_TEXTURES: (1, struct.pack("<IIII", 1, 0, 0, 0)),
                          buckles.HDR_MATERIALS: (1, struct.pack("<HH", 16, 2)),
                          buckles.HDR_BONE_COMBOS: (2, struct.pack("<2H", 0, 1)),
                          S.HDR_TEXTURE_LOOKUP: (1, struct.pack("<H", 0)),
                          S.HDR_ATTACHMENTS: (len(attachments), atts)})


def _skin() -> bytes:
    head = bytearray(0x30)
    head[0:4] = b"SKIN"
    subs = b"".join(struct.pack("<10H7f", sid, 0, vs, 3, vs, 3, 2, 0, 1, 0, *([0.0] * 7)) for sid, vs in ((0, 0), (1803, 3)))
    batches = b"".join(struct.pack("<BbHHHhHHHHHHH", 16, 0, 0, sec, 0, -1, 0, 0, 1, 0, 0, 0, 0) for sec in (0, 1))
    return _arrays(head, {S.SKIN_VERTICES: (6, struct.pack("<6H", *range(6))),
                          S.SKIN_INDICES: (6, struct.pack("<6H", 0, 1, 2, 3, 4, 5)),
                          buckles.SKIN_BONES: (6, bytes(4) * 3 + bytes([1, 0, 0, 0]) * 3),
                          S.SKIN_SUBMESHES: (2, subs),
                          S.SKIN_BATCHES: (2, batches)})


MESH = buckles.Mesh(b"".join(_vertex(p, bone=3, uv=(0.5, 0.5)) for p in ((0, 0, 0), (0, 0.2, 0), (0, 0, -0.2))),
                    [0, 2, 1])


def _plan():
    plan = buckles.plan([(224011, 7, 71, 0), (224002, 7, 70, 2), (224005, 7, 70, 2)])
    for b in plan:
        b.texture_name = f"Item\\ObjectComponents\\Waist\\Pandaren_{b.texture}.blp"
    return plan


def _read_skin(skin):
    nv, ov = S.read_m2array(skin, S.SKIN_VERTICES)
    ni, oi = S.read_m2array(skin, S.SKIN_INDICES)
    ns, os_ = S.read_m2array(skin, S.SKIN_SUBMESHES)
    nb, ob = S.read_m2array(skin, S.SKIN_BATCHES)
    lookup = struct.unpack_from(f"<{nv}H", skin, ov)
    indices = struct.unpack_from(f"<{ni}H", skin, oi)
    subs = [struct.unpack_from("<10H", skin, os_ + i * 48) for i in range(ns)]
    batches = [struct.unpack_from("<BbHHHhHHHH", skin, ob + i * 24) for i in range(nb)]
    return lookup, indices, subs, batches


def test_a_belt_geoset_each_from_10_in_item_order():
    plan = _plan()
    assert [(b.value, b.texture, b.band, b.items) for b in plan] == [(10, 70, 2, [224002, 224005]), (11, 71, 0, [224011])]


def test_the_buckle_hangs_on_the_attachment_in_a_belt_geoset_with_the_band():
    m2, (skin,) = buckles.bake(_model(), [_skin()], _plan(), {7: MESH})
    # the knot's vertices once, at the attachment, wholly on its bone
    vc, vo = S.read_m2array(m2, S.HDR_VERTICES)
    assert vc == 9
    for k, local in enumerate(((0, 0, 0), (0, 0.2, 0), (0, 0, -0.2))):
        pos = struct.unpack_from("<3f", m2, vo + (6 + k) * 48)
        assert pos == pytest.approx(tuple(a + b for a, b in zip(local, ATTACH_AT)))
        assert struct.unpack_from("<8B", m2, vo + (6 + k) * 48 + 12) == (255, 0, 0, 0, BONE, 0, 0, 0)
    # a texture slot naming each belt's texture, looked up by the buckles' batches
    slots = read_texture_slots(m2)
    assert [s.type for s in slots] == [1, 0, 0]
    nt, ot = S.read_m2array(m2, S.HDR_TEXTURES)
    count, at = struct.unpack_from("<II", m2, ot + 16 + 8)
    assert m2[at:at + count] == b"Item\\ObjectComponents\\Waist\\Pandaren_70.blp\0"
    nl, ol = S.read_m2array(m2, S.HDR_TEXTURE_LOOKUP)
    assert struct.unpack_from(f"<{nl}H", m2, ol) == (0, 1, 2)
    nc, oc = S.read_m2array(m2, buckles.HDR_BONE_COMBOS)
    assert struct.unpack_from(f"<{nc}H", m2, oc) == (0, 1, 1)               # the knot's palette: its bone
    nm, om = S.read_m2array(m2, buckles.HDR_MATERIALS)
    assert struct.unpack_from(f"<{2 * nm}H", m2, om) == (16, 2, 0, 0)       # opaque, added

    lookup, indices, subs, batches = _read_skin(skin)
    assert [s[0] for s in subs] == [0, 1803, 1811, 1811, 1812]
    band, knot10, knot11 = subs[2], subs[3], subs[4]
    # the band again in a window of its own: the same vertices, the same triangles
    assert band[2:6] == (6, 3, 6, 3)
    assert [lookup[i] for i in indices[6:9]] == [3, 4, 5]
    assert band[6:] == subs[1][6:]
    # each knot its own window over the same three model vertices, on the bone's palette
    assert knot10[2:10] == (9, 3, 9, 3, 1, 2, 1, BONE) and knot11[2:10] == (12, 3, 12, 3, 1, 2, 1, BONE)
    assert [lookup[i] for i in indices[9:12]] == [6, 8, 7] and [lookup[i] for i in indices[12:15]] == [6, 8, 7]
    # the band's batch copied onto its copy; each knot drawn alone in its belt's texture
    assert batches[2][3] == 2 and batches[2][8:] == batches[1][8:]
    assert [(b[3], b[4], b[6], b[8], b[9]) for b in batches[3:]] == [(3, 3, 1, 1, 1), (4, 4, 1, 1, 2)]
    assert buckles.baked_values(skin) == {10, 11}


def test_unbake_gives_the_body_back_exactly_and_a_baked_body_is_refused():
    m2, skin = _model(), _skin()
    new_m2, (new_skin,) = buckles.bake(m2, [skin], _plan(), {7: MESH})
    assert buckles.unbake(new_m2, buckles.record(m2, new_m2, buckles.M2_HEAD)) == m2
    assert buckles.unbake(new_skin, buckles.record(skin, new_skin, buckles.SKIN_HEAD)) == skin
    assert buckles.unbake(m2, buckles.record(m2, new_m2, buckles.M2_HEAD)) is None    # not the bake's file
    with pytest.raises(buckles.BucklesError, match="already wears buckles"):
        buckles.bake(new_m2, [new_skin], _plan(), {7: MESH})


def test_a_body_without_the_attachment_gets_none():
    with pytest.raises(buckles.BucklesError, match="BeltBuckle"):
        buckles.bake(_model(attachments=((11, 0, (0, 0, 2)),)), [_skin()], _plan(), {7: MESH})
