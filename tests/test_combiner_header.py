"""MultiConverter's combiner-combos header overlap, and keeping usable combiner tables.

Mirrors a real case (11AT_Arathor_BookWheel01): the modern model has no combos and its
sequence table starts at 0x130. MultiConverter sets flag 0x08 and writes the combos
M2Array at 0x130 without moving the sequences, wiping sequence 0's id/variation/duration.
"""

import json
import struct

import pytest
from typer.testing import CliRunner

from retroport.check import check_model
from retroport.cli import app
from retroport.convert import fixup
from retroport.convert.tools import ToolResult
from retroport.formats import structs as S
from retroport.formats.md20 import read_sequences
from retroport.formats.verify import combiner_combos_problem, combiner_header_overlaps, m2_problems
from retroport.pipeline import Pipeline, PipelineConfig

runner = CliRunner()

STAND, SPELL = 0, 213
COMBOS = [1, 4]          # Blizzard's most common table: texture + env-mapped reflection


def _sequence(anim_id, variation, duration):
    rec = bytearray(S.M2SEQUENCE)
    struct.pack_into("<HHI", rec, 0, anim_id, variation, duration)
    struct.pack_into("<I", rec, S.M2SEQUENCE_FLAGS, S.SEQ_FLAG_INTERNAL_0x20)
    return bytes(rec)


def build_original():
    """A converted-layout model before MultiConverter: no flag 0x08, sequences at 0x130."""
    b = bytearray(0x130)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 274)
    seqs = _sequence(STAND, 0, 1000) + _sequence(SPELL, 0, 18667)
    S.write_m2array(b, S.HDR_SEQUENCES, 2, len(b))
    b += seqs
    S.write_m2array(b, S.HDR_BONES, 0, len(b))
    return bytes(b)


def multiconvert(original: bytes, combos=COMBOS) -> bytes:
    """What MultiConverter does: MD20 v264, flag 0x08, combos field written over 0x130."""
    b = bytearray(original)
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    struct.pack_into("<I", b, S.HDR_GLOBAL_FLAGS, S.read_u32(b, S.HDR_GLOBAL_FLAGS) | S.FLAG_USE_COMBINER_COMBOS)
    combo_off = len(b)
    b += struct.pack(f"<{len(combos)}H", *combos)
    S.write_m2array(b, S.HDR_TEXTURE_COMBINER_COMBOS, len(combos), combo_off)
    return bytes(b)


def build_skin(batches):
    """batches: [(shader, texture_count)]"""
    bat_off = 0x30
    s = bytearray(bat_off + len(batches) * S.M2BATCH)
    s[0:4] = b"SKIN"
    S.write_m2array(s, S.SKIN_BATCHES, len(batches), bat_off)
    for i, (shader, tex_count) in enumerate(batches):
        rec = bat_off + i * S.M2BATCH
        struct.pack_into("<H", s, rec + S.BATCH_SHADER, shader)
        struct.pack_into("<H", s, rec + S.BATCH_TEXTURE_COUNT, tex_count)
    return bytes(s)


def _anim_ids(data):
    return [(q.anim_id, q.sub_id) for q in read_sequences(data)]


def test_multiconverter_overlap_is_detected():
    converted = multiconvert(build_original())
    assert combiner_header_overlaps(converted) == ["sequences"]
    assert "sequences data overlaps the combiner-combos header field" in m2_problems(converted)
    assert _anim_ids(converted)[0] != (STAND, 0)            # Stand is gone


def test_repair_relocates_sequences_and_keeps_combos(tmp_path):
    original = build_original()
    m2 = tmp_path / "m.m2"
    m2.write_bytes(multiconvert(original))

    assert fixup.repair_combiner_header(str(m2), original) == ["sequences"]
    data = m2.read_bytes()
    assert _anim_ids(data) == [(STAND, 0), (SPELL, 0)]
    _, seq_off = S.read_m2array(data, S.HDR_SEQUENCES)
    assert seq_off >= S.MD20_HEADER_SIZE_COMBOS and seq_off % 16 == 0
    assert S.read_u32(data, S.HDR_GLOBAL_FLAGS) & S.FLAG_USE_COMBINER_COMBOS
    cnt, off = S.read_m2array(data, S.HDR_TEXTURE_COMBINER_COMBOS)
    assert list(struct.unpack_from(f"<{cnt}H", data, off)) == COMBOS
    assert not combiner_header_overlaps(data)
    assert fixup.repair_combiner_header(str(m2), original) == []        # idempotent


