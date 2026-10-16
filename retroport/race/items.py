"""A race's own start outfits from BfA: the armour BfA's character creation dresses it in.

A 3.3.5 start outfit (CharStartOutfit) names items; an item (Item.dbc, the server's item_template)
names a display (ItemDisplayInfo) whose textures the client paints into the sections of the body
texture and whose geoset groups pick the body's sleeves, boots, belt and so on. The template
race's start armour (a Human's, an Orc's) is 3.3.5's: 128x64 a section, drawn at twice its size
into a BfA body's 512x512 skin and painted for 3.3.5's own body UVs - blurred and stretched on a
Pandaren. BfA's start armour was painted for these bodies, at their resolution (256x128 a
section: the HD layout's, layouts.HD_335).

So for each class BfA's CharStartOutfit dresses the race in (a Kul Tiran warrior: Warsinger's
plate), or ``KITS`` names for it (a Pandaren: BfA dresses every class alike, see there), each
piece drawn on the body (``BODY_SLOTS``) becomes a 3.3.5 item of its own in the race's id block
(racials.block_base: 222000 on for a Kul Tiran), item and display the same id: BfA's textures for
the race and each sex (ItemDisplayInfoMaterialRes -> TextureFileData, ComponentTextureFileData)
copied under the race's name (palettized: the client paints the body texture itself and draws a
DXT one green), BfA's first three geoset groups (the ones 3.3.5 items set: sleeves, chest, robe
for a chest; boots for feet; ...), BfA's name and icon. The race's outfits wear them in
place of the template's armour; a class BfA has no outfit for borrows one of its armour
(``kit_for``: a Kul Tiran paladin, the warrior's plate); a death knight keeps 3.3.5's own (BfA's
is the same). Weapons stay the template's - 3.3.5's start weapon skills are the template's - but
where ``KITS`` names BfA's own for a class a level 1 character of it can wield (a Pandaren's
Trainee's): those take the hands they need (``_gone``; a crossbow's arrows in place of a gun's
shot), their models converted as the helmets are (``held_models``, helmets.convert_item_models),
sheathed as 3.3.5's own of their kind, with 3.3.5's start weapons' damage and speed.

The items are start gear as 3.3.5's are: white, item level 1, of the armour every class wearing
one has at level 1 (``LEVEL1_ARMOUR``: a warrior's plate is mail until 40) - item_template rows in
the race's SQL (sql.py). A belt's model (a Pandaren cord's knot) is drawn as part of the body
(buckles.py); other pieces' models are not carried.
"""

from __future__ import annotations

import os
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from ..dbc import DBCFile, RawDBC
from ..dbclayout import DbcLayout
from ..formats import blp
from ..safeio import write_verified

# the BfA tables it reads
DB2 = ("charstartoutfit", "item", "itemsearchname", "itemmodifiedappearance", "itemappearance", "itemdisplayinfo",
       "itemdisplayinfomaterialres", "texturefiledata", "componenttexturefiledata", "modelfiledata")
ARMOUR = 4                                          # ItemClass
BODY_SLOTS = {4, 5, 6, 7, 8, 9, 10, 19, 20}         # InventoryType: shirt, chest, waist, legs, feet, wrists, hands, tabard, robe
REPLACED_SLOTS = BODY_SLOTS | {1, 3, 16}            # the template's armour an outfit gives up (head, shoulders, back too)
SECTION_FOLDERS = {0: "ArmUpperTexture", 1: "ArmLowerTexture", 2: "HandTexture", 3: "TorsoUpperTexture",
                   4: "TorsoLowerTexture", 5: "LegUpperTexture", 6: "LegLowerTexture", 7: "FootTexture"}
