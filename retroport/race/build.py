"""Build everything a new playable race needs, for the client and the server.

Output (``out_dir``):

* ``client\\`` - copy its contents into the client's top patch (e.g. ``Data\\patch-H.MPQ``):
  ``Character\\<Race>\\Male|Female`` (models + textures), the race's own helmets
  (``Item\\ObjectComponents\\Head``), ``DBFilesClient\\*.dbc``, ``Interface\\GlueXML\\*`` and the
  race icon atlas;
* ``server\\dbc\\`` - copy into the server's DBC folder (e.g. ``D:\\AC\\Build\\Data\\dbc``);
* ``server\\<Race>_race.sql`` / ``<Race>_race_undo.sql`` - apply to the world database;
* ``INSTALL.txt`` and ``race.json`` - what was built and where it goes.

Every DBC is built on the copy the client (or server) actually loads, so nothing
already installed is lost. ``replace=True`` first removes a previous build of the
same race ids, so the output can be rebuilt and re-installed over itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from ..character.convert import (BfaInstall, CharacterResult, _find_listfile_paths, _find_race, append_customization,
                                 charsections_layout_problems,
                                 convert_character)
from ..convert import engine, fixup
from ..dbc import DBCError, DBCFile, RawDBC, load_dbc
from ..dbclayout import DbcLayout, LayoutError
from ..formats import blp
from ..formats.verify import m2_problems, no_new_problems, skin_problems
from ..glue import scenes, zoom
from ..mpq import ClientData
from ..safeio import write_verified
from . import anims, bounds, buckles, glue, helm, helmets, icons, items, racials, seams, sounds, sql, visuals
from .spells import BfaSpells
from .tables import (ALLIANCE, FIELDS, HORDE, BarberStyle, RaceSpec, add_barber_styles, add_base_info, add_chr_race,
                     add_displays, add_helmet_vis_race, add_names, add_race_bit, clone_race_rows, clone_start_outfits,
                     free_ids, item_displays, playable_races, strip_race)

CLIENT_TABLES = ["ChrRaces", "CharBaseInfo", "CharStartOutfit", "SkillRaceClassInfo", "SkillLineAbility", "Faction",
                 "CreatureModelData", "CreatureDisplayInfo", "CharSections", "CharHairGeosets",
                 "CharacterFacialHairStyles", "EmotesTextSound", "VocalUISounds", "NameGen", "BarberShopStyle",
                 "SoundEntries", "CreatureSoundData", "HelmetGeosetVisData"]
SERVER_TABLES = ["ChrRaces", "CharBaseInfo", "CharStartOutfit", "SkillRaceClassInfo", "SkillLineAbility", "Faction",
                 "CreatureModelData", "CreatureDisplayInfo", "EmotesTextSound", "BarberShopStyle"]
# a race's own racials (racials.py): its spells, their icons, its skill line, the index tables they use
RACIAL_CLIENT_TABLES = ["Spell", "SpellIcon", "SkillLine", "SpellCastTimes", "SpellDuration", "SpellRange", "SpellRadius",
                        "SpellVisual", "SpellVisualKit", "SpellVisualEffectName"]
RACIAL_SERVER_TABLES = ["Spell", "SkillLine", "SpellCastTimes", "SpellDuration", "SpellRange", "SpellRadius"]
# its own start outfits (items.py): the items and their displays
OUTFIT_TABLES = ["Item", "ItemDisplayInfo"]
RAW_TABLES = {"CharBaseInfo", "CharStartOutfit", "Spell"}           # Spell.dbc: 52,000 rows stay bytes
RACIALS_DB2 = ("skillline", "skilllineability", "skillraceclassinfo")
GLUE = "Interface\\GlueXML\\"
ATLAS = "Interface\\Glues\\CharacterCreate\\UI-CharacterCreate-Races.blp"
ROUND_ATLAS = "Interface\\Glues\\CharacterCreate\\UI-CharacterCreate-RacesRound.blp"
SEXES = ("Male", "Female")
VOICE_DB2 = ("soundkit", "soundkitentry", "emotestext", "emotestextsound", "vocaluisounds", "creaturesounddata",
             "creaturedisplayinfo", "creaturemodeldata")
HELMET_DB2 = ("modelfiledata", "componentmodelfiledata", "helmetgeosetdata")
# BfA ChrRaces.Flags: set on every allied race in 8.3.7 and on no other playable race - a race
# without it (a Pandaren) is on the create screen's core page with the classic races
BFA_ALLIED_RACE = 0x80000


class RaceBuildResult(BaseModel):
    race: str
    name: str
    races: list[dict] = []
    displays: dict[str, int] = {}
    client_sources: dict[str, str] = {}
    # package path -> the file it was built on ({"source", "sha256"}); `install` refuses to
    # replace a file that no longer matches, so later customizations are never lost
    bases: dict[str, dict] = {}
    rows: dict[str, dict[str, int]] = {}
    files: list[str] = []
    warnings: list[str] = []
    # the race's own helmet models' prefix (Item\ObjectComponents\Head\*_<prefix>M.m2); "" when it
    # wears the look template's
    helmet_prefix: str = ""
    # client files outside the race's folders that are its own (its racials' icons): install
    # replaces them like the race's folders
    owns: list[str] = []
    # its own racials (racials.py): {"skill_line", "name", "spells": {id: name}, "notes"}
    racials: dict = {}
    # its own start outfits (items.py): {"bfa_race", "items": {id: name}}
    outfits: dict = {}
    # its belts' models baked into its bodies (buckles.py): {"geosets": {value: {...}}, "files":
    # {client path: buckles.record}} - a rebuild unbakes the bodies first
    buckles: dict = {}


def _parse(name: str, data: bytes, layouts: dict[str, DbcLayout] | None = None):
    if name in RAW_TABLES:
        return RawDBC.from_bytes(data, name)
    return load_dbc(data, name, FIELDS[name] if name in FIELDS else layouts[name].field_count)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_inside(path: str, parent: str) -> bool:
    path, parent = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(parent))
    return path == parent or path.startswith(parent.rstrip("\\/") + os.sep)


def _apply(tables: dict, specs: list[RaceSpec], *, name: str, cfs: str, look: int, ids: tuple[int, int, int, int],
           characters: dict[int, CharacterResult] | None,
           barber: dict[int, list[BarberStyle]] | None = None, voice: sounds.Voice | None = None,
           sound_ids: dict[int, int] | None = None, csd_ids: dict[int, int] | None = None,
           helmet_prefix: str | None = None, helmet_hides: dict[int, set[int]] | None = None,
           displays: dict[int, int] | None = None, names: list[tuple[str, int]] | None = None,
           skip_skills: set[int] | None = None) -> dict[str, int]:
    """Add every spec to one set of tables (client or server); returns rows touched per table.
    With ``voice`` the race gets its own sounds (ids from sounds.assign_ids, shared by client and
    server); without, it speaks with the look template's. With ``helmet_prefix`` it wears its own
    helmets, hiding what ``helmet_hides`` says (helmets.py); without, the look template's. With
    ``names`` ((name, sex), BfA's) its random names are its own; without, the look template's.
    ``skip_skills``: skill lines not copied from the faction template (its racial line, for a race
    with racials of its own)."""
    counts: dict[str, int] = {}

    def bump(k: str, n: int) -> None:
        counts[k] = counts.get(k, 0) + n

    for s in specs:
        if tables["ChrRaces"].find_by_id(s.race_id):
            raise DBCError(f"race {s.race_id} already exists in ChrRaces - pick free ids, or rebuild with "
                           "--replace to remove the previous build of this race first")
    chr_races = tables["ChrRaces"]
    look_row = chr_races.find_by_id(look)
    if look_row is None:
        raise DBCError(f"look template race {look} is not in ChrRaces")
    models = tuple(f"Character\\{cfs}\\{sx}\\{cfs}{sx}.m2" for sx in SEXES)
    add_displays(tables["CreatureModelData"], tables["CreatureDisplayInfo"], (look_row[4], look_row[5]), models, ids)
    bump("CreatureModelData", 2)
    bump("CreatureDisplayInfo", 2)
    if voice is not None and "SoundEntries" in tables:
        bump("SoundEntries", sounds.add_sound_entries(tables["SoundEntries"], voice, sound_ids))
    if voice is not None and voice.creature and "CreatureSoundData" in tables:
        bump("CreatureSoundData", sounds.add_creature_sounds(tables["CreatureSoundData"], tables["CreatureModelData"],
                                                             voice, sound_ids, csd_ids, ids[:2]))
    for s in specs:
        add_chr_race(chr_races, s, name=name, file_string=cfs, male_display=ids[2], female_display=ids[3],
                     look_template=look, client_prefix=helmet_prefix)
        bump("ChrRaces", 1)
        bump("CharBaseInfo", len(add_base_info(tables["CharBaseInfo"], s.race_id)))
        bump("CharStartOutfit", clone_start_outfits(tables["CharStartOutfit"], s.template, s.race_id, displays))
        for t in ("SkillRaceClassInfo", "SkillLineAbility", "Faction"):
            bump(t, add_race_bit(tables[t], t, s.template_bit, s.bit, skip_skills))
        if "HelmetGeosetVisData" in tables:
            bump("HelmetGeosetVisData", add_helmet_vis_race(tables["HelmetGeosetVisData"], look, s.race_id,
                                                            helmet_hides if helmet_prefix else None))
        for t in ("EmotesTextSound", "VocalUISounds", "NameGen"):
            if t not in tables:
                continue
            if t == "EmotesTextSound" and voice is not None and voice.emotes:
                bump(t, sounds.add_emote_sounds(tables[t], voice, s.race_id, sound_ids))
            elif t == "VocalUISounds" and voice is not None and voice.vocal:
                bump(t, sounds.add_vocal_sounds(tables[t], voice, s.race_id, sound_ids))
            elif t == "NameGen" and names:
                bump(t, add_names(tables[t], s.race_id, names))
            else:
                bump(t, clone_race_rows(tables[t], t, look, s.race_id))
        if barber and "BarberShopStyle" in tables:
            bump("BarberShopStyle", add_barber_styles(tables["BarberShopStyle"], s.race_id, barber[s.race_id]))
        if characters:
            for sex_id, ch in characters.items():
                for k, v in append_customization(tables["CharSections"], tables["CharHairGeosets"],
                                                 tables["CharacterFacialHairStyles"], s.race_id, sex_id,
                                                 ch.sections, ch.hair, ch.facial).items():
                    bump(k, v)
    return counts


def build_race(*, install_dir: str, client_dir: str, out_dir: str, listfile: str, dbd_dir: str,
               race: str = "KulTiran", name: str | None = None, alliance_id: int = 22, horde_id: int = 23,
               alliance_template: int = 1, horde_template: int = 2, look_template: int = 1,
               server_dbc_dir: str | None = None, target: str = "hd", reuse: bool = False, replace: bool = False,
               on_top: bool = False, own_helmets: bool = True, own_racials: bool = True, own_outfits: bool = True,
               patch: str | None = "patch-H.MPQ", product: str | None = None,
               on_log: Callable[[str], None] | None = None) -> RaceBuildResult:
    """``install_dir`` is a BfA install or, with ``product`` ("wow"), a retail one (the character
    is projected from retail's customization: character/retail.py).
    ``own_helmets``: a race BfA gave helmets of its own wears those (helmets.py); False, or a
    race without, wears the look template's, fitted to its head (helm.py). ``replace`` rebuilds a
    race already in the tables where its rows are, under any race built after it
    (``_take_out_later``); with ``on_top`` its rows go after every other race's instead (new ids:
    the races built after it are then rebuilt on it). ``own_racials``: a race BfA gave racials of
    its own (a racial skill line) gets them (racials.py) in place of its faction template's."""
    log = on_log or (lambda m: None)
    specs = [RaceSpec(alliance_id, ALLIANCE, alliance_template)] if alliance_id else []
    specs += [RaceSpec(horde_id, HORDE, horde_template)] if horde_id else []
    if not specs:
        raise ValueError("nothing to build: give an Alliance and/or Horde race id")
    if len({s.race_id for s in specs}) != len(specs):
        raise ValueError("the Alliance and Horde race ids must differ")
    for s in specs:
        _ = s.bit                                               # validates 1-32
    for p in (client_dir, server_dbc_dir):
        if p and _is_inside(out_dir, p):
            raise ValueError(f"output folder {out_dir} is inside {p} - pick a separate folder; "
                             "retroport never writes into the client or server")

    client_out = os.path.join(out_dir, "client")
    os.makedirs(client_out, exist_ok=True)

    # --- 1. the character itself (models, textures, customization rows) ---
    characters: dict[int, CharacterResult] = {}
    for sex_id, sex in enumerate(SEXES):
        manifest = os.path.join(out_dir, f"character-{sex.lower()}.json")
        if reuse and os.path.isfile(manifest):
            with open(manifest, encoding="utf-8") as fh:
                characters[sex_id] = CharacterResult.model_validate(json.load(fh))
            log(f"Reusing the converted {sex} character ({manifest})")
            continue
        log(f"Converting {race} {sex}...")
        ch = convert_character(install_dir, race, sex, client_out, race_id=specs[0].race_id, listfile=listfile,
                               dbd_dir=dbd_dir, target=target, product=product, on_log=log)
        write_verified(manifest, ch.model_dump_json(indent=1).encode("utf-8"))
        characters[sex_id] = ch
    cfs = characters[0].race
    if characters[1].race != cfs:
        raise ValueError("male and female conversions are of different races")
    key = cfs.upper()
    name = name or cfs
    res = RaceBuildResult(race=cfs, name=name, races=[s.__dict__ | {"bit": s.bit} for s in specs])
    for ch in characters.values():
        res.warnings += [f"{ch.sex}: {w}" for w in ch.warnings]
    _unbake_buckles(out_dir, client_out, log)

    # --- 2. client DBCs, built on the copies the client loads ---
    log(f"Reading {client_dir} ...")
    client = ClientData(client_dir, top=patch)
    layouts = {t: DbcLayout(t, dbd_dir) for t in set(RACIAL_CLIENT_TABLES + RACIAL_SERVER_TABLES + ["SoundEntries"])} \
        if own_racials else {}
    if own_outfits:
        layouts.update({t: DbcLayout(t, dbd_dir) for t in OUTFIT_TABLES})
    ctables = {}
    for t in CLIENT_TABLES + (RACIAL_CLIENT_TABLES if own_racials else []) + (OUTFIT_TABLES if own_outfits else []):
        hit = client.find(f"DBFilesClient\\{t}.dbc")
        if hit is None:
            raise DBCError(f"{t}.dbc is not in any archive of the client")
        res.client_sources[t] = hit[0]
        res.bases[f"client/DBFilesClient/{t}.dbc"] = {"source": hit[0], "sha256": sha256(hit[1])}
        ctables[t] = _parse(t, hit[1], layouts)
    _check_charsections_base(client, ctables["CharSections"], res.client_sources["CharSections"],
                             {s.race_id for s in specs})
    bfa = BfaInstall(install_dir, dbd_dir, listfile, product=product)
    bfa.fdids.update(_find_listfile_paths(listfile, {f"dbfilesclient/{t}.db2" for t in
                                                     ("uitextureatlas", "uitextureatlasmember", "globalstrings",
                                                      "barbershopstyle", "chrraces", "namegen") + HELMET_DB2
                                                     + RACIALS_DB2}))
    _weld_seams(cfs, client_out, res, log)
    _bone_influences(cfs, client_out, res, log)
    helmet_prefix, helmet_hides = (_race_helmets(bfa, race, client, ctables, look_template, cfs, out_dir, listfile,
                                                 res, log) if own_helmets else (None, None))
    if helmet_prefix is None:
        _place_helmets(client, ctables, look_template, cfs, client_out, res, log)
    _align_animations(client, ctables, look_template, cfs, client_out, res, log)
    _fit_bounds(client, cfs, client_out, res, log)
    stables = {}
    if server_dbc_dir:
        for t in SERVER_TABLES + (RACIAL_SERVER_TABLES if own_racials else []) + (OUTFIT_TABLES if own_outfits else []):
            p = os.path.join(server_dbc_dir, f"{t}.dbc")
            if not os.path.isfile(p):
                raise DBCError(f"{p} not found - is this the server's DBC folder?")
            with open(p, "rb") as fh:
                data = fh.read()
            res.bases[f"server/dbc/{t}.dbc"] = {"source": p, "sha256": sha256(data)}
            stables[t] = _parse(t, data, layouts)

    plan = _plan_racials(bfa, race, cfs, listfile, client, client_out, out_dir, ctables, stables, layouts, specs, res,
                         log) if own_racials else None
    outfit = _plan_outfits(bfa, race, cfs, listfile, client, client_out, out_dir, specs, res, log) \
        if own_outfits else None

    def own_rows(tables: dict) -> dict[str, int]:
        gone = racials.strip_racials(tables, plan) if plan else {}
        if outfit:
            gone.update(items.strip(tables, racials.block_base(outfit.block), racials.BLOCK_SIZE))
        return gone

    later: dict[str, dict[str, _Slot]] = {}
    if replace:
        for label, tables in (("client", ctables), ("server", stables)):
            if tables:
                later[label] = _take_out_later(tables, [s.race_id for s in specs], label, log, keep_place=not on_top,
                                               extra_strip=own_rows if plan or outfit else None)
    skip_skills = racials.racial_skill_lines(ctables["SkillLine"], layouts["SkillLine"]) if plan else None
    cmds = [ctables["CreatureModelData"]] + ([stables["CreatureModelData"]] if stables else [])
    cdis = [ctables["CreatureDisplayInfo"]] + ([stables["CreatureDisplayInfo"]] if stables else [])
    m_ids, d_ids = free_ids(2, *cmds), free_ids(2, *cdis)
    ids = (m_ids[0], m_ids[1], d_ids[0], d_ids[1])
    res.displays = {"male_model": ids[0], "female_model": ids[1], "male_display": ids[2], "female_display": ids[3]}
    barbers = [ctables["BarberShopStyle"]] + ([stables["BarberShopStyle"]] if stables else [])
    barber = _barber_styles(bfa, race, characters, specs, barbers, res.warnings)
    voice, sound_ids, csd_ids = _race_voice(bfa, race, cfs, client, ctables, client_out, listfile, res, log)
    names = _race_names(bfa, race, res, log)
    log(f"Client: adding races {[s.race_id for s in specs]} (displays {ids[2]}/{ids[3]})")
    item_dbc = client.find("DBFilesClient\\Item.dbc")
    displays = item_displays(DBCFile.from_bytes(item_dbc[1])) if item_dbc else None
    if displays is None:
        res.warnings.append("Item.dbc is not in the client: starting outfits keep the template's displays")
    res.rows["client"] = _apply(ctables, specs, name=name, cfs=cfs, look=look_template, ids=ids,
                                characters=characters, barber=barber, voice=voice, sound_ids=sound_ids,
                                csd_ids=csd_ids, helmet_prefix=helmet_prefix, helmet_hides=helmet_hides,
                                displays=displays, names=names, skip_skills=skip_skills)
    if plan:
        for k, v in _add_racials(ctables, plan, layouts, specs, client_out, bfa, res, log, client).items():
            res.rows["client"][k] = res.rows["client"].get(k, 0) + v
        _renumber_animations(plan, cfs, client_out, res, log)
    sound_groups = {}
    if outfit:
        _race_buckles(bfa, outfit, cfs, listfile, client_out, res, log)
        sound_groups = items.sound_groups(ctables["Item"], ctables["ItemDisplayInfo"], layouts["ItemDisplayInfo"])
        items.adopt_kinds(outfit, ctables["Item"], ctables["ItemDisplayInfo"], layouts["ItemDisplayInfo"])
        _add_outfits(ctables, outfit, specs, layouts, sound_groups, res.rows["client"])
    _put_back_later(ctables, later.get("client", {}), "client")
    layout = charsections_layout_problems(ctables["CharSections"], {s.race_id for s in specs})
    if layout:
        raise DBCError("CharSections rows for the new race are not laid out the way the client's "
                       f"customization cache requires (it would corrupt memory): {layout[:3]}")
    dbc_out = os.path.join(client_out, "DBFilesClient")
    os.makedirs(dbc_out, exist_ok=True)
    _save_tables(ctables, dbc_out, "client/DBFilesClient", res)
    _sweep(dbc_out, set(res.files), log)
    if stables:
        log("Server: adding the same races")
        res.rows["server"] = _apply(stables, specs, name=name, cfs=cfs, look=look_template, ids=ids,
                                    characters=None, barber=barber, voice=voice, sound_ids=sound_ids,
                                    helmet_prefix=helmet_prefix, displays=displays,
                                    skip_skills=racials.racial_skill_lines(stables["SkillLine"], layouts["SkillLine"])
                                    if plan else None)
        if plan:
            for k, v in _add_racials(stables, plan, layouts, specs, None, bfa, res, log).items():
                res.rows["server"][k] = res.rows["server"].get(k, 0) + v
        if outfit:
            _add_outfits(stables, outfit, specs, layouts, sound_groups, res.rows["server"])
        _put_back_later(stables, later.get("server", {}), "server")
        sdir = os.path.join(out_dir, "server", "dbc")
        os.makedirs(sdir, exist_ok=True)
        _save_tables(stables, sdir, "server/dbc", res)
        _sweep(sdir, set(res.files), log)
    else:
        res.warnings.append("no server DBC folder given: copy the client's ChrRaces, CharStartOutfit, "
                            "SkillRaceClassInfo, SkillLineAbility, Faction, CreatureDisplayInfo, CreatureModelData "
                            "EmotesTextSound and BarberShopStyle changes to the server yourself")

    # --- 3. character-create screen ---
    chr_races = ctables["ChrRaces"]
    max_races = len(playable_races(chr_races))

    def read_base(rel: str) -> bytes:
        hit = client.find(rel)
        if hit is None:
            raise DBCError(f"{rel} is not in any archive of the client")
        res.bases["client/" + rel.replace("\\", "/")] = {"source": hit[0], "sha256": sha256(hit[1])}
        return hit[1]

    lua = read_base(GLUE + "CharacterCreate.lua").decode("utf-8", "replace")
    xml = read_base(GLUE + "CharacterCreate.xml").decode("utf-8", "replace")
    parent = read_base(GLUE + "GlueParent.lua").decode("utf-8", "replace")
    strings = read_base(GLUE + "GlueStrings.lua").decode("utf-8", "replace")

    work = os.path.join(out_dir, "_work")
    os.makedirs(work, exist_ok=True)
    atlas_bytes = read_base(ATLAS)
    atlas_path = os.path.join(work, "atlas.blp")
    write_verified(atlas_path, atlas_bytes)
    atlas_img = blp.decode(atlas_path)
    known = glue.icon_coords(lua)
    look_file = chr_races.get_string(chr_races.find_by_id(look_template)[11]).upper()
    icon_coords: dict[str, tuple] = {}
    try:
        atlases = {i: r for i, r in bfa.table("UiTextureAtlas")}
        members = {str(r["CommittedName"]).lower(): r for _i, r in bfa.table("UiTextureAtlasMember")}
        src = {sx: members.get(f"raceicon-{cfs.lower()}-{sx.lower()}") for sx in SEXES}
        if not all(src.values()):
            raise ValueError(f"no raceicon-{cfs.lower()}-male/female in the BfA atlas list")
        used = {(round(c[0] * atlas_img.width / icons.CELL), round(c[2] * atlas_img.height / icons.CELL))
                for c in known.values()}
        round_bytes = read_base(ROUND_ATLAS)
        round_path = os.path.join(work, "round.blp")
        write_verified(round_path, round_bytes)
        round_img = blp.decode(round_path)
        if f"{key}_MALE" in known:
            col = round(known[f"{key}_MALE"][0] * atlas_img.width / icons.CELL)
            log(f"  race icons: reusing atlas column {col}")
        else:
            col = icons.free_column(atlas_img, used, round_img)
            log(f"  race icons: atlas column {col}")
        for row, sx in ((0, "Male"), (2, "Female")):
            m = src[sx]
            a = atlases[m["UiTextureAtlasID"]]
            p = os.path.join(work, f"bfa_atlas_{a['FileDataID']}.blp")
            if not os.path.isfile(p):
                write_verified(p, bfa.read(a["FileDataID"]))
            img = icons.crop_atlas_member(blp.decode(p), m)
            atlas_bytes = icons.splice(atlas_bytes, (col, row), img)
            # the info panel's round portrait: same cell, cut to the circle of column 0's (Human)
            mask = round_img.crop((0, row * icons.CELL, icons.CELL, (row + 1) * icons.CELL)).getchannel("A")
            round_bytes = icons.splice(round_bytes, (col, row), icons.round_icon(img, mask))
            icon_coords[f"{key}_{sx.upper()}"] = icons.coords(col, row, atlas_img.size)
        # spliced into the client's own atlases: their mip chains are whatever those files have
        for rel, data, size in ((ATLAS, atlas_bytes, atlas_img.size), (ROUND_ATLAS, round_bytes, round_img.size)):
            res.files.append(write_verified(os.path.join(client_out, *rel.split("\\")), data,
                                            lambda p, size=size: blp.verify_blp(p, size, full_chain=False)))
    except (ValueError, KeyError, blp.BLPError) as e:
        res.warnings.append(f"race icons: {e}; using the {look_file.title()} icons instead")
        for sx in ("MALE", "FEMALE"):
            icon_coords[f"{key}_{sx}"] = known[f"{look_file}_{sx}"]

    zoom_heads = {}
    if glue.bfa_style(lua):
        for sex_id, sx in enumerate(SEXES):
            stem = characters[sex_id].model[:-3]
            try:
                with open(stem + ".m2", "rb") as fh_m2, open(stem + "00.skin", "rb") as fh_skin:
                    zoom_heads[f"{key}_{sx.upper()}"] = zoom.head_spot(fh_m2.read(), fh_skin.read())
            except (OSError, zoom.ZoomError, struct.error) as e:
                res.warnings.append(f"customize zoom: {sx}: {e}; the screen won't zoom for it")
        if zoom_heads:
            log("  customize zoom: head " + ", ".join(f"{k} {h:.2f} up, {f:.2f} forward"
                                                        for k, (h, f) in sorted(zoom_heads.items())))
    lua = glue.patch_character_create_lua(lua, max_races, icon_coords, key, zoom_heads,
                                          core=_bfa_core_race(bfa, race, res))
    xml = glue.patch_character_create_xml(xml, max_races)
    look_cfs = chr_races.get_string(chr_races.find_by_id(look_template)[11])
    # the race's own scene (BfA's select scene, which 3.3.5's create screen uses too), else the look
    # template's; the look template's music either way, until the race's is ported
    own_scene = _select_scene(bfa, cfs, client, client_out, work, listfile, res, log)
    parent = glue.patch_glue_parent(parent, {} if own_scene else {key: look_cfs}, key, music={key: look_cfs})

    text = {}
    gs: dict[str, str] = {}
    try:
        gs = {str(r.get("BaseTag", "")): str(r.get("TagText_lang", "")) for _i, r in bfa.table("GlobalStrings")}
        text = {k: v for k, v in gs.items() if k in (f"RACE_INFO_{key}", f"RACE_INFO_{key}_FEMALE")}
    except ValueError as e:
        res.warnings.append(f"race description: {e}")
    if f"RACE_INFO_{key}" not in text:
        res.warnings.append("no race description in the BfA install; wrote a placeholder")
        text[f"RACE_INFO_{key}"] = f"{name}."
    text[f"{key}_DISABLED"] = f"{name} is not available."
    # its racial text: BfA's own when it has its own racials, else its faction template's
    own_lines = {}
    while plan and f"ABILITY_INFO_{key}{len(own_lines) + 1}" in gs:
        own_lines[len(own_lines) + 1] = gs[f"ABILITY_INFO_{key}{len(own_lines) + 1}"]
    for s in specs:
        if own_lines:
            text.update({f"ABILITY_INFO_{key}_{s.faction.upper()}{n}": v for n, v in own_lines.items()})
            continue
        tpl_file = chr_races.get_string(chr_races.find_by_id(s.template)[11]).upper()
        prefix = f"ABILITY_INFO_{tpl_file}"
        abilities = {int(k[len(prefix):]): v for k, v in glue.read_strings(strings, prefix).items()
                     if k[len(prefix):].isdigit()}
        for n in sorted(abilities):
            text[f"ABILITY_INFO_{key}_{s.faction.upper()}{n}"] = abilities[n]
    strings = glue.patch_glue_strings(strings, text, key)

    for fname, body in (("CharacterCreate.lua", lua), ("CharacterCreate.xml", xml), ("GlueParent.lua", parent),
                        ("GlueStrings.lua", strings)):
        res.files.append(write_verified(os.path.join(client_out, *GLUE.split("\\"), fname), body.encode("utf-8")))
    shutil.rmtree(work, ignore_errors=True)

    # --- 4. server SQL ---
    disp_map = {ids[2]: look_row_display(chr_races, look_template, 0), ids[3]: look_row_display(chr_races, look_template, 1)}
    racial_sql = None
    if plan:
        excluded = racials.racial_skill_lines(ctables["SkillLine"], layouts["SkillLine"])
        scripted = {s: n for s, n in racials.SCRIPTS.items() if s in plan.ported}
        scripted.update({s: p.script for s, p in plan.ported.items() if p.script and s not in scripted})
        racial_sql = sql.RacialSql(
            plan.skill_line, plan.name, sorted(excluded),
            ap_bonus={s: p.ap_bonus for s, p in plan.ported.items() if p.ap_bonus},
            scripts=scripted, procs={s: plan.ported[s].proc_flags for s in scripted if plan.ported[s].proc_flags},
            no_bonus=sorted(s for s in racials.SCRIPT_AMOUNTS if s in plan.ported),
            old_spells=sorted({r[2] for r in ctables["SkillLineAbility"].records if r[1] in excluded}),
            bound={script: racials.spells_named(stables["Spell"], layouts["Spell"], target)
                   for s, (script, target) in racials.BOUND_SCRIPTS.items() if s in plan.ported},
            pet_auras=[link for p in plan.ported.values() for link in p.pet_auras])
    start_items = [sql.StartItem(it.id, it.name, it.wear, it.material, it.inventory_type, item_class=it.item_class,
                                 **items.server_stats(it))
                   for it in sorted(outfit.items.values(), key=lambda i: i.id)] if outfit else None
    install, undo = sql.race_sql(specs, name, disp_map, racial_sql, start_items)
    sdir = os.path.join(out_dir, "server")
    os.makedirs(sdir, exist_ok=True)
    res.files.append(write_verified(os.path.join(sdir, f"{cfs}_race.sql"), install.encode("utf-8")))
    res.files.append(write_verified(os.path.join(sdir, f"{cfs}_race_undo.sql"), undo.encode("utf-8")))
    if racial_sql:
        res.files.append(write_verified(os.path.join(sdir, f"{cfs}_racials_characters.sql"),
                                        sql.characters_cleanup_sql(specs, name, racial_sql).encode("utf-8")))

    # --- 5. checks ---
    missing = []
    for s in specs:
        for r in ctables["CharSections"].records:
            if r[1] == s.race_id:
                for f in (4, 5, 6):
                    t = ctables["CharSections"].get_string(r[f])
                    if t and not os.path.isfile(os.path.join(client_out, *t.split("\\"))):
                        missing.append(t)
    for sx in SEXES:
        if not os.path.isfile(os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}.m2")):
            missing.append(f"Character\\{cfs}\\{sx}\\{cfs}{sx}.m2")
    if missing:
        raise ValueError(f"{len(missing)} file(s) the new rows point at are missing, e.g. {sorted(set(missing))[:3]}")
    buttons = len(set(glue._BUTTON.findall(xml)))
    if buttons < max_races and not glue.bfa_style(xml):
        raise ValueError(f"CharacterCreate.xml has {buttons} race buttons for {max_races} races")

    _sweep(os.path.join(client_out, "Item"), set(res.files), log)
    _write_install(out_dir, res, specs, server_dbc_dir)
    write_verified(os.path.join(out_dir, "race.json"), res.model_dump_json(indent=1).encode("utf-8"))
    log(f"Done: {out_dir}")
    return res


def _save_tables(tables: dict, folder: str, key: str, res: RaceBuildResult) -> None:
    """Write the tables into the package - but one loaded only for the racials that the build left
    as it was and the package has never held (SpellRange, say) stays out of it, and out of the client's patch,
    where a copy would hide later changes from the archives below. One the package held before
    stays in it even unchanged: it is installed (NameGen with the race's names), and leaving it
    out would make the install move it aside."""
    for t, d in tables.items():
        path = os.path.join(folder, f"{t}.dbc")
        base = res.bases.get(f"{key}/{t}.dbc")
        optional = t in RACIAL_CLIENT_TABLES + RACIAL_SERVER_TABLES + OUTFIT_TABLES \
            and t not in CLIENT_TABLES + SERVER_TABLES
        if optional and base and not os.path.isfile(path) and sha256(d.to_bytes()) == base["sha256"]:
            res.bases.pop(f"{key}/{t}.dbc")
            continue
        res.files.append(d.save(path))


def _plan_racials(bfa: BfaInstall, race: str, cfs: str, listfile: str, client: ClientData, client_out: str,
                  out_dir: str, ctables: dict, stables: dict, layouts: dict[str, DbcLayout], specs: list[RaceSpec],
                  res: RaceBuildResult, log: Callable[[str], None]) -> racials.RacialPlan | None:
    """BfA's racials for the race, ported (racials.py) with their visuals (visuals.py), or None: it
    keeps its faction template's."""
    if not stables:
        res.warnings.append("racials: no server DBC folder given; the race keeps its template's racials")
        return None
    try:
        spells = BfaSpells(bfa, lambda paths: _find_listfile_paths(listfile, paths))
        bfa_race, _row = _find_race(bfa, race)
        plan = racials.plan_racials(bfa, spells, bfa_race)
        if plan is None:
            res.warnings.append("racials: BfA has no racial skill line for the race; it keeps its template's")
            return None
        index_tables = {t: [ctables[t], stables[t]] for t in ("SpellCastTimes", "SpellDuration", "SpellRange",
                                                              "SpellRadius")}
        notes = racials.port_racials(plan, spells, layout=layouts["Spell"], index_tables=index_tables,
                                     range_layout=layouts["SpellRange"], block=specs[0].race_id,
                                     listfile_names=lambda fdids: sounds.listfile_names(listfile, fdids),
                                     skills={r[0] for r in ctables["SkillLine"].records}
                                     & {r[0] for r in stables["SkillLine"].records})
    except (racials.RacialsError, LayoutError, ValueError, KeyError) as e:
        res.warnings.append(f"racials: {e}; the race keeps its template's")
        return None
    plan.source = spells
    notes += _port_visuals(bfa, plan, cfs, listfile, client, client_out, out_dir, ctables, layouts, res, log)
    res.racials = {"skill_line": plan.skill_line, "name": plan.name,
                   "spells": {str(i): spells.names.get(i, "") for i in plan.spell_ids}, "notes": notes}
    res.warnings += [f"racials: {n}" for n in notes]
    log(f"  racials: BfA's {plan.name} ({plan.skill_line}): "
        + ", ".join(plan.ported[a].name for a in plan.abilities)
        + (f"; {len(plan.helpers)} spell(s) they use" if plan.helpers else ""))
    return plan


def _port_visuals(bfa: BfaInstall, plan: racials.RacialPlan, cfs: str, listfile: str, client: ClientData,
                  client_out: str, out_dir: str, ctables: dict, layouts: dict[str, DbcLayout], res: RaceBuildResult,
                  log: Callable[[str], None]) -> list[str]:
    """The racials' BfA visuals (visuals.py): their models converted into the package, their rows
    kept on the plan for _add_racials."""
    with_visuals = [p for p in plan.ported.values() if p.visual_id]
    if not with_visuals:
        return []
    try:
        bv = visuals.BfaVisuals(bfa, lambda paths: _find_listfile_paths(listfile, paths))
    except (ValueError, KeyError) as e:
        return [f"visuals: {e}; the racials have none"]
    base = racials.block_base(plan.block)
    ids = {t: visuals.BlockIds(base) for t in ("SpellVisual", "SpellVisualKit", "SpellVisualEffectName", "SoundEntries")}
    names = ctables["SpellVisualEffectName"]
    existing = {}
    for r in names.records:
        if base <= r[0] < base + racials.BLOCK_SIZE:
            continue                                    # the race's own, from its earlier install
        path = names.get_string(r[2]).lower()
        existing.setdefault(path[:-4] + ".m2" if path.endswith(".mdx") else path, r[0])
    client = _NotOurs(client, _previous_owns(out_dir))
    work = os.path.join(out_dir, "_work", "visuals")
    os.makedirs(work, exist_ok=True)
    notes = []
    for p in with_visuals:
        stuns = any(r[layouts["Spell"].at("EffectAura", k)] == 12 for r in p.rows for k in range(3))
        try:
            pv = visuals.port_visual(bv, p.visual_id, spell_name=p.name, stuns=stuns, layouts=layouts, ids=ids,
                                     client=client, client_out=client_out, work=work, listfile=listfile, cfs=cfs,
                                     existing_effect_names=existing)
        except (scenes.SceneError, ValueError, KeyError, OSError) as e:
            notes.append(f"{p.name}: visual {p.visual_id}: {e}; it plays none")
            continue
        if pv is None:
            continue
        plan.visuals[p.visual_id] = pv
        plan.visual_source = bv
        res.files += pv.files
        res.owns += [os.path.relpath(f, client_out).lower() for f in pv.files]
        notes += [f"{p.name}: visual: {n}" for n in pv.notes]
        log(f"  {p.name}: BfA's visual {p.visual_id}: {len(pv.kits)} kit(s), {len(pv.effect_names)} model(s) "
            f"({sum(1 for f in pv.files if f.lower().endswith('.m2'))} converted), {len(pv.sounds.kits)} sound(s), "
            f"animations {sorted(pv.anims)}")
    shutil.rmtree(work, ignore_errors=True)
    # BfA's own new animations (Haymaker's) under ids the client plays: CustomSpell slots the
    # race's models leave free (anims.py)
    wanted = {a for pv in plan.visuals.values() for a in pv.anims if a in visuals.BFA_ANIMS}
    if wanted:
        try:
            taken: set[int] = set()
            for sx in SEXES:
                with open(os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}.m2"), "rb") as fh:
                    taken |= anims.own_ids(fh.read())
        except (OSError, struct.error) as e:
            return notes + [f"animations {sorted(wanted)}: the race's models can't be read ({e}); the spells play none"]
        plan.anim_ids, left = visuals.anim_slots(wanted, taken)
        notes += [f"animation {a} ({visuals.BFA_ANIMS[a]}): the models use every CustomSpell slot; the spell plays "
                  "without it" for a in left]
        log("  racial animations in 3.3.5's spare spell slots: "
            + ", ".join(f"{visuals.BFA_ANIMS[a]} {a} -> {i}" for a, i in plan.anim_ids.items()))
    return notes


def _previous_owns(out_dir: str) -> set[str]:
    """Client files the package's earlier builds and installs put outside its folders: race.json's
    ``owns`` and every file an install receipt (installs/*.json) says it added."""
    owns: set[str] = set()
    try:
        with open(os.path.join(out_dir, "race.json"), encoding="utf-8") as fh:
            owns |= {o.lower() for o in json.load(fh).get("owns", [])}
    except (OSError, ValueError):
        pass
    receipts = os.path.join(out_dir, "installs")
    for name in (os.listdir(receipts) if os.path.isdir(receipts) else []):
        try:
            with open(os.path.join(receipts, name), encoding="utf-8") as fh:
                added = json.load(fh).get("added", [])
        except (OSError, ValueError):
            continue
        for item in added:
            parts = str(item.get("target", "")).replace("/", "\\").split("\\")
            low = [x.lower() for x in parts]
            if "data" in low and low.index("data") + 2 < len(parts):
                owns.add("\\".join(parts[low.index("data") + 2:]).lower())     # below Data\<patch>
    return owns


class _NotOurs:
    """The client as it is without the package's own earlier install: a spell model or sound the
    package put there is not the client's own, so a rebuild converts or extracts it again."""

    def __init__(self, client: ClientData, owns: set[str]):
        self.client, self.owns = client, owns

    def _ours(self, rel: str) -> bool:
        return rel.replace("/", "\\").lower() in self.owns

    def exists(self, rel: str) -> bool:
        return not self._ours(rel) and self.client.exists(rel)

    def find(self, rel: str):
        return None if self._ours(rel) else self.client.find(rel)


def _template_racial_line(tables: dict, layouts: dict[str, DbcLayout], template: int) -> int:
    lines = racials.racial_skill_lines(tables["SkillLine"], layouts["SkillLine"])
    bit = 1 << (template - 1)
    for r in tables["SkillRaceClassInfo"].records:
        if r[1] in lines and r[2] & bit:
            return r[1]
    raise DBCError(f"no racial skill line for template race {template}")


def _add_racials(tables: dict, plan: racials.RacialPlan, layouts: dict[str, DbcLayout], specs: list[RaceSpec],
                 client_out: str | None, bfa: BfaInstall, res: RaceBuildResult,
                 log: Callable[[str], None], client: ClientData | None = None) -> dict[str, int]:
    """The plan's rows in one set of tables (client: with ``client_out``, its icons too)."""
    mask = 0
    for s in specs:
        mask |= s.bit
    template_line = _template_racial_line(tables, layouts, specs[0].template)
    counts = {}
    if client_out is not None:
        before = len(tables["SpellIcon"].records)
        plan.icon_ids = racials.add_icons(tables["SpellIcon"], plan)
        counts["SpellIcon"] = len(tables["SpellIcon"].records) - before
        for fdid, path in plan.icons.items():
            rel = path + ".blp"
            work = os.path.join(client_out, "..", "_work")
            os.makedirs(work, exist_ok=True)
            src = os.path.join(work, f"icon_{fdid}.blp")
            write_verified(src, bfa.read(fdid))
            img = blp.decode(src)
            out = os.path.join(client_out, *rel.split("\\"))
            # BfA's icons carry one mip level; 3.3.5's own have the whole chain
            res.files.append(write_verified(out, blp.encode_dxt(img), lambda p, size=img.size: blp.verify_blp(p, size)))
            res.owns.append(rel.lower())
    visual_ids = {}
    if client_out is not None and plan.visuals:
        for k, v in _add_visuals(tables, plan, layouts, client_out, bfa, _NotOurs(client, _previous_owns(
                os.path.dirname(client_out))), res).items():
            counts[k] = counts.get(k, 0) + v
        visual_ids = {bfa_id: pv.visual[0] for bfa_id, pv in plan.visuals.items()}
    elif plan.visuals:
        visual_ids = {bfa_id: pv.visual[0] for bfa_id, pv in plan.visuals.items()}
    counts["Spell"] = racials.add_spells(tables["Spell"], layouts["Spell"], plan, plan.source, plan.icon_ids,
                                         visual_ids)
    racials.add_skill_line(tables["SkillLine"], layouts["SkillLine"], template_line, plan)
    counts["SkillLine"] = 1
    counts["SkillRaceClassInfo"] = racials.add_race_class_info(tables["SkillRaceClassInfo"], template_line, plan, mask)
    counts["SkillLineAbility"] = racials.add_abilities(tables["SkillLineAbility"], template_line, plan, mask)
    return counts


def _add_visuals(tables: dict, plan: racials.RacialPlan, layouts: dict[str, DbcLayout], client_out: str,
                 bfa: BfaInstall, client: ClientData, res: RaceBuildResult) -> dict[str, int]:
    """The client rows of the racials' visuals: SpellVisual, its kits (playing the animations
    under their 3.3.5 ids, plan.anim_ids), the models' effect names, the sounds; the sounds' files."""
    counts: dict[str, int] = {}

    def bump(k: str, n: int) -> None:
        counts[k] = counts.get(k, 0) + n

    names, LN = tables["SpellVisualEffectName"], layouts["SpellVisualEffectName"]
    LK = layouts["SpellVisualKit"]
    for pv in plan.visuals.values():
        tables["SpellVisual"].add_record(pv.visual)
        for kit in pv.kits:
            kit = list(kit)
            LK.set(kit, "AnimID", plan.anim_ids.get(LK.get(kit, "AnimID"), LK.get(kit, "AnimID")))
            tables["SpellVisualKit"].add_record(kit)
        for eid, name, path, scale in pv.effect_names:
            row = LN.blank()
            row[LN.at("ID")], row[LN.at("Name")], row[LN.at("FileName")] = eid, names.add_string(name), names.add_string(path)
            row[LN.at("AreaEffectSize")], row[LN.at("Scale")] = visuals._f(1.0), visuals._f(scale)
            row[LN.at("MinAllowedScale")], row[LN.at("MaxAllowedScale")] = visuals._f(0.01), visuals._f(100.0)
            names.add_record(row)
        if pv.sounds.kits:
            sounds.add_sound_entries(tables["SoundEntries"], pv.sounds, pv.sound_ids)
            written = visuals.write_sound_files(bfa.read, pv.sounds, client_out, client)
            res.files += written
            res.owns += [os.path.relpath(f, client_out).lower() for f in written]
        bump("SpellVisual", 1)
        bump("SpellVisualKit", len(pv.kits))
        bump("SpellVisualEffectName", len(pv.effect_names))
        bump("SoundEntries", len(pv.sounds.kits))
    return counts


def _renumber_animations(plan: racials.RacialPlan, cfs: str, client_out: str, res: RaceBuildResult,
                         log: Callable[[str], None]) -> None:
    """The race's body models play the racials' BfA animations under their 3.3.5 ids (anims.py)."""
    for sx in SEXES:
        own = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}.m2")
        try:
            with open(own, "rb") as fh:
                m2 = fh.read()
            data, changes = anims.renumber_sequences(m2, plan.anim_ids)
            missing = [a for a, i in plan.anim_ids.items() if anims.client_sequence(data, i) == anims.NO_SEQUENCE]
            if missing:
                res.warnings.append(f"racials: the {sx.lower()} model has no animation {missing}; "
                                    "the spell plays without it")
            if data != m2:
                write_verified(own, data)
            if changes:
                log(f"  {sx.lower()} racial animations under 3.3.5 ids: " + ", ".join(changes))
        except (ValueError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} racial animations left at BfA's ids (the client won't play them): {e}")


