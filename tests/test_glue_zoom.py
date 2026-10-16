"""The customize zoom (glue/zoom.py): where a scene stands the character, the cameras and
animations added to it, a race's head, and the Lua the screen and race packages get."""

import math
import struct

import pytest

wotlkconv = pytest.importorskip("wotlkconv")
from wotlkconv.m2.model import M2Model, parse_md20  # noqa: E402
from wotlkconv.m2.types import Track  # noqa: E402
from wotlkconv.m2.write import write_md20  # noqa: E402

from retroport.glue import build as gb  # noqa: E402
from retroport.glue import zoom  # noqa: E402
from retroport.race import glue as raceglue  # noqa: E402

FEET = (3.0, -2.0, -0.25)
CAMERA, TARGET = (-4.0, 4.0, 0.6), (3.0, -2.0, 1.0)


def _quat16(v: float) -> int:
    return round(v * 32767 - 32768) if v > 0 else round(v * 32767 + 32767)


def _turn(degrees: float) -> tuple[int, int, int, int]:
    half = math.radians(degrees) / 2
    return (_quat16(0.0), _quat16(0.0), _quat16(math.sin(half)), _quat16(math.cos(half)))


def _static(kind, value, gseq=-1):
    return Track(kind=kind, interpolation=0, global_sequence=gseq, timestamps=[[0]], values=[[value]])


def _bone(parent, pivot, turn=None, key=-1, scale=1.0):
    return {"key_bone_id": key, "flags": 0, "parent_bone": parent, "submesh_id": 0, "bone_name_crc": 0,
            "translation": _static("vec3", (0.0, 0.0, 0.0)), "rotation": _static("quat16", turn or _turn(0)),
            "scale": _static("vec3", (scale, scale, scale)), "pivot": pivot}


STAND = {"id": 0, "variation_index": 0, "duration": 3333, "movespeed": 0.0, "flags": 0x20, "frequency": 32767,
         "padding": 0, "replay_min": 0, "replay_max": 0, "blend_time": 150, "bounds_min": (-1.0, -1.0, -1.0),
         "bounds_max": (1.0, 1.0, 1.0), "bounds_radius": 1.7, "variation_next": -1, "alias_next": 0}