TEXTURE_DIR = "Item\\TextureComponents\\"
ICON_DIR = "Interface\\Icons\\"
MALE, FEMALE, BOTH = 0, 1, 3                        # ComponentTextureFileData.GenderIndex
PALETTIZED = 1                                      # BLP2 encoding: what 3.3.5 component textures are
DEATH_KNIGHT = 6
CLASSES = (1, 2, 3, 4, 5, 7, 8, 9, 11)              # 3.3.5's, death knight aside
# armour subclass: 1 cloth, 2 leather, 3 mail, 4 plate - what a 3.3.5 class wears at level 1, and
# at its best (the outfit a class BfA has none for borrows: the nearest above, then below)
LEVEL1_ARMOUR = {1: 3, 2: 3, 3: 2, 4: 2, 5: 1, 7: 2, 8: 1, 9: 1, 11: 2}
BEST_ARMOUR = {1: 4, 2: 4, 3: 3, 4: 2, 5: 1, 7: 3, 8: 1, 9: 1, 10: 2, 11: 2}
# 3.3.5's start armour: armour and durability by slot (Recruit's Pants 2/25, Neophyte's Robe 3/35)
ARMOUR_VALUE = {5: 3, 20: 3, 7: 2, 8: 1, 10: 1, 6: 1, 9: 1}
DURABILITY = {5: 35, 20: 35, 7: 25}
# CharStartOutfit (296 bytes): uint32 ID, uint8 race, class, sex, outfit; 24 items, 24 displays, 24 slots
OUTFIT_ITEMS, OUTFIT_DISPLAYS, OUTFIT_SLOTS, SLOTS = 8, 104, 200, 24
EMPTY = (-1, -1, -1)
WAIST = 6
_SEX_SUFFIX = re.compile(r"_[umf]$", re.IGNORECASE)

# held pieces: weapons (ItemClass 2) and the armour a hand holds - a shield, an off-hand frill
WEAPON = 2
SHIELD, HELD_OFF_HAND, TWO_HAND = 14, 23, 17
HELD_SLOTS = {SHIELD, HELD_OFF_HAND}
MAIN_HAND, OFF_HAND, RANGED = {13, 17, 21}, {14, 22, 23}, {15, 25, 26}     # InventoryType
ONE_HAND, OFF_HAND_WEAPON = 13, 22
WEAPON_DIR, SHIELD_DIR = "Item\\ObjectComponents\\Weapon\\", "Item\\ObjectComponents\\Shield\\"
# 3.3.5's own start weapons (item_template): damage, speed (ms) and durability by weapon subclass -
# Worn Axe, Worn Battleaxe, Worn Shortbow, Old Blunderbuss, Worn Mace, Battleworn Hammer, Worn
# Shortsword, Worn Greatsword, Bent Staff, Worn Dagger (a crossbow as the bow)
STARTER_WEAPONS = {0: (1, 3, 2000, 20), 1: (3, 5, 2900, 25), 2: (5, 10, 2300, 20), 3: (5, 10, 2300, 20),
                   4: (1, 3, 1900, 20), 5: (3, 5, 2900, 25), 7: (1, 3, 1900, 20), 8: (3, 5, 2900, 25),
                   10: (3, 5, 2900, 25), 15: (1, 2, 1600, 16), 18: (5, 10, 2300, 20)}
SHIELD_STATS = (5, 1, 20)                           # Worn Wooden Shield: armour, block, durability
AMMO_TYPE = {2: 2, 18: 2, 3: 3}                     # what a bow, crossbow, gun shoots: arrows, bullets
# 3.3.5's start ammo and what carries it, by ammo type: Rough Arrow and Light Quiver, Light Shot
# and Small Ammo Pouch (a hunter given a crossbow in place of a gun takes arrows)
AMMO_ITEMS = {2: (2512, 2101), 3: (2516, 2102)}
PROJECTILE, QUIVER = 6, 11                          # ItemClass

