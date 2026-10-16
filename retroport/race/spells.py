"""BfA (8.x) spells as 3.3.5a Spell.dbc rows: names and text, icon, cast time, range, cooldown,
levels, categories and effects.

Effects and auras keep their BfA ids where 3.3.5 has the same one (``EFFECTS``, ``AURAS``: the
ids that mean the same thing in both); one 3.3.5 has no aura for is translated where it has a
fair equivalent (``TRANSLATED``: Versatility -> damage and healing done), else dropped and
reported. Ratings keep the bits 3.3.5 shares, a retail level-scaled one as the percent it is
worth (``_port_rating``); an aura on the pet becomes a pet spell (``PET_AURAS``). 3.3.5 holds
three effects a spell: a spell with more (Jack of All Trades: eleven) is
split into the spell and hidden passive companions (``companion_ids``), learned with it.

Index tables (cast times, durations, ranges, radii) are resolved against the client's and the
server's copies together: an id both have with the value, else a new row in each.

Texts come in a second pass (``spell_texts``), once every spell of a set is ported: ``$s1`` and
the like follow their effect to its new place (``$4300016s1`` on a companion), one that was
dropped becomes its value, ``$<var>`` BfA's description variables (in the subset 3.3.5's
tooltips evaluate) and ``$@spelldesc`` the other spell's text.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from ..dbclayout import DbcLayout

# BfA SpellEffect.Effect ids that mean the same in 3.3.5
EFFECTS = {2: "SCHOOL_DAMAGE", 3: "DUMMY", 6: "APPLY_AURA", 10: "HEAL", 30: "ENERGIZE", 64: "TRIGGER_SPELL",
           98: "KNOCK_BACK"}
# BfA SpellEffect.EffectAura ids -> 3.3.5 aura ids (the same where both have it): 172/211 mounted
# and flight speed that doesn't stack (Open Skies, as Crusader Aura), 245 the duration of auras of a
# dispel type (Savage Blood: poisons, curses, diseases; the dispel types are the same too)
AURAS = {3: 3, 4: 4, 8: 8, 12: 12, 13: 13, 22: 22, 26: 26, 29: 29, 30: 30, 31: 31, 33: 33, 42: 42, 58: 58, 79: 79,
         87: 87, 98: 98, 136: 136, 155: 155, 172: 172, 211: 211, 226: 226, 245: 245}
ALL_SCHOOLS = 127
# BfA auras 3.3.5 lacks, as the 3.3.5 auras that do most of it: (aura, misc value) per BfA one.
# Versatility N%: N% more damage and healing done (its damage-taken half is a server script's)
VERSATILITY = 471
DAMAGE_AND_HEALING = [(79, ALL_SCHOOLS), (136, 0)]
TRANSLATED = {VERSATILITY: DAMAGE_AND_HEALING}
# MOD_RATING: its misc value is a mask of combat ratings, the same bits in both but for those
# 3.3.5 never had - mastery and versatility, damage and healing done there as Versatility is
MOD_RATING = 189
SAME_RATINGS = sum(1 << b for b in (*range(1, 11), 17, 18, 19, 23, 24))   # defense .. crit, haste, expertise, arp
MASTERY_RATING, VERSATILITY_RATINGS = 1 << 25, 0x7 << 28
RATING_NAMES = {1: "defense", 2: "dodge", 3: "parry", 4: "block", 5: "hit", 8: "critical strike", 17: "haste",
                23: "expertise", 24: "armor penetration", 25: "mastery", 28: "versatility"}
# a rating retail scales by level (SpellEffect.Coefficient on a SpellScaling column, no base points)
# is worth about the same percent at every level: 3.3.5 holds that percent, and mod-retroport-racials
# turns it into the rating that gives it at the player's level
RATING_PCT_SCRIPT = "spell_retroport_rating_pct"
SCALING_COLUMNS = {-1: "Item"}
# BfA auras 3.3.5 lacks that mod-retroport-racials does by the spell's id: a dummy aura (its amount
# BfA's) the script finds the spell by
SCRIPT_AURAS = {374: "falling damage taken %"}
# retail auras on the caster's pet, as a 3.3.5 aura a pet spell puts on it (AzerothCore's
# spell_pet_auras casts it on every permanent pet while the caster has the dummy aura the effect
# becomes). Retail's pet stat % (382, not even in TrinityCore) is pet health in Sympathetic Vigor
PET_STAT_PCT = 382
PET_AURAS = {PET_STAT_PCT: (133, "Health increased by $s1%.")}     # 133 MOD_INCREASE_HEALTH_PERCENT
AURA_NAMES = {VERSATILITY: "Versatility", 218: "modifier by spell label", 226: "periodic dummy (scripted)",
              PET_STAT_PCT: "pet stat %"}
APPLY_AURA, DUMMY, DUMMY_AURA = 6, 3, 4
SKILL_AURAS = {30, 98}                  # a skill's value up: the skill line is the misc value
# BfA SpellAuraInterruptFlags keep 3.3.5's bits but one: DamageCancelsScript (0x8000000), damage
# ending the aura, is 3.3.5's TAKE_DAMAGE
DAMAGE_CANCELS_SCRIPT, TAKE_DAMAGE = 0x8000000, 0x2
# a crowd control BfA breaks with a charge used up by a hit taken (Quaking Palm), 3.3.5 with
# TAKE_DAMAGE (Gouge): its auras, and the proc flags of hits taken (melee, ranged, spells, periodic)
BREAKABLE_AURAS = {5, 7, 12, 26}        # confuse, fear, stun, root
TAKEN_HITS = 0x8 | 0x20 | 0x80 | 0x200 | 0x2000 | 0x20000 | 0x80000
MAX_EFFECTS = 3
ATTR0_PASSIVE, ATTR0_HIDDEN_CLIENTSIDE = 0x40, 0x80
ATTR1_NO_AURA_ICON = 0x10000000         # the same bit in both (Ancestral Call's own aura: the buff it casts shows)
GCD_CATEGORY = 133
NO_ITEM = 0xFFFFFFFF
MELEE_RANGE_FLAG = 0x1
SELF_RANGE, MELEE_RANGE = 1, 2
TARGET_ENEMY, FACING_REQUIRED = 6, 0x1
MELEE_MAX_YARDS = 6.0


def _f(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(v)))[0]


def _fv(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def _u(v: int) -> int:
    return int(v) & 0xFFFFFFFF


ONE = _f(1.0)


class SpellPortError(ValueError):
    pass


@dataclass
class PortedSpell:
    spell_id: int
    name: str
    rows: list[list[int]] = field(default_factory=list)         # the spell first, then its companions
    icon_fdid: int = 0
    # BfA effect index -> ("slot", row, slot) where it went, or ("value", n) when it is gone
    effects: dict[int, tuple] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)             # effects 3.3.5 has no equivalent for
    translated: list[str] = field(default_factory=list)          # effects done with other 3.3.5 auras
    scripted: list[str] = field(default_factory=list)            # effects that work only with a server script
    visual_id: int = 0                                           # BfA SpellVisualID
    ap_bonus: float = 0.0                                        # spell_bonus_data.ap_bonus (SQL)
    proc_flags: int = 0
    amounts: dict[int, float] = field(default_factory=dict)      # BfA effect index -> its amount, worked out
    rating_pct: dict[int, int] = field(default_factory=dict)     # BfA effect index -> rating bit: a scaled rating's %
    script: str = ""                                             # a module script the spell needs (SQL binds it)
    pet_rows: list[list[int]] = field(default_factory=list)      # spells cast on the pet (not learned)
    pet_auras: list[tuple[int, int, int]] = field(default_factory=list)   # (spell, effect, pet spell): spell_pet_auras

    @property
    def ids(self) -> list[int]:
        return [r[0] for r in self.rows]

    @property
    def all_ids(self) -> list[int]:
        return self.ids + [r[0] for r in self.pet_rows]


class BfaSpells:
    """BfA's spell tables, indexed by spell id."""

    TABLES = ("SpellName", "Spell", "SpellMisc", "SpellEffect", "SpellCooldowns", "SpellCategories", "SpellInterrupts",
              "SpellLevels", "SpellPower", "SpellCastingRequirements", "SpellAuraOptions", "SpellCastTimes",
              "SpellDuration", "SpellRange", "SpellRadius", "SpellXSpellVisual", "SpellXDescriptionVariables",
              "SpellDescriptionVariables")

    GAME_TABLES = ("SpellScaling", "CombatRatings")

    def __init__(self, bfa, listfile_paths):
        self.bfa = bfa
        self._listfile_paths = listfile_paths
        self._game_tables: dict[str, list[list[float]]] = {}
        bfa.fdids.update(listfile_paths({f"dbfilesclient/{t.lower()}.db2" for t in self.TABLES}))
        self.names = {i: r["Name_lang"] for i, r in bfa.table("SpellName")}
        self.text = {i: r for i, r in bfa.table("Spell")}
        self.by_spell = {}
        for t in ("SpellMisc", "SpellCooldowns", "SpellCategories", "SpellInterrupts", "SpellLevels", "SpellPower",
                  "SpellCastingRequirements", "SpellAuraOptions", "SpellXSpellVisual", "SpellXDescriptionVariables"):
            rows = {}
            for _i, r in bfa.table(t):
                if r.get("DifficultyID", 0) == 0:
                    rows.setdefault(r["SpellID"], r)
            self.by_spell[t] = rows
        self.effects: dict[int, list[dict]] = {}
        for _i, r in bfa.table("SpellEffect"):
            if r.get("DifficultyID", 0) == 0:
                self.effects.setdefault(r["SpellID"], []).append(r)
        for v in self.effects.values():
            v.sort(key=lambda e: e["EffectIndex"])
        self.cast_times = dict(bfa.table("SpellCastTimes"))
        self.durations = dict(bfa.table("SpellDuration"))
        self.ranges = dict(bfa.table("SpellRange"))
        self.radii = dict(bfa.table("SpellRadius"))
        self.desc_vars = dict(bfa.table("SpellDescriptionVariables"))
        self.max_id = max(self.names)

    def get(self, table: str, spell: int) -> dict:
        return self.by_spell[table].get(spell, {})

    def variables(self, spell: int) -> dict[str, str]:
        xv = self.get("SpellXDescriptionVariables", spell)
        return description_variables(self.desc_vars.get(xv.get("SpellDescriptionVariablesID"), {})
                                     .get("Variables", "")) if xv else {}

    def game_table(self, name: str) -> tuple[list[str], list[list[float]]]:
        """A game table (gametables/<name>.txt): its column names and its rows, a level each."""
        if name not in self._game_tables:
            path = f"gametables/{name.lower()}.txt"
            fdid = self._listfile_paths({path}).get(path)
            if fdid is None:
                raise SpellPortError(f"{path} is not in the listfile")
            self._game_tables[name] = parse_game_table(self.bfa.read(fdid).decode("utf-8", "replace"))
        return self._game_tables[name]

    def rating_percent(self, effect: dict, bit: int) -> float | None:
        """What a level-scaled rating effect gives of rating ``bit``, in percent (scaled_rating_percent)."""
        column = SCALING_COLUMNS.get(effect.get("ScalingClass"))
        if column is None or not effect.get("Coefficient"):
            return None
        return scaled_rating_percent(self.game_table("SpellScaling"), self.game_table("CombatRatings"),
                                     column, float(effect["Coefficient"]), bit)


