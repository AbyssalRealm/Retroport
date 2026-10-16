"""Install a `race` package into a client folder patch (and the server's DBC folder) safely.

Nothing customized is ever overwritten:

* A DBC/UI file the package *modified* (it has a recorded base) is only installed while
  the file the client loads today is still the exact file the package was built on. If
  you changed it since (a new creature in CreatureDisplayInfo, your own CharacterCreate
  edits, ...), the install stops and says so: rebuild the package (`race ... --reuse
  --replace`) and it is rebuilt on your current copy, keeping your changes.
* A race file (model/texture) only replaces a different copy in the race's own folders
  (``Character\\<Race>\\``, its select scene's ``Interface\\Glues\\Models\\UI_<Race>\\``), or
  one of the race's own helmet models (``Item\\ObjectComponents\\Head\\*_<prefix>M.m2``,
  ``F``, and their skins and animations; the prefix is the race's own, e.g. ``Kt``).
* Every file that is replaced, and (with ``clean``) every leftover file in the race's
  folder - or among its helmets, or that an earlier install of the package added and left
  untouched since - that the package doesn't contain, is copied to
  ``<package>\\backups\\<time>\\`` first; a receipt in ``<package>\\installs\\`` lets
  ``uninstall`` put everything back.

Plan first (dry run is the default), then apply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time

from pydantic import BaseModel

from ..mpq import ClientData
from ..safeio import write_verified
from .build import sha256
from .helmets import HEAD


def _helmet_file(prefix: str) -> re.Pattern | None:
    """Matches a client-relative path of one of the race's own helmet files."""
    if not prefix:
        return None
    return re.compile(re.escape(HEAD.lower()) + r"[^\\]+_" + re.escape(prefix.lower())
                      + r"[mf](?:\.m2|\d\d\.skin|\d{4}-\d\d\.anim)$")


def _race_folders(race: str) -> list[str]:
    """The client folders that are the race's own: its character's and its select scene's."""
    return [f"Character\\{race}\\", f"Interface\\Glues\\Models\\UI_{race}\\"]


def _race_file(rel: str, race: str, helmet: re.Pattern | None) -> bool:
    low = rel.lower()
    return any(low.startswith(f.lower()) for f in _race_folders(race)) or bool(helmet and helmet.match(low))


def _owned(rel: str, owns: list[str]) -> bool:
    """A file a glue package owns: one of its files (``owns`` entry) or in one of its folders
    (an entry ending in a backslash)."""
    low = rel.lower()
    return any(low.startswith(o) if o.endswith("\\") else low == o for o in owns)


def _manifest(package: str) -> dict:
    """A race package's race.json, or another package's package.json (``owns``: the client paths
    whose files are its own, in place of a race's folder and helmets)."""
    for name in ("race.json", "package.json"):
        path = os.path.join(package, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    raise ValueError(f"{package} is not a retroport package (no race.json or package.json)")


class Action(BaseModel):
    kind: str            # add | replace | same | stale | conflict | leftover
    rel: str             # package-relative path (client/... or server/dbc/...)
    target: str
    detail: str = ""


class InstallPlan(BaseModel):
    package: str
    race: str
    actions: list[Action] = []
    # package path -> the higher patch the client loads it from instead (installing still puts the
    # package's copy in its patch; the higher one has to be rebuilt on top of it to show it)
    shadowed: dict[str, str] = {}

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


def _is_inside(path: str, parent: str) -> bool:
    path, parent = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(parent))
    return path.startswith(parent.rstrip("\\/") + os.sep)


def _added_earlier(package: str) -> dict[str, str]:
    """Every file an earlier install of this package added (its receipts), with the SHA-256 the
    last install that wrote it left there."""
    added, last = set(), {}
    folder = os.path.join(package, "installs")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                receipt = json.load(fh)
        except (OSError, ValueError):
            continue
        # one receipt may write a path F:/x\y, the next F:\x\y
        for item in receipt.get("added", []):
            added.add(os.path.normpath(item["target"]))
            last[os.path.normpath(item["target"])] = item["sha256"]
        for item in receipt.get("replaced", []):
            last[os.path.normpath(item["target"])] = item["sha256"]
    return {t: last[t] for t in added}


def plan_install(package: str, client_dir: str | None, patch: str = "patch-H.MPQ",
                 server_dbc_dir: str | None = None, clean: bool = False) -> InstallPlan:
    meta = _manifest(package)
    bases: dict[str, dict] = meta.get("bases", {})
    race = meta.get("race", "")
    owned_paths = meta.get("owns", [])
    owns = [o.lower() for o in owned_paths]
    helmet = _helmet_file(meta.get("helmet_prefix", ""))

    def own(rel: str) -> bool:
        # a race's folders and helmets, and the files it lists as its own (its racials' icons)
        return (race and _race_file(rel, race, helmet)) or _owned(rel, owns)

    plan = InstallPlan(package=package, race=race or meta.get("name", ""))
    if client_dir:
        root = os.path.join(client_dir, "Data", patch)
        if not os.path.isdir(root):
            raise ValueError(f"{root} is not a folder patch - install copies files into an unpacked "
                             "(folder) patch such as Data\\patch-H.MPQ")
        client = ClientData(client_dir, top=patch)
        pkg_client = os.path.join(package, "client")
        in_package = set()
        for dirpath, _dirs, files in os.walk(pkg_client):
            for f in files:
                src = os.path.join(dirpath, f)
                rel = os.path.relpath(src, pkg_client)
                in_package.add(os.path.normcase(rel))
                key = "client/" + rel.replace("\\", "/")
                target = os.path.join(root, rel)
                if (higher := client.shadowed_by(rel)) is not None:
                    plan.shadowed[key] = higher
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
                elif key in bases or own(rel):
                    plan.actions.append(Action(kind="replace", rel=key, target=target,
                                               detail="built on this copy" if key in bases else "package file"))
                else:
                    plan.actions.append(Action(kind="conflict", rel=key, target=target,
                                               detail="a different file already exists here"))
        leftovers = []
        folders = [os.path.join(root, *f.rstrip("\\").split("\\")) for f in _race_folders(race)] if race else \
            [os.path.join(root, *o.rstrip("\\").split("\\")) for o in owned_paths if o.endswith("\\")]
        for folder in folders:
            if os.path.isdir(folder):
                for dirpath, _dirs, files in os.walk(folder):
                    leftovers += [os.path.join(dirpath, f) for f in files]
        head_dir = os.path.join(root, *HEAD.rstrip("\\").split("\\"))
        if helmet and os.path.isdir(head_dir):
            leftovers += [os.path.join(head_dir, f) for f in sorted(os.listdir(head_dir))
                          if helmet.match(os.path.join(HEAD, f).lower())]
        # anything an earlier install of this package added, as it left it, that the package no
        # longer holds (the waist textures of earlier builds)
        leftovers += [t for t, sha in _added_earlier(package).items()
                      if _is_inside(t, root) and (data := _read(t)) is not None and sha256(data) == sha]
        seen = set()
        for target in leftovers:
            rel = os.path.relpath(target, root)
            if os.path.normcase(rel) not in in_package and os.path.normcase(rel) not in seen:
                seen.add(os.path.normcase(rel))
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
    stamp = base = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while os.path.exists(os.path.join(plan.package, "installs", f"{stamp}.json")) or \
            os.path.exists(os.path.join(plan.package, "backups", stamp)):
        n += 1                                   # two installs in one second keep both receipts
        stamp = f"{base}-{n}"
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
