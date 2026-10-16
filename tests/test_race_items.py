"""A race's own start outfits from BfA (items.py): its armour, textures, rows and outfits."""

import os
import struct

import pytest
from PIL import Image

from retroport.dbc import DBCFile, RawDBC
from retroport.dbclayout import DbcLayout
from retroport.formats import blp
from retroport.race import items, sql
from retroport.race.tables import ALLIANCE, RaceSpec

DBD = r"I:\WoW Modding\WoWDBDefs\definitions"
needs_dbd = pytest.mark.skipif(not os.path.isdir(DBD), reason="WoWDBDefs not found")

KT = 32
VEST, LEGS, BOOTS, ROBE, AXE = 1001, 1002, 1003, 1004, 1005


def _bfa(**extra):
    """BfA's tables for a race dressed by class: a warrior in a plate vest, legs and boots, a priest in
    a robe, a monk in the vest (no such 3.3.5 class); every outfit a weapon and a hearthstone."""
    tables = {
        "Item": {VEST: {"ClassID": 4, "SubclassID": 4, "Material": 6, "InventoryType": 5},
                 LEGS: {"ClassID": 4, "SubclassID": 4, "Material": 6, "InventoryType": 7},
                 BOOTS: {"ClassID": 4, "SubclassID": 4, "Material": 6, "InventoryType": 8},
                 ROBE: {"ClassID": 4, "SubclassID": 1, "Material": 7, "InventoryType": 20},
                 AXE: {"ClassID": 2, "SubclassID": 1, "Material": 1, "InventoryType": 17}},
        "CharStartOutfit": {
            1: {"RaceID": KT, "ClassID": 1, "SexID": 0, "ItemID": [AXE, VEST, LEGS, BOOTS, 6948] + [0] * 19},
            2: {"RaceID": KT, "ClassID": 1, "SexID": 1, "ItemID": [AXE, VEST, LEGS, BOOTS, 6948] + [0] * 19},
            3: {"RaceID": KT, "ClassID": 5, "SexID": 0, "ItemID": [ROBE, 6948] + [0] * 22},
            4: {"RaceID": KT, "ClassID": 10, "SexID": 0, "ItemID": [VEST] + [0] * 23},
            5: {"RaceID": KT, "ClassID": 6, "SexID": 0, "ItemID": [VEST] + [0] * 23}},
        "ItemSearchName": {VEST: {"Display_lang": "Warsinger's Breastplate"}, LEGS: {"Display_lang": "Warsinger's Legplates"},
                           BOOTS: {"Display_lang": "Warsinger's Sabatons"}, ROBE: {"Display_lang": "Curate's Robe"}},
        "ItemModifiedAppearance": {k: {"ItemID": i, "ItemAppearanceModifierID": 0, "ItemAppearanceID": 100 + k}
                                   for k, i in enumerate((VEST, LEGS, BOOTS, ROBE))},
        "ItemAppearance": {100 + k: {"ItemDisplayInfoID": 500 + k, "DefaultIconFileDataID": 900 + k} for k in range(4)},
        "ItemDisplayInfo": {500: {"GeosetGroup": [0, 0, 0, 0, 0, 0], "ModelResourcesID": [0, 0]},
                            501: {"GeosetGroup": [0, 3, 0, 0, 0, 0], "ModelResourcesID": [0, 0]},
                            502: {"GeosetGroup": [1, 0, 0, 0, 0, 0], "ModelResourcesID": [7, 0]},
                            503: {"GeosetGroup": [1, 0, 1, 0, 0, 0], "ModelResourcesID": [0, 0]}},
        # the vest: a male and a female chest, a race-specific female one, one sleeve for both
        "ItemDisplayInfoMaterialRes": {1: {"ItemDisplayInfoID": 500, "ComponentSection": 3, "MaterialResourcesID": 70},
                                       2: {"ItemDisplayInfoID": 500, "ComponentSection": 0, "MaterialResourcesID": 71},
                                       3: {"ItemDisplayInfoID": 501, "ComponentSection": 5, "MaterialResourcesID": 72},
                                       4: {"ItemDisplayInfoID": 502, "ComponentSection": 7, "MaterialResourcesID": 73},
                                       5: {"ItemDisplayInfoID": 503, "ComponentSection": 8, "MaterialResourcesID": 74}},
        "TextureFileData": {k: {"FileDataID": f, "UsageType": 0, "MaterialResourcesID": m}
                            for k, (f, m) in enumerate(((10, 70), (11, 70), (12, 70), (13, 71), (14, 72), (15, 73),
                                                        (16, 74)))},
        "ComponentTextureFileData": {10: {"RaceID": 0, "GenderIndex": 0}, 11: {"RaceID": 0, "GenderIndex": 1},
                                     12: {"RaceID": KT, "GenderIndex": 1}},
    }
    tables.update(extra)
    return lambda name: list(tables[name].items())


