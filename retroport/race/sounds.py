"""Give a ported race its own voice: BfA's emote sounds, spoken error messages and combat sounds.

BfA keeps a sound as a SoundKit row plus one SoundKitEntry row per file; 3.3.5 keeps it as one
SoundEntries row with up to ten files under one directory. BfA's kit values (sound type, volume,
flags, hearing distances) are on 3.3.5's scale - Human's kits and Human's SoundEntries rows hold
the same numbers - so each kit becomes a SoundEntries row as it is, and the .ogg files play as
they are (the 3.3.5 client's FMOD has the Ogg Vorbis codec).

* EmotesTextSound - the race's BfA rows (ClassID 0) for the emotes 3.3.5 has. A race id of 22 or
  more also needs the wxl-race-emote-sounds client extension: the client looks up races 0-21 only.
* VocalUISounds - by VocalUIEnum; both versions number them the same (Human's rows line up).
* CreatureSoundData - the player sounds BfA gives the race's display (CreatureDisplayInfo.SoundID,
  else its model's): attack, critical attack, wound, critical wound, death. A 3.3.5 player's come
  from CreatureModelData.SoundID, so each new model gets its own row.

3.3.5a (12340) layouts:
  SoundEntries (30)        0 ID, 1 SoundType, 2 Name, 3-12 File[10], 13-22 Freq[10], 23 DirectoryBase,
                           24 Volume (float), 25 Flags, 26 MinDistance, 27 DistanceCutoff (floats),
                           28 EAXDef, 29 SoundEntriesAdvancedID
  CreatureSoundData (38)   0 ID, the sound ids of CSD_KIT_COLUMNS, 9 FootstepTerrain, 23 NPCSound,
                           25 ImpactType, 31/32 FidgetDelay (floats), 37 pet row
  EmotesTextSound (5)      0 ID, 1 EmotesTextID, 2 RaceID, 3 SexID, 4 SoundID
  VocalUISounds (7)        0 ID, 1 VocalUIEnum, 2 RaceID, 3/4 NormalSoundID[2], 5/6 PissedSoundID[2]
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass, field
from typing import Callable

from ..dbc import DBCError, DBCFile, float_to_u32
from ..listfile import Listfile
from ..safeio import write_verified

SEXES = ("Male", "Female")
MAX_FILES = 10                                  # SoundEntries.File[10]
# 3.3.5 CreatureSoundData column -> (BfA field, array index): every column that holds a sound
CSD_KIT_COLUMNS: dict[int, tuple[str, int | None]] = {
    1: ("SoundExertionID", None), 2: ("SoundExertionCriticalID", None), 3: ("SoundInjuryID", None),
    4: ("SoundInjuryCriticalID", None), 5: ("SoundInjuryCrushingBlowID", None), 6: ("SoundDeathID", None),
    7: ("SoundStunID", None), 8: ("SoundStandID", None), 10: ("SoundAggroID", None),
    11: ("SoundWingFlapID", None), 12: ("SoundWingGlideID", None), 13: ("SoundAlertID", None),
    **{14 + i: ("SoundFidget", i) for i in range(5)}, **{19 + i: ("CustomAttack", i) for i in range(4)},
    24: ("LoopSoundID", None), 26: ("SoundJumpStartID", None), 27: ("SoundJumpEndID", None),
    28: ("SoundPetAttackID", None), 29: ("SoundPetOrderID", None), 30: ("SoundPetDismissID", None),
    33: ("BirthSoundID", None), 34: ("SpellCastDirectedSoundID", None), 35: ("SubmergeSoundID", None),
    36: ("SubmergedSoundID", None)}

Table = Callable[[str], list[tuple[int, dict]]]


@dataclass
class Kit:
    """One BfA sound kit, as a 3.3.5 SoundEntries row."""
    id: int
    name: str
    files: list[tuple[int, str, int]]          # (FileDataID, client path with backslashes, frequency)
    sound_type: int
    volume: float
    flags: int
    min_distance: float
    cutoff: float
    eax: int

    @property
    def directory(self) -> str:
        return ntpath.dirname(self.files[0][1])


@dataclass
class Voice:
    kits: dict[int, Kit] = field(default_factory=dict)
    emotes: dict[tuple[int, int], int] = field(default_factory=dict)       # (EmotesTextID, sex) -> kit
    vocal: dict[int, tuple[int, int]] = field(default_factory=dict)        # VocalUIEnum -> (male, female) kit
    creature: dict[int, dict[int, int]] = field(default_factory=dict)      # sex -> {CSD column: kit or 0}
    skipped: list[str] = field(default_factory=list)

    def drop_kit(self, kit_id: int) -> None:
        """Forget a kit and every use of it (its files could not be extracted)."""
        self.kits.pop(kit_id, None)
        self.emotes = {k: v for k, v in self.emotes.items() if v != kit_id}
        self.vocal = {e: tuple(0 if k == kit_id else k for k in pair) for e, pair in self.vocal.items()}
        self.vocal = {e: pair for e, pair in self.vocal.items() if any(pair)}
        for cols in self.creature.values():
            for c, k in cols.items():
                if k == kit_id:
                    cols[c] = 0


def listfile_names(listfile: str, fdids: set[int]) -> dict[int, str]:
    """Stream the listfile once for FileDataID -> path (backslashes)."""
    found: dict[int, str] = {}
    with open(listfile, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parsed = Listfile._split(line)
            if parsed and parsed[0] in fdids:
                found[parsed[0]] = parsed[1]
    return found


def collect_voice(table: Table, race_id: int, cfs: str, *, emotes: set[int], vocal_enums: set[int],
                  names: Callable[[set[int]], dict[int, str]]) -> Voice:
    """Read a BfA race's voice. ``emotes`` / ``vocal_enums``: what the 3.3.5 client knows;
    ``names`` maps FileDataIDs to listfile paths."""
    voice = Voice()
    kits = dict(table("SoundKit"))
    entries: dict[int, list[dict]] = {}
    for _i, r in table("SoundKitEntry"):
        entries.setdefault(r["SoundKitID"], []).append(r)
    emote_names = {i: str(r.get("Name", i)) for i, r in table("EmotesText")}
    wanted: dict[int, str] = {}                 # kit -> first use, for the SoundEntries name

    def use(kit_id: int, label: str) -> int:
        if not kit_id:
            return 0
        if kit_id not in kits or not entries.get(kit_id):
            voice.skipped.append(f"{label}: BfA sound kit {kit_id} has no files")
            return 0
        wanted.setdefault(kit_id, label)
        return kit_id

    for _i, r in sorted(table("EmotesTextSound"), key=lambda t: t[0]):
        if r["RaceID"] != race_id or r.get("ClassID", 0) or r["SexID"] not in (0, 1):
            continue
        emote, sex = r["EmotesTextID"], r["SexID"]
        name = emote_names.get(emote, str(emote))
        if emote not in emotes:
            voice.skipped.append(f"/{name.lower()} ({SEXES[sex].lower()}): no such emote in 3.3.5")
            continue
        kit = use(r["SoundID"], f"{cfs}{SEXES[sex]}Emote{name.title()}")
        if kit:
            voice.emotes.setdefault((emote, sex), kit)
    for _i, r in sorted(table("VocalUISounds"), key=lambda t: t[0]):
        if r["RaceID"] != race_id or r.get("ClassID", 0):
            continue
        enum = r["VocalUIEnum"]
        if enum not in vocal_enums:
            voice.skipped.append(f"spoken error {enum}: not in 3.3.5's VocalUISounds")
            continue
        pair = tuple(use(k, f"{cfs}{SEXES[i]}Error{enum:02d}") for i, k in enumerate(list(r["NormalSoundID"])[:2]))
        if any(pair):
            voice.vocal.setdefault(enum, pair)

    race = dict(table("ChrRaces")).get(race_id)
    if race is None:
        raise DBCError(f"race {race_id} is not in BfA's ChrRaces")
    cdi, cmd, csd = dict(table("CreatureDisplayInfo")), dict(table("CreatureModelData")), dict(table("CreatureSoundData"))
    for sex, key in enumerate(("MaleDisplayID", "FemaleDisplayID")):
        display = cdi.get(race.get(key, 0))
        sid = display and (display.get("SoundID") or cmd.get(display["ModelID"], {}).get("SoundID"))
        row = csd.get(sid) if sid else None
        if row is None:
            voice.skipped.append(f"{SEXES[sex].lower()} combat sounds: BfA display {race.get(key)} has none")
            continue
        cols = {}
        for col, (fname, idx) in CSD_KIT_COLUMNS.items():
            v = row.get(fname, 0)
            v = (list(v) + [0] * 5)[idx] if idx is not None else v
            label = fname.removeprefix("Sound").removesuffix("ID") + (str(idx + 1) if idx is not None else "")
            cols[col] = use(v, f"{cfs}{SEXES[sex]}Player{label}")
        voice.creature[sex] = cols

    paths = names({e["FileDataID"] for k in wanted for e in entries[k]})
    own_dir = f"Sound\\Character\\{cfs}"
    for kit_id, label in sorted(wanted.items()):
        k = kits[kit_id]
        rows = sorted(entries[kit_id], key=lambda e: e["ID"])
        if len(rows) > MAX_FILES:
            voice.skipped.append(f"{label}: {len(rows)} files, 3.3.5 plays the first {MAX_FILES}")
        files = [(e["FileDataID"], paths.get(e["FileDataID"]) or f"{own_dir}\\{e['FileDataID']}.ogg",
                  max(1, int(e.get("Frequency") or 1))) for e in rows[:MAX_FILES]]
        if len({ntpath.dirname(p).lower() for _f, p, _w in files}) > 1:  # one row, one directory
            files = [(f, f"{own_dir}\\{ntpath.basename(p)}", w) for f, p, w in files]
        voice.kits[kit_id] = Kit(kit_id, label, files, int(k["SoundType"]), float(k["VolumeFloat"]),
                                 int(k["Flags"]), float(k["MinDistance"]), float(k["DistanceCutoff"]),
                                 int(k["EAXDef"]))
    return voice


def write_voice_files(read: Callable[[int], bytes], voice: Voice, client_out: str) -> list[str]:
    """Extract every kit's files under ``client_out``; a file that isn't Ogg (missing or
    encrypted in the install) is left out, and a kit left without files is dropped."""
    written: dict[str, str] = {}
    for kit in list(voice.kits.values()):
        keep = []
        for fdid, path, weight in kit.files:
            dst = os.path.join(client_out, *path.split("\\"))
            if path.lower() not in written:
                try:
                    data = read(fdid)
                except Exception as e:  # noqa: BLE001 - CASC raises several kinds for a missing file
                    voice.skipped.append(f"{kit.name}: file {fdid} could not be read ({e})")
                    continue
                if data[:4] != b"OggS":
                    voice.skipped.append(f"{kit.name}: file {fdid} is not Ogg (encrypted in this install?)")
                    continue
                written[path.lower()] = write_verified(dst, data)
            keep.append((fdid, path, weight))
        kit.files = keep
        if not keep:
            voice.drop_kit(kit.id)
    return sorted(set(written.values()))


def assign_ids(voice: Voice, sound_entries: DBCFile, creature_sounds: DBCFile) -> tuple[dict[int, int], dict[int, int]]:
    """New SoundEntries ids per kit and CreatureSoundData ids per sex, above each table's maximum."""
    start = sound_entries.max_id() + 1
    sounds = {kit: start + i for i, kit in enumerate(sorted(voice.kits))}
    start = creature_sounds.max_id() + 1
    creature = {sex: start + i for i, sex in enumerate(sorted(voice.creature))}
    return sounds, creature


