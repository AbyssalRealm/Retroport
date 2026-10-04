"""WotLK-compatibility binary fixups on converted models/skins.

These operate on files on disk and are pure-Python (no external tools):

* ``clamp_skin_submeshes`` - drop skin submeshes (and the batches that use them)
  down to WotLK's ceiling (default 64). Modern models carry far more geoset groups than
  WotLK handles; the extras overflow and render wrong. (Concept from RaceBuilder's
  clamp_skin_submeshes; validate against that proven version on real files.) RaceBuilder used 59; the
  3.3.5 HD patch's HumanMale ships 61-63 submeshes, so the default is 64.
* ``clear_stale_anim_flags`` - after full re-inlining there are no .anim files, so
  the header's chunked-anim flags (0x2000 / 0x200000) are stale; clear them.
* ``set_num_skin_profiles`` - set the M2's nViews to match the skins you shipped.
* ``repair_combiner_header`` - restore model data MultiConverter overwrote with the
  combiner-combos header field (the first array after the header: global loops or sequences).
* ``clear_combiner_combos`` - clear a combiner-combos table WotLK can't use (black-mesh fix).

Every write goes through ``safeio.write_verified``: the edited bytes land in a
temp file, are checked for structural problems the input didn't already have,
and only then replace the file. A rejected edit leaves the file untouched.
"""

from __future__ import annotations

import os
import struct

from ..formats import structs as S
from ..formats.verify import combiner_combos_problem, m2_data_block, m2_problems, no_new_problems, skin_problems
from ..safeio import write_verified


def _save_m2(path: str, before: bytes, after: bytes) -> None:
    write_verified(path, after, no_new_problems(before, m2_problems))


def _save_skin(path: str, before: bytes, after: bytes) -> None:
    write_verified(path, after, no_new_problems(before, skin_problems))


def _data_block_bounds(data: bytes) -> tuple[int, int]:
    """Return (start, length) of the MD20 data within the file (handles MD21)."""
    if data[:4] == b"MD20":
        return 0, len(data)
    if data[:4] == b"MD21":
        (size,) = struct.unpack_from("<I", data, 4)
        return 8, size
    raise ValueError("not an M2 (no MD20/MD21)")


def clear_stale_anim_flags(m2_path: str) -> bool:
    """Clear the chunked/upgraded .anim flags in global_flags. Returns True if changed."""
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    start, _ = _data_block_bounds(data)
    off = start + S.HDR_GLOBAL_FLAGS
    flags = struct.unpack_from("<I", data, off)[0]
    new = flags & ~(S.FLAG_CHUNKED_ANIM_0x2000 | S.FLAG_UPGRADED_ANIM_0x200000)
    if new == flags:
        return False
    before = bytes(data)
    struct.pack_into("<I", data, off, new)
    _save_m2(m2_path, before, bytes(data))
    return True


def set_num_skin_profiles(m2_path: str, n: int) -> None:
    """Set the M2 header's nViews (skin profile count)."""
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    before = bytes(data)
    start, _ = _data_block_bounds(data)
    struct.pack_into("<I", data, start + S.HDR_NUM_SKIN_PROFILES, n)
    _save_m2(m2_path, before, bytes(data))


def clamp_skin_submeshes(skin_path: str, max_submeshes: int = 64) -> int:
    """Truncate a .skin to at most ``max_submeshes`` submeshes and drop batches
    that reference the removed ones. Returns the new submesh count.

    Submeshes are ordered by geoset id (body first, Shadowlands extras last), so
    keeping the first N retains the core mesh and drops the modern-only geosets.
    """
    with open(skin_path, "rb") as fh:
        data = bytearray(fh.read())
    if data[:4] != S.SKIN_MAGIC:
        raise ValueError("not a SKIN file")

    sub_cnt, _sub_off = S.read_m2array(data, S.SKIN_SUBMESHES)
    if sub_cnt <= max_submeshes:
        return sub_cnt
    before = bytes(data)

    # keep the first N submeshes
    S.write_m2array(data, S.SKIN_SUBMESHES, max_submeshes, _sub_off)

    # filter batches to those referencing a kept submesh
    bat_cnt, bat_off = S.read_m2array(data, S.SKIN_BATCHES)
    kept = bytearray()
    kept_n = 0
    for i in range(bat_cnt):
        rec = bat_off + i * S.M2BATCH
        if rec + S.M2BATCH > len(data):
            break
        sub_index = struct.unpack_from("<H", data, rec + 0x04)[0]  # skinSectionIndex
        if sub_index < max_submeshes:
            kept += data[rec:rec + S.M2BATCH]
            kept_n += 1
    # overwrite the batch array in place with the kept subset (shorter), fix count
    data[bat_off:bat_off + len(kept)] = kept
    S.write_m2array(data, S.SKIN_BATCHES, kept_n, bat_off)

    _save_skin(skin_path, before, bytes(data))
    return max_submeshes


