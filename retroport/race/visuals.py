"""BfA spell visuals for 3.3.5: the models, animations and sounds a spell plays.

BfA: SpellXSpellVisual -> SpellVisual -> SpellVisualEvent (a kit started at an event of the cast,
on the caster or the target) -> SpellVisualKit -> its effects: model attachments
(SpellVisualKitModelAttach -> SpellVisualEffectName -> the model), an animation (SpellVisualAnim: an anim kit, or an initial and
a loop animation), a sound kit.
3.3.5: SpellVisual names a kit per moment (PrecastKit, CastKit, ImpactKit, StateKit, ...); a
SpellVisualKit plays an animation, attaches a model in each of its slots (HeadEffect, ChestEffect,
BaseEffect, RightHandEffect, ...: SpellVisualEffectName rows) and a SoundEntries sound.

So each BfA event's kit becomes the 3.3.5 kit of its moment (``EVENT_SLOTS``), each model attached
at a BfA attachment point goes in the slot that attaches there (``ATTACH_SLOTS``), converted as the
scenes are (glue/scenes.py: WarcraftXL's shaders, particle emitters) into ``Spells\\`` - or the
client's own copy when it has the file (3.3.5's stun swirl); the animation is the kit's, the one
the moment starts with (an anim kit's first segment, else the initial animation) - a state kit's
the one it holds (the loop, an anim kit's last segment: Haymaker's knockdown ends in Stun) - and
when the install has none (hotfixed) the model's own animation named for the spell
(``BFA_ANIMS``: Haymaker's wind-up and punch, 1446 and 1444). The client plays
no animation id past 505 (anims.py), so those take 3.3.5's spare spell animations
(``CUSTOM_SPELL``: CustomSpell01-10, rows AnimationData has) the race's body models leave free,
and the models' sequences move there (anims.renumber_sequences); a sound kit becomes a
SoundEntries row with its files.

An attachment's offset (3.3.5 kits have none: Haymaker's wind-up glow sits 0.7 yards behind the
caster) is moved into the converted model itself (``translate_model``). Not carried: an
attachment's start/end animations, and BfA's "unit sound type" effects (the caster's own grunt).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from ..dbclayout import DbcLayout
from ..glue import scenes
from ..safeio import write_verified
from . import sounds

# BfA SpellVisualEvent.StartEvent -> the 3.3.5 SpellVisual column for that moment of the cast
EVENT_SLOTS = {1: "PrecastKit", 3: "CastKit", 6: "ImpactKit", 7: "StateKit", 8: "StateDoneKit", 11: "ChannelKit"}
# an M2 attachment point -> the 3.3.5 SpellVisualKit column whose model hangs there
ATTACH_SLOTS = {1: "RightHandEffect", 22: "RightHandEffect", 2: "LeftHandEffect", 21: "LeftHandEffect",
                0: "BaseEffect", 19: "BaseEffect", 11: "HeadEffect", 20: "HeadEffect", 15: "ChestEffect",
                16: "ChestEffect", 34: "ChestEffect", 17: "BreathEffect"}
SPECIAL_SLOTS = 3                                  # SpecialEffect[3]: anything else, in turn
# BfA animation ids 3.3.5's AnimationData lacks, by name (BfA's own AnimationData has no names):
# the ones a ported spell plays - the model has them (the conversion keeps every sequence)
BFA_ANIMS = {1444: "Haymaker", 1446: "HaymakerPrecast"}
# 3.3.5's CustomSpell01-10: animation ids AnimationData keeps for a model's own spell animations
CUSTOM_SPELL = range(213, 223)
STUN_ANIM = 14                                     # 3.3.5 "Stun": what its own stun kits play
NO_ANIM = 0xFFFFFFFF
EFFECT_TYPE_MODEL, EFFECT_TYPE_ANIM, EFFECT_TYPE_SOUND = 2, 6, 5
TABLES = ("SpellVisual", "SpellVisualEvent", "SpellVisualKit", "SpellVisualKitEffect", "SpellVisualKitModelAttach",
          "SpellVisualEffectName", "SpellVisualAnim", "AnimKit", "AnimKitSegment", "SoundKit", "SoundKitEntry")


def _f(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(v)))[0]


class BfaVisuals:
    def __init__(self, bfa, listfile_paths):
        self.bfa = bfa
        bfa.fdids.update(listfile_paths({f"dbfilesclient/{t.lower()}.db2" for t in TABLES}))
        self.visuals = dict(bfa.table("SpellVisual"))
        self.events: dict[int, list[dict]] = {}
        for _i, r in bfa.table("SpellVisualEvent"):
            self.events.setdefault(r["SpellVisualID"], []).append(r)
        self.kit_effects: dict[int, list[dict]] = {}
        for _i, r in bfa.table("SpellVisualKitEffect"):
            self.kit_effects.setdefault(r["ParentSpellVisualKitID"], []).append(r)
        self.attach = dict(bfa.table("SpellVisualKitModelAttach"))
        self.effect_names = dict(bfa.table("SpellVisualEffectName"))
        self.visual_anims = dict(bfa.table("SpellVisualAnim"))
        self.anim_kits = dict(bfa.table("AnimKit"))
        self.segments: dict[int, list[dict]] = {}
        for _i, r in bfa.table("AnimKitSegment"):
            self.segments.setdefault(r["ParentAnimKitID"], []).append(r)
        self.sound_kits = dict(bfa.table("SoundKit"))
        self.sound_entries: dict[int, list[dict]] = {}
        for _i, r in bfa.table("SoundKitEntry"):
            self.sound_entries.setdefault(r["SoundKitID"], []).append(r)


class BlockIds:
    """Ids from a race's own block (racials.block_base): the same on every rebuild, whatever
    other packages add to the table."""

    def __init__(self, base: int):
        self.base, self.n = base, 0

    def next(self) -> int:
        self.n += 1
        return self.base + self.n - 1


@dataclass
class PortedVisual:
    visual: list[int] = field(default_factory=list)                 # the SpellVisual row
    kits: list[list[int]] = field(default_factory=list)
    effect_names: list[list] = field(default_factory=list)          # [id, name, file, scale] (strings later)
    sounds: sounds.Voice = field(default_factory=sounds.Voice)
    sound_ids: dict[int, int] = field(default_factory=dict)          # BfA sound kit -> SoundEntries id
    anims: set[int] = field(default_factory=set)                     # animation ids the kits play
    files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def port_visual(bv: BfaVisuals, visual_id: int, *, spell_name: str, stuns: bool, layouts: dict[str, DbcLayout],
                ids: dict[str, BlockIds], client, client_out: str, work: str, listfile: str, cfs: str,
                existing_effect_names: dict[str, int]) -> PortedVisual | None:
    """3.3.5 rows for BfA's SpellVisual ``visual_id`` (module doc). ``existing_effect_names``: the
    client's SpellVisualEffectName ids by lower-cased model path (``.m2``), for models it has."""
    v = bv.visuals.get(visual_id)
    if v is None:
        return None
    out = PortedVisual()
    LV, LK = layouts["SpellVisual"], layouts["SpellVisualKit"]
    row = LV.blank()
    LV.set(row, "ID", ids["SpellVisual"].next())
    LV.set(row, "MissileDestinationAttachment", v.get("MissileDestinationAttachment", 0) & 0xFFFFFFFF)
    LV.set(row, "MissileAttachment", v.get("MissileAttachment", -1) & 0xFFFFFFFF)
    out.visual = row
    model_paths = sounds.listfile_names(listfile, {
        bv.effect_names.get(bv.attach.get(e["Effect"], {}).get("SpellVisualEffectNameID"), {}).get("ModelFileDataID", 0)
        for ev in bv.events.get(visual_id, []) for e in bv.kit_effects.get(ev["SpellVisualKitID"], [])
        if e["EffectType"] == EFFECT_TYPE_MODEL} - {0})
    converted: dict[str, int] = {}
    for ev in sorted(bv.events.get(visual_id, []), key=lambda e: e["StartEvent"]):
        slot = EVENT_SLOTS.get(ev["StartEvent"])
        if slot is None:
            out.notes.append(f"kit {ev['SpellVisualKitID']}: event {ev['StartEvent']} has no 3.3.5 moment")
            continue
        kit = LK.blank()
        kid = ids["SpellVisualKit"].next()
        LK.set(kit, "ID", kid)
        LK.set(kit, "StartAnimID", NO_ANIM)
        LK.set(kit, "AnimID", NO_ANIM)
        for k in range(4):
            LK.set(kit, "CharProc", NO_ANIM, k)
        specials = 0
        for e in bv.kit_effects.get(ev["SpellVisualKitID"], []):
            if e["EffectType"] == EFFECT_TYPE_ANIM:
                anim = _kit_anim(bv, e["Effect"], slot, spell_name, stuns)
                if anim is None:
                    out.notes.append(f"{slot}: animation {e['Effect']} is not in the install and none "
                                     f"is named for {spell_name}")
                else:
                    LK.set(kit, "AnimID", anim)
                    out.anims.add(anim)
            elif e["EffectType"] == EFFECT_TYPE_MODEL:
                a = bv.attach.get(e["Effect"], {})
                name = bv.effect_names.get(a.get("SpellVisualEffectNameID"), {})
                path = model_paths.get(name.get("ModelFileDataID", 0))
                if not path:
                    out.notes.append(f"{slot}: model {name.get('ModelFileDataID')} is not in the listfile")
                    continue
                col = ATTACH_SLOTS.get(a.get("AttachmentID", 0))
                if col is None or LK.get(kit, col):
                    if specials >= SPECIAL_SLOTS:
                        out.notes.append(f"{slot}: no free slot for {path}")
                        continue
                    col, specials = "SpecialEffect", specials + 1
                eid = _effect_name(out, path, a, bv, client, client_out, work, listfile, ids, converted,
                                   existing_effect_names)
                if eid:
                    LK.set(kit, col, eid, specials - 1 if col == "SpecialEffect" else 0)
            elif e["EffectType"] == EFFECT_TYPE_SOUND:
                sid = _sound(out, e["Effect"], bv, f"{cfs}{spell_name.replace(' ', '')}{slot[:-3]}", ids, listfile)
                if sid:
                    LK.set(kit, "SoundID", sid)
        if slot == "StateKit" and stuns and LK.get(kit, "AnimID") == NO_ANIM:
            LK.set(kit, "AnimID", STUN_ANIM)
        out.kits.append(kit)
        LV.set(row, slot, kid)
    return out


