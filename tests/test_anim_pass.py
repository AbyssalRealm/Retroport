"""Milestone B tests - the animation pass inlines external .anim AFSB/AFSA data.

Builds a model with two sequences: sequence 0's bone track data is inline (in
SKB1), sequence 1's is external (in an AFSB chunk of a .anim file, registered via
AFID). After re-inlining with an anim_loader, both must resolve *inside* the M2,
and sequence 1's keyframes must equal the bytes from the .anim file. This also
proves AFID membership overrides the range heuristic: sequence 1's external
offsets are small (would look "inline" by range) but are correctly treated as
external because the sequence has an AFID entry.
"""

import struct

from retroport.formats import M2File, parse_skel
from retroport.formats import structs as S
from retroport.convert.skel_reinliner import reinline


def build_m2(extra_chunks):
    md20 = bytearray(0x138)
    md20[0:4] = b"MD20"
    struct.pack_into("<I", md20, S.MD20_VERSION, 274)
    out = b"MD21" + struct.pack("<I", len(md20)) + bytes(md20)
    for magic, content in extra_chunks:
        out += magic.encode("ascii") + struct.pack("<I", len(content)) + content
    return out


def build_skel_two_seq():
    """SKS1 with 2 sequences + SKB1 with one bone whose translation track has an
    inline array for seq0 and an external (AFSB) array for seq1."""
    # --- SKS1: global_loops{0}, sequences{2}, sequence_lookups{0} ---
    sks = bytearray(160)
    off_seq = 32
    struct.pack_into("<II", sks, S.SKS1_GLOBAL_LOOPS, 0, 0)
    struct.pack_into("<II", sks, S.SKS1_SEQUENCES, 2, off_seq)
    struct.pack_into("<II", sks, S.SKS1_SEQUENCE_LOOKUPS, 0, 0)
    struct.pack_into("<HH", sks, off_seq + 0 * S.M2SEQUENCE, 0, 0)   # seq0 id=0 var=0 (inline)
    struct.pack_into("<HH", sks, off_seq + 1 * S.M2SEQUENCE, 5, 0)   # seq1 id=5 var=0 (external)
    # seq0 is inline -> already flagged internal in a real .skel; seq1 external -> not.
    struct.pack_into("<I", sks, off_seq + 0 * S.M2SEQUENCE + S.M2SEQUENCE_FLAGS, 0x20)
    sks1 = b"SKS1" + struct.pack("<I", len(sks)) + bytes(sks)

    # --- SKB1: one bone, translation track outer count = 2 ---
    c = bytearray(172)
    off_bone, off_kbl = 16, 104
    off_ts_outer, off_val_outer = 108, 124
    off_ts0, off_val0 = 140, 148          # inline leaves (seq0)
    ot1_ext, ov1_ext = 0, 8               # external offsets into AFSB (seq1)

    struct.pack_into("<II", c, S.SKB1_BONES, 1, off_bone)
    struct.pack_into("<II", c, S.SKB1_KEY_BONE_LOOKUP, 1, off_kbl)
    struct.pack_into("<i", c, off_bone + 0x00, -1)
    t = off_bone + S.BONE_TRANSLATION
    struct.pack_into("<HH", c, t + 0x00, 1, 0xFFFF)
    struct.pack_into("<II", c, t + S.TRACK_TIMESTAMPS, 2, off_ts_outer)
    struct.pack_into("<II", c, t + S.TRACK_VALUES, 2, off_val_outer)
    struct.pack_into("<H", c, off_kbl, 0)
    # outer arrays: [inline seq0, external seq1]
    struct.pack_into("<II", c, off_ts_outer + 0, 2, off_ts0)
    struct.pack_into("<II", c, off_ts_outer + 8, 2, ot1_ext)
    struct.pack_into("<II", c, off_val_outer + 0, 2, off_val0)
    struct.pack_into("<II", c, off_val_outer + 8, 2, ov1_ext)
    # inline leaves for seq0
    struct.pack_into("<II", c, off_ts0, 100, 200)
    struct.pack_into("<6f", c, off_val0, 1, 2, 3, 4, 5, 6)
    skb1 = b"SKB1" + struct.pack("<I", len(c)) + bytes(c)

    return sks1 + skb1