def parse_game_table(text: str) -> tuple[list[str], list[list[float]]]:
    lines = [ln.split("\t") for ln in text.splitlines() if ln.strip()]
    return lines[0], [[float(v) for v in ln] for ln in lines[1:]]


def scaled_rating_percent(scaling: tuple[list[str], list[list[float]]], ratings: tuple[list[str], list[list[float]]],
                          column: str, coefficient: float, bit: int) -> float:
    """A rating ``coefficient`` x SpellScaling[``column``] worth, in percent of rating ``bit``
    (CombatRatings' column ``bit`` + 1: rating per 1%), at the source's top level - the first
    level its scaling stops growing at. Retail scales a racial's rating so that it is about as
    many percent at every level (a Mag'har's clan buff: 4.6-5.1% crit from level 60 to 90)."""
    names, rows = scaling
    col = names.index(column)
    top = next(r for r in rows if r[col] == rows[-1][col])
    level = top[0]
    per_pct = next(r for r in ratings[1] if r[0] == level)[bit + 1]
    return top[col] * coefficient / per_pct


# --------------------------------------------------------------------------- index tables

def _index(tables: list, match, make) -> int:
    """An id every table has a matching row at, else a new row (``make(id)``) in each."""
    common = None
    for t in tables:
        ids = {(r[0] if isinstance(r, list) else struct.unpack_from("<I", r)[0])
               for r in t.records if match(r if isinstance(r, list) else t.row(r))}
        common = ids if common is None else common & ids
    if common:
        return min(common)
    new = max(t.max_id() for t in tables) + 1
    for t in tables:
        t.add_record(make(new))
    return new