def _plan_outfits(bfa: BfaInstall, race: str, cfs: str, listfile: str, client: ClientData, client_out: str,
                  out_dir: str, specs: list[RaceSpec], res: RaceBuildResult,
                  log: Callable[[str], None]) -> items.OutfitPlan | None:
    """BfA's start armour for the race (items.py), its textures and icons written into the package;
    or None: it wears its template's."""
    try:
        bfa.fdids.update(_find_listfile_paths(listfile, {f"dbfilesclient/{t}.db2" for t in items.DB2}))
        bfa_race, row = _find_race(bfa, race)
        plan = items.collect(bfa.table, [bfa_race, row.get("NeutralRaceID") or 0], items.KITS.get(race))
    except (ValueError, KeyError) as e:
        res.warnings.append(f"outfits: {e}; the race wears its template's")
        return None
    if plan is None:
        res.warnings.append("outfits: BfA dresses the race in no armour of its own; it wears its template's")
        return None
    plan.block = specs[0].race_id
    items.assign(plan, racials.block_base(plan.block), _previous_items(out_dir, plan))
    wanted = {f for it in plan.items.values() for t in it.textures.values() for f in t.values()}
    wanted |= {f for it in plan.items.values() if it.model for f in it.model}
    names = sounds.listfile_names(listfile, wanted | {it.icon for it in plan.items.values() if it.icon})
    items.write_textures(plan, bfa.read, names, client_out, cfs)
    items.write_icons(plan, bfa.read, names, _NotOurs(client, _previous_owns(out_dir)).exists, client_out)
    _held_models(bfa, plan, names, cfs, listfile, client, client_out, out_dir, log)
    res.files += plan.files
    res.owns += plan.owns
    res.warnings += [f"outfits: {n}" for n in plan.notes]
    res.warnings += [f"outfits: {it.name}: not carried: {d}" for it in plan.items.values() for d in it.dropped]
    res.outfits = {"bfa_race": plan.bfa_race,
                   "items": {str(it.id): it.name for it in sorted(plan.items.values(), key=lambda i: i.id)},
                   "bfa": {str(it.bfa_id): it.id for it in sorted(plan.items.values(), key=lambda i: i.id)}}
    log(f"  start outfits: BfA's (race {plan.bfa_race}{', items.KITS' if race in items.KITS else ''}), "
        f"{len(plan.items)} piece(s) of armour, "
        f"{sum(1 for f in plan.files if f.lower().endswith('.blp'))} texture/icon file(s)")
    return plan


