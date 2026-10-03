"""Structural sanity checks for M2 / .skin bytes, used to verify every write.

The rule (adapted from Conjure's "re-read and verify before reporting success"):
an edit must not introduce a structural problem the input didn't already have.
So these functions return a *set* of problem strings, and ``no_new_problems``
builds a verifier that compares the written file against the pre-edit bytes.
Comparing against a baseline (instead of demanding a perfect file) matters:
converter output and synthetic fixtures legitimately carry odd arrays that a
fixup never touches, and a fixup must not be blamed for them.
"""

from __future__ import annotations

import struct
from typing import Callable

from . import structs as S


class VerifyError(ValueError):
    """A written file failed its post-write verification."""


def m2_data_block(data: bytes) -> bytes:
    """Return the MD20-layout bytes whether the file is raw MD20 or MD21-wrapped."""
    if data[:4] == b"MD20":
        return data
    pos = 0
    while pos + 8 <= len(data):
        magic = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if magic == b"MD21":
            return data[pos + 8:pos + 8 + size]
        pos += 8 + size
    raise ValueError("not an M2 (no MD20/MD21)")


# (name, header field offset, element size) for the MD20 header arrays we bounds-check
_M2_ARRAYS = (
    ("name", S.HDR_NAME, 1),
    ("global_loops", S.HDR_GLOBAL_LOOPS, S.M2LOOP),
    ("sequences", S.HDR_SEQUENCES, S.M2SEQUENCE),
    ("sequence_lookups", S.HDR_SEQUENCE_LOOKUPS, 2),
    ("bones", S.HDR_BONES, S.M2COMPBONE),
    ("key_bone_lookup", S.HDR_KEY_BONE_LOOKUP, 2),
    ("vertices", S.HDR_VERTICES, 48),
    ("textures", S.HDR_TEXTURES, S.M2TEXTURE),
    ("materials", S.HDR_MATERIALS, 4),
    ("attachments", S.HDR_ATTACHMENTS, S.M2ATTACHMENT),
    ("attachment_lookup", S.HDR_ATTACHMENT_LOOKUP, 2),
)


def _array_ok(buf: bytes, count: int, offset: int, elem: int) -> bool:
    return count == 0 or (offset + count * elem <= len(buf))


def combiner_header_overlaps(block: bytes) -> list[str]:
    """Header arrays whose data starts inside the combiner-combos field (0x130-0x137) while
    global flag 0x08 has the client read those 8 bytes as that field, so the data's first
    bytes are lost. MultiConverter does this when it adds combos to a model that had none."""
    if len(block) < S.MD20_HEADER_SIZE_COMBOS:
        return []
    if not S.read_u32(block, S.HDR_GLOBAL_FLAGS) & S.FLAG_USE_COMBINER_COMBOS:
        return []
    out: list[str] = []
    for name, field, _elem in S.WOTLK_HEADER_ARRAYS:
        cnt, off = S.read_m2array(block, field)
        if cnt and S.MD20_HEADER_SIZE <= off < S.MD20_HEADER_SIZE_COMBOS:
            out.append(name)
    return out


def combiner_combos_problem(m2: bytes, skins: list[bytes]) -> str | None:
    """Why a model's combiner-combos table (global flag 0x08) can't be used on WotLK, or
    None if it can. Blizzard's own 3.3.5 models use such a table in about one model in
    eight, most often [1, 4]: a texture plus an env-mapped reflection. ``skins`` are the
    bytes of the model's .skin files, whose batch shaders index the table."""
    block = m2_data_block(m2)
    cnt, off = S.read_m2array(block, S.HDR_TEXTURE_COMBINER_COMBOS)
    if not cnt:
        return "the combiner-combos table is empty"
    if off < S.MD20_HEADER_SIZE_COMBOS or off + 2 * cnt > len(block):
        return "the combiner-combos table lies outside the model data"
    overlaps = combiner_header_overlaps(block)
    if overlaps:
        return f"{', '.join(overlaps)} data overlaps the combiner-combos header field"
    bad = sorted({v for v in struct.unpack_from(f"<{cnt}H", block, off) if v > S.WOTLK_MAX_COMBINER_VALUE})
    if bad:
        return f"combo values {bad} are not WotLK blend modes (0-{S.WOTLK_MAX_COMBINER_VALUE})"
    for skin in skins:
        if skin[:4] != S.SKIN_MAGIC:
            continue
        b_cnt, b_off = S.read_m2array(skin, S.SKIN_BATCHES)
        for i in range(b_cnt):
            rec = b_off + i * S.M2BATCH
            if rec + S.M2BATCH > len(skin):
                break
            shader, = struct.unpack_from("<H", skin, rec + S.BATCH_SHADER)
            tex_count, = struct.unpack_from("<H", skin, rec + S.BATCH_TEXTURE_COUNT)
            if not shader & 0x8000 and shader + tex_count > cnt:
                return f"batch shader {shader} with {tex_count} texture(s) reads past the {cnt}-entry combo table"
    return None


