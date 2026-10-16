"""Run the retail backport kit - with a local retail install as its data source, where there is one.

The kit (wow-retail-backport's ``bp.py``) reads retail through one function, ``wago._get(url, dest)``:
a table as wago.tools' CSV, a file by FileDataID, at the project's pinned build, each kept on disk
once fetched. This launcher runs the kit as it is - its files are not touched or copied - with
that function answered from a local install when the request is for the install's own build:

* a table: our DB2 reader's rows written as wago's CSV (``ID`` + WoWDBDefs column names, arrays as
  ``Name_0``, ``Name_1``; checked identical to wago's on ChrRaces, ParticleColor,
  ItemNameDescription and HelmetGeosetData at 12.1.5.70391). wago's tables also carry the
  server's hotfixes, which an install's own tables do not: a table this install has no
  FileDataID for (an older listfile) comes from wago;
* a file: its bytes out of the install; one the install does not list does not exist at that
  build ("missing", no network); one it holds encrypted comes from wago (which serves zeros for
  content Blizzard has not released - the kit's own "encrypted" answer).

Anything else - another build, the community listfile, the build list - goes to the network as
before. A summary of what came from where is printed when the kit finishes. Without ``--retail``
the kit reads wago.tools as it always does.

Either way the kit is shown WarcraftXL's open-folder patches: a FOLDER named like an archive
(``Data\\patch-H.MPQ\\...``, as Equip Plus installs its pieces) is mounted by those clients where a
packed patch of that name would be. A kit whose client reader opens only MPQ files (its ``client``
module has no ``FolderArchive``) is handed our ``FolderArchive`` for those folders, in this process
only - its files are not touched.

usage: python -m retroport.workbench.kitlocal --bp <bp.py> [--retail <install> --dbd <WoWDBDefs\\definitions>
           --listfile <listfile.csv> --product wow --cache <dir>] -- <bp.py arguments>
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import runpy
import sys
import threading
from collections import Counter

DB2_URL = re.compile(r"^https://wago\.tools/db2/([^/?]+)/csv\?build=(.+)$")
FILE_URL = re.compile(r"^https://wago\.tools/api/casc/(\d+)\?download&version=(.+)$")


def flatten(row: dict) -> dict:
    """A DB2 row as wago's CSV columns: an array field ``X`` becomes ``X_0``, ``X_1``, ..."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                out[f"{k}_{i}"] = x
        else:
            out[k] = v
    return out


def table_csv(rows: list[tuple[int, dict]]) -> str:
    """Rows as wago's CSV text: ID first, then the columns in the order the rows have them."""
    flat = [flatten(r) for _i, r in rows]
    columns = ["ID"]
    seen = {"ID"}
    for r in flat[:1] + flat[-1:]:
        for k in r:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for (rid, _r), r in zip(rows, flat):
        w.writerow([rid if c == "ID" else _cell(r.get(c, "")) for c in columns])
    return buf.getvalue()


def _cell(v):
    """wago's float text: 11 decimals, trailing zeros dropped (-1.70000004768, 0.07699999958, 0)."""
    if isinstance(v, float):
        text = f"{v:.11f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text
    return v


class LocalRetail:
    """Answers the kit's requests from a local install (see the module doc)."""

    def __init__(self, source, extra_listfiles: list[str] = ()):
        self.src = source
        self.build = source.build
        self.stats: Counter = Counter()
        self.fallbacks: list[str] = []
        self._lock = threading.Lock()
        self._extra = [p for p in extra_listfiles if p and os.path.isfile(p)]

    def _db2_id(self, name: str) -> int | None:
        self.src.open()
        fdid = self.src._db2.get(name.lower())
        if fdid is None and self._extra:
            from .catalog import db2_ids
            for lf in self._extra:                           # a newer listfile (the kit's own) names more
                self.src._db2.update({k: v for k, v in db2_ids(lf).items() if k not in self.src._db2})
            self._extra = []
            fdid = self.src._db2.get(name.lower())
        return fdid

    def table(self, name: str, dest: str) -> str | None:
        """Write the table's CSV to ``dest``: "ok", or None when this install cannot answer."""
        if self._db2_id(name) is None:
            return None
        try:
            text = table_csv(self.src.table(name))
        except Exception as e:                               # unreadable here: let wago answer
            self._note(f"table {name}: {type(e).__name__}: {e}")
            return None
        _write(dest, text.encode("utf-8"))
        with self._lock:
            self.stats["tables"] += 1
        return "ok"

    def file(self, fdid: int, dest: str) -> str | None:
        """Write the file's bytes to ``dest``: "ok", "missing" (not in this build), or None."""
        casc = self.src.open()
        if fdid not in casc:
            with self._lock:
                self.stats["missing"] += 1
            return "missing"
        try:
            data = self.src.read(fdid)
        except Exception as e:                               # encrypted here, or not stored and no CDN
            self._note(f"file {fdid}: {type(e).__name__}")
            return None
        _write(dest, data)
        with self._lock:
            self.stats["files"] += 1
        return "ok"

    def _note(self, msg: str) -> None:
        with self._lock:
            self.fallbacks.append(msg)

    def wrap(self, original):
        """The kit's ``_get`` answered locally where it can, else ``original``."""
        def _get(url, dest, *args, **kwargs):
            m = DB2_URL.match(url)
            if m and m.group(2) == self.build:
                st = self.table(m.group(1), dest)
                if st:
                    return st
            m = FILE_URL.match(url)
            if m and m.group(2) == self.build:
                st = self.file(int(m.group(1)), dest)
                if st:
                    return st
            with self._lock:
                self.stats["network"] += 1
            return original(url, dest, *args, **kwargs)
        return _get

    def summary(self) -> str:
        s = self.stats
        line = (f"local retail {self.build}: {s['tables']} table(s) and {s['files']} file(s) from "
                f"{self.src.spec.path}; {s['missing']} not in this build; {s['network']} request(s) to the network")
        if self.fallbacks:
            line += "\n  from the network instead: " + "; ".join(self.fallbacks[:12]) + \
                    (f" (+{len(self.fallbacks) - 12} more)" if len(self.fallbacks) > 12 else "")
        return line


