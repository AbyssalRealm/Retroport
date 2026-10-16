"""DBC engine + creature-registration tests (synthetic WDBC files)."""

import struct

import pytest

from retroport.dbc import (
    CREATURE_DISPLAY_INFO_FIELDS as DI_FIELDS,
    CREATURE_MODEL_DATA_FIELDS as MD_FIELDS,
    DBCError, DBCFile, float_to_u32, register_creature_model, set_texture_variations, u32_to_float,
)
from retroport.sql import build_name_search_sql, build_repoint_sql_block, SQLInputError


def _wdbc(path, field_count, records, string_block=b"\x00"):
    record_size = field_count * 4
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sIIII", b"WDBC", len(records), field_count,
                             record_size, len(string_block)))
        for r in records:
            fh.write(struct.pack(f"<{field_count}I", *r))
        fh.write(string_block)


def _row(n, rec_id, **fields):
    r = [0] * n
    r[0] = rec_id
    for k, v in fields.items():
        r[int(k[1:])] = v
    return r


def _creature_dbcs(tmp_path, md_ids=(10,), di_ids=(20,)):
    d = tmp_path / "dbc"; d.mkdir()
    _wdbc(str(d / "CreatureModelData.dbc"), MD_FIELDS, [_row(MD_FIELDS, i) for i in md_ids])
    _wdbc(str(d / "CreatureDisplayInfo.dbc"), DI_FIELDS, [_row(DI_FIELDS, i) for i in di_ids])
    return d


def test_dbc_roundtrip_and_strings(tmp_path):
    p = tmp_path / "T.dbc"
    _wdbc(str(p), 3, [[1, 2, 3], [4, 5, 6]])
    d = DBCFile.load(str(p))
    assert d.max_id() == 4 and d.next_free_id() == 5
    off = d.add_string("hello")
    d.add_record([7, off, 0])
    out = tmp_path / "o.dbc"
    d.save(str(out))

    d2 = DBCFile.load(str(out))
    assert len(d2.records) == 3
    assert d2.get_string(d2.records[-1][1]) == "hello"


def test_a_row_stripped_and_added_again_leaves_the_file_as_it_was(tmp_path):
    p = tmp_path / "T.dbc"
    _wdbc(str(p), 2, [[1, 1], [2, 7]], b"\x00Human\x00KulTiran\x00")
    d = DBCFile.load(str(p))
    del d.records[1]
    d.add_record([2, d.add_string("KulTiran")])
    assert d.to_bytes() == p.read_bytes()                    # its string is shared, not copied
    assert d.add_string("Dwarf") == 16 and d.add_string("Dwarf") == 16


def test_next_free_id_with_floor_skips_used():
    d = DBCFile(1)
    for i in (100, 101, 103):
        d.add_record([i])
    assert d.next_free_id(100) == 102
    assert d.next_free_id(50) == 50
    assert d.next_free_id() == 104


def test_rejects_bad_dbcs(tmp_path):
    bad = tmp_path / "bad.dbc"
    bad.write_bytes(b"XXXX" + b"\x00" * 16)
    with pytest.raises(DBCError, match="not a WDBC"):
        DBCFile.load(str(bad))
    trunc = tmp_path / "trunc.dbc"
    trunc.write_bytes(struct.pack("<4sIIII", b"WDBC", 5, 2, 8, 1))   # claims 5 records, has none
    with pytest.raises(DBCError, match="truncated"):
        DBCFile.load(str(trunc))
    with pytest.raises(DBCError, match="fields"):
        DBCFile(3).add_record([1, 2])


def test_register_creature_model(tmp_path):
    dbc_dir = _creature_dbcs(tmp_path)
    out_dir = tmp_path / "out"
    r = register_creature_model(str(dbc_dir), str(out_dir), "Creature/Foo/foo.m2", scale=1.5)
    assert r.model_id == 11 and r.display_id == 21
    assert r.model_name == "Creature\\Foo\\foo.m2"          # forward slashes normalised
    assert "creature_model_info" in r.sql and "creature_template_model" in r.sql

    md = DBCFile.load(str(out_dir / "CreatureModelData.dbc"))
    new = md.records[-1]
    assert new[0] == 11 and md.get_string(new[2]) == "Creature\\Foo\\foo.m2"
    assert u32_to_float(new[4]) == 1.5                      # ModelScale
    assert u32_to_float(new[14]) == pytest.approx(0.6)      # CollisionWidth is not 0

    di = DBCFile.load(str(out_dir / "CreatureDisplayInfo.dbc"))
    ndi = di.records[-1]
    assert ndi[0] == 21 and ndi[1] == 11                    # displayId -> its modelId
    assert ndi[4] == float_to_u32(1.0)
    assert ndi[5] == 255                                    # regression: alpha 0 = invisible creature


