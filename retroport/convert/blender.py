"""Blender decimation wrapper.

Runs the headless ``blender_decimate.py`` script under a Blender exe to reduce a
mesh's triangle count below WotLK's ceiling. This is the pre-conversion poly step:
it operates on geometry Blender can read (.obj/.gltf/.fbx, or .m2i with the M2Mod/
WBS addon), NOT on the .m2 directly - getting geometry to/from .m2 is M2Mod's job
(.m2 <-> .m2i).
"""

from __future__ import annotations

import os

from .tools import ToolResult, run_tool, passes_size_floor

_SCRIPT = os.path.join(os.path.dirname(__file__), "blender_decimate.py")

# leave headroom under the hard 21845 cap for skinning/precision
DEFAULT_TARGET_TRIS = 21000


def decimate_ratio(before_tris: int, target_tris: int) -> float:
    """Collapse ratio to bring ``before_tris`` down to ``target_tris`` (<= 1.0)."""
    if before_tris <= 0:
        return 1.0
    return min(1.0, target_tris / before_tris)


def run_blender_decimate(blender_exe: str, input_mesh: str, output_mesh: str,
                         target_tris: int = DEFAULT_TARGET_TRIS, hair_ratio: float = 0.4,
                         script_path: str = _SCRIPT, timeout: int = 1200) -> ToolResult:
    """Decimate ``input_mesh`` to <= ``target_tris`` and write ``output_mesh``.

    ``hair_ratio`` (0 = flat) decimates hair geosets to that ratio and spends the rest
    of the triangle budget on the body - better silhouette than a flat ratio.
    """
    if not os.path.isfile(input_mesh):
        return ToolResult(ok=False, stderr=f"input mesh not found: {input_mesh}")
    if not os.path.isfile(script_path):
        return ToolResult(ok=False, stderr=f"decimate script missing: {script_path}")

    args = [
        "--background", "--python", script_path, "--",
        "--input", input_mesh,
        "--output", output_mesh,
        "--target-tris", str(target_tris),
        "--hair-ratio", str(hair_ratio),
    ]
    res = run_tool(blender_exe, args, timeout=timeout)
    if res.ok and not passes_size_floor(output_mesh):
        return ToolResult(ok=False, stdout=res.stdout,
                          stderr="Blender ran but produced no/empty output mesh")
    return res
