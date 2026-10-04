"""BLP2 reader/writer for the 3.3.5a client.

Layout, confirmed against Blizzard / HD-patch 3.3.5 files:

    "BLP2" | u32 type=1 | u8 encoding | u8 alphaDepth | u8 alphaEncoding | u8 hasMips
    | u32 width | u32 height | u32 mipOffsets[16] | u32 mipSizes[16] | u32 palette[256]

148-byte header + 1024-byte palette, so mip 0 always starts at 1172.

* palettized (encoding 1) - what Blizzard and the HD patch use for everything the
  client composites onto a character (body skins, armor component textures):
  256-color BGRA palette (4th byte 0), then per mip w*h index bytes followed by
  w*h alpha bytes when alphaDepth is 8. alphaEncoding is 8 in those files.
* DXT (encoding 2) - object textures (weapons, helms): DXT1 (alphaDepth 0,
  alphaEncoding 0) or DXT5 (alphaDepth 8, alphaEncoding 7). Blocks come from
  Pillow's DDS encoder.

Mip chains halve until the short side reaches 1 (a 512x256 Blizzard texture ends
at 2x1; a square one at 1x1).
"""

from __future__ import annotations

import io
import struct

from PIL import Image

MAGIC = b"BLP2"
HEADER = 148
DATA_START = HEADER + 1024


class BLPError(ValueError):
    pass


def decode(path: str) -> Image.Image:
    """Any BLP the client uses (BLP1/2, palettized/DXT/BGRA) as an RGBA image.

    Palettized and raw-BGRA BLP2, and all BLP1, are decoded here: Pillow takes
    palettized alpha from the palette's 4th byte, but Blizzard stores it as a
    separate plane after the indices (palette byte 0), so Pillow reads every real
    alpha texture as fully transparent (and it can't read BGRA at all). DXT goes
    through Pillow."""
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        if data[:4] == MAGIC and len(data) >= DATA_START and data[8] in (1, 3):
            return _decode_uncompressed(data)
        if data[:4] == b"BLP1" and len(data) >= BLP1_HEADER:
            return _decode_blp1(data)
    except (struct.error, ValueError, OSError) as exc:
        raise BLPError(f"{path}: cannot decode BLP ({exc})") from exc
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:                      # Pillow raises several types here
        raise BLPError(f"{path}: cannot decode BLP ({exc})") from exc
    return img.convert("RGBA")


BLP1_HEADER = 156     # "BLP1" + compression, alphaBits, w, h, pictureType, pictureSubType + 2x16 mip u32s


def _decode_blp1(data: bytes) -> Image.Image:
    """BLP1 (Warcraft III-era). Pillow gets both variants' alpha wrong: palettized
    alpha is a separate plane (like BLP2), and JPEG files hold four channels that
    read back as 'CMYK' holding inverted B, G, R, A (Pillow keeps only the colour).
    Checked on real files: rounded icon corners come out transparent."""
    comp, alpha_bits, w, h = struct.unpack_from("<4I", data, 4)
    off, size = struct.unpack_from("<I", data, 28)[0], struct.unpack_from("<I", data, 92)[0]
    if comp == 1:
        pal = data[BLP1_HEADER:BLP1_HEADER + 1024]
        fake = _pack(1, alpha_bits if alpha_bits in (1, 4, 8) else 0, 8, (w, h),
                     [data[off:off + size]], pal)
        return _decode_uncompressed(fake)
    if comp != 0:
        raise ValueError(f"unknown BLP1 compression {comp}")
    hsize = struct.unpack_from("<I", data, BLP1_HEADER)[0]
    jpeg = data[BLP1_HEADER + 4:BLP1_HEADER + 4 + hsize] + data[off:off + size]
    img = Image.open(io.BytesIO(jpeg))
    img.load()
    if img.mode != "CMYK":
        return img.convert("RGBA")
    c, m, y, k = (ch.point(lambda v: 255 - v) for ch in img.split())   # inverted B, G, R, A
    if not alpha_bits:
        k = Image.new("L", img.size, 255)
    return Image.merge("RGBA", (y, m, c, k))


