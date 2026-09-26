"""Loaders that resolve unnamed files by FileDataID.

wow.export exports a model's dependencies (.skel, .anim, .bone, .skin) and writes
a ``<name>.manifest.json`` sidecar mapping each FileDataID to the file it wrote -
and it names those files by their in-game name, not by id. So the robust resolver
reads that manifest; the directory scanner is the fallback for exports named by id.

A loader is just ``Callable[[int], bytes | None]`` so it is trivial to swap for a
CASC-backed or in-memory implementation when wiring into Warcrafter.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Callable

# manifest sections whose entries each have {fileDataID, file}
_MANIFEST_SECTIONS = ("skelAnims", "skelBones", "skins", "lodSkins", "textures")


def _read_file(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def find_manifest(model_path: str) -> str | None:
    """Locate the wow.export manifest sidecar next to a model file."""
    base = os.path.splitext(model_path)[0]
    cand = base + ".manifest.json"
    if os.path.isfile(cand):
        return cand
    folder = os.path.dirname(model_path) or "."
    matches = glob.glob(os.path.join(folder, "*.manifest.json"))
    stem = os.path.basename(base).lower()
    for m in matches:                       # prefer a stem match
        if os.path.basename(m).lower().startswith(stem):
            return m
    return matches[0] if matches else None


def make_manifest_loader(manifest_path: str) -> Callable[[int], bytes | None]:
    """Build a loader from a wow.export manifest: FileDataID -> that file's bytes.

    Paths in the manifest are relative to the manifest's own directory.
    """
    index: dict[int, str] = {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    base = os.path.dirname(os.path.abspath(manifest_path))

    def add(entry) -> None:
        if isinstance(entry, dict) and "fileDataID" in entry and "file" in entry:
            index[int(entry["fileDataID"])] = os.path.join(base, entry["file"])

    add(data.get("skeleton"))
    for section in _MANIFEST_SECTIONS:
        for entry in data.get(section, []):
            add(entry)

    def loader(file_id: int) -> bytes | None:
        path = index.get(file_id)
        return _read_file(path) if path else None

    loader.index = index            # type: ignore[attr-defined]
    loader.manifest = data          # type: ignore[attr-defined]
    return loader


def make_file_id_loader(root: str, ext: str) -> Callable[[int], bytes | None]:
    """Index ``root`` recursively for files named ``<id><ext>`` and return a
    loader that maps a FileDataID to that file's bytes (or None)."""
    ext = ext if ext.startswith(".") else "." + ext
    index: dict[int, str] = {}
    if root and os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                stem, e = os.path.splitext(name)
                if e.lower() == ext.lower() and stem.isdigit():
                    index.setdefault(int(stem), os.path.join(dirpath, name))

    def loader(file_id: int) -> bytes | None:
        path = index.get(file_id)
        return _read_file(path) if path else None

    loader.index = index            # type: ignore[attr-defined]
    return loader
