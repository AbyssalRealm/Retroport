"""retroport race: the look template's helmets are fitted to the race's cranium (helm.py)."""

import math
import struct

import pytest

from retroport.race import helm


def _model(skull_z: tuple[float, float], skull_x: tuple[float, float], attach: tuple[float, float, float],
           hair_z: float, helm_bone_pivot: tuple[float, float, float] | None = None,
           face: tuple[tuple[float, float], ...] = (),
           head: list[tuple[float, float, float]] | None = None) -> tuple[bytes, bytes]:
    """Bones root and head (key bone 6), plus a helmet-only bone under the head when
    ``helm_bone_pivot`` is given; a body mesh with a torso skinned to the root and a skull box
    skinned to the head, with ``face`` (x, z) points on its centre line; a hair mesh (geoset 2)
    above the skull, also on the head; attachment 11 on the helmet bone, or else on the head.
    ``head`` replaces the skull box and face with those points."""
    head_pts = [(x, y, z) for x in skull_x for y in (-0.1, 0.1) for z in skull_z]
    head_pts += [(x, 0.0, z) for x, z in face]
    if head is not None:
        head_pts = list(head)
    torso_pts = [(0.0, 0.0, skull_z[0] - 1.0), (0.1, 0.0, skull_z[0] - 1.0), (0.0, 0.1, skull_z[0] - 1.0)]
    hair_pts = [(0.0, 0.0, hair_z), (0.1, 0.0, hair_z), (0.0, 0.1, hair_z)]
    verts = [(p, 1) for p in head_pts] + [(p, 0) for p in torso_pts] + [(p, 1) for p in hair_pts]

    m2 = bytearray(0x200)
    m2[0:4] = b"MD20"

    def array(slot: int, payload: bytes, count: int) -> None:
        struct.pack_into("<II", m2, slot, count, len(m2))
        m2.extend(payload)
    bone_list = [(-1, (0.0, 0.0, 0.0)), (0, (0.0, 0.0, skull_z[0]))]
    if helm_bone_pivot is not None:
        bone_list.append((1, helm_bone_pivot))
    bones = b""
    for parent, pivot in bone_list:
        rec = bytearray(0x58)
        struct.pack_into("<iIh", rec, 0, -1, 0, parent)
        struct.pack_into("<3f", rec, 0x4C, *pivot)
        bones += bytes(rec)
    array(0x2C, bones, len(bone_list))
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
    struct.pack_into("<IHH3f", att, 0, helm.HELM, len(bone_list) - 1, 0, *attach)
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


# template (Human): skull top 1.8, 0.2 deep and wide; its helmet origin 0.04 above the top, 0.05 forward.
# The new race: skull top 2.6, 0.3 deep (centre x -0.05) and 0.2 wide - a cranium 1.5 times the
# template's front to back, as wide, and 1.33 times as high (top 45%: 0.18 against 0.135), shaped
# unlike it, so fitted axis by axis - with its helmet attachment on the neck joint and hair far above.
TEMPLATE = dict(skull_z=(1.5, 1.8), skull_x=(-0.1, 0.1), attach=(0.05, 0.0, 1.84), hair_z=2.5)
RACE = dict(skull_z=(2.2, 2.6), skull_x=(-0.2, 0.1), attach=(-0.07, 0.0, 2.2), hair_z=3.5)
SCALES = (1.5, 1.0, 0.18 / 0.135)
SPOT = (-0.05 + SCALES[0] * 0.05, 0.0, 2.6 + SCALES[2] * 0.04)


def test_the_helmet_is_fitted_to_the_cranium():
    t_m2, t_skin = _model(**TEMPLATE)
    m2, skin = _model(**RACE)
    lo, hi = helm.skull_box(m2, skin)
    assert hi[2] == pytest.approx(2.6)                  # the hair mesh is not part of the skull
    scales, spot = helm.fit_helmet(m2, skin, t_m2, t_skin)
    assert scales == pytest.approx(SCALES) and spot == pytest.approx(SPOT)


