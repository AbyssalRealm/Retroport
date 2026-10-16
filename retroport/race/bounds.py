"""Fit a ported model's header bounds to the animations the 3.3.5 client can play.

An M2's header bounding box and radius are the union of its sequences' bounds. A BfA model
keeps sequences the 3.3.5 client never plays - animation ids past AnimationData.dbc's last
row (505 in 12340) - and they are its largest: with them a Kul Tiran female's header radius
is 6.1, where the sequences the client can play need 4.3 (Human's is 3.7).

The client tests that sphere against the liquid plane under the unit: CM2Scene's element build
(0x821CBF) takes the header box's centre and radius (times the model's scale) against the
horizontal plane the unit's liquid query sets (0x7C10C0, from 0x7C2525), and every batch of a
model that straddles it is drawn with a user clip plane (element flag 2, set up at 0x81FD61).
In this client that plane cuts through the model at some camera angles - a Kul Tiran standing
near water lost her face, half her belt buckle and slices of her fingers while the camera
turned, and her shoulder pads opened onto their own flames. An oversized sphere straddles
water the body is nowhere near.

The fitted box is the union of the playable sequences' boxes and the radius its half diagonal,
which is how Blizzard's own headers relate to their sequences.

M2 (12340): 0x1C sequences (0x40 each: +0 animation id, +0x20 box min xyz / max xyz, +0x38
radius), 0xA0 header box (min xyz, max xyz), 0xB8 header radius.
"""

from __future__ import annotations

import math
import struct

HDR_SEQUENCES = 0x1C
HDR_BOX = 0xA0
HDR_RADIUS = 0xB8
SEQUENCE_SIZE = 0x40
SEQUENCE_BOX = 0x20


def fit_header_bounds(m2: bytes, known_animations: set[int]) -> tuple[bytes, float, float]:
    """(model, old radius, new radius): ``m2`` with its header box set to the union of the boxes
    of the sequences whose animation id is in ``known_animations``, and its radius to that box's
    half diagonal. A model with no such sequence comes back unchanged."""
    if m2[:4] != b"MD20":
        raise ValueError("not an MD20 model")
    count, offset = struct.unpack_from("<II", m2, HDR_SEQUENCES)
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for i in range(count):
        record = offset + i * SEQUENCE_SIZE
        if struct.unpack_from("<H", m2, record)[0] not in known_animations:
            continue
        box = struct.unpack_from("<6f", m2, record + SEQUENCE_BOX)
        for k in range(3):
            lo[k], hi[k] = min(lo[k], box[k]), max(hi[k], box[3 + k])
    old = struct.unpack_from("<f", m2, HDR_RADIUS)[0]
    if math.isinf(lo[0]):
        return m2, old, old
    radius = math.dist(lo, hi) / 2
    out = bytearray(m2)
    struct.pack_into("<6f", out, HDR_BOX, *lo, *hi)
    struct.pack_into("<f", out, HDR_RADIUS, radius)
    return bytes(out), old, radius
