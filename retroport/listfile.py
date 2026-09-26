"""FileDataID <-> path resolution.

wow.export resolves most FileDataIDs to paths during extraction, but some files
are unnamed in the community listfile. This module loads the community
``listfile.csv`` plus an ``overrides.csv`` for manual fixes, and synthesises a
stable placeholder path for anything still unresolved so a conversion can
proceed (and the gap gets logged).

Listfile format is tolerant: ``fdid;path`` or ``fdid,path`` per line.
"""

from __future__ import annotations

import os


class Listfile:
    def __init__(self) -> None:
        self._fwd: dict[int, str] = {}        # fdid -> path
        self._rev: dict[str, int] = {}        # normalised path -> fdid
        self.unresolved: set[int] = set()

    # --- loading ------------------------------------------------------------
    @staticmethod
    def _split(line: str) -> tuple[int, str] | None:
        line = line.strip()
        if not line:
            return None
        sep = ";" if ";" in line else ("," if "," in line else None)
        if sep is None:
            return None
        left, _, right = line.partition(sep)
        try:
            fdid = int(left.strip())
        except ValueError:
            return None
        path = right.strip().replace("/", "\\")
        if not path:
            return None
        return fdid, path

    def load_csv(self, path: str) -> int:
        """Load a listfile/override CSV. Returns the number of entries added."""
        if not path or not os.path.exists(path):
            return 0
        added = 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parsed = self._split(line)
                if parsed is None:
                    continue
                fdid, p = parsed
                self._fwd[fdid] = p
                self._rev[p.lower()] = fdid
                added += 1
        return added

    @classmethod
    def open(cls, listfile: str | None = None, overrides: str | None = None) -> "Listfile":
        lf = cls()
        if listfile:
            lf.load_csv(listfile)
        if overrides:
            lf.load_csv(overrides)  # overrides loaded last so they win
        return lf

    # --- resolution ---------------------------------------------------------
    def resolve(self, fdid: int, *, synthesise: bool = True) -> str | None:
        path = self._fwd.get(fdid)
        if path is not None:
            return path
        self.unresolved.add(fdid)
        if synthesise:
            # Stable, unique placeholder so renames/lookups stay deterministic.
            return f"unknown\\fdid_{fdid}"
        return None

    def reverse(self, path: str) -> int | None:
        return self._rev.get(path.replace("/", "\\").lower())

    def __len__(self) -> int:
        return len(self._fwd)