def cast_time_index(tables: list, ms: int) -> int:
    return _index(tables, lambda r: r[1] == ms and r[2] == 0, lambda i: [i, ms, 0, ms])


def duration_index(tables: list, ms: int) -> int:
    return _index(tables, lambda r: _u(r[1]) == _u(ms) and r[2] == 0 and _u(r[3]) == _u(ms),
                  lambda i: [i, _u(ms), 0, _u(ms)])


def radius_index(tables: list, radius: float) -> int:
    return _index(tables, lambda r: abs(_fv(r[1]) - radius) < 0.01 and r[2] == 0,
                  lambda i: [i, _f(radius), 0, _f(radius)])


def range_index(tables: list, layout: DbcLayout, low: float, high: float, melee: bool) -> int:
    if high <= 0:
        return SELF_RANGE
    if melee or (low <= 0 and high <= MELEE_MAX_YARDS):
        return MELEE_RANGE                                           # 3.3.5's "Combat Range" (+ reach)
    lo_at, hi_at = layout.at("RangeMin", 0), layout.at("RangeMax", 0)
    common = None
    for t in tables:
        ids = {r[0] for r in t.records if abs(_fv(r[lo_at]) - low) < 0.01 and abs(_fv(r[hi_at]) - high) < 0.01}
        common = ids if common is None else common & ids
    if not common:
        raise SpellPortError(f"3.3.5 has no {low}-{high} yard range")
    return min(common)


