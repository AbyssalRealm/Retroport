"""Refresh retroport's own copy of wotlkconv (``wotlkconv/`` at the repository's top) from a checkout
of its repository, and record which commit it is.

wotlkconv - https://github.com/Bar3b0n3s/Converter (MIT, "Converter contributors") - is retroport's
conversion engine and game-file reader. retroport keeps a copy, unchanged, so it installs, tests and
builds its releases without depending on that repository staying online.

usage:  python packaging/update_wotlkconv.py <Converter checkout>
        (git clone https://github.com/Bar3b0n3s/Converter first, at the commit you want)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEST = os.path.join(REPO, "wotlkconv")
ORIGIN = "https://github.com/Bar3b0n3s/Converter"

NOTE = """# wotlkconv (kept in retroport)

An unchanged copy of **wotlkconv**, the conversion engine and game-file readers (CASC, DB2, M2, BLP)
retroport is built on, from {origin} - MIT License, (c) Converter contributors: see `LICENSE` here.

Copied from commit `{commit}` ({date}). retroport keeps its own copy so it installs, tests and builds
releases without depending on that repository staying online. To update it, clone the repository at
the commit you want and run `python packaging/update_wotlkconv.py <the clone>`.
"""


def git(checkout: str, *args: str) -> str:
    return subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    checkout = os.path.abspath(argv[0])
    src = os.path.join(checkout, "src", "wotlkconv")
    if not os.path.isfile(os.path.join(src, "__init__.py")):
        raise SystemExit(f"no src/wotlkconv in {checkout}")
    if git(checkout, "status", "--porcelain", "--", "src", "LICENSE"):
        raise SystemExit(f"{checkout} has uncommitted changes: copy a commit, not a work in progress")
    commit, date = git(checkout, "rev-parse", "HEAD"), git(checkout, "log", "-1", "--format=%cs")
    shutil.rmtree(DEST, ignore_errors=True)
    shutil.copytree(src, DEST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(os.path.join(checkout, "LICENSE"), os.path.join(DEST, "LICENSE"))
    with open(os.path.join(DEST, "VENDORED.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(NOTE.format(origin=ORIGIN, commit=commit, date=date))
    print(f"wotlkconv {commit[:7]} ({date}) -> {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
