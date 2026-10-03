"""Install a `race` package into a client folder patch (and the server's DBC folder) safely.

Nothing customized is ever overwritten:

* A DBC/UI file the package *modified* (it has a recorded base) is only installed while
  the file the client loads today is still the exact file the package was built on. If
  you changed it since (a new creature in CreatureDisplayInfo, your own CharacterCreate
  edits, ...), the install stops and says so: rebuild the package (`race ... --reuse
  --replace`) and it is rebuilt on your current copy, keeping your changes.
* A race file (model/texture) only replaces a different copy in the race's own folder.
* Every file that is replaced, and (with ``clean``) every leftover file in the race's
  folder that the package doesn't contain, is copied to ``<package>\\backups\\<time>\\``
  first; a receipt in ``<package>\\installs\\`` lets ``uninstall`` put everything back.

Plan first (dry run is the default), then apply.
"""

from __future__ import annotations

import json
import os
import shutil
import time

from pydantic import BaseModel

from ..mpq import ClientData
from ..safeio import write_verified
from .build import sha256


class Action(BaseModel):
    kind: str            # add | replace | same | stale | conflict | leftover
    rel: str             # package-relative path (client/... or server/dbc/...)
    target: str
    detail: str = ""


class InstallPlan(BaseModel):
    package: str
    race: str
    actions: list[Action] = []

    @property
    def blocked(self) -> list[Action]:
        return [a for a in self.actions if a.kind in ("stale", "conflict")]

    def count(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            out[a.kind] = out.get(a.kind, 0) + 1
        return out


def _read(path: str) -> bytes | None:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def plan_install(package: str, client_dir: str | None, patch: str = "patch-H.MPQ",
                 server_dbc_dir: str | None = None, clean: bool = False) -> InstallPlan:
    with open(os.path.join(package, "race.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    bases: dict[str, dict] = meta.get("bases", {})
    race = meta["race"]
    plan = InstallPlan(package=package, race=race)
    if client_dir:
        root = os.path.join(client_dir, "Data", patch)
        if not os.path.isdir(root):
            raise ValueError(f"{root} is not a folder patch - install copies files into an unpacked "
                             "(folder) patch such as Data\\patch-H.MPQ")
        client = ClientData(client_dir)
        pkg_client = os.path.join(package, "client")
        in_package = set()
        for dirpath, _dirs, files in os.walk(pkg_client):
            for f in files:
                src = os.path.join(dirpath, f)
                rel = os.path.relpath(src, pkg_client)
                in_package.add(os.path.normcase(rel))
                key = "client/" + rel.replace("\\", "/")
                target = os.path.join(root, rel)
                new, cur = _read(src), _read(target)
                if cur is not None and cur == new:
                    plan.actions.append(Action(kind="same", rel=key, target=target))
                    continue
                if key in bases:
                    hit = client.find(rel)
                    now = sha256(hit[1]) if hit else None
                    if now != bases[key]["sha256"]:
                        where = hit[0] if hit else "nowhere"
                        plan.actions.append(Action(kind="stale", rel=key, target=target, detail=(
                            f"the copy your client loads ({where}) changed since the build - rebuild the "
                            "package so it includes your changes")))
                        continue
                if cur is None:
                    plan.actions.append(Action(kind="add", rel=key, target=target))
                elif cur == new:
                    plan.actions.append(Action(kind="same", rel=key, target=target))
                elif key in bases or rel.lower().startswith(f"character\\{race.lower()}\\"):
                    plan.actions.append(Action(kind="replace", rel=key, target=target,
                                               detail="built on this copy" if key in bases else "race file"))
                else:
                    plan.actions.append(Action(kind="conflict", rel=key, target=target,
                                               detail="a different file already exists here"))
        race_dir = os.path.join(root, "Character", race)
        if os.path.isdir(race_dir):
            for dirpath, _dirs, files in os.walk(race_dir):
                for f in files:
                    target = os.path.join(dirpath, f)
                    rel = os.path.relpath(target, root)
                    if os.path.normcase(rel) not in in_package:
                        plan.actions.append(Action(
                            kind="leftover", rel="client/" + rel.replace("\\", "/"), target=target,
                            detail="moved to the backup" if clean else "not in the package (use --clean to move it out)"))
    if server_dbc_dir:
        pkg_server = os.path.join(package, "server", "dbc")
        for f in sorted(os.listdir(pkg_server)) if os.path.isdir(pkg_server) else []:
            key, target = f"server/dbc/{f}", os.path.join(server_dbc_dir, f)
            new, cur = _read(os.path.join(pkg_server, f)), _read(target)
            base = bases.get(key)
            if cur is not None and cur == new:
                plan.actions.append(Action(kind="same", rel=key, target=target))
            elif base and (cur is None or sha256(cur) != base["sha256"]):
                plan.actions.append(Action(kind="stale", rel=key, target=target,
                                           detail="the server's copy changed since the build - rebuild the package"))
            elif cur is None:
                plan.actions.append(Action(kind="add", rel=key, target=target))
            elif cur == new:
                plan.actions.append(Action(kind="same", rel=key, target=target))
            else:
                plan.actions.append(Action(kind="replace", rel=key, target=target, detail="built on this copy"))
    return plan


def apply_install(plan: InstallPlan, clean: bool = False) -> str:
    """Carry out a plan with no blocking actions; returns the receipt path."""
    if plan.blocked:
        raise ValueError(f"{len(plan.blocked)} file(s) block the install, e.g. {plan.blocked[0].rel}: "
                         f"{plan.blocked[0].detail}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(plan.package, "backups", stamp)
    receipt = {"package": plan.package, "backup": backup, "added": [], "replaced": [], "moved": []}
    for a in plan.actions:
        src = os.path.join(plan.package, *a.rel.split("/"))
        if a.kind in ("replace", "leftover") and (a.kind == "replace" or clean):
            dst = os.path.join(backup, *a.rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(a.target, dst)
            if _read(dst) != _read(a.target):
                raise OSError(f"backup of {a.target} did not verify")
        if a.kind in ("add", "replace"):
            data = _read(src)
            write_verified(a.target, data)
            receipt["added" if a.kind == "add" else "replaced"].append(
                {"target": a.target, "sha256": sha256(data), "backup": None if a.kind == "add"
                 else os.path.join(backup, *a.rel.split("/"))})
        elif a.kind == "leftover" and clean:
            os.remove(a.target)
            receipt["moved"].append({"target": a.target, "backup": os.path.join(backup, *a.rel.split("/"))})
    path = os.path.join(plan.package, "installs", f"{stamp}.json")
    write_verified(path, json.dumps(receipt, indent=1).encode("utf-8"))
    return path


def uninstall(receipt_path: str) -> dict[str, int]:
    """Undo an install: restore replaced/moved files, remove added ones. Refuses if a file
    was changed after the install (it would be lost)."""
    with open(receipt_path, encoding="utf-8") as fh:
        r = json.load(fh)
    for item in r["added"] + r["replaced"]:
        cur = _read(item["target"])
        if cur is not None and sha256(cur) != item["sha256"]:
            raise ValueError(f"{item['target']} changed after the install; not undoing (restore it by hand "
                             f"from {r['backup']})")
    for item in r["replaced"] + r["moved"]:
        write_verified(item["target"], _read(item["backup"]))
    for item in r["added"]:
        if os.path.isfile(item["target"]):
            os.remove(item["target"])
    return {"restored": len(r["replaced"]) + len(r["moved"]), "removed": len(r["added"])}
