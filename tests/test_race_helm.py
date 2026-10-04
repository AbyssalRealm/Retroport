"""retroport race: the helmet attachment follows the look template's (helm.py)."""

import struct

import pytest

from retroport.race import helm


def _model(skull_z: tuple[float, float], skull_x: tuple[float, float], attach: tuple[float, float, float],
           hair_z: float) -> tuple[bytes, bytes]:
    """Two bones (root, head = key bone 6); a body mesh with a torso skinned to the root and a
    skull box skinned to the head; a hair mesh (geoset 2) above the skull, also on the head;
    attachment 11 on the head."""
    head_pts = [(x, y, z) for x in skull_x for y in (-0.1, 0.1) for z in skull_z]
    torso_pts = [(0.0, 0.0, skull_z[0] - 1.0), (0.1, 0.0, skull_z[0] - 1.0), (0.0, 0.1, skull_z[0] - 1.0)]
    hair_pts = [(0.0, 0.0, hair_z), (0.1, 0.0, hair_z), (0.0, 0.1, hair_z)]
    verts = [(p, 1) for p in head_pts] + [(p, 0) for p in torso_pts] + [(p, 1) for p in hair_pts]

    m2 = bytearray(0x200)
    def array(slot: int, payload: bytes, count: int) -> None:
        struct.pack_into("<II", m2, slot, count, len(m2))
        m2.extend(payload)
    bones = b""
    for parent, pivot in ((-1, (0.0, 0.0, 0.0)), (0, (0.0, 0.0, skull_z[0]))):
        rec = bytearray(0x58)
        struct.pack_into("<iIh", rec, 0, -1, 0, parent)
        struct.pack_into("<3f", rec, 0x4C, *pivot)
        bones += bytes(rec)
    array(0x2C, bones, 2)
    array(0x34, struct.pack("<7h", -1, -1, -1, -1, -1, -1, 1), 7)
    vrec = b""
    for (x, y, z), bone in verts:
        rec = bytearray(48)
        struct.pack_into("<3f", rec, 0, x, y, z)
        rec[12:16] = bytes((255, 0, 0, 0))
        rec[16:20] = bytes((bone, 0, 0, 0))
        vrec += bytes(rec)
    array(0x3C, vrec, len(verts))
    att = bytearray(0x28)
    struct.pack_into("<IHH3f", att, 0, helm.HELM, 1, 0, *attach)
    array(0xF0, bytes(att), 1)
    array(0xF8, struct.pack("<12h", *([-1] * 11 + [0])), 12)

    skin = bytearray(0x30)
    skin[0:4] = b"SKIN"
    def sarray(slot: int, payload: bytes, count: int) -> None:
        struct.pack_into("<II", skin, slot, count, len(skin))
        skin.extend(payload)
    n_body = len(head_pts) + len(torso_pts)
    sarray(0x04, struct.pack(f"<{len(verts)}H", *range(len(verts))), len(verts))
    corners = list(range(n_body))
    while len(corners) % 3:
        corners.append(0)                                   # whole triangles, body vertices only
    hair = [len(verts) - 3, len(verts) - 2, len(verts) - 1]
    sarray(0x0C, struct.pack(f"<{len(corners) + 3}H", *corners, *hair), len(corners) + 3)
    subs = struct.pack("<HHHHHH", 0, 0, 0, n_body, 0, len(corners)) + bytes(36)
    subs += struct.pack("<HHHHHH", 2, 0, n_body, 3, len(corners), 3) + bytes(36)
    sarray(0x1C, subs, 2)
    return bytes(m2), bytes(skin)


def test_helm_attachment_moves_to_the_templates_spot_on_the_skull():
    # template (Human): skull top 1.8, centre x 0.0; its helm point 0.04 above the top, 0.05 forward
    t_m2, t_skin = _model((1.5, 1.8), (-0.1, 0.1), (0.05, 0.0, 1.84), hair_z=2.5)
    # the new race: skull top 2.6, centre x -0.05, helm point on the neck joint; hair far above
    m2, skin = _model((2.2, 2.6), (-0.2, 0.1), (-0.07, 0.0, 2.2), hair_z=3.5)
    lo, hi = helm.skull_box(m2, skin)
    assert hi[2] == pytest.approx(2.6)                  # the hair mesh is not part of the skull
    out, old, new = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert old == pytest.approx((-0.07, 0.0, 2.2))
    assert new == pytest.approx((-0.05 + 0.05, 0.0, 2.6 + 0.04))
    assert helm.helm_attachment(out)[1] == pytest.approx(new)
    # idempotent: a rebuild over the patched model lands on the same spot
    assert helm.place_helm_attachment(out, skin, t_m2, t_skin)[2] == pytest.approx(new)


def test_helm_attachment_refuses_a_model_without_one():
    m2, skin = _model((2.2, 2.6), (-0.2, 0.1), (0.0, 0.0, 2.2), hair_z=3.5)
    broken = bytearray(m2)
    n, off = struct.unpack_from("<II", broken, 0xF8)
    struct.pack_into("<h", broken, off + 11 * 2, -1)
    with pytest.raises(helm.HelmError, match="no helmet attachment"):
        helm.helm_attachment(bytes(broken))