def _previous_items(out_dir: str, plan: items.OutfitPlan) -> dict[int, int]:
    """The ids the package's last build gave its outfit pieces (BfA id -> id), from race.json: its
    ``bfa`` map, or for a build before it kept one, the pieces' names."""
    try:
        with open(os.path.join(out_dir, "race.json"), encoding="utf-8") as fh:
            old = json.load(fh).get("outfits", {})
    except (OSError, ValueError):
        return {}
    if old.get("bfa"):
        return {int(b): int(i) for b, i in old["bfa"].items()}
    by_name = {name: int(i) for i, name in old.get("items", {}).items()}
    return {b: by_name[it.name] for b, it in plan.items.items() if it.name in by_name}


WAIST_MODELS = "Item\\ObjectComponents\\Waist\\"


def _body_files(cfs: str, client_out: str) -> dict[str, list[str]]:
    """Each body model's path (client-relative) -> its skins'."""
    out = {}
    for sx in SEXES:
        rel = f"Character\\{cfs}\\{sx}\\{cfs}{sx}.m2"
        with open(os.path.join(client_out, *rel.split("\\")), "rb") as fh:
            count = struct.unpack_from("<I", fh.read(0x48), 0x44)[0]
        out[rel] = [f"{rel[:-3]}{k:02d}.skin" for k in range(count)]
    return out


