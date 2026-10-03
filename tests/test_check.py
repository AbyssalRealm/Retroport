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
    """A skin over both the triangle (21845) and submesh (64) ceilings."""
    n_sub, n_bat = 65, 1
    sub_off = 0x30
    bat_off = sub_off + n_sub * S.M2SKINSECTION
    total = bat_off + n_bat * S.M2BATCH
    s = bytearray(total)
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_VERTICES, 40000, 0)
    _m2a(s, S.SKIN_INDICES, 21846 * 3, 0)              # 21846 triangles -> over 21845
    _m2a(s, S.SKIN_SUBMESHES, n_sub, sub_off)          # 65 submeshes -> over 64
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


# --- merged from Conjure: LOD gate, animation table, version high bits ---

def _skin_with_lookup(indices):
    """A .skin whose vertex lookup (local -> global) is ``indices``."""
    look_off = 0x30
    s = bytearray(look_off + 2 * len(indices))
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_VERTICES, len(indices), look_off)
    struct.pack_into(f"<{len(indices)}H", s, look_off, *indices)
    return bytes(s)


def _md20_with(n_vertices=0, sequences=(), version=264, n_views=1):
    """MD20 with a vertex count and [(anim_id, sub_id, flags)] sequences."""
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, version)
    struct.pack_into("<I", b, S.HDR_NUM_SKIN_PROFILES, n_views)
    _m2a(b, S.HDR_VERTICES, n_vertices, 0)
    seq_off = len(b)
    for anim_id, sub_id, flags in sequences:
        rec = bytearray(S.M2SEQUENCE)
        struct.pack_into("<HH", rec, 0, anim_id, sub_id)
        struct.pack_into("<I", rec, S.M2SEQUENCE_FLAGS, flags)
        b += rec
    _m2a(b, S.HDR_SEQUENCES, len(sequences), seq_off)
    return bytes(b)


def _sev(res, check):
    return {f.check: f.severity.value for f in res.report().findings}.get(check)


def test_check_flags_unmerged_lod(tmp_path):
    (tmp_path / "m.m2").write_bytes(_md20_with(n_vertices=1000))
    (tmp_path / "m00.skin").write_bytes(_skin_with_lookup([0, 5, 199]))   # spans 200 of 1000
    res = check_model(str(tmp_path / "m.m2"))
    assert res.skins[0].vertex_span == 200
    assert _sev(res, "lod_unmerged") == "fail"

    (tmp_path / "m00.skin").write_bytes(_skin_with_lookup([0, 999]))      # spans all 1000
    assert _sev(check_model(str(tmp_path / "m.m2")), "lod_unmerged") is None


def _stacked_axe(tmp_path, n_views):
    """The real axe_2h_artifactarathor_d_06 layout: 9090 vertices = skin00's 3067-vertex
    block + LOD blocks of 2509/1958/1556, each LOD skin numbering from 0."""
    (tmp_path / "axe.m2").write_bytes(_md20_with(n_vertices=9090, n_views=n_views))
    for i, span in enumerate((3067, 2509, 1958, 1556)):
        (tmp_path / f"axe{i:02d}.skin").write_bytes(_skin_with_lookup([0, span - 1]))
    return check_model(str(tmp_path / "axe.m2"))


def test_check_stacked_lod_blocks_warn_not_fail(tmp_path):
    res = _stacked_axe(tmp_path, n_views=1)
    assert _sev(res, "lod_blocks") == "warn"
    assert _sev(res, "lod_unmerged") is None and _sev(res, "lod_skin_misaligned") is None
    detail = next(f.detail for f in res.report().findings if f.check == "lod_blocks")
    assert "2509 + 1958 + 1556" in detail and "axe01.skin" in detail


def test_check_loading_a_stacked_lod_skin_is_fail(tmp_path):
    res = _stacked_axe(tmp_path, n_views=4)
    f = next(f for f in res.report().findings if f.check == "lod_skin_misaligned")
    assert f.severity.value == "fail" and "Set nViews to 1" in f.detail


def test_check_lod_gate_ignores_unconverted_md21(tmp_path):
    block = _md20_with(n_vertices=1000)
    (tmp_path / "m.m2").write_bytes(b"MD21" + struct.pack("<I", len(block)) + block)
    (tmp_path / "m00.skin").write_bytes(_skin_with_lookup([0, 199]))
    assert _sev(check_model(str(tmp_path / "m.m2")), "lod_unmerged") is None


def test_check_classifies_animations_and_missing_files(tmp_path):
    seqs = [(0, 0, 0x20), (4, 0, 0x40), (5, 0, 0), (5, 1, 0)]   # inline, alias, 2 external
    (tmp_path / "m.m2").write_bytes(_md20_with(sequences=seqs))
    (tmp_path / "m00.skin").write_bytes(_skin_with_lookup([0]))
    (tmp_path / "m0005-00.anim").write_bytes(b"\x00" * 16)       # raw (WotLK) anim present
    res = check_model(str(tmp_path / "m.m2"))
    assert res.anim_inline == 1 and res.anim_alias == ["0004-00"]
    assert res.anim_external == ["0005-00", "0005-01"]
    missing = next(f for f in res.report().findings if f.check == "anim_missing")
    assert missing.severity.value == "fail" and "0005-01" in missing.detail

    (tmp_path / "m0005-01.anim").write_bytes(b"AFM2" + b"\x00" * 12)   # modern chunked anim
    res = check_model(str(tmp_path / "m.m2"))
    assert _sev(res, "anim_missing") is None
    assert _sev(res, "anim_format") == "warn" and _sev(res, "animations") == "warn"

    (tmp_path / "m0005-01.anim").write_bytes(b"\x00" * 16)            # all present and raw
    res = check_model(str(tmp_path / "m.m2"))
    assert _sev(res, "anim_format") is None and _sev(res, "animations") == "pass"


def test_check_version_high_bits(tmp_path):
    (tmp_path / "m.m2").write_bytes(_md20_with(version=0x00010108))
    (tmp_path / "m00.skin").write_bytes(_skin_with_lookup([0]))
    res = check_model(str(tmp_path / "m.m2"))
    v = next(f for f in res.report().findings if f.check == "version")
    assert "high bits" in v.detail
