"""Every item and display id the workbench's projects ever gave a look - so none is given twice.

The kit numbers a project's items from 900000 and its displays above the client's highest, skipping
the ids its scanned client tables hold. A look a project's patch no longer holds - taken out, or
installed by another project whose patch is gone - leaves no row there, and the kit would give its
numbers to another look; but the server still has the old item row, and players still carry it.
So the workbench keeps every project's ``ids.json`` here (``<data>/item-ids.json``):

* ``reserved`` - every id any project gave: a project's next look never gets one of another's;
* ``item`` / ``display`` - the ids each retail display was first given: a look that comes back
  (put in the basket again, or into another project) gets them again when they are free, so the
  items players carry show again.

``kitlocal`` applies it to the kit's ``tables`` step (``--ids``); the workbench adds the project's
ids after each install (``remember``).
"""

from __future__ import annotations

import json
import os

NAME = "item-ids.json"


def path(data_dir: str) -> str:
    return os.path.join(data_dir, NAME)


def load(file: str) -> dict:
    try:
        with open(file, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        got = {}
    got = got if isinstance(got, dict) else {}
    for k in ("item", "display"):
        got[k] = {str(d): int(v) for d, v in (got.get(k) or {}).items()}
    res = got.get("reserved") or {}
    got["reserved"] = {k: sorted({int(x) for x in res.get(k) or []}) for k in ("item", "display")}
    got["projects"] = list(got.get("projects") or [])
    return got


def remember(file: str, ids: dict, project: str = "") -> bool:
    """A project's ``ids.json`` added: its ids reserved, the first ids of each look kept. True when
    anything was new."""
    reg = load(file)
    before = json.dumps(reg, sort_keys=True)
    for k in ("item", "display"):
        mine = {str(d): int(v) for d, v in ((ids or {}).get(k) or {}).items()}
        for d, v in mine.items():
            reg[k].setdefault(d, v)
        reg["reserved"][k] = sorted(set(reg["reserved"][k]) | set(mine.values()))
    if project and project not in reg["projects"]:
        reg["projects"].append(project)
    if json.dumps(reg, sort_keys=True) == before:
        return False
    os.makedirs(os.path.dirname(file) or ".", exist_ok=True)
    with open(file + ".part", "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=1, sort_keys=True)
    os.replace(file + ".part", file)
    return True


def give_back(reg: dict, ids: dict, looks, base_items: set, base_displays: set) -> int:
    """``ids`` (a project's ``ids.json``) given the registry's ids for ``looks`` it has none for, where
    no other look of the project and no row of the client's tables has them. How many it got."""
    got = 0
    for kind, base in (("item", base_items), ("display", base_displays)):
        mine = ids.setdefault(kind, {})
        used = {int(v) for v in mine.values()}
        for d in looks:
            d = str(d)
            v = reg[kind].get(d)
            if d in mine or v is None or v in used or v in base:
                continue
            mine[d] = v
            used.add(v)
            got += 1
    return got


def reserved_for(reg: dict, ids: dict) -> dict:
    """The ids a project may not give a new look: every project's but its own."""
    return {k: set(reg["reserved"][k]) - {int(v) for v in ((ids or {}).get(k) or {}).values()}
            for k in ("item", "display")}
