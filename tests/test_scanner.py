"""Scanner tests using synthetic headers - no game files required.

Each helper builds the minimal byte layout the scanner keys on, so these prove
the chunk-walk logic (M2 non-reversed vs RIFF reversed, .skel detection, FDID
detection, BLP metadata) in isolation.
"""

import io
import struct

from retroport.models import AssetType
from retroport.scan.scanner import classify_stream
from retroport.validate.rules import validate


def _chunk(magic: bytes, data: bytes, reverse: bool = False) -> bytes:
    m = magic[::-1] if reverse else magic
    return m + struct.pack("<I", len(data)) + data


def make_md20(version: int = 264) -> bytes:
    return b"MD20" + struct.pack("<I", version) + b"\x00" * 64


def make_md21(version: int = 274, extra=(b"SFID", b"TXID")) -> bytes:
    md20 = b"MD20" + struct.pack("<I", version) + b"\x00" * 64
    out = _chunk(b"MD21", md20)
    for c in extra:
        out += _chunk(c, b"\x00" * 8)
    return out


def make_blp2(width=256, height=256, encoding=2, alpha_enc=7, mips=1) -> bytes:
    h = b"BLP2"
    h += struct.pack("<I", 1)                      # type
    h += bytes([encoding, 8, alpha_enc, mips])     # encoding, alphaDepth, alphaEnc, hasMips
    h += struct.pack("<II", width, height)
    h += b"\x00" * (148 - len(h))                  # pad mip tables
    return h


def make_wmo_root() -> bytes:
    out = _chunk(b"MVER", struct.pack("<I", 17), reverse=True)
    out += _chunk(b"MOHD", b"\x00" * 64, reverse=True)
    return out


def make_adt() -> bytes:
    out = _chunk(b"MVER", struct.pack("<I", 18), reverse=True)
    out += _chunk(b"MHDR", b"\x00" * 64, reverse=True)
    return out


def make_adt_fdid() -> bytes:
    out = _chunk(b"MVER", struct.pack("<I", 18), reverse=True)
    out += _chunk(b"MDID", b"\x00" * 16, reverse=True)
    return out


def _classify(data: bytes, hint: str = ""):
    return classify_stream(io.BytesIO(data), path_hint=hint)


def test_md20_is_wotlk_ready():
    info = _classify(make_md20(264), "Creature/foo/foo.m2")
    assert info.type is AssetType.M2
    assert info.chunked is False
    assert info.version == 264
    assert info.expansion == "WotLK"
    assert info.wotlk_ready is True


def test_md21_detects_chunked_and_fdid():
    info = _classify(make_md21(274, extra=(b"SFID", b"TXID")), "x.m2")
    assert info.type is AssetType.M2
    assert info.chunked is True
    assert info.version == 274
    assert info.uses_fdid is True
    assert info.uses_skel is False
    assert info.wotlk_ready is False


def test_md21_detects_skel():
    info = _classify(make_md21(274, extra=(b"SKID", b"SFID")), "char.m2")
    assert info.uses_skel is True
    rep = validate(info)
    assert any(f.check == "m2_skel" for f in rep.findings)


def test_md21_first_chunk_not_md21():
    # PFID before MD21 - scanner must still walk and find the version.
    md20 = b"MD20" + struct.pack("<I", 272) + b"\x00" * 32
    data = _chunk(b"PFID", b"\x00" * 4) + _chunk(b"MD21", md20) + _chunk(b"SKID", b"\x00" * 4)
    info = _classify(data, "y.m2")
    assert info.type is AssetType.M2
    assert info.version == 272
    assert info.uses_skel is True


def test_blp2_dxt5():
    info = _classify(make_blp2(256, 256, encoding=2, alpha_enc=7, mips=1), "t.blp")
    assert info.type is AssetType.BLP
    assert info.compression == "DXT5"
    assert info.width == 256 and info.height == 256
    assert info.has_mipmaps is True
    assert validate(info).ok is True


def test_blp2_non_pow2_fails():
    info = _classify(make_blp2(300, 256), "bad.blp")
    assert info.wotlk_ready is False
    rep = validate(info)
    assert rep.ok is False
    assert any(f.check == "blp_pow2" for f in rep.findings)


def test_wmo_root():
    info = _classify(make_wmo_root(), "building.wmo")
    assert info.type is AssetType.WMO


def test_adt_and_fdid():
    assert _classify(make_adt(), "map_30_30.adt").type is AssetType.ADT
    fd = _classify(make_adt_fdid(), "map_30_30_tex0.adt")
    assert fd.type is AssetType.ADT
    assert fd.uses_fdid is True


def test_extension_fallback_for_skin():
    info = _classify(b"\x00\x00\x00\x00garbage", "model00.skin")
    assert info.type is AssetType.SKIN


def test_unknown():
    assert _classify(b"not a wow file at all", "readme.txt").type is AssetType.UNKNOWN
