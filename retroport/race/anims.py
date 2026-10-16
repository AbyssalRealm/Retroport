"""Make a ported race's animations play like the look template's in 3.3.5.

* Blending. 3.3.5 eases from one animation into the next only when a sequence has flag 0x80
  (0x01 is its load-time twin). BfA blends every transition on per-sequence blend times, and the
  converter carries BfA's flags over: a Kul Tiran's /cry, /chicken (which /taunt plays too), /shy
  and /dance had neither, so they snapped back to standing. Each sequence takes those two bits
  from the template's sequence of the same animation.
* Length. BfA plays some emotes as a short cycle it repeats for as long as the emote lasts; 3.3.5
  plays a one-shot sequence ``replay`` times. The Kul Tiran male's /cry is a 0.67 s sob that 3.3.5
  played once (Human's lasts 4.3 s). A one-shot emote animation - an Emotes row without a special
  proc, an AnimationData name starting "Emote" - shorter than 60% of the template's is repeated
  to match it. Combat and movement animations are never repeated.
* Ids. 3.3.5 plays no animation id past 505, whatever AnimationData holds: CM2Model refuses ids
  from 506 on (0x826050, 0x825F40) and the unit stands. An animation BfA added (Haymaker, 1444)
  takes one of 3.3.5's spare spell ids its model leaves free (visuals.CUSTOM_SPELL):
  ``renumber_sequences`` moves the model's sequences there.

M2 (12340) sequences: header 0x1C, 64 bytes each: +0 id, +2 variation, +4 duration (ms),
+12 flags, +18 padding, +20/+24 replay min/max. Sequence lookup table: header 0x24.
"""

from __future__ import annotations

import struct

BLEND_FLAGS = 0x81
SHORT = 0.6
PAD = 0x12                      # a sequence's unused uint16: the BfA id of one renumber_sequences moved
PARKED = 0xFFFE                 # where a BfA animation goes when its id is taken: nothing plays it
NO_SEQUENCE = 0xFFFF            # an empty lookup slot
MAX_ANIM = 506                  # CM2Model plays no animation id from here on (3.3.5's AnimationData: 0-505)


def sequences(m2: bytes) -> list[tuple[int, int, int, int, int, int, int]]:
    """(record offset, animation id, variation, duration, flags, replay min, replay max) per sequence."""
    n, off = struct.unpack_from("<II", m2, 0x1C)
    out = []
    for i in range(n):
        rec = off + i * 64
        aid, var, dur = struct.unpack_from("<HHI", m2, rec)
        flags = struct.unpack_from("<I", m2, rec + 12)[0]
        rmin, rmax = struct.unpack_from("<II", m2, rec + 20)
        out.append((rec, aid, var, dur, flags, rmin, rmax))
    return out


def align_sequences(m2: bytes, template: bytes, one_shot: set[int]) -> tuple[bytes, list[str]]:
    """``m2`` with the template's blend bits and, for short one-shot emotes, repetitions.
    Returns (model, one line per change)."""
    tmpl: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for _rec, aid, var, dur, flags, rmin, rmax in sequences(template):
        tmpl.setdefault((aid, var), (dur, flags, rmin, rmax))
    out = bytearray(m2)
    changes = []
    for rec, aid, var, dur, flags, rmin, rmax in sequences(m2):
        t = tmpl.get((aid, var)) or tmpl.get((aid, 0))
        if t is None:
            continue
        t_dur, t_flags, t_min, t_max = t
        want = flags | (t_flags & BLEND_FLAGS)
        if want != flags:
            struct.pack_into("<I", out, rec + 12, want)
            changes.append(f"{aid}.{var} blends ({flags:#x} -> {want:#x})")
        if aid in one_shot and dur:
            ours = dur * max(1, rmax)
            theirs = t_dur * max(1, (t_min + t_max) / 2)
            if ours < SHORT * theirs:
                n = max(2, round(theirs / dur))
                struct.pack_into("<II", out, rec + 20, n, n)
                changes.append(f"{aid}.{var} repeats x{n} ({dur} ms -> {dur * n} ms; template {round(theirs)} ms)")
    return bytes(out), changes