def _unbake_buckles(out_dir: str, client_out: str, log: Callable[[str], None]) -> None:
    """The bodies as they were before the last build baked its buckles into them (buckles.py), for
    a rebuild over them (``--reuse``); a body converted since is left as it is."""
    try:
        with open(os.path.join(out_dir, "race.json"), encoding="utf-8") as fh:
            files = json.load(fh).get("buckles", {}).get("files", {})
    except (OSError, ValueError):
        return
    restored = 0
    for rel, rec in files.items():
        path = os.path.join(client_out, *rel.split("\\"))
        try:
            with open(path, "rb") as fh:
                before = buckles.unbake(fh.read(), rec)
        except OSError:
            continue
        if before is not None:
            write_verified(path, before)
            restored += 1
    if restored:
        log(f"  belt buckles: {restored} body file(s) as they were before the last build's")


def _race_buckles(bfa: BfaInstall, outfit: items.OutfitPlan, cfs: str, listfile: str, client_out: str,
                  res: RaceBuildResult, log: Callable[[str], None]) -> None:
    """The outfit's belts' models drawn as part of the race's bodies, a belt geoset each (buckles.py);
    the belts' displays name those geosets. Failing that the belts keep BfA's band alone."""
    by_id = {it.id: it for it in outfit.items.values()}
    plan = buckles.plan([(it.id, *it.buckle, it.geosets[0]) for it in by_id.values() if it.buckle])
    if not plan:
        return
    try:
        meshes = {}
        for b in plan:
            if b.model not in meshes:
                model = bfa.read(b.model)
                sfid = helmets.chunks(model).get("SFID", b"")
                if len(sfid) < 4:
                    raise buckles.BucklesError(f"model {b.model} names no skin")
                meshes[b.model] = buckles.read_mesh(model, bfa.read(struct.unpack_from("<I", sfid)[0]))
        names = sounds.listfile_names(listfile, {b.texture for b in plan})
        textures = {}
        for b in plan:
            if b.texture not in names:
                raise buckles.BucklesError(f"texture {b.texture} is not in the listfile")
            stem = os.path.splitext(os.path.basename(names[b.texture].replace("\\", "/")))[0]
            b.texture_name = f"{WAIST_MODELS}{cfs}_{stem}.blp"
            textures[b.texture_name] = b.texture
        baked = {}
        for rel, skins in _body_files(cfs, client_out).items():
            files = [rel] + skins
            before = []
            for f in files:
                with open(os.path.join(client_out, *f.split("\\")), "rb") as fh:
                    before.append(fh.read())
            m2, new_skins = buckles.bake(before[0], before[1:], plan, meshes)
            for f, old, new in zip(files, before, [m2] + new_skins):
                baked[f] = (old, new, buckles.M2_HEAD if f == rel else buckles.SKIN_HEAD)
    except (buckles.BucklesError, ValueError, KeyError, OSError, struct.error) as e:
        res.warnings.append(f"outfits: belt buckles left off (the belts show BfA's band alone): {e}")
        return
    for rel, fdid in sorted(textures.items()):
        res.files.append(write_verified(os.path.join(client_out, *rel.split("\\")), bfa.read(fdid), blp.verify_blp))
        res.owns.append(rel.lower())
    for f, (old, new, head) in baked.items():
        write_verified(os.path.join(client_out, *f.split("\\")), new,
                       no_new_problems(old, m2_problems if head == buckles.M2_HEAD else skin_problems))
    res.buckles = {"geosets": {str(b.value): {"model": b.model, "texture": b.texture_name, "band": b.band,
                                              "items": b.items} for b in plan},
                   "files": {f: buckles.record(old, new, head) for f, (old, new, head) in baked.items()}}
    for b in plan:
        for i in b.items:
            by_id[i].geosets[0] = b.value
    log(f"  belt buckles: {len(plan)} belt geoset(s) baked into the bodies "
        f"({', '.join(f'{buckles.BELT + b.value} {os.path.basename(b.texture_name)}' for b in plan)})")


