"""Texture-composition report tests."""

import json
import struct

from retroport.texreport import build_texture_report
from retroport.formats import structs as S


def _m2_with_textures():
    """MD20 with 3 textures: type 0 (path), type 1 (body/CharSections), type 6 (hair)."""
    n = 3
    tex_off = 0x140
    name = b"Creature\\Foo\\Foo.blp\x00"
    name_off = tex_off + n * S.M2TEXTURE
    total = name_off + len(name)
    b = bytearray(total)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    struct.pack_into("<II", b, S.HDR_TEXTURES, n, tex_off)
    b[name_off:name_off + len(name)] = name          # write the type-0 texture path bytes
    # tex 0: type 0, filename -> name
    struct.pack_into("<I", b, tex_off + 0 * S.M2TEXTURE + S.TEXTURE_TYPE, 0)
    struct.pack_into("<II", b, tex_off + 0 * S.M2TEXTURE + S.TEXTURE_FILENAME, len(name), name_off)
    # tex 1: type 1 (body skin), empty filename
    struct.pack_into("<I", b, tex_off + 1 * S.M2TEXTURE + S.TEXTURE_TYPE, 1)
    # tex 2: type 6 (hair), empty filename
    struct.pack_into("<I", b, tex_off + 2 * S.M2TEXTURE + S.TEXTURE_TYPE, 6)
    return bytes(b)


def test_texreport_categorizes_and_flags_charsections(tmp_path):
    m2 = tmp_path / "kultiranmale.m2"
    m2.write_bytes(_m2_with_textures())

    report = build_texture_report(str(m2))

    assert "kultiranmale.m2" in report
    assert "IN-MODEL path" in report                 # type 0 with a path
    assert "CharSections.dbc" in report              # type 1/6 flagged as runtime CharSections
    assert "body/skin" in report and "hair" in report
    assert "overwriting a race" in report            # the key porting caveat
    # summary counts: 1 in-model, 2 CharSections
    assert "1 in-model" in report and "2 CharSections" in report


def test_texreport_pairs_manifest_textures(tmp_path):
    m2 = tmp_path / "kultiranmale.m2"
    m2.write_bytes(_m2_with_textures())
    manifest = tmp_path / "kultiranmale.manifest.json"
    manifest.write_text(json.dumps({
        "textures": [
            {"fileDataID": 111, "file": "creature/foo/foo.blp"},
            {"fileDataID": 222, "file": "character/kultiran/male/kultiranmaleskin.blp"},
        ]
    }))

    report = build_texture_report(str(m2), manifest_path=str(manifest))
    assert "fdid 111" in report                       # manifest cross-reference shown
    assert "kultiranmaleskin" in report
