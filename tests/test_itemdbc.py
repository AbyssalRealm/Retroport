"""ItemDisplayInfo / Item.dbc writer (synthetic 3.3.5a-layout DBCs)."""

import json

import pytest
from typer.testing import CliRunner

from retroport.cli import app
from retroport.dbc import DBCError, DBCFile
from retroport.itemdbc import (
    ITEM_DISPLAY_INFO_FIELDS as IDI, ITEM_FIELDS, model_folder, register_item_display, suggest_from_model,
)

from test_dbc import _wdbc


def _item_dbcs(tmp_path):
    """ItemDisplayInfo with one Blizzard-style 2H axe (id 782), Item.dbc with two items."""
    d = tmp_path / "dbc"; d.mkdir()
    strings = b"\x00Axe_2H_Battle_B_01.mdx\x00Axe_2H_Battle_B_01\x00INV_Axe_04\x00"
    axe = [0] * IDI
    axe[0], axe[1], axe[3], axe[5], axe[12] = 782, 1, 24, 43, 9      # offsets into `strings`
    _wdbc(str(d / "ItemDisplayInfo.dbc"), IDI, [axe], strings)
    _wdbc(str(d / "Item.dbc"), ITEM_FIELDS, [[100, 2, 1, 0xFFFFFFFF, 1, 782, 17, 1],
                                             [200, 2, 1, 0xFFFFFFFF, 1, 782, 17, 1]])
    return d


def test_clone_display_overrides_strings_and_keeps_numbers(tmp_path):
    d = _item_dbcs(tmp_path)
    r = register_item_display(str(d), str(tmp_path / "out"), template_display_id=782,
                              model="Axe_2H_New.mdx", texture="Axe_2H_New_Dark.blp", icon="INV_Axe_New")
    assert r.display_id == 783
    idi = DBCFile.load(r.display_dbc)
    row = idi.find_by_id(783)
    assert [idi.get_string(row[i]) for i in (1, 3, 5)] == ["Axe_2H_New.mdx", "Axe_2H_New_Dark", "INV_Axe_New"]
    assert row[12] == 9                                       # GroupSoundIndex inherited from the axe
    old = idi.find_by_id(782)
    assert idi.get_string(old[1]) == "Axe_2H_Battle_B_01.mdx"   # template row untouched
    assert r.pack[0] == "Item\\ObjectComponents\\Weapon\\Axe_2H_New.m2 (+ Axe_2H_New00.skin)"
    assert "Item\\ObjectComponents\\Weapon\\Axe_2H_New_Dark.blp" in r.pack
    assert r.item_dbc is None                                 # no entries -> Item.dbc untouched


def test_item_dbc_repoint_add_and_missing(tmp_path):
    d = _item_dbcs(tmp_path)
    r = register_item_display(str(d), str(tmp_path / "out"), model="Axe_2H_New.mdx",
                              item_entries="200, 5000", item_like=100)
    assert r.items_updated == [200] and r.items_added == [5000]
    item = DBCFile.load(r.item_dbc)
    assert item.find_by_id(200)[5] == r.display_id
    assert item.find_by_id(5000) == [5000, 2, 1, 0xFFFFFFFF, 1, r.display_id, 17, 1]   # cloned from 100
    assert item.find_by_id(100)[5] == 782                                            # template unchanged
    assert "WHERE entry IN (200, 5000)" in r.sql

    r2 = register_item_display(str(d), str(tmp_path / "out2"), model="Axe_2H_New.mdx", item_entries="6000")
    assert r2.items_missing == [6000] and r2.item_dbc is None
    assert "creature_equip_template will drop them" in r2.sql


def test_component_armor_and_validation(tmp_path):
    d = _item_dbcs(tmp_path)
    r = register_item_display(str(d), str(tmp_path / "out"), icon="INV_Chest_New",
                              component_textures={"TorsoUpper": "New_Chest_TU", "ArmUpper": "New_Sleeve_AU"},
                              geoset_groups=(1, 0, 1))
    idi = DBCFile.load(r.display_dbc)
    row = idi.find_by_id(r.display_id)
    assert idi.get_string(row[18]) == "New_Chest_TU" and idi.get_string(row[15]) == "New_Sleeve_AU"
    assert row[7:10] == [1, 0, 1]
    assert "Item\\TextureComponents\\TorsoUpperTexture\\New_Chest_TU_U.blp  (or _M / _F per gender)" in r.pack

    out = str(tmp_path / "bad")
    with pytest.raises(DBCError, match="bare name"):
        register_item_display(str(d), out, model="Item\\ObjectComponents\\Weapon\\a.mdx")
    with pytest.raises(DBCError, match="ending in .mdx"):
        register_item_display(str(d), out, model="Axe_2H_New.blp")
    with pytest.raises(DBCError, match="unknown component section"):
        register_item_display(str(d), out, component_textures={"Chest": "x"})
    with pytest.raises(DBCError, match="invisible"):
        register_item_display(str(d), out, icon="INV_Only")
    with pytest.raises(DBCError, match="same as the input"):
        register_item_display(str(d), str(d), model="Axe_2H_New.mdx")
    with pytest.raises(DBCError, match="not in ItemDisplayInfo"):
        register_item_display(str(d), out, template_display_id=1, model="Axe_2H_New.mdx")


def test_model_folder_guess():
    assert model_folder("Helm_Plate_D_01.mdx") == "Head"
    assert model_folder("LShoulder_Mail_C_01.mdx") == "Shoulder"
    assert model_folder("Shield_Crest_B_01.mdx") == "Shield"
    assert model_folder("Axe_2H_Battle_B_01.mdx") == "Weapon"


def test_suggest_from_model_uses_type2_slot_texture(tmp_path):
    from test_texbake import build_md20_textures
    (tmp_path / "axe_new.m2").write_bytes(build_md20_textures([(0, "x\\y.blp"), (2, "")]))
    (tmp_path / "axe_new.manifest.json").write_text(json.dumps(
        {"textures": [{"fileDataID": 1, "file": "reflect.blp"}, {"fileDataID": 2, "file": "axe_new_dark.blp"}]}))
    assert suggest_from_model(str(tmp_path / "axe_new.m2")) == {"model": "axe_new.mdx", "texture": "axe_new_dark"}


def test_cli_register_item(tmp_path):
    d = _item_dbcs(tmp_path)
    r = CliRunner().invoke(app, ["register-item", "--dbc-dir", str(d), "--out", str(tmp_path / "out"),
                                 "--like-display", "782", "--model", "Axe_2H_New.mdx", "--texture", "Axe_2H_New",
                                 "--entries", "100", "--json"])
    assert r.exit_code == 0, r.output
    res = json.loads(r.output)["result"]
    assert res["display_id"] == 783 and res["items_updated"] == [100]
    assert res["row"]["GroupSoundIndex"] == 9