def _held_models(bfa: BfaInstall, plan: items.OutfitPlan, names: dict[int, str], cfs: str, listfile: str,
                 client: ClientData, client_out: str, out_dir: str, log: Callable[[str], None]) -> None:
    """The outfit's weapons, shields and off-hands: BfA's models converted as the helmets are
    (helmets.convert_item_models), under the race's name; a piece whose model fails keeps none."""
    held = items.held_models(plan, bfa.read, names, client_out, cfs)
    if not held:
        return
    work = os.path.join(out_dir, "_work", "weapons")
    got = helmets.convert_item_models(held, client_out=client_out, read=bfa.read, casc=bfa.casc, listfile=listfile,
                                      exists=client.exists, work_dir=work)
    shutil.rmtree(work, ignore_errors=True)
    plan.files += got.files
    plan.owns += [os.path.relpath(f, client_out).lower() for f in got.files]
    for stem, why in sorted(got.failed.items()):
        for it in plan.items.values():
            if it.model_name == f"{stem}.mdx":
                it.model_name = it.model_texture = ""
                it.dropped.append(f"its model ({why})")
        if stem.startswith("texture "):
            plan.notes.append(f"{stem}: {why}")
    log(f"  start weapons: {len(got.own)} model(s) from BfA ({', '.join(sorted(got.own))})"
        + (f", {len(got.textures)} texture(s)" if got.textures else "")
        + (f", {got.redrawn} batch(es) given WarcraftXL's shaders" if got.redrawn else ""))


