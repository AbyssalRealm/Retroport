"""Pipeline orchestration tests.

Builds a synthetic .skel model + manifest on disk and runs the headless Pipeline
without any external tools (do_convert off), then a monkeypatched convert stage.
Reuses the Milestone-B fixtures from test_anim_pass.
"""

import json
import os
import struct

from retroport.pipeline import Pipeline, PipelineConfig
from retroport.convert.tools import ToolResult

from test_anim_pass import build_m2, build_skel_two_seq, build_anim_afsb


def _stage(result, name):
    return next(s for s in result.stages if s.name == name)


def _setup_skel_model(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "dragon.skel").write_bytes(build_skel_two_seq())
    (src / "dragon0005-00.anim").write_bytes(build_anim_afsb())
    afid = struct.pack("<HHI", 5, 0, 777)                      # (anim 5, sub 0) -> fid 777
    (src / "dragon.m2").write_bytes(build_m2([("SKID", struct.pack("<I", 1)), ("AFID", afid)]))
    (src / "dragon.manifest.json").write_text(json.dumps({
        "skeleton": {"fileDataID": 1, "file": "dragon.skel"},
        "skelAnims": [{"fileDataID": 777, "file": "dragon0005-00.anim"}],
    }))
    return str(src / "dragon.m2")


def test_pipeline_reinlines_without_external_tools(tmp_path):
    model = _setup_skel_model(tmp_path)
    logs = []
    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"),
                         do_convert=False, do_check=False)
    result = Pipeline(cfg, on_log=logs.append).run()

    assert _stage(result, "triage").ok
    reinline = _stage(result, "reinline")
    assert reinline.ok and not reinline.skipped          # real .skel -> actually ran
    assert _stage(result, "fixup").ok
    assert result.output_m2 and os.path.exists(result.output_m2)
    assert any("inline" in m.lower() for m in logs)


def test_pipeline_convert_stage_with_faked_multiconverter(tmp_path, monkeypatch):
    model = _setup_skel_model(tmp_path)

    def fake_mc(exe, m2_path, extra_args=None, work_dir=None, expect_growth=False):
        assert extra_args == ["-fixhelm"]                 # explicit args flow through
        with open(m2_path, "wb") as fh:                   # simulate a real MD21 -> MD20 downgrade
            fh.write(b"MD20" + struct.pack("<I", 264) + b"\x00" * 400)
        return ToolResult(ok=True, stdout="Converting 1 files... Done.")
    monkeypatch.setattr("retroport.pipeline.run_multiconverter", fake_mc)

    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"), do_check=False,
                         tools={"multiconverter": "C:/fake/MultiConverter.exe"},
                         multiconverter_args=["-fixhelm"])
    result = Pipeline(cfg, on_log=lambda m: None).run()
    assert _stage(result, "convert").ok


def test_pipeline_stages_skins_renamed(tmp_path):
    # a non-skel model whose skin is named oddly in the source should be staged
    # into out_dir as <stem>00.skin so MultiConverter/the client can find it.
    src = tmp_path / "src"; src.mkdir()
    (src / "beast.m2").write_bytes(build_m2([]))                 # no SKID -> uses_skel False
    (src / "beast_lod0.skin").write_bytes(b"SKIN" + b"\x00" * 60)
    (src / "beast.manifest.json").write_text(json.dumps({
        "skins": [{"fileDataID": 5, "file": "beast_lod0.skin"}],
    }))
    out = tmp_path / "out"
    cfg = PipelineConfig(model=str(src / "beast.m2"), out_dir=str(out),
                         do_convert=False, do_fixup=False, do_check=False)
    result = Pipeline(cfg, on_log=lambda m: None).run()

    assert _stage(result, "stage").ok and not _stage(result, "stage").skipped
    assert (out / "beast00.skin").exists()                       # renamed to WotLK convention


def test_pipeline_polyreduce_runs_when_over_limit(tmp_path, monkeypatch):
    # poly-reduce runs BEFORE convert via M2Mod .m2i (reduce_model mocked).
    model = _setup_skel_model(tmp_path)
    reduced = tmp_path / "reduced_dragon.m2"; reduced.write_bytes(build_m2([]))

    import retroport.pipeline as P
    from retroport.convert.m2mod import ReduceResult
    called = {}

    def fake_reduce(m2mod, blender, m2_path, work_dir, target_tris=21000, mappings_dir=None,
                    working_dir=None, hair_ratio=0.4, mesh_ext=".m2i"):
        called["target"] = target_tris
        called["working_dir"] = working_dir
        called["hair"] = hair_ratio
        return ReduceResult(True, reduced_m2=str(reduced), detail="reduced 53348->20800")
    monkeypatch.setattr(P, "reduce_model", fake_reduce)

    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"), source="I:/WoWDownPort",
                         do_convert=False, do_check=False, do_polyreduce=True,
                         poly_target_tris=21000, poly_hair_ratio=0.35,
                         tools={"m2mod": "C:/M2Mod.exe", "blender": "C:/blender.exe"})
    result = Pipeline(cfg, on_log=lambda m: None).run()

    assert called.get("target") == 21000 and called.get("hair") == 0.35
    assert called.get("working_dir") == "I:/WoWDownPort"       # extraction root -> M2Mod --working-dir
    pr = _stage(result, "polyreduce")
    assert pr.ok and not pr.skipped


