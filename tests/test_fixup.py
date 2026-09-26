"""Fixup tests - submesh clamp, stale anim-flag clear, nViews set."""

import struct

from retroport.formats import structs as S
from retroport.convert import fixup


def _m2a(buf, off, count, offset):
    struct.pack_into("<II", buf, off, count, offset)


def _md20(flags=0, nviews=1):
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    struct.pack_into("<I", b, S.HDR_GLOBAL_FLAGS, flags)
    struct.pack_into("<I", b, S.HDR_NUM_SKIN_PROFILES, nviews)
    return bytes(b)


def _skin(n_sub, batch_section_indices):
    sub_off = 0x30
    bat_off = sub_off + n_sub * S.M2SKINSECTION
    total = bat_off + len(batch_section_indices) * S.M2BATCH
    s = bytearray(total)
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_SUBMESHES, n_sub, sub_off)
    _m2a(s, S.SKIN_BATCHES, len(batch_section_indices), bat_off)
    for i in range(n_sub):
        struct.pack_into("<H", s, sub_off + i * S.M2SKINSECTION + S.SKINSECTION_ID, i)
    for i, si in enumerate(batch_section_indices):
        struct.pack_into("<H", s, bat_off + i * S.M2BATCH + 0x04, si)  # skinSectionIndex
    return bytes(s)


def test_clamp_drops_submeshes_and_batches(tmp_path):
    p = tmp_path / "m00.skin"
    # 60 submeshes, batches pointing at sections 0, 58, 59 (last should be dropped)
    p.write_bytes(_skin(60, [0, 58, 59]))
    new_n = fixup.clamp_skin_submeshes(str(p), max_submeshes=59)
    assert new_n == 59

    data = p.read_bytes()
    sub_cnt, _ = S.read_m2array(data, S.SKIN_SUBMESHES)
    bat_cnt, bat_off = S.read_m2array(data, S.SKIN_BATCHES)
    assert sub_cnt == 59
    assert bat_cnt == 2                       # the section-59 batch was filtered out
    kept = [struct.unpack_from("<H", data, bat_off + i * S.M2BATCH + 0x04)[0] for i in range(bat_cnt)]
    assert kept == [0, 58]


def test_clamp_noop_when_under_limit(tmp_path):
    p = tmp_path / "m00.skin"
    p.write_bytes(_skin(10, [0, 1]))
    assert fixup.clamp_skin_submeshes(str(p), 59) == 10


def test_clear_stale_anim_flags(tmp_path):
    p = tmp_path / "m.m2"
    p.write_bytes(_md20(flags=S.FLAG_CHUNKED_ANIM_0x2000 | S.FLAG_UPGRADED_ANIM_0x200000 | 0x1))
    assert fixup.clear_stale_anim_flags(str(p)) is True
    flags = struct.unpack_from("<I", p.read_bytes(), S.HDR_GLOBAL_FLAGS)[0]
    assert flags == 0x1                        # stale bits cleared, others kept
    assert fixup.clear_stale_anim_flags(str(p)) is False   # idempotent


def test_set_num_skin_profiles(tmp_path):
    p = tmp_path / "m.m2"
    p.write_bytes(_md20(nviews=1))
    fixup.set_num_skin_profiles(str(p), 4)
    assert struct.unpack_from("<I", p.read_bytes(), S.HDR_NUM_SKIN_PROFILES)[0] == 4


def _skin_with_shaders(shaders):
    n_bat = len(shaders)
    bat_off = 0x30
    total = bat_off + n_bat * S.M2BATCH
    s = bytearray(total)
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_BATCHES, n_bat, bat_off)
    for i, sh in enumerate(shaders):
        struct.pack_into("<H", s, bat_off + i * S.M2BATCH + S.BATCH_SHADER, sh)
    return bytes(s)


def test_clear_combiner_combos(tmp_path):
    m2 = tmp_path / "m.m2"
    m2.write_bytes(_md20(flags=S.FLAG_USE_COMBINER_COMBOS | 0x1))       # 0x08 set + another bit
    skin = tmp_path / "m00.skin"
    skin.write_bytes(_skin_with_shaders([0, 16401, 0x8000]))            # default, combo-index, no-render

    cleared, changed = fixup.clear_combiner_combos(str(m2), [str(skin)])
    assert cleared is True
    assert changed == 1                                                 # only 16401 was reset

    flags = struct.unpack_from("<I", m2.read_bytes(), S.HDR_GLOBAL_FLAGS)[0]
    assert flags == 0x1                                                 # combiner bit cleared, other kept

    data = skin.read_bytes()
    _, b_off = S.read_m2array(data, S.SKIN_BATCHES)
    got = [struct.unpack_from("<H", data, b_off + i * S.M2BATCH + S.BATCH_SHADER)[0] for i in range(3)]
    assert got == [0, 0, 0x8000]                                       # index->0, no-render preserved


def test_clear_combiner_noop_when_flag_absent(tmp_path):
    m2 = tmp_path / "m.m2"
    m2.write_bytes(_md20(flags=0))
    skin = tmp_path / "m00.skin"
    skin.write_bytes(_skin_with_shaders([0, 0x8000]))
    cleared, changed = fixup.clear_combiner_combos(str(m2), [str(skin)])
    assert cleared is False and changed == 0