def test_a_helmet_only_bone_is_scaled_and_the_attachment_lands_on_the_spot():
    t_m2, t_skin = _model(**TEMPLATE)
    pivot = (-0.07, 0.0, 2.2)
    m2, skin = _model(**RACE, helm_bone_pivot=pivot)
    out, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert fit.scaled_bone and fit.scales == pytest.approx(SCALES) and fit.bone == 2 and fit.turn == 0.0
    # the client scales the attachment about the bone's pivot, so P lands at pivot + S * (P - pivot)
    _off, position = helm.helm_attachment(out)
    landed = tuple(p + s * (a - p) for p, s, a in zip(pivot, SCALES, position))
    assert landed == pytest.approx(SPOT)
    # a constant scale track on a global sequence (one appended: the model had none), bone flagged
    n_gs, o_gs = struct.unpack_from("<II", out, 0x14)
    assert n_gs == 1 and struct.unpack_from("<I", out, o_gs)[0] == 1000
    _n, o_b = struct.unpack_from("<II", out, 0x2C)
    rec = o_b + 2 * 0x58
    assert struct.unpack_from("<I", out, rec + 4)[0] & helm.BONE_TRANSFORMED
    interp, gseq, n_ts, o_ts, n_vals, o_vals = struct.unpack_from("<HhIIII", out, rec + 0x38)
    assert (interp, gseq, n_ts, n_vals) == (helm.INTERPOLATE_LINEAR, 0, 1, 1)
    n_keys, o_keys = struct.unpack_from("<II", out, o_vals)
    assert struct.unpack_from(f"<{n_keys * 3}f", out, o_keys) == pytest.approx(list(SCALES) * n_keys)
    # nothing else moved: the head and root bones are as they were
    _n, o_b_in = struct.unpack_from("<II", m2, 0x2C)
    assert out[o_b:o_b + 2 * 0x58] == m2[o_b_in:o_b_in + 2 * 0x58]
    # idempotent: a rebuild over the patched model gives the same bytes
    again, fit2 = helm.place_helm_attachment(out, skin, t_m2, t_skin)
    assert again == out and fit2.scales == pytest.approx(SCALES)


def test_a_shared_bone_is_not_scaled_and_only_the_spot_moves():
    t_m2, t_skin = _model(**TEMPLATE)
    m2, skin = _model(**RACE)                           # attachment 11 on the head bone itself
    out, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert not fit.scaled_bone and fit.scales == (1.0, 1.0, 1.0) and fit.old == pytest.approx((-0.07, 0.0, 2.2))
    assert helm.helm_attachment(out)[1] == pytest.approx(SPOT)
    assert out[len(m2):] == b"" and out[:0x14] == m2[:0x14]     # no track appended


def test_a_cranium_like_the_templates_keeps_the_helmets_size():
    t_m2, t_skin = _model(**TEMPLATE)
    m2, skin = _model(skull_z=(2.3, 2.6), skull_x=(-0.15, 0.05), attach=(0.0, 0.0, 2.2), hair_z=3.5,
                      helm_bone_pivot=(0.0, 0.0, 2.2))
    out, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert fit.scales == (1.0, 1.0, 1.0) and not fit.scaled_bone and len(out) == len(m2)
    assert helm.helm_attachment(out)[1] == pytest.approx((-0.05 + 0.05, 0.0, 2.64), abs=1e-6)


def _profile(crown: float, back_x: float, brow: float) -> tuple[tuple[float, float], ...]:
    """A centre-line profile 0.3 from crown to chin: back of the head, crown, brow ridge (with the
    eye socket behind and below it), nose 70% of the way down, mouth, chin."""
    return ((back_x, crown - 0.25), (back_x, crown - 0.15), (back_x, crown - 0.05), (back_x / 2, crown - 0.01),
            (0.0, crown), (0.08, brow), (0.04, brow - 0.04), (0.1, crown - 0.21), (0.07, crown - 0.25),
            (0.06, crown - 0.3))


TEMPLATE_FACE = _profile(1.8, -0.1, 1.72)            # brow 0.08 below the crown


def test_the_brow_ridge_is_the_most_forward_point_above_the_nose():
    m2, skin = _model(**TEMPLATE, face=TEMPLATE_FACE)
    assert helm.brow_height(m2, skin) == pytest.approx(1.72)
    assert helm.brow_height(*_model(**TEMPLATE)) is None             # a box skull has no profile


def test_a_short_forehead_lifts_the_helmet_to_the_brow():
    t_m2, t_skin = _model(**TEMPLATE, face=TEMPLATE_FACE)
    # 0.07 from crown to brow where the template's 0.08, scaled 1.25, would be 0.1
    m2, skin = _model(**RACE, face=_profile(2.6, -0.2, 2.53))
    scales, spot = helm.fit_helmet(m2, skin, t_m2, t_skin)
    assert scales == pytest.approx(SCALES)
    # brow to brow (2.69, above the crown fit's 2.65), then a quarter of crown-to-brow higher
    assert spot[2] == pytest.approx(2.53 + SCALES[2] * (1.84 - 1.72) + helm.BROW_CLEARANCE * (2.6 - 2.53))
    assert spot[0] == pytest.approx(SPOT[0])


