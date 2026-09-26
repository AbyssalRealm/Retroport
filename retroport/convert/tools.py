"""Runner for external command-line tools (MultiConverter, BLPConverter, M2Mod,
MPQ Editor, Blender).

Each tool is a Windows exe the user points at in Settings. This wraps subprocess
defensively, because these tools are built for drag-and-drop and misbehave under
automation:

  * stdin is DEVNULL - if a tool waits for a keypress ("Press any key...", or an
    interactive y/n), it gets EOF and proceeds instead of hanging us forever.
  * stdout/stderr go to temp *files*, not pipes - if the tool spawns a helper that
    inherits and holds the output handle, a pipe read would deadlock past the
    timeout; a file read never does. We wait on the child process itself.
  * on timeout the process is killed, so a stuck tool can never freeze the app.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

# On Windows, avoid spawning console windows for each tool call.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class ToolResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None


def run_tool(exe: str, args: list[str], cwd: str | None = None, timeout: int = 600) -> ToolResult:
    """Run ``exe`` with ``args``. Never blocks on tool input or a held output pipe;
    returns ok=False on non-zero exit, timeout, or launch failure."""
    if not exe or not os.path.isfile(exe):
        return ToolResult(ok=False, stderr=f"tool not found: {exe!r}")

    name = os.path.basename(exe)
    out_f = tempfile.TemporaryFile(mode="w+b")
    err_f = tempfile.TemporaryFile(mode="w+b")
    try:
        try:
            proc = subprocess.Popen(
                [exe, *args],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            return ToolResult(ok=False, stderr=str(exc))

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return ToolResult(ok=False, returncode=proc.returncode,
                              stderr=f"{name} timed out after {timeout}s and was killed")

        out_f.seek(0); err_f.seek(0)
        out = out_f.read().decode("utf-8", "replace")
        err = err_f.read().decode("utf-8", "replace")
        return ToolResult(ok=proc.returncode == 0, stdout=out, stderr=err, returncode=proc.returncode)
    finally:
        out_f.close()
        err_f.close()


def passes_size_floor(path: str, floor: int = 64) -> bool:
    """A converted output smaller than the floor is a silent-failure artifact."""
    try:
        return os.path.getsize(path) >= floor
    except OSError:
        return False