def _add_outfits(tables: dict, outfit: items.OutfitPlan, specs: list[RaceSpec], layouts: dict[str, DbcLayout],
                 sound_groups: dict, counts: dict[str, int]) -> None:
    """The outfit items' rows and the race's start outfits wearing them, in one set of tables."""
    for k, v in items.add_items(tables, outfit, layouts, sound_groups).items():
        counts[k] = counts.get(k, 0) + v
    items.dress(tables["CharStartOutfit"], [s.race_id for s in specs], outfit,
                {r[0]: r for r in tables["Item"].records})


def _race_voice(bfa: BfaInstall, race: str, cfs: str, client: ClientData, ctables: dict, client_out: str,
                listfile: str, res: RaceBuildResult, log: Callable[[str], None]
                ) -> tuple[sounds.Voice | None, dict[int, int], dict[int, int]]:
    """The race's own voice from BfA (sounds.py), its files extracted under ``client_out`` - or
    None, and the race speaks with the look template's voice."""
    try:
        bfa.fdids.update(_find_listfile_paths(listfile, {f"dbfilesclient/{t}.db2" for t in VOICE_DB2}))
        hit = client.find("DBFilesClient\\EmotesText.dbc")
        if hit is None:
            raise DBCError("EmotesText.dbc is not in any archive of the client")
        emotes = {r[0] for r in DBCFile.from_bytes(hit[1]).records}
        vocal_enums = {r[1] for r in ctables["VocalUISounds"].records}
        bfa_race, _row = _find_race(bfa, race)
        voice = sounds.collect_voice(bfa.table, bfa_race, cfs, emotes=emotes, vocal_enums=vocal_enums,
                                     names=lambda fdids: sounds.listfile_names(listfile, fdids))
        files = sounds.write_voice_files(bfa.read, voice, client_out)
    except (ValueError, KeyError) as e:                          # DBCError is a ValueError
        res.warnings.append(f"voice: {e}; the race speaks with the look template's voice")
        return None, {}, {}
    res.files += files
    res.warnings += [f"voice: {s}" for s in voice.skipped]
    if not voice.kits:
        res.warnings.append("voice: no sound of the race could be extracted; it speaks with the look template's voice")
        return None, {}, {}
    sound_ids, csd_ids = sounds.assign_ids(voice, ctables["SoundEntries"], ctables["CreatureSoundData"])
    log(f"  voice: {len(voice.kits)} sounds ({len(files)} files): {len(voice.emotes)} emote sounds, "
        f"{len(voice.vocal)} spoken errors, combat sounds for {len(voice.creature)} sex(es)")
    return voice, sound_ids, csd_ids


@dataclass
class _Slot:
    """Where a rebuilt race's rows were in one table (``_take_out_later``)."""
    head: int                       # the index its first row had
    tail: list                      # the rows built after it, set aside for now
    ids: list[int]                  # the ids its rows had, in order


def _take_out_later(tables: dict, race_ids: list[int], label: str, log: Callable[[str], None], *,
                    keep_place: bool = True, extra_strip: Callable[[dict], dict] | None = None) -> dict[str, _Slot]:
    """Remove a previous build of the races (strip_race) so this build adds them again - where
    they were: each table's rows that builds after it added (a race installed on top of this one)
    come out for now and go back after this build's (``_put_back_later``). The new rows take the
    old ones' places and ids, so a rebuild that changes nothing gives the tables as installed and
    the package on top stays current. ``keep_place=False``: only strip."""
    before = {name: list(d.records) for name, d in tables.items()}
    for rid in race_ids:
        if tables["ChrRaces"].find_by_id(rid):
            log(f"  removing the previous build of race {rid} ({label}): {strip_race(tables, rid)}")
    if extra_strip is not None:
        gone = extra_strip(tables)
        if gone:
            log(f"  removing the previous build's racials and outfits ({label}): {gone}")
    later: dict[str, _Slot] = {}
    if not keep_place:
        return later
    for name, d in tables.items():
        kept = {id(r) for r in d.records}
        gone = [r for r in before[name] if id(r) not in kept]
        if not gone:
            continue
        first = next(i for i, r in enumerate(before[name]) if id(r) not in kept)
        tail = [r for r in before[name][first:] if id(r) in kept]
        d.records = before[name][:first]
        later[name] = _Slot(first, tail, [i for r in gone if (i := _row_id(name, r)) is not None])
    moved = {name: len(slot.tail) for name, slot in later.items() if slot.tail}
    if moved:
        log(f"  kept in place under {sum(moved.values())} row(s) built after it ({label}: "
            + ", ".join(sorted(moved)) + ")")
    return later


# tables whose rows have no id of their own (CharacterFacialHairStyles: race, sex, variation, ...)
NO_ID_TABLES = {"CharBaseInfo", "CharacterFacialHairStyles"}
# tables no other row points at by id: a rebuilt race's rows there take back the ids its old ones
# had, whatever ids were free when it was built
LEAF_TABLES = {"NameGen", "CharSections", "CharHairGeosets", "EmotesTextSound", "VocalUISounds", "CharStartOutfit"}


def _row_id(name: str, row) -> int | None:
    if name in NO_ID_TABLES:
        return None
    return struct.unpack_from("<I", row)[0] if isinstance(row, (bytes, bytearray)) else row[0]


def _with_id(row, new_id: int):
    if isinstance(row, (bytes, bytearray)):
        return struct.pack("<I", new_id) + bytes(row[4:])
    return [new_id] + list(row[1:])


def _put_back_later(tables: dict, later: dict[str, _Slot], label: str) -> None:
    """The rows ``_take_out_later`` set aside, back after this build's. In a table nothing points
    at (``LEAF_TABLES``) this build's rows take the ids its old rows had, any more than those ids
    above every row; elsewhere a build that reaches the ids of the rows set aside (it added more
    rows than the one it replaces) is refused."""
    for name, slot in later.items():
        d = tables[name]
        ours = d.records[slot.head:]
        theirs = {i for r in slot.tail if (i := _row_id(name, r)) is not None}
        if name in LEAF_TABLES and slot.ids:
            used = theirs | set(slot.ids) | {i for r in d.records[:slot.head] if (i := _row_id(name, r)) is not None}
            fresh = iter(range(max(used) + 1, max(used) + 1 + len(ours)))
            ours = [_with_id(r, slot.ids[k] if k < len(slot.ids) else next(fresh)) for k, r in enumerate(ours)]
        clash = sorted(theirs & {i for r in ours if (i := _row_id(name, r)) is not None})
        if clash:
            raise DBCError(f"{name} ({label}): this build needs ids a race built after it uses ({clash[:5]}) - "
                           "rebuild with --on-top, then rebuild the races built after it on top of it")
        d.records = d.records[:slot.head] + ours + slot.tail


def _race_names(bfa: BfaInstall, race: str, res: RaceBuildResult,
                log: Callable[[str], None]) -> list[tuple[str, int]] | None:
    """BfA's random names for the race, (name, sex) in BfA's order: its own NameGen rows, else its
    neutral race's (a Pandaren's are on the neutral one) - or None: the look template's."""
    try:
        bfa_race, row = _find_race(bfa, race)
        rows = sorted(bfa.table("NameGen"), key=lambda t: t[0])
        for rid in (bfa_race, row.get("NeutralRaceID") or 0):
            names = [(str(r["Name"]), int(r["Sex"])) for _i, r in rows
                     if rid and r["RaceID"] == rid and int(r["Sex"]) in (0, 1) and r["Name"]]
            if names:
                log(f"  random names: BfA's, {sum(1 for _n, sx in names if sx == 0)} male and "
                    f"{sum(1 for _n, sx in names if sx == 1)} female")
                return names
    except (ValueError, KeyError) as e:
        res.warnings.append(f"random names: {e}; the race takes the look template's")
        return None
    res.warnings.append("random names: BfA has none for the race; it takes the look template's")
    return None


