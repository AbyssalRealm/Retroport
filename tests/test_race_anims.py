"""retroport race: animations play like the look template's (anims.py)."""

import struct

from retroport.race import anims


def _model(seqs: list[tuple[int, int, int, int, int, int]]) -> bytes:
    """An M2 whose only content is its sequences: (anim id, variation, duration, flags, replay min, max)."""
    m2 = bytearray(0x200)
    struct.pack_into("<II", m2, 0x1C, len(seqs), len(m2))
    for aid, var, dur, flags, rmin, rmax in seqs:
        rec = bytearray(64)
        struct.pack_into("<HHI", rec, 0, aid, var, dur)
        struct.pack_into("<I", rec, 12, flags)
        struct.pack_into("<II", rec, 20, rmin, rmax)
        m2 += rec
    return bytes(m2)


def test_blend_bits_and_one_shot_emote_lengths_follow_the_template():
    human = _model([(77, 0, 4333, 0x81, 0, 0),      # EmoteCry
                    (78, 0, 533, 0x81, 4, 6),       # EmoteChicken: a short cycle repeated 4-6 times
                    (46, 0, 1000, 0x81, 0, 0),      # AttackBow: not an emote, never repeated
                    (69, 0, 2133, 0x80, 0, 0)])     # EmoteDance (a looping state)
    kul_tiran = _model([(77, 0, 667, 0x0, 0, 0), (78, 0, 2134, 0x0, 0, 0), (46, 0, 300, 0x0, 0, 0),
                        (69, 0, 6966, 0x0, 0, 0), (999, 0, 100, 0x0, 0, 0)])
    out, changes = anims.align_sequences(kul_tiran, human, one_shot={77, 78})
    got = {aid: (flags, rmin, rmax) for _rec, aid, _v, _d, flags, rmin, rmax in anims.sequences(out)}
    assert got[77] == (0x81, 6, 6)          # blends, and the 0.67 s sob repeats to ~4.3 s
    assert got[78] == (0x81, 0, 0)          # 2.1 s already is >= 60% of 0.53 s x 5: blends only
    assert got[46] == (0x81, 0, 0)          # combat animation: blend bits only
    assert got[69] == (0x80, 0, 0)
    assert got[999] == (0, 0, 0)            # no template sequence: untouched
    assert len(changes) == 5


def test_one_shot_emotes_are_emote_animations_without_a_special_proc():
    emotes = [[18, 0, 77, 0, 0, 0, 0],      # /cry: one-shot
              [10, 0, 69, 28801, 2, 0, 0],  # /dance: a looping state
              [13, 0, 0, 25090, 1, 1, 0],   # /sit: a stand state, no animation
              [50, 0, 46, 0, 0, 0, 0]]      # an emote row playing a combat animation
    names = {77: "EmoteCry", 69: "EmoteDance", 46: "AttackBow"}
    assert anims.one_shot_emotes(emotes, names) == {77}


def _with_lookups(m2: bytes, size: int) -> bytes:
    """``m2`` with a sequence lookup table of ``size`` slots, filled the client's way (first
    sequence of each id at id % size, probing on (i * i + last slot) % size)."""
    table = [anims.NO_SEQUENCE] * size
    for index, (_rec, aid, var, *_rest) in enumerate(anims.sequences(m2)):
        if var:
            continue
        slot, probe = aid % size, 0
        while table[slot] != anims.NO_SEQUENCE:
            probe += 1
            slot = (probe * probe + slot) % size
        table[slot] = index
    out = bytearray(m2)
    struct.pack_into("<II", out, 0x24, size, len(out))
    return bytes(out + struct.pack(f"<{size}H", *table))


def test_bfa_animations_move_under_the_ids_the_client_can_play():
    # Haymaker (1444) and its wind-up (1446) into CustomSpell01/02 (213, 214), where this model
    # has a BfA animation of its own already; 13 slots so the new ids probe past taken ones
    bfa = _with_lookups(_model([(0, 0, 1000, 0x20, 0, 0), (14, 0, 1000, 0x20, 0, 0), (1444, 0, 867, 0xA1, 0, 0),
                                (1446, 0, 1000, 0xA1, 0, 0), (214, 0, 500, 0x20, 0, 0), (0, 1, 2000, 0xA0, 0, 0)]), 13)
    assert [anims.client_sequence(bfa, a) for a in (0, 14, 214)] == [0, 1, 4]
    assert anims.client_sequence(bfa, 1444) == anims.NO_SEQUENCE          # past 505: the client plays none
    out, changes = anims.renumber_sequences(bfa, {1444: 213, 1446: 214})
    assert changes == ["1444 -> 213", "1446 -> 214", "214 parked (1446 takes its id)"]
    assert [anims.client_sequence(out, a) for a in (0, 14, 213, 214)] == [0, 1, 2, 3]
    assert anims.own_ids(out) == anims.own_ids(bfa) == {0, 14, 1444, 1446, 214}
    # a rebuild puts every sequence back first: the same model again, or the new ids
    assert anims.renumber_sequences(out, {1444: 213, 1446: 214})[0] == out
    again, _ = anims.renumber_sequences(out, {1444: 215})
    assert [anims.client_sequence(again, a) for a in (215, 214, 0, 14)] == [2, 4, 0, 1]
    assert anims.client_sequence(again, 213) == anims.NO_SEQUENCE
    assert anims.renumber_sequences(again, {})[0][:0x200 + 64 * 6] == bfa[:0x200 + 64 * 6]
