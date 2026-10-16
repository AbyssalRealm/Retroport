"""Run a Python program as ``python <script> <args>`` would: its own folder first on the path.

The release's embedded Python has a ``._pth`` file that fixes its path, so it doesn't put a
script's folder there by itself - and Equip Plus's tools import their neighbours. The workbench
runs them as ``python -m retroport.workbench.runscript <script> <args>``.
"""

from __future__ import annotations

import os
import runpy
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m retroport.workbench.runscript <script.py> [args]")
        return 2
    script = os.path.abspath(sys.argv[1])
    sys.argv = [script, *sys.argv[2:]]
    sys.path.insert(0, os.path.dirname(script))
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
