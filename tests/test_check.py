"""Checker tests - synthetic WotLK MD20 + .skin, verifying the diagnostics."""

import struct

from retroport.check import check_model
from retroport.formats import structs as S


def _m2a(buf, off, count, offset):
    struct.pack_into("<II", buf, off, count, offset)


def build_md20():
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    struct.pack_into("<I", b, S.HDR_GLOBAL_FLAGS, S.FLAG_USE_COMBINER_COMBOS)  # 0x08
    _m2a(b, S.HDR_BONES, 10, 0)
    _m2a(b, S.HDR_VERTICES, 100, 0)
    struct.pack_into("<I", b, S.HDR_NUM_SKIN_PROFILES, 1)
    _m2a(b, S.HDR_MATERIALS, 3, 0)
    _m2a(b, S.HDR_TEXTURE_COMBINER_COMBOS, 4, 0)

    tex_off = len(b)                         # textures appended after the header
    path = b"Textures\\foo.blp\x00"
    recs = bytearray(2 * S.M2TEXTURE)
    path_off = tex_off + len(recs)
    struct.pack_into("<II", recs, 0, 0, 0)                    # tex0 type 0 (hardcoded)
    struct.pack_into("<II", recs, S.TEXTURE_FILENAME, len(path), path_off)
    struct.pack_into("<II", recs, S.M2TEXTURE + 0, 1, 0)     # tex1 type 1 (runtime)
    struct.pack_into("<II", recs, S.M2TEXTURE + S.TEXTURE_FILENAME, 0, 0)
    b += recs
    b += path
    _m2a(b, S.HDR_TEXTURES, 2, tex_off)
    return bytes(b)


def build_skin():
    sub_off = 0x30
    n_sub, n_bat = 2, 2
    bat_off = sub_off + n_sub * S.M2SKINSECTION
    total = bat_off + n_bat * S.M2BATCH
    s = bytearray(total)
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_SUBMESHES, n_sub, sub_off)
    _m2a(s, S.SKIN_BATCHES, n_bat, bat_off)
    struct.pack_into("<I", s, S.SKIN_BONE_COUNT_MAX, 64)
    struct.pack_into("<H", s, sub_off + 0 * S.M2SKINSECTION + S.SKINSECTION_ID, 0)     # body
    struct.pack_into("<H", s, sub_off + 1 * S.M2SKINSECTION + S.SKINSECTION_ID, 101)   # hair
    struct.pack_into("<H", s, bat_off + 0 * S.M2BATCH + S.BATCH_SHADER, 0)
    struct.pack_into("<H", s, bat_off + 1 * S.M2BATCH + S.BATCH_SHADER, 0x8000)
    return bytes(s)


def test_check_reads_md20_and_skin(tmp_path):
    (tmp_path / "model.m2").write_bytes(build_md20())
    (tmp_path / "model00.skin").write_bytes(build_skin())

    res = check_model(str(tmp_path / "model.m2"))
    assert res.version == 264
    assert res.combiner_combos is True
    assert res.combiner_combo_count == 4
    assert res.n_views == 1
    assert res.bones == 10
    assert res.materials == 3
    assert res.textures[0] == (0, "Textures\\foo.blp")
    assert res.textures[1][0] == 1 and res.textures[1][1] == ""    # runtime texture: empty is fine

    assert len(res.skins) == 1
    skin = res.skins[0]
    assert skin.present and skin.magic_ok
    assert skin.geosets == [0, 101]
    assert skin.shaders == [0, 0x8000]
    assert skin.bone_count_max == 64

    checks = {f.check for f in res.report().findings}
    assert "combiner_combos" in checks      # the black-mesh warning
    assert "geosets" in checks               # the missing-parts diagnostic


def test_check_missing_skin_is_fail(tmp_path):
    (tmp_path / "model.m2").write_bytes(build_md20())     # nViews=1, but no skin on disk
    res = check_model(str(tmp_path / "model.m2"))
    fails = [f for f in res.report().findings if f.severity.value == "fail"]
    assert any(f.check == "skin_missing" for f in fails)


def test_check_handles_md21_wrapped(tmp_path):
    # a chunked MD21 wrapping the MD20 block should also be readable
    block = build_md20()
    wrapped = b"MD21" + struct.pack("<I", len(block)) + block
    (tmp_path / "m.m2").write_bytes(wrapped)
    res = check_model(str(tmp_path / "m.m2"))
    assert res.version == 264 and res.n_views == 1


def build_skin_overlimit():
    """A skin over both the triangle (21845) and submesh (59) ceilings."""
    n_sub, n_bat = 60, 1
    sub_off = 0x30
    bat_off = sub_off + n_sub * S.M2SKINSECTION
    total = bat_off + n_bat * S.M2BATCH
    s = bytearray(total)
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_VERTICES, 40000, 0)
    _m2a(s, S.SKIN_INDICES, 21846 * 3, 0)              # 21846 triangles -> over 21845
    _m2a(s, S.SKIN_SUBMESHES, n_sub, sub_off)          # 60 submeshes -> over 59
    _m2a(s, S.SKIN_BATCHES, n_bat, bat_off)
    for i in range(n_sub):
        struct.pack_into("<H", s, sub_off + i * S.M2SKINSECTION + S.SKINSECTION_ID, i)
    return bytes(s)


def test_check_flags_poly_and_submesh_ceilings(tmp_path):
    (tmp_path / "big.m2").write_bytes(build_md20())
    (tmp_path / "big00.skin").write_bytes(build_skin_overlimit())
    res = check_model(str(tmp_path / "big.m2"))
    assert res.skins[0].triangles == 21846
    checks = {f.check: f.severity.value for f in res.report().findings}
    assert checks.get("triangle_limit") == "fail"
    assert checks.get("submesh_limit") == "warn"
