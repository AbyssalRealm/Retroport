"""ItemDisplayInfo.dbc (+ Item.dbc) writer - make a converted weapon/armor model usable as an item.

3.3.5a (12340) ItemDisplayInfo, 25 fields, checked against a real client/server DBC:

    [0] ID           [1-2] ModelName L/R   [3-4] ModelTexture L/R   [5-6] InventoryIcon
    [7-9] GeosetGroup  [10] Flags  [11] SpellVisualID  [12] GroupSoundIndex
    [13-14] HelmetGeosetVis  [15-22] Texture[8] (body component textures)
    [23] ItemVisual  [24] ParticleColorID

String conventions (from Blizzard's own rows): ModelName is a bare filename with
extension ("Axe_2H_Battle_B_01.mdx" - the client finds the folder by item type);
ModelTexture, InventoryIcon and component textures are bare names with no path
and no extension ("Axe_2H_Battle_B_01", "INV_Axe_04", "Plate_A_01Silver_Chest_TU").

Item.dbc (8 fields: ID, Class, Subclass, SoundOverrideSubclass, Material,
DisplayInfoID, InventoryType, SheatheType) matters on the server: AzerothCore
overwrites item_template.displayid with Item.dbc's DisplayInfoID for any item
listed there (DBC.EnforceItemAttributes, on by default), and creature_equip_template
refuses items missing from it. So repointing an item updates its Item.dbc row too.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel

from .dbc import DBCError, DBCFile, _load_expecting, _refuse_overwrite
from .sql import parse_entries

ITEM_DISPLAY_INFO_FIELDS = 25
ITEM_FIELDS = 8
ITEM_DISPLAY_ID = 5          # Item.dbc field holding the display id

# Texture[8] -> field index, and the Item\TextureComponents folder each lives in
COMPONENT_SECTIONS = {
    "ArmUpper": 15, "ArmLower": 16, "Hand": 17, "TorsoUpper": 18,
    "TorsoLower": 19, "LegUpper": 20, "LegLower": 21, "Foot": 22,
}
_STRING_FIELDS = {1, 2, 3, 4, 5, 6, *COMPONENT_SECTIONS.values()}


class ItemRegistrationResult(BaseModel):
    display_id: int
    display_dbc: str
    item_dbc: str | None = None
    items_updated: list[int] = []        # existing Item.dbc rows repointed
    items_added: list[int] = []          # new Item.dbc rows (cloned from item_like)
    items_missing: list[int] = []        # entries with no Item.dbc row and no item_like
    row: dict[str, object] = {}          # the written display row, by field name
    pack: list[str] = []                 # in-MPQ paths the model/textures must be packed at
    sql: str = ""


def _bare(value: str, label: str, *, keep_ext: bool = False) -> str:
    v = value.strip()
    if any(c in v for c in "\\/:"):
        raise DBCError(f"{label} must be a bare name, not a path - got '{value}' "
                       "(the client finds the folder from the item type)")
    if not keep_ext and v.lower().endswith(".blp"):
        v = v[:-4]
    return v


def _model_name(value: str, label: str) -> str:
    v = _bare(value, label, keep_ext=True)
    if v and not v.lower().endswith((".mdx", ".m2")):
        raise DBCError(f"{label} must be a model filename ending in .mdx (or .m2) - got '{value}'")
    return v


def model_folder(model_name: str) -> str:
    """The Item\\ObjectComponents folder the client looks in, guessed from Blizzard's
    naming (the real rule is the item's InventoryType)."""
    n = model_name.lower()
    if n.startswith("helm_"):
        return "Head"
    if n.startswith(("lshoulder_", "rshoulder_")):
        return "Shoulder"
    if n.startswith(("shield_", "buckler_")):
        return "Shield"
    if n.startswith("quiver"):
        return "Quiver"
    if n.startswith("cape_"):
        return "Cape"
    return "Weapon"


def suggest_from_model(m2_path: str, manifest_path: str | None = None) -> dict[str, str]:
    """Prefill values from a converted model: ModelName from its filename, and the
    ModelTexture from the wow.export texture exported for its type-2 (item skin) slot."""
    from .check import analyze_m2
    from .convert.loaders import find_manifest

    stem = os.path.splitext(os.path.basename(m2_path))[0]
    out = {"model": stem + ".mdx", "texture": ""}
    manifest_path = manifest_path or find_manifest(m2_path)
    try:
        slots = analyze_m2(m2_path).textures
        with open(manifest_path, "r", encoding="utf-8") as fh:
            files = [e.get("file", "") for e in json.load(fh).get("textures", []) if isinstance(e, dict)]
    except (OSError, ValueError, TypeError):
        return out
    for i, (ttype, _path) in enumerate(slots):
        if ttype == 2 and i < len(files) and files[i]:
            out["texture"] = os.path.splitext(os.path.basename(files[i]))[0]
            break
    return out


def register_item_display(
    dbc_dir: str, out_dir: str, *,
    template_display_id: int | None = None,
    model: str | None = None, model_right: str | None = None,
    texture: str | None = None, texture_right: str | None = None,
    icon: str | None = None,
    geoset_groups: tuple[int, int, int] | None = None,
    flags: int | None = None, spell_visual: int | None = None, sound_group: int | None = None,
    helmet_geoset_vis: tuple[int, int] | None = None,
    component_textures: dict[str, str] | None = None,
    item_visual: int | None = None, particle_color: int | None = None,
    display_floor: int | None = None,
    item_entries: str = "", item_like: int | None = None,
) -> ItemRegistrationResult:
    """Append an ItemDisplayInfo row (fresh, or cloned from ``template_display_id``
    with every given value overriding it; None = keep/zero, "" = clear a string).

    ``item_entries`` (comma-separated item_template entries) get their Item.dbc
    DisplayInfoID repointed; entries not in Item.dbc get a row cloned from
    ``item_like`` (an existing item of the same kind), or are reported as missing.
    Patched DBCs are written into ``out_dir`` under their original names.
    """
    _refuse_overwrite(dbc_dir, out_dir)
    idi = _load_expecting(os.path.join(dbc_dir, "ItemDisplayInfo.dbc"), ITEM_DISPLAY_INFO_FIELDS,
                          "ItemDisplayInfo.dbc")

    if template_display_id is not None:
        src = idi.find_by_id(template_display_id)
        if src is None:
            raise DBCError(f"template DisplayID {template_display_id} is not in ItemDisplayInfo.dbc")
        # strings are re-added by value so the new row never shares string offsets
        row: list[int | str] = [idi.get_string(v) if i in _STRING_FIELDS else v for i, v in enumerate(src)]
    else:
        row = ["" if i in _STRING_FIELDS else 0 for i in range(ITEM_DISPLAY_INFO_FIELDS)]

    def put(i: int, value) -> None:
        if value is not None:
            row[i] = value

    put(1, None if model is None else _model_name(model, "model"))
    put(2, None if model_right is None else _model_name(model_right, "right model"))
    put(3, None if texture is None else _bare(texture, "texture"))
    put(4, None if texture_right is None else _bare(texture_right, "right texture"))
    put(5, None if icon is None else _bare(icon, "icon"))
    for i, v in enumerate(geoset_groups or ()):
        put(7 + i, v)
    put(10, flags)
    put(11, spell_visual)
    put(12, sound_group)
    for i, v in enumerate(helmet_geoset_vis or ()):
        put(13 + i, v)
    for section, name in (component_textures or {}).items():
        if section not in COMPONENT_SECTIONS:
            raise DBCError(f"unknown component section '{section}' - use one of {', '.join(COMPONENT_SECTIONS)}")
        put(COMPONENT_SECTIONS[section], _bare(name, f"{section} texture"))
    put(23, item_visual)
    put(24, particle_color)

    if not row[1] and not any(row[i] for i in COMPONENT_SECTIONS.values()):
        raise DBCError("the display has neither a model nor any component texture - it would be invisible")

    display_id = idi.next_free_id(display_floor)
    row[0] = display_id
    idi.add_record([idi.add_string(v) if i in _STRING_FIELDS else int(v) & 0xFFFFFFFF
                    for i, v in enumerate(row)])

    # --- Item.dbc: repoint listed items so the server doesn't revert/drop them ---
    entries = parse_entries(item_entries) if item_entries.strip() else []
    updated: list[int] = []
    added: list[int] = []
    missing: list[int] = []
    item = None
    if entries:
        item = _load_expecting(os.path.join(dbc_dir, "Item.dbc"), ITEM_FIELDS, "Item.dbc")
        like = None
        if item_like is not None:
            like = item.find_by_id(item_like)
            if like is None:
                raise DBCError(f"item_like entry {item_like} is not in Item.dbc")
        for e in entries:
            rec = item.find_by_id(e)
            if rec is not None:
                rec[ITEM_DISPLAY_ID] = display_id
                updated.append(e)
            elif like is not None:
                new = list(like)
                new[0], new[ITEM_DISPLAY_ID] = e, display_id
                item.add_record(new)
                added.append(e)
            else:
                missing.append(e)

    os.makedirs(out_dir, exist_ok=True)
    idi_out = idi.save(os.path.join(out_dir, "ItemDisplayInfo.dbc"))
    item_out = item.save(os.path.join(out_dir, "Item.dbc")) if (updated or added) else None

    check = DBCFile.load(idi_out).find_by_id(display_id)
    if check is None:
        raise DBCError("post-write verification failed: the new display row is missing")
    if item_out:
        written = DBCFile.load(item_out)
        for e in updated + added:
            r = written.find_by_id(e)
            if r is None or r[ITEM_DISPLAY_ID] != display_id:
                raise DBCError(f"post-write verification failed: Item.dbc entry {e} does not point at {display_id}")

    names = {1: "ModelName", 2: "ModelNameRight", 3: "ModelTexture", 4: "ModelTextureRight", 5: "InventoryIcon",
             7: "GeosetGroup1", 8: "GeosetGroup2", 9: "GeosetGroup3", 10: "Flags", 11: "SpellVisualID",
             12: "GroupSoundIndex", 13: "HelmetGeosetVis1", 14: "HelmetGeosetVis2", 23: "ItemVisual",
             24: "ParticleColorID", **{v: f"Texture{k}" for k, v in COMPONENT_SECTIONS.items()}}
    row_out = {"ID": display_id, **{n: row[i] for i, n in names.items() if row[i]}}

    return ItemRegistrationResult(
        display_id=display_id, display_dbc=idi_out, item_dbc=item_out,
        items_updated=updated, items_added=added, items_missing=missing,
        row=row_out, pack=_pack_list(row), sql=_item_sql(display_id, entries, missing))


def _pack_list(row: list) -> list[str]:
    out: list[str] = []
    for mi, ti in ((1, 3), (2, 4)):
        if row[mi]:
            folder = f"Item\\ObjectComponents\\{model_folder(row[mi])}"
            stem = os.path.splitext(row[mi])[0]
            if folder.endswith("Head"):
                out.append(f"{folder}\\{stem}_<Race><Gender>.m2  (one per race/gender, e.g. {stem}_HuM.m2) + .skin")
            else:
                out.append(f"{folder}\\{stem}.m2 (+ {stem}00.skin)")
            if row[ti]:
                out.append(f"{folder}\\{row[ti]}.blp")
    for section, i in COMPONENT_SECTIONS.items():
        if row[i]:
            out.append(f"Item\\TextureComponents\\{section}Texture\\{row[i]}_U.blp  (or _M / _F per gender)")
    if row[5]:
        out.append(f"Interface\\Icons\\{row[5]}.blp")
    return out


def _item_sql(display_id: int, entries: list[int], missing: list[int]) -> str:
    lines = [f"-- new ItemDisplayInfo {display_id}"]
    if entries:
        ids = ", ".join(str(e) for e in entries)
        lines += [f"UPDATE item_template SET displayid = {display_id} WHERE entry IN ({ids});"]
        if missing:
            lines += [f"-- WARNING: {', '.join(map(str, missing))} not in Item.dbc: fine for player items, but "
                      "creature_equip_template will drop them - rerun with item_like to add Item.dbc rows."]
    else:
        lines += [f"-- UPDATE item_template SET displayid = {display_id} WHERE entry = <entry>;",
                  "-- (if that entry is in Item.dbc, repoint it there too - rerun with item entries)"]
    lines += ["-- Put ItemDisplayInfo.dbc (and Item.dbc if written) in BOTH the client patch and the server dbc\\",
              "-- folder, restart worldserver, and delete the client's Cache\\ folder (itemcache.wdb)."]
    return "\n".join(lines)