# --------------------------------------------------------------------------- the rows

def port_spell(spells: BfaSpells, spell: int, *, layout: DbcLayout, index_tables: dict, range_layout: DbcLayout,
               companion_ids: list[int], skills: set[int] | None = None) -> PortedSpell:
    """3.3.5 Spell.dbc rows for BfA's ``spell`` - itself, and ``companion_ids`` for effects past its
    third - without their texts (``spell_texts``). ``index_tables``: SpellCastTimes,
    SpellDuration, SpellRange, SpellRadius -> [client table, server table]. ``skills``: 3.3.5's
    skill lines; an effect raising a skill it lacks (Gourmand's BfA cooking) is dropped."""
    name = spells.names.get(spell)
    if name is None:
        raise SpellPortError(f"BfA has no spell {spell}")
    misc = spells.get("SpellMisc", spell)
    out = PortedSpell(spell, name, icon_fdid=misc.get("SpellIconFileDataID", 0))
    cooldown = spells.get("SpellCooldowns", spell)
    cats = spells.get("SpellCategories", spell)
    interrupts = spells.get("SpellInterrupts", spell)
    levels = spells.get("SpellLevels", spell)
    power = spells.get("SpellPower", spell)
    casting = spells.get("SpellCastingRequirements", spell)
    aura_opts = spells.get("SpellAuraOptions", spell)
    out.visual_id = spells.get("SpellXSpellVisual", spell).get("SpellVisualID", 0)

    L = layout
    row = L.blank()
    L.set(row, "ID", spell)
    attr0, attr1 = (_u(a) for a in (list(misc.get("Attributes", [0, 0])) + [0, 0])[:2])
    L.set(row, "Attributes", attr0)              # BfA's: a heal it shows (Brush It Off's) shows here too
    L.set(row, "AttributesEx", attr1 & ATTR1_NO_AURA_ICON)
    L.set(row, "Category", cats.get("Category", 0))
    L.set(row, "DispelType", cats.get("DispelType", 0))
    L.set(row, "Mechanic", cats.get("Mechanic", 0))
    L.set(row, "DefenseType", cats.get("DefenseType", 0))
    L.set(row, "PreventionType", cats.get("PreventionType", 0))
    L.set(row, "FacingCasterFlags", casting.get("FacingCasterFlags", 0))
    ct = spells.cast_times.get(misc.get("CastingTimeIndex", 1), {}).get("Base", 0)
    L.set(row, "CastingTimeIndex", cast_time_index(index_tables["SpellCastTimes"], ct))
    du = spells.durations.get(misc.get("DurationIndex", 0), {}).get("Duration")
    L.set(row, "DurationIndex", duration_index(index_tables["SpellDuration"], du) if du else 0)
    rg = spells.ranges.get(misc.get("RangeIndex", 1), {})
    lo, hi = (list(rg.get("RangeMin", [0.0])) + [0.0])[0], (list(rg.get("RangeMax", [0.0])) + [0.0])[0]
    L.set(row, "RangeIndex", range_index(index_tables["SpellRange"], range_layout, lo, hi,
                                         bool(rg.get("Flags", 0) & MELEE_RANGE_FLAG)))
    L.set(row, "RecoveryTime", cooldown.get("RecoveryTime", 0))
    L.set(row, "CategoryRecoveryTime", cooldown.get("CategoryRecoveryTime", 0))
    gcd = cooldown.get("StartRecoveryTime", 0)
    L.set(row, "StartRecoveryTime", gcd)
    L.set(row, "StartRecoveryCategory", cats.get("StartRecoveryCategory", GCD_CATEGORY if gcd else 0))
    L.set(row, "InterruptFlags", interrupts.get("InterruptFlags", 0))
    aura_interrupts = _u((list(interrupts.get("AuraInterruptFlags", [0])) + [0])[0])
    if aura_interrupts & DAMAGE_CANCELS_SCRIPT:
        aura_interrupts = aura_interrupts & ~DAMAGE_CANCELS_SCRIPT | TAKE_DAMAGE
    L.set(row, "ChannelInterruptFlags", _u((list(interrupts.get("ChannelInterruptFlags", [0])) + [0])[0]))
    out.proc_flags = _u((list(aura_opts.get("ProcTypeMask", [0])) + [0])[0])
    charges = aura_opts.get("ProcCharges", 0)
    if charges and out.proc_flags & TAKEN_HITS and any(
            e["Effect"] == APPLY_AURA and e.get("EffectAura") in BREAKABLE_AURAS for e in spells.effects.get(spell, [])):
        out.translated.append("broken by a hit taken (BfA: a proc charge) -> aura interrupt on damage")
        aura_interrupts |= TAKE_DAMAGE
        out.proc_flags, charges = 0, 0
    L.set(row, "AuraInterruptFlags", aura_interrupts)
    L.set(row, "ProcTypeMask", out.proc_flags)
    L.set(row, "ProcChance", aura_opts.get("ProcChance", 101) or 101)
    L.set(row, "ProcCharges", charges)
    L.set(row, "CumulativeAura", aura_opts.get("CumulativeAura", 0))
    L.set(row, "BaseLevel", levels.get("BaseLevel", 0))
    L.set(row, "SpellLevel", levels.get("SpellLevel", 0))
    L.set(row, "MaxLevel", levels.get("MaxLevel", 0))
    L.set(row, "PowerType", _u(power.get("PowerType", 0)))
    L.set(row, "ManaCost", power.get("ManaCost", 0))
    L.set(row, "ManaCostPct", power.get("PowerCostPct", 0))
    L.set(row, "EquippedItemClass", NO_ITEM)
    L.set(row, "SchoolMask", misc.get("SchoolMask", 1))
    for k in range(MAX_EFFECTS):
        L.set(row, "EffectChainAmplitude", ONE, k)
        L.set(row, "EffectBonusCoefficient", ONE, k)

    # the effects 3.3.5 can do (BfA's own or translated), three a row
    kept: list[tuple] = []          # (BfA index, effect, 3.3.5 effect, aura, misc override, amount or None: BfA's)
    pets: list[tuple[int, int, int]] = []                # (BfA index, pet aura, amount): pet spells (PET_AURAS)
    passive = bool(attr0 & ATTR0_PASSIVE)
    for e in spells.effects.get(spell, []):
        i, eff, aura = e["EffectIndex"], e["Effect"], e.get("EffectAura", 0)
        literal = ("value", abs(int(round(float(e.get("EffectBasePointsF", 0.0))))))
        skill = (list(e.get("EffectMiscValue", [0])) + [0])[0]
        if eff == DUMMY and passive:
            # a passive's dummy does nothing on 3.3.5 but as an aura the script finds (Epicurean)
            out.scripted.append("dummy (effect 3) -> dummy aura")
            kept.append((i, e, APPLY_AURA, DUMMY_AURA, -1, None))
        elif eff == DUMMY:
            out.scripted.append("dummy (effect 3)")
            out.effects[i] = literal
        elif eff not in EFFECTS:
            out.dropped.append(f"effect {eff}")
            out.effects[i] = literal
        elif eff == APPLY_AURA and aura in TRANSLATED:
            out.translated.append(f"aura {aura} ({AURA_NAMES.get(aura, '')}) -> " +
                                  ", ".join(str(a) for a, _m in TRANSLATED[aura]))
            for a, m in TRANSLATED[aura]:
                kept.append((i, e, eff, a, m, None))
        elif eff == APPLY_AURA and aura == MOD_RATING:
            before = len(kept)
            _port_rating(spells, out, kept, i, e)
            if len(kept) == before:
                out.effects[i] = literal
        elif eff == APPLY_AURA and aura in PET_AURAS:
            amount = int(round(float(e.get("EffectBasePointsF", 0.0))))
            if any(a == aura and v == amount for _i, a, v in pets):
                # Sympathetic Vigor's second one (another pet kind, misc 28): 3.3.5's spell_pet_auras reach every pet
                out.dropped.append(f"aura {aura} ({AURA_NAMES[aura]}, misc {skill}): the pet spell of the one before does it")
                out.effects[i] = literal
            else:
                out.translated.append(f"aura {aura} ({AURA_NAMES[aura]}) -> dummy aura + a pet spell with aura "
                                      f"{PET_AURAS[aura][0]} (spell_pet_auras)")
                pets.append((i, aura, amount))
                kept.append((i, e, eff, DUMMY_AURA, 0, None))       # retail's misc (a pet kind) means nothing here
        elif eff == APPLY_AURA and aura in SCRIPT_AURAS:
            out.scripted.append(f"aura {aura} ({SCRIPT_AURAS[aura]}) -> dummy aura")
            kept.append((i, e, eff, DUMMY_AURA, -1, None))
        elif eff == APPLY_AURA and aura not in AURAS:
            out.dropped.append(f"aura {aura} ({AURA_NAMES.get(aura, 'no 3.3.5 equivalent')})")
            out.effects[i] = literal
        elif eff == APPLY_AURA and aura in SKILL_AURAS and skills is not None and skill not in skills:
            out.dropped.append(f"skill {skill} (3.3.5 has no such skill)")
            out.effects[i] = literal
        else:
            if eff == APPLY_AURA and aura == 226:
                out.scripted.append("periodic dummy (aura 226)")
            kept.append((i, e, eff, AURAS.get(aura, 0) if eff == APPLY_AURA else 0, -1, None))
    rows = [row]

    def blank(attributes: int) -> list[int]:
        """The spell's row without effects under the next companion id: a companion, or a pet spell."""
        used = len(rows) - 1 + len(out.pet_rows)
        if used >= len(companion_ids):
            raise SpellPortError(f"{name}: {len(kept)} effects need more companion spell ids than given")
        comp = list(row)
        comp[0] = companion_ids[used]
        for col in ("Effect", "EffectDieSides", "EffectRealPointsPerLevel", "EffectBasePoints", "EffectMechanic",
                    "ImplicitTargetA", "ImplicitTargetB", "EffectRadiusIndex", "EffectAura", "EffectAuraPeriod",
                    "EffectAmplitude", "EffectChainTargets", "EffectItemType", "EffectMiscValue",
                    "EffectMiscValueB", "EffectTriggerSpell", "EffectPointsPerCombo"):
            for j in range(MAX_EFFECTS):
                L.set(comp, col, 0, j)
        L.set(comp, "Attributes", attributes)
        return comp

    for k, (i, e, effect, aura, misc_override, amount) in enumerate(kept):
        slot = k % MAX_EFFECTS
        if k and slot == 0:
            rows.append(blank(L.get(row, "Attributes") | ATTR0_PASSIVE | ATTR0_HIDDEN_CLIENTSIDE))
        if i not in out.effects:
            out.effects[i] = ("slot", len(rows) - 1, slot)
        target = rows[-1]
        value = float(e.get("EffectBasePointsF", 0.0)) if amount is None else float(amount)
        L.set(target, "Effect", effect, slot)
        L.set(target, "EffectDieSides", 1, slot)
        L.set(target, "EffectBasePoints", _u(int(round(value)) - 1), slot)
        L.set(target, "EffectRealPointsPerLevel", _f(e.get("EffectRealPointsPerLevel", 0.0)), slot)
        L.set(target, "EffectMechanic", e.get("EffectMechanic", 0), slot)
        targets = list(e.get("ImplicitTarget", [0, 0])) + [0, 0]
        L.set(target, "ImplicitTargetA", targets[0], slot)
        L.set(target, "ImplicitTargetB", targets[1], slot)
        radius = (list(e.get("EffectRadiusIndex", [0])) + [0])[0]
        if radius:
            r = spells.radii.get(radius, {}).get("Radius", 0.0)
            L.set(target, "EffectRadiusIndex", radius_index(index_tables["SpellRadius"], r), slot)
        if effect == APPLY_AURA:
            L.set(target, "EffectAura", aura, slot)
        L.set(target, "EffectAuraPeriod", e.get("EffectAuraPeriod", 0), slot)
        L.set(target, "EffectAmplitude", _f(e.get("EffectAmplitude", 0.0)), slot)
        L.set(target, "EffectChainTargets", e.get("EffectChainTargets", 0), slot)
        L.set(target, "EffectItemType", e.get("EffectItemType", 0), slot)
        misc_v = list(e.get("EffectMiscValue", [0, 0])) + [0, 0]
        L.set(target, "EffectMiscValue", _u(misc_override if misc_override >= 0 else misc_v[0]), slot)
        L.set(target, "EffectMiscValueB", _u(misc_v[1]), slot)
        L.set(target, "EffectTriggerSpell", e.get("EffectTriggerSpell", 0), slot)
        if e.get("BonusCoefficientFromAP"):
            out.ap_bonus = max(out.ap_bonus, float(e["BonusCoefficientFromAP"]))
    # damage BfA works out from a description variable ("$damage=${$AP*0.75}", Haymaker's): an
    # attack power coefficient (AzerothCore's spell_bonus_data) on the damage effect
    if not out.ap_bonus and any(k[1]["Effect"] == 2 and not k[1].get("EffectBasePointsF") for k in kept):
        m = re.fullmatch(r"\$\{\$AP\*([\d.]+)\}", spells.variables(spell).get("damage", ""))
        if m:
            out.ap_bonus = float(m.group(1))
    # 3.3.5 checks facing only where a spell asks; BfA's client checks it for every hostile spell
    if any(TARGET_ENEMY in (list(k[1].get("ImplicitTarget", [0, 0])) + [0])[:2] for k in kept):
        L.set(row, "FacingCasterFlags", L.get(row, "FacingCasterFlags") | FACING_REQUIRED)
    # what the caster's pet gets: a passive on it, its amount the effect's, under the spell's name
    for i, aura, amount in pets:
        _kind, at, slot = out.effects[i]
        pet = blank(L.get(row, "Attributes") | ATTR0_PASSIVE)
        L.set(pet, "Effect", APPLY_AURA, 0)
        L.set(pet, "EffectDieSides", 1, 0)
        L.set(pet, "EffectBasePoints", _u(amount - 1), 0)
        L.set(pet, "ImplicitTargetA", 1, 0)                   # the pet itself (AzerothCore casts it on the pet)
        L.set(pet, "EffectAura", PET_AURAS[aura][0], 0)
        out.pet_rows.append(pet)
        out.pet_auras.append((rows[at][0], slot, pet[0]))
    out.rows = rows
    return out