def translate_model(m2: bytes, offset: tuple[float, float, float]) -> bytes:
    """An MD20 model with everything in it moved by ``offset``: vertices, bone pivots, particle
    emitters, ribbons, attachments, lights and bounds - the same model hung that far off its point."""
    from wotlkconv.m2.model import parse_md20
    from wotlkconv.m2.write import write_md20
    model = parse_md20(m2)
    d = tuple(float(v) for v in offset)
    add = lambda p: tuple(a + b for a, b in zip(p, d))                     # noqa: E731
    verts = bytearray(model.vertices)
    for i in range(model.vertex_count):
        struct.pack_into("<3f", verts, i * 48, *add(struct.unpack_from("<3f", verts, i * 48)))
    model.vertices = bytes(verts)
    for b in model.bones:
        b["pivot"] = add(b["pivot"])
    for group in (model.particles, model.ribbons, model.attachments, model.lights):
        for item in group:
            item["position"] = add(item["position"])
    box = list(model.bounding_box)
    model.bounding_box = tuple(add(box[:3]) + add(box[3:]))
    for seq in model.sequences:
        if "bounds" in seq and len(seq["bounds"]) == 6:
            seq["bounds"] = tuple(add(seq["bounds"][:3]) + add(seq["bounds"][3:]))
    return write_md20(model)


