"""Exporter.

Phase 0 ships the loose-file path (a ``Data/`` patch folder) since that is the
fast-iteration workflow you already use. MPQ packing is a Phase 1 add-on via the
MPQ Editor CLI or a StormLib binding.
"""

from __future__ import annotations

import os
import shutil

from .config import Config, OutputMode
from .models import ConversionResult


def export_loose(results: list[ConversionResult], cfg: Config) -> list[str]:
    """Copy successful, non-quarantined outputs into the output folder,
    preserving the in-game relative path. Returns the written paths."""
    root = os.path.join(cfg.output_dir, "Data") if cfg.output_mode is OutputMode.LOOSE else cfg.output_dir
    written: list[str] = []
    for r in results:
        if not r.success or r.quarantined or not r.output:
            continue
        # Preserve relative path under the wow.export input root where possible.
        rel = os.path.relpath(r.output, start=cfg.input_dir)
        if rel.startswith(".."):
            rel = os.path.basename(r.output)
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(r.output, dest)
        written.append(dest)
    return written


def export(results: list[ConversionResult], cfg: Config) -> list[str]:
    if cfg.output_mode is OutputMode.MPQ:
        # TODO(phase1): stage to a temp tree then drive MPQ Editor / StormLib.
        raise NotImplementedError("MPQ export arrives in Phase 1; use OutputMode.LOOSE for now.")
    return export_loose(results, cfg)
