"""A race's start weapons, shields and off-hands from BfA (items.py: held pieces)."""

import os
import struct

import pytest

from retroport.dbc import DBCFile, RawDBC
from retroport.dbclayout import DbcLayout
from retroport.race import items, sql
from retroport.race.tables import ALLIANCE, RaceSpec

PA = 24
DBD = r"I:\WoW Modding\WoWDBDefs\definitions"
needs_dbd = pytest.mark.skipif(not os.path.isdir(DBD), reason="WoWDBDefs not found")
STAFF, CROSSBOW, DAGGER, SHIELD, BOOK, ROBE, AXE, DAGGER2 = 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008


def _bfa():
    """BfA's tables: a staff, a crossbow, a dagger, a shield, a book (held in the off hand), a robe."""
    rows = {STAFF: (2, 10, 17, 2, 2, "Trainee's Staff", 9001), CROSSBOW: (2, 18, 26, 2, 5, "Trainee's Crossbow", 9002),
            DAGGER: (2, 15, 13, 1, 3, "Trainee's Dagger", 9003), SHIELD: (4, 6, 14, 6, 4, "Trainee's Shield", 9004),
            BOOK: (4, 0, 23, 8, 3, "Trainee's Book of Prayers", 9005), ROBE: (4, 1, 20, 7, 0, "Initiate's Robes", 9006),
            AXE: (2, 0, 13, 1, 3, "Trainee's Axe", 9007), DAGGER2: (2, 15, 13, 1, 3, "Trainee's Dagger", 9003)}
    tables = {
        "Item": {i: {"ClassID": c, "SubclassID": s, "InventoryType": inv, "Material": m, "SheatheType": sh}
                 for i, (c, s, inv, m, sh, _n, _d) in rows.items()},
        "CharStartOutfit": {},
        "ItemSearchName": {i: {"Display_lang": n} for i, (*_x, n, _d) in rows.items()},
        "ItemModifiedAppearance": {k: {"ItemID": i, "ItemAppearanceModifierID": 0, "ItemAppearanceID": d}
                                   for k, (i, (*_x, d)) in enumerate(rows.items())},
        "ItemAppearance": {d: {"ItemDisplayInfoID": d, "DefaultIconFileDataID": d + 100} for *_x, d in rows.values()},
        "ItemDisplayInfo": {d: {"GeosetGroup": [0] * 6, "ModelResourcesID": [0 if i == ROBE else d, 0],
                                "ModelMaterialResourcesID": [0 if i == ROBE else d, 0]}
                            for i, (*_x, d) in rows.items()},
        "ItemDisplayInfoMaterialRes": {},
        "TextureFileData": {k: {"FileDataID": d + 500, "UsageType": 0, "MaterialResourcesID": d}
                            for k, (*_x, d) in enumerate(rows.values())},
        "ComponentTextureFileData": {},
        "ModelFileData": {d + 700: {"FileDataID": d + 700, "ModelResourcesID": d} for *_x, d in rows.values()},
    }
    return lambda name: list(tables[name].items())


def _plan():
    plan = items.collect(_bfa(), [PA, 0], {3: (ROBE, AXE, CROSSBOW), 4: (DAGGER, DAGGER2), 5: (BOOK, STAFF),
                                           7: (SHIELD,), 8: (STAFF,)})
    items.assign(plan, 224000)
    return plan


def test_a_chosen_kit_holds_weapons_a_shield_and_an_off_hand_with_their_models():
    plan = _plan()
    staff, crossbow, shield, book = (plan.items[i] for i in (STAFF, CROSSBOW, SHIELD, BOOK))
    assert staff.held and crossbow.held and shield.held and book.held and not plan.items[ROBE].held
    assert staff.model == (9701, 9501) and staff.item_class == items.WEAPON and staff.sheathe == 2
    assert not staff.dropped and not plan.items[ROBE].model
    # a shield and an off-hand stay what they are; body armour still drops to what every wearer has
    assert (shield.wear, book.wear, staff.wear) == (6, 0, 10)
    assert plan.items[ROBE].wear == 1