def test_a_long_forehead_keeps_the_crown_fit():
    t_m2, t_skin = _model(**TEMPLATE, face=TEMPLATE_FACE)
    m2, skin = _model(**RACE, face=_profile(2.6, -0.2, 2.45))
    _scales, spot = helm.fit_helmet(m2, skin, t_m2, t_skin)
    assert spot == pytest.approx(SPOT)


def test_helm_attachment_refuses_a_model_without_one():
    m2, skin = _model(**RACE)
    broken = bytearray(m2)
    n, off = struct.unpack_from("<II", broken, 0xF8)
    struct.pack_into("<h", broken, off + 11 * 2, -1)
    with pytest.raises(helm.HelmError, match="no helmet attachment"):
        helm.helm_attachment(bytes(broken))


def _skull(centre: tuple[float, float, float], scale: float = 1.0, turn: float = 0.0) -> list[tuple[float, ...]]:
    """An egg of a skull, longer front to back than high, in rows from crown to chin - turned
    ``turn`` degrees about the y axis (negative: facing up), scaled and moved to ``centre``."""
    c, n = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    points = [(0.0, 0.0, 0.11), (0.0, 0.0, -0.11)]
    for row in range(1, 18):
        lat = math.pi * row / 18 - math.pi / 2
        for k in range(24):
            lon = 2 * math.pi * k / 24
            x = (0.13 if math.cos(lon) > 0 else 0.11) * math.cos(lat) * math.cos(lon)
            points.append((x, 0.09 * math.cos(lat) * math.sin(lon), 0.11 * math.sin(lat)))
    return [(centre[0] + scale * (c * x + n * z), centre[1] + scale * y, centre[2] + scale * (-n * x + c * z))
            for x, y, z in points]


def _decoded_turn(m2: bytes, bone: int) -> float:
    """Degrees about the y axis of a bone's (constant) rotation track's first key."""
    _n, o_b = struct.unpack_from("<II", m2, 0x2C)
    _i, gseq, n_ts, _o_ts, n_vals, o_vals = struct.unpack_from("<HhIIII", m2, o_b + bone * 0x58 + 0x24)
    assert gseq >= 0 and n_vals == 1
    _n_keys, o_keys = struct.unpack_from("<II", m2, o_vals)
    x, y, z, w = (((v + 32768) if v < 0 else (v - 32767)) / 32767 for v in struct.unpack_from("<4h", m2, o_keys))
    assert x == pytest.approx(0, abs=1e-4) and z == pytest.approx(0, abs=1e-4)
    return math.degrees(2 * math.atan2(y, w))


def test_a_cranium_facing_further_up_turns_the_helmet():
    t_m2, t_skin = _model(skull_z=(0, 0), skull_x=(0, 0), attach=(0.02, 0.0, 1.82), hair_z=2.5,
                          head=_skull((0.0, 0.0, 1.7)))
    pivot = (-0.05, 0.0, 2.25)
    m2, skin = _model(skull_z=(0, 0), skull_x=(0, 0), attach=pivot, hair_z=3.5, helm_bone_pivot=pivot,
                      head=_skull((-0.05, 0.0, 2.4), 1.3, -6.0))
    scales = helm.cranium_scales(m2, skin, t_m2, t_skin)
    assert scales == pytest.approx((1.3, 1.3, 1.3), abs=0.03)          # shaped like the template's
    turn, x = helm.cranium_turn(m2, skin, t_m2, t_skin, scales)
    # the template's helmet origin, (0.02, 0.12) from its skull's centre, carried onto the race's
    c, n = math.cos(math.radians(-6.0)), math.sin(math.radians(-6.0))
    assert turn == pytest.approx(-6.0, abs=0.5) and x == pytest.approx(-0.05 + 1.3 * (c * 0.02 + n * 0.12), abs=0.01)

    out, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert fit.turn == pytest.approx(turn) and fit.spot[0] == pytest.approx(x) and fit.scaled_bone
    assert _decoded_turn(out, 2) == pytest.approx(turn, abs=0.01)
    # the client turns and scales about the pivot: P lands at pivot + R * S * (P - pivot)
    _off, p = helm.helm_attachment(out)
    c, n = math.cos(math.radians(fit.turn)), math.sin(math.radians(fit.turn))
    d = [s * (a - b) for s, a, b in zip(fit.scales, p, pivot)]
    landed = (pivot[0] + c * d[0] + n * d[2], pivot[1] + d[1], pivot[2] - n * d[0] + c * d[2])
    assert landed == pytest.approx(fit.spot, abs=1e-6)
    again, _fit = helm.place_helm_attachment(out, skin, t_m2, t_skin)
    assert again == out