def client_sequence(m2: bytes, anim: int) -> int:
    """The sequence the client plays for an animation id (CM2Model, 0x8260C0): with a lookup table,
    the entry at id % size, probing on (i * i + last slot) % size past other ids; with none, the
    first sequence of that id - and none for an id past ``MAX_ANIM``. NO_SEQUENCE: none."""
    if anim >= MAX_ANIM:
        return NO_SEQUENCE
    seqs = sequences(m2)
    n, off = struct.unpack_from("<II", m2, 0x24)
    if not n:
        return next((i for i, s in enumerate(seqs) if s[1] == anim), NO_SEQUENCE)
    slot, probe = anim % n, 0
    while (index := struct.unpack_from("<H", m2, off + 2 * slot)[0]) != NO_SEQUENCE and seqs[index][1] != anim:
        probe += 1
        if probe > n:
            return NO_SEQUENCE
        slot = (probe * probe + slot) % n
    return index


def own_ids(m2: bytes) -> set[int]:
    """The model's animation ids as BfA's (a sequence ``renumber_sequences`` moved counts as BfA's
    own id), so a rebuild sees the slots free that its earlier build took."""
    return {struct.unpack_from("<H", m2, rec + PAD)[0] or aid for rec, aid, *_rest in sequences(m2)}


def renumber_sequences(m2: bytes, ids: dict[int, int]) -> tuple[bytes, list[str]]:
    """``m2`` with the sequences of each BfA animation id in ``ids`` under its 3.3.5 id, and the
    lookup table finding them there. A BfA animation already at one of those ids is parked
    (``PARKED``). Each moved sequence keeps its BfA id in its padding, so a rebuild first puts
    every sequence back and the result depends only on ``ids``. Returns (model, changes)."""
    out = bytearray(m2)
    for rec, *_rest in sequences(m2):
        bfa = struct.unpack_from("<H", out, rec + PAD)[0]
        if bfa:
            struct.pack_into("<H", out, rec, bfa)
            struct.pack_into("<H", out, rec + PAD, 0)
    taken = {new: bfa for bfa, new in ids.items()}
    changes = []
    for rec, aid, *_rest in sequences(bytes(out)):
        new = ids.get(aid, PARKED if aid in taken else None)
        if new is None or new == aid:
            continue
        struct.pack_into("<H", out, rec, new)
        struct.pack_into("<H", out, rec + PAD, aid)
        changes.append(f"{aid} -> {new}" if new != PARKED else f"{aid} parked ({taken[aid]} takes its id)")
    n, off = struct.unpack_from("<II", out, 0x24)
    seqs = sequences(bytes(out))
    for new in sorted(set(ids.values())):
        first = next((i for i, s in enumerate(seqs) if s[1] == new and s[2] == 0), None)
        if first is None or not n:
            continue
        slot, probe = new % n, 0
        while (index := struct.unpack_from("<H", out, off + 2 * slot)[0]) != NO_SEQUENCE and seqs[index][1] != new:
            probe += 1
            if probe > n:
                raise ValueError(f"the sequence lookup table has no free slot for animation {new}")
            slot = (probe * probe + slot) % n
        struct.pack_into("<H", out, off + 2 * slot, first)
    return bytes(out), changes


def one_shot_emotes(emotes: list[list[int]], animation_names: dict[int, str]) -> set[int]:
    """Animations Emotes.dbc plays once (no special proc: not a sit/sleep/kneel state, not a
    looping state) whose AnimationData name starts with "Emote". Emotes (12340): 0 ID, 1 slash
    command, 2 AnimID, 3 flags, 4 EmoteSpecProc."""
    return {r[2] for r in emotes if r[2] and r[4] == 0 and animation_names.get(r[2], "").startswith("Emote")}
