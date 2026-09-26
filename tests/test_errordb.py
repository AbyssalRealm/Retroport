"""Crash-address decoder tests."""

import struct

from retroport.errordb import decode_error
from retroport.bugreport import build_report
from retroport.formats import structs as S


def test_decode_error_matches_known_skin_bone_address():
    err = ("ERROR #132 (0x85100084) Fatal Exception\n"
           "The instruction at \"0x00684527\" referenced memory at \"0x00000000\".")
    hits = decode_error(err)
    assert len(hits) == 1
    addr, cause = hits[0]
    assert addr == "0x00684527"
    assert "bone" in cause.lower()


def test_decode_error_handles_section_colon_form_and_ignores_data_addr():
    # 'section:address' form, plus a data address that must NOT match
    err = "Exception (ACCESS_VIOLATION) at 0023:00836D11 ... referenced memory at 0x5C6711D0"
    hits = dict(decode_error(err))
    assert "0x00836d11" in hits and "shader" in hits["0x00836d11"].lower()
    assert "0x5c6711d0" not in hits           # data address isn't a known crash site


def test_decode_error_none_for_unknown():
    assert decode_error("ERROR #132 at 0x00401000") == []
    assert decode_error("") == []


def _md20():
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    struct.pack_into("<II", b, S.HDR_BONES, 42, 0)
    struct.pack_into("<I", b, S.HDR_NUM_SKIN_PROFILES, 1)
    struct.pack_into("<II", b, S.HDR_TEXTURES, 0, len(b))
    return bytes(b)


def test_bugreport_includes_decoded_cause(tmp_path):
    m2 = tmp_path / "foo.m2"
    m2.write_bytes(_md20())
    report = build_report(str(m2), error_text="Exception at 0x00833BDE referenced memory at 0x1")
    assert "Likely cause" in report
    assert "EMITTER_TYPE" in report            # particle type decode surfaced