def _bfa_core_race(bfa: BfaInstall, race: str, res: RaceBuildResult) -> bool:
    """BfA lists the race with the classic ones (not an allied race): the BfA-style create screen
    shows it on its core page."""
    try:
        _rid, row = _find_race(bfa, race)
    except ValueError as e:
        res.warnings.append(f"create screen page: {e}; the race goes on the allied page")
        return False
    return not int(row.get("Flags", 0)) & BFA_ALLIED_RACE


def _look_model(client: ClientData, ctables: dict, look: int, sex_id: int) -> tuple[str, bytes, bytes]:
    """(path stem, .m2, 00.skin) of the look template's model for one sex, as the client loads it."""
    chr_races, cdi, cmd = ctables["ChrRaces"], ctables["CreatureDisplayInfo"], ctables["CreatureModelData"]
    look_row = chr_races.find_by_id(look)
    display = cdi.find_by_id(look_row[4 + sex_id]) if look_row else None
    model = cmd.find_by_id(display[1]) if display else None
    if model is None:
        raise helm.HelmError(f"no model row for the look template's {SEXES[sex_id].lower()} display")
    path = cmd.get_string(model[2])
    base = path[:path.rfind(".")]
    m2, skin = client.find(base + ".m2"), client.find(base + "00.skin")
    if not m2 or not skin:
        raise helm.HelmError(f"{base}.m2 or its 00.skin is not in the client")
    return base, m2[1], skin[1]


def _place_helmets(client: ClientData, ctables: dict, look: int, cfs: str, client_out: str,
                   res: RaceBuildResult, log: Callable[[str], None]) -> None:
    """Fit the look template's helmets on each model (helm.py): the race wears that template's
    helmet models, scaled to the race's cranium."""
    for sex_id, sx in enumerate(SEXES):
        try:
            base, t_m2, t_skin = _look_model(client, ctables, look, sex_id)
            own = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}")
            with open(own + ".m2", "rb") as fh:
                m2 = fh.read()
            with open(own + "00.skin", "rb") as fh:
                skin = fh.read()
            data, fit = helm.place_helm_attachment(m2, skin, t_m2, t_skin)
            write_verified(own + ".m2", data)
            template = base.rsplit(chr(92), 1)[-1]
            scales = ("x" + "/".join(f"{v:.2f}" for v in fit.scales) if len(set(fit.scales)) > 1
                      else f"x{fit.scales[0]:.2f}")
            log(f"  {sx.lower()} helmets ({template}'s): origin {tuple(round(v, 3) for v in fit.old)} -> "
                f"{tuple(round(v, 3) for v in fit.spot)}, scaled {scales}"
                + (f", turned {fit.turn:+.1f} deg" if fit.turn else ""))
            if not fit.scaled_bone:
                wanted, _spot = helm.fit_helmet(m2, skin, t_m2, t_skin)
                if wanted != (1.0, 1.0, 1.0):
                    res.warnings.append(f"{sx} helmets not scaled (x{'/'.join(f'{v:.2f}' for v in wanted)} needed): "
                                        f"attachment 11's bone {fit.bone} carries other things")
        except (helm.HelmError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} helmet attachment left as converted: {e}")


def _race_helmets(bfa: BfaInstall, race: str, client: ClientData, ctables: dict, look: int, cfs: str, out_dir: str,
                  listfile: str, res: RaceBuildResult, log: Callable[[str], None]
                  ) -> tuple[str | None, dict[int, set[int]] | None]:
    """The race's own helmets from BfA (helmets.py): every helmet the client has for the look
    template, as BfA made it for the race, BfA's helmet attachment back on both bodies, and what
    BfA's helmets hide on the race. (None, None) when BfA gave the race no helmets of its own: it
    then wears the look template's (``_place_helmets``)."""
    client_out = os.path.join(out_dir, "client")
    try:
        bfa_race, race_row = _find_race(bfa, race)
        prefix = str(race_row.get("ClientPrefix") or "")
        chr_races = ctables["ChrRaces"]
        t_prefix = chr_races.get_string(chr_races.find_by_id(look)[6])
        idi = client.find("DBFilesClient\\ItemDisplayInfo.dbc")
        if not prefix or not t_prefix or idi is None:
            return None, None
        bases = helmets.helmet_bases(DBCFile.from_bytes(idi[1]), client.exists, t_prefix)
        spelled = {b.lower(): b for b in bases}
        found = helmets.race_models(helmets.listfile_variants(listfile, bases),
                                    (r for _i, r in bfa.table("ModelFileData")),
                                    {i: r for i, r in bfa.table("ComponentModelFileData")}, bfa_race)
        if not found:
            return None, None
        models = {(spelled[b], sex): fdid for (b, sex), fdid in found.items()}
        bodies = _find_listfile_paths(listfile, {f"character/{cfs.lower()}/{sx.lower()}/{cfs.lower()}{sx.lower()}.m2"
                                                 for sx in SEXES})
        attachments = {}
        for sex_id, sx in enumerate(SEXES):
            # BfA's attachment back: these helmets hang where BfA hangs them (undoing an earlier fit)
            fdid = bodies[f"character/{cfs.lower()}/{sx.lower()}/{cfs.lower()}{sx.lower()}.m2"]
            attachments[sex_id] = helmets.source_attachment(bfa.read, fdid)
            own = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}.m2")
            with open(own, "rb") as fh:
                m2 = fh.read()
            data = helm.set_attachment(m2, attachments[sex_id])
            if data != m2:
                write_verified(own, data)
        work = os.path.join(out_dir, "_work", "helmets")
        hs = helmets.convert_race_helmets(models, prefix=prefix, client_out=client_out, read=bfa.read,
                                          casc=bfa.casc, listfile=listfile, exists=client.exists, work_dir=work)
        shutil.rmtree(work, ignore_errors=True)
        hides = helmets.race_hides((r for _i, r in bfa.table("HelmetGeosetData")), bfa_race)
    except (helmets.HelmetsError, helm.HelmError, ValueError, KeyError, struct.error, OSError) as e:
        res.warnings.append(f"the race's own helmets: {e}; it wears the look template's, fitted")
        return None, None
    res.files += hs.files
    carried = []
    for sex_id, sx in enumerate(SEXES):
        # a helmet BfA has no version of for the race: the template's, carried to where helm.py fits it
        missing = [b for b in bases if (b, sex_id) not in hs.own
                   and client.exists(f"{helmets.HEAD}{b}_{t_prefix}{sx[0]}.m2")]
        if not missing:
            continue
        try:
            _base, t_m2, t_skin = _look_model(client, ctables, look, sex_id)
            own = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}")
            with open(own + ".m2", "rb") as fh:
                m2 = fh.read()
            with open(own + "00.skin", "rb") as fh:
                skin = fh.read()
            fit = helm.fit_attachment(m2, skin, t_m2, t_skin)
            _n_b, o_b = struct.unpack_from("<II", m2, 0x2C)
            pivot = struct.unpack_from("<3f", m2, o_b + fit.bone * 0x58 + 0x4C)
            linear, offset = helmets.carry_transform(fit, pivot, attachments[sex_id])
        except (helm.HelmError, helmets.HelmetsError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} helmets BfA has no {cfs} version of ({len(missing)}) are missing: {e}")
            continue
        for b in missing:
            try:
                res.files += helmets.carry_template_helmet(lambda p: (h := client.find(p)) and h[1], b, sex_id,
                                                           t_prefix, prefix, linear, offset, client_out)
                carried.append(f"{b}_{prefix}{sx[0]}")
            except (helmets.HelmetsError, struct.error, OSError) as e:
                hs.failed[(b, sex_id)] = str(e)
    res.helmet_prefix = prefix
    counts = [sum(1 for (_b, s) in hs.own if s == sex_id) for sex_id in (0, 1)]
    log(f"  helmets: BfA's own {cfs} models ({prefix}), {counts[0]} male and {counts[1]} female"
        + (f"; the {t_prefix} model carried to fit for {', '.join(carried)}" if carried else "")
        + (f"; {len(hs.textures)} texture(s) from BfA" if hs.textures else "")
        + (f"; {hs.redrawn} batch(es) given WarcraftXL's shaders" if hs.redrawn else "")
        + (f"; {hs.emitters} particle emitter(s) fixed" if hs.emitters else "")
        + (f"; {hs.influences} skin section(s) given their bone influences" if hs.influences else ""))
    for (b, sex), why in sorted(hs.failed.items()):
        res.warnings.append(f"helmet {b}" + (f" ({SEXES[sex].lower()})" if sex >= 0 else "") + f": {why}")
    return prefix, hides


def _align_animations(client: ClientData, ctables: dict, look: int, cfs: str, client_out: str,
                      res: RaceBuildResult, log: Callable[[str], None]) -> None:
    """Blend bits and one-shot emote lengths from the look template's sequences (anims.py)."""
    try:
        em, ad = client.find("DBFilesClient\\Emotes.dbc"), client.find("DBFilesClient\\AnimationData.dbc")
        if not em or not ad:
            raise helm.HelmError("Emotes.dbc or AnimationData.dbc is not in the client")
        ad_dbc = DBCFile.from_bytes(ad[1])
        one_shot = anims.one_shot_emotes(DBCFile.from_bytes(em[1]).records,
                                         {r[0]: ad_dbc.get_string(r[1]) for r in ad_dbc.records})
    except (helm.HelmError, ValueError) as e:
        res.warnings.append(f"animations left as converted: {e}")
        return
    for sex_id, sx in enumerate(SEXES):
        try:
            _base, t_m2, _t_skin = _look_model(client, ctables, look, sex_id)
            own = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}.m2")
            with open(own, "rb") as fh:
                m2 = fh.read()
            data, changes = anims.align_sequences(m2, t_m2, one_shot)
            if changes:
                write_verified(own, data)
                log(f"  {sx.lower()} animations like the look template's: " + "; ".join(changes))
        except (helm.HelmError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} animations left as converted: {e}")


def _weld_seams(cfs: str, client_out: str, res: RaceBuildResult, log: Callable[[str], None]) -> None:
    """Seams between geosets move as one (seams.py): coincident vertices get one set of weights."""
    for sx in SEXES:
        base = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}")
        try:
            with open(base + ".m2", "rb") as fh:
                m2 = fh.read()
            paths = [f"{base}{k:02d}.skin" for k in range(struct.unpack_from("<I", m2, 0x44)[0])]
            skins = []
            for p in paths:
                with open(p, "rb") as fh:
                    skins.append(fh.read())
            data, welded_skins, report = seams.weld_seams(m2, skins)
            if data != m2:
                write_verified(base + ".m2", data)
            for p, old, new in zip(paths, skins, welded_skins):
                if new != old:
                    write_verified(p, new)
            log(f"  {sx.lower()} seams: {report.welded} vertices take their neighbouring geoset's weights"
                + (f", {report.blocked} left (their palette lacks the bones)" if report.blocked else ""))
        except (ValueError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} seams left as converted: {e}")


