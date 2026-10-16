"""The programs the Backport page runs, fetched from their own homes on first use - never bundled.

* the retail backport kit (github.com/Kirazul/wow-retail-backport) carries no license, so it can't
  be passed on with the workbench: each user's workbench downloads it from its author's GitHub;
* Equip Plus (github.com/TheDaveCalaz/wxl-equip-plus, GPL-3) is its author's project: its tools are
  run as programs, fetched the same way.

Each comes at the commit this workbench was made and checked with (``PINNED``), as GitHub's
archive of it, unpacked under ``<data>/deps/<name>/``. The Settings page can point at another
copy instead (the Backport Helper's, a newer clone).
"""

from __future__ import annotations

import io
import os
import shutil
import threading
import zipfile

from . import wago

PINNED = {
    "kit": {"repo": "Kirazul/wow-retail-backport", "ref": "d086009108fec9cc07eab6a80a49f4b8ea8beb20",
            "entry": os.path.join("wow-retail-backport", "scripts", "bp.py"), "label": "the retail backport kit"},
    "equip-plus": {"repo": "TheDaveCalaz/wxl-equip-plus", "ref": "c719e3fccf7453f0958800ad716750945a87b978",
                   "entry": os.path.join("tools", "backport_to_equipplus.py"), "label": "Equip Plus"},
}
_lock = threading.Lock()


def folder(data_dir: str, name: str) -> str:
    return os.path.join(data_dir, "deps", name)


def entry(data_dir: str, name: str) -> str:
    """Where the fetched copy's entry file is (bp.py; Equip Plus's tools/backport_to_equipplus.py)."""
    return os.path.join(folder(data_dir, name), PINNED[name]["entry"])


def have(data_dir: str, name: str) -> bool:
    return os.path.isfile(entry(data_dir, name))


def fetch(data_dir: str, name: str, say=print, get=wago.http_get) -> str:
    """Download and unpack ``name`` at its pinned commit (once); its entry file's path."""
    p = PINNED[name]
    with _lock:
        if have(data_dir, name):
            return entry(data_dir, name)
        url = f"https://codeload.github.com/{p['repo']}/zip/{p['ref']}"
        say(f"Getting {p['label']} from github.com/{p['repo']} ({p['ref'][:7]})")
        tmp = os.path.join(data_dir, "deps", f"{name}.zip")
        get(url, tmp)
        dest, part = folder(data_dir, name), folder(data_dir, name) + ".part"
        shutil.rmtree(part, ignore_errors=True)
        with open(tmp, "rb") as fh:
            unpack(fh.read(), part)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(part, dest)
        os.remove(tmp)
        if not have(data_dir, name):
            raise RuntimeError(f"{p['label']}: the download has no {p['entry']}")
        say(f"   {p['label']} is in {dest}")
        return entry(data_dir, name)


def unpack(data: bytes, dest: str) -> None:
    """A GitHub archive unpacked into ``dest`` without its top folder (``repo-<sha>/``); nothing
    outside ``dest`` is written."""
    root = os.path.realpath(dest)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            parts = info.filename.replace("\\", "/").split("/")[1:]
            if not parts or not parts[-1] and len(parts) == 1:
                continue
            target = os.path.realpath(os.path.join(dest, *parts))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError(f"the archive names a path outside its folder: {info.filename}")
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
