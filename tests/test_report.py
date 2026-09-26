"""Combined report writer tests."""

import os
import struct

from retroport.report import write_reports
from retroport.formats import structs as S


def _m2():
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    struct.pack_into("<II", b, S.HDR_BONES, 42, 0)
    struct.pack_into("<I", b, S.HDR_NUM_SKIN_PROFILES, 1)
    struct.pack_into("<II", b, S.HDR_TEXTURES, 0, len(b))
    return bytes(b)


def test_write_reports_creates_all_files(tmp_path):
    m2 = tmp_path / "humanmale.m2"
    m2.write_bytes(_m2())
    out = tmp_path / "reports"

    written = write_reports(str(m2), str(out), error_text="Exception at 0x00836D11")

    # per-model subfolder with combined + 3 individual reports
    dest = out / "humanmale"
    assert (dest / "humanmale_report.md").exists()
    assert (dest / "check.md").exists()
    assert (dest / "textures.md").exists()
    assert (dest / "bugreport.md").exists()
    assert written[0].endswith("humanmale_report.md")

    combined = (dest / "humanmale_report.md").read_text()
    assert "## Check" in combined and "## Texture composition" in combined and "## Bug report" in combined
    assert "0x00836d11" in combined            # decoded crash flows through to the combined file
    # all ASCII (survives a cp1252 console)
    combined.encode("cp1252")