def _low_bit(mask: int) -> int:
    return (mask & -mask).bit_length() - 1


def _port_rating(spells, out: PortedSpell, kept: list, i: int, e: dict) -> None:
    """A MOD_RATING effect: ratings 3.3.5 has stay 189 with their bits, mastery and versatility
    become damage and healing done (Versatility's translation). A level-scaled one is held as the
    percent it gives at the source's top level (BfaSpells.rating_percent): mod-retroport-racials
    turns a 189 one into rating at the player's level (RATING_PCT_SCRIPT); the 79/136 ones are
    percent already. 3.3.5's damage and healing done takes no rating - a fixed amount of mastery or
    versatility rating has no meaning there, and is dropped."""
    mask = _u((list(e.get("EffectMiscValue", [0])) + [0])[0])
    same, other = mask & SAME_RATINGS, mask & (MASTERY_RATING | VERSATILITY_RATINGS)
    scaled = not e.get("EffectBasePointsF") and e.get("Coefficient")

    def named(m: int) -> str:
        return RATING_NAMES.get(_low_bit(m), f"rating bit {_low_bit(m)}")
    if mask & ~(same | other):
        out.dropped.append(f"aura 189 ({named(mask & ~(same | other))} rating: 3.3.5 has none)")
    for part, rating in ((same, True), (other, False)):
        if not part:
            continue
        pct = spells.rating_percent(e, _low_bit(part)) if scaled else None
        if scaled and pct is None:
            out.dropped.append(f"aura 189 ({named(part)} rating: scaled by a column 3.3.5 has no equivalent of)")
            continue
        amount = max(1, int(round(pct))) if pct is not None else None
        if amount is not None:
            out.amounts.setdefault(i, amount)
            out.rating_pct.setdefault(i, _low_bit(part))
        if rating:
            if amount is not None:
                out.script = RATING_PCT_SCRIPT
                out.scripted.append(f"aura 189 ({named(part)} rating, scaled) -> {amount}% at every level "
                                    "(the module makes it rating)")
            kept.append((i, e, APPLY_AURA, MOD_RATING, part, amount))
        elif amount is None:
            out.dropped.append(f"aura 189 ({named(part)} rating: a fixed rating 3.3.5 has no equivalent of)")
        else:
            out.translated.append(f"aura 189 ({named(part)} rating) -> 79, 136: {amount}% damage and healing done")
            if part & VERSATILITY_RATINGS:
                out.scripted.append(f"versatility's damage taken: {amount / 2:g}% less")
            for a, m in DAMAGE_AND_HEALING:
                kept.append((i, e, APPLY_AURA, a, m, amount))


