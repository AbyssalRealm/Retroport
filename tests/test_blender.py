"""Blender wrapper tests - the decimate ratio math and the argv the wrapper builds.
(The bpy script itself needs a real Blender + addon and is validated on the user's
machine; here we test everything around it.)
"""

import retroport.convert.blender as B
from retroport.convert.tools import ToolResult


def test_decimate_ratio():
    assert B.decimate_ratio(39761, 21000) < 1.0
    assert abs(B.decimate_ratio(40000, 20000) - 0.5) < 1e-6
    assert B.decimate_ratio(10000, 21000) == 1.0        # already under target -> no reduction
    assert B.decimate_ratio(0, 21000) == 1.0            # guard


def test_wrapper_builds_blender_args(tmp_path, monkeypatch):
    mesh = tmp_path / "m.obj"; mesh.write_text("o m\n")
    out = tmp_path / "m_lo.obj"

    seen = {}

    def fake_run_tool(exe, args, cwd=None, timeout=600):
        seen["exe"] = exe
        seen["args"] = args
        out.write_text("o decimated\n" + "v 0 0 0\n" * 20)   # >64 bytes, passes size floor
        return ToolResult(ok=True, stdout="[decimate] 39761 -> 20950 tris")
    monkeypatch.setattr(B, "run_tool", fake_run_tool)

    res = B.run_blender_decimate("C:/blender/blender.exe", str(mesh), str(out), target_tris=21000)
    assert res.ok
    a = seen["args"]
    assert "--background" in a and "--python" in a
    assert a[a.index("--input") + 1] == str(mesh)
    assert a[a.index("--output") + 1] == str(out)
    assert a[a.index("--target-tris") + 1] == "21000"


def test_wrapper_missing_input(tmp_path):
    res = B.run_blender_decimate("blender.exe", str(tmp_path / "nope.obj"), str(tmp_path / "o.obj"))
    assert not res.ok and "not found" in res.stderr