def test_repair_accepts_md21_original(tmp_path):
    original = build_original()
    md21 = b"MD21" + struct.pack("<I", len(original)) + original + b"SFID" + struct.pack("<I", 4) + b"\0" * 4
    m2 = tmp_path / "m.m2"
    m2.write_bytes(multiconvert(original))
    assert fixup.repair_combiner_header(str(m2), md21) == ["sequences"]
    assert _anim_ids(m2.read_bytes())[0] == (STAND, 0)


def test_repair_in_place_after_flag_was_cleared(tmp_path):
    # older retroport output: MultiConverter's overlap, then the combiner flag cleared
    original = build_original()
    broken = bytearray(multiconvert(original))
    struct.pack_into("<I", broken, S.HDR_GLOBAL_FLAGS, 0)
    m2 = tmp_path / "m.m2"
    m2.write_bytes(bytes(broken))

    assert fixup.repair_combiner_header(str(m2), original) == ["sequences"]
    data = m2.read_bytes()
    assert S.read_m2array(data, S.HDR_SEQUENCES) == S.read_m2array(original, S.HDR_SEQUENCES)
    assert _anim_ids(data) == [(STAND, 0), (SPELL, 0)]
    assert len(data) == len(broken)                                    # restored in place


def test_repair_refuses_a_different_original(tmp_path):
    original = build_original()
    m2 = tmp_path / "m.m2"
    converted = multiconvert(original)
    m2.write_bytes(converted)

    other = bytearray(original)
    struct.pack_into("<H", other, 0x130 + S.M2SEQUENCE, 99)           # sequence 1 differs
    with pytest.raises(ValueError, match="doesn't hold the same data"):
        fixup.repair_combiner_header(str(m2), bytes(other))
    with pytest.raises(ValueError, match="same bytes at 0x130"):
        fixup.repair_combiner_header(str(m2), converted)              # its own output, not the input
    assert m2.read_bytes() == converted                               # untouched


def test_repair_skips_healthy_and_unconverted_models(tmp_path):
    original = build_original()
    healthy = tmp_path / "ok.m2"
    healthy.write_bytes(original)                                     # no flag, nothing overwritten
    assert fixup.repair_combiner_header(str(healthy), original) == []

    md21 = tmp_path / "modern.m2"
    md21.write_bytes(b"MD21" + struct.pack("<I", len(original)) + original)
    assert fixup.repair_combiner_header(str(md21), original) == []


def _repaired_model(tmp_path, combos=COMBOS):
    original = build_original()
    m2 = tmp_path / "m.m2"
    m2.write_bytes(multiconvert(original, combos))
    fixup.repair_combiner_header(str(m2), original)
    return m2


def test_valid_combiner_table_is_kept(tmp_path):
    m2 = _repaired_model(tmp_path)
    skin = tmp_path / "m00.skin"
    skin.write_bytes(build_skin([(0, 2), (0, 1), (0x8000, 1)]))
    before = (m2.read_bytes(), skin.read_bytes())

    assert fixup.combiner_status(str(m2), [str(skin)]) == ""
    assert fixup.clear_combiner_combos(str(m2), [str(skin)]) == (False, 0)
    assert (m2.read_bytes(), skin.read_bytes()) == before


