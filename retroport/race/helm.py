"""Fit the look template's helmets on a ported race's head.

A race BfA gave no helmets of its own (or built with ``--template-helmets``) wears the look
template's helmet models (ChrRaces.ClientPrefix, ``Hu`` for Human): each hangs down from
attachment 11, modelled for that race's head. A race with its own wears BfA's (helmets.py),
which carries a template helmet BfA lacks to where this fit puts it. Human's attachment 11 sits
at the top of the skull; BfA's Kul Tiran models keep theirs on the head bone's pivot - the neck
joint, 0.21 lower - because BfA gives Kul Tirans their own helmets. Human helmets on that point
sink into the neck and chest.

The attachment goes to the same spot relative to the new race's cranium as the template's is
to its own, and the helmet is scaled to the cranium: a Kul Tiran's is 1.3-1.5 times a Human's
across, so a Human helmet sat on the back of the head with the face out of its front (eighth
client test). The cranium is the top of the skull, the skull being the body mesh (geoset 0)
skinned mostly to the head key bone or its children (hair and beards left out); the jaw and
face below it would pull the fit forward and down. A cranium shaped like the template's (its
front-to-back, side-to-side and top-to-bottom ratios within PROPORTIONS of each other) keeps the
helmet's shape, scaled by the mean of the first two; one shaped otherwise has the helmet scaled
along each axis by its own ratio. A Kul Tiran male's is 1.29 times a Human's front to back, 1.52
across and 1.21 high: scaled 1.40 all round, a Human helmet stood out in front of his face and
rode high on his head (tenth client test).

The height follows the top of the cranium, unless the race's brow ridge sits higher on its
skull than the template's, scaled, would: a Kul Tiran male's forehead is short for the width of
his skull (crown to brow 1.17 times a Human's), and a crown-fitted helmet came down over his
eyes. There the helmet is raised until its brow line meets his, and a quarter of his
crown-to-brow distance further: on the ridge itself it still read as too low in game.

The helmet is also turned to the way the cranium faces. The template's cranium, scaled, is laid
on the race's in the side view (a turn about the left-right axis and an offset, by iterative
closest points) and the helmet takes that turn, its origin going where the template's lands.
The fit is taken from two cuts of the cranium and used only when they agree. Then half the
difference in how far the race's stand and walk animations tip the head down from the
template's is turned back, about the brow, so the helmet sits level in the poses it is mostly
seen in rather than only on the skull at rest: a Kul Tiran male holds his head 9 degrees lower
than a Human, and a helmet fitted to his skull alone still looked tipped forward. BfA had his
helmet bone tipped 2.9 degrees down for his own helmets (ninth client test); that turn is
replaced, or dropped when there is none to make.

The scale and the turn are constant tracks (on a global sequence, so every animation keeps
them) on the attachment's own bone. The client hands an attached model the bone's whole matrix,
so the helmet grows and turns about the bone's pivot; the attachment position is set so that
the turned, scaled point lands on the fitted spot. Only a bone nothing else uses is changed -
BfA's character models carry one just for the helmet.

M2 (12340): 0x14 global sequences (u32 durations), 0x1C sequences (0x40 each: +0 animation id,
+2 variation, +0xC flags, 0x20 = keyed in the .m2 itself), 0x2C bones (0x58 each: +4 flags, +8
parent, +0x24 rotation track (keys: 4 int16, the client's compressed quaternion), +0x38 scale
track, +0x4C pivot), 0x34 key-bone lookup (6 = head), 0x3C vertices (48: +12 weights[4], +16
bone indices[4]), 0xF0 attachments (0x28: +0 id, +4 bone, +8 position), 0xF8 attachment lookup,
0x100 events (0x24: +8 bone), 0x108 lights (0x9C: +2 bone), 0x120 ribbons (0xB0: +4 bone), 0x128
particles (0x1DC: +0x14 bone). A track (20 bytes): u16 interpolation, i16 global sequence,
M2Array of per-sequence timestamp arrays, M2Array of value arrays. Skin: 0x04 vertex lookup, 0x0C
indices, 0x1C submeshes (48: +0 id, +2 level, +8 index start, +10 index count).
"""

from __future__ import annotations

import math
import struct
from collections import deque
from dataclasses import dataclass