def add_sound_entries(se: DBCFile, voice: Voice, ids: dict[int, int]) -> int:
    for kit_id, kit in sorted(voice.kits.items()):
        if se.find_by_id(ids[kit_id]):
            raise DBCError(f"SoundEntries id {ids[kit_id]} is already used")
        files = [se.add_string(ntpath.basename(p)) for _f, p, _w in kit.files] + [0] * (MAX_FILES - len(kit.files))
        freqs = [w for _f, _p, w in kit.files] + [0] * (MAX_FILES - len(kit.files))
        se.add_record([ids[kit_id], kit.sound_type, se.add_string(kit.name), *files, *freqs,
                       se.add_string(kit.directory), float_to_u32(kit.volume), kit.flags,
                       float_to_u32(kit.min_distance), float_to_u32(kit.cutoff), kit.eax, 0])
    return len(voice.kits)


def add_creature_sounds(csd: DBCFile, cmd: DBCFile, voice: Voice, sound_ids: dict[int, int],
                        csd_ids: dict[int, int], models: tuple[int, int]) -> int:
    """A CreatureSoundData row per sex, copied from the one the model has now (the look
    template's: footsteps, impact type) with every sound replaced by the race's own - or none,
    where the race has none - and the model pointed at it."""
    added = 0
    for sex, cols in sorted(voice.creature.items()):
        model = cmd.find_by_id(models[sex])
        if model is None:
            raise DBCError(f"model {models[sex]} is not in CreatureModelData")
        template = csd.find_by_id(model[13])
        if template is None:
            raise DBCError(f"model {models[sex]}'s CreatureSoundData row {model[13]} does not exist")
        if csd.find_by_id(csd_ids[sex]):
            raise DBCError(f"CreatureSoundData id {csd_ids[sex]} is already used")
        row = list(template)
        row[0] = csd_ids[sex]
        for col, kit in cols.items():
            row[col] = sound_ids.get(kit, 0) if kit else 0
        csd.add_record(row)
        model[13] = csd_ids[sex]
        added += 1
    return added


def add_emote_sounds(ets: DBCFile, voice: Voice, race_id: int, sound_ids: dict[int, int]) -> int:
    if any(r[2] == race_id for r in ets.records):
        raise DBCError(f"EmotesTextSound already has rows for race {race_id}")
    next_id = ets.max_id() + 1
    for (emote, sex), kit in sorted(voice.emotes.items()):
        ets.add_record([next_id, emote, race_id, sex, sound_ids[kit]])
        next_id += 1
    return len(voice.emotes)


def add_vocal_sounds(vui: DBCFile, voice: Voice, race_id: int, sound_ids: dict[int, int]) -> int:
    if any(r[2] == race_id for r in vui.records):
        raise DBCError(f"VocalUISounds already has rows for race {race_id}")
    next_id = vui.max_id() + 1
    for enum, (male, female) in sorted(voice.vocal.items()):
        vui.add_record([next_id, enum, race_id, sound_ids.get(male, 0), sound_ids.get(female, 0), 0, 0])
        next_id += 1
    return len(voice.vocal)
