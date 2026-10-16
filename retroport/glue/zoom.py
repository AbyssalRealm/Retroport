"""BfA's customize zoom, carried by the create scene itself.

When BfA's create screen goes to customization its camera glides to the character's head
(``C_CharacterCreation.SetFaceCustomizeCamera``). 3.3.5's glue API has no camera call, but the
scene frame reaches every camera and animation of the scene model: ``SetCamera(i)`` takes
``cameras[i]`` (CSimpleModel::SetCamera, 0x95F9F0, bounds-checked) and ``SetSequence(id)`` /
``SetSequenceTime(id, ms)`` play an animation of the scene (0x9607E0 / 0x95F610), its camera
tracks included. So each create scene gets:

* a zoom camera per head on a grid - its height (``FIRST + k * STEP``, ``COUNT`` of them) by how
  far forward of the feet it sits (``j * FORWARD_STEP``, ``FORWARDS`` of them) by how wide its view
  is (``WIDTHS``), after the scene's own camera 0: in Stand it sits where camera 0 does; in
  ``ZOOM_IN`` it glides from there to a head-and-shoulders view of that head in ``GLIDE_MS``, and
  holds; in ``ZOOM_OUT`` it glides back;
* the two animations, an hour long, every other per-animation track of the scene copied from
  Stand (the scenes animate their flags, fire and fog on global sequences, which play on).

The character stands where the client puts it: attached to the scene's attachment 0
(CCharacterCreation, 0x4E1533), turned by that attachment's bone - BfA's scenes turn it to the
camera - and by the facing (``SetCharacterCreateFacing(-15)``, degrees, about that spot), and
scaled by that bone: BfA's Horde scene draws every character 1.185 times its size, the Alliance
scene 1.044 times, so a head, its forward and the view around it are that much bigger (unscaled,
a Horde zoom framed the chest). The view's height comes from the camera's field of view, which the
client takes as a diagonal: vertical = fov / sqrt(1 + aspect^2) (0x6BFE00).

``head_spot`` measures a race's model - the middle of its head standing, the head being the skin
the head bone and the bones under it move (horns and tusks too; not a tauren's hump): how high,
and how far forward of the feet (a hunched race's head is half a yard out, nearer the camera) -
for the screen's ``CHARCREATE_ZOOM_HEAD`` table; the screen picks the camera nearest it.
"""

from __future__ import annotations

import copy
import math
import struct
from dataclasses import dataclass

# the zoom animations: Rise and StandWound, ids AnimationData gives no flags
ZOOM_IN, ZOOM_OUT = 7, 8
GLIDE_MS = 500
HOLD_MS = 3_600_000             # an hour: the last key holds as long as anyone customizes
GLIDE_KEYS = 11
# the heads the cameras are made for: 0.6 to 2.8 yards up, 0 to 0.6 forward of the feet
FIRST, STEP, COUNT = 0.6, 0.05, 45
FORWARD_STEP, FORWARDS = 0.3, 3
# the view as measured, or that much wider: sized by the head's height alone it came out a bit too
# close for some races - dwarves, gnomes, the Horde's men (client test) - which the screen gives a
# wider one (CHARCREATE_ZOOM_WIDE, an index here)
WIDTHS = (1.0, 1.2, 1.4)
FACING = -15.0                  # SetCharacterCreateFacing: on show and on every race change
# the view: SPAN_BASE yards plus SPAN_SHARE of the head's height tall (a gnome's head is most of a
# gnome: a share alone would show it whole), the head LIFT of that above the middle (the eyes a
# third down), the character SHIFT of the screen's width right of the middle (the customization
# panel is on the left)
SPAN_BASE, SPAN_SHARE, LIFT, SHIFT = 0.35, 0.30, 0.12, 0.08
ASPECT = 16 / 9

HELM = 11                       # attachment 11: the helmet, on top of the head
KEY_HEAD = 6                    # key bone 6: the head
STAND = 0
SEQ_IN_M2 = 0x20                # the sequence's keys are in the .m2
NO_SEQUENCE = 0xFFFF

