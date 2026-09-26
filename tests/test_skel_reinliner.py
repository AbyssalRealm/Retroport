"""Re-inliner correctness tests using synthetic M2 + .skel fixtures.

These don't use real Blizzard files - they construct byte layouts that exactly
match the documented structures, then check that after re-inlining we can follow
bone -> M2Track -> per-animation array -> keyframe leaf and recover the original
bytes. That validates the offset-rebasing arithmetic end to end.
"""

import struct

from retroport.formats import M2File, parse_skel
from retroport.formats import structs as S
from retroport.convert.skel_reinliner import reinline


# --- fixture builders -------------------------------------------------------

def build_m2(extra_chunks):
    """Minimal MD21 M2 with empty header arrays + the given extra chunks."""
    md20 = bytearray(0x138)            # room through particle_emitters
    md20[0:4] = b"MD20"
    struct.pack_into("<I", md20, S.MD20_VERSION, 274)
    out = b"MD21" + struct.pack("<I", len(md20)) + bytes(md20)
    for magic, content in extra_chunks:
        out += magic.encode("ascii") + struct.pack("<I", len(content)) + content
    return out


def build_skel_one_bone():
    """A .skel with SKB1: one bone, translation track with 2 inline keyframes."""
    c = bytearray(156)
    off_bone, off_kbl = 16, 104
    off_ts_outer, off_val_outer = 108, 116
    off_ts_leaf, off_val_leaf = 124, 132

    # SKB1 header
    struct.pack_into("<II", c, S.SKB1_BONES, 1, off_bone)
    struct.pack_into("<II", c, S.SKB1_KEY_BONE_LOOKUP, 1, off_kbl)

    # bone (88 bytes)
    struct.pack_into("<i", c, off_bone + 0x00, -1)      # key_bone_id
    struct.pack_into("<I", c, off_bone + 0x04, 0)       # flags
    struct.pack_into("<h", c, off_bone + 0x08, -1)      # parent_bone
    struct.pack_into("<H", c, off_bone + 0x0A, 0)       # submesh_id
    struct.pack_into("<I", c, off_bone + 0x0C, 0)       # boneNameCRC
    # translation track @ +0x10
    t = off_bone + S.BONE_TRANSLATION
    struct.pack_into("<HH", c, t + 0x00, 1, 0xFFFF)     # interp, global_seq
    struct.pack_into("<II", c, t + S.TRACK_TIMESTAMPS, 1, off_ts_outer)
    struct.pack_into("<II", c, t + S.TRACK_VALUES, 1, off_val_outer)
    # rotation @ +0x24 and scale @ +0x38 left zeroed (count 0)
    # pivot @ +0x4C zeroed

    # key bone lookup (1x uint16)
    struct.pack_into("<H", c, off_kbl, 0)

    # outer arrays: each one inner M2Array
    struct.pack_into("<II", c, off_ts_outer, 2, off_ts_leaf)
    struct.pack_into("<II", c, off_val_outer, 2, off_val_leaf)
    # leaves
    struct.pack_into("<II", c, off_ts_leaf, 100, 200)                    # 2 timestamps
    struct.pack_into("<6f", c, off_val_leaf, 1, 2, 3, 4, 5, 6)           # 2 C3Vectors

    return b"SKB1" + struct.pack("<I", len(c)) + bytes(c)


# --- tests ------------------------------------------------------------------

def test_structural_reinline_rebases_nested_track_exactly():
    skid = struct.pack("<I", 12345)
    m2 = M2File(build_m2([("SKID", skid)]))
    skel = parse_skel(build_skel_one_bone())

    assert skel.bones.count == 1
    res = reinline(m2, skel, basename="testmodel")

    assert res.success is True
    assert res.bones == 1
    assert res.external_tracks == 0
    assert res.fully_inline is True
    assert m2.has_chunk("SKID") is False           # skeleton link dropped

    # Re-parse the emitted file and walk to the keyframe leaves.
    out = M2File(m2.to_bytes())
    md = out.md20
    bcnt, boff = S.read_m2array(md, S.HDR_BONES)
    assert bcnt == 1
    track = boff + S.BONE_TRANSLATION

    tcnt, touter = S.read_m2array(md, track + S.TRACK_TIMESTAMPS)
    assert tcnt == 1
    icnt, ileaf = S.read_m2array(md, touter)               # inner timestamp array
    assert icnt == 2
    assert struct.unpack_from("<II", md, ileaf) == (100, 200)

    vcnt, vouter = S.read_m2array(md, track + S.TRACK_VALUES)
    jcnt, vleaf = S.read_m2array(md, vouter)
    assert jcnt == 2
    assert struct.unpack_from("<6f", md, vleaf) == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_header_bone_array_count_and_offset_set():
    m2 = M2File(build_m2([("SKID", struct.pack("<I", 1))]))
    skel = parse_skel(build_skel_one_bone())
    reinline(m2, skel, basename="m")
    cnt, off = S.read_m2array(m2.md20, S.HDR_BONES)
    assert cnt == 1
    assert off == 0x138 + 16              # base of appended SKB1 + bones.offset


def test_external_anim_detected_via_afid():
    afid = struct.pack("<HHI", 0, 0, 999)            # one anim file id entry
    m2 = M2File(build_m2([("SKID", struct.pack("<I", 1)), ("AFID", afid)]))
    skel = parse_skel(build_skel_one_bone())
    res = reinline(m2, skel, basename="m")
    # structural merge still succeeds, but flagged for the animation pass
    assert res.success is True
    assert res.fully_inline is False
    assert any(f.check == "needs_anim_pass" for f in res.findings)


def test_rejects_md20_input():
    import pytest
    from retroport.formats import M2Error
    with pytest.raises(M2Error):
        M2File(b"MD20" + struct.pack("<I", 264) + bytes(0x130))


def test_no_skid_means_nothing_to_do():
    m2 = M2File(build_m2([]))            # no SKID chunk
    assert m2.skel_file_id() is None