CHARACTER_UNFILLED_TYPES = frozenset({11, 12, 13})   # creature skins: never filled on a player model


def strip_unfilled_texture_layers(m2_path: str, skin_paths: list[str],
                                  unfilled_types: frozenset[int] = CHARACTER_UNFILLED_TYPES) -> int:
    """Drop trailing texture layers that sample a replaceable texture nothing will fill.

    BfA characters draw the guild-emblem cloak (geoset 1507) as cape texture + emblem
    layer (types 16-18, which 3.3.5 lacks; the conversion leaves an empty type-11 slot).
    An empty second layer is drawn wrong, so the batch keeps its first layer only - the
    same repair as the released Mag'har port. Returns the number of batches changed."""
    with open(m2_path, "rb") as fh:
        m2 = fh.read()
    n_tex, tex_off = S.read_m2array(m2, S.HDR_TEXTURES)
    unfilled = set()
    for i in range(n_tex):
        typ, _flags, name_len, _name_off = struct.unpack_from("<IIII", m2, tex_off + i * 16)
        if typ in unfilled_types and name_len <= 1:
            unfilled.add(i)
    if not unfilled:
        return 0
    n_lk, lk_off = S.read_m2array(m2, S.HDR_TEXTURE_LOOKUP)
    lookup = struct.unpack_from(f"<{n_lk}h", m2, lk_off) if n_lk else ()
    changed = 0
    for sp in skin_paths:
        with open(sp, "rb") as fh:
            data = bytearray(fh.read())
        before = bytes(data)
        bat_cnt, bat_off = S.read_m2array(data, S.SKIN_BATCHES)
        n = 0
        for i in range(bat_cnt):
            rec = bat_off + i * S.M2BATCH
            count, combo = struct.unpack_from("<HH", data, rec + 0x0E)
            keep = count
            while keep > 1 and combo + keep - 1 < len(lookup) and lookup[combo + keep - 1] in unfilled:
                keep -= 1
            if keep != count:
                struct.pack_into("<H", data, rec + 0x0E, keep)
                n += 1
        if n:
            _save_skin(sp, before, bytes(data))
            changed += n
    return changed


