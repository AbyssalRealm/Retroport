"""Native texture-path baking - fills the "type 0, empty path" gap without M2Mod.

A modern model names its textures by FileDataID (the TXID chunk, one id per
texture slot) instead of a path string. MultiConverter resolves most of them
when its listfile knows the id; any it can't leave a type-0 slot with no path,
which renders untextured in 3.3.5. This stage:

1. reads TXID from the modern model *before* conversion (the chunk is gone after),
2. after conversion, maps each still-empty type-0 slot's id through the
   listfile (+ overrides) to its in-game path - streaming the CSV for just
   those ids, since the community listfile is ~2M rows,
3. applies any explicit per-slot paths you gave (those win),
4. refuses paths that aren't in-game paths, and bakes the rest into the MD20.

Baking and the path rules are adapted from Conjure (MIT, TheDaveCalaz).
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field

from ..formats.md20 import bake_texture_paths, read_texture_slots
from ..formats.verify import m2_problems, no_new_problems, VerifyError
from ..listfile import Listfile
from ..safeio import write_verified

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC = re.compile(r"^(\\\\|//)")


def ingame_path_problem(path: str) -> str:
    """Why ``path`` isn't a valid in-game texture path, or "" if it's fine.

    Catches the most common baking mistake: pasting where the .blp sits on YOUR
    disk (e.g. a wow.export folder) instead of the path it will have in the MPQ."""
    if _DRIVE.match(path):
        return "looks like a location on your computer (a drive path), not an in-game path"
    if _UNC.match(path):
        return "looks like a network/UNC path, not an in-game path"
    if path.startswith("/") or path.startswith("\\"):
        return "starts with a slash - in-game paths are relative, e.g. Creature\\Foo\\foo.blp"
    if "/" in path:
        return "uses forward slashes - WoW in-game paths use backslashes"
    if not path.lower().endswith(".blp"):
        return "does not end in .blp"
    return ""


def read_txid(m2_path: str) -> list[int]:
    """The TXID chunk (texture FileDataIDs, one per slot) of a chunked M2, or []."""
    try:
        with open(m2_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    if data[:4] == b"MD20":
        return []
    pos = 0
    while pos + 8 <= len(data):
        magic = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if magic == b"TXID":
            n = min(size, len(data) - pos - 8) // 4
            return list(struct.unpack_from(f"<{n}I", data, pos + 8))
        pos += 8 + size
    return []


def manifest_texture_files(manifest_path: str | None) -> dict[int, str]:
    """FileDataID -> the file wow.export wrote for it (what you'll pack)."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: dict[int, str] = {}
    for entry in data.get("textures", []) or []:
        if isinstance(entry, dict) and "fileDataID" in entry and "file" in entry:
            out[int(entry["fileDataID"])] = entry["file"]
    return out


@dataclass
class BakePlan:
    paths: dict[int, str] = field(default_factory=dict)          # slot -> in-game path
    fdids: dict[int, int] = field(default_factory=dict)          # slot -> FileDataID (auto-resolved slots)
    unresolved: list[tuple[int, int]] = field(default_factory=list)   # (slot, fdid) the listfile didn't know
    no_id: list[int] = field(default_factory=list)               # empty type-0 slots with no TXID entry


def empty_slot_fdids(md20: bytes, txid: list[int]) -> list[int]:
    """FileDataIDs of the type-0 slots that still have no path (what the listfile must resolve)."""
    return [txid[t.index] for t in read_texture_slots(md20)
            if t.type == 0 and not t.name and t.index < len(txid) and txid[t.index]]


def resolve_fdids(fdids, listfile_path: str | None, overrides_path: str | None = None) -> dict[int, str]:
    """Look up just ``fdids`` in the listfile (+ overrides, which win), streaming the
    CSV instead of loading all ~2M rows into memory."""
    wanted = set(fdids)
    found: dict[int, str] = {}
    for path in (listfile_path, overrides_path):
        if not wanted or not path or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parsed = Listfile._split(line)
                if parsed and parsed[0] in wanted:
                    found[parsed[0]] = parsed[1]
    return found


def plan_bake(md20: bytes, txid: list[int], known: dict[int, str] | None,
              explicit: dict[int, str] | None = None) -> BakePlan:
    """Decide what to bake: listfile paths (``known``: fdid -> path) for empty type-0
    slots, then explicit per-slot paths on top."""
    plan = BakePlan()
    for slot in read_texture_slots(md20):
        if slot.type != 0 or slot.name:
            continue
        fdid = txid[slot.index] if slot.index < len(txid) else 0
        if not fdid:
            plan.no_id.append(slot.index)
            continue
        path = (known or {}).get(fdid)
        if path:
            plan.paths[slot.index] = path
            plan.fdids[slot.index] = fdid
        else:
            plan.unresolved.append((slot.index, fdid))
    for idx, path in (explicit or {}).items():
        plan.paths[int(idx)] = path.strip()
        plan.fdids.pop(int(idx), None)
    plan.unresolved = [(s, f) for s, f in plan.unresolved if s not in plan.paths]
    plan.no_id = [s for s in plan.no_id if s not in plan.paths]
    return plan


def bake_file(m2_path: str, slot_paths: dict[int, str], out_path: str | None = None) -> str:
    """Bake ``slot_paths`` into an MD20 model. Writes ``out_path`` (default: in place)
    atomically, after verifying each slot reads back as baked and nothing else broke.
    Raises ValueError (with every bad path listed) before writing anything."""
    problems = [f"slot {i}: '{p}' - {why}" for i, p in sorted(slot_paths.items())
                for why in [ingame_path_problem(p)] if why]
    if problems:
        raise ValueError("refusing to bake - these don't look like in-game paths:\n  "
                         + "\n  ".join(problems)
                         + "\nBake the path the .blp will have INSIDE the MPQ, e.g. Creature\\Foo\\foo.blp.")
    with open(m2_path, "rb") as fh:
        before = fh.read()
    after = bake_texture_paths(before, slot_paths)
    structure = no_new_problems(before, m2_problems)

    def verify(path: str) -> None:
        structure(path)
        with open(path, "rb") as fh:
            slots = read_texture_slots(fh.read())
        for idx, want in slot_paths.items():
            if slots[idx].type != 0 or slots[idx].name != want:
                raise VerifyError(f"slot {idx} does not read back as baked")

    return write_verified(out_path or m2_path, after, verify)