def test_bfas_own_helmet_turn_is_dropped_when_the_cranium_gives_none():
    t_m2, t_skin = _model(**TEMPLATE)
    pivot = (-0.07, 0.0, 2.2)
    m2, skin = _model(**RACE, helm_bone_pivot=pivot)
    bfa = bytearray(m2)
    helm._set_constant_turn(bfa, 2, 2.9)                    # BfA tips a Kul Tiran's helmet 2.9 degrees
    assert helm.cranium_turn(bytes(bfa), skin, t_m2, t_skin, SCALES) is None   # a box skull has no bearing
    out, fit = helm.place_helm_attachment(bytes(bfa), skin, t_m2, t_skin)
    assert fit.turn == 0.0 and _decoded_turn(out, 2) == pytest.approx(0.0, abs=1e-3)
    _off, p = helm.helm_attachment(out)
    assert tuple(b + s * (a - b) for s, a, b in zip(SCALES, p, pivot)) == pytest.approx(SPOT)


def test_a_cranium_shaped_like_the_templates_keeps_the_helmets_shape():
    t_m2, t_skin = _model(**TEMPLATE)

    def skull(high: float) -> list[tuple[float, float, float]]:
        return [(x, y, z) for x in (-0.12, 0.12) for y in (-0.12, 0.12) for z in (2.6 - high, 2.6)]
    # 1.2 times the template's front to back and across, 1.3 high: within PROPORTIONS, so the mean of
    # the first two all round; 1.5 high is not, and each axis takes its own
    m2, skin = _model(skull_z=(0, 0), skull_x=(0, 0), attach=(0.0, 0.0, 2.2), hair_z=3.5, head=skull(0.3 * 1.3))
    assert helm.cranium_scales(m2, skin, t_m2, t_skin) == pytest.approx((1.2, 1.2, 1.2))
    m2, skin = _model(skull_z=(0, 0), skull_x=(0, 0), attach=(0.0, 0.0, 2.2), hair_z=3.5, head=skull(0.3 * 1.5))
    assert helm.cranium_scales(m2, skin, t_m2, t_skin) == pytest.approx((1.2, 1.2, 1.5))


def _posed(m2: bytes, pitch: dict[int, float]) -> bytes:
    """``m2`` with a sequence for each animation in ``pitch`` (variation 0, keyed in the .m2) and the
    head bone (1) turned that many degrees nose down at its first key in each."""
    out = bytearray(m2)

    def append(payload: bytes) -> int:
        out.extend(bytes(-len(out) % 16))
        at = len(out)
        out.extend(payload)
        return at
    anims = list(pitch)
    seqs = b"".join(struct.pack("<HHIfI", a, 0, 1000, 0.0, helm.SEQ_IN_M2) + bytes(0x30) for a in anims)
    struct.pack_into("<II", out, 0x1C, len(anims), append(seqs))
    times, values = [], []
    for a in anims:
        half = math.radians(pitch[a]) / 2
        key = struct.pack("<4h", *(helm._compress(v) for v in (0.0, math.sin(half), 0.0, math.cos(half))))
        times.append(struct.pack("<II", 1, append(struct.pack("<I", 0))))
        values.append(struct.pack("<II", 1, append(key)))
    track = struct.pack("<HhIIII", helm.INTERPOLATE_LINEAR, -1, len(anims), append(b"".join(times)),
                        len(anims), append(b"".join(values)))
    _n, o_b = struct.unpack_from("<II", out, 0x2C)
    out[o_b + 1 * 0x58 + 0x24:o_b + 1 * 0x58 + 0x38] = track
    return bytes(out)