def build_anim_afsb():
    """A chunked .anim whose AFSB holds seq1's keyframes at the offsets above."""
    afsb = bytearray(32)
    struct.pack_into("<II", afsb, 0, 300, 400)                      # timestamps at ot1_ext=0
    struct.pack_into("<6f", afsb, 8, 7, 8, 9, 10, 11, 12)           # values at ov1_ext=8
    return b"AFSB" + struct.pack("<I", len(afsb)) + bytes(afsb)


def test_animation_pass_inlines_external_afsb():
    ANIM_FID = 777
    afid = struct.pack("<HHI", 5, 0, ANIM_FID)        # (anim_id 5, sub 0) -> file 777
    m2 = M2File(build_m2([("SKID", struct.pack("<I", 1)), ("AFID", afid)]))
    skel = parse_skel(build_skel_two_seq())

    def loader(fid):
        return build_anim_afsb() if fid == ANIM_FID else None

    res = reinline(m2, skel, basename="dragon", anim_loader=loader,
                   m2_afid=afid)

    assert res.success is True
    assert res.inlined_anims == 1
    assert res.missing_anims == []
    assert res.external_tracks == 0
    assert res.fully_inline is True

    # Re-parse and confirm both sequences resolve inside the M2.
    out = M2File(m2.to_bytes())
    md = out.md20
    _, boff = S.read_m2array(md, S.HDR_BONES)
    track = boff + S.BONE_TRANSLATION

    _, ts_outer = S.read_m2array(md, track + S.TRACK_TIMESTAMPS)
    # seq0 (inline) keyframes preserved
    _, ts0 = S.read_m2array(md, ts_outer + 0 * S.M2ARRAY)
    assert struct.unpack_from("<II", md, ts0) == (100, 200)
    # seq1 (external) keyframes pulled from the .anim AFSB
    _, ts1 = S.read_m2array(md, ts_outer + 1 * S.M2ARRAY)
    assert struct.unpack_from("<II", md, ts1) == (300, 400)

    _, val_outer = S.read_m2array(md, track + S.TRACK_VALUES)
    _, v1 = S.read_m2array(md, val_outer + 1 * S.M2ARRAY)
    assert struct.unpack_from("<6f", md, v1) == (7.0, 8.0, 9.0, 10.0, 11.0, 12.0)

    # The inlined sequence (1) must be flagged internal (0x20) so the client
    # reads inline data and parses its bone blocks; the already-inline seq (0)
    # must remain internal.
    assert res.marked_internal == 1
    _, soff = S.read_m2array(md, S.HDR_SEQUENCES)
    seq0_flags = struct.unpack_from("<I", md, soff + 0 * S.M2SEQUENCE + S.M2SEQUENCE_FLAGS)[0]
    seq1_flags = struct.unpack_from("<I", md, soff + 1 * S.M2SEQUENCE + S.M2SEQUENCE_FLAGS)[0]
    assert seq0_flags & 0x20
    assert seq1_flags & 0x20


def test_missing_anim_is_reported_not_fatal():
    afid = struct.pack("<HHI", 5, 0, 777)
    m2 = M2File(build_m2([("SKID", struct.pack("<I", 1)), ("AFID", afid)]))
    skel = parse_skel(build_skel_two_seq())

    res = reinline(m2, skel, basename="d", anim_loader=lambda fid: None, m2_afid=afid)
    # structural merge still succeeds; the external seq is left + flagged
    assert res.success is True
    assert 777 in res.missing_anims
    assert res.fully_inline is False
    assert res.external_tracks >= 1


def test_no_anim_loader_flags_needs_anim_pass():
    afid = struct.pack("<HHI", 5, 0, 777)
    m2 = M2File(build_m2([("SKID", struct.pack("<I", 1)), ("AFID", afid)]))
    skel = parse_skel(build_skel_two_seq())
    res = reinline(m2, skel, basename="d")          # no anim_loader => Milestone A only
    assert res.success is True
    assert res.fully_inline is False
    assert any(f.check == "needs_anim_pass" for f in res.findings)