def test_held_models_go_where_3_3_5_keeps_them_under_the_races_name(tmp_path, monkeypatch):
    plan = _plan()
    names = {9701: "item/objectcomponents/weapon/stave_2h_panstart_a_01.m2", 9501: "item/objectcomponents/weapon/stave_blue.blp",
             9704: "item/objectcomponents/shield/shield_panstart_a_01.m2", 9504: "item/objectcomponents/shield/shield_yellow.blp"}
    monkeypatch.setattr(items.blp, "verify_blp", lambda path: None)
    got = items.held_models(plan, lambda fdid: b"BLP2", names, str(tmp_path), "Pandaren")
    assert got == {"Pandaren_stave_2h_panstart_a_01": (items.WEAPON_DIR, 9701),
                   "Pandaren_shield_panstart_a_01": (items.SHIELD_DIR, 9704)}
    staff, shield = plan.items[STAFF], plan.items[SHIELD]
    assert (staff.model_name, staff.model_texture) == ("Pandaren_stave_2h_panstart_a_01.mdx", "Pandaren_stave_blue")
    assert (tmp_path / "Item" / "ObjectComponents" / "Shield" / "Pandaren_shield_yellow.blp").is_file()
    assert any("not in the listfile" in d for d in plan.items[CROSSBOW].dropped)     # no names for it here


def _outfit(race, cls, sex, entries):
    rec = bytearray(296)
    struct.pack_into("<IBBBB", rec, 0, 1, race, cls, sex, 0)
    entries = entries + [items.EMPTY] * (24 - len(entries))
    for k, (i, d, s) in enumerate(entries):
        struct.pack_into("<i", rec, 8 + 4 * k, i)
        struct.pack_into("<i", rec, 104 + 4 * k, d)
        struct.pack_into("<i", rec, 200 + 4 * k, s)
    return bytes(rec)


def _entries(rec):
    return [e for e in zip(struct.unpack_from("<24i", rec, 8), struct.unpack_from("<24i", rec, 104),
                           struct.unpack_from("<24i", rec, 200)) if e[0] > 0]


ITEM_ROWS = {2516: [2516, 6, 3, -1, 2, 5998, 24, 0], 2102: [2102, 11, 3, -1, -1, 1816, 18, 0],
             2512: [2512, 6, 2, -1, 2, 5996, 24, 0], 2101: [2101, 11, 2, -1, -1, 21328, 18, 0]}


def test_held_pieces_take_the_templates_hands_and_a_crossbow_its_arrows():
    plan = _plan()
    outfits = RawDBC(74, 296)
    # a hunter: battleaxe, blunderbuss, shot and pouch; a priest: staff; a shaman: mace and shield; a rogue
    outfits.add_record(_outfit(PA, 3, 0, [(12282, 22291, 17), (2508, 6606, 26), (2516, 5998, 24), (2102, 1816, 18),
                                          (6948, 6418, 0)]))
    outfits.add_record(_outfit(PA, 5, 0, [(35, 472, 17), (6948, 6418, 0)]))
    outfits.add_record(_outfit(PA, 7, 0, [(36, 5194, 21), (2362, 18730, 14), (6948, 6418, 0)]))
    outfits.add_record(_outfit(PA, 4, 0, [(2092, 6442, 13), (50055, 6442, 22), (28979, 40187, 25), (6948, 6418, 0)]))
    assert items.dress(outfits, [PA], plan, ITEM_ROWS) == 4
    i = {b: plan.items[b].id for b in plan.items}
    hunter, priest, shaman, rogue = (_entries(r) for r in outfits.records)
    assert hunter == [(i[ROBE], i[ROBE], 20), (i[AXE], i[AXE], 13), (i[CROSSBOW], i[CROSSBOW], 26),
                      (2512, 5996, 24), (2101, 21328, 18), (6948, 6418, 0)]
    # the create screen draws an entry by its type: a held book (23) not at all, so it goes as an
    # off-hand weapon (22) - the server reads only the item ids
    assert priest == [(i[BOOK], i[BOOK], 22), (i[STAFF], i[STAFF], 17), (6948, 6418, 0)]
    # a shield alone takes the off hand: the template's mace stays in the main
    assert shaman == [(i[SHIELD], i[SHIELD], 14), (36, 5194, 21), (6948, 6418, 0)]
    # two one-handers: both hands (the second drawn in the left at create: 22); the thrown knife stays
    assert rogue == [(i[DAGGER], i[DAGGER], 13), (i[DAGGER2], i[DAGGER2], 22), (28979, 40187, 25), (6948, 6418, 0)]