# --------------------------------------------------------------------------- texts

_VAR = re.compile(r"\$<(\w+)>")
_DESC = re.compile(r"\$@spelldesc(\d+)")
_REF = re.compile(r"\$(\d*)([smwotaxM])(\d)")
# BfA's Versatility N% in its tooltips, as what TRANSLATED makes of it in 3.3.5: N% more damage
# and healing done, and the damage-taken half (N/2% less) mod-retroport-racials gives
_VERSATILITY = re.compile(r"\bVersatility (increased )?by \$s(\d)%")
# retail's rating auras ("Increases critical strike by $w1."), level-scaled, as the percent 3.3.5
# holds (_port_rating): its crit chance or haste, mastery and versatility as damage and healing done
_RATING_TEXT = re.compile(r"\b(critical strike|haste|mastery|versatility) by \$([sw])(\d)\b(?!%)", re.I)
_AP_OR_SP = re.compile(r"\$cond\(\$gt\(\$SP,\$AP\),\$SP\*([\d.]+),\$AP\*([\d.]+)\)")


def description_variables(formula: str) -> dict[str, str]:
    """``$damage=${...}`` lines -> {"damage": "${...}"}, in the subset 3.3.5's tooltips evaluate:
    the larger of spell and attack power becomes attack power (3.3.5 has no ``$cond``), BfA's
    percentage and Versatility multipliers go."""
    out = {}
    for line in formula.splitlines():
        m = re.match(r"\s*\$(\w+)\s*=\s*(.+)$", line)
        if not m:
            continue
        expr = _AP_OR_SP.sub(lambda mm: f"$AP*{mm.group(2)}", m.group(2))
        expr = re.sub(r"\*\$pct\w+", "", expr)
        expr = re.sub(r"\*\(1\+\$@versa\w+\)", "", expr)
        out[m.group(1)] = expr
    return out


