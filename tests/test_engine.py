"""wotlkconv engine: scoped listfile, real conversions of synthetic models, pipeline wiring."""

import struct

import pytest

from retroport.convert import engine
from retroport.convert.texbake import placeholder_fdid, plan_bake
from retroport.pipeline import Pipeline, PipelineConfig

from test_anim_pass import build_m2
from test_texbake import build_md20_textures, build_md21_with_txid

needs_wotlkconv = pytest.mark.skipif(not engine.available(), reason="wotlkconv not installed")


def _chunk(magic: bytes, payload: bytes) -> bytes:
    return magic + struct.pack("<I", len(payload)) + payload


def _stage(result, name):
    return next(s for s in result.stages if s.name == name)


def test_chunk_fdids_reads_only_fdid_chunks():
    data = build_m2([("TXID", struct.pack("<2I", 111, 0)), ("SKID", struct.pack("<I", 222)),
                     ("AFID", struct.pack("<HHI", 5, 0, 333)), ("LDV1", struct.pack("<I", 999))])
    ids = engine.chunk_fdids(data)
    assert {111, 222, 333} <= ids and 999 not in ids and 0 not in ids


@needs_wotlkconv
def test_scoped_listfile_follows_the_skeleton(tmp_path):
    lf = tmp_path / "listfile.csv"
    lf.write_text("222;character/foo/foo.skel\n333;character/foo/foo0005-00.anim\n"
                  "444;character/foo/parent.skel\n555;unrelated/thing.m2\n")
    (tmp_path / "foo.skel").write_bytes(_chunk(b"SKPD", struct.pack("<2I", 0, 0) + struct.pack("<2I", 444, 0))
                                        + _chunk(b"AFID", struct.pack("<HHI", 5, 0, 333)))
    model = build_m2([("SKID", struct.pack("<I", 222))])
    scoped = engine.scoped_listfile(model, [str(tmp_path)], str(lf))
    assert scoped.path_for(222) == "character\\foo\\foo.skel"
    assert scoped.path_for(333) and scoped.path_for(444)       # found through the .skel
    assert scoped.path_for(555) is None                          # nothing the model doesn't use
    assert len(engine.scoped_listfile(model, [str(tmp_path)], None)) == 0


@needs_wotlkconv
def test_convert_with_wotlkconv_writes_verified_md20(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "beast.m2").write_bytes(build_m2([]))
    r = engine.convert_with_wotlkconv(str(src / "beast.m2"), str(tmp_path / "out"))
    assert r.ok and r.status in ("ok", "lossy") and r.output.endswith("beast.m2")
    data = open(r.output, "rb").read()
    assert data[:4] == b"MD20" and struct.unpack_from("<I", data, 4)[0] == 264


@needs_wotlkconv
def test_convert_with_wotlkconv_reports_failure_without_writing(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "bad.m2").write_bytes(build_md21_with_txid([1]))       # version 0: refused
    r = engine.convert_with_wotlkconv(str(src / "bad.m2"), str(tmp_path / "out"))
    assert not r.ok and "predates Wrath" in r.error
    assert not (tmp_path / "out").exists()


def test_missing_wotlkconv_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: False)
    src = tmp_path / "src"; src.mkdir()
    (src / "beast.m2").write_bytes(build_m2([]))
    r = engine.convert_with_wotlkconv(str(src / "beast.m2"), str(tmp_path / "out"))
    assert not r.ok and "pip install" in r.error
    res = Pipeline(PipelineConfig(model=str(src / "beast.m2"), out_dir=str(tmp_path / "o2"), do_check=False),
                   on_log=lambda m: None).run()
    assert not _stage(res, "convert").ok and "not installed" in _stage(res, "convert").detail


@needs_wotlkconv
def test_pipeline_default_engine_converts_and_skips_mc_stages(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "dragon.m2").write_bytes(build_m2([]))
    logs = []
    res = Pipeline(PipelineConfig(model=str(src / "dragon.m2"), out_dir=str(tmp_path / "out")),
                   on_log=logs.append).run()
    assert _stage(res, "stage").skipped and "wotlkconv" in _stage(res, "stage").detail
    conv = _stage(res, "convert")
    assert conv.ok and "MD20 v264" in conv.detail
    assert open(tmp_path / "out" / "dragon.m2", "rb").read(4) == b"MD20"
    assert any("wotlkconv" in m for m in logs)


def test_pipeline_skips_reinline_for_skel_models_with_wotlkconv(tmp_path, monkeypatch):
    src = tmp_path / "src"; src.mkdir()
    (src / "dragon.m2").write_bytes(build_m2([("SKID", struct.pack("<I", 1))]))
    called = {}

    def fake(model_path, out_dir, **kw):
        called["stem"] = kw["output_stem"]
        out = tmp_path / "out" / "dragon.m2"
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(build_md20_textures([]))
        return engine.EngineResult(ok=True, status="ok", output=str(out), written=[str(out)])
    monkeypatch.setattr(engine, "convert_with_wotlkconv", fake)
    monkeypatch.setattr(engine, "available", lambda: True)
    res = Pipeline(PipelineConfig(model=str(src / "dragon.m2"), out_dir=str(tmp_path / "out"), do_check=False),
                   on_log=lambda m: None).run()
    assert _stage(res, "reinline").skipped and "merges" in _stage(res, "reinline").detail
    assert _stage(res, "convert").ok and called["stem"] == "dragon"


def test_unknown_engine_is_refused(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "x.m2").write_bytes(build_m2([]))
    res = Pipeline(PipelineConfig(model=str(src / "x.m2"), out_dir=str(tmp_path / "out"), engine="magic"),
                   on_log=lambda m: None).run()
    assert not res.ok and "unknown engine" in _stage(res, "config").detail and len(res.stages) == 1


def test_texbake_resolves_wotlkconv_placeholders():
    assert placeholder_fdid("unknown\\1234567.blp") == 1234567
    assert placeholder_fdid("item\\weapon\\a.blp") is None
    md20 = build_md20_textures([(0, "unknown\\555.blp"), (0, "Item\\A\\a.blp")])
    plan = plan_bake(md20, [], {555: "item\\weapon\\real.blp"})
    assert plan.paths == {0: "item\\weapon\\real.blp"} and plan.fdids == {0: 555}
