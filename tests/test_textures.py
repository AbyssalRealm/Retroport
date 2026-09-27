"""BLP2 writer (checked with Pillow as an independent decoder) and 3.3.5 texture sizing."""

import struct

import pytest
from PIL import Image

from retroport.formats import blp
from retroport.textures import (
    component_names, convert_component, convert_object, convert_textures, detect_section, region_size,
)


def _save_src(path, size, alpha=True, fmt="dxt"):
    """A source texture written by our own writer (gradient so resizes are visible)."""
    img = Image.new("RGBA", size)
    img.putdata([((x * 7) % 256, (y * 5) % 256, 90, 128 if (alpha and x < size[0] // 2) else 255)
                 for y in range(size[1]) for x in range(size[0])])
    data = blp.encode_dxt(img) if fmt == "dxt" else blp.encode_palettized(img)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return img


def test_palettized_matches_blizzard_layout(tmp_path):
    img = Image.new("RGBA", (128, 64), (40, 80, 120, 200))
    data = blp.encode_palettized(img)
    h = blp.header(data)
    assert (h["type"], h["encoding"], h["alpha_depth"], h["alpha_encoding"], h["has_mips"]) == (1, 1, 8, 8, 1)
    assert h["mips"][0][0] == 1172                                   # header + palette, like Blizzard files
    assert [s for _, s in h["mips"]] == [16384, 4096, 1024, 256, 64, 16, 4]   # 128x64 .. 2x1, idx + alpha
    p = tmp_path / "t.blp"; p.write_bytes(data)
    back = blp.decode(str(p))
    assert back.size == (128, 64) and back.getpixel((5, 5)) == (40, 80, 120, 200)

    opaque = blp.header(blp.encode_palettized(Image.new("RGB", (64, 64), (1, 2, 3))))
    assert opaque["alpha_depth"] == 0 and opaque["mips"][0][1] == 64 * 64      # no alpha bytes


@pytest.mark.parametrize("depth,plane,expect", [
    (1, bytes([0b00000101]), [255, 0, 255, 0, 0, 0, 0, 0]),      # LSB-first bits
    (4, bytes([0x0F, 0x80, 0, 0]), [255, 0, 0, 136, 0, 0, 0, 0]),  # low nibble first, x17
])
def test_decode_packed_palette_alpha(tmp_path, depth, plane, expect):
    """Blizzard-style palettized file: palette 4th byte 0, alpha as a separate plane
    (Pillow ignores that plane; our decoder must not)."""
    w, h = 8, 1
    idx = bytes(w * h)
    data = blp._pack(1, depth, 8, (w, h), [idx + plane], struct.pack("<BBBB", 30, 20, 10, 0) + b"\x00" * 1020)
    p = tmp_path / "a.blp"; p.write_bytes(data)
    img = blp.decode(str(p))
    assert list(img.getchannel("A").tobytes()) == expect
    assert img.getpixel((0, 0))[:3] == (10, 20, 30)                  # BGRA palette -> RGB


def _blp1(comp, alpha_bits, size, mip, extra=b""):
    """BLP1: 'BLP1' + 6 u32 (compression, alphaBits, w, h, pictureType, subType) + 16+16 mip u32s,
    then `extra` (palette, or u32 JPEG header size + header), then mip 0."""
    off = 156 + len(extra)
    head = struct.pack("<4s6I", b"BLP1", comp, alpha_bits, size[0], size[1], 4, 1)
    return head + struct.pack("<16I", off, *[0] * 15) + struct.pack("<16I", len(mip), *[0] * 15) + extra + mip


def test_decode_blp1_palettized_uses_alpha_plane(tmp_path):
    pal = struct.pack("<BBBB", 30, 20, 10, 0) + b"\x00" * 1020            # BGRA, alpha byte unused
    data = _blp1(1, 8, (2, 1), bytes([0, 0]) + bytes([255, 7]), pal)
    p = tmp_path / "p1.blp"; p.write_bytes(data)
    img = blp.decode(str(p))
    assert img.getpixel((0, 0)) == (10, 20, 30, 255) and img.getpixel((1, 0))[3] == 7


def test_decode_blp1_jpeg_inverted_bgra(tmp_path):
    import io
    want = (200, 120, 40, 90)                                          # RGBA
    r, g, b, a = want
    cmyk = Image.new("CMYK", (16, 16), (255 - b, 255 - g, 255 - r, 255 - a))   # stored: inverted B,G,R,A
    buf = io.BytesIO(); cmyk.save(buf, "JPEG", quality=100)
    data = _blp1(0, 8, (16, 16), buf.getvalue(), struct.pack("<I", 0))  # empty shared header
    p = tmp_path / "j1.blp"; p.write_bytes(data)
    got = blp.decode(str(p)).getpixel((8, 8))
    assert all(abs(x - y) <= 3 for x, y in zip(got, want)), got


def test_dxt_picks_format_from_alpha(tmp_path):
    h1 = blp.header(blp.encode_dxt(Image.new("RGBA", (64, 32), (9, 9, 9, 255))))
    h5 = blp.header(blp.encode_dxt(Image.new("RGBA", (64, 32), (9, 9, 9, 100))))
    assert (h1["encoding"], h1["alpha_depth"], h1["alpha_encoding"]) == (2, 0, 0)       # DXT1
    assert (h5["encoding"], h5["alpha_depth"], h5["alpha_encoding"]) == (2, 8, 7)       # DXT5
    p = tmp_path / "d.blp"; p.write_bytes(blp.encode_dxt(Image.new("RGBA", (64, 32), (9, 9, 9, 100))))
    blp.verify_blp(str(p), (64, 32))


def test_section_detection_and_names():
    assert detect_section(r"X\TorsoUpperTexture\anything.blp") == "TorsoUpper"
    assert detect_section(r"X\plate_raid_q_01_chest_tu_m.blp") == "TorsoUpper"
    assert detect_section(r"X\cloth_a_01black_boot_fo.blp") == "Foot"
    assert detect_section(r"X\axe_2h_foo.blp") is None
    assert component_names(r"X\plate_q_01_chest_tu_m.blp") == ("plate_q_01_chest_tu_m", "plate_q_01_chest_tu")
    assert component_names(r"X\plate_q_01_chest_tu.blp") == ("plate_q_01_chest_tu_U", "plate_q_01_chest_tu")
    assert region_size("Hand", "classic") == (128, 32) and region_size("LegUpper", "hd") == (256, 128)
    with pytest.raises(ValueError):
        region_size("Chest")


@pytest.mark.parametrize("target,size", [("classic", (128, 64)), ("hd", (256, 128))])
def test_convert_component_to_region(tmp_path, target, size):
    src = tmp_path / "src" / "plate_q_01_chest_tu_f.blp"
    _save_src(src, (512, 256))
    r = convert_component(str(src), str(tmp_path / "out"), target=target)
    assert r.section == "TorsoUpper" and r.size == size and r.format == "palettized"
    assert r.dbc_name == "plate_q_01_chest_tu" and not r.warnings
    assert r.output.endswith(r"Item\TextureComponents\TorsoUpperTexture\plate_q_01_chest_tu_f.blp")
    h = blp.header(open(r.output, "rb").read())
    assert (h["encoding"], h["alpha_depth"], h["width"], h["height"]) == (1, 8, *size)


def test_component_aspect_mismatch_warns(tmp_path):
    src = tmp_path / "src" / "foo_hand_ha_u.blp"
    _save_src(src, (256, 256))                               # Hand region is 4:1
    r = convert_component(str(src), str(tmp_path / "out"))
    assert r.size == (128, 32) and "aspect" in r.warnings[0]


def test_convert_object_copies_or_shrinks(tmp_path):
    src = tmp_path / "src" / "axe_2h_foo.blp"
    _save_src(src, (256, 256))
    kept = convert_object(str(src), str(tmp_path / "a"))
    assert kept.format == "unchanged" and open(kept.output, "rb").read() == src.read_bytes()
    small = convert_object(str(src), str(tmp_path / "b"), max_object=64)
    assert small.size == (64, 64) and small.format == "DXT5"
    blp.verify_blp(small.output, (64, 64))

    odd = tmp_path / "src" / "odd.blp"
    _save_src(odd, (96, 48), fmt="pal")
    r = convert_object(str(odd), str(tmp_path / "c"))
    assert r.size == (64, 32) and "not power-of-two" in r.warnings[0]


def test_convert_textures_routes_folder(tmp_path):
    src = tmp_path / "src"
    _save_src(src / "armor_pant_lu_m.blp", (256, 128))
    _save_src(src / "sword_1h_foo.blp", (128, 128))
    res = {r.kind: r for r in convert_textures([str(src)], str(tmp_path / "out"), target="hd")}
    assert res["component"].section == "LegUpper" and res["component"].size == (256, 128)
    assert res["object"].format == "unchanged"


def test_undecodable_input_is_a_clear_error(tmp_path):
    bad = tmp_path / "bad_tu.blp"; bad.write_bytes(b"BLP2" + b"\x00" * 50)
    with pytest.raises(blp.BLPError, match="cannot decode"):
        convert_component(str(bad), str(tmp_path / "out"))
