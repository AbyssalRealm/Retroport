"""DBC edits that add (or remove) one playable race.

Every edit copies a *template* race so the new race behaves exactly like it unless
told otherwise: the faction template (Human for Alliance, Orc for Horde) gives faction,
language, racial skills/abilities, reputations and start gear; the *look* template
(Human by default) gives voices, random names, helmet models (ClientPrefix) and the
customization labels.

3.3.5a (12340) layouts used here:
  ChrRaces (69)            0 ID, 1 Flags, 2 FactionID, 4/5 Male/FemaleDisplayID, 6 ClientPrefix,
                           7 BaseLanguage (7 Alliance / 1 Horde = AzerothCore's TeamID),
                           11 ClientFileString, 13 Alliance (0 Alliance, 1 Horde),
                           14-29 Name[16] 30 flags, 31-46 NameFemale 47, 48-63 NameMale 64,
                           65/66 FacialHairCustomization, 67 HairCustomization, 68 Expansion
  CreatureDisplayInfo (16) 0 ID, 1 ModelID     CreatureModelData (28) 0 ID, 2 ModelName
  CharBaseInfo             uint8 race, uint8 class
  CharStartOutfit          uint32 ID, uint8 race, class, sex, outfit, 72 uint32
  SkillRaceClassInfo (8)   2 RaceMask          SkillLineAbility (14) 3 RaceMask, 5 ExcludeRace
  Faction (57)             2-5 ReputationRaceMask[4]
  EmotesTextSound (5)      0 ID, 2 RaceID      VocalUISounds (7) 0 ID, 2 RaceID
  NameGen (4)              0 ID, 2 RaceID
  BarberShopStyle (40)     0 ID, 1 Type (0 hair, 2 facial hair), 2-17 DisplayName 18 flags,
                           19-34 Description 35 flags, 36 CostModifier (float), 37 Race, 38 Sex, 39 Data
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..dbc import DBCError, DBCFile, RawDBC

FIELDS = {"ChrRaces": 69, "CreatureDisplayInfo": 16, "CreatureModelData": 28, "SkillRaceClassInfo": 8,
          "SkillLineAbility": 14, "Faction": 57, "EmotesTextSound": 5, "VocalUISounds": 7, "NameGen": 4,
          "CharSections": 10, "CharHairGeosets": 6, "CharacterFacialHairStyles": 8, "BarberShopStyle": 40,
          "SoundEntries": 30, "CreatureSoundData": 38}
# CreatureSoundData columns that hold SoundEntries ids (sounds.CSD_KIT_COLUMNS)
CSD_SOUND_COLUMNS = (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 24, 26, 27, 28, 29,
                     30, 33, 34, 35, 36)
MASK_FIELDS = {"SkillRaceClassInfo": (2,), "SkillLineAbility": (3, 5), "Faction": (2, 3, 4, 5)}
RACE_ROW_FIELDS = {"EmotesTextSound": 2, "VocalUISounds": 2, "NameGen": 2}
OWNED_ROW_FIELDS = {**RACE_ROW_FIELDS, "BarberShopStyle": 37}       # rows strip_race removes by race
ALL_RACES = (0xFFFFFFFF, 0x7FFFFFFF)
ALLIANCE, HORDE = "Alliance", "Horde"


def race_bit(race_id: int) -> int:
    if not 1 <= race_id <= 32:
        raise DBCError(f"race {race_id}: 3.3.5 and AzerothCore race masks are 32-bit, so a playable "
                       "race id must be 1-32 (22-32 are free in a stock client)")
    return 1 << (race_id - 1)


@dataclass
class RaceSpec:
    race_id: int
    faction: str                 # Alliance | Horde
    template: int                # faction template race (1 Human, 2 Orc, ...)

    @property
    def bit(self) -> int:
        return race_bit(self.race_id)

    @property
    def template_bit(self) -> int:
        return race_bit(self.template)


# --------------------------------------------------------------------------- ChrRaces

def add_chr_race(chr_races: DBCFile, spec: RaceSpec, *, name: str, file_string: str,
                 male_display: int, female_display: int, look_template: int) -> list[int]:
    if chr_races.find_by_id(spec.race_id):
        raise DBCError(f"ChrRaces already has race {spec.race_id}")
    tpl = chr_races.find_by_id(spec.template)
    look = chr_races.find_by_id(look_template)
    if tpl is None or look is None:
        raise DBCError(f"template race {spec.template if tpl is None else look_template} is not in ChrRaces")
    if tpl[1] & 0x1:
        raise DBCError(f"template race {spec.template} is not playable")
    want_team = 0 if spec.faction == ALLIANCE else 1
    if tpl[13] != want_team:
        raise DBCError(f"template race {spec.template} is not {spec.faction}")
    row = list(tpl)
    row[0] = spec.race_id
    row[4], row[5] = male_display, female_display
    row[6] = look[6]                                   # ClientPrefix: helmet models (Helm_*_<Prefix><M|F>)
    row[11] = chr_races.add_string(file_string)
    s = chr_races.add_string(name)
    for base in (14, 31, 48):                          # Name, NameFemale, NameMale: every locale
        row[base:base + 16] = [s] * 16
    row[65:68] = look[65:68]                           # customization labels
    chr_races.add_record(row)
    return row


def playable_races(chr_races: DBCFile) -> list[list[int]]:
    return [r for r in chr_races.records if not r[1] & 0x1]


# --------------------------------------------------------------------------- displays

def add_displays(cmd: DBCFile, cdi: DBCFile, template_displays: tuple[int, int], models: tuple[str, str],
                 ids: tuple[int, int, int, int]) -> None:
    """Clone the template race's base displays (sound, scale, collision) onto the new models.
    ``ids`` = (male model, female model, male display, female display)."""
    for sex in (0, 1):
        t_cdi = cdi.find_by_id(template_displays[sex])
        if t_cdi is None:
            raise DBCError(f"template display {template_displays[sex]} is not in CreatureDisplayInfo")
        t_cmd = cmd.find_by_id(t_cdi[1])
        if t_cmd is None:
            raise DBCError(f"template model {t_cdi[1]} is not in CreatureModelData")
        for d, i in ((cmd, ids[sex]), (cdi, ids[2 + sex])):
            if d.find_by_id(i):
                raise DBCError(f"id {i} is already used")
        path = models[sex]
        if cmd.get_string(t_cmd[2]).lower().endswith(".mdx") and path.lower().endswith(".m2"):
            path = path[:-2] + "mdx"                   # match Blizzard's rows; the client loads the .m2
        new_cmd = list(t_cmd)
        new_cmd[0], new_cmd[2] = ids[sex], cmd.add_string(path)
        cmd.add_record(new_cmd)
        new_cdi = list(t_cdi)
        new_cdi[0], new_cdi[1] = ids[2 + sex], ids[sex]
        cdi.add_record(new_cdi)


def free_ids(count: int, *dbcs: DBCFile) -> list[int]:
    """``count`` consecutive ids above every given table's maximum."""
    start = max(d.max_id() for d in dbcs) + 1
    return list(range(start, start + count))