def test_the_races_armour_with_its_own_textures_by_sex():
    plan = items.collect(_bfa(), [KT, 0])
    assert plan.bfa_race == KT
    # armour only (no axe, no hearthstone), 3.3.5's classes only (no monk, the death knight's 3.3.5's)
    assert plan.kits == {(1, 0): [VEST, LEGS, BOOTS], (1, 1): [VEST, LEGS, BOOTS], (5, 0): [ROBE]}
    vest = plan.items[VEST]
    assert vest.name == "Warsinger's Breastplate" and vest.inventory_type == 5 and vest.icon == 900
    assert vest.textures == {3: {0: 10, 1: 12}, 0: {0: 13, 1: 13}}       # the race's own female chest
    assert plan.items[LEGS].geosets == [0, 3, 0]
    assert plan.items[BOOTS].dropped == ["its models (3.3.5 draws none for this slot)"]
    assert "section 8" in plan.items[ROBE].dropped[0]                    # Accessory: no 3.3.5 section


def test_chosen_kits_dress_each_class_in_them_both_sexes_alike():
    """A Pandaren's: BfA's own outfit is the same for every class (items.KITS)."""
    plan = items.collect(_bfa(), [KT, 0], {1: (VEST, LEGS, AXE), 5: (ROBE,)})
    assert plan.kits == {(1, 0): [VEST, LEGS], (1, 1): [VEST, LEGS], (5, 0): [ROBE], (5, 1): [ROBE]}
    assert sorted(plan.items) == [VEST, LEGS, ROBE]
    assert any(str(AXE) in n for n in plan.notes)                        # a weapon: left out, said so
    assert plan.items[VEST].textures == {3: {0: 10, 1: 12}, 0: {0: 13, 1: 13}}    # still the race's own


def test_a_belts_model_is_its_buckle():
    belt = 1006
    plan = items.collect(_bfa(
        Item={belt: {"ClassID": 4, "SubclassID": 1, "Material": 7, "InventoryType": 6}},
        CharStartOutfit={1: {"RaceID": KT, "ClassID": 5, "SexID": 0, "ItemID": [belt] + [0] * 23}},
        ItemModifiedAppearance={1: {"ItemID": belt, "ItemAppearanceModifierID": 0, "ItemAppearanceID": 110}},
        ItemAppearance={110: {"ItemDisplayInfoID": 510, "DefaultIconFileDataID": 910}},
        ItemDisplayInfo={510: {"GeosetGroup": [2, 0, 0, 0, 0, 0], "ModelResourcesID": [9, 0],
                               "ModelMaterialResourcesID": [75, 0]}},
        ItemDisplayInfoMaterialRes={1: {"ItemDisplayInfoID": 510, "ComponentSection": 5, "MaterialResourcesID": 76}},
        TextureFileData={1: {"FileDataID": 17, "UsageType": 0, "MaterialResourcesID": 75},
                         2: {"FileDataID": 18, "UsageType": 0, "MaterialResourcesID": 76}},
        ModelFileData={600: {"FileDataID": 600, "ModelResourcesID": 9}}), [KT, 0])
    cord = plan.items[belt]
    assert cord.buckle == (600, 17) and not cord.dropped                 # its knot, in its own texture
    assert cord.geosets[0] == 2 and cord.textures == {5: {0: 18, 1: 18}}  # and its band


def test_the_pandaren_kits_give_every_class_a_set_of_its_own():
    kits = items.KITS["Pandaren"]
    assert sorted(kits) == sorted(items.CLASSES)
    assert len({frozenset(k) for k in kits.values()}) == len(kits)
    assert not set().union(*map(set, kits.values())) & {77273, 77272, 77264, 6948}   # no bags or hearthstones