# BfA armour by class (BfA item ids) for a race whose own outfits don't tell its classes apart. BfA
# dresses every Pandaren class alike - the Trainee's vest, leggings, cord and wraps: it starts
# neutral, on the Wandering Isle. The Isle's quest rewards dress them by armour instead: cloth,
# leather and plate (mail to look at), each in colours - white (the Initiate's), blue, green, red,
# yellow. Each class here wears a set no other class does: the plate wearers red and gold, the
# cloth white, blue and green, the leather white, teal and red, a rogue the Trainee's.
# Weapons: BfA's own Trainee's for each class, where a level 1 character of that 3.3.5 class can
# wield it (AzerothCore's playercreateinfo_skills with All Races All Classes): a shaman the mace,
# not BfA's axe; a mage the staff, not BfA's spellblade (a sword) and fan; paladin, warlock and
# druid, classes BfA's Pandaren never were, the sword and the staff; a hunter the axe in melee.
TRAINEE_SWORD, TRAINEE_STAFF, TRAINEE_MACE, TRAINEE_AXE = 73210, 73209, 73207, 76391
TRAINEE_CROSSBOW, TRAINEE_DAGGERS, TRAINEE_BOOK, TRAINEE_SHIELD = 73211, (73208, 73212), 76393, 73213
KITS = {
    "Pandaren": {
        1: (74573, 74582, 74570, 74576, 74579, 74585, TRAINEE_SWORD),        # warrior: Glistening Breastplate ... (red)
        2: (74602, 74608, 74599, 74596, 74605, TRAINEE_SWORD),               # paladin: Ritual Breastplate ... (gold)
        3: (73225, 73228, 73218, 73215, 73221, 73231, TRAINEE_AXE, TRAINEE_CROSSBOW),  # hunter: Initiate's Vest ... (white)
        4: (72019, 72020, 77509, 77526) + TRAINEE_DAGGERS,                   # rogue: the Trainee's (BfA's outfit)
        5: (73224, 73227, 73217, 73214, 73220, 73230, TRAINEE_MACE, TRAINEE_BOOK),  # priest: Initiate's Robes ... (white)
        7: (74575, 74581, 74569, 74572, 74584, TRAINEE_MACE, TRAINEE_SHIELD),  # shaman: Sun Pearl Vest ... (teal)
        8: (74577, 74580, 74568, 74571, 74574, 74583, TRAINEE_STAFF),        # mage: Robes of the Water Spirit ... (blue)
        9: (74606, 74603, 74597, 74600, 74594, TRAINEE_STAFF),               # warlock: Protector's Robes ... (green)
        11: (74604, 74601, 74598, 74578, 74595, 74607, TRAINEE_STAFF),       # druid: Vest of Compassion ... (red)
    },
}


@dataclass
class OutfitItem:
    bfa_id: int
    name: str
    inventory_type: int
    subclass: int                                   # BfA's armour
    material: int
    geosets: list[int]                              # 3.3.5's three groups
    textures: dict[int, dict[int, int]] = field(default_factory=dict)    # section -> {sex: FileDataID}
    icon: int = 0                                   # its icon's FileDataID
    dropped: list[str] = field(default_factory=list)
    id: int = 0                                     # its 3.3.5 item and display id
    wear: int = 1                                   # its 3.3.5 armour subclass
    texture_names: dict[int, str] = field(default_factory=dict)          # section -> name (no _U/_M/_F)
    icon_name: str = ""
    buckle: tuple[int, int] | None = None           # a belt's model and its texture (FileDataIDs)
    item_class: int = ARMOUR
    sheathe: int = 0                                # where it hangs sheathed (Item.SheatheType)
    model: tuple[int, int] | None = None            # a held piece's model and its texture (FileDataIDs)
    model_name: str = ""                            # its 3.3.5 ItemDisplayInfo ModelName ("<stem>.mdx")
    model_texture: str = ""                         # and ModelTexture
    visual: int = 0                                 # a held piece's ItemDisplayInfo SpellVisualID
    sound_group: int = 0                            # and GroupSoundIndex

    @property
    def held(self) -> bool:
        return self.item_class == WEAPON or self.inventory_type in HELD_SLOTS


@dataclass
class OutfitPlan:
    bfa_race: int
    kits: dict[tuple[int, int], list[int]]          # (class, sex) -> its BfA armour, in BfA's order
    items: dict[int, OutfitItem]                    # BfA id -> item
    block: int = 0
    files: list[str] = field(default_factory=list)
    owns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return sorted(it.id for it in self.items.values())


