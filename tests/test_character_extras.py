import struct

from retroport.character.extras import ExtraRegion, region_rect, split_skin
from retroport.character.layouts import Layout, Rect
from retroport.formats import structs as S


def _skin(lookup, indices, subs, batches):
    """A 3.3.5 skin: subs as (id, vertexStart, vertexCount, indexStart, indexCount), batches as
    (section, texture combo)."""
    body = bytearray(48)
    arrays = []
    for fmt, items in (("H", lookup), ("H", indices)):
        arrays.append((len(items), struct.pack(f"<{len(items)}{fmt}", *items)))
    arrays.append((len(lookup), b"".join(bytes([k, 0, 0, 0]) for k in range(len(lookup)))))     # bones
    arrays.append((len(subs), b"".join(struct.pack("<6H", sid, 0, vs, vc, ist, ic) + bytes(36)
                                       for sid, vs, vc, ist, ic in subs)))
    arrays.append((len(batches), b"".join(struct.pack("<BbHHHhHHHHHHH", 0, 0, 0, sec, sec, -1, 0, 0, 1, combo,
                                                      0, 0, 0) for sec, combo in batches)))
    body[0:4] = b"SKIN"
    for k, (count, payload) in enumerate(arrays):
        body.extend(bytes(-len(body) % 16))
        struct.pack_into("<II", body, 4 + 8 * k, count, len(body))
        body.extend(payload)
    return bytes(body)


def _read(skin):
    nv, ov = S.read_m2array(skin, S.SKIN_VERTICES)
    ni, oi = S.read_m2array(skin, S.SKIN_INDICES)
    ns, os_ = S.read_m2array(skin, S.SKIN_SUBMESHES)
    nb, ob = S.read_m2array(skin, S.SKIN_BATCHES)
    lookup = struct.unpack_from(f"<{nv}H", skin, ov)
    indices = struct.unpack_from(f"<{ni}H", skin, oi)
    subs = [struct.unpack_from("<6H", skin, os_ + i * 48) for i in range(ns)]
    batches = [struct.unpack_from("<HHHH", skin, ob + i * 24 + 4) for i in range(nb)]     # section, geoset, ...
    combos = [struct.unpack_from("<HH", skin, ob + i * 24 + 0x0E) for i in range(nb)]
    return lookup, indices, subs, batches, combos


def test_the_toes_get_a_submesh_and_vertex_window_of_their_own():
    # body vertices 10, 12, 14 and toe vertices 11, 13, 15 interleaved in the body's window;
    # then a glove piece (401) on vertices 16-18
    lookup = [10, 11, 12, 13, 14, 15, 16, 17, 18]
    indices = [0, 2, 4, 1, 3, 5, 6, 7, 8]
    skin = _skin(lookup, indices, [(0, 0, 6, 0, 6), (401, 6, 3, 6, 3)], [(0, 0), (1, 0)])
    region = ExtraRegion()
    out = split_skin(skin, {11, 13, 15}, {0}, 7, region)
    lk, idx, subs, batches, combos = _read(out)
    assert [s[0] for s in subs] == [0, 0, 401]
    body, toes, glove = subs
    assert body[2:] == (0, 3, 0, 3) and toes[2:] == (3, 3, 3, 3) and glove[2:] == (6, 3, 6, 3)
    assert [lk[i] for i in idx[0:3]] == [10, 12, 14]              # the same triangles...
    assert [lk[i] for i in idx[3:6]] == [11, 13, 15]
    assert all(0 <= i < 3 for i in idx[0:3]) and all(3 <= i < 6 for i in idx[3:6])   # ...in their own windows
    assert [b[:2] for b in batches] == [(0, 0), (1, 1), (2, 2)]   # the glove's batch follows its piece
    assert combos == [(1, 0), (1, 7), (1, 0)]                     # the toes' copy draws the extra texture
    assert region.triangles == 1 and region.submeshes == 1 and not region.skipped


def test_a_piece_all_outside_turns_over_whole_and_mixed_triangles_stay():
    lookup = [10, 11, 12, 20, 21, 22]
    indices = [0, 1, 2, 3, 4, 5]
    skin = _skin(lookup, indices, [(1104, 0, 3, 0, 3), (0, 3, 3, 3, 3)], [(0, 0), (1, 0)])
    region = ExtraRegion()
    out = split_skin(skin, {10, 11, 12, 20}, {0}, 7, region)
    _lk, _idx, subs, _b, combos = _read(out)
    assert len(subs) == 2 and combos == [(1, 7), (1, 0)]          # the tail piece draws the extra texture
    assert region.skipped == 1                                     # 20-21-22 shares a body vertex


def test_the_corner_is_rounded_out_to_the_grid():
    src = Layout(1024, 512)
    assert region_rect([(16, 361), (252, 510), (24, 369)], src) == Rect(0, 320, 256, 192)
    assert region_rect([(2, 437), (247, 511)], src) == Rect(0, 384, 256, 128)