def test_register_floors_texvars_bounds_and_sql(tmp_path):
    dbc_dir = _creature_dbcs(tmp_path, md_ids=(10, 500), di_ids=(20, 900, 901))
    r = register_creature_model(
        str(dbc_dir), str(tmp_path / "out"), "Creature\\Foo\\foo.m2",
        display_floor=900, modeldata_floor=500,
        texture_variations=("sl_skin", "sl_armor", ""), geoset_data=0x0101,
        bounds=((-2.0, -3.0, 0.0), (2.0, 3.0, 4.0)),
        creature_entries="1234, 5678", search_term="Vol'jin")
    assert r.display_id == 902 and r.model_id == 501        # first free at/above each floor

    md = DBCFile.load(r.model_dbc)
    rec = md.find_by_id(501)
    assert [round(u32_to_float(v), 3) for v in rec[17:23]] == [-2.0, -3.0, 0.0, 2.0, 3.0, 4.0]
    di = DBCFile.load(r.display_dbc)
    drec = di.find_by_id(902)
    assert di.get_string(drec[6]) == "sl_skin" and di.get_string(drec[7]) == "sl_armor"
    assert drec[8] == 0 and drec[14] == 0x0101
    assert "Vol\\'jin" in r.sql                             # apostrophe escaped
    assert "WHERE CreatureID IN (1234, 5678)" in r.sql


def test_register_refuses_wrong_build_and_overwrite(tmp_path):
    d = tmp_path / "dbc"; d.mkdir()
    _wdbc(str(d / "CreatureModelData.dbc"), 5, [[10, 0, 0, 0, 0]])     # not a 3.3.5 layout
    _wdbc(str(d / "CreatureDisplayInfo.dbc"), DI_FIELDS, [_row(DI_FIELDS, 20)])
    with pytest.raises(DBCError, match="expected 28 fields"):
        register_creature_model(str(d), str(tmp_path / "out"), "Creature\\Foo\\foo.m2")

    base = tmp_path / "good"; base.mkdir()
    good = _creature_dbcs(base)
    with pytest.raises(DBCError, match="same as the input"):
        register_creature_model(str(good), str(good), "Creature\\Foo\\foo.m2")
    with pytest.raises(DBCError, match="not an in-game path"):
        register_creature_model(str(good), str(tmp_path / "o2"), "C:\\export\\foo.m2")
    with pytest.raises(DBCError, match="bare name"):
        register_creature_model(str(good), str(tmp_path / "o3"), "Creature\\Foo\\foo.m2",
                                texture_variations=("Creature\\x\\sl_skin.blp", "", ""))


def test_set_texture_variations(tmp_path):
    dbc_dir = _creature_dbcs(tmp_path, di_ids=(20, 30))
    r = set_texture_variations(str(dbc_dir / "CreatureDisplayInfo.dbc"), str(tmp_path / "out"),
                               30, "a", "b", "c", geoset=7)
    di = DBCFile.load(r["output_path"])
    rec = di.find_by_id(30)
    assert [di.get_string(rec[i]) for i in (6, 7, 8)] == ["a", "b", "c"] and rec[14] == 7
    with pytest.raises(DBCError, match="not found"):
        set_texture_variations(str(dbc_dir / "CreatureDisplayInfo.dbc"), str(tmp_path / "out"),
                               99, "a", "", "")


def test_sql_helpers():
    assert build_name_search_sql("Vol'jin").endswith("LIKE '%Vol\\'jin%';")
    block = build_repoint_sql_block("", "42", 90013)
    assert "no name search term" in block
    assert "SET CreatureDisplayID = 90013 WHERE CreatureID IN (42)" in block
    with pytest.raises(SQLInputError):
        build_repoint_sql_block("", "12, abc", 1)