def _scene(lookups=(), scale=1.0) -> bytes:
    """A create scene: the camera looking at a spot (attachment 0) whose bone turns the character
    140 degrees, toward it (and scales it); a colour animated on a global sequence. BfA's scenes
    have no sequence lookup table."""
    m = M2Model(name="scene", global_loops=[1000], sequences=[dict(STAND)], sequence_lookups=list(lookups))
    m.bones = [_bone(-1, FEET, _turn(140), scale=scale)]
    m.attachments = [{"id": 0, "bone": 0, "unknown": 0, "position": FEET,
                      "animate_attached": _static("u8", 1)}]
    m.attachment_lookup = [0]
    m.colors = [{"color": Track(kind="vec3", interpolation=1, global_sequence=0, timestamps=[[0, 500]],
                                values=[[(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]]),
                 "alpha": _static("fixed16", 32767)}]
    zero = (0.0,) * 9
    m.cameras = [{"type": 0xFFFFFFFF, "fov": 0.8457, "far_clip": 500.0, "near_clip": 0.22,
                  "positions": Track(kind="splinevec3", interpolation=1, timestamps=[[0]], values=[[zero]]),
                  "position_base": CAMERA,
                  "target_position": Track(kind="splinevec3", interpolation=1, timestamps=[[0]], values=[[zero]]),
                  "target_position_base": TARGET, "roll": _static("splinef32", (0.0, 0.0, 0.0))}]
    m.camera_lookup = [0xFFFF]
    return bytes(write_md20(m))


def test_the_character_stands_on_attachment_0_turned_by_its_bone():
    stage = zoom.scene_stage(_scene())
    assert stage.feet == pytest.approx(FEET, abs=1e-5)
    assert math.degrees(stage.heading) == pytest.approx(140, abs=0.1)
    assert stage.camera == pytest.approx(CAMERA) and stage.target == pytest.approx(TARGET)
    assert stage.scale == pytest.approx(1.0)


def test_a_scene_that_draws_the_character_bigger_zooms_to_the_bigger_head():
    """BfA's Horde scene draws a character 1.185 times its size through attachment 0's bone - the
    head higher, further forward, the view around it bigger (unscaled, a zoom framed the chest)."""
    stage = zoom.scene_stage(_scene(scale=1.185))
    assert stage.scale == pytest.approx(1.185, abs=1e-4)
    assert stage.feet == pytest.approx(FEET, abs=1e-5)          # scaled about its pivot: the feet
    assert math.degrees(stage.heading) == pytest.approx(140, abs=0.1)
    near, look = zoom.zoom_view(stage, 1.9, 0.3)
    span = (zoom.SPAN_BASE + zoom.SPAN_SHARE * 1.9) * 1.185
    assert look[2] == pytest.approx(FEET[2] + 1.9 * 1.185 - zoom.LIFT * span)
    vertical = 0.8457 / math.sqrt(1 + zoom.ASPECT ** 2)
    assert math.dist(near, look) == pytest.approx(span / 2 / math.tan(vertical / 2))
    _near, level = zoom.zoom_view(stage, 1.9)
    turn = stage.heading + math.radians(zoom.FACING)
    assert (look[0] - level[0], look[1] - level[1]) == pytest.approx(
        (0.3 * 1.185 * math.cos(turn), 0.3 * 1.185 * math.sin(turn)))


def test_a_zoom_view_frames_the_head_the_way_the_scene_camera_looks():
    stage = zoom.scene_stage(_scene())
    near, look = zoom.zoom_view(stage, 1.9)
    span = zoom.SPAN_BASE + zoom.SPAN_SHARE * 1.9
    assert look[2] == pytest.approx(FEET[2] + 1.9 - zoom.LIFT * span)
    # straight down the scene camera's line of sight, at the distance the client's projection
    # gives that span (vertical fov = diagonal / sqrt(1 + aspect^2))
    view = [TARGET[i] - CAMERA[i] for i in range(3)]
    mine = [look[i] - near[i] for i in range(3)]
    cos = sum(a * b for a, b in zip(view, mine)) / (math.dist(TARGET, CAMERA) * math.dist(look, near))
    assert cos == pytest.approx(1.0, abs=1e-9)
    vertical = 0.8457 / math.sqrt(1 + zoom.ASPECT ** 2)
    assert math.dist(near, look) == pytest.approx(span / 2 / math.tan(vertical / 2))
    # a head forward of the feet moves the view with it, the way the character faces
    _near2, look2 = zoom.zoom_view(stage, 1.9, 0.5)
    turn = stage.heading + math.radians(zoom.FACING)
    assert (look2[0] - look[0], look2[1] - look[1]) == pytest.approx((0.5 * math.cos(turn), 0.5 * math.sin(turn)))


def _client_sequence(m, anim: int) -> int:
    """The sequence the client plays for an animation id (CM2Model, 0x8260C0): with a lookup table,
    the table's entry at id % size, probing on (i * i + last slot) % size past other ids; with none,
    the first sequence of that id. 0xFFFF: none."""
    table = m.sequence_lookups
    if not table:
        return next((i for i, s in enumerate(m.sequences) if s["id"] == anim), zoom.NO_SEQUENCE)
    slot, probe = anim % len(table), 0
    while (index := table[slot]) != zoom.NO_SEQUENCE and m.sequences[index]["id"] != anim:
        probe += 1
        if probe > len(table) ** 2:
            raise AssertionError(f"the client would probe for animation {anim} forever")
        slot = (probe * probe + slot) % len(table)
    return index


@pytest.mark.parametrize("lookups", [(), (0,)], ids=["no table", "a table"])
def test_the_zoom_adds_cameras_and_animations_and_leaves_the_rest_playing(lookups):
    data = _scene(lookups)
    assert _client_sequence(parse_md20(data, "scene"), 0) == 0
    out, first, cameras = gb.zoom_scene(data, "scene")
    m = parse_md20(out, "scene")
    assert first == 1 and cameras == 1 + zoom.COUNT * zoom.FORWARDS * len(zoom.WIDTHS) == len(m.cameras)
    assert [s["id"] for s in m.sequences] == [0, zoom.ZOOM_IN, zoom.ZOOM_OUT]
    assert all(s["duration"] == zoom.HOLD_MS for s in m.sequences[1:])
    # the client finds each animation, Stand too - the scene loads playing it (error #132 if not)
    assert [_client_sequence(m, anim) for anim in (0, zoom.ZOOM_IN, zoom.ZOOM_OUT)] == [0, 1, 2]
    assert _client_sequence(m, 69) == zoom.NO_SEQUENCE
    # Stand's per-animation tracks play the same in the zoom animations; global ones are untouched
    assert len(m.bones[0]["rotation"].values) == 3 and m.bones[0]["rotation"].values[1] == m.bones[0]["rotation"].values[0]
    assert len(m.colors[0]["color"].timestamps) == 1
    assert len(m.cameras[0]["positions"].timestamps) == 3
    # a zoom camera: the scene's view in Stand, the head at the end of ZOOM_IN, back in ZOOM_OUT
    stage = zoom.scene_stage(data)
    index = zoom.camera_for(1.9, 0.3)
    head, forward, width = zoom.grid()[index - 1]
    near, look = zoom.zoom_view(stage, head, forward)
    assert width == 1.0
    cam = m.cameras[index]
    pos = cam["positions"]
    assert pos.values[0][0][:3] == pytest.approx(CAMERA, abs=1e-5)
    assert pos.timestamps[1][-2:] == [zoom.GLIDE_MS, zoom.HOLD_MS]
    assert pos.values[1][-1][:3] == pytest.approx(near, abs=1e-4)
    assert pos.values[1][0][:3] == pytest.approx(CAMERA, abs=1e-5) == pos.values[2][-1][:3]
    assert cam["target_position"].values[1][-1][:3] == pytest.approx(look, abs=1e-4)
    assert cam["position_base"] == (0.0, 0.0, 0.0)
    # a wider view of the same head: the camera further back along the same line, the head as high
    wide = m.cameras[zoom.camera_for(1.9, 0.3, wide=1)]
    far, far_look = zoom.zoom_view(stage, head, forward, width=zoom.WIDTHS[1])
    assert wide["positions"].values[1][-1][:3] == pytest.approx(far, abs=1e-4)
    assert math.dist(far, far_look) == pytest.approx(zoom.WIDTHS[1] * math.dist(near, look), rel=1e-6)
    # a scene zoomed already is refused rather than zoomed twice
    with pytest.raises(zoom.ZoomError):
        zoom.add_zoom(parse_md20(out, "scene"), stage)


def test_the_screen_rounds_to_the_nearest_camera_and_clamps():
    assert zoom.camera_for(zoom.FIRST, 0.0) == 1
    assert zoom.camera_for(zoom.FIRST + 2 * zoom.STEP + 0.02, 0.0) == 3
    assert zoom.camera_for(9.0, 0.0) == zoom.COUNT
    assert zoom.camera_for(zoom.FIRST, 0.31) == 1 + zoom.COUNT
    assert zoom.camera_for(zoom.FIRST, 5.0) == 1 + (zoom.FORWARDS - 1) * zoom.COUNT
    assert zoom.camera_for(zoom.FIRST, 0.0, wide=1) == 1 + zoom.FORWARDS * zoom.COUNT
    assert zoom.camera_for(zoom.FIRST, 0.0, wide=9) == 1 + (len(zoom.WIDTHS) - 1) * zoom.FORWARDS * zoom.COUNT
    assert zoom.lua_wide({"GNOME_MALE": 1}) == 'CHARCREATE_ZOOM_WIDE["GNOME_MALE"] = 1;'


def _vertex(pos, bone):
    return struct.pack("<3f4B4B3f4f", *pos, 255, 0, 0, 0, bone, 0, 0, 0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)


def _character() -> tuple[bytes, bytes]:
    """Four vertices on the head bone (key bone 6) from 1.7 to 2.0 up, 0.1 to 0.3 forward; two on
    the root lower down; one skin submesh (id 0) over all of them."""
    m = M2Model(name="char", sequences=[dict(STAND)], sequence_lookups=[0])
    m.bones = [_bone(-1, (0.0, 0.0, 0.0)), _bone(0, (0.0, 0.0, 1.6), key=6)]
    m.key_bone_lookup = [0xFFFF] * 6 + [1]
    head = [(0.1, 0.0, 1.7), (0.3, 0.1, 1.8), (0.2, -0.1, 2.0), (0.15, 0.0, 1.9)]
    body = [(0.0, 0.0, 0.2), (0.0, 0.0, 1.0)]
    m.vertices = b"".join(_vertex(p, 1) for p in head) + b"".join(_vertex(p, 0) for p in body)
    m.vertex_count = 6
    m.num_skin_profiles = 1
    m2 = bytes(write_md20(m))
    lookup, tris = list(range(6)), [0, 1, 2, 1, 2, 3, 4, 5, 0]
    head_size, o = 48, 48
    skin = bytearray(b"SKIN" + struct.pack("<II", 6, o) + struct.pack("<II", 9, o + 12) + struct.pack("<II", 0, 0)
                     + struct.pack("<II", 1, o + 12 + 18 + 2) + struct.pack("<II", 0, 0) + struct.pack("<I", 0))
    assert len(skin) == head_size
    skin += struct.pack("<6H", *lookup) + struct.pack("<9H", *tris) + b"\0\0"
    skin += struct.pack("<6H", 0, 0, 0, 6, 0, 9) + b"\0" * 36
    return m2, bytes(skin)


def test_a_head_is_where_the_head_bone_carries_the_skin():
    m2, skin = _character()
    height, forward = zoom.head_spot(m2, skin)
    assert height == pytest.approx((1.7 + 2.0) / 2) and forward == pytest.approx((0.1 + 0.3) / 2)


def test_the_lua_names_the_cameras_and_the_heads():
    assert "camera = 1" in zoom.lua_settings(1) and f"zoomIn = {zoom.ZOOM_IN}" in zoom.lua_settings(1)
    assert zoom.lua_heads({"GNOME_MALE": (0.8, 0.0)}) == 'CHARCREATE_ZOOM_HEAD["GNOME_MALE"] = { 0.800, 0.000 };'


def test_a_race_on_the_bfa_screen_adds_its_heads_with_its_icons():
    icons = {"KULTIRAN_MALE": (0.625, 0.75, 0, 0.25)}
    heads = {"KULTIRAN_MALE": (2.335, 0.17)}
    out = raceglue.patch_character_create_lua('RETROPORT_GLUE_STYLE = "bfa";\nMAX_RACES = 32;\n', 12, icons,
                                              "KULTIRAN", heads)
    block = out[out.index("begin icons KULTIRAN"):out.index("end icons KULTIRAN")]
    assert 'CHARCREATE_ZOOM_HEAD["KULTIRAN_MALE"] = { 2.335, 0.170 };' in block
    # the glue package carries the icons block - heads included - when it is rebuilt
    carried, tags = gb.carry_race_icons('RETROPORT_GLUE_STYLE = "bfa";\n', out)
    assert tags == ["icons KULTIRAN"] and "CHARCREATE_ZOOM_HEAD" in carried
    # 3.3.5's own screen has no zoom
    stock = raceglue.patch_character_create_lua("MAX_RACES = 10;\n", 12, icons, "KULTIRAN", heads)
    assert "CHARCREATE_ZOOM_HEAD" not in stock
