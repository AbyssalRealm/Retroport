"""BLP recompression stage (Phase 1).

This is the integration point for your existing ``blp_hd_patch.py`` - do NOT
reimplement the BLP codec here. The retroport-specific job is narrow:

    decode -> ensure power-of-two -> re-encode DXT1 (no alpha) / DXT5 (alpha)
    -> regenerate the full mip chain -> reject BC4/BC5/BC7 inputs.

The function below defines the contract the pipeline expects. Wire the body to
``blp_hd_patch`` (or texconv.exe / BLPConverter via subprocess) and validate the
output before returning success.
"""

from __future__ import annotations

import os

from ..config import Config
from ..models import AssetInfo, ConversionResult
from ..validate.rules import validate


def convert_blp(info: AssetInfo, workdir: str, cfg: Config) -> ConversionResult:
    result = ConversionResult(source=info.path, stage="blp")

    if info.wotlk_ready:
        # Already DXT/pal/bgra with sane dims; copy through unchanged.
        result.output = info.path
        result.success = True
        result.report = validate(info)
        return result

    # TODO(phase1): call blp_hd_patch to decode/resize/recompress/mip, writing
    # to `out_path`. Then re-scan `out_path` and validate.
    out_path = os.path.join(workdir, os.path.basename(info.path))
    raise NotImplementedError(
        "Wire convert_blp to blp_hd_patch.py (decode/resize/recompress/mips) "
        f"and validate the output at {out_path!r}."
    )


def _passes_size_floor(path: str, cfg: Config) -> bool:
    """0-byte / truncated-output defence used by every convert stage."""
    try:
        return os.path.getsize(path) >= cfg.size_floor_bytes
    except OSError:
        return False