HDR_SEQUENCES, HDR_BONES, HDR_KEY_BONES, HDR_VERTICES = 0x1C, 0x2C, 0x34, 0x3C
HDR_ATTACHMENTS, HDR_ATTACHMENT_LOOKUP, HDR_CAMERAS = 0xF0, 0xF8, 0x110
SEQUENCE_SIZE, BONE_SIZE, ATTACHMENT_SIZE, VERTEX_SIZE = 0x40, 0x58, 0x28, 48


class ZoomError(ValueError):
    """A scene or model the zoom can't be made for."""


# --------------------------------------------------------------------------- posing

Matrix = tuple[list[list[float]], list[float]]      # rotation-scale rows, translation


def _quaternion(raw: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    return tuple(((s + 32768) if s < 0 else (s - 32767)) / 32767 for s in raw)


def _rotation(q: tuple[float, float, float, float]) -> list[list[float]]:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _compose(a: Matrix, b: Matrix) -> Matrix:
    """``a`` after ``b``."""
    ra, ta = a
    rb, tb = b
    return ([[sum(ra[i][k] * rb[k][j] for k in range(3)) for j in range(3)] for i in range(3)],
            [sum(ra[i][k] * tb[k] for k in range(3)) + ta[i] for i in range(3)])


def _apply(m: Matrix, p) -> tuple[float, float, float]:
    r, t = m
    return tuple(sum(r[i][k] * p[k] for k in range(3)) + t[i] for i in range(3))


def _first_key(m2: bytes, at: int, sequence: int | None, fmt: str):
    """A track's value at the first key of ``sequence`` (or of its global sequence), or None."""
    _interp, gseq, _n_ts, _o_ts, n_vals, o_vals = struct.unpack_from("<HhIIII", m2, at)
    index = 0 if gseq >= 0 else sequence
    if index is None or index >= n_vals:
        return None
    n_keys, o_keys = struct.unpack_from("<II", m2, o_vals + index * 8)
    return struct.unpack_from(fmt, m2, o_keys) if n_keys else None


def stand_sequence(m2: bytes) -> int | None:
    """Index of Stand's first variation, when its keys are in the .m2."""
    n_s, o_s = struct.unpack_from("<II", m2, HDR_SEQUENCES)
    for i in range(n_s):
        rec = o_s + i * SEQUENCE_SIZE
        if struct.unpack_from("<HH", m2, rec) == (STAND, 0) and struct.unpack_from("<I", m2, rec + 0xC)[0] & SEQ_IN_M2:
            return i
    return None


def bone_matrices(m2: bytes, sequence: int | None) -> list[Matrix]:
    """Every bone's matrix to the model at the first key of ``sequence`` (None: the rest pose)."""
    n_b, o_b = struct.unpack_from("<II", m2, HDR_BONES)
    world: list[Matrix] = []
    for b in range(n_b):
        rec = o_b + b * BONE_SIZE
        parent = struct.unpack_from("<h", m2, rec + 8)[0]
        pivot = struct.unpack_from("<3f", m2, rec + 0x4C)
        t = _first_key(m2, rec + 0x10, sequence, "<3f") or (0.0, 0.0, 0.0)
        q = _first_key(m2, rec + 0x24, sequence, "<4h")
        s = _first_key(m2, rec + 0x38, sequence, "<3f") or (1.0, 1.0, 1.0)
        r = _rotation(_quaternion(q)) if q else [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        rs = [[r[i][j] * s[j] for j in range(3)] for i in range(3)]
        local = (rs, [pivot[i] + t[i] - sum(rs[i][j] * pivot[j] for j in range(3)) for i in range(3)])
        world.append(_compose(world[parent], local) if 0 <= parent < b else local)
    return world


def attachment(m2: bytes, world: list[Matrix], attachment_id: int) -> tuple[Matrix, tuple] | None:
    """(its bone's matrix, its position on the bone) of an attachment, or None."""
    n_a, o_a = struct.unpack_from("<II", m2, HDR_ATTACHMENTS)
    n_l, o_l = struct.unpack_from("<II", m2, HDR_ATTACHMENT_LOOKUP)
    index = struct.unpack_from("<h", m2, o_l + attachment_id * 2)[0] if n_l > attachment_id else -1
    if not 0 <= index < n_a:
        return None
    rec = o_a + index * ATTACHMENT_SIZE
    bone = struct.unpack_from("<H", m2, rec + 4)[0]
    if bone >= len(world):
        return None
    return world[bone], struct.unpack_from("<3f", m2, rec + 8)


def _head_bones(m2: bytes) -> set[int]:
    """The head bone (key bone 6) and every bone under it."""
    n_k, o_k = struct.unpack_from("<II", m2, HDR_KEY_BONES)
    head = struct.unpack_from("<h", m2, o_k + KEY_HEAD * 2)[0] if n_k > KEY_HEAD else -1
    n_b, o_b = struct.unpack_from("<II", m2, HDR_BONES)
    if not 0 <= head < n_b:
        return set()
    parents = [struct.unpack_from("<h", m2, o_b + b * BONE_SIZE + 8)[0] for b in range(n_b)]
    under = set()
    for b in range(n_b):
        x, hops = b, 0
        while 0 <= x < n_b and hops <= n_b:
            if x == head:
                under.add(b)
                break
            x, hops = parents[x], hops + 1
    return under


def _body_vertices(skin: bytes) -> set[int]:
    """The model vertices of the skin's body (submesh id 0)."""
    n_l, o_l = struct.unpack_from("<II", skin, 0x04)
    n_t, o_t = struct.unpack_from("<II", skin, 0x0C)
    n_s, o_s = struct.unpack_from("<II", skin, 0x1C)
    lookup = struct.unpack_from(f"<{n_l}H", skin, o_l)
    triangles = struct.unpack_from(f"<{n_t}H", skin, o_t)
    wide = n_t > 0xFFFF
    out: set[int] = set()
    for i in range(n_s):
        sid, level, _vs, _vc, start, count = struct.unpack_from("<6H", skin, o_s + i * 48)
        if sid == 0:
            first = (level << 16) | start if wide else start
            out.update(lookup[k] for k in triangles[first:first + count] if k < n_l)
    return out


def head_spot(m2: bytes, skin: bytes) -> tuple[float, float]:
    """(height, forward) of the middle of the head, standing: half way between the lowest and
    highest, the backmost and foremost body vertex the head bone and the bones under it carry (at
    least half their weight). A model without a head bone: a head's half height below its
    helmet attachment."""
    world = bone_matrices(m2, stand_sequence(m2))
    heads = _head_bones(m2)
    n_v, o_v = struct.unpack_from("<II", m2, HDR_VERTICES)
    points = []
    if heads:
        for v in _body_vertices(skin):
            if v >= n_v:
                continue
            rec = o_v + v * VERTEX_SIZE
            weights, bones = m2[rec + 12:rec + 16], m2[rec + 16:rec + 20]
            if sum(w for w, b in zip(weights, bones) if b in heads) < 128:
                continue
            p = struct.unpack_from("<3f", m2, rec)
            placed = [(w / 255, _apply(world[b], p)) for w, b in zip(weights, bones) if w and b < len(world)]
            points.append(tuple(sum(w * q[i] for w, q in placed) for i in range(3)))
    if points:
        xs, zs = [p[0] for p in points], [p[2] for p in points]
        return (min(zs) + max(zs)) / 2, (min(xs) + max(xs)) / 2
    helm = attachment(m2, world, HELM)
    if helm is None:
        raise ZoomError("model has neither a head bone carrying its body nor a helmet attachment")
    x, _y, z = _apply(*helm)
    return z - 0.15, x


# --------------------------------------------------------------------------- the scene

@dataclass
class Stage:
    camera: tuple[float, float, float]     # the scene camera (camera 0) and what it looks at
    target: tuple[float, float, float]
    fov: float
    feet: tuple[float, float, float]       # where the character stands: attachment 0
    heading: float                         # the way attachment 0 turns it, radians (toward the camera)
    scale: float = 1.0                     # how much bigger attachment 0's bone draws it


def _camera_point(m2: bytes, rec: int, track: int, base: int, sequence: int | None) -> tuple:
    p = struct.unpack_from("<3f", m2, rec + base)
    key = _first_key(m2, rec + track, sequence, "<3f")
    return tuple(p[i] + (key[i] if key else 0.0) for i in range(3))


def scene_stage(m2: bytes) -> Stage:
    """The scene's camera 0 and the spot the client stands the character on."""
    n_c, o_c = struct.unpack_from("<II", m2, HDR_CAMERAS)
    if not n_c:
        raise ZoomError("the scene has no camera")
    sequence = stand_sequence(m2)
    world = bone_matrices(m2, sequence)
    spot = attachment(m2, world, 0)
    if spot is None:
        raise ZoomError("the scene has no attachment 0 - the client stands the character on it")
    feet = _apply(*spot)
    r, _t = spot[0]
    scale = sum(math.sqrt(sum(r[i][j] ** 2 for i in range(3))) for j in range(3)) / 3
    return Stage(camera=_camera_point(m2, o_c, 0x10, 0x24, sequence), target=_camera_point(m2, o_c, 0x30, 0x44, sequence),
                 fov=struct.unpack_from("<f", m2, o_c + 4)[0], feet=feet, heading=math.atan2(r[1][0], r[0][0]),
                 scale=scale)


def _unit(v) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def zoom_view(stage: Stage, head: float, forward: float = 0.0, aspect: float = ASPECT,
              width: float = 1.0) -> tuple[tuple, tuple]:
    """(camera, target) of the head-and-shoulders view of a head ``head`` yards up and ``forward``
    of the feet, as the race's model has it (the character turned by the stage and FACING, and
    scaled by the stage): looking the way the scene camera does, SPAN_BASE + SPAN_SHARE * head tall
    at the head, the head LIFT of that above the middle, the character SHIFT of the screen's width
    right of the middle - all of it as much bigger as the stage draws the character, the view
    ``width`` times as wide again."""
    look = _unit([stage.target[i] - stage.camera[i] for i in range(3)])
    right = _unit((look[1], -look[0], 0.0))
    turn = stage.heading + math.radians(FACING)
    s = stage.scale
    span = (SPAN_BASE + SPAN_SHARE * head) * s * width
    centre = [stage.feet[0] + forward * s * math.cos(turn), stage.feet[1] + forward * s * math.sin(turn),
              stage.feet[2] + head * s - LIFT * span]
    centre = [centre[i] - right[i] * SHIFT * span * aspect for i in range(3)]
    vertical = stage.fov / math.sqrt(1 + aspect * aspect)
    distance = (span / 2) / math.tan(vertical / 2)
    return tuple(centre[i] - look[i] * distance for i in range(3)), tuple(centre)


def grid() -> list[tuple[float, float, float]]:
    """(head height, forward, view width) of every zoom camera, in camera order."""
    return [(round(FIRST + k * STEP, 4), round(j * FORWARD_STEP, 4), w)
            for w in WIDTHS for j in range(FORWARDS) for k in range(COUNT)]


def camera_for(head: float, forward: float, first_camera: int = 1, wide: int = 0) -> int:
    """The zoom camera for a head ``head`` yards up and ``forward`` of the feet, its view
    ``WIDTHS[wide]`` wide - the screen's own rounding (CharacterCreate.lua, CharCreate_ZoomCamera)."""
    k = max(0, min(COUNT - 1, math.floor((head - FIRST) / STEP + 0.5)))
    j = max(0, min(FORWARDS - 1, math.floor(forward / FORWARD_STEP + 0.5)))
    w = max(0, min(len(WIDTHS) - 1, wide))
    return first_camera + (w * FORWARDS + j) * COUNT + k


# --------------------------------------------------------------------------- adding the zoom

def _ease(t: float) -> float:
    return t * t * t * (t * (6 * t - 15) + 10)


def _key(p) -> tuple:
    return (*p, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _glide(start, end) -> tuple[list[int], list[tuple]]:
    """Keys from ``start`` to ``end`` over GLIDE_MS (eased), the end held to HOLD_MS."""
    times, keys = [], []
    for i in range(GLIDE_KEYS):
        t = i / (GLIDE_KEYS - 1)
        s = _ease(t)
        times.append(round(t * GLIDE_MS))
        keys.append(_key([start[k] + (end[k] - start[k]) * s for k in range(3)]))
    times.append(HOLD_MS)
    keys.append(keys[-1])
    return times, keys


def add_zoom(model, stage: Stage, aspect: float = ASPECT) -> int:
    """Add the zoom animations and cameras to a parsed create scene (a wotlkconv ``M2Model``
    holding only Stand). Returns the index of the first zoom camera."""
    from wotlkconv.m2.types import Track

    if len(model.sequences) != 1 or model.sequences[0]["id"] != STAND:
        raise ZoomError(f"a create scene holds Stand alone; this one has {[s['id'] for s in model.sequences]}")
    stand = model.sequences[0]
    for anim in (ZOOM_IN, ZOOM_OUT):
        model.sequences.append(dict(stand, id=anim, variation_index=0, duration=HOLD_MS, blend_time=0,
                                    variation_next=-1, alias_next=len(model.sequences)))
    # once a model has a lookup table the client finds every animation through it (hashed by id,
    # 0x8260C0; without one it searches the sequences), Stand too: the scene loads playing Stand, and
    # a Stand it can't find is sequence 0xFFFF - error #132. BfA's scenes come with no table.
    lookups = list(model.sequence_lookups)
    lookups += [NO_SEQUENCE] * (max(ZOOM_IN, ZOOM_OUT) + 1 - len(lookups))
    lookups[STAND], lookups[ZOOM_IN], lookups[ZOOM_OUT] = 0, 1, 2
    model.sequence_lookups = lookups

    # every per-animation track plays in the zoom animations as it does in Stand
    for track in model.tracks():
        if track.global_sequence >= 0 or len(track.timestamps) != 1:
            continue
        for _anim in (ZOOM_IN, ZOOM_OUT):
            track.timestamps.append(list(track.timestamps[0]))
            if isinstance(track, Track):
                track.values.append(copy.deepcopy(track.values[0]))

    def path(rest, near) -> Track:
        """At ``rest`` in Stand, gliding to ``near`` in ZOOM_IN and back in ZOOM_OUT."""
        zin, zout = _glide(rest, near), _glide(near, rest)
        return Track(kind="splinevec3", interpolation=1, global_sequence=-1,
                     timestamps=[[0], zin[0], zout[0]], values=[[_key(rest)], zin[1], zout[1]])

    base = model.cameras[0]
    first = len(model.cameras)
    for head, forward, width in grid():
        near, look = zoom_view(stage, head, forward, aspect, width)
        model.cameras.append({
            "type": base["type"], "fov": base["fov"], "far_clip": base["far_clip"], "near_clip": base["near_clip"],
            "positions": path(stage.camera, near), "position_base": (0.0, 0.0, 0.0),
            "target_position": path(stage.target, look), "target_position_base": (0.0, 0.0, 0.0),
            "roll": Track(kind="splinef32", interpolation=0, global_sequence=-1,
                          timestamps=[[0], [0], [0]], values=[[(0.0, 0.0, 0.0)]] * 3),
        })
    return first


def lua_settings(first_camera: int) -> str:
    """The screen's zoom settings, matching the cameras ``add_zoom`` made."""
    return (f"CHARCREATE_ZOOM = {{ first = {FIRST}, step = {STEP}, count = {COUNT}, forwardStep = {FORWARD_STEP}, "
            f"forwards = {FORWARDS}, widths = {len(WIDTHS)}, camera = {first_camera}, zoomIn = {ZOOM_IN}, zoomOut = {ZOOM_OUT}, "
            f"glide = {GLIDE_MS / 1000} }};")


def lua_heads(heads: dict[str, tuple[float, float]]) -> str:
    """``CHARCREATE_ZOOM_HEAD`` entries: ``RACE_SEX`` (the race's ClientFileString upper-cased,
    MALE / FEMALE) -> { yards up, yards forward of the feet } of the middle of its head."""
    return "\n".join(f'CHARCREATE_ZOOM_HEAD["{key}"] = {{ {h:.3f}, {f:.3f} }};' for key, (h, f) in sorted(heads.items()))


def lua_wide(wide: dict[str, int]) -> str:
    """``CHARCREATE_ZOOM_WIDE`` entries: ``RACE_SEX`` -> the index in ``WIDTHS`` of its view (none: 0)."""
    return "\n".join(f'CHARCREATE_ZOOM_WIDE["{key}"] = {w};' for key, w in sorted(wide.items()))
