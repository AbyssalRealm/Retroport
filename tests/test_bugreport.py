"""Bug-report generator tests - verifies the #Bug-Report summary flags later-expansion
geosets and embeds the WoWError."""

import struct

from retroport.bugreport import build_report
from retroport.formats import structs as S


def _m2a(buf, off, count, offset):
    struct.pack_into("<II", buf, off, count, offset)


def _md20():
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    _m2a(b, S.HDR_BONES, 42, 0)
    _m2a(b, S.HDR_VERTICES, 100, 0)
    struct.pack_into("<I", b, S.HDR_NUM_SKIN_PROFILES, 1)
    _m2a(b, S.HDR_TEXTURES, 0, len(b))
    return bytes(b)


def _skin_with_sl_geoset():
    sub_off = 0x30
    n_sub, n_bat = 2, 1
    bat_off = sub_off + n_sub * S.M2SKINSECTION
    total = bat_off + n_bat * S.M2BATCH
    s = bytearray(total)
    s[0:4] = b"SKIN"
    _m2a(s, S.SKIN_SUBMESHES, n_sub, sub_off)
    _m2a(s, S.SKIN_BATCHES, n_bat, bat_off)
    struct.pack_into("<H", s, sub_off + 0 * S.M2SKINSECTION + S.SKINSECTION_ID, 0)      # body (group 0)
    struct.pack_into("<H", s, sub_off + 1 * S.M2SKINSECTION + S.SKINSECTION_ID, 2801)   # group 28 (SL+)
    return bytes(s)


def test_bugreport_flags_later_expansion_geoset_and_embeds_error(tmp_path):
    m2 = tmp_path / "orcmale.m2"
    m2.write_bytes(_md20())
    (tmp_path / "orcmale00.skin").write_bytes(_skin_with_sl_geoset())

    report = build_report(str(m2), error_text="ERROR #132 (0x85100084) Fatal Exception")

    assert "orcmale.m2" in report                      # model name
    assert "28" in report                              # the later-expansion group
    assert "not in 3.3.5" in report                    # flagged as post-WotLK
    assert "#132" in report                            # WoWError embedded
    assert "**Bones:** 42" in report                   # check data surfaced


def test_bugreport_without_error_has_no_error_block(tmp_path):
    m2 = tmp_path / "orcmale.m2"
    m2.write_bytes(_md20())
    (tmp_path / "orcmale00.skin").write_bytes(_skin_with_sl_geoset())

    report = build_report(str(m2))
    assert "WoWError" not in report
