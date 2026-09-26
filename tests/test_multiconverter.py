"""MultiConverter wrapper tests - argument construction and placeholder ordering.

We monkeypatch run_tool to capture the argv the wrapper builds, so we can verify
-fixhelm passes through and that {file} controls argument order, without a real exe.
"""

import retroport.convert.multiconverter as mc
from retroport.convert.tools import ToolResult


def _capture(monkeypatch):
    seen = {}

    def fake_run_tool(exe, args, cwd=None, timeout=600):
        seen["exe"] = exe
        seen["args"] = args
        return ToolResult(ok=True)
    monkeypatch.setattr(mc, "run_tool", fake_run_tool)
    return seen


def test_file_first_then_flags(tmp_path, monkeypatch):
    m2 = tmp_path / "foo.m2"
    m2.write_bytes(b"MD20" + b"\x00" * 128)
    seen = _capture(monkeypatch)
    res = mc.run_multiconverter("C:/x/MultiConverter_Console.exe", str(m2), extra_args=["-fixhelm"])
    assert res.ok
    assert seen["args"] == [str(m2), "-fixhelm"]          # path first, flag after


def test_file_placeholder_controls_order(tmp_path, monkeypatch):
    m2 = tmp_path / "foo.m2"
    m2.write_bytes(b"MD20" + b"\x00" * 128)
    seen = _capture(monkeypatch)
    mc.run_multiconverter("mc.exe", str(m2), extra_args=["-fixhelm", "{file}"])
    assert seen["args"] == ["-fixhelm", str(m2)]          # flag first, path where {file} was


def test_missing_model_fails_fast(tmp_path):
    res = mc.run_multiconverter("mc.exe", str(tmp_path / "nope.m2"), extra_args=["-fixhelm"])
    assert not res.ok and "not found" in res.stderr
