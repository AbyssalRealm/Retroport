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