HELM = 11
HEAD_KEY_BONE = 6
CRANIUM = 0.45          # the top share of the skull's height a helmet is fitted to
MIN_SCALE = 1.02        # a cranium within 2% of the template's keeps the helmet's own size
PROPORTIONS = 1.15      # a cranium whose axis ratios differ by more than this is fitted axis by axis
MIDLINE = 0.12          # the profile: skull vertices this share of its width either side of its centre line
BROW_BAND = (0.2, 0.6)  # the brow ridge lies this far down the face, crown to chin (the nose below it)
BROW_CLEARANCE = 0.25   # a brow-fitted helmet's brow line sits this share of crown-to-brow above the ridge
TURN_CUTS = (0.0, 0.25) # the cranium the turn is fitted to: down to this share of crown-to-brow under the brow
TURN_AGREE = 2.0        # degrees: the fits from the two cuts must agree this closely
TURN_POINTS = 100       # a cranium with fewer vertices than this has no bearing worth fitting
TURN_MIN = 1.0          # degrees: a smaller turn is left out
POSTURE_ANIMS = (0, 4)  # Stand, Walk: the poses a helmet is mostly seen in
POSTURE_SHARE = 0.5     # of the race's head held lower than the template's, turned back on the helmet
FIT_ROUNDS = 80
FIT_CELL = 0.05         # the nearest-point grid's cell
SEQ_IN_M2 = 0x20
BONE_TRANSFORMED = 0x200
INTERPOLATE_LINEAR = 1


class HelmError(ValueError):
    pass


def _head_bone(m2: bytes) -> int:
    n_b, _o_b = struct.unpack_from("<II", m2, 0x2C)
    n_k, o_k = struct.unpack_from("<II", m2, 0x34)
    if n_k <= HEAD_KEY_BONE:
        raise HelmError("model has no head key bone")
    head = struct.unpack_from("<h", m2, o_k + HEAD_KEY_BONE * 2)[0]
    if not 0 <= head < n_b:
        raise HelmError("model has no head key bone")
    return head


def _head_bones(m2: bytes) -> set[int]:
    n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
    head = _head_bone(m2)
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


def _skull_points(m2: bytes, skin: bytes) -> list[tuple[float, float, float]]:
    """The body mesh's vertices skinned mostly to the head or its children (rest positions)."""
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
    return points


def skull_box(m2: bytes, skin: bytes, top_share: float = 1.0) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(min, max) of the skull's vertices (``_skull_points``); with ``top_share`` below 1, of
    those in that top share of the skull's height only."""
    points = _skull_points(m2, skin)
    if top_share < 1.0:
        top, bottom = max(p[2] for p in points), min(p[2] for p in points)
        points = [p for p in points if p[2] >= top - top_share * (top - bottom)]
    return tuple(map(min, zip(*points))), tuple(map(max, zip(*points)))