def collect(table: Callable[[str], list[tuple[int, dict]]], races: list[int],
            chosen: dict[int, tuple[int, ...]] | None = None) -> OutfitPlan | None:
    """BfA's start armour of the first of ``races`` (the race, then its neutral one) it has outfits
    for, with each piece's display data; None: BfA dresses it in no armour of its own. ``chosen``
    (class -> BfA items, a ``KITS`` entry) dresses each class in those instead, both sexes alike,
    the textures still the first of ``races``' own where BfA has some - weapons, shields and
    off-hands too (held pieces: their models)."""
    item = dict(table("Item"))

    def armour(ids, held: bool = False) -> list[int]:
        def wearable(r: dict) -> bool:
            if r.get("ClassID") == ARMOUR and r.get("InventoryType") in BODY_SLOTS:
                return True
            return held and (r.get("ClassID") == WEAPON
                             or (r.get("ClassID") == ARMOUR and r.get("InventoryType") in HELD_SLOTS))
        return list(dict.fromkeys(i for i in ids if i > 0 and wearable(item.get(i, {}))))

    kits = {}
    notes = []
    if chosen:
        race = next((rc for rc in races if rc), None)
        for cls, ids in sorted(chosen.items()):
            worn = armour(ids, held=True)
            notes += [f"class {cls}: item {i} is no BfA armour or weapon; left out" for i in ids if i not in worn]
            for sex in (MALE, FEMALE):
                kits[(cls, sex)] = worn
    else:
        outfits = [r for _i, r in table("CharStartOutfit")]
        race = next((rc for rc in races if rc and any(r["RaceID"] == rc and r["ClassID"] in CLASSES
                                                      for r in outfits)), None)
        for r in outfits:
            if r["RaceID"] != race or r["ClassID"] not in CLASSES:     # a monk's: no such 3.3.5 class
                continue
            worn = armour(r["ItemID"])
            if worn:
                kits[(r["ClassID"], r["SexID"])] = worn
    if race is None or not any(kits.values()):
        return None
    names = {i: r.get("Display_lang", "") for i, r in table("ItemSearchName")}
    appearance: dict[int, tuple[int, int]] = {}
    for _i, r in table("ItemModifiedAppearance"):
        if r.get("ItemAppearanceModifierID", 0) == 0:
            key = (r.get("OrderIndex", 0), r["ItemAppearanceID"])
            appearance[r["ItemID"]] = min(appearance.get(r["ItemID"], key), key)
    appearances = dict(table("ItemAppearance"))
    displays = dict(table("ItemDisplayInfo"))
    materials: dict[int, list[dict]] = {}
    for _i, r in table("ItemDisplayInfoMaterialRes"):
        materials.setdefault(r["ItemDisplayInfoID"], []).append(r)
    files: dict[int, list[dict]] = {}
    for _i, r in table("TextureFileData"):
        if r.get("UsageType", 0) == 0:
            files.setdefault(r["MaterialResourcesID"], []).append(r)
    component = dict(table("ComponentTextureFileData"))
    model_files: dict[int, int] | None = None
    plan = OutfitPlan(race, kits, {}, notes=notes)
    for bfa_id in sorted({i for kit in kits.values() for i in kit}):
        src = item[bfa_id]
        app = appearances.get(appearance.get(bfa_id, (0, 0))[1], {})
        did = app.get("ItemDisplayInfoID", 0)
        d = displays.get(did)
        if d is None:
            plan.notes.append(f"{names.get(bfa_id) or bfa_id}: BfA has no display for it; left out")
            continue
        it = OutfitItem(bfa_id, names.get(bfa_id) or f"Item {bfa_id}", src["InventoryType"], src.get("SubclassID", 1),
                        src.get("Material", 7), list(d.get("GeosetGroup", [0, 0, 0]))[:3],
                        icon=app.get("DefaultIconFileDataID", 0) or src.get("IconFileDataID", 0),
                        item_class=src.get("ClassID", ARMOUR), sheathe=src.get("SheatheType", 0))
        models = list(d.get("ModelResourcesID") or [0])
        # a belt's model is drawn on the body (buckles.py), a held piece's in the hand
        if (src["InventoryType"] == WAIST or it.held) and models[0]:
            if model_files is None:
                model_files = {}
                for i, r in table("ModelFileData"):
                    res = r.get("ModelResourcesID", 0)
                    model_files[res] = min(model_files.get(res, r.get("FileDataID", i)), r.get("FileDataID", i))
            skin = (d.get("ModelMaterialResourcesID") or [0])[0]
            texture = min((f["FileDataID"] for f in files.get(skin, [])), default=0)
            if model_files.get(models[0]) and texture:
                if it.held:
                    it.model = (model_files[models[0]], texture)
                else:
                    it.buckle = (model_files[models[0]], texture)
            else:
                it.dropped.append("its model (BfA names no file or texture for it)")
        elif any(models):
            it.dropped.append("its models (3.3.5 draws none for this slot)")
        for m in materials.get(did, []):
            section = m["ComponentSection"]
            if section not in SECTION_FOLDERS:
                it.dropped.append(f"its texture for section {section} (3.3.5 has no such section)")
                continue
            candidates = [(f["FileDataID"], component.get(f["FileDataID"], {}).get("RaceID", 0) or 0,
                           component.get(f["FileDataID"], {}).get("GenderIndex", BOTH))
                          for f in files.get(m["MaterialResourcesID"], [])]
            for sex in (MALE, FEMALE):
                pick = _pick(candidates, races, sex)
                if pick:
                    it.textures.setdefault(section, {})[sex] = pick
        plan.items[bfa_id] = it
    for key in list(kits):
        kits[key] = [i for i in kits[key] if i in plan.items]
    return plan


