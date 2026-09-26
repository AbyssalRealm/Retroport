"""Parser for M2 .skel files.

A .skel is chunked with the M2 convention (magics NOT reversed on disk). The
chunks we care about (SKA1 attachments, SKB1 bones, SKS1 sequences, SKL1 name,
SKPD parent link, AFID/BFID file ids) each hold a small fixed header of
M2Arrays whose offsets are relative to that chunk's content, followed by the
raw data the arrays point into.

This parser keeps each chunk's *content bytes* verbatim so the re-inliner can
relocate a whole region in one copy and rebase offsets by a single delta.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import structs as S


@dataclass
class ArrayRef:
    """An M2Array (count, offset) plus the chunk content the offset indexes."""
    count: int
    offset: int            # relative to chunk content start
    content: bytes         # the owning chunk's full content


@dataclass
class SkelData:
    name: bytes = b""
    parent_skel_file_id: int | None = None
    afid: list[tuple[int, int, int]] = field(default_factory=list)  # (anim_id, sub, file_id)
    bfid: list[int] = field(default_factory=list)

    bones: ArrayRef | None = None
    key_bone_lookup: ArrayRef | None = None
    attachments: ArrayRef | None = None
    attachment_lookup: ArrayRef | None = None
    global_loops: ArrayRef | None = None
    sequences: ArrayRef | None = None
    sequence_lookups: ArrayRef | None = None

    chunks: dict[str, bytes] = field(default_factory=dict)

    @property
    def has_parent(self) -> bool:
        return bool(self.parent_skel_file_id)

    @property
    def skb1(self) -> bytes:
        return self.chunks.get("SKB1", b"")

    @property
    def ska1(self) -> bytes:
        return self.chunks.get("SKA1", b"")

    @property
    def sks1(self) -> bytes:
        return self.chunks.get("SKS1", b"")


def _walk_chunks(data: bytes):
    """Yield (magic_str, content_bytes) for an M2-convention chunked file."""
    pos = 0
    n = len(data)
    while pos + 8 <= n:
        magic = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        start = pos + 8
        end = start + size
        if end > n:                      # truncated / corrupt: stop cleanly
            break
        yield magic.decode("ascii", "replace"), data[start:end]
        pos = end


def parse_skel(data: bytes) -> SkelData:
    skel = SkelData()
    for magic, content in _walk_chunks(data):
        skel.chunks[magic] = content

        if magic == "SKL1" and len(content) >= S.SKL1_HEADER:
            n, off = S.read_m2array(content, S.SKL1_NAME)
            if n and off + n <= len(content):
                skel.name = content[off:off + n].split(b"\x00", 1)[0]

        elif magic == "SKA1" and len(content) >= S.SKA1_HEADER:
            a_n, a_o = S.read_m2array(content, S.SKA1_ATTACHMENTS)
            l_n, l_o = S.read_m2array(content, S.SKA1_ATTACH_LOOKUP)
            skel.attachments = ArrayRef(a_n, a_o, content)
            skel.attachment_lookup = ArrayRef(l_n, l_o, content)

        elif magic == "SKB1" and len(content) >= S.SKB1_HEADER:
            b_n, b_o = S.read_m2array(content, S.SKB1_BONES)
            k_n, k_o = S.read_m2array(content, S.SKB1_KEY_BONE_LOOKUP)
            skel.bones = ArrayRef(b_n, b_o, content)
            skel.key_bone_lookup = ArrayRef(k_n, k_o, content)

        elif magic == "SKS1" and len(content) >= S.SKS1_HEADER:
            g_n, g_o = S.read_m2array(content, S.SKS1_GLOBAL_LOOPS)
            s_n, s_o = S.read_m2array(content, S.SKS1_SEQUENCES)
            q_n, q_o = S.read_m2array(content, S.SKS1_SEQUENCE_LOOKUPS)
            skel.global_loops = ArrayRef(g_n, g_o, content)
            skel.sequences = ArrayRef(s_n, s_o, content)
            skel.sequence_lookups = ArrayRef(q_n, q_o, content)

        elif magic == "SKPD" and len(content) >= S.SKPD_SIZE:
            skel.parent_skel_file_id = S.read_u32(content, S.SKPD_PARENT_FILE_ID) or None

        elif magic == "AFID":
            for i in range(0, len(content) - 7, 8):
                aid, sub, fid = struct.unpack_from("<HHI", content, i)
                skel.afid.append((aid, sub, fid))

        elif magic == "BFID":
            for i in range(0, len(content) - 3, 4):
                skel.bfid.append(struct.unpack_from("<I", content, i)[0])

    return skel