def m2_problems(data: bytes) -> set[str]:
    """Structural problems in an M2 (MD20 or MD21): bad magic, header arrays or
    texture-name strings that run past the data block."""
    try:
        block = m2_data_block(data)
    except (ValueError, struct.error):
        return {"not an M2 (no MD20/MD21)"}
    if block[:4] != b"MD20" or len(block) < 0x100:
        return {"MD20 header missing or truncated"}
    out: set[str] = set()
    for name, field, elem in _M2_ARRAYS:
        cnt, off = S.read_m2array(block, field)
        if not _array_ok(block, cnt, off, elem):
            out.add(f"{name} array runs past end of data")
    for name in combiner_header_overlaps(block):
        out.add(f"{name} data overlaps the combiner-combos header field")
    t_cnt, t_off = S.read_m2array(block, S.HDR_TEXTURES)
    if _array_ok(block, t_cnt, t_off, S.M2TEXTURE):
        for i in range(t_cnt):
            n_cnt, n_off = S.read_m2array(block, t_off + i * S.M2TEXTURE + S.TEXTURE_FILENAME)
            if not _array_ok(block, n_cnt, n_off, 1):
                out.add(f"texture {i} name runs past end of data")
    return out


# .skin arrays: (name, offset, element size)
_SKIN_ARRAYS = (
    ("vertices", S.SKIN_VERTICES, 2),
    ("indices", S.SKIN_INDICES, 2),
    ("submeshes", S.SKIN_SUBMESHES, S.M2SKINSECTION),
    ("batches", S.SKIN_BATCHES, S.M2BATCH),
)


def skin_problems(data: bytes) -> set[str]:
    """Structural problems in a WotLK .skin: magic, arrays in bounds, and every
    batch pointing at a submesh that exists."""
    if data[:4] != S.SKIN_MAGIC or len(data) < 0x30:
        return {"not a SKIN file"}
    out: set[str] = set()
    for name, field, elem in _SKIN_ARRAYS:
        cnt, off = S.read_m2array(data, field)
        if not _array_ok(data, cnt, off, elem):
            out.add(f"{name} array runs past end of file")
    sub_cnt, _ = S.read_m2array(data, S.SKIN_SUBMESHES)
    b_cnt, b_off = S.read_m2array(data, S.SKIN_BATCHES)
    if _array_ok(data, b_cnt, b_off, S.M2BATCH):
        for i in range(b_cnt):
            sub = struct.unpack_from("<H", data, b_off + i * S.M2BATCH + 0x04)[0]
            if sub >= sub_cnt:
                out.add("a batch references a submesh that does not exist")
                break
    return out


def no_new_problems(before: bytes, problems: Callable[[bytes], set[str]]) -> Callable[[str], None]:
    """Build a verifier for ``safeio.write_verified``: the written file may keep
    the problems ``before`` already had, but must not add any."""
    baseline = problems(before)

    def verify(path: str) -> None:
        with open(path, "rb") as fh:
            after = fh.read()
        new = problems(after) - baseline
        if new:
            raise VerifyError("edit would corrupt the file: " + "; ".join(sorted(new)))

    return verify