def test_held_pieces_sheathe_as_3_3_5s_own_and_carry_its_start_weapon_stats():
    plan = _plan()
    client_items = DBCFile(8)
    for k, (cls, sub, inv, sheath) in enumerate([(2, 18, 26, 0), (2, 18, 26, 0), (2, 18, 26, 1), (4, 0, 23, 7),
                                                 (4, 1, 20, 0)]):
        client_items.add_record([100 + k, cls, sub, -1, 1, 1, inv, sheath])
    items.adopt_kinds(plan, client_items)
    assert (plan.items[CROSSBOW].sheathe, plan.items[BOOK].sheathe, plan.items[STAFF].sheathe) == (0, 7, 2)
    assert plan.items[ROBE].sheathe == 0
    bow = items.server_stats(plan.items[CROSSBOW])
    assert (bow["dmg_min"], bow["dmg_max"], bow["delay"], bow["ammo_type"], bow["ranged_mod_range"]) == (5, 10, 2300, 2, 100)
    assert items.server_stats(plan.items[SHIELD]) == {"armor": 5, "durability": 20, "block": 1, "sheath": 4}
    rows = [sql.StartItem(it.id, it.name, it.wear, it.material, it.inventory_type, item_class=it.item_class,
                          **items.server_stats(it)) for it in (plan.items[STAFF], plan.items[ROBE])]
    install, _undo = sql.race_sql([RaceSpec(PA, ALLIANCE, 1)], "Pandaren", {}, None, rows)
    staff = plan.items[STAFF].id
    assert (f"({staff}, 2, 10, -1, 'Trainee\\'s Staff', {staff}, 1, 5, 1, 17, -1, -1, 1, 1, 1, 3, 5, 0, 0, 2900, 0, 0, "
            "2, 2, 0, 25);") in install
    robe = plan.items[ROBE].id
    assert f"({robe}, 4, 1, -1, 'Initiate\\'s Robes', {robe}, 1, 5, 1, 20, -1, -1, 1, 1, 1, 3, 7, 35);" in install


@needs_dbd
def test_a_ranged_weapon_takes_3_3_5s_shot_visual_for_its_kind():
    """3.3.5 animates Auto Shot through the weapon's ItemDisplayInfo.SpellVisualID (a crossbow's 743:
    LoadRifle, then AttackRifle) - none and the hunter shoots standing still."""
    plan = _plan()
    LI, LD = (DbcLayout(t, DBD) for t in ("Item", "ItemDisplayInfo"))
    client_items, displays = DBCFile(8), DBCFile(LD.field_count)
    for k, (cls, sub, inv, visual, sound) in enumerate([(2, 18, 26, 743, 12), (2, 18, 26, 743, 12), (2, 18, 26, 0, 8),
                                                      (4, 6, 14, 0, 9), (4, 0, 23, 0, 21), (4, 1, 20, 0, 1)]):
        client_items.add_record([100 + k, cls, sub, -1, 1, 500 + k, inv, 0])
        row = LD.blank()
        LD.set(row, "ID", 500 + k)
        LD.set(row, "SpellVisualID", visual)
        LD.set(row, "GroupSoundIndex", sound)
        displays.add_record(row)
    items.adopt_kinds(plan, client_items, displays, LD)
    crossbow, shield, book, robe = (plan.items[i] for i in (CROSSBOW, SHIELD, BOOK, ROBE))
    assert (crossbow.visual, crossbow.sound_group) == (743, 12)
    assert (shield.visual, shield.sound_group, book.sound_group) == (0, 9, 21)
    assert (robe.visual, robe.sound_group) == (0, 0)             # body armour: sound groups by material
    tables = {"Item": DBCFile(LI.field_count), "ItemDisplayInfo": DBCFile(LD.field_count)}
    items.add_items(tables, plan, {"Item": LI, "ItemDisplayInfo": LD})
    row = next(r for r in tables["ItemDisplayInfo"].records if r[0] == crossbow.id)
    assert (row[LD.at("SpellVisualID")], row[LD.at("GroupSoundIndex")]) == (743, 12)
