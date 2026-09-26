"""MultiConverter wrapper.

MultiConverter is normally drag-and-drop (drop the .m2, click Fix). Most builds
also accept the file path as an argument, which is what we attempt here. It
converts in place (MD21->MD20, version->264, resolves TXID/SFID), so we snapshot
the size before/after and treat "no change" as a probable no-op.

If a given MultiConverter build does not accept a CLI argument, this stage should
be run manually; the pipeline reports that clearly instead of failing silently.
"""

from __future__ import annotations

import os
import shutil

from .tools import ToolResult, run_tool, passes_size_floor


def run_multiconverter(exe: str, m2_path: str, extra_args: list[str] | None = None,
                       work_dir: str | None = None, expect_growth: bool = False) -> ToolResult:
    """Convert an M2 in place with MultiConverter's console build.

    Point ``exe`` at MultiConverter_Console.exe (the GUI build ignores argv and
    opens a window). ``extra_args`` are passed through - e.g. ``["-fixhelm"]``.
    If any arg contains ``{file}`` (or ``{m2}``) the model path is substituted
    there, giving full control of argument order; otherwise the path is placed
    first, then the extra args.

    ``expect_growth``: only when MultiConverter itself re-inlines a .skel; we warn
    (never fail) if the expected growth doesn't happen.
    """
    if not os.path.isfile(m2_path):
        return ToolResult(ok=False, stderr=f"model not found: {m2_path}")

    extra_args = list(extra_args or [])
    if any(("{file}" in a or "{m2}" in a) for a in extra_args):
        argv = [a.replace("{file}", m2_path).replace("{m2}", m2_path) for a in extra_args]
    else:
        argv = [m2_path, *extra_args]

    before = os.path.getsize(m2_path)
    # Run from the exe's own directory (where its support files live), matching a
    # manual run; the model path is absolute so it's still converted in place.
    res = run_tool(exe, argv, cwd=work_dir or os.path.dirname(exe) or None)
    if not res.ok:
        return res

    if not passes_size_floor(m2_path):
        return ToolResult(ok=False, stderr="MultiConverter output is empty/0-byte (silent failure)")

    after = os.path.getsize(m2_path)
    if expect_growth and after <= before:
        res.stderr = (res.stderr + " | warning: expected size growth from skeleton re-inline but file "
                      "did not grow.").strip()
    return res
