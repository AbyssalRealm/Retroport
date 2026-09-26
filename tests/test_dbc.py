"""DBC engine + creature-registration tests (synthetic WDBC files)."""

import struct

from retroport.dbc import DBC, register_creature_model


def _wdbc(path, field_count, records, string_block=b"\x00"):
    record_size = field_count * 4
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sIIII", b"WDBC", len(records), field_count,
                             record_size, len(string_block)))
        for r in records:
            fh.write(struct.pack(f"<{field_count}I", *r))
        fh.write(string_block)


def test_dbc_roundtrip_and_strings(tmp_path):
    p = tmp_path / "T.dbc"
    _wdbc(str(p), 3, [[1, 2, 3], [4, 5, 6]])
    d = DBC(str(p))
    assert d.max_id() == 4
    off = d.append_string("hello")
    d.add_record([7, off, 0])
    out = tmp_path / "o"
    d.save(str(out))

    d2 = DBC(str(out / "T.dbc"))
    assert d2.record_count == 3
    assert d2.get_string(d2.unpack(d2.records[-1])[1]) == "hello"


def test_register_creature_model(tmp_path):
    dbc_dir = tmp_path / "dbc"; dbc_dir.mkdir()
    out_dir = tmp_path / "out"
    _wdbc(str(dbc_dir / "CreatureModelData.dbc"), 5, [[10, 0, 0, 0, 0]])
    _wdbc(str(dbc_dir / "CreatureDisplayInfo.dbc"), 5, [[20, 0, 0, 0, 0]])

    r = register_creature_model(str(dbc_dir), str(out_dir), "Creature\\Foo\\foo.m2", scale=1.0)
    assert r.model_id == 11 and r.display_id == 21
    assert "21" in r.sql and "creature_model_info" in r.sql

    cmd = DBC(str(out_dir / "CreatureModelData.dbc"))
    assert cmd.record_count == 2
    new = cmd.unpack(cmd.records[-1])
    assert new[0] == 11
    assert cmd.get_string(new[2]) == "Creature\\Foo\\foo.m2"

    cdi = DBC(str(out_dir / "CreatureDisplayInfo.dbc"))
    assert cdi.record_count == 2
    ndi = cdi.unpack(cdi.records[-1])
    assert ndi[0] == 21 and ndi[1] == 11        # displayId -> its modelId