def port_text(spells: BfaSpells, ported: dict[int, PortedSpell], spell: int, text: str, depth: int = 0,
              qualify: bool = False) -> str:
    """BfA tooltip text for ``spell`` as 3.3.5 shows it (module doc). ``qualify``: references name
    their spell (``$291628s1``) - text inlined into another spell's."""
    if not text:
        return ""
    variables = spells.variables(spell)
    text = _VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
    text = _VERSATILITY.sub(lambda m: _versatility(spells, spell, text, m), text)
    if spell in ported and ported[spell].rating_pct:
        text = _RATING_TEXT.sub(lambda m: _rating_words(ported[spell], m), text)

    def ref(m: re.Match) -> str:
        owner = int(m.group(1)) if m.group(1) else spell
        p = ported.get(owner)
        where = p.effects.get(int(m.group(3)) - 1) if p else None
        if where is None:
            return m.group(0)                       # another spell 3.3.5 has, or nothing to follow
        if where[0] == "value":
            return str(where[1]) if m.group(2) in "smw" else m.group(0)
        _kind, row, slot = where
        target = p.rows[row][0]
        prefix = "" if target == spell and not qualify else str(target)
        return f"${prefix}{m.group(2)}{slot + 1}"
    # this spell's references first: an inlined text (``$@spelldesc``) comes with its own, ported
    parts = _DESC.split(text)
    for k in range(0, len(parts), 2):
        parts[k] = _REF.sub(ref, parts[k])
    for k in range(1, len(parts), 2):
        other = int(parts[k])
        parts[k] = port_text(spells, ported, other, spells.text.get(other, {}).get("Description_lang", ""),
                             depth + 1, qualify=True) if depth < 3 else f"$@spelldesc{other}"
    return "".join(parts).strip()