# --------------------------------------------------------------------------- class / outfit tables

def add_base_info(base_info: RawDBC, race_id: int, classes: list[int] | None = None) -> list[int]:
    """CharBaseInfo: the race/class pairs the create screen offers. Default: every class any
    race already has (all ten with ARAC)."""
    pairs = [struct.unpack("<BB", r) for r in base_info.records]
    if any(r == race_id for r, _c in pairs):
        raise DBCError(f"CharBaseInfo already has race {race_id}")
    classes = sorted(classes or {c for _r, c in pairs})
    for c in classes:
        base_info.add_record(struct.pack("<BB", race_id, c))
    return classes


def clone_start_outfits(outfits: RawDBC, template: int, race_id: int) -> int:
    if outfits.record_size != 296:
        raise DBCError(f"CharStartOutfit: record size {outfits.record_size}, expected 296 (3.3.5a)")
    if any(r[4] == race_id for r in outfits.records):
        raise DBCError(f"CharStartOutfit already has race {race_id}")
    next_id = max((struct.unpack_from("<I", r)[0] for r in outfits.records), default=0) + 1
    added = 0
    for r in [r for r in outfits.records if r[4] == template]:
        outfits.add_record(struct.pack("<IB", next_id, race_id) + r[5:])
        next_id += 1
        added += 1
    return added


# --------------------------------------------------------------------------- race masks and race-keyed rows

def add_race_bit(dbc: DBCFile, table: str, template_bit: int, bit: int) -> int:
    """Wherever a mask includes the template race, include the new race too."""
    changed = 0
    for r in dbc.records:
        hit = False
        for f in MASK_FIELDS[table]:
            v = r[f] & 0xFFFFFFFF
            if v & template_bit and not v & bit:
                r[f] = v | bit
                hit = True
        changed += hit
    return changed


def clone_race_rows(dbc: DBCFile, table: str, template: int, race_id: int) -> int:
    rf = RACE_ROW_FIELDS[table]
    if any(r[rf] == race_id for r in dbc.records):
        raise DBCError(f"{table} already has rows for race {race_id}")
    next_id = dbc.max_id() + 1
    added = 0
    for r in [r for r in dbc.records if r[rf] == template]:
        new = list(r)
        new[0], new[rf] = next_id, race_id
        dbc.add_record(new)
        next_id += 1
        added += 1
    return added


# --------------------------------------------------------------------------- barber shop

@dataclass
class BarberStyle:
    id: int
    type: int                    # 0 hair style, 2 facial hair
    sex: int
    data: int                    # the CharHairGeosets / CharacterFacialHairStyles VariationID
    name: str
    cost: float


