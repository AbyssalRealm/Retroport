"""M2 file model with just enough editing power for .skel re-inlining.

Parses a chunked (MD21) M2 into its ordered chunk list, exposes the MD21 data
block (the MD20-layout bytes) as a mutable buffer you can append to and rewrite
header arrays in, and re-serialises to a valid MD21 file with the chunk size
fixed up. Non-MD21 (already MD20) files are rejected - they need no re-inlining.
"""

from __future__ import annotations

import struct

from . import structs as S


class M2Error(Exception):
    pass


class M2File:
    def __init__(self, data: bytes):
        if data[:4] == b"MD20":
            raise M2Error("file is already MD20 (non-chunked); no .skel to re-inline")
        if len(data) < 8:
            raise M2Error("file too small to be an M2")

        self.chunks: list[list] = []          # ordered [magic_str, bytearray content]
        self._md21_index: int | None = None
        pos, n = 0, len(data)
        while pos + 8 <= n:
            magic = data[pos:pos + 4].decode("ascii", "replace")
            (size,) = struct.unpack_from("<I", data, pos + 4)
            start = pos + 8
            end = start + size
            if end > n:
                raise M2Error(f"chunk {magic} runs past EOF")
            if magic == "MD21":
                self._md21_index = len(self.chunks)
            self.chunks.append([magic, bytearray(data[start:end])])
            pos = end

        if self._md21_index is None:
            raise M2Error("no MD21 chunk found")
        if self.md20[:4] != b"MD20":
            raise M2Error("MD21 content does not start with MD20 magic")

    # --- the editable MD20 data block --------------------------------------
    @property
    def md20(self) -> bytearray:
        return self.chunks[self._md21_index][1]

    @property
    def version(self) -> int:
        return S.read_u32(self.md20, S.MD20_VERSION)

    @property
    def global_flags(self) -> int:
        return S.read_u32(self.md20, S.HDR_GLOBAL_FLAGS)

    def has_chunk(self, magic: str) -> bool:
        return any(c[0] == magic for c in self.chunks)

    def chunk(self, magic: str) -> bytearray | None:
        for m, content in self.chunks:
            if m == magic:
                return content
        return None

    def remove_chunk(self, magic: str) -> bool:
        before = len(self.chunks)
        self.chunks = [c for c in self.chunks if c[0] != magic]
        # md21 index may have shifted
        for i, c in enumerate(self.chunks):
            if c[0] == "MD21":
                self._md21_index = i
                break
        return len(self.chunks) != before

    def skel_file_id(self) -> int | None:
        skid = self.chunk("SKID")
        if skid is not None and len(skid) >= 4:
            return S.read_u32(skid, 0) or None
        return None

    # --- editing the data block --------------------------------------------
    def append_data(self, blob: bytes, align: int = 4) -> int:
        """Append bytes to the MD20 data block, return the offset they start at."""
        md20 = self.md20
        while len(md20) % align:
            md20.append(0)
        base = len(md20)
        md20.extend(blob)
        return base

    def read_header_array(self, field_off: int) -> tuple[int, int]:
        return S.read_m2array(self.md20, field_off)

    def set_header_array(self, field_off: int, count: int, offset: int) -> None:
        S.write_m2array(self.md20, field_off, count, offset)

    # --- output -------------------------------------------------------------
    def to_bytes(self) -> bytes:
        out = bytearray()
        for magic, content in self.chunks:
            out += magic.encode("ascii")
            out += struct.pack("<I", len(content))
            out += content
        return bytes(out)
