"""Safe file writing: write, verify, then replace - never leave a half-bad file.

Adapted from Conjure's safety rules (MIT, TheDaveCalaz): every edit is re-read
and verified before it counts as done, and inputs are never overwritten. The
write here is atomic - data goes to a temp file beside the target, the verifier
runs on that temp file, and only a passing file replaces the target. A failed
verification leaves the previous file exactly as it was.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Callable


def write_verified(path: str, data: bytes, verify: Callable[[str], None] | None = None) -> str:
    """Atomically write ``data`` to ``path`` after ``verify(temp_path)`` passes.

    ``verify`` raises to reject the write; the temp file is removed and the
    exception propagates, leaving ``path`` untouched."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".~rp_", suffix=os.path.splitext(path)[1], dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if verify is not None:
            verify(tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def backup_files(paths: list[str], backup_dir: str) -> list[str]:
    """Copy each existing file in ``paths`` into ``backup_dir`` (overwriting an
    older backup of the same name). Returns the backup paths written."""
    os.makedirs(backup_dir, exist_ok=True)
    out: list[str] = []
    for p in paths:
        if os.path.isfile(p):
            dst = os.path.join(backup_dir, os.path.basename(p))
            shutil.copy2(p, dst)
            out.append(dst)
    return out


def same_dir(a: str, b: str) -> bool:
    """True if two directory paths refer to the same folder."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