class FolderArchive:
    """An open-folder patch read like an MPQ (``locate`` / ``read``, names without case, its
    ``(listfile)`` made from what's in it)."""

    def __init__(self, path: str):
        self.path = path
        self.index = {}
        for d, _dirs, names in os.walk(path):
            for n in names:
                full = os.path.join(d, n)
                rel = os.path.relpath(full, path).replace(os.sep, "\\")
                self.index[rel.lower()] = (rel, full)

    def locate(self, name: str):
        hit = self.index.get(name.replace("/", "\\").lower())
        return hit[1] if hit else None

    def read(self, name: str) -> bytes:
        if name == "(listfile)":
            return "\r\n".join(rel for rel, _full in self.index.values()).encode("latin-1", "replace")
        full = self.locate(name)
        if full is None:
            raise KeyError(name)
        with open(full, "rb") as f:
            return f.read()


def show_folder_patches(client) -> bool:
    """Teach the kit's ``client`` module open-folder patches (see the module doc); False when it
    knows them already."""
    if hasattr(client, "FolderArchive"):
        return False
    real_os, real_reader = client.os, client.MpqReader

    class _Path:
        def __getattr__(self, name):
            return getattr(real_os.path, name)

        @staticmethod
        def isfile(p):
            return real_os.path.isfile(p) or (real_os.path.isdir(p) and str(p).lower().endswith(".mpq"))

    class _Os:
        path = _Path()

        def __getattr__(self, name):
            return getattr(real_os, name)

    client.os = _Os()
    client.MpqReader = lambda p, *a, **k: FolderArchive(p) if real_os.path.isdir(p) else real_reader(p, *a, **k)
    return True


def _write(dest: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = f"{dest}.{threading.get_ident()}.local"
    with open(part, "wb") as fh:
        fh.write(data)
    os.replace(part, dest)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: python -m retroport.workbench.kitlocal --bp <bp.py> --retail <install> ... -- <bp.py args>")
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser(prog="kitlocal")
    ap.add_argument("--bp", required=True)
    ap.add_argument("--retail", default="")
    ap.add_argument("--product", default="wow")
    ap.add_argument("--dbd", default="")
    ap.add_argument("--listfile", default="")
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".retroport", "workbench", "cache"))
    a = ap.parse_args(argv[:cut])
    kit_args = argv[cut + 1:]

    scripts = os.path.dirname(os.path.abspath(a.bp))
    for d in (os.path.join(scripts, "lib"), scripts):
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import client as kit_client                          # the kit's own modules, as they are
        if show_folder_patches(kit_client):
            print("kitlocal: the kit is shown the client's open-folder patches", flush=True)
    except ImportError:
        pass
    local = None
    if a.retail:
        from .catalog import Source, SourceSpec
        src = Source(SourceSpec("retail", "Retail", a.retail, a.product, True), a.dbd, a.listfile, a.cache)
        work = kit_args[kit_args.index("--work") + 1] if "--work" in kit_args else ""
        local = LocalRetail(src, [os.path.join(work, "listfile.csv")] if work else [])
        import wago
        wago._get = local.wrap(wago._get)
        print(f"kitlocal: retail data from {a.retail} (build {local.build}); the kit's other requests go to the network",
              flush=True)
    sys.argv = [a.bp, *kit_args]
    code = 0
    try:
        runpy.run_path(a.bp, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        if local:
            print(local.summary(), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