def _kit_anim(bv: BfaVisuals, visual_anim: int, slot: str, spell_name: str, stuns: bool) -> int | None:
    """A kit's animation from its SpellVisualAnim (module doc), or None."""
    state = slot == "StateKit"
    a = bv.visual_anims.get(visual_anim, {})
    segments = sorted(bv.segments.get(a.get("AnimKitID", 0), []), key=lambda s: s.get("OrderIndex", 0))
    first, loop = a.get("InitialAnimID", -1), a.get("LoopAnimID", -1)
    if segments:
        anim = segments[-1 if state else 0].get("AnimID", 0)
    else:
        anim = (loop if loop >= 0 else first) if state else (first if first >= 0 else loop)
    if anim >= 0:
        # BfA's generic stun kit stands (0) under an anim config; 3.3.5 plays its "Stun" animation
        return STUN_ANIM if (state and stuns and anim == 0) else anim
    wanted = spell_name.replace(" ", "") + ("Precast" if slot == "PrecastKit" else "")
    return next((i for i, n in BFA_ANIMS.items() if n == wanted), None)


def _effect_name(out: PortedVisual, path: str, attach: dict, bv: BfaVisuals, client, client_out: str, work: str,
                 listfile: str, ids: dict[str, BlockIds], converted: dict[str, int],
                 existing: dict[str, int]) -> int:
    """A SpellVisualEffectName id for the model at listfile ``path``: the client's own row when it has
    the model, else a converted copy in ``Spells\\``."""
    rel = path.replace("/", "\\")
    low = rel.lower()
    if low in existing:
        return existing[low]
    if low in converted:
        return converted[low]
    stem = os.path.basename(rel).rsplit(".", 1)[0]
    folder = os.path.dirname(rel)
    if not client.exists(rel):
        data = bv.bfa.read(_fdid_for(listfile, path))
        lf = scenes.scoped_listfile(bv.bfa, [data], work, listfile)
        scene = scenes.convert_scene(bv.bfa, data, stem, client=client, client_out=client_out, work=work, lf=lf,
                                     folder=folder[:1].upper() + folder[1:], own_folder=False)
        out.files += scene.files
        out.notes += scene.warnings
        offset = tuple(attach.get("Offset", (0.0, 0.0, 0.0)))
        if any(offset):
            with open(scene.output, "rb") as fh:
                moved = translate_model(fh.read(), offset)
            write_verified(scene.output, moved)
            out.notes.append(f"{stem}: moved {[round(v, 2) for v in offset]} off its point, as BfA hangs it")
    eid = ids["SpellVisualEffectName"].next()
    scale = float(attach.get("Scale", 1.0) or 1.0)
    # 3.3.5's own rows name the model .mdx; the client loads the .m2
    out.effect_names.append([eid, stem, folder[:1].upper() + folder[1:] + "\\" + stem + ".mdx", scale])
    converted[low] = eid
    return eid


