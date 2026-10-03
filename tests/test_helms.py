"""Helm sets: variant discovery and the per-variant pipeline run with/without -fixhelm."""

import pytest

from retroport.convert.tools import ToolResult
from retroport.helms import RACES_335, convert_helm_set, find_helm_set, helm_base

from test_anim_pass import build_m2


def _helm_folder(tmp_path, codes, extra=()):
    src = tmp_path / "src"; src.mkdir()
    for code in codes:
        (src / f"helm_plate_q_01_{code}.m2").write_bytes(build_m2([]))
        (src / f"helm_plate_q_01_{code}00.skin").write_bytes(b"SKIN" + b"\x00" * 60)
    for name in extra:
        (src / name).write_bytes(build_m2([]))
    return src


def test_helm_base():
    assert helm_base("helm_plate_q_01_hum.m2") == "helm_plate_q_01"
    assert helm_base("Helm_Leather_D_01_BeF.m2") == "Helm_Leather_D_01"
    assert helm_base("helm_plate_q_01_za_m.m2") is None


def test_find_helm_set_keeps_335_races_and_reports_gaps(tmp_path):
    src = _helm_folder(tmp_path, ["hum", "huf", "bef", "gom", "pam"],
                       extra=["helm_plate_q_01_za_m.m2", "helm_plate_q_01_vu_f.m2", "shoulder_other.m2"])
    hs = find_helm_set(str(src / "helm_plate_q_01_bef.m2"))
    assert hs.base == "helm_plate_q_01" and hs.dbc_model == "helm_plate_q_01.mdx"
    assert sorted(hs.variants) == ["bef", "gom", "huf", "hum"]            # goblin kept, pandaren not 3.3.5
    assert "bem" in hs.missing and "hum" not in hs.missing and len(hs.missing) == 2 * len(RACES_335) - 3
    assert "helm_plate_q_01_za_m.m2" in hs.skipped and "helm_plate_q_01_pam.m2" in hs.skipped
    assert "shoulder_other.m2" not in hs.skipped
    assert "gom" not in find_helm_set(str(src / "helm_plate_q_01_hum.m2"), include_npc_races=False).variants


@pytest.mark.parametrize("client,expect_fix", [("classic", True), ("hd", False)])
def test_convert_helm_set_runs_each_variant(tmp_path, monkeypatch, client, expect_fix):
    src = _helm_folder(tmp_path, ["hum", "huf"])
    seen = {}

    def fake_mc(exe, m2_path, extra_args=None, work_dir=None, expect_growth=False):
        seen[m2_path.split("\\")[-1].split("/")[-1]] = list(extra_args or [])
        with open(m2_path, "wb") as fh:
            fh.write(b"MD20" + (264).to_bytes(4, "little") + b"\x00" * 400)
        return ToolResult(ok=True)
    monkeypatch.setattr("retroport.pipeline.run_multiconverter", fake_mc)

    out = tmp_path / "out"
    hs, results = convert_helm_set(str(src / "helm_plate_q_01_hum.m2"), str(out), client_models=client, engine="multiconverter",
                                   tools={"multiconverter": "C:/fake/MC.exe"}, do_check=False)
    assert sorted(results) == ["huf", "hum"] and all(r.ok for r in results.values())
    assert sorted(seen) == ["helm_plate_q_01_huf.m2", "helm_plate_q_01_hum.m2"]
    assert all(("-fixhelm" in a) == expect_fix for a in seen.values())
    assert (out / "helm_plate_q_01_hum.m2").exists() and (out / "helm_plate_q_01_hum00.skin").exists()


def test_convert_helm_set_errors(tmp_path):
    src = _helm_folder(tmp_path, [], extra=["helm_plate_q_01_za_m.m2"])
    with pytest.raises(ValueError, match="no 3.3.5 race/gender variants"):
        convert_helm_set(str(src / "helm_plate_q_01_za_m.m2"), str(tmp_path / "out"))
    with pytest.raises(ValueError, match="client_models"):
        convert_helm_set(str(src / "helm_plate_q_01_za_m.m2"), str(tmp_path / "out"), client_models="retail")