def _pick(candidates: list[tuple[int, int, int]], races: list[int], sex: int) -> int | None:
    """The texture a race and sex wear: its race's own before any race's, its sex's own before both's."""
    fits = [c for c in candidates if c[2] in (sex, BOTH) and (c[1] == 0 or c[1] in races)]
    if not fits:
        return None
    return min(fits, key=lambda c: (c[1] == 0, c[2] != sex, c[0]))[0]


def kit_for(plan: OutfitPlan, cls: int, sex: int) -> list[int]:
    """The BfA armour a 3.3.5 class wears: its own outfit's, else that of the class whose best armour
    is nearest above its own (a paladin: the warrior's plate; a warlock: the mage's cloth), then below."""
    if (cls, sex) in plan.kits:
        return plan.kits[(cls, sex)]
    own = BEST_ARMOUR.get(cls, 1)
    have = sorted({c for c, s in plan.kits if s == sex}, key=lambda c: (BEST_ARMOUR.get(c, 1) < own,
                                                                       abs(BEST_ARMOUR.get(c, 1) - own), c))
    return plan.kits[(have[0], sex)] if have else []


def assign(plan: OutfitPlan, block_base: int, previous: dict[int, int] | None = None) -> None:
    """Item and display ids in the race's block: the id an earlier build gave a piece (``previous``,
    BfA id -> id: characters already wear it), the rest the free ids in BfA id order; each piece's
    3.3.5 armour: BfA's, down to what every class wearing it has at level 1 (a weapon, a shield, an
    off-hand stay what they are)."""
    used = set()
    for bfa_id in sorted(plan.items):
        old = (previous or {}).get(bfa_id)
        if old is not None and old >= block_base and old not in used:
            plan.items[bfa_id].id = old
            used.add(old)
    free = (i for i in range(block_base, block_base + 10 ** 6) if i not in used)
    for bfa_id in sorted(plan.items):
        if plan.items[bfa_id].id not in used:
            plan.items[bfa_id].id = next(free)
    for it in plan.items.values():
        if it.held:
            it.wear = it.subclass
            continue
        wearers = [c for c in CLASSES for s in (MALE, FEMALE) if it.bfa_id in kit_for(plan, c, s)]
        it.wear = max(1, min([it.subclass] + [LEVEL1_ARMOUR[c] for c in wearers]))


def stem(path: str) -> str:
    """A component texture's name without its folder, extension and sex (``_u``/``_m``/``_f``)."""
    base = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]
    return _SEX_SUFFIX.sub("", base)


def write_textures(plan: OutfitPlan, read: Callable[[int], bytes], names: dict[int, str], client_out: str,
                   prefix: str) -> None:
    """Each piece's textures into ``Item\\TextureComponents\\`` under the race's name (``prefix``):
    ``_U`` when both sexes wear one, else ``_M`` and ``_F``."""
    for it in plan.items.values():
        for section, by_sex in sorted(it.textures.items()):
            m, f = by_sex.get(MALE), by_sex.get(FEMALE)
            path = names.get(m or f)
            if not path:
                it.dropped.append(f"its section {section} texture (not in the listfile)")
                continue
            base = f"{prefix}_{stem(path)}"
            wanted = {"U": m} if m == f else {k: v for k, v in (("M", m), ("F", f)) if v}
            for suffix, fdid in wanted.items():
                rel = f"{TEXTURE_DIR}{SECTION_FOLDERS[section]}\\{base}_{suffix}.blp"
                plan.files.append(_write_blp(os.path.join(client_out, *rel.split("\\")), read(fdid)))
                plan.owns.append(rel.lower())
            it.texture_names[section] = base


def _write_blp(path: str, data: bytes) -> str:
    """BfA's file as it is when it is palettized, as every 3.3.5 component texture is - the client
    paints them into the body texture itself and draws a DXT one green (BfA's are both) - else
    decoded and palettized."""
    try:
        if blp.header(data)["encoding"] == PALETTIZED:
            return write_verified(path, data, lambda p: blp.verify_blp(p, full_chain=False))
    except (blp.BLPError, ValueError, OSError):
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".bfa"
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        img = blp.decode(tmp)
    finally:
        os.remove(tmp)
    return write_verified(path, blp.encode_palettized(img), lambda p, size=img.size: blp.verify_blp(p, size))


