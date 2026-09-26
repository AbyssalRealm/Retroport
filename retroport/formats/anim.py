"""Parser for M2 .anim files.

A WotLK .anim is headerless raw track data. A Legion non-.skel .anim is the same
bytes wrapped in a single AFM2 chunk. For a .skel-based model the data is split
into AFM2 (events), AFSA (attachment tracks) and AFSB (bone tracks) - "the same
content as an old anim file would have" (wowdev.wiki/M2). Chunk magics follow the
M2 convention (NOT reversed on disk).

We expose each chunk's content verbatim so the re-inliner can relocate a whole
region and rebase the external track offsets by a single delta.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_ANIM_MAGICS = (b"AFM2", b"AFSA", b"AFSB", b"AFSR", b"AFM2")


@dataclass
class AnimData:
    afm2: bytes = b""      # events (and, for non-.skel models, the whole payload)
    afsa: bytes = b""      # attachment track data
    afsb: bytes = b""      # bone track data
    chunked: bool = False  # False => headerless old-style .anim (whole file is raw)
    raw: bytes = b""       # the whole file, for the headerless case


def parse_anim(data: bytes) -> AnimData:
    out = AnimData(raw=data)
    if len(data) < 8 or data[:4] not in _ANIM_MAGICS:
        # headerless old-form .anim: a single offset space, kept as raw.
        return out

    out.chunked = True
    pos, n = 0, len(data)
    while pos + 8 <= n:
        magic = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        start = pos + 8
        end = start + size
        if end > n:
            break
        content = data[start:end]
        if magic == b"AFM2":
            out.afm2 = content
        elif magic == b"AFSA":
            out.afsa = content
        elif magic == b"AFSB":
            out.afsb = content
        pos = end
    return out