def _select_scene(bfa: BfaInstall, cfs: str, client: ClientData, client_out: str, work: str, listfile: str,
                  res: RaceBuildResult, log: Callable[[str], None]) -> bool:
    """BfA's select scene for the race (ChrRaces.SelectScreenFileDataID: ``UI_<ClientFileString>``,
    a Kul Tiran's the docks of Boralus) converted into the race's scene folder with its textures
    (glue/scenes.py, as the glue package converts the ten classic races'). 3.3.5 stands the race's
    characters in it on character select (GetSelectBackgroundModel) and its new characters on
    3.3.5's own create screen. False, with a warning, when BfA has none or it won't convert: the
    race then borrows its look template's."""
    if not engine.available():
        res.warnings.append("select scene: the conversion engine is not installed; borrowing the look template's")
        return False
    try:
        fdid = scenes.select_scene_fdid(bfa, cfs)
        data = bfa.read(fdid) if fdid else None
    except (ValueError, KeyError, OSError) as e:
        res.warnings.append(f"select scene: {e}; borrowing the look template's")
        return False
    if not data:
        res.warnings.append(f"select scene: BfA has none for {cfs}; borrowing the look template's")
        return False
    stem = f"UI_{cfs}"
    try:
        scene = scenes.convert_scene(bfa, data, stem, client=client, client_out=client_out, work=work,
                                     lf=scenes.scoped_listfile(bfa, [data], work, listfile))
    except scenes.SceneError as e:
        res.warnings.append(f"select scene {e}; borrowing the look template's")
        return False
    res.files += scene.files
    res.warnings += [f"select scene {w}" for w in scene.warnings]
    log(f"  select scene {stem}: BfA's, {scene.summary()}")
    return True


def _bone_influences(cfs: str, client_out: str, res: RaceBuildResult, log: Callable[[str], None]) -> None:
    """Skin sections the conversion left at 0 bone influences get the count their vertices weigh
    (fixup.py) - here too for a body converted before the character conversion did it."""
    for sx in SEXES:
        base = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}")
        try:
            with open(base + ".m2", "rb") as fh:
                count = struct.unpack_from("<I", fh.read(), 0x44)[0]
            filled = fixup.fill_bone_influences(base + ".m2", [f"{base}{k:02d}.skin" for k in range(count)])
            if filled:
                log(f"  {sx.lower()}: {filled} skin section(s) given the bone influences their vertices weigh")
        except (ValueError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} bone influences left as converted: {e}")


def _sweep(folder: str, keep: set[str], log: Callable[[str], None]) -> None:
    """Remove files an earlier build of this package left in ``folder`` that this one didn't write
    (``keep``), so the package holds what this build makes - install moves aside what an earlier
    install of them put in the client (install.py). Only for a folder whose every file the build
    lists (the package's Item folder: the race's helmets; its DBC folders: a table an earlier build
    changed and this one doesn't); a body model's own textures, say, come from the character
    conversion unlisted."""
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    gone = 0
    for dirpath, _dirs, files in os.walk(folder, topdown=False):
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.normcase(os.path.abspath(p)) not in keep:
                os.remove(p)
                gone += 1
        if dirpath != folder and not os.listdir(dirpath):
            os.rmdir(dirpath)
    if gone:
        log(f"  package: removed {gone} file(s) an earlier build made that this one doesn't")


def _fit_bounds(client: ClientData, cfs: str, client_out: str, res: RaceBuildResult,
                log: Callable[[str], None]) -> None:
    """Header bounds from the sequences the client can play (bounds.py)."""
    ad = client.find("DBFilesClient\\AnimationData.dbc")
    if not ad:
        res.warnings.append("model bounds left as converted: AnimationData.dbc is not in the client")
        return
    known = {r[0] for r in DBCFile.from_bytes(ad[1]).records}
    for sx in SEXES:
        own = os.path.join(client_out, "Character", cfs, sx, f"{cfs}{sx}.m2")
        try:
            with open(own, "rb") as fh:
                m2 = fh.read()
            data, old, new = bounds.fit_header_bounds(m2, known)
            if data != m2:
                write_verified(own, data)
            log(f"  {sx.lower()} bounds from the animations the client plays: radius {old:.2f} -> {new:.2f}")
        except (ValueError, struct.error, OSError) as e:
            res.warnings.append(f"{sx} bounds left as converted: {e}")


def _check_charsections_base(client: ClientData, cs: DBCFile, source: str, skip_races: set[int],
                             sample: int = 60) -> None:
    """Refuse a CharSections whose textures the client doesn't have: building on it would point
    every existing race at missing textures (green hair, white tusks)."""
    rows = [r for r in cs.records if r[1] not in skip_races and cs.get_string(r[4])]
    step = max(1, len(rows) // sample)
    picked = rows[::step][:sample]
    missing = [cs.get_string(r[4]) for r in picked if not client.exists(cs.get_string(r[4]))]
    if picked and len(missing) > len(picked) // 5:
        raise DBCError(f"the CharSections.dbc taken as the base ({source}) points at textures this client "
                       f"doesn't have ({len(missing)} of {len(picked)} sampled, e.g. {missing[0]}); it is "
                       "probably not the copy the game uses - nothing was written")


def _barber_styles(bfa: BfaInstall, race: str, characters: dict[int, CharacterResult], specs: list[RaceSpec],
                   tables: list[DBCFile], warnings: list[str]) -> dict[int, list[BarberStyle]]:
    """Hair-style and facial-hair barber rows for each race id, named as in BfA and limited to
    the variations that were converted. Ids are free in every given table (client and server).
    A retail race (no BarberShopStyle since 9.0) is named by its customization choices."""
    if any(ch.source == "retail" for ch in characters.values()):
        rows = [(sex, typ, int(var), name) for sex, ch in sorted(characters.items())
                for typ, key in ((0, "hair"), (2, "facial"))
                for var, name in sorted(ch.names.get(key, {}).items(), key=lambda t: int(t[0]))]
        if not rows:
            warnings.append("barber shop: the retail customization named no styles; no barber rows written")
            return {}
        ids = iter(free_ids(len(rows) * len(specs), *tables))
        return {s.race_id: [BarberStyle(next(ids), typ, sex, var, name, 1.0) for sex, typ, var, name in rows]
                for s in specs}
    try:
        bfa_race, _row = _find_race(bfa, race)
        source = [r for _i, r in bfa.table("BarberShopStyle") if r["Race"] == bfa_race and r["Type"] in (0, 2)]
    except ValueError as e:
        warnings.append(f"barber shop: {e}; no barber rows written")
        return {}
    have = {(sex, 0): {h["VariationID"] for h in ch.hair} for sex, ch in characters.items()}
    have.update({(sex, 2): {f["VariationID"] for f in ch.facial} for sex, ch in characters.items()})
    rows = sorted({(r["Sex"], r["Type"], r["Data"]): r for r in source
                   if r["Data"] in have.get((r["Sex"], r["Type"]), set())}.values(),
                  key=lambda r: (r["Sex"], r["Type"], r["Data"]))
    if not rows:
        warnings.append("barber shop: BfA has no hair/facial-hair rows for this race")
        return {}
    ids = iter(free_ids(len(rows) * len(specs), *tables))
    return {s.race_id: [BarberStyle(next(ids), r["Type"], r["Sex"], r["Data"], str(r["DisplayName_lang"]),
                                    float(r.get("Cost_Modifier", 1.0))) for r in rows] for s in specs}


def look_row_display(chr_races: DBCFile, race_id: int, sex: int) -> int:
    return chr_races.find_by_id(race_id)[4 + sex]


def _write_install(out_dir: str, res: RaceBuildResult, specs: list[RaceSpec], server_dbc_dir: str | None) -> None:
    lines = [f"{res.name} ({res.race}) - playable race package built by retroport", "",
             "Races: " + ", ".join(f"{s.race_id} = {res.name} ({s.faction}, copies race {s.template})" for s in specs),
             f"Base displays: male {res.displays['male_display']}, female {res.displays['female_display']}", "",
             "INSTALL (recommended): retroport install - it never overwrites a file you changed since",
             "this build, backs up everything it replaces, and writes an undo receipt:",
             f"  retroport install \"{out_dir}\" --client <WoW folder> --patch patch-H.MPQ"
             + (f" --server-dbc \"{server_dbc_dir}\"" if server_dbc_dir else "") + " --clean",
             "  (shows the plan; add --apply to install; undo: retroport uninstall <installs\\...json>)",
             "  If it reports 'stale', you changed that file after the build: rebuild with",
             "  `retroport race ... --reuse --replace` and the package is rebuilt on your current copy.", "",
             "BY HAND: copy everything inside client\\ into your top folder patch (e.g. Data\\patch-H.MPQ\\);",
             "  the DBCs were built on the copies your client loads (sources below), so they keep what",
             "  was in them as long as you haven't changed those files since this build.", ""]
    if any(s.race_id >= 22 for s in specs):
        lines += ["CLIENT EXTENSION: the client plays emote sounds (/laugh, /cheer, ...) for race ids 0-21 only;",
                  "  races 22+ need the WarcraftXL extension wxl-race-emote-sounds (retroport-tool\\native\\",
                  "  wxl-race-emote-sounds) in <WoW>\\Extensions\\wxl-race-emote-sounds\\.", ""]
    lines += ["SERVER"]
    if server_dbc_dir:
        lines += [f"  1. server\\dbc\\*.dbc go into {server_dbc_dir} (retroport install does this with backups)."]
    else:
        lines += ["  1. Copy the race DBC changes into the server's DBC folder (no server folder was given)."]
    lines += [f"  2. Apply server\\{res.race}_race.sql to the world database (undo: {res.race}_race_undo.sql)."]
    if res.racials:
        lines += [f"     Its own racials ({res.racials.get('name')}): optional, for the CHARACTERS database,",
                  f"     server\\{res.race}_racials_characters.sql takes the template race's racials from characters",
                  "     made before (new ones never get them). Racials BfA scripts on the server need the",
                  "     AzerothCore module retroport-tool\\native\\mod-retroport-racials (copy it into modules\\,",
                  "     re-run CMake, build)."]
    lines += ["  3. Restart worldserver.", "", "Client DBC sources:"]
    lines += [f"  {t}: {src}" for t, src in res.client_sources.items()]
    if res.warnings:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in res.warnings]
    write_verified(os.path.join(out_dir, "INSTALL.txt"), ("\r\n".join(lines) + "\r\n").encode("utf-8"))