def held_models(plan: OutfitPlan, read: Callable[[int], bytes], names: dict[int, str], client_out: str,
                prefix: str) -> dict[str, tuple[str, int]]:
    """Each held piece's model to convert, stem -> (client folder, BfA FileDataID), under the race's
    name - a shield's in Shield\\, a weapon's or off-hand's in Weapon\\, where 3.3.5 keeps them -
    and its texture copied beside it as BfA has it (a model's texture: DXT draws). The piece names
    both (ItemDisplayInfo ModelName, ModelTexture)."""
    out: dict[str, tuple[str, int]] = {}
    for it in sorted(plan.items.values(), key=lambda i: i.bfa_id):
        if not it.model:
            continue
        model, texture = it.model
        if model not in names or texture not in names:
            it.dropped.append("its model (not in the listfile)")
            continue
        folder = SHIELD_DIR if it.inventory_type == SHIELD else WEAPON_DIR
        stem = f"{prefix}_{os.path.splitext(os.path.basename(names[model].replace(chr(92), '/')))[0]}"
        tex = f"{prefix}_{os.path.splitext(os.path.basename(names[texture].replace(chr(92), '/')))[0]}"
        rel = f"{folder}{tex}.blp"
        if rel.lower() not in plan.owns:
            plan.files.append(write_verified(os.path.join(client_out, *rel.split("\\")), read(texture), blp.verify_blp))
            plan.owns.append(rel.lower())
        out[stem] = (folder, model)
        it.model_name, it.model_texture = f"{stem}.mdx", tex
    return out


def write_icons(plan: OutfitPlan, read: Callable[[int], bytes], names: dict[int, str], exists: Callable[[str], bool],
                client_out: str) -> None:
    """Each piece's icon: the client's own when it has the file, else BfA's (one mip) with a full chain."""
    for it in plan.items.values():
        path = names.get(it.icon, "")
        if not path:
            it.dropped.append("its icon (not in the listfile)")
            continue
        name = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]
        rel = f"{ICON_DIR}{name}.blp"
        it.icon_name = name
        if exists(rel) or rel.lower() in plan.owns:
            continue
        out = os.path.join(client_out, *rel.split("\\"))
        tmp = out + ".bfa"
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(read(it.icon))
        try:
            img = blp.decode(tmp)
        finally:
            os.remove(tmp)
        plan.files.append(write_verified(out, blp.encode_dxt(img), lambda p, size=img.size: blp.verify_blp(p, size)))
        plan.owns.append(rel.lower())


def server_stats(it: OutfitItem) -> dict:
    """A piece's item_template values as 3.3.5's own start gear has them: armour and durability by
    slot; a weapon's damage, speed and durability by type (``STARTER_WEAPONS``) and what it shoots;
    a shield's armour and block; an off-hand nothing."""
    if it.item_class == WEAPON:
        low, high, delay, durability = STARTER_WEAPONS.get(it.wear, STARTER_WEAPONS[7])
        ranged = it.inventory_type in RANGED
        return {"armor": 0, "durability": durability, "dmg_min": low, "dmg_max": high, "delay": delay,
                "ammo_type": AMMO_TYPE.get(it.wear, 0) if ranged else 0, "ranged_mod_range": 100 if ranged else 0,
                "sheath": it.sheathe}
    if it.inventory_type == SHIELD:
        armour, block, durability = SHIELD_STATS
        return {"armor": armour, "durability": durability, "block": block, "sheath": it.sheathe}
    if it.inventory_type == HELD_OFF_HAND:
        return {"armor": 0, "durability": 0, "sheath": it.sheathe}
    return {"armor": ARMOUR_VALUE.get(it.inventory_type, 0), "durability": DURABILITY.get(it.inventory_type, 0)}


