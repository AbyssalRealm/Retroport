"""run_tool tests against a REAL subprocess (the Python interpreter), so the
hang-proof behaviour is actually exercised, not mocked. The stdin-wait case is
the one that was freezing the app.
"""

import sys
import time

import pytest

from retroport.convert.tools import run_tool, passes_size_floor

PY = sys.executable


def test_normal_exit_captures_stdout():
    res = run_tool(PY, ["-c", "print('hello-from-tool')"])
    assert res.ok and res.returncode == 0
    assert "hello-from-tool" in res.stdout


def test_nonzero_exit_is_failure():
    res = run_tool(PY, ["-c", "import sys; sys.exit(3)"])
    assert not res.ok and res.returncode == 3


def test_tool_that_waits_on_stdin_does_not_hang():
    # Without DEVNULL stdin this blocks forever (the original bug). With it, the
    # read hits EOF, input() raises, the process exits - quickly, not after timeout.
    start = time.monotonic()
    res = run_tool(PY, ["-c", "input()"], timeout=30)
    assert time.monotonic() - start < 10          # returned promptly, did not hang
    assert not res.ok                              # exited via unhandled EOFError


def test_timeout_kills_process():
    start = time.monotonic()
    res = run_tool(PY, ["-c", "import time; time.sleep(30)"], timeout=1)
    assert time.monotonic() - start < 10
    assert not res.ok and "timed out" in res.stderr


def test_missing_exe():
    res = run_tool("/no/such/tool.exe", ["-x"])
    assert not res.ok and "not found" in res.stderr


def test_size_floor(tmp_path):
    small = tmp_path / "s.bin"; small.write_bytes(b"x" * 10)
    big = tmp_path / "b.bin"; big.write_bytes(b"x" * 200)
    assert not passes_size_floor(str(small), 64)
    assert passes_size_floor(str(big), 64)