def add_barber_styles(dbc: DBCFile, race_id: int, styles: list[BarberStyle]) -> int:
    """BarberShopStyle rows; ids are chosen by the caller so client and server agree (the
    server validates a barber request by row id, race and sex)."""
    if any(r[37] == race_id for r in dbc.records):
        raise DBCError(f"BarberShopStyle already has rows for race {race_id}")
    name_flags = dbc.records[0][18] if dbc.records else 0
    for st in styles:
        if dbc.find_by_id(st.id):
            raise DBCError(f"BarberShopStyle id {st.id} is already used")
        s = dbc.add_string(st.name)
        dbc.add_record([st.id, st.type, *([s] * 16), name_flags, *([0] * 16), name_flags,
                        struct.unpack("<I", struct.pack("<f", st.cost))[0], race_id, st.sex, st.data])
    return len(styles)


# --------------------------------------------------------------------------- removal (rebuilds)

def strip_race(tables: dict[str, object], race_id: int) -> dict[str, int]:
    """Remove everything ``add_*`` wrote for ``race_id``, so a rebuild starts clean.
    ``tables`` maps table name -> loaded DBCFile/RawDBC (any subset)."""
    removed: dict[str, int] = {}
    bit = race_bit(race_id)

    def drop(name: str, keep) -> None:
        d = tables.get(name)
        if d is not None:
            before = len(d.records)
            d.records = [r for r in d.records if keep(r)]
            if before != len(d.records):
                removed[name] = before - len(d.records)

    # the race's voice (sounds.py): SoundEntries rows its rows point at, removed below once no
    # remaining row uses them - the other faction's twin shares them until it goes too
    ets, vui, csd, se = (tables.get(t) for t in ("EmotesTextSound", "VocalUISounds", "CreatureSoundData", "SoundEntries"))
    sounds = {r[4] for r in ets.records if r[2] == race_id} if ets is not None else set()
    sounds |= {v for r in vui.records if r[2] == race_id for v in r[3:7]} if vui is not None else set()

    chr_races = tables.get("ChrRaces")
    row = chr_races.find_by_id(race_id) if chr_races is not None else None
    file_string = chr_races.get_string(row[11]) if row is not None else ""
    if row is not None:
        displays = {row[4], row[5]}
        others = {d for r in chr_races.records if r[0] != race_id for d in (r[4], r[5])}
        cdi, cmd = tables.get("CreatureDisplayInfo"), tables.get("CreatureModelData")
        if cdi is not None:
            models = {r[1] for r in cdi.records if r[0] in displays - others}
            drop("CreatureDisplayInfo", lambda r: r[0] not in displays - others)
            still_used = {r[1] for r in cdi.records}
            if cmd is not None:
                gone = models - still_used
                csd_rows = {r[13] for r in cmd.records if r[0] in gone}
                drop("CreatureModelData", lambda r: r[0] not in gone)
                # combat-sound rows only the race's models used (Human's stay: HumanMale uses them)
                csd_rows -= {r[13] for r in cmd.records} | {r[2] for r in cdi.records}
                if csd is not None and csd_rows:
                    sounds |= {r[c] for r in csd.records if r[0] in csd_rows for c in CSD_SOUND_COLUMNS}
                    drop("CreatureSoundData", lambda r: r[0] not in csd_rows)
        drop("ChrRaces", lambda r: r[0] != race_id)
    drop("CharBaseInfo", lambda r: r[0] != race_id)
    drop("CharStartOutfit", lambda r: r[4] != race_id)
    drop("CharSections", lambda r: r[1] != race_id)
    drop("CharHairGeosets", lambda r: r[1] != race_id)
    drop("CharacterFacialHairStyles", lambda r: r[0] != race_id)
    for name, rf in OWNED_ROW_FIELDS.items():
        drop(name, lambda r, rf=rf: r[rf] != race_id)
    if se is not None and file_string and sounds - {0}:
        in_use = {r[4] for r in ets.records} if ets is not None else set()
        in_use |= {v for r in vui.records for v in r[3:7]} if vui is not None else set()
        in_use |= {r[c] for r in csd.records for c in CSD_SOUND_COLUMNS} if csd is not None else set()
        # only rows the voice port wrote (named after the race) - a sound cloned from the
        # look template is Blizzard's and stays
        ours = {r[0] for r in se.records if r[0] in sounds - in_use
                and se.get_string(r[2]).startswith(file_string)}
        if ours:
            drop("SoundEntries", lambda r: r[0] not in ours)
    for name, fields in MASK_FIELDS.items():
        d = tables.get(name)
        if d is None:
            continue
        n = 0
        for r in d.records:
            hit = False
            for f in fields:
                v = r[f] & 0xFFFFFFFF
                if v & bit and v not in ALL_RACES:
                    r[f] = v & ~bit
                    hit = True
            n += hit
        if n:
            removed[name] = n
    return removed
