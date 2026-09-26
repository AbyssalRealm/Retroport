"""WotLK-compatibility binary fixups on converted models/skins.

These operate on files on disk and are pure-Python (no external tools):

* ``clamp_skin_submeshes`` - drop skin submeshes (and the batches that use them)
  down to WotLK's ceiling (~59). Modern models carry far more geoset groups than
  WotLK handles; the extras overflow and render wrong. (Concept from RaceBuilder's
  clamp_skin_submeshes; validate against that proven version on real files.)
* ``clear_stale_anim_flags`` - after full re-inlining there are no .anim files, so
  the header's chunked-anim flags (0x2000 / 0x200000) are stale; clear them.
* ``set_num_skin_profiles`` - set the M2's nViews to match the skins you shipped.
"""

from __future__ import annotations

import os
import struct

from ..formats import structs as S


def _data_block_bounds(data: bytes) -> tuple[int, int]:
    """Return (start, length) of the MD20 data within the file (handles MD21)."""
    if data[:4] == b"MD20":
        return 0, len(data)
    if data[:4] == b"MD21":
        (size,) = struct.unpack_from("<I", data, 4)
        return 8, size
    raise ValueError("not an M2 (no MD20/MD21)")


def clear_stale_anim_flags(m2_path: str) -> bool:
    """Clear the chunked/upgraded .anim flags in global_flags. Returns True if changed."""
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    start, _ = _data_block_bounds(data)
    off = start + S.HDR_GLOBAL_FLAGS
    flags = struct.unpack_from("<I", data, off)[0]
    new = flags & ~(S.FLAG_CHUNKED_ANIM_0x2000 | S.FLAG_UPGRADED_ANIM_0x200000)
    if new == flags:
        return False
    struct.pack_into("<I", data, off, new)
    with open(m2_path, "wb") as fh:
        fh.write(data)
    return True


def set_num_skin_profiles(m2_path: str, n: int) -> None:
    """Set the M2 header's nViews (skin profile count)."""
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    start, _ = _data_block_bounds(data)
    struct.pack_into("<I", data, start + S.HDR_NUM_SKIN_PROFILES, n)
    with open(m2_path, "wb") as fh:
        fh.write(data)


def clamp_skin_submeshes(skin_path: str, max_submeshes: int = 59) -> int:
    """Truncate a .skin to at most ``max_submeshes`` submeshes and drop batches
    that reference the removed ones. Returns the new submesh count.

    Submeshes are ordered by geoset id (body first, Shadowlands extras last), so
    keeping the first N retains the core mesh and drops the modern-only geosets.
    """
    with open(skin_path, "rb") as fh:
        data = bytearray(fh.read())
    if data[:4] != S.SKIN_MAGIC:
        raise ValueError("not a SKIN file")

    sub_cnt, _sub_off = S.read_m2array(data, S.SKIN_SUBMESHES)
    if sub_cnt <= max_submeshes:
        return sub_cnt

    # keep the first N submeshes
    S.write_m2array(data, S.SKIN_SUBMESHES, max_submeshes, _sub_off)

    # filter batches to those referencing a kept submesh
    bat_cnt, bat_off = S.read_m2array(data, S.SKIN_BATCHES)
    kept = bytearray()
    kept_n = 0
    for i in range(bat_cnt):
        rec = bat_off + i * S.M2BATCH
        if rec + S.M2BATCH > len(data):
            break
        sub_index = struct.unpack_from("<H", data, rec + 0x04)[0]  # skinSectionIndex
        if sub_index < max_submeshes:
            kept += data[rec:rec + S.M2BATCH]
            kept_n += 1
    # overwrite the batch array in place with the kept subset (shorter), fix count
    data[bat_off:bat_off + len(kept)] = kept
    S.write_m2array(data, S.SKIN_BATCHES, kept_n, bat_off)

    with open(skin_path, "wb") as fh:
        fh.write(data)
    return max_submeshes


def clear_combiner_combos(m2_path: str, skin_paths: list[str]) -> tuple[bool, int]:
    """Clear the texture-combiner-combos global flag (0x08) and normalise batch
    shaders - the common fix for Shadowlands models rendering black on WotLK, where
    M2Batch.shader is otherwise read as an index into the combiner-combos table.

    Shaders are normalised in place: 0x8000 ("do not render") is preserved, and any
    other non-zero value (a combo index that is meaningless on WotLK) is reset to 0,
    the default shader. Returns (flag_was_cleared, shaders_changed).
    """
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    start, _ = _data_block_bounds(data)
    off = start + S.HDR_GLOBAL_FLAGS
    flags = struct.unpack_from("<I", data, off)[0]
    flag_cleared = bool(flags & S.FLAG_USE_COMBINER_COMBOS)
    if flag_cleared:
        struct.pack_into("<I", data, off, flags & ~S.FLAG_USE_COMBINER_COMBOS)
        with open(m2_path, "wb") as fh:
            fh.write(data)

    changed = 0
    for sp in skin_paths:
        try:
            with open(sp, "rb") as fh:
                sdata = bytearray(fh.read())
        except OSError:
            continue
        if sdata[:4] != S.SKIN_MAGIC:
            continue
        b_cnt, b_off = S.read_m2array(sdata, S.SKIN_BATCHES)
        dirty = False
        for i in range(b_cnt):
            rec = b_off + i * S.M2BATCH
            if rec + S.M2BATCH > len(sdata):
                break
            sh = struct.unpack_from("<H", sdata, rec + S.BATCH_SHADER)[0]
            if sh & 0x8000:
                new = 0x8000            # preserve "do not render"
            elif sh != 0:
                new = 0                 # combo index / invalid -> default WotLK shader
            else:
                new = 0
            if new != sh:
                struct.pack_into("<H", sdata, rec + S.BATCH_SHADER, new)
                changed += 1
                dirty = True
        if dirty:
            with open(sp, "wb") as fh:
                fh.write(sdata)
    return flag_cleared, changed