def _fdid_for(listfile: str, path: str) -> int:
    from ..character.convert import _find_listfile_paths
    found = _find_listfile_paths(listfile, {path.lower().replace("\\", "/")})
    return found[path.lower().replace("\\", "/")]


def _sound(out: PortedVisual, kit_id: int, bv: BfaVisuals, name: str, ids: dict[str, BlockIds], listfile: str) -> int:
    if kit_id in out.sound_ids:
        return out.sound_ids[kit_id]
    k, rows = bv.sound_kits.get(kit_id), sorted(bv.sound_entries.get(kit_id, []), key=lambda e: e["ID"])
    if not k or not rows:
        out.notes.append(f"sound kit {kit_id} has no files")
        return 0
    paths = sounds.listfile_names(listfile, {e["FileDataID"] for e in rows})
    files = [(e["FileDataID"], (paths.get(e["FileDataID"]) or "").replace("/", "\\"), max(1, int(e.get("Frequency") or 1)))
             for e in rows[:sounds.MAX_FILES]]
    files = [f for f in files if f[1]]
    if not files or len({os.path.dirname(p).lower() for _f, p, _w in files}) > 1:
        out.notes.append(f"sound kit {kit_id}: its files are not in one listed folder")
        return 0
    sid = ids["SoundEntries"].next()
    out.sounds.kits[kit_id] = sounds.Kit(kit_id, name, files, int(k["SoundType"]), float(k["VolumeFloat"]),
                                         int(k["Flags"]), float(k["MinDistance"]), float(k["DistanceCutoff"]),
                                         int(k["EAXDef"]))
    out.sound_ids[kit_id] = sid
    return sid


def anim_slots(anims: set[int], taken: set[int]) -> tuple[dict[int, int], list[int]]:
    """3.3.5 ids for the BfA animations the client can't play (BfA id -> id): the CustomSpell slots
    the race's models don't use (``taken``: their own sequences' ids), in order. Returns (ids,
    the BfA animations left without a slot)."""
    free = iter(i for i in CUSTOM_SPELL if i not in taken)
    ids, left = {}, []
    for a in sorted(a for a in anims if a in BFA_ANIMS):
        slot = next(free, None)
        if slot is None:
            left.append(a)
        else:
            ids[a] = slot
    return ids, left


def write_sound_files(read, voice: sounds.Voice, client_out: str, client) -> list[str]:
    """The kits' files the client lacks, extracted (a file it has stays its own)."""
    written = []
    for kit in voice.kits.values():
        for fdid, path, _w in kit.files:
            if client.exists(path):
                continue
            data = read(fdid)
            if data[:4] != b"OggS":
                continue
            written.append(write_verified(os.path.join(client_out, *path.split("\\")), data))
    return sorted(set(written))