def _brow(m2: bytes, skin: bytes) -> tuple[float, float] | None:
    """(x, z) of the brow ridge: the most forward point of the skull's centre-line profile
    between BROW_BAND of the way down from the crown to the chin (the profile's lowest point in
    front). None when the skull has no profile to read."""
    points = _skull_points(m2, skin)
    ys = [p[1] for p in points]
    centre, width = (max(ys) + min(ys)) / 2, max(ys) - min(ys)
    profile = [p for p in points if abs(p[1] - centre) <= MIDLINE * width]
    if len(profile) < 3:
        return None
    middle = sorted(p[0] for p in profile)[len(profile) // 2]
    front = [p for p in profile if p[0] > middle]
    if not front:
        return None
    crown, chin = max(p[2] for p in points), min(p[2] for p in front)
    band = [p for p in front if crown - BROW_BAND[1] * (crown - chin) <= p[2] <= crown - BROW_BAND[0] * (crown - chin)]
    if crown <= chin or not band:
        return None
    ridge = max(band, key=lambda p: p[0])
    return ridge[0], ridge[2]


def brow_height(m2: bytes, skin: bytes) -> float | None:
    """Height of the brow ridge (``_brow``), or None."""
    ridge = _brow(m2, skin)
    return ridge[1] if ridge else None


def helm_attachment(m2: bytes) -> tuple[int, tuple[float, float, float]]:
    """(file offset of its position, position) of the attachment the client uses for helmets."""
    n_a, o_a = struct.unpack_from("<II", m2, 0xF0)
    n_l, o_l = struct.unpack_from("<II", m2, 0xF8)
    index = struct.unpack_from("<h", m2, o_l + HELM * 2)[0] if n_l > HELM else -1
    if not 0 <= index < n_a or struct.unpack_from("<I", m2, o_a + index * 0x28)[0] != HELM:
        raise HelmError("model has no helmet attachment (11)")
    pos = o_a + index * 0x28 + 8
    return pos, struct.unpack_from("<3f", m2, pos)


def cranium_scales(m2: bytes, skin: bytes, template_m2: bytes, template_skin: bytes) -> tuple[float, float, float]:
    """The helmet's scale along x (front to back), y (across) and z (up): the cranium's length and
    width over the template's, and the skull's height over the template's (the cranium being its
    top share, the two height ratios are one). Within PROPORTIONS of each other, the mean of the
    first two all round (1.0 when within MIN_SCALE of the template's); otherwise each axis's own."""
    t_lo, t_hi = skull_box(template_m2, template_skin, CRANIUM)
    lo, hi = skull_box(m2, skin, CRANIUM)
    (t_bottom, t_top), (bottom, top) = ((b[2], t[2]) for b, t in (skull_box(template_m2, template_skin), skull_box(m2, skin)))
    spans = ((hi[0] - lo[0], t_hi[0] - t_lo[0]), (hi[1] - lo[1], t_hi[1] - t_lo[1]), (top - bottom, t_top - t_bottom))
    if any(theirs <= 0 for _own, theirs in spans):
        raise HelmError("the look template's cranium is flat")
    ratios = tuple(own / theirs for own, theirs in spans)
    if min(ratios) > 0 and max(ratios) / min(ratios) > PROPORTIONS:
        return ratios
    scale = (ratios[0] + ratios[1]) / 2
    if scale < MIN_SCALE:
        scale = 1.0
    return scale, scale, scale


def fit_helmet(m2: bytes, skin: bytes, template_m2: bytes, template_skin: bytes
               ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(scales, spot): the helmet's scale per axis (``cranium_scales``), and its origin as far from
    the cranium's front-to-back centre as the template's, times the front-to-back scale. Its
    height: as far above the top of the cranium as the template's, times the height scale - or,
    where that would bring the helmet's brow below the race's (a short forehead for the skull), as
    far above the brow ridge (``brow_height``), plus BROW_CLEARANCE of the crown-to-brow distance
    so the helmet's brow line clears the ridge instead of resting on it."""
    t_lo, t_hi = skull_box(template_m2, template_skin, CRANIUM)
    lo, hi = skull_box(m2, skin, CRANIUM)
    _off, t_att = helm_attachment(template_m2)
    _off, old = helm_attachment(m2)
    scales = cranium_scales(m2, skin, template_m2, template_skin)
    height = hi[2] + scales[2] * (t_att[2] - t_hi[2])
    brow, t_brow = brow_height(m2, skin), brow_height(template_m2, template_skin)
    if brow is not None and t_brow is not None:
        brow_fit = brow + scales[2] * (t_att[2] - t_brow)
        if brow_fit > height:
            height = brow_fit + BROW_CLEARANCE * (hi[2] - brow)
    spot = ((lo[0] + hi[0]) / 2 + scales[0] * (t_att[0] - (t_lo[0] + t_hi[0]) / 2), old[1], height)
    return scales, spot


def _cranium(m2: bytes, skin: bytes, below: float) -> list[tuple[float, float, float]]:
    """The skull's vertices from ``below`` of crown-to-brow under the brow ridge up."""
    points, brow = _skull_points(m2, skin), brow_height(m2, skin)
    if brow is None:
        return []
    cut = brow - below * (max(p[2] for p in points) - brow)
    return [p for p in points if p[2] >= cut]


class _Nearest:
    """The nearest of a fixed set of points, through a grid of FIT_CELL buckets searched ring by ring."""

    def __init__(self, points: list[tuple[float, float, float]]):
        self.points = points
        self.cells: dict[tuple[int, int, int], list[int]] = {}
        for i, p in enumerate(points):
            self.cells.setdefault(self._cell(p), []).append(i)

    @staticmethod
    def _cell(p: tuple[float, float, float]) -> tuple[int, int, int]:
        return math.floor(p[0] / FIT_CELL), math.floor(p[1] / FIT_CELL), math.floor(p[2] / FIT_CELL)

    def index(self, p: tuple[float, float, float]) -> int:
        cx, cy, cz = self._cell(p)
        best, best_d, ring = -1, math.inf, 0
        while True:
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for i in self.cells.get((cx + dx, cy + dy, cz + dz), ()):
                            q = self.points[i]
                            d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2
                            if d < best_d:
                                best, best_d = i, d
            # every point not yet searched is at least `ring` cells away
            if best >= 0 and best_d <= (ring * FIT_CELL) ** 2:
                return best
            ring += 1


def _turned(points, turn, x, z):
    """``points`` turned ``turn`` radians about the y axis (x' = cos x + sin z), then offset."""
    c, s = math.cos(turn), math.sin(turn)
    return [(c * px + s * pz + x, py, -s * px + c * pz + z) for px, py, pz in points]


def _fit_side_view(src, dst) -> tuple[float, float, float]:
    """(turn, x, z): the turn about the y axis and x/z offset that best lay ``src`` on ``dst``,
    by iterative closest points matched both ways, so that a cranium reaching further down on one
    model doesn't drag the fit."""
    def mean(ps, k):
        return sum(p[k] for p in ps) / len(ps)
    turn, x, z = 0.0, mean(dst, 0) - mean(src, 0), mean(dst, 2) - mean(src, 2)
    to_dst = _Nearest(dst)
    for _ in range(FIT_ROUNDS):
        moved = _turned(src, turn, x, z)
        to_src = _Nearest(moved)
        pairs = [(a, dst[to_dst.index(m)]) for a, m in zip(src, moved)]
        pairs += [(src[to_src.index(b)], b) for b in dst]
        ax, az = mean([a for a, _ in pairs], 0), mean([a for a, _ in pairs], 2)
        bx, bz = mean([b for _, b in pairs], 0), mean([b for _, b in pairs], 2)
        dot = cross = 0.0
        for a, b in pairs:
            u, w, p, q = a[0] - ax, a[2] - az, b[0] - bx, b[2] - bz
            dot, cross = dot + p * u + q * w, cross + p * w - q * u
        new = math.atan2(cross, dot)
        x = bx - (math.cos(new) * ax + math.sin(new) * az)
        z = bz - (-math.sin(new) * ax + math.cos(new) * az)
        settled, turn = abs(new - turn) < 1e-8, new
        if settled:
            break
    return turn, x, z


def cranium_turn(m2: bytes, skin: bytes, template_m2: bytes, template_skin: bytes,
                 scales: tuple[float, float, float]) -> tuple[float, float] | None:
    """(turn, x): how many degrees about the y axis the race's cranium faces from the template's
    (negative: further up), and the x the template's helmet origin is carried to - the template's
    cranium, scaled by ``scales``, laid on the race's (``_fit_side_view``) down to each of
    TURN_CUTS under the brow ridge. None when a cranium has fewer than TURN_POINTS vertices or the
    two fits disagree by more than TURN_AGREE degrees: a shape too unlike the template's to say
    which way it faces."""
    fits = []
    for below in TURN_CUTS:
        src = [(p[0] * scales[0], p[1] * scales[1], p[2] * scales[2]) for p in _cranium(template_m2, template_skin, below)]
        dst = _cranium(m2, skin, below)
        if min(len(src), len(dst)) < TURN_POINTS:
            return None
        fits.append(_fit_side_view(src, dst))
    turns = [math.degrees(f[0]) for f in fits]
    if max(turns) - min(turns) > TURN_AGREE:
        return None
    turn, x, _z = fits[0]
    _off, t_att = helm_attachment(template_m2)
    return sum(turns) / len(turns), math.cos(turn) * t_att[0] * scales[0] + math.sin(turn) * t_att[2] * scales[2] + x


def _quaternion(raw: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    return tuple(((s + 32768) if s < 0 else (s - 32767)) / 32767 for s in raw)


def _product(a, b):
    """The quaternion turning by ``b`` and then by ``a``."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _first_turn(m2: bytes, bone: int, sequence: int) -> tuple[float, float, float, float]:
    """A bone's rotation at the first key of ``sequence`` (or of its global sequence); none, no turn."""
    _n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
    _interp, gseq, _n_ts, _o_ts, n_vals, o_vals = struct.unpack_from("<HhIIII", m2, o_b + bone * 0x58 + 0x24)
    index = 0 if gseq >= 0 else sequence
    if index >= n_vals:
        return 0.0, 0.0, 0.0, 1.0
    n_keys, o_keys = struct.unpack_from("<II", m2, o_vals + index * 8)
    return _quaternion(struct.unpack_from("<4h", m2, o_keys)) if n_keys else (0.0, 0.0, 0.0, 1.0)


def head_pitch(m2: bytes, animation: int) -> float | None:
    """Degrees the head is tipped down (positive) from the rest pose at the first key of
    ``animation``'s first variation: the head bone's forward axis, turned by its chain to the root.
    None when the model has no such sequence keyed in the .m2 itself."""
    n_s, o_s = struct.unpack_from("<II", m2, 0x1C)
    sequence = next((i for i in range(n_s) if struct.unpack_from("<HH", m2, o_s + i * 0x40) == (animation, 0)
                     and struct.unpack_from("<I", m2, o_s + i * 0x40 + 0xC)[0] & SEQ_IN_M2), None)
    if sequence is None:
        return None
    _n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
    chain, bone = [], _head_bone(m2)
    while bone >= 0 and len(chain) < 256:
        chain.append(bone)
        bone = struct.unpack_from("<h", m2, o_b + bone * 0x58 + 8)[0]
    turn = (0.0, 0.0, 0.0, 1.0)
    for bone in reversed(chain):                   # root first: world = parent * local
        turn = _product(turn, _first_turn(m2, bone, sequence))
    x, y, z, w = turn
    norm = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    forward = (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w))
    return math.degrees(math.atan2(-forward[2], math.hypot(forward[0], forward[1])))


def posture_turn(m2: bytes, template_m2: bytes) -> float:
    """Degrees to turn the helmet (negative: front up) for the race holding its head lower than the
    template in the POSTURE_ANIMS: POSTURE_SHARE of the mean difference; 0 when a model has none."""
    diffs = []
    for animation in POSTURE_ANIMS:
        own, theirs = head_pitch(m2, animation), head_pitch(template_m2, animation)
        if own is not None and theirs is not None:
            diffs.append(own - theirs)
    return -POSTURE_SHARE * sum(diffs) / len(diffs) if diffs else 0.0


@dataclass
class HelmFit:
    scales: tuple[float, float, float]   # the helmet's scale along x, y, z; (1, 1, 1) keeps its own size
    spot: tuple[float, float, float]     # where the helmet's origin lands on the head
    old: tuple[float, float, float]      # attachment 11's position before
    bone: int                            # attachment 11's bone
    scaled_bone: bool                    # False: the bone is shared, so only the spot moved
    turn: float = 0.0                    # degrees about the y axis the helmet is turned (negative: front up)


def _bone_is_helmets_alone(m2: bytes, bone: int) -> bool:
    """Nothing but the helmet attachment hangs off ``bone``: no vertex weight, child bone, key-bone
    slot, other attachment, event, light, ribbon or particle emitter."""
    n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
    if any(struct.unpack_from("<h", m2, o_b + i * 0x58 + 8)[0] == bone for i in range(n_b)):
        return False
    n_k, o_k = struct.unpack_from("<II", m2, 0x34)
    if bone in struct.unpack_from(f"<{n_k}h", m2, o_k):
        return False
    n_v, o_v = struct.unpack_from("<II", m2, 0x3C)
    for v in range(n_v):
        rec = o_v + v * 48
        if any(m2[rec + 12 + k] and m2[rec + 16 + k] == bone for k in range(4)):
            return False
    n_a, o_a = struct.unpack_from("<II", m2, 0xF0)
    for i in range(n_a):
        att_id, att_bone = struct.unpack_from("<IH", m2, o_a + i * 0x28)
        if att_bone == bone and att_id != HELM:
            return False
    for header, stride, field, fmt in ((0x100, 0x24, 8, "<I"), (0x108, 0x9C, 2, "<h"),
                                       (0x120, 0xB0, 4, "<I"), (0x128, 0x1DC, 0x14, "<H")):
        n, o = struct.unpack_from("<II", m2, header)
        if any(struct.unpack_from(fmt, m2, o + i * stride + field)[0] == bone for i in range(n)):
            return False
    return True


def _append(out: bytearray, payload: bytes) -> int:
    """Append ``payload`` 16-byte aligned to an MD20 model; returns its offset."""
    out.extend(bytes(-len(out) % 16))
    at = len(out)
    out.extend(payload)
    return at


def _global_sequence(out: bytearray) -> tuple[int, int]:
    """(index, duration) of a global sequence with a duration, appending one when there is none."""
    n, o = struct.unpack_from("<II", out, 0x14)
    durations = list(struct.unpack_from(f"<{n}I", out, o)) if n else []
    for i, duration in enumerate(durations):
        if duration:
            return i, duration
    durations.append(1000)
    struct.pack_into("<II", out, 0x14, len(durations), _append(out, struct.pack(f"<{len(durations)}I", *durations)))
    return len(durations) - 1, 1000


def _set_constant_track(out: bytearray, bone: int, field: int, key: bytes) -> None:
    """Give ``bone``'s track at ``field`` the constant ``key`` on a global sequence (so it is in
    force whatever animation plays); a track already on a global sequence has every key
    rewritten."""
    _n_b, o_b = struct.unpack_from("<II", out, 0x2C)
    track = o_b + bone * 0x58 + field
    _interp, gseq, n_ts, _o_ts, n_vals, o_vals = struct.unpack_from("<HhIIII", out, track)
    if gseq >= 0 and n_ts == 1 and n_vals == 1:
        n_keys, o_keys = struct.unpack_from("<II", out, o_vals)
        for k in range(n_keys):
            out[o_keys + k * len(key):o_keys + (k + 1) * len(key)] = key
    else:
        index, duration = _global_sequence(out)
        times = _append(out, struct.pack("<2I", 0, duration))
        values = _append(out, key * 2)
        outer_times = _append(out, struct.pack("<II", 2, times))
        outer_values = _append(out, struct.pack("<II", 2, values))
        struct.pack_into("<HhIIII", out, track, INTERPOLATE_LINEAR, index, 1, outer_times, 1, outer_values)
    flags_at = o_b + bone * 0x58 + 4
    struct.pack_into("<I", out, flags_at, struct.unpack_from("<I", out, flags_at)[0] | BONE_TRANSFORMED)


def _set_constant_scale(out: bytearray, bone: int, scales: tuple[float, float, float]) -> None:
    _set_constant_track(out, bone, 0x38, struct.pack("<3f", *scales))


def _compress(v: float) -> int:
    """A quaternion component as the client stores it: an int16 s read back as (s + 32768) / 32767
    when negative, (s - 32767) / 32767 otherwise."""
    return round(v * 32767 - 32768) if v > 0 else round(v * 32767 + 32767)


def _has_turn(m2: bytes, bone: int) -> bool:
    _n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
    return struct.unpack_from("<I", m2, o_b + bone * 0x58 + 0x24 + 12)[0] > 0


def _set_constant_turn(out: bytearray, bone: int, degrees: float) -> None:
    """Give ``bone`` a rotation track holding a turn of ``degrees`` about the y axis."""
    half = math.radians(degrees) / 2
    _set_constant_track(out, bone, 0x24, struct.pack("<4h", *(_compress(v) for v in (0.0, math.sin(half), 0.0, math.cos(half)))))


def fit_attachment(m2: bytes, skin: bytes, template_m2: bytes, template_skin: bytes, *,
                   alone: bool = True) -> HelmFit:
    """Where the template's helmets go on ``m2``'s head (``fit_helmet``, ``cranium_turn``,
    ``posture_turn``), without changing it. ``alone`` False (attachment 11's bone carries other
    things) gives the spot only: no scale, no turn."""
    if m2[:4] != b"MD20":
        raise HelmError("not an MD20 model")
    scales, spot = fit_helmet(m2, skin, template_m2, template_skin)
    off, old = helm_attachment(m2)
    bone = struct.unpack_from("<H", m2, off - 4)[0]
    scaled = scales != (1.0, 1.0, 1.0) and alone
    turn = 0.0
    if alone:
        found = cranium_turn(m2, skin, template_m2, template_skin, scales)
        if found is not None:
            turn, spot = found[0], (found[1], spot[1], spot[2])
        lean = posture_turn(m2, template_m2)
        ridge = _brow(m2, skin)
        if abs(turn + lean) >= TURN_MIN:
            if lean and ridge:
                # turned about the brow ridge, so the helmet's brow line stays where it was fitted
                c, n = math.cos(math.radians(lean)), math.sin(math.radians(lean))
                dx, dz = spot[0] - ridge[0], spot[2] - ridge[1]
                spot = (ridge[0] + c * dx + n * dz, spot[1], ridge[1] - n * dx + c * dz)
            turn += lean
        else:
            turn = 0.0
    return HelmFit(scales if scaled else (1.0, 1.0, 1.0), spot, old, bone, scaled, turn)


def place_helm_attachment(m2: bytes, skin: bytes, template_m2: bytes, template_skin: bytes
                          ) -> tuple[bytes, HelmFit]:
    """``m2`` with the template's helmets fitted (``fit_attachment``): attachment 11's bone scaled
    and turned, and the attachment placed so that its turned, scaled position is the fitted spot.
    A shared bone is left as it is and only the spot moves. Rebuilding over the result gives the
    same model."""
    if m2[:4] != b"MD20":
        raise HelmError("not an MD20 model")
    off, _old = helm_attachment(m2)
    bone = struct.unpack_from("<H", m2, off - 4)[0]
    alone = _bone_is_helmets_alone(m2, bone)
    fit = fit_attachment(m2, skin, template_m2, template_skin, alone=alone)
    out = bytearray(m2)
    if alone and (fit.turn or _has_turn(m2, bone)):
        _set_constant_turn(out, bone, fit.turn)          # BfA's own turn was for its own helmets
    if fit.scaled_bone:
        _set_constant_scale(out, bone, fit.scales)
    if alone:
        # the client turns and scales about the pivot: P lands at pivot + R * S * (P - pivot)
        _n_b, o_b = struct.unpack_from("<II", out, 0x2C)
        px, py, pz = struct.unpack_from("<3f", out, o_b + bone * 0x58 + 0x4C)
        s = fit.scales
        c, n = math.cos(math.radians(fit.turn)), math.sin(math.radians(fit.turn))
        dx, dy, dz = fit.spot[0] - px, fit.spot[1] - py, fit.spot[2] - pz
        position = (px + (c * dx - n * dz) / s[0], py + dy / s[1], pz + (n * dx + c * dz) / s[2])
    else:
        position = fit.spot
    struct.pack_into("<3f", out, off, *position)
    return bytes(out), fit


@dataclass
class Attachment:
    """A model's own attachment 11 as its source shipped it: the bone, the position, the bone's
    flags and, per track (TRACKS), the one key it holds throughout (None: no track)."""
    bone: int
    position: tuple[float, float, float]
    flags: int
    keys: dict[int, bytes | None]


TRACKS = {0x10: 12, 0x24: 8, 0x38: 12}  # translation, rotation (compressed quaternion), scale: key sizes


def _clear_track(out: bytearray, bone: int, field: int) -> None:
    _n_b, o_b = struct.unpack_from("<II", out, 0x2C)
    track = o_b + bone * 0x58 + field
    if struct.unpack_from("<I", out, track + 12)[0]:
        struct.pack_into("<HhIIII", out, track, 0, -1, 0, 0, 0, 0)


def set_attachment(m2: bytes, attachment: Attachment) -> bytes:
    """``m2`` with attachment 11 and its bone put back as ``attachment`` describes - undoing a
    fit (``place_helm_attachment``) when the race wears its own helmets again. A model already
    like that comes back unchanged."""
    if m2[:4] != b"MD20":
        raise HelmError("not an MD20 model")
    off, _old = helm_attachment(m2)
    bone = struct.unpack_from("<H", m2, off - 4)[0]
    if bone != attachment.bone:
        raise HelmError(f"attachment 11 hangs from bone {bone}, not {attachment.bone} as in the source")
    out = bytearray(m2)
    struct.pack_into("<3f", out, off, *attachment.position)
    for field, key in sorted(attachment.keys.items()):
        if key is None:
            _clear_track(out, bone, field)
        else:
            _set_constant_track(out, bone, field, key)
    _n_b, o_b = struct.unpack_from("<II", out, 0x2C)
    flags_at = o_b + bone * 0x58 + 4
    flags = struct.unpack_from("<I", out, flags_at)[0]
    struct.pack_into("<I", out, flags_at, (flags & ~BONE_TRANSFORMED) | (attachment.flags & BONE_TRANSFORMED))
    return bytes(out)
