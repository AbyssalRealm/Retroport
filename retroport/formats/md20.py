"""WotLK MD20 readers and the texture-path baker.

Texture baking and the animation-table classifier are adapted from Conjure's
``conjure/m2.py`` (MIT, TheDaveCalaz). Readers accept MD20 or MD21 data blocks;
baking is MD20-only, because it appends the path string at end-of-file, which
is only valid when offsets are absolute file offsets (true for MD20, not for an
MD21 file with chunks after the data block).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import structs as S

# M2Sequence (WotLK, 0x40 bytes): id u16 @0, variation u16 @2, flags u32 @0x0C, aliasNext u16 @0x3E
SEQ_FLAG_ALIAS = 0x40
SEQ_ALIAS_NEXT = 0x3E

# bounding box: CAaBox (min xyz, max xyz) then radius float
HDR_BOUNDING_BOX = 0xA0


@dataclass
class TextureSlot:
    index: int
    type: int
    flags: int
    name: str


@dataclass
class Sequence:
    anim_id: int
    sub_id: int
    flags: int
    alias_next: int

    @property
    def kind(self) -> str:
        """'alias' (resolves to another sequence), 'inline' (keyframes in the
        .m2) or 'external' (keyframes in <model><id>-<sub>.anim)."""
        if self.flags & SEQ_FLAG_ALIAS:
            return "alias"
        if self.flags & S.SEQ_FLAG_INTERNAL_0x20:
            return "inline"
        return "external"

    @property
    def tag(self) -> str:
        return f"{self.anim_id:04d}-{self.sub_id:02d}"


def read_texture_slots(block: bytes) -> list[TextureSlot]:
    out: list[TextureSlot] = []
    cnt, off = S.read_m2array(block, S.HDR_TEXTURES)
    for i in range(cnt):
        rec = off + i * S.M2TEXTURE
        if rec + S.M2TEXTURE > len(block):
            break
        ttype, flags = struct.unpack_from("<II", block, rec)
        n_cnt, n_off = S.read_m2array(block, rec + S.TEXTURE_FILENAME)
        name = ""
        if n_cnt and n_off + n_cnt <= len(block):
            name = block[n_off:n_off + n_cnt].split(b"\x00", 1)[0].decode("latin-1", "replace")
        out.append(TextureSlot(i, ttype, flags, name))
    return out


def read_sequences(block: bytes) -> list[Sequence]:
    out: list[Sequence] = []
    cnt, off = S.read_m2array(block, S.HDR_SEQUENCES)
    for i in range(cnt):
        rec = off + i * S.M2SEQUENCE
        if rec + S.M2SEQUENCE > len(block):
            break
        anim_id, sub_id = struct.unpack_from("<HH", block, rec)
        flags = S.read_u32(block, rec + S.M2SEQUENCE_FLAGS)
        alias_next = struct.unpack_from("<H", block, rec + SEQ_ALIAS_NEXT)[0]
        out.append(Sequence(anim_id, sub_id, flags, alias_next))
    return out


def read_bounds(block: bytes) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """The model's bounding box (min, max), or None if the header is too short."""
    if HDR_BOUNDING_BOX + 24 > len(block):
        return None
    v = struct.unpack_from("<6f", block, HDR_BOUNDING_BOX)
    return (v[0], v[1], v[2]), (v[3], v[4], v[5])


def bake_texture_paths(data: bytes, slot_paths: dict[int, str]) -> bytes:
    """Return a copy of an MD20 file with each slot in ``slot_paths`` baked:
    type -> 0 (hardcoded), the path appended at end-of-file, and the slot's name
    M2Array pointed at it. Every other offset is left untouched."""
    if data[:4] != b"MD20":
        raise ValueError("texture baking needs an MD20 (converted) model; run MultiConverter first")
    buf = bytearray(data)
    cnt, off = S.read_m2array(buf, S.HDR_TEXTURES)
    if off + cnt * S.M2TEXTURE > len(buf):
        raise ValueError("texture array runs past end of file")
    for idx, path in sorted(slot_paths.items()):
        if not 0 <= idx < cnt:
            raise ValueError(f"texture slot {idx} out of range (model has {cnt} slots)")
        if not path:
            raise ValueError(f"texture slot {idx}: path cannot be empty")
        name = path.encode("latin-1") + b"\x00"
        name_off = len(buf)
        buf += name
        rec = off + idx * S.M2TEXTURE
        flags = S.read_u32(buf, rec + 4)
        struct.pack_into("<II", buf, rec, 0, flags)
        S.write_m2array(buf, rec + S.TEXTURE_FILENAME, len(name), name_off)
    return bytes(buf)
