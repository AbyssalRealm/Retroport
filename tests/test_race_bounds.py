"""retroport race: header bounds from the sequences the client can play (bounds.py)."""

import math
import struct

import pytest

from retroport.race import bounds

PLAYABLE = set(range(506))                              # AnimationData.dbc ids in 12340


def _m2(sequences, box=(-5.0, -5.0, -5.0, 5.0, 5.0, 5.0), radius=8.0) -> bytes:
    m2 = bytearray(0x130)
    m2[0:4] = b"MD20"
    struct.pack_into("<6f", m2, 0xA0, *box)
    struct.pack_into("<f", m2, 0xB8, radius)
    records = b""
    for anim, seq_box in sequences:
        rec = bytearray(0x40)
        struct.pack_into("<H", rec, 0, anim)
        struct.pack_into("<6f", rec, 0x20, *seq_box)
        records += bytes(rec)
    struct.pack_into("<II", m2, 0x1C, len(sequences), len(m2))
    return bytes(m2 + records)


def test_bounds_cover_only_the_animations_the_client_plays():
    m2 = _m2([(0, (-1.0, -1.0, 0.0, 1.0, 1.0, 2.0)), (5, (-2.0, -1.0, 0.0, 1.0, 1.0, 2.5)),
              (1004, (-4.0, -3.0, -1.0, 4.0, 3.0, 5.5))])           # a BfA-only animation
    out, old, new = bounds.fit_header_bounds(m2, PLAYABLE)
    assert old == 8.0
    assert struct.unpack_from("<6f", out, 0xA0) == pytest.approx((-2.0, -1.0, 0.0, 1.0, 1.0, 2.5))
    assert new == pytest.approx(math.dist((-2.0, -1.0, 0.0), (1.0, 1.0, 2.5)) / 2)
    assert struct.unpack_from("<f", out, 0xB8)[0] == pytest.approx(new)
    assert out[:0xA0] == m2[:0xA0] and out[0xBC:] == m2[0xBC:]       # nothing else moves
    assert bounds.fit_header_bounds(out, PLAYABLE)[0] == out          # rebuilding changes nothing


def test_a_model_with_no_playable_sequence_keeps_its_bounds():
    m2 = _m2([(1004, (-4.0, -3.0, -1.0, 4.0, 3.0, 5.5))])
    out, old, new = bounds.fit_header_bounds(m2, PLAYABLE)
    assert out == m2 and old == new == 8.0
    with pytest.raises(ValueError, match="MD20"):
        bounds.fit_header_bounds(b"MD21" + m2[4:], PLAYABLE)
