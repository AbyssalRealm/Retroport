"""Asset scanner.

Classifies a file by reading its header and walking its chunk structure. No
external dependencies and no game files required - works on any seekable stream,
which makes it fully unit-testable with synthetic fixtures.

The scanner answers, per file: what type, which expansion, MD20 vs MD21, does it
use a .skel skeleton, does it reference FileDataIDs, is it a split ADT. That is
exactly the information the router needs to decide a conversion path.
"""

from __future__ import annotations

import os
import struct
from typing import BinaryIO, Iterator

from ..models import AssetInfo, AssetType
from . import signatures as sig


def _iter_chunks(
    fh: BinaryIO,
    reversed_names: bool,
    max_chunks: int = 4096,
) -> Iterator[tuple[bytes, int, int]]:
    """Yield ``(human_magic, size, data_offset)`` for each chunk.

    ``human_magic`` is the 4-byte magic in human order (reversed off-disk for
    RIFF-family files). Walking is bounded and tolerant of corrupt sizes: a read
    that runs short simply ends iteration.
    """
    fh.seek(0)
    pos = 0
    for _ in range(max_chunks):
        fh.seek(pos)
        hdr = fh.read(8)
        if len(hdr) < 8:
            return
        raw_magic = hdr[:4]
        (size,) = struct.unpack("<I", hdr[4:8])
        human = raw_magic[::-1] if reversed_names else raw_magic
        data_off = pos + 8
        yield human, size, data_off
        # zero-size chunks: step past the header only, so we keep walking
        pos = data_off + (size if size else 0)


def _read_u32_at(fh: BinaryIO, offset: int) -> int | None:
    fh.seek(offset)
    b = fh.read(4)
    if len(b) < 4:
        return None
    return struct.unpack("<I", b)[0]


# --- per-family classifiers -------------------------------------------------

def _classify_blp(fh: BinaryIO, info: AssetInfo, head: bytes) -> AssetInfo:
    info.type = AssetType.BLP
    if head == sig.MAGIC_BLP1:
        info.notes.append("BLP1 (WotLK prefers BLP2)")
        info.width = _read_u32_at(fh, 12)
        info.height = _read_u32_at(fh, 16)
        return info

    # BLP2 fixed header: encoding@8, alphaDepth@9, alphaEncoding@10,
    # hasMips@11, width@12, height@16.
    info.notes.append("BLP2")
    fh.seek(8)
    meta = fh.read(4)
    if len(meta) == 4:
        encoding, _alpha_depth, alpha_encoding, has_mips = meta[0], meta[1], meta[2], meta[3]
        info.has_mipmaps = has_mips != 0
        if encoding == 2:  # DXTC
            info.compression = {0: "DXT1", 1: "DXT3", 7: "DXT5"}.get(alpha_encoding, "DXT?")
        elif encoding == 1:
            info.compression = "PAL"
        elif encoding == 3:
            info.compression = "BGRA"
        else:
            info.compression = "JPEG"
    info.width = _read_u32_at(fh, 12)
    info.height = _read_u32_at(fh, 16)
    return info


def _classify_m2(fh: BinaryIO, info: AssetInfo, head: bytes) -> AssetInfo:
    info.type = AssetType.M2
    if head == sig.MAGIC_MD20:
        info.chunked = False
        info.version = _read_u32_at(fh, 4)
    else:
        # MD21 first, or some other M2 chunk first -> chunked model.
        info.chunked = True
        present: set[bytes] = set()
        for human, _size, data_off in _iter_chunks(fh, reversed_names=False):
            present.add(human)
            if human == sig.MAGIC_MD21:
                # MD21 data begins with "MD20" + version word.
                info.version = _read_u32_at(fh, data_off + 4)
        info.uses_skel = b"SKID" in present
        info.uses_fdid = bool(present & sig.M2_FDID_CHUNKS)
        info.chunks = sorted(c.decode("ascii", "replace") for c in present)

    if info.version is not None:
        info.expansion = sig.expansion_for_m2_version(info.version)
    return info


def _classify_riff(fh: BinaryIO, info: AssetInfo) -> AssetInfo:
    """WMO / ADT / WDT all start with MVER (REVM on disk); the next chunk tells
    us which."""
    present: set[bytes] = {human for human, _s, _o in _iter_chunks(fh, reversed_names=True)}
    info.chunks = sorted(c.decode("ascii", "replace") for c in present)

    if present & sig.WMO_ROOT_CHUNKS:
        info.type = AssetType.WMO
        info.notes.append("WMO root")
    elif present & sig.WMO_GROUP_CHUNKS:
        info.type = AssetType.WMO
        info.notes.append("WMO group")
    elif present & sig.WDT_CHUNKS:
        info.type = AssetType.WDT
    elif present & sig.ADT_CHUNKS:
        info.type = AssetType.ADT
        info.uses_fdid = bool(present & sig.ADT_FDID_CHUNKS)
    else:
        # Only MVER recognised in the walked window (can happen on split
        # tex0/obj0 fragments). Best-effort guess.
        info.type = AssetType.ADT
        info.notes.append("RIFF chunk, only MVER recognised")
    return info


# --- entry points -----------------------------------------------------------

def classify_stream(fh: BinaryIO, path_hint: str = "") -> AssetInfo:
    info = AssetInfo(path=path_hint, type=AssetType.UNKNOWN)
    fh.seek(0)
    head = fh.read(4)
    fh.seek(0)
    ext = os.path.splitext(path_hint)[1].lower()

    if head in (sig.MAGIC_BLP2, sig.MAGIC_BLP1):
        return _classify_blp(fh, info, head)
    if head in (sig.MAGIC_MD20, sig.MAGIC_MD21) or head in sig.M2_CHUNK_MAGICS:
        return _classify_m2(fh, info, head)
    if head == sig.MAGIC_REVM:
        return _classify_riff(fh, info)
    if head == sig.MAGIC_SKL1:
        info.type = AssetType.SKEL
        return info
    if head == sig.MAGIC_AFM2:
        info.type = AssetType.ANIM
        info.notes.append("chunked anim")
        return info
    if head == sig.MAGIC_SKIN:
        info.type = AssetType.SKIN
        return info

    # Extension fallbacks: modern .skin/.anim/.skel often have no stable magic.
    fallback = {".skin": AssetType.SKIN, ".anim": AssetType.ANIM,
                ".skel": AssetType.SKEL, ".blp": AssetType.BLP}.get(ext)
    if fallback:
        info.type = fallback
        info.notes.append("classified by extension")
    return info


def _has_split_adt_siblings(path: str) -> bool:
    if not path.lower().endswith(".adt"):
        return False
    base = path[:-4]
    return any(os.path.exists(base + suffix) for suffix in ("_tex0.adt", "_obj0.adt"))


def classify_file(path: str) -> AssetInfo:
    """Classify a file on disk. Returns an UNKNOWN AssetInfo on read errors."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            info = classify_stream(fh, path_hint=path)
        info.size = size
    except OSError as exc:
        info = AssetInfo(path=path, type=AssetType.UNKNOWN, notes=[f"read error: {exc}"])
        return info

    if info.type == AssetType.ADT:
        info.split_adt = _has_split_adt_siblings(path)
    return info