def remap_geosets(skin_paths: list[str], mapping: dict[int, int | None]) -> dict[int, int]:
    """Renumber submeshes (geoset ids) per ``mapping``; ``None`` hides that geoset by dropping
    the batches that draw it (the submesh stays, so nothing else shifts). Returns
    {geoset id: submeshes changed} for the first skin."""
    report: dict[int, int] = {}
    for n, sp in enumerate(skin_paths):
        with open(sp, "rb") as fh:
            data = bytearray(fh.read())
        before = bytes(data)
        sub_cnt, sub_off = S.read_m2array(data, S.SKIN_SUBMESHES)
        hidden = set()
        for i in range(sub_cnt):
            rec = sub_off + i * 48
            sid = struct.unpack_from("<H", data, rec)[0]
            if sid in mapping:
                if n == 0:
                    report[sid] = report.get(sid, 0) + 1
                if mapping[sid] is None:
                    hidden.add(i)
                else:
                    struct.pack_into("<H", data, rec, mapping[sid])
        if hidden:
            bat_cnt, bat_off = S.read_m2array(data, S.SKIN_BATCHES)
            kept = bytearray()
            for i in range(bat_cnt):
                rec = data[bat_off + i * S.M2BATCH:bat_off + (i + 1) * S.M2BATCH]
                if struct.unpack_from("<H", rec, 0x04)[0] not in hidden:
                    kept += rec
            data[bat_off:bat_off + len(kept)] = kept
            S.write_m2array(data, S.SKIN_BATCHES, len(kept) // S.M2BATCH, bat_off)
        if bytes(data) != before:
            _save_skin(sp, before, bytes(data))
    return report


SKIN_U16 = 0xFFFF


def order_skin_indices(skin_paths: list[str]) -> list[dict]:
    """Lay each skin's index buffer out switchable geosets first, the always-drawn body (id 0)
    next and undrawn submeshes (no batch) last, so as much switchable geometry as fits starts
    below index 65,536. A skin with more indices keeps the high 16 bits of a submesh's start in
    its ``level``, and 3.3.5 matches geosets on ``(level << 16) | id``: a switchable submesh past
    the line can never be hidden (wxl-wide-skin-geosets fixes the client; this keeps clients
    without it right as far as the geometry allows). Returns per skin {indices, switchable,
    past_line: switchable geoset ids that still start past it}; a skin whose submeshes share or
    skip index ranges is left alone."""
    reports = []
    for sp in skin_paths:
        with open(sp, "rb") as fh:
            data = bytearray(fh.read())
        before = bytes(data)
        n_t, t_off = S.read_m2array(data, S.SKIN_INDICES)
        n_s, s_off = S.read_m2array(data, S.SKIN_SUBMESHES)
        b_cnt, b_off = S.read_m2array(data, S.SKIN_BATCHES)
        drawn = {struct.unpack_from("<H", data, b_off + i * S.M2BATCH + 0x04)[0] for i in range(b_cnt)}
        subs = []
        for i in range(n_s):
            sid, level, _vs, _vc, start, count = struct.unpack_from("<HHHHHH", data, s_off + i * 48)
            subs.append((i, sid, (level << 16) | start, count))
        report = {"skin": os.path.basename(sp), "indices": n_t, "reordered": False,
                  "switchable": sum(c for i, sid, _f, c in subs if sid and i in drawn), "past_line": []}
        spans = sorted((f, f + c) for _i, _sid, f, c in subs if c)
        # submeshes tile the buffer from 0; a tail nothing points at (submeshes clamp_skin_submeshes
        # dropped) is kept as it is, after them
        contiguous = bool(spans) and spans[0][0] == 0 and spans[-1][1] <= n_t \
            and all(a[1] == b[0] for a, b in zip(spans, spans[1:]))
        if n_t > SKIN_U16 and contiguous:
            indices = struct.unpack_from(f"<{n_t}H", data, t_off)
            order = sorted(subs, key=lambda s: (0 if s[1] and s[0] in drawn else 1 if s[0] in drawn else 2, s[0]))
            out, starts = [], {}
            for i, _sid, first, count in order:
                starts[i] = len(out)
                out.extend(indices[first:first + count])
            out.extend(indices[spans[-1][1]:])
            struct.pack_into(f"<{n_t}H", data, t_off, *out)
            report["reordered"] = True
            for i, _sid, _first, _count in subs:
                struct.pack_into("<H", data, s_off + i * 48 + 2, starts[i] >> 16)       # level
                struct.pack_into("<H", data, s_off + i * 48 + 8, starts[i] & SKIN_U16)  # indexStart
            subs = [(i, sid, starts[i], c) for i, sid, _f, c in subs]
        report["past_line"] = sorted({sid for i, sid, f, c in subs if sid and i in drawn and f > SKIN_U16})
        if bytes(data) != before:
            _save_skin(sp, before, bytes(data))
        reports.append(report)
    return reports


def repair_combiner_header(m2_path: str, original: bytes) -> list[str]:
    """Restore model data that MultiConverter overwrote with the combiner-combos header field.

    When it adds combos to a model that had none, MultiConverter sets global flag 0x08 and
    writes the combos M2Array at 0x130 without moving the data that starts there: the first
    array after the header. On real models (a 2H axe, hozumonkey) that was the global loops'
    timestamps; when a model has none it is the sequence table, whose first 8 bytes are
    sequence 0's id, variation and duration (normally Stand). ``original`` is the model as
    MultiConverter received it (MD21 or MD20).

    Each array starting inside 0x130-0x137 is restored from it: moved to the end of the file
    while flag 0x08 is set, so the field keeps the combos, or rewritten in place on a model
    whose flag was already cleared. Returns the repaired array names ([] for an unconverted
    model or nothing to repair). Raises ValueError, leaving the file as it was, if the
    original doesn't hold the same array - nothing is guessed.
    """
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    if data[:4] != b"MD20":
        return []
    orig = m2_data_block(original)
    before = bytes(data)
    combos = bool(S.read_u32(data, S.HDR_GLOBAL_FLAGS) & S.FLAG_USE_COMBINER_COMBOS)
    field_bytes = slice(S.MD20_HEADER_SIZE, S.MD20_HEADER_SIZE_COMBOS)
    repaired: list[str] = []
    for name, field, elem in S.WOTLK_HEADER_ARRAYS:
        cnt, off = S.read_m2array(data, field)
        if not cnt or not S.MD20_HEADER_SIZE <= off < S.MD20_HEADER_SIZE_COMBOS:
            continue
        end = off + cnt * elem
        if data[field_bytes] == orig[field_bytes]:
            if combos:
                raise ValueError(f"{name}: the original has the same bytes at 0x130, so it can't restore them "
                                 "(pass the model MultiConverter was given, not its output)")
            continue                    # flag clear and nothing overwritten
        if (S.read_m2array(orig, field) != (cnt, off) or end > len(orig) or end > len(data)
                or data[S.MD20_HEADER_SIZE_COMBOS:end] != orig[S.MD20_HEADER_SIZE_COMBOS:end]):
            raise ValueError(f"{name}: the original model doesn't hold the same data, so it can't be restored "
                             "(for a .skel model, re-run the pipeline instead)")
        if combos:
            data += b"\0" * (-len(data) % 16)
            S.write_m2array(data, field, cnt, len(data))
            data += orig[off:end]
        else:
            data[off:end] = orig[off:end]
        repaired.append(name)
    if repaired:
        _save_m2(m2_path, before, bytes(data))
    return repaired


def _read_skins(skin_paths: list[str]) -> list[bytes]:
    out: list[bytes] = []
    for sp in skin_paths:
        try:
            with open(sp, "rb") as fh:
                out.append(fh.read())
        except OSError:
            continue
    return out


def combiner_status(m2_path: str, skin_paths: list[str]) -> str | None:
    """None if the model doesn't set flag 0x08, "" if its combiner-combos table is usable
    on WotLK, otherwise the reason it isn't."""
    with open(m2_path, "rb") as fh:
        data = fh.read()
    if not S.read_u32(m2_data_block(data), S.HDR_GLOBAL_FLAGS) & S.FLAG_USE_COMBINER_COMBOS:
        return None
    return combiner_combos_problem(data, _read_skins(skin_paths)) or ""


def clear_combiner_combos(m2_path: str, skin_paths: list[str], keep_valid: bool = True) -> tuple[bool, int]:
    """Clear the texture-combiner-combos global flag (0x08) and normalise batch
    shaders - the fix for models rendering black on WotLK, where M2Batch.shader is
    read as an index into a combiner-combos table that doesn't fit.

    With ``keep_valid`` a usable table (see ``verify.combiner_combos_problem``) is left
    alone, flag and shaders both: WotLK renders it like Blizzard's own models, and clearing
    it would drop texture blending such as env-mapped reflections.

    Shaders are normalised in place: 0x8000 ("do not render") is preserved, and any
    other non-zero value (a combo index that is meaningless on WotLK) is reset to 0,
    the default shader. Returns (flag_was_cleared, shaders_changed).
    """
    with open(m2_path, "rb") as fh:
        data = bytearray(fh.read())
    start, _ = _data_block_bounds(data)
    off = start + S.HDR_GLOBAL_FLAGS
    flags = struct.unpack_from("<I", data, off)[0]
    if (keep_valid and flags & S.FLAG_USE_COMBINER_COMBOS
            and combiner_combos_problem(bytes(data), _read_skins(skin_paths)) is None):
        return False, 0
    flag_cleared = bool(flags & S.FLAG_USE_COMBINER_COMBOS)
    if flag_cleared:
        before = bytes(data)
        struct.pack_into("<I", data, off, flags & ~S.FLAG_USE_COMBINER_COMBOS)
        _save_m2(m2_path, before, bytes(data))

    changed = 0
    for sp in skin_paths:
        try:
            with open(sp, "rb") as fh:
                sdata = bytearray(fh.read())
        except OSError:
            continue
        if sdata[:4] != S.SKIN_MAGIC:
            continue
        s_before = bytes(sdata)
        b_cnt, b_off = S.read_m2array(sdata, S.SKIN_BATCHES)
        dirty = False
        for i in range(b_cnt):
            rec = b_off + i * S.M2BATCH
            if rec + S.M2BATCH > len(sdata):
                break
            sh = struct.unpack_from("<H", sdata, rec + S.BATCH_SHADER)[0]
            if sh & 0x8000:
                new = 0x8000            # preserve "do not render"
            elif sh != 0:
                new = 0                 # combo index / invalid -> default WotLK shader
            else:
                new = 0
            if new != sh:
                struct.pack_into("<H", sdata, rec + S.BATCH_SHADER, new)
                changed += 1
                dirty = True
        if dirty:
            _save_skin(sp, s_before, bytes(sdata))
    return flag_cleared, changed
