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

M2 (12340) sequences: header 0x1C, 64 bytes each: +0 id, +2 variation, +4 duration (ms),
+12 flags, +20/+24 replay min/max.
"""

from __future__ import annotations

import struct

BLEND_FLAGS = 0x81
SHORT = 0.6


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


def one_shot_emotes(emotes: list[list[int]], animation_names: dict[int, str]) -> set[int]:
    """Animations Emotes.dbc plays once (no special proc: not a sit/sleep/kneel state, not a
    looping state) whose AnimationData name starts with "Emote". Emotes (12340): 0 ID, 1 slash
    command, 2 AnimID, 3 flags, 4 EmoteSpecProc."""
    return {r[2] for r in emotes if r[2] and r[4] == 0 and animation_names.get(r[2], "").startswith("Emote")}