def adopt_kinds(plan: OutfitPlan, items: DBCFile, displays: DBCFile | None = None,
                layout: DbcLayout | None = None) -> None:
    """Each held piece as 3.3.5's own of its kind (class, subclass, slot) mostly are: sheathed by
    the commonest Item.dbc SheatheType - BfA's numbers differ: a crossbow 5 (3.3.5's ranged weapons
    0), an off-hand frill 3 (3.3.5's 7) - and, with ``displays``, the commonest ItemDisplayInfo
    SpellVisualID and GroupSoundIndex. A ranged weapon's visual IS its shot: 3.3.5 plays its kits
    (a crossbow's 743: LoadRifle and its sound, then AttackRifle and its sound) on each Auto Shot -
    without one the hunter fires standing still. BfA's sheath, no visual, where the client has none
    of the kind."""
    by_display = {r[0]: r for r in displays.records} if displays is not None else {}
    sheathes: dict[tuple[int, int, int], Counter] = {}
    looks: dict[tuple[int, int, int], Counter] = {}
    for r in items.records:
        kind = (r[1], r[2], r[6])
        sheathes.setdefault(kind, Counter())[r[7]] += 1
        d = by_display.get(r[5])
        if d is not None and layout is not None:
            looks.setdefault(kind, Counter())[(d[layout.at("SpellVisualID")], d[layout.at("GroupSoundIndex")])] += 1
    for it in plan.items.values():
        if not it.held:
            continue
        kind = (it.item_class, it.wear, it.inventory_type)
        if kind in sheathes:
            it.sheathe = sheathes[kind].most_common(1)[0][0]
        if kind in looks:
            it.visual, it.sound_group = looks[kind].most_common(1)[0][0]


def sound_groups(items: DBCFile, displays: DBCFile, layout: DbcLayout) -> dict[tuple[int, int], int]:
    """3.3.5's usual equip-sound group (ItemDisplayInfo.GroupSoundIndex) by (material, slot), from its
    own armour."""
    by_display = {r[0]: r for r in displays.records}
    seen: dict[tuple[int, int], Counter] = {}
    for r in items.records:
        if r[1] != ARMOUR or r[6] not in BODY_SLOTS:
            continue
        d = by_display.get(r[5])
        if d is not None and d[layout.at("GroupSoundIndex")]:
            seen.setdefault((r[4], r[6]), Counter())[d[layout.at("GroupSoundIndex")]] += 1
    return {k: c.most_common(1)[0][0] for k, c in seen.items()}


def add_items(tables: dict, plan: OutfitPlan, layouts: dict[str, DbcLayout], sounds: dict | None = None) -> dict[str, int]:
    """The plan's Item.dbc rows and, where the table is given, its ItemDisplayInfo rows."""
    LI = layouts["Item"]
    counts = {}
    for it in sorted(plan.items.values(), key=lambda i: i.id):
        row = LI.blank()
        for col, v in (("ID", it.id), ("ClassID", it.item_class), ("SubclassID", it.wear),
                       ("Sound_override_subclassID", -1), ("Material", it.material), ("DisplayInfoID", it.id),
                       ("InventoryType", it.inventory_type), ("SheatheType", it.sheathe if it.held else 0)):
            LI.set(row, col, v & 0xFFFFFFFF)
        tables["Item"].add_record(row)
        counts["Item"] = counts.get("Item", 0) + 1
        if "ItemDisplayInfo" not in tables:
            continue
        t, LD = tables["ItemDisplayInfo"], layouts["ItemDisplayInfo"]
        row = LD.blank()
        LD.set(row, "ID", it.id)
        LD.set(row, "InventoryIcon", t.add_string(it.icon_name), 0)
        if it.model_name:
            LD.set(row, "ModelName", t.add_string(it.model_name), 0)
            LD.set(row, "ModelTexture", t.add_string(it.model_texture), 0)
        for k, g in enumerate(it.geosets):
            LD.set(row, "GeosetGroup", g, k)
        if it.held:
            LD.set(row, "SpellVisualID", it.visual)
            LD.set(row, "GroupSoundIndex", it.sound_group)
        else:
            LD.set(row, "GroupSoundIndex", (sounds or {}).get((it.material, it.inventory_type), 0))
        for section, name in it.texture_names.items():
            LD.set(row, "Texture", t.add_string(name), section)
        t.add_record(row)
        counts["ItemDisplayInfo"] = counts.get("ItemDisplayInfo", 0) + 1
    return counts


def _gone(held: list[OutfitItem]) -> set[int]:
    """The template's slots a kit's held pieces take: its weapon the main hand (a two-hander the
    off hand too), a shield, off-hand or second one-hander the off hand, a ranged weapon the ranged."""
    gone = set(REPLACED_SLOTS)
    if any(it.inventory_type in MAIN_HAND for it in held):
        gone |= MAIN_HAND
    if (any(it.inventory_type in OFF_HAND or it.inventory_type == TWO_HAND for it in held)
            or sum(1 for it in held if it.inventory_type == ONE_HAND) > 1):
        gone |= OFF_HAND
    if any(it.inventory_type in RANGED for it in held):
        gone |= RANGED
    return gone