def _decode_uncompressed(data: bytes) -> Image.Image:
    h = header(data)
    w, ht = h["width"], h["height"]
    if not h["mips"]:
        raise ValueError("no mip data")
    off, size = h["mips"][0]
    n = w * ht
    if h["encoding"] == 3:                        # raw BGRA
        bgra = data[off:off + n * 4]
        if len(bgra) < n * 4:
            raise ValueError("pixel data truncated")
        b, g, r, a = Image.frombytes("RGBA", (w, ht), bgra).split()
        return Image.merge("RGBA", (r, g, b, a))
    idx = data[off:off + n]
    if len(idx) < n:
        raise ValueError("index data truncated")
    pal = data[HEADER:DATA_START]
    img = Image.frombytes("P", (w, ht), bytes(idx))
    img.putpalette([pal[i + c] for i in range(0, 1024, 4) for c in (2, 1, 0)])   # BGRA -> RGB
    img = img.convert("RGBA")
    depth = h["alpha_depth"]
    if depth:
        raw = data[off + n:off + size]
        if depth == 8:
            alpha = raw[:n]
        elif depth == 4:                          # two pixels per byte, low nibble first
            alpha = bytes((raw[i // 2] >> (4 * (i % 2)) & 0xF) * 17 for i in range(n))
        elif depth == 1:                          # eight pixels per byte, LSB first
            alpha = bytes(255 if raw[i // 8] >> (i % 8) & 1 else 0 for i in range(n))
        else:
            raise ValueError(f"unsupported alpha depth {depth}")
        if len(alpha) < n:
            raise ValueError("alpha data truncated")
        img.putalpha(Image.frombytes("L", (w, ht), bytes(alpha)))
    return img


def has_alpha(img: Image.Image) -> bool:
    return img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 255


def mip_chain(img: Image.Image) -> list[Image.Image]:
    """Every level down to 1x1, as Blizzard writes them (a 128x64 texture has 8). A non-square
    texture cut short at the shorter side (1024x512 ending at 2x1) is rejected when the client
    puts it on a model directly: Kul Tiran female hair (type 6) showed the green fallback."""
    mips = [img]
    while max(mips[-1].size) > 1 and len(mips) < 16:
        w, h = mips[-1].size
        mips.append(img.resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.LANCZOS))
    return mips


def _pack(encoding: int, alpha_depth: int, alpha_encoding: int, size: tuple[int, int],
          mips: list[bytes], palette: bytes = b"\x00" * 1024) -> bytes:
    offsets, sizes, pos = [], [], DATA_START
    for m in mips:
        offsets.append(pos)
        sizes.append(len(m))
        pos += len(m)
    offsets += [0] * (16 - len(offsets))
    sizes += [0] * (16 - len(sizes))
    head = struct.pack("<4sIBBBBII16I16I", MAGIC, 1, encoding, alpha_depth, alpha_encoding, 1,
                       size[0], size[1], *offsets, *sizes)
    return head + palette + b"".join(mips)


def encode_palettized(img: Image.Image, alpha: bool | None = None) -> bytes:
    """Blizzard's character-texture format: 256 colors, 8-bit alpha if needed, full mips."""
    img = img.convert("RGBA")
    alpha = has_alpha(img) if alpha is None else alpha
    chain = mip_chain(img)
    pal_img = img.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT)
    rgb = (pal_img.getpalette() or [])[:768]
    rgb += [0] * (768 - len(rgb))
    palette = b"".join(struct.pack("<BBBB", rgb[i + 2], rgb[i + 1], rgb[i], 0) for i in range(0, 768, 3))
    data = []
    for m in chain:
        idx = m.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG).tobytes()
        data.append(idx + (m.getchannel("A").tobytes() if alpha else b""))
    return _pack(1, 8 if alpha else 0, 8, img.size, data, palette)


def encode_dxt(img: Image.Image, alpha: bool | None = None) -> bytes:
    """DXT5 if the image has alpha, DXT1 otherwise, full mips."""
    img = img.convert("RGBA")
    alpha = has_alpha(img) if alpha is None else alpha
    fmt = "DXT5" if alpha else "DXT1"
    data = []
    for m in mip_chain(img):
        buf = io.BytesIO()
        m.save(buf, "DDS", pixel_format=fmt)
        raw = buf.getvalue()
        if raw[84:88] != fmt.encode():
            raise BLPError(f"Pillow's DDS encoder did not produce {fmt}")
        data.append(raw[128:])
    return _pack(2, 8 if alpha else 0, 7 if alpha else 0, img.size, data)


def header(data: bytes) -> dict:
    if len(data) < DATA_START or data[:4] != MAGIC:
        raise BLPError("not a BLP2 file")
    _m, typ, enc, adepth, aenc, mips, w, h = struct.unpack_from("<4sIBBBBII", data, 0)
    offs = struct.unpack_from("<16I", data, 20)
    sizes = struct.unpack_from("<16I", data, 84)
    levels = [(o, s) for o, s in zip(offs, sizes) if o and s]
    return {"type": typ, "encoding": enc, "alpha_depth": adepth, "alpha_encoding": aenc,
            "has_mips": mips, "width": w, "height": h, "mips": levels}


def verify_blp(path: str, size: tuple[int, int] | None = None, *, full_chain: bool = True) -> None:
    """Header sane, every mip inside the file, the mip chain down to 1x1 (``full_chain``; the
    client rejects a model texture cut short), and it decodes at the expected size."""
    with open(path, "rb") as fh:
        data = fh.read()
    h = header(data)
    if h["type"] != 1 or h["encoding"] not in (1, 2, 3) or not h["mips"]:
        raise BLPError(f"{path}: bad BLP2 header {h}")
    for o, s in h["mips"]:
        if o + s > len(data):
            raise BLPError(f"{path}: mip data runs past end of file")
    want = max(h["width"], h["height"]).bit_length()
    if full_chain and h["has_mips"] and len(h["mips"]) < want:
        raise BLPError(f"{path}: {len(h['mips'])} mip levels, a {h['width']}x{h['height']} texture needs {want}")
    img = decode(path)
    if size and img.size != tuple(size):
        raise BLPError(f"{path}: decodes as {img.size}, expected {tuple(size)}")
