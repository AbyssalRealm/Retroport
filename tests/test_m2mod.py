"""M2Mod wrapper tests - exact CLI arg construction (from the discovered flags),
success/failure detection, and the export->decimate->import chain with mocks.
"""

import retroport.convert.m2mod as M
from retroport.convert.tools import ToolResult


def _capture(monkeypatch, stdout="Export successful.", make_output=True):
    seen = {}

    def fake_run_tool(exe, args, cwd=None, timeout=600):
        seen["args"] = args
        # emulate M2Mod writing its --output file
        if make_output and "--output" in args:
            out = args[args.index("--output") + 1]
            with open(out, "wb") as fh:
                fh.write(b"\x00" * 128)
        return ToolResult(ok=True, stdout=stdout)
    monkeypatch.setattr(M, "run_tool", fake_run_tool)
    return seen


def test_m2_to_m2i_args(tmp_path, monkeypatch):
    m2 = tmp_path / "foo.m2"; m2.write_bytes(b"MD21" + b"\x00" * 64)
    seen = _capture(monkeypatch)
    res = M.m2_to_m2i("C:/m/M2Mod.exe", str(m2), str(tmp_path / "foo.m2i"), mappings_dir="C:/maps")
    assert res.ok
    a = seen["args"]
    assert a[0] == "--m2-to-m2i"
    assert a[a.index("--input") + 1] == str(m2)
    assert a[a.index("--mappings-dir") + 1] == "C:/maps"


def test_m2i_to_m2_args(tmp_path, monkeypatch):
    m2i = tmp_path / "foo.m2i"; m2i.write_bytes(b"\x00" * 64)
    ref = tmp_path / "foo.m2"; ref.write_bytes(b"MD21" + b"\x00" * 64)
    seen = _capture(monkeypatch, stdout="Import successful.")
    res = M.m2i_to_m2("M2Mod.exe", str(m2i), str(ref), str(tmp_path / "out.m2"))
    assert res.ok
    a = seen["args"]
    assert a[0] == "--m2i-to-m2"
    assert a[a.index("--input-m2i") + 1] == str(m2i)
    assert a[a.index("--input-m2") + 1] == str(ref)


def test_failure_detected_from_output_text(tmp_path, monkeypatch):
    m2 = tmp_path / "foo.m2"; m2.write_bytes(b"MD21" + b"\x00" * 64)

    def fake(exe, args, cwd=None, timeout=600):
        return ToolResult(ok=True, stdout="Failed to export M2I: something")
    monkeypatch.setattr(M, "run_tool", fake)
    res = M.m2_to_m2i("M2Mod.exe", str(m2), str(tmp_path / "foo.m2i"))
    assert not res.ok                                    # 'failed' in output -> failure


def test_txid_remove_args(tmp_path, monkeypatch):
    m2 = tmp_path / "foo.m2"; m2.write_bytes(b"MD21" + b"\x00" * 64)
    seen = _capture(monkeypatch, stdout="done")
    res = M.txid_remove("M2Mod.exe", str(m2), str(tmp_path / "foo_clean.m2"))
    assert res.ok and seen["args"][0] == "--txid-remove"


def test_reduce_model_chain(tmp_path, monkeypatch):
    # stub the three steps to exercise the orchestration order and success path
    m2 = tmp_path / "big.m2"; m2.write_bytes(b"MD21" + b"\x00" * 64)
    calls = []

    def fake_export(exe, src, out, **kw):
        calls.append("export"); open(out, "wb").write(b"\x00" * 128); return ToolResult(ok=True)

    def fake_decimate(exe, src, out, **kw):
        calls.append("decimate"); open(out, "wb").write(b"\x00" * 128); return ToolResult(ok=True)

    def fake_import(exe, m2i, ref, out, **kw):
        calls.append("import"); open(out, "wb").write(b"\x00" * 128); return ToolResult(ok=True)

    monkeypatch.setattr(M, "m2_to_m2i", fake_export)
    monkeypatch.setattr(M, "run_blender_decimate", fake_decimate)
    monkeypatch.setattr(M, "m2i_to_m2", fake_import)

    res = M.reduce_model("M2Mod.exe", "blender.exe", str(m2), str(tmp_path / "work"), target_tris=21000)
    assert res.ok
    assert calls == ["export", "decimate", "import"]     # correct order
    assert res.reduced_m2 and res.reduced_m2.endswith("big.m2")


def test_reduce_model_stops_on_import_failure(tmp_path, monkeypatch):
    m2 = tmp_path / "big.m2"; m2.write_bytes(b"MD21" + b"\x00" * 64)
    monkeypatch.setattr(M, "m2_to_m2i", lambda e, s, o, **k: (open(o, "wb").write(b"\x00" * 128), ToolResult(ok=True))[1])
    monkeypatch.setattr(M, "run_blender_decimate", lambda e, s, o, **k: (open(o, "wb").write(b"\x00" * 128), ToolResult(ok=True))[1])
    monkeypatch.setattr(M, "m2i_to_m2", lambda e, m, r, o, **k: ToolResult(ok=False, stderr="TooManyVertices"))
    res = M.reduce_model("M2Mod.exe", "blender.exe", str(m2), str(tmp_path / "work"))
    assert not res.ok and "import failed" in res.detail.lower()
