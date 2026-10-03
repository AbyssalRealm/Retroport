"""Character porting: layout remap maths, texture conversion, model UV remap, DBC rows."""

import struct

import pytest
from PIL import Image

from retroport.character import layouts as L
from retroport.character.convert import _Row, _write_dbcs
from retroport.character.model import remap_model_uvs
from retroport.character.textures import convert_composite, convert_region, group_for
from retroport.dbc import DBCError, DBCFile
from retroport.formats import structs as S

from test_dbc import _wdbc

# BfA's tables as decoded from 8.3.7 (layout 1 = 3.3.5 HD; layout 2 = allied races)
BFA_LAYOUTS = {1: {"Width": 512, "Height": 512}, 2: {"Width": 1024, "Height": 512}}
_BODY = [(0, 0, 0, 256, 128), (1, 0, 128, 256, 128), (2, 0, 256, 256, 64), (3, 256, 0, 256, 128),
         (4, 256, 128, 256, 64), (5, 256, 192, 256, 128), (6, 256, 320, 256, 128), (7, 256, 448, 256, 64)]
BFA_SECTIONS = (
    [dict(CharComponentTextureLayoutID=1, SectionType=t, X=x, Y=y, Width=w, Height=h, OverlapSectionMask=0)
     for t, x, y, w, h in _BODY + [(9, 0, 320, 256, 64), (10, 0, 384, 256, 128)]]
    + [dict(CharComponentTextureLayoutID=2, SectionType=t, X=x, Y=y, Width=w, Height=h, OverlapSectionMask=0)
       for t, x, y, w, h in _BODY + [(8, 768, 384, 256, 128), (9, 512, 0, 512, 512), (10, 512, 0, 512, 512)]]
    + [dict(CharComponentTextureLayoutID=2, SectionType=13, X=0, Y=0, Width=1024, Height=512, OverlapSectionMask=1791)])


def _layout2():
    return L.layout_from_tables(BFA_LAYOUTS, BFA_SECTIONS, 2)


def test_bfa_layout1_is_the_335_hd_layout():
    l1 = L.layout_from_tables(BFA_LAYOUTS, BFA_SECTIONS, 1)
    assert l1.sections == L.HD_335.sections and L.is_identity(l1, L.HD_335)
    assert L.HD_335.scaled(0.5).sections == L.CLASSIC_335.sections
    assert 13 not in _layout2().sections                       # overlay sections aren't regions


def test_layout2_groups_and_uv_remap():
    src, dst = _layout2(), L.HD_335
    groups = L.build_groups(src, dst)
    face = group_for(9, groups)
    assert face.section_types == (9, 10) and face.dst == L.Rect(0, 320, 256, 192)
    assert all(g.section_types != (8,) for g in groups)        # accessory has no 3.3.5 region
    assert not L.is_identity(src, dst)
    # face centre -> centre of 3.3.5's FaceUpper+FaceLower block
    assert L.remap_uv(0.75, 0.5, src, dst, groups) == pytest.approx((0.25, (320 + 96) / 512))
    # body keeps its pixel position: u doubles on the half-width layout
    assert L.remap_uv(0.30, 0.10, src, dst, groups) == pytest.approx((0.60, 0.10))
    # layout 2 has no section where 3.3.5 keeps the face
    assert L.remap_uv(0.10, 0.80, src, dst, groups) is None
    # float noise just past the edge (BfA Kul Tiran female eye glow: v = -0.001) stays in its region,
    # keeping its small overhang; real outliers don't
    u, v = L.remap_uv(0.663, -0.001, src, dst, groups)
    assert u == pytest.approx(0.663 - 0.5) and v < 0.625         # face: u -> u - 0.5
    assert L.remap_uv(0.663, -0.2, src, dst, groups) is None


def test_convert_region_splits_face_into_upper_and_lower():
    face = Image.new("RGBA", (512, 512), (255, 0, 0, 255))
    face.paste((0, 0, 255, 255), (0, 256, 512, 512))           # top red, bottom blue
    groups = L.build_groups(_layout2(), L.HD_335)
    parts = convert_region([face], group_for(9, groups), L.HD_335)
    assert parts[9].size == (256, 64) and parts[10].size == (256, 128)
    assert parts[9].getpixel((128, 32))[:3] == (255, 0, 0)
    assert parts[10].getpixel((128, 120))[:3] == (0, 0, 255)


def test_convert_composite_moves_the_face_half():
    src = Image.new("RGBA", (1024, 512), (128, 128, 128, 255))
    src.paste((0, 255, 0, 255), (512, 0, 1024, 512))           # layout 2 face half: green
    out = convert_composite(src, _layout2(), L.HD_335, L.build_groups(_layout2(), L.HD_335))
    assert out.size == (512, 512)
    assert out.getpixel((100, 400))[:3] == (0, 255, 0)          # 3.3.5 face block
    assert out.getpixel((300, 100))[:3] == (128, 128, 128)      # torso