def _create_slots(pieces: list[OutfitItem]) -> list[int]:
    """The InventoryType each piece's outfit entry gets. The create screen draws an entry by it
    (0x4F29C0; the server reads only the item ids): 13 in the right hand only - a second one-hander
    would take the first's place - and 23 not at all, so a second one-hander and a held off-hand
    go in the left hand as an off-hand weapon (22)."""
    out, one_handers = [], 0
    for it in pieces:
        inv = it.inventory_type
        one_handers += inv == ONE_HAND
        if (inv == ONE_HAND and one_handers > 1) or inv == HELD_OFF_HAND:
            inv = OFF_HAND_WEAPON
        out.append(inv)
    return out


def _ammo_for(item_id: int, ammo: int | None, rows: dict[int, list] | None) -> int:
    """The template's ammo or what carries it, for a ranged weapon that shoots ``ammo``: arrows and
    a quiver for a crossbow where the template had shot and a pouch (``AMMO_ITEMS``)."""
    row = (rows or {}).get(item_id)
    if not ammo or row is None or row[2] not in AMMO_ITEMS or row[2] == ammo:
        return item_id
    if row[1] == PROJECTILE:
        return AMMO_ITEMS[ammo][0]
    if row[1] == QUIVER:
        return AMMO_ITEMS[ammo][1]
    return item_id


def dress(outfits: RawDBC, race_ids: list[int], plan: OutfitPlan, item_rows: dict[int, list] | None = None) -> int:
    """The races' outfits in the plan's armour in place of the template's (``REPLACED_SLOTS``), and
    its weapons, shield and off-hand in place of the template's in the slots they take (``_gone``) -
    the template's ammo then what the ranged weapon shoots (``item_rows``: Item.dbc rows by id);
    the hearthstone and the rest kept. A death knight's stay 3.3.5's."""
    by_bfa = plan.items
    changed = 0
    for n, r in enumerate(outfits.records):
        race, cls, sex = r[4], r[5], r[6]
        if race not in race_ids or cls == DEATH_KNIGHT:
            continue
        kit = kit_for(plan, cls, sex)
        if not kit:
            continue
        rec = bytearray(r)
        items = struct.unpack_from(f"<{SLOTS}i", rec, OUTFIT_ITEMS)
        disp = struct.unpack_from(f"<{SLOTS}i", rec, OUTFIT_DISPLAYS)
        slots = struct.unpack_from(f"<{SLOTS}i", rec, OUTFIT_SLOTS)
        held = [by_bfa[b] for b in kit if by_bfa[b].held]
        gone = _gone(held)
        ammo = next((AMMO_TYPE.get(it.wear) for it in held if it.inventory_type in RANGED), None)
        kept = []
        for i, d, s in zip(items, disp, slots):
            if i <= 0 or s in gone:
                continue
            swapped = _ammo_for(i, ammo, item_rows)
            if swapped != i:
                i, d = swapped, item_rows[swapped][5] if swapped in item_rows else d
            kept.append((i, d, s))
        pieces = [by_bfa[b] for b in kit]
        worn = [(it.id, it.id, inv) for it, inv in zip(pieces, _create_slots(pieces))]
        entries = (worn + kept)[:SLOTS]
        entries += [EMPTY] * (SLOTS - len(entries))
        for k, (i, d, s) in enumerate(entries):
            struct.pack_into("<i", rec, OUTFIT_ITEMS + 4 * k, i)
            struct.pack_into("<i", rec, OUTFIT_DISPLAYS + 4 * k, d)
            struct.pack_into("<i", rec, OUTFIT_SLOTS + 4 * k, s)
        outfits.records[n] = bytes(rec)
        changed += 1
    return changed


def strip(tables: dict, block_base: int, block_size: int) -> dict[str, int]:
    """Remove what an earlier build added: every Item and ItemDisplayInfo row in the race's block."""
    removed = {}
    for name in ("Item", "ItemDisplayInfo"):
        d = tables.get(name)
        if d is None:
            continue
        before = len(d.records)
        d.records = [r for r in d.records if not block_base <= r[0] < block_base + block_size]
        if len(d.records) != before:
            removed[name] = before - len(d.records)
    return removed