def _versatility(spells: BfaSpells, spell: int, text: str, m: re.Match) -> str:
    e = next((e for e in spells.effects.get(spell, []) if e["EffectIndex"] == int(m.group(2)) - 1), None)
    half = f"{float(e.get('EffectBasePointsF', 0)) / 2:g}" if e and e.get("EffectAura") == VERSATILITY else None
    taken = f" and {'damage taken reduced' if m.group(1) else 'reduces damage taken'} by {half}%" if half else ""
    words = f"damage and healing done {'increased ' if m.group(1) else ''}by $s{m.group(2)}%{taken}"
    start = text[:m.start()].rstrip()
    return words[0].upper() + words[1:] if not start or start[-1] in ".!?" else words


def _rating_words(p: PortedSpell, m: re.Match) -> str:
    index = int(m.group(3)) - 1
    bit = p.rating_pct.get(index)
    ref = f"$s{m.group(3)}"             # retail's $w (the aura's amount as sent): 3.3.5's tooltips use $s
    if bit is None:
        return m.group(0)
    if bit == _low_bit(MASTERY_RATING):
        return f"damage and healing done by {ref}%"
    if bit >= _low_bit(VERSATILITY_RATINGS):
        return f"damage and healing done by {ref}% and reduces damage taken by {p.amounts[index] / 2:g}%"
    return f"{m.group(1)}{' chance' if m.group(1).lower() == 'critical strike' else ''} by {ref}%"


def spell_texts(spells: BfaSpells, ported: dict[int, PortedSpell], spell: int,
                overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Name, subtext, description and aura text of a ported spell, as 3.3.5 shows them;
    ``overrides`` (subtext, description, aura) in place of BfA's, their references followed too."""
    text = spells.text.get(spell, {})
    o = overrides or {}
    return {"Name_lang": ported[spell].name,
            "NameSubtext_lang": o.get("subtext", str(text.get("NameSubtext_lang", ""))),
            "Description_lang": port_text(spells, ported, spell,
                                          o.get("description") or str(text.get("Description_lang", ""))),
            "AuraDescription_lang": port_text(spells, ported, spell,
                                              o.get("aura") or str(text.get("AuraDescription_lang", "")))}


def pet_texts(spells: BfaSpells, ported: dict[int, PortedSpell], spell: int, pet_row: list[int],
              layout: DbcLayout) -> dict[str, str]:
    """A pet spell's (PET_AURAS): its owner's name, and what it does to the pet."""
    aura = layout.get(pet_row, "EffectAura", 0)
    words = next((t for a, t in PET_AURAS.values() if a == aura), "")
    return {"Name_lang": ported[spell].name, "NameSubtext_lang": str(spells.text.get(spell, {}).get("NameSubtext_lang", "")),
            "Description_lang": words, "AuraDescription_lang": words}