def _model(tmp_path, uvs, tris_skin, tris_hair):
    """MD20 with vertices at `uvs`, textures [type 1, type 6], lookup [0, 1], and one skin
    with a skin-textured and a hair-textured submesh."""
    m = bytearray(0x138)
    m[0:4] = b"MD20"
    struct.pack_into("<I", m, 4, 264)
    vo = len(m)
    for u, v in uvs:
        rec = bytearray(48); struct.pack_into("<2f", rec, 32, u, v); m += rec
    S.write_m2array(m, S.HDR_VERTICES, len(uvs), vo)
    to = len(m); m += struct.pack("<4I", 1, 0, 0, 0) + struct.pack("<4I", 6, 0, 0, 0)
    S.write_m2array(m, S.HDR_TEXTURES, 2, to)
    lo = len(m); m += struct.pack("<2H", 0, 1)
    S.write_m2array(m, 0x80, 2, lo)
    (tmp_path / "c.m2").write_bytes(bytes(m))

    verts = list(range(len(uvs)))
    idx_skin = [i for t in tris_skin for i in t]
    idx_hair = [i for t in tris_hair for i in t]
    s = bytearray(0x30); s[0:4] = b"SKIN"
    vo = len(s); s += struct.pack(f"<{len(verts)}H", *verts); S.write_m2array(s, S.SKIN_VERTICES, len(verts), vo)
    io = len(s); idx = idx_skin + idx_hair; s += struct.pack(f"<{len(idx)}H", *idx)
    S.write_m2array(s, S.SKIN_INDICES, len(idx), io)
    so = len(s)
    span = lambda idx: (min(idx), max(idx) - min(idx) + 1)       # a submesh's own vertex range  # noqa: E731
    for (start, count), ist, icnt in ((span(idx_skin), 0, len(idx_skin)), (span(idx_hair), len(idx_skin), len(idx_hair))):
        rec = bytearray(48); struct.pack_into("<HHHHHH", rec, 0, 0, 0, start, count, ist, icnt); s += rec
    S.write_m2array(s, S.SKIN_SUBMESHES, 2, so)
    bo = len(s)
    for sub, combo in ((0, 0), (1, 1)):
        rec = bytearray(24); struct.pack_into("<H", rec, 4, sub); struct.pack_into("<HH", rec, 0x0E, 1, combo); s += rec
    S.write_m2array(s, S.SKIN_BATCHES, 2, bo)
    (tmp_path / "c00.skin").write_bytes(bytes(s))
    return str(tmp_path / "c.m2"), [str(tmp_path / "c00.skin")]


def _uvs(path):
    d = open(path, "rb").read(); cnt, off = S.read_m2array(d, S.HDR_VERTICES)
    return [struct.unpack_from("<2f", d, off + i * 48 + 32) for i in range(cnt)]


def test_model_remap_moves_only_skin_vertices(tmp_path):
    src, dst = _layout2(), L.HD_335
    groups = L.build_groups(src, dst)
    # 0,1,2: face (skin); 3,4,5: torso (skin, across the TorsoUpper/LegUpper seam); 6: hair only
    uvs = [(0.6, 0.2), (0.9, 0.2), (0.75, 0.8), (0.3, 0.2), (0.4, 0.2), (0.35, 0.45), (0.95, 0.95)]
    m2, skins = _model(tmp_path, uvs, [(0, 1, 2), (3, 4, 5)], [(6, 6, 6)])
    rep = remap_model_uvs(m2, skins, src, dst, groups)
    after = _uvs(m2)
    assert rep.remapped == 6 and rep.shared == 0 and rep.outside == 0
    assert rep.straddling == 0                                  # body sections share one transform
    assert after[0] == pytest.approx(L.remap_uv(0.6, 0.2, src, dst, groups))
    assert after[3] == pytest.approx((0.6, 0.2))
    assert after[6] == pytest.approx((0.95, 0.95))              # hair texture's UV untouched


def test_model_remap_reports_shared_and_straddling(tmp_path):
    src, dst = _layout2(), L.HD_335
    uvs = [(0.3, 0.2), (0.4, 0.2), (0.7, 0.3)]                 # one triangle across body and face
    m2, skins = _model(tmp_path, uvs, [(0, 1, 2)], [(2, 2, 2)])   # vertex 2 also used with hair
    rep = remap_model_uvs(m2, skins, src, dst, L.build_groups(src, dst))
    assert rep.straddling == 1 and rep.shared == 1 and rep.remapped == 2
    assert _uvs(m2)[2] == pytest.approx((0.7, 0.3))


def test_write_dbcs_appends_rows_for_the_new_race(tmp_path):
    t = tmp_path / "tpl"; t.mkdir()
    _wdbc(str(t / "CharSections.dbc"), 10, [[7, 1, 0, 0, 0, 0, 0, 17, 0, 0]])
    _wdbc(str(t / "CharHairGeosets.dbc"), 6, [[4, 1, 0, 0, 0, 1]])
    _wdbc(str(t / "CharacterFacialHairStyles.dbc"), 8, [[1, 0, 0, 0, 0, 0, 0, 0]])
    rows = [_Row(1, 0, 0, 1, ["Character\\KulTiran\\Male\\KulTiranMaleFaceLower00_00.blp",
                              "Character\\KulTiran\\Male\\KulTiranMaleFaceUpper00_00.blp", ""])]
    hair = [dict(VariationID=1, GeosetID=2, Showscalp=0)]
    facial = [dict(VariationID=3, Geoset=[2, -1, -1, -1, -1])]
    counts = _write_dbcs(str(t), str(tmp_path / "out"), 33, 0, rows, hair, facial)
    assert counts == {"CharSections": 1, "CharHairGeosets": 1, "CharacterFacialHairStyles": 1}
    cs = DBCFile.load(str(tmp_path / "out" / "CharSections.dbc"))
    new = cs.records[-1]
    assert new[:4] == [8, 33, 0, 1] and new[7:] == [1, 0, 0]
    assert cs.get_string(new[4]).endswith("FaceLower00_00.blp") and cs.get_string(new[6]) == ""
    assert DBCFile.load(str(tmp_path / "out" / "CharHairGeosets.dbc")).records[-1] == [5, 33, 0, 1, 2, 0]
    assert DBCFile.load(str(tmp_path / "out" / "CharacterFacialHairStyles.dbc")).records[-1] == [33, 0, 3, 2, 0, 0, 0, 0]
    with pytest.raises(DBCError, match="already has rows"):
        _write_dbcs(str(tmp_path / "out"), str(tmp_path / "out2"), 33, 0, rows, hair, facial)
