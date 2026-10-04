"""Build everything a new playable race needs, for the client and the server.

Output (``out_dir``):

* ``client\\`` - copy its contents into the client's top patch (e.g. ``Data\\patch-H.MPQ``):
  ``Character\\<Race>\\Male|Female`` (models + textures), ``DBFilesClient\\*.dbc``,
  ``Interface\\GlueXML\\*`` and the race icon atlas;
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
from typing import Callable

from pydantic import BaseModel

from ..character.convert import (BfaInstall, CharacterResult, _find_listfile_paths, _find_race, append_customization,
                                 charsections_layout_problems,
                                 convert_character)
from ..dbc import DBCError, DBCFile, RawDBC, load_dbc
from ..formats import blp
from ..mpq import ClientData
from ..safeio import write_verified
from . import glue, icons, sounds, sql
from .tables import (ALLIANCE, FIELDS, HORDE, BarberStyle, RaceSpec, add_barber_styles, add_base_info, add_chr_race,
                     add_displays, add_race_bit, clone_race_rows, clone_start_outfits, free_ids, playable_races,
                     strip_race)

CLIENT_TABLES = ["ChrRaces", "CharBaseInfo", "CharStartOutfit", "SkillRaceClassInfo", "SkillLineAbility", "Faction",
                 "CreatureModelData", "CreatureDisplayInfo", "CharSections", "CharHairGeosets",
                 "CharacterFacialHairStyles", "EmotesTextSound", "VocalUISounds", "NameGen", "BarberShopStyle",
                 "SoundEntries", "CreatureSoundData"]
SERVER_TABLES = ["ChrRaces", "CharBaseInfo", "CharStartOutfit", "SkillRaceClassInfo", "SkillLineAbility", "Faction",
                 "CreatureModelData", "CreatureDisplayInfo", "EmotesTextSound", "BarberShopStyle"]
RAW_TABLES = {"CharBaseInfo", "CharStartOutfit"}
GLUE = "Interface\\GlueXML\\"
ATLAS = "Interface\\Glues\\CharacterCreate\\UI-CharacterCreate-Races.blp"
SEXES = ("Male", "Female")
VOICE_DB2 = ("soundkit", "soundkitentry", "emotestext", "emotestextsound", "vocaluisounds", "creaturesounddata",
             "creaturedisplayinfo", "creaturemodeldata")


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


def _parse(name: str, data: bytes):
    return RawDBC.from_bytes(data, name) if name in RAW_TABLES else load_dbc(data, name, FIELDS[name])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_inside(path: str, parent: str) -> bool:
    path, parent = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(parent))
    return path == parent or path.startswith(parent.rstrip("\\/") + os.sep)


def _apply(tables: dict, specs: list[RaceSpec], *, name: str, cfs: str, look: int, ids: tuple[int, int, int, int],
           characters: dict[int, CharacterResult] | None,
           barber: dict[int, list[BarberStyle]] | None = None, voice: sounds.Voice | None = None,
           sound_ids: dict[int, int] | None = None, csd_ids: dict[int, int] | None = None) -> dict[str, int]:
    """Add every spec to one set of tables (client or server); returns rows touched per table.
    With ``voice`` the race gets its own sounds (ids from sounds.assign_ids, shared by client and
    server); without, it speaks with the look template's."""
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
                     look_template=look)
        bump("ChrRaces", 1)
        bump("CharBaseInfo", len(add_base_info(tables["CharBaseInfo"], s.race_id)))
        bump("CharStartOutfit", clone_start_outfits(tables["CharStartOutfit"], s.template, s.race_id))
        for t in ("SkillRaceClassInfo", "SkillLineAbility", "Faction"):
            bump(t, add_race_bit(tables[t], t, s.template_bit, s.bit))
        for t in ("EmotesTextSound", "VocalUISounds", "NameGen"):
            if t not in tables:
                continue
            if t == "EmotesTextSound" and voice is not None and voice.emotes:
                bump(t, sounds.add_emote_sounds(tables[t], voice, s.race_id, sound_ids))
            elif t == "VocalUISounds" and voice is not None and voice.vocal:
                bump(t, sounds.add_vocal_sounds(tables[t], voice, s.race_id, sound_ids))
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
               on_log: Callable[[str], None] | None = None) -> RaceBuildResult:
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
                               dbd_dir=dbd_dir, target=target, on_log=log)
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

    # --- 2. client DBCs, built on the copies the client loads ---
    log(f"Reading {client_dir} ...")
    client = ClientData(client_dir)
    ctables = {}
    for t in CLIENT_TABLES:
        hit = client.find(f"DBFilesClient\\{t}.dbc")
        if hit is None:
            raise DBCError(f"{t}.dbc is not in any archive of the client")
        res.client_sources[t] = hit[0]
        res.bases[f"client/DBFilesClient/{t}.dbc"] = {"source": hit[0], "sha256": sha256(hit[1])}
        ctables[t] = _parse(t, hit[1])
    _check_charsections_base(client, ctables["CharSections"], res.client_sources["CharSections"],
                             {s.race_id for s in specs})
    stables = {}
    if server_dbc_dir:
        for t in SERVER_TABLES:
            p = os.path.join(server_dbc_dir, f"{t}.dbc")
            if not os.path.isfile(p):
                raise DBCError(f"{p} not found - is this the server's DBC folder?")
            with open(p, "rb") as fh:
                data = fh.read()
            res.bases[f"server/dbc/{t}.dbc"] = {"source": p, "sha256": sha256(data)}
            stables[t] = _parse(t, data)

    if replace:
        for s in specs:
            for label, tables in (("client", ctables), ("server", stables)):
                if tables and tables["ChrRaces"].find_by_id(s.race_id):
                    log(f"  removing the previous build of race {s.race_id} ({label}): "
                        f"{strip_race(tables, s.race_id)}")
    cmds = [ctables["CreatureModelData"]] + ([stables["CreatureModelData"]] if stables else [])
    cdis = [ctables["CreatureDisplayInfo"]] + ([stables["CreatureDisplayInfo"]] if stables else [])
    m_ids, d_ids = free_ids(2, *cmds), free_ids(2, *cdis)
    ids = (m_ids[0], m_ids[1], d_ids[0], d_ids[1])
    res.displays = {"male_model": ids[0], "female_model": ids[1], "male_display": ids[2], "female_display": ids[3]}
    bfa = BfaInstall(install_dir, dbd_dir, listfile)
    bfa.fdids.update(_find_listfile_paths(listfile, {f"dbfilesclient/{t}.db2" for t in
                                                     ("uitextureatlas", "uitextureatlasmember", "globalstrings",
                                                      "barbershopstyle")}))
    barbers = [ctables["BarberShopStyle"]] + ([stables["BarberShopStyle"]] if stables else [])
    barber = _barber_styles(bfa, race, characters, specs, barbers, res.warnings)
    voice, sound_ids, csd_ids = _race_voice(bfa, race, cfs, client, ctables, client_out, listfile, res, log)
    log(f"Client: adding races {[s.race_id for s in specs]} (displays {ids[2]}/{ids[3]})")
    res.rows["client"] = _apply(ctables, specs, name=name, cfs=cfs, look=look_template, ids=ids,
                                characters=characters, barber=barber, voice=voice, sound_ids=sound_ids,
                                csd_ids=csd_ids)
    layout = charsections_layout_problems(ctables["CharSections"], {s.race_id for s in specs})
    if layout:
        raise DBCError("CharSections rows for the new race are not laid out the way the client's "
                       f"customization cache requires (it would corrupt memory): {layout[:3]}")
    dbc_out = os.path.join(client_out, "DBFilesClient")
    os.makedirs(dbc_out, exist_ok=True)
    for t, d in ctables.items():
        res.files.append(d.save(os.path.join(dbc_out, f"{t}.dbc")))
    if stables:
        log("Server: adding the same races")
        res.rows["server"] = _apply(stables, specs, name=name, cfs=cfs, look=look_template, ids=ids,
                                    characters=None, barber=barber, voice=voice, sound_ids=sound_ids)
        sdir = os.path.join(out_dir, "server", "dbc")
        os.makedirs(sdir, exist_ok=True)
        for t, d in stables.items():
            res.files.append(d.save(os.path.join(sdir, f"{t}.dbc")))
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
        if f"{key}_MALE" in known:
            col = round(known[f"{key}_MALE"][0] * atlas_img.width / icons.CELL)
            log(f"  race icons: reusing atlas column {col}")
        else:
            col = icons.free_column(atlas_img, used)
            log(f"  race icons: atlas column {col}")
        for row, sx in ((0, "Male"), (2, "Female")):
            m = src[sx]
            a = atlases[m["UiTextureAtlasID"]]
            p = os.path.join(work, f"bfa_atlas_{a['FileDataID']}.blp")
            if not os.path.isfile(p):
                write_verified(p, bfa.read(a["FileDataID"]))
            img = icons.crop_atlas_member(blp.decode(p), m)
            atlas_bytes = icons.splice(atlas_bytes, (col, row), img)
            icon_coords[f"{key}_{sx.upper()}"] = icons.coords(col, row, atlas_img.size)
        out_atlas = os.path.join(client_out, *ATLAS.split("\\"))
        res.files.append(write_verified(out_atlas, atlas_bytes, lambda p: blp.verify_blp(p, atlas_img.size)))
    except (ValueError, KeyError, blp.BLPError) as e:
        res.warnings.append(f"race icons: {e}; using the {look_file.title()} icons instead")
        for sx in ("MALE", "FEMALE"):
            icon_coords[f"{key}_{sx}"] = known[f"{look_file}_{sx}"]

    lua = glue.patch_character_create_lua(lua, max_races, icon_coords, key)
    xml = glue.patch_character_create_xml(xml, max_races)
    look_cfs = chr_races.get_string(chr_races.find_by_id(look_template)[11])
    parent = glue.patch_glue_parent(parent, {key: look_cfs}, key)

    text = {}
    try:
        gs = {str(r.get("BaseTag", "")): str(r.get("TagText_lang", "")) for _i, r in bfa.table("GlobalStrings")}
        text = {k: v for k, v in gs.items() if k in (f"RACE_INFO_{key}", f"RACE_INFO_{key}_FEMALE")}
    except ValueError as e:
        res.warnings.append(f"race description: {e}")
    if f"RACE_INFO_{key}" not in text:
        res.warnings.append("no race description in the BfA install; wrote a placeholder")
        text[f"RACE_INFO_{key}"] = f"{name}."
    text[f"{key}_DISABLED"] = f"{name} is not available."
    for s in specs:
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
    install, undo = sql.race_sql(specs, name, disp_map)
    sdir = os.path.join(out_dir, "server")
    os.makedirs(sdir, exist_ok=True)
    res.files.append(write_verified(os.path.join(sdir, f"{cfs}_race.sql"), install.encode("utf-8")))
    res.files.append(write_verified(os.path.join(sdir, f"{cfs}_race_undo.sql"), undo.encode("utf-8")))

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
    if buttons < max_races:
        raise ValueError(f"CharacterCreate.xml has {buttons} race buttons for {max_races} races")

    _write_install(out_dir, res, specs, server_dbc_dir)
    write_verified(os.path.join(out_dir, "race.json"), res.model_dump_json(indent=1).encode("utf-8"))
    log(f"Done: {out_dir}")
    return res


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
    the variations that were converted. Ids are free in every given table (client and server)."""
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
    lines += [f"  2. Apply server\\{res.race}_race.sql to the world database (undo: {res.race}_race_undo.sql).",
              "  3. Restart worldserver.", "", "Client DBC sources:"]
    lines += [f"  {t}: {src}" for t, src in res.client_sources.items()]
    if res.warnings:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in res.warnings]
    write_verified(os.path.join(out_dir, "INSTALL.txt"), ("\r\n".join(lines) + "\r\n").encode("utf-8"))
