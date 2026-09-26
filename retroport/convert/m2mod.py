"""M2Mod CLI wrappers and the poly-reduction chain.

CLI (discovered from the M2Mod assembly):
    --m2-to-m2i   --input <m2>  [--output <m2i>]
    --m2i-to-m2   --input-m2i <m2i> --input-m2 <m2> [--replace-m2 <m2>] [--output <m2>]
    --txid-remove --input <m2>  [--output <m2>]
    --mappings-dir <path>   --profile <name>   --working-dir <path>

Poly reduction is: export the modern M2 to M2I, decimate the M2I in Blender, then
import the reduced M2I back to M2 (using the original as a template). M2Mod rejects
an import that's still over the WotLK vertex/index caps, so a too-small reduction
fails loudly rather than producing a broken model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .tools import ToolResult, run_tool, passes_size_floor
from .blender import run_blender_decimate, DEFAULT_TARGET_TRIS


def _common(mappings_dir: str | None, working_dir: str | None, profile: str | None) -> list[str]:
    extra: list[str] = []
    if mappings_dir:
        extra += ["--mappings-dir", mappings_dir]
    if working_dir:
        extra += ["--working-dir", working_dir]
    if profile:
        extra += ["--profile", profile]
    return extra


def _ok(res: ToolResult, output_path: str) -> ToolResult:
    """M2Mod prints 'failed'/'Error:' on failure; success writes the output file."""
    blob = (res.stdout + " " + res.stderr).lower()
    if res.ok and "failed" not in blob and "error:" not in blob and passes_size_floor(output_path):
        return res
    detail = res.stderr.strip() or res.stdout.strip() or f"M2Mod did not produce {os.path.basename(output_path)}"
    return ToolResult(ok=False, stdout=res.stdout, stderr=detail, returncode=res.returncode)


def m2_to_m2i(exe: str, m2_path: str, m2i_out: str, mappings_dir: str | None = None,
              working_dir: str | None = None, profile: str | None = None) -> ToolResult:
    if not os.path.isfile(m2_path):
        return ToolResult(ok=False, stderr=f"input M2 not found: {m2_path}")
    args = ["--m2-to-m2i", "--input", m2_path, "--output", m2i_out] + _common(mappings_dir, working_dir, profile)
    return _ok(run_tool(exe, args, cwd=os.path.dirname(exe) or None), m2i_out)


def m2i_to_m2(exe: str, m2i_path: str, m2_ref: str, m2_out: str, mappings_dir: str | None = None,
              working_dir: str | None = None, profile: str | None = None) -> ToolResult:
    if not os.path.isfile(m2i_path):
        return ToolResult(ok=False, stderr=f"input M2I not found: {m2i_path}")
    if not os.path.isfile(m2_ref):
        return ToolResult(ok=False, stderr=f"template M2 not found: {m2_ref}")
    args = ["--m2i-to-m2", "--input-m2i", m2i_path, "--input-m2", m2_ref, "--output", m2_out] \
        + _common(mappings_dir, working_dir, profile)
    return _ok(run_tool(exe, args, cwd=os.path.dirname(exe) or None), m2_out)


def txid_remove(exe: str, m2_path: str, m2_out: str, mappings_dir: str | None = None,
                working_dir: str | None = None) -> ToolResult:
    if not os.path.isfile(m2_path):
        return ToolResult(ok=False, stderr=f"input M2 not found: {m2_path}")
    args = ["--txid-remove", "--input", m2_path, "--output", m2_out] + _common(mappings_dir, working_dir, None)
    return _ok(run_tool(exe, args, cwd=os.path.dirname(exe) or None), m2_out)


@dataclass
class ReduceResult:
    ok: bool
    reduced_m2: str | None = None
    detail: str = ""


def reduce_model(m2mod_exe: str, blender_exe: str, m2_path: str, work_dir: str,
                 target_tris: int = DEFAULT_TARGET_TRIS, mappings_dir: str | None = None,
                 working_dir: str | None = None, hair_ratio: float = 0.4,
                 mesh_ext: str = ".m2i") -> ReduceResult:
    """Export -> decimate -> import. Returns the reduced M2 (written in work_dir).

    ``working_dir`` is M2Mod's --working-dir: point it at the extraction root (whose
    folders mirror in-game paths) so M2Mod can resolve shared *parent* skeletons.
    ``hair_ratio`` decimates hair geosets harder (0 = flat).

    The reduced M2 keeps the original's skeleton reference (decimation changes
    vertices, not bones), so the existing re-inline step still resolves the .skel
    from the original manifest.
    """
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(m2_path))[0]
    m2i = os.path.join(work_dir, stem + mesh_ext)
    m2i_lo = os.path.join(work_dir, stem + "_lo" + mesh_ext)
    reduced_m2 = os.path.join(work_dir, stem + ".m2")

    r1 = m2_to_m2i(m2mod_exe, m2_path, m2i, mappings_dir=mappings_dir, working_dir=working_dir)
    if not r1.ok:
        return ReduceResult(False, detail=f"M2->M2I export failed: {r1.stderr}")

    r2 = run_blender_decimate(blender_exe, m2i, m2i_lo, target_tris=target_tris, hair_ratio=hair_ratio)
    if not r2.ok:
        return ReduceResult(False, detail=f"Blender decimate failed: {r2.stderr}")

    r3 = m2i_to_m2(m2mod_exe, m2i_lo, m2_path, reduced_m2, mappings_dir=mappings_dir, working_dir=working_dir)
    if not r3.ok:
        return ReduceResult(False, detail=f"M2I->M2 import failed (may still be over caps): {r3.stderr}")

    return ReduceResult(True, reduced_m2=reduced_m2, detail=f"reduced -> {os.path.basename(reduced_m2)}")