def test_a_head_held_lower_than_the_templates_turns_the_helmet_back_about_the_brow():
    t_m2, t_skin = _model(**TEMPLATE, face=TEMPLATE_FACE)
    t_m2 = _posed(t_m2, {0: 2.0, 4: 2.0})
    pivot = (-0.07, 0.0, 2.2)
    m2, skin = _model(**RACE, helm_bone_pivot=pivot, face=_profile(2.6, -0.2, 2.53))
    m2 = _posed(m2, {0: 10.0, 4: 14.0, 5: 40.0})            # run (5) is not read for the posture
    assert helm.head_pitch(m2, 0) == pytest.approx(10.0, abs=0.01)
    assert helm.head_pitch(m2, 4) == pytest.approx(14.0, abs=0.01)
    assert helm.head_pitch(m2, 26) is None
    lean = helm.posture_turn(m2, t_m2)
    assert lean == pytest.approx(-helm.POSTURE_SHARE * 10.0, abs=0.01)   # half the mean of 8 and 12 lower
    _scales, spot = helm.fit_helmet(m2, skin, t_m2, t_skin)
    out, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert fit.turn == pytest.approx(lean) and _decoded_turn(out, 2) == pytest.approx(lean, abs=0.01)
    # turned about the brow ridge (0.08, 2.53): as far from it as before, and turned by the lean
    ridge = (0.08, 2.53)
    before = (spot[0] - ridge[0], spot[2] - ridge[1])
    after = (fit.spot[0] - ridge[0], fit.spot[2] - ridge[1])
    assert math.hypot(*after) == pytest.approx(math.hypot(*before))
    assert math.degrees(math.atan2(before[1], before[0]) - math.atan2(after[1], after[0])) == pytest.approx(lean, abs=0.01)
    assert fit.spot[0] < spot[0] and fit.spot[2] < spot[2]  # front up about the brow: back and down
    _off, p = helm.helm_attachment(out)
    c, n = math.cos(math.radians(fit.turn)), math.sin(math.radians(fit.turn))
    d = [s * (a - b) for s, a, b in zip(fit.scales, p, pivot)]
    landed = (pivot[0] + c * d[0] + n * d[2], pivot[1] + d[1], pivot[2] - n * d[0] + c * d[2])
    assert landed == pytest.approx(fit.spot, abs=1e-6)
    again, _fit = helm.place_helm_attachment(out, skin, t_m2, t_skin)
    assert again == out


def test_a_posture_like_the_templates_leaves_the_helmet_unturned():
    t_m2, t_skin = _model(**TEMPLATE, face=TEMPLATE_FACE)
    t_m2 = _posed(t_m2, {0: 5.0, 4: 7.0})
    m2, skin = _model(**RACE, helm_bone_pivot=(-0.07, 0.0, 2.2), face=_profile(2.6, -0.2, 2.53))
    m2 = _posed(m2, {0: 5.5, 4: 6.0})                       # a quarter of a degree: under TURN_MIN
    out, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert fit.turn == 0.0 and not helm._has_turn(out, 2)


def test_the_fit_is_undone_when_the_race_wears_its_own_helmets():
    t_m2, t_skin = _model(**TEMPLATE)
    pivot = (-0.07, 0.0, 2.2)
    m2, skin = _model(**RACE, helm_bone_pivot=pivot)
    fit = helm.fit_attachment(m2, skin, t_m2, t_skin)
    fitted, placed = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
    assert (fit.scales, fit.spot, fit.turn) == (placed.scales, placed.spot, placed.turn)
    # the source's own: on the pivot, turned 2.8 degrees nose down, no scale
    half = math.radians(2.82) / 2
    key = struct.pack("<4h", *(helm._compress(v) for v in (0.0, math.sin(half), 0.0, math.cos(half))))
    own = helm.Attachment(2, pivot, helm.BONE_TRANSFORMED, {0x10: None, 0x24: key, 0x38: None})
    out = helm.set_attachment(fitted, own)
    assert helm.helm_attachment(out)[1] == pytest.approx(pivot)
    _n, o_b = struct.unpack_from("<II", out, 0x2C)
    rec = o_b + 2 * 0x58
    assert struct.unpack_from("<I", out, rec + 0x38 + 12)[0] == 0                  # the scale track is gone
    assert helm._first_turn(out, 2, 0) == pytest.approx(helm._quaternion(struct.unpack("<4h", key)))
    assert helm.set_attachment(out, own) == out                                     # rebuilding changes nothing
    plain = helm.Attachment(2, pivot, 0, {0x10: None, 0x24: None, 0x38: None})
    bare = helm.set_attachment(out, plain)
    assert not helm._has_turn(bare, 2) and not struct.unpack_from("<I", bare, rec + 4)[0] & helm.BONE_TRANSFORMED
    with pytest.raises(helm.HelmError, match="bone 2, not 1"):
        helm.set_attachment(out, helm.Attachment(1, pivot, 0, {}))