@pytest.mark.parametrize("combos, batches, reason", [
    (COMBOS, [(1, 2)], "reads past the 2-entry combo table"),
    ([1, 9], [(0, 2)], "not WotLK blend modes"),
])
def test_unusable_combiner_table_is_cleared(tmp_path, combos, batches, reason):
    m2 = _repaired_model(tmp_path, combos)
    skin = tmp_path / "m00.skin"
    skin.write_bytes(build_skin(batches))

    assert reason in fixup.combiner_status(str(m2), [str(skin)])
    cleared, _changed = fixup.clear_combiner_combos(str(m2), [str(skin)])
    assert cleared
    assert not S.read_u32(m2.read_bytes(), S.HDR_GLOBAL_FLAGS) & S.FLAG_USE_COMBINER_COMBOS


def test_combiner_table_still_overlapping_is_unusable():
    converted = multiconvert(build_original())
    assert "overlaps the combiner-combos header" in combiner_combos_problem(converted, [build_skin([(0, 2)])])


def test_check_fails_overlap_and_passes_valid_table(tmp_path):
    original = build_original()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "m.m2").write_bytes(multiconvert(original))
    (broken / "m00.skin").write_bytes(build_skin([(0, 2)]))
    findings = {f.check: f for f in check_model(str(broken / "m.m2")).report().findings}
    assert findings["combiner_header"].severity.value == "fail"

    m2 = _repaired_model(tmp_path)
    (tmp_path / "m00.skin").write_bytes(build_skin([(0, 2)]))
    findings = {f.check: f for f in check_model(str(m2)).report().findings}
    assert "combiner_header" not in findings
    assert findings["combiner_combos"].severity.value == "pass"


def test_pipeline_repairs_multiconverter_overlap(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    original = build_original()
    (src / "wheel.m2").write_bytes(b"MD21" + struct.pack("<I", len(original)) + original)
    (src / "wheel.skin").write_bytes(build_skin([(0, 2), (0, 1)]))
    (src / "wheel.manifest.json").write_text(json.dumps({"skins": [{"fileDataID": 1, "file": "wheel.skin"}]}))

    def fake_mc(exe, m2_path, extra_args=None, work_dir=None, expect_growth=False):
        with open(m2_path, "rb") as fh:
            data = fh.read()
        size = struct.unpack_from("<I", data, 4)[0]
        with open(m2_path, "wb") as fh:
            fh.write(multiconvert(data[8:8 + size]))
        return ToolResult(ok=True)
    monkeypatch.setattr("retroport.pipeline.run_multiconverter", fake_mc)

    logs = []
    cfg = PipelineConfig(engine="multiconverter", model=str(src / "wheel.m2"), out_dir=str(tmp_path / "out"), do_texbake=False,
                         tools={"multiconverter": "C:/fake/MultiConverter.exe"})
    result = Pipeline(cfg, on_log=logs.append).run()

    fixup_stage = next(s for s in result.stages if s.name == "fixup")
    assert "restored sequences" in fixup_stage.detail
    assert "combiner combos kept" in fixup_stage.detail
    data = (tmp_path / "out" / "wheel.m2").read_bytes()
    assert _anim_ids(data) == [(STAND, 0), (SPELL, 0)]
    assert S.read_u32(data, S.HDR_GLOBAL_FLAGS) & S.FLAG_USE_COMBINER_COMBOS
    assert not any(f.check == "combiner_header" for f in result.check.report().findings)


def test_cli_repair(tmp_path):
    original = build_original()
    (tmp_path / "orig.m2").write_bytes(original)
    conv = tmp_path / "conv"
    conv.mkdir()
    (conv / "m.m2").write_bytes(multiconvert(original))

    r = runner.invoke(app, ["repair", str(conv / "m.m2"), "--source-m2", str(tmp_path / "orig.m2"), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)["result"]["output"]
    assert _anim_ids(open(out, "rb").read())[0] == (STAND, 0)
    assert (conv / "m.m2").read_bytes() == multiconvert(original)      # input untouched

    r = runner.invoke(app, ["repair", out, "--source-m2", str(tmp_path / "orig.m2"), "--out", str(tmp_path / "again")])
    assert r.exit_code == 0 and "Nothing to repair" in r.output