def test_a_class_bfa_has_no_outfit_for_borrows_one_of_its_armour():
    plan = items.OutfitPlan(KT, {(1, 0): [VEST], (5, 0): [ROBE], (4, 0): [LEGS]}, {})
    assert items.kit_for(plan, 2, 0) == [VEST]            # paladin: the warrior's plate
    assert items.kit_for(plan, 9, 0) == [ROBE]            # warlock: cloth
    assert items.kit_for(plan, 11, 0) == [LEGS]           # druid: leather
    assert items.kit_for(plan, 3, 0) == [VEST]            # hunter (mail): the nearest above, plate
    assert items.kit_for(plan, 1, 1) == []                # no female outfits at all


def _complete(plan):
    """Every armour's outfit for both sexes: plate (warrior), mail (hunter), leather (rogue), cloth (priest)."""
    plan.kits = {(c, s): kit for c, kit in ((1, [VEST]), (3, [BOOTS]), (4, [LEGS]), (5, [ROBE])) for s in (0, 1)}
    return plan


def test_ids_in_the_block_and_the_armour_every_wearer_has_at_level_1():
    plan = _complete(items.collect(_bfa(), [KT, 0]))
    items.assign(plan, 222000)
    assert [plan.items[i].id for i in (VEST, LEGS, BOOTS, ROBE)] == [222000, 222001, 222002, 222003]
    # the warrior's (and paladin's) plate is mail at level 1, the hunter's (and shaman's) mail
    # leather; the robe stays cloth
    assert [plan.items[i].wear for i in (VEST, BOOTS, LEGS, ROBE)] == [3, 2, 2, 1]


def test_pieces_keep_the_ids_an_earlier_build_gave_them():
    """Characters already wear them: a kit that gains pieces renumbers none it had."""
    plan = _complete(items.collect(_bfa(), [KT, 0]))
    items.assign(plan, 222000, {ROBE: 222000, LEGS: 222001, 999: 222002})
    assert [plan.items[i].id for i in (ROBE, LEGS, VEST, BOOTS)] == [222000, 222001, 222002, 222003]


def _blp_bytes(size=(8, 4), color=(200, 30, 30, 255)) -> bytes:
    return blp.encode_dxt(Image.new("RGBA", size, color))


def test_textures_under_the_races_name_one_for_both_sexes_or_one_each(tmp_path):
    plan = items.collect(_bfa(), [KT, 0])
    names = {10: "item/texturecomponents/torsouppertexture/plate_x_chest_tu_m.blp",
             12: "item/texturecomponents/torsouppertexture/plate_x_chest_tu_kt_f.blp",
             13: "item/texturecomponents/armuppertexture/plate_x_sleeve_au_u.blp"}
    vest = items.OutfitPlan(KT, plan.kits, {VEST: plan.items[VEST]})
    items.write_textures(vest, lambda f: _blp_bytes(), names, str(tmp_path), "KulTiran")
    got = sorted(o for o in vest.owns)
    assert got == [r"item\texturecomponents\armuppertexture\kultiran_plate_x_sleeve_au_u.blp",
                   r"item\texturecomponents\torsouppertexture\kultiran_plate_x_chest_tu_f.blp",
                   r"item\texturecomponents\torsouppertexture\kultiran_plate_x_chest_tu_m.blp"]
    assert vest.items[VEST].texture_names == {3: "KulTiran_plate_x_chest_tu", 0: "KulTiran_plate_x_sleeve_au"}
    # BfA's DXT textures palettized: the client paints the body's textures itself and draws a DXT
    # one green (every 3.3.5 component texture is palettized)
    for f in vest.files:
        blp.verify_blp(f, full_chain=False)
        assert blp.header(open(f, "rb").read())["encoding"] == items.PALETTIZED
    assert blp.decode(vest.files[0]).getpixel((2, 2))[:3] == pytest.approx((200, 30, 30), abs=8)


def test_a_palettized_bfa_texture_is_copied_as_it_is(tmp_path):
    data = blp.encode_palettized(Image.new("RGBA", (8, 4), (10, 120, 40, 255)))
    out = items._write_blp(str(tmp_path / "x_U.blp"), data)
    assert open(out, "rb").read() == data


