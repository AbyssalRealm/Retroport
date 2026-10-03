"""Add race icons to the character-create icon atlas (UI-CharacterCreate-Races.blp).

The 3.3.5a atlas is 512x256 DXT: 64x64 cells, 8 columns x 4 rows; rows 0/1 hold
Alliance/Horde male icons and rows 2/3 the females, columns 0-4 are taken. A new race
gets the first column whose row-0 and row-2 cells are empty (male, female).

The new icons are encoded as DXT blocks and spliced into every mip level where the
cell is at least one 4x4 block, so the existing icons stay bit-for-bit as they were.
"""

from __future__ import annotations

import io

from PIL import Image

from ..formats import blp

CELL = 64


def _block_format(h: dict) -> tuple[str, int]:
    if h["encoding"] != 2:
        raise blp.BLPError("icon atlas is not DXT-compressed")
    if h["alpha_depth"] == 0 or h["alpha_encoding"] == 0:
        return "DXT1", 8
    return ("DXT3", 16) if h["alpha_encoding"] == 1 else ("DXT5", 16)


def free_column(atlas: Image.Image, used: set[tuple[int, int]]) -> int:
    """First column whose male (row 0) and female (row 2) cells are empty and unused."""
    cols = atlas.width // CELL
    alpha = atlas.getchannel("A")
    for c in range(cols):
        if (c, 0) in used or (c, 2) in used:
            continue
        empty = True
        for row in (0, 2):
            box = (c * CELL + 4, row * CELL + 4, (c + 1) * CELL - 4, (row + 1) * CELL - 4)
            if alpha.crop(box).getextrema()[1] > 16:
                empty = False
        if empty:
            return c
    raise ValueError("no free column left in the race icon atlas")


def coords(col: int, row: int, size: tuple[int, int]) -> tuple[float, float, float, float]:
    w, h = size
    return (col * CELL / w, (col + 1) * CELL / w, row * CELL / h, (row + 1) * CELL / h)


def splice(atlas_blp: bytes, cell: tuple[int, int], icon: Image.Image) -> bytes:
    """Return the atlas with ``icon`` written into the 64x64 cell at (col, row)."""
    h = blp.header(atlas_blp)
    fmt, bsize = _block_format(h)
    out = bytearray(atlas_blp)
    x0, y0 = cell[0] * CELL, cell[1] * CELL
    icon = icon.convert("RGBA")
    for level, (off, size) in enumerate(h["mips"]):
        mw = max(1, h["width"] >> level)
        cw = CELL >> level
        if cw < 4 or mw < 4:
            break
        cx, cy = x0 >> level, y0 >> level
        img = icon.resize((cw, cw), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "DDS", pixel_format=fmt)
        blocks = buf.getvalue()[128:]
        per_row, row_blocks = mw // 4, cw // 4
        if len(blocks) != row_blocks * row_blocks * bsize:
            raise blp.BLPError("unexpected DXT block count for the icon")
        for by in range(row_blocks):
            src = blocks[by * row_blocks * bsize:(by + 1) * row_blocks * bsize]
            dst = off + ((cy // 4 + by) * per_row + cx // 4) * bsize
            if dst + len(src) > off + size:
                raise blp.BLPError("icon cell runs past the mip level")
            out[dst:dst + len(src)] = src
    return bytes(out)


def crop_atlas_member(atlas: Image.Image, member: dict) -> Image.Image:
    box = (member["CommittedLeft"], member["CommittedTop"], member["CommittedRight"], member["CommittedBottom"])
    return atlas.crop(box)