def test_pipeline_polyreduce_skips_without_tools(tmp_path):
    model = _setup_skel_model(tmp_path)
    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"),
                         do_polyreduce=True, do_convert=False, do_check=False, tools={})
    result = Pipeline(cfg, on_log=lambda m: None).run()
    pr = _stage(result, "polyreduce")
    assert pr.skipped and result.ok


def test_pipeline_txid_remove_step(tmp_path, monkeypatch):
    model = _setup_skel_model(tmp_path)
    import retroport.pipeline as P
    from retroport.convert.tools import ToolResult
    called = {}

    def fake_txid(exe, m2_path, m2_out, mappings_dir=None, working_dir=None):
        called["ran"] = True
        called["working_dir"] = working_dir
        with open(m2_out, "wb") as fh:                    # write the distinct output for the swap
            fh.write(b"MD21" + b"\x00" * 100)
        return ToolResult(ok=True, stdout="ok")
    monkeypatch.setattr(P, "txid_remove", fake_txid)

    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"), source="I:/WoWDownPort",
                         do_convert=False, do_check=False, do_txid_remove=True,
                         tools={"m2mod": "C:/M2Mod.exe"})
    result = Pipeline(cfg, on_log=lambda m: None).run()
    assert called.get("ran") and called.get("working_dir") == "I:/WoWDownPort"
    assert _stage(result, "txid").ok


def test_pipeline_txid_skips_without_m2mod(tmp_path):
    model = _setup_skel_model(tmp_path)
    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"),
                         do_convert=False, do_check=False, do_txid_remove=True, tools={})
    result = Pipeline(cfg, on_log=lambda m: None).run()
    assert _stage(result, "txid").skipped


def test_pipeline_convert_skipped_without_tool(tmp_path):
    model = _setup_skel_model(tmp_path)
    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"),
                         do_check=False, tools={})
    result = Pipeline(cfg, on_log=lambda m: None).run()
    convert = _stage(result, "convert")
    assert convert.skipped and not convert.ok
    assert result.ok                                      # a skipped stage doesn't fail the run


def test_pipeline_refuses_to_write_into_the_models_folder(tmp_path):
    model = _setup_skel_model(tmp_path)
    cfg = PipelineConfig(model=model, out_dir=os.path.dirname(model), do_convert=False, do_check=False)
    result = Pipeline(cfg, on_log=lambda m: None).run()
    assert not result.ok and not _stage(result, "triage").ok
    assert "own folder" in _stage(result, "triage").detail


def test_pipeline_texbake_fills_empty_slots_from_txid(tmp_path, monkeypatch):
    from test_texbake import build_md20_textures, build_md21_with_txid
    from retroport.formats.md20 import read_texture_slots

    src = tmp_path / "src"; src.mkdir()
    (src / "sword.m2").write_bytes(build_md21_with_txid([111, 0, 404]))
    listfile = tmp_path / "listfile.csv"
    listfile.write_text("111;item/objectcomponents/weapon/sword.blp\n")

    def fake_mc(exe, m2_path, extra_args=None, work_dir=None, expect_growth=False):
        with open(m2_path, "wb") as fh:          # "converted": three type-0 slots, all empty
            fh.write(build_md20_textures([(0, ""), (1, ""), (0, "")]))
        return ToolResult(ok=True)
    monkeypatch.setattr("retroport.pipeline.run_multiconverter", fake_mc)

    logs = []
    out = tmp_path / "out"
    cfg = PipelineConfig(model=str(src / "sword.m2"), out_dir=str(out), do_check=False,
                         tools={"multiconverter": "C:/fake/MC.exe", "listfile": str(listfile)})
    result = Pipeline(cfg, on_log=logs.append).run()

    tb = _stage(result, "texbake")
    assert tb.ok and not tb.skipped and "baked 1" in tb.detail and "1 unresolved" in tb.detail
    slots = read_texture_slots((out / "sword.m2").read_bytes())
    assert slots[0].name == r"item\objectcomponents\weapon\sword.blp"
    assert slots[2].name == ""                                       # fdid 404 unknown
    assert any("404 not in the listfile" in m for m in logs)
    assert (out / "_backup" / "sword.m2").exists()                   # pre-edit copy kept


def test_pipeline_texbake_skips_unconverted_model(tmp_path):
    model = _setup_skel_model(tmp_path)
    cfg = PipelineConfig(model=model, out_dir=str(tmp_path / "out"), do_convert=False, do_check=False)
    result = Pipeline(cfg, on_log=lambda m: None).run()
    tb = _stage(result, "texbake")
    assert tb.skipped and result.ok