def test_icons_the_clients_own_or_bfas(tmp_path):
    plan = items.collect(_bfa(), [KT, 0])
    names = {900: "interface/icons/inv_chest_plate_x.blp", 901: "interface/icons/inv_pants_plate_x.blp"}
    two = items.OutfitPlan(KT, {}, {VEST: plan.items[VEST], LEGS: plan.items[LEGS]})
    items.write_icons(two, lambda f: _blp_bytes((64, 64)), names,
                      lambda rel: rel.lower() == r"interface\icons\inv_pants_plate_x.blp", str(tmp_path))
    assert two.owns == [r"interface\icons\inv_chest_plate_x.blp"]
    assert two.items[LEGS].icon_name == "inv_pants_plate_x"
    blp.verify_blp(two.files[0], (64, 64))


@needs_dbd
def test_item_and_display_rows():
    plan = _complete(items.collect(_bfa(), [KT, 0]))
    items.assign(plan, 222000)
    vest = plan.items[VEST]
    vest.texture_names, vest.icon_name = {3: "KulTiran_plate_x_chest_tu"}, "inv_chest_plate_x"
    layouts = {t: DbcLayout(t, DBD) for t in ("Item", "ItemDisplayInfo")}
    tables = {"Item": DBCFile(8), "ItemDisplayInfo": DBCFile(25)}
    counts = items.add_items(tables, items.OutfitPlan(KT, {}, {VEST: vest}), layouts, {(6, 5): 11})
    assert counts == {"Item": 1, "ItemDisplayInfo": 1}
    assert tables["Item"].records[0] == [222000, 4, 3, 0xFFFFFFFF, 6, 222000, 5, 0]
    LD, d = layouts["ItemDisplayInfo"], tables["ItemDisplayInfo"]
    row = d.records[0]
    assert d.get_string(row[LD.at("Texture", 3)]) == "KulTiran_plate_x_chest_tu"
    assert d.get_string(row[LD.at("InventoryIcon", 0)]) == "inv_chest_plate_x"
    assert row[LD.at("GroupSoundIndex")] == 11
    assert items.strip(tables, 222000, 1000) == {"Item": 1, "ItemDisplayInfo": 1}


def _outfit(race, cls, sex, entries):
    rec = bytearray(296)
    struct.pack_into("<IBBBB", rec, 0, 1, race, cls, sex, 0)
    entries = entries + [items.EMPTY] * (24 - len(entries))
    for k, (i, d, s) in enumerate(entries):
        struct.pack_into("<i", rec, 8 + 4 * k, i)
        struct.pack_into("<i", rec, 104 + 4 * k, d)
        struct.pack_into("<i", rec, 200 + 4 * k, s)
    return bytes(rec)


def test_outfits_wear_the_armour_and_keep_weapons_and_the_rest():
    plan = items.collect(_bfa(), [KT, 0])
    items.assign(plan, 222000)
    outfits = RawDBC(74, 296)
    template = [(38, 9891, 4), (39, 9892, 7), (40, 10141, 8), (0, -1, -1), (49778, 2380, 17), (6948, 6418, 0)]
    outfits.add_record(_outfit(22, 1, 0, template))                 # warrior: plate in place of the shirt, pants, boots
    outfits.add_record(_outfit(22, 6, 0, [(34652, 1, 1)]))          # death knight: 3.3.5's own
    outfits.add_record(_outfit(1, 1, 0, template))                  # another race: untouched
    assert items.dress(outfits, [22, 23], plan) == 1
    rec = outfits.records[0]
    got = list(zip(struct.unpack_from("<24i", rec, 8), struct.unpack_from("<24i", rec, 104),
                   struct.unpack_from("<24i", rec, 200)))
    assert got[:6] == [(222000, 222000, 5), (222001, 222001, 7), (222002, 222002, 8), (49778, 2380, 17),
                       (6948, 6418, 0), items.EMPTY]
    assert outfits.records[1] == _outfit(22, 6, 0, [(34652, 1, 1)]) and outfits.records[2] == _outfit(1, 1, 0, template)


def test_item_template_rows_in_the_race_sql():
    rows = [sql.StartItem(222000, "Warsinger's Breastplate", 3, 6, 5, 3, 35)]
    install, undo = sql.race_sql([RaceSpec(22, ALLIANCE, 1)], "Kul Tiran", {}, None, rows)
    assert "DELETE FROM `item_template` WHERE `entry` IN (222000);" in install
    assert "(222000, 4, 3, -1, 'Warsinger\\'s Breastplate', 222000, 1, 5, 1, 5, -1, -1, 1, 1, 1, 3, 6, 35);" in install
    assert "DELETE FROM `item_template` WHERE `entry` IN (222000);" in undo
