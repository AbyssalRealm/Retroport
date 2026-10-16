"""Racials from BfA: spell rows, texts, the racial skill line, id blocks, visuals and SQL."""

import struct

import pytest

from retroport.dbc import DBCFile, RawDBC
from retroport.dbclayout import DbcLayout
from retroport.race import racials, sql, visuals
from retroport.race.spells import (PortedSpell, description_variables, parse_game_table, port_spell,
                                   scaled_rating_percent, spell_texts)
from retroport.race.tables import ALLIANCE, HORDE, RaceSpec

DBD = r"I:\WoW Modding\WoWDBDefs\definitions"
pytestmark = pytest.mark.skipif(not __import__("os").path.isdir(DBD), reason="WoWDBDefs not found")


class FakeSpells:
    """BfaSpells' shape over hand-made rows."""

    def __init__(self, spells: dict):
        self.names = {i: s["name"] for i, s in spells.items()}
        self.text = {i: {"Description_lang": s.get("desc", ""), "NameSubtext_lang": s.get("sub", ""),
                         "AuraDescription_lang": s.get("aura", "")} for i, s in spells.items()}
        self.by_spell = {t: {} for t in ("SpellMisc", "SpellCooldowns", "SpellCategories", "SpellInterrupts",
                                         "SpellLevels", "SpellPower", "SpellCastingRequirements", "SpellAuraOptions",
                                         "SpellXSpellVisual", "SpellXDescriptionVariables")}
        for i, s in spells.items():
            attr = s.get("attr", 0x50)
            self.by_spell["SpellMisc"][i] = {"Attributes": attr if isinstance(attr, list) else [attr],
                                             "CastingTimeIndex": s.get("cast", 1),
                                             "DurationIndex": s.get("dur", 0), "RangeIndex": s.get("range", 1),
                                             "SchoolMask": 1, "SpellIconFileDataID": s.get("icon", 0)}
            if "vars" in s:
                self.by_spell["SpellXDescriptionVariables"][i] = {"SpellDescriptionVariablesID": i}
            if "interrupts" in s:
                self.by_spell["SpellInterrupts"][i] = {"AuraInterruptFlags": [s["interrupts"], 0]}
            if "procs" in s:
                self.by_spell["SpellAuraOptions"][i] = {"ProcTypeMask": [s["procs"][0], 0], "ProcCharges": s["procs"][1],
                                                       "ProcChance": 100}
        self.effects = {i: [dict(e, EffectIndex=k) for k, e in enumerate(s.get("effects", []))] for i, s in spells.items()}
        self.cast_times = {1: {"Base": 0}, 4: {"Base": 1000}}
        self.durations = {27: {"Duration": 3000}}
        self.ranges = {1: {"RangeMin": [0.0, 0.0], "RangeMax": [0.0, 0.0], "Flags": 0},
                       140: {"RangeMin": [0.0, 0.0], "RangeMax": [6.0, 6.0], "Flags": 0}}
        self.radii = {}
        self.desc_vars = {i: {"Variables": s["vars"]} for i, s in spells.items() if "vars" in s}
        self.max_id = max(spells)

    def get(self, table, spell):
        return self.by_spell[table].get(spell, {})

    def variables(self, spell):
        xv = self.get("SpellXDescriptionVariables", spell)
        return description_variables(self.desc_vars.get(xv.get("SpellDescriptionVariablesID"), {})
                                     .get("Variables", "")) if xv else {}

    def rating_percent(self, effect, bit):
        if effect.get("ScalingClass") != -1:
            return None
        return scaled_rating_percent(RETAIL_SCALING, RETAIL_RATINGS, "Item", effect["Coefficient"], bit)


# retail 12.1.5's game tables around its top level (90): the Item column flat from there on;
# rating per 1% - crit 46, haste 44, mastery (a point) 46, versatility damage done 54
RETAIL_SCALING = (["Level", "Item"], [[89.0, 120.0], [90.0, 131.0804863], [91.0, 131.0804863]])
_RATING_ROW = [0.0] * 32
for _bit, _per in ((8, 46.0), (9, 46.0), (10, 46.0), (17, 44.0), (18, 44.0), (19, 44.0), (25, 46.0), (28, 54.0),
                   (29, 54.0), (30, 108.0)):
    _RATING_ROW[_bit] = _per
RETAIL_RATINGS = (["Level"] + [f"r{k}" for k in range(32)],
                  [[89.0] + [v * 0.9 for v in _RATING_ROW], [90.0] + _RATING_ROW, [91.0] + _RATING_ROW])


def _index_tables():
    ct, du, rg, ra = DBCFile(4), DBCFile(4), DBCFile(40), DBCFile(4)
    ct.add_record([1, 0, 0, 0])
    ct.add_record([4, 1000, 0, 1000])
    du.add_record([27, 3000, 0, 3000])
    for i in (1, 2):
        rg.add_record([i] + [0] * 39)
    return {"SpellCastTimes": [ct], "SpellDuration": [du], "SpellRange": [rg], "SpellRadius": [ra]}


def aura(a, value, misc=0, target=1, period=0):
    return {"Effect": 6, "EffectAura": a, "EffectBasePointsF": float(value), "EffectMiscValue": [misc, 0],
            "ImplicitTarget": [target, 0], "EffectAuraPeriod": period}


KT = {
    291417: {"name": "Rime of the Ancient Mariner", "desc": "Reduces Frost and Nature damage taken by $s1%.",
             "effects": [aura(87, -1, 8), aura(87, -1, 16)], "icon": 5},
    291628: {"name": "Brush It Off", "desc": "Increases Versatility by $s1%. When you take damage, heal for $s2% of "
             "that amount over $291843d.", "effects": [aura(471, 1), aura(226, 2, period=2000), aura(218, -33)],
             "icon": 4},
    291843: {"name": "Brush It Off", "desc": "$@spelldesc291628", "effects": [aura(8, 0, period=2000)]},
    291442: {"name": "Jack of All Trades", "desc": "Skill in all professions increased by $s1.",
             "effects": [aura(98, 5, sk) for sk in (202, 197, 171, 164, 182, 755, 165, 186, 393, 333, 773)], "icon": 3},
    287712: {"name": "Haymaker", "desc": "dealing $<damage> Physical damage, stunning for $d", "cast": 4, "dur": 27,
             "range": 140, "attr": 0x10, "icon": 2,
             "vars": "$damage=${$cond($gt($SP,$AP),$SP*0.75,$AP*0.75)*$pctD*(1+$@versadmg)}",
             "effects": [{"Effect": 2, "ImplicitTarget": [6, 0]}, aura(12, 0, target=6),
                         {"Effect": 98, "EffectBasePointsF": 60.0, "EffectMiscValue": [300, 0], "ImplicitTarget": [6, 0]},
                         {"Effect": 3}]},
}


def _port(spells, spell, companions=(9001, 9002, 9003)):
    lay = DbcLayout("Spell", DBD)
    return port_spell(spells, spell, layout=lay, index_tables=_index_tables(),
                      range_layout=DbcLayout("SpellRange", DBD), companion_ids=list(companions)), lay


def test_effects_keep_their_ids_translate_or_drop():
    spells = FakeSpells(KT)
    rime, lay = _port(spells, 291417)
    row = rime.rows[0]
    assert [row[lay.at("EffectAura", k)] for k in range(3)] == [87, 87, 0]
    assert [row[lay.at("EffectMiscValue", k)] for k in range(2)] == [8, 16]
    assert row[lay.at("EffectBasePoints", 0)] == 0xFFFFFFFE and row[lay.at("EffectDieSides", 0)] == 1   # -2 + 1d1 = -1
    brush, _ = _port(spells, 291628)
    row = brush.rows[0]
    assert [row[lay.at("EffectAura", k)] for k in range(3)] == [79, 136, 226]          # Versatility -> damage, healing
    assert row[lay.at("EffectMiscValue", 0)] == 127
    assert brush.dropped == ["aura 218 (modifier by spell label)"] and brush.scripted
    assert brush.effects == {0: ("slot", 0, 0), 1: ("slot", 0, 2), 2: ("value", 33)}


PANDAREN = {
    107079: {"name": "Quaking Palm", "attr": 0x110810, "dur": 27, "range": 140, "interrupts": 0x8000000,
             "procs": (-2146819416, 1), "effects": [dict(aura(12, 0, target=6), EffectMechanic=14)]},
    107076: {"name": "Bouncy", "attr": 0x40, "effects": [aura(374, -50, 185)]},
    107073: {"name": "Gourmand", "attr": 0x50, "effects": [aura(98, 15, 185), aura(98, 15, 980)]},
    107072: {"name": "Epicurean", "attr": 0x40,
             "effects": [{"Effect": 3, "EffectBasePointsF": 100.0, "ImplicitTarget": [1, 0]}]},
}


def test_a_crowd_control_broken_by_a_hit_breaks_on_damage_as_3_3_5s_do():
    """Quaking Palm: BfA breaks it with a proc charge used up by a hit taken; 3.3.5 (Gouge) with an
    aura interrupt on damage. Its DamageCancelsScript flag is that interrupt too."""
    palm, lay = _port(FakeSpells(PANDAREN), 107079)
    row = palm.rows[0]
    assert row[lay.at("AuraInterruptFlags")] == 0x2
    assert row[lay.at("ProcTypeMask")] == 0 and row[lay.at("ProcCharges")] == 0 and palm.proc_flags == 0
    assert row[lay.at("EffectAura", 0)] == 12 and row[lay.at("EffectMechanic", 0)] == 14
    assert row[lay.at("Attributes")] == 0x110810                          # turns off your attack


def test_what_the_server_script_does_is_a_dummy_aura_it_finds_the_spell_by():
    spells = FakeSpells(PANDAREN)
    for spell, amount in ((107076, -50), (107072, 100)):                  # Bouncy's aura 374, Epicurean's dummy
        p, lay = _port(spells, spell)
        row = p.rows[0]
        assert (row[lay.at("Effect", 0)], row[lay.at("EffectAura", 0)]) == (6, 4)
        assert row[lay.at("EffectBasePoints", 0)] == (amount - 1) & 0xFFFFFFFF
        assert p.scripted and p.effects[0] == ("slot", 0, 0)                # $s1 still the amount


def test_a_skill_3_3_5_lacks_is_dropped():
    lay = DbcLayout("Spell", DBD)
    gourmand = port_spell(FakeSpells(PANDAREN), 107073, layout=lay, index_tables=_index_tables(),
                          range_layout=DbcLayout("SpellRange", DBD), companion_ids=[9001], skills={185, 129})
    row = gourmand.rows[0]
    assert [(row[lay.at("EffectAura", k)], row[lay.at("EffectMiscValue", k)]) for k in range(2)] == [(98, 185), (0, 0)]
    assert gourmand.dropped == ["skill 980 (3.3.5 has no such skill)"] and gourmand.ids == [107073]


def test_more_than_three_effects_split_into_hidden_companions():
    jack, lay = _port(FakeSpells(KT), 291442)
    assert jack.ids == [291442, 9001, 9002, 9003]
    skills = [r[lay.at("EffectMiscValue", k)] for r in jack.rows for k in range(3) if r[lay.at("Effect", k)]]
    assert skills == [202, 197, 171, 164, 182, 755, 165, 186, 393, 333, 773]
    assert all(r[lay.at("Attributes")] & 0x80 for r in jack.rows[1:]) and not jack.rows[0][lay.at("Attributes")] & 0x80


def test_haymaker_melee_facing_attack_power_and_cast():
    hay, lay = _port(FakeSpells(KT), 287712)
    row = hay.rows[0]
    assert [row[lay.at("Effect", k)] for k in range(3)] == [2, 6, 98]                  # the dummy is gone
    assert row[lay.at("EffectMiscValue", 2)] == 300 and row[lay.at("EffectBasePoints", 2)] == 59
    assert row[lay.at("RangeIndex")] == 2 and row[lay.at("FacingCasterFlags")] == 1
    assert row[lay.at("CastingTimeIndex")] == 4 and row[lay.at("DurationIndex")] == 27
    assert hay.ap_bonus == 0.75


def test_texts_follow_their_effects():
    spells = FakeSpells(KT)
    ported = {s: _port(spells, s)[0] for s in (291628, 291843, 291442, 287712)}
    # 3.3.5 has no Versatility: what its translation (and the server script) does
    assert spell_texts(spells, ported, 291628)["Description_lang"] == \
        "Increases damage and healing done by $s1% and reduces damage taken by 0.5%. When you take damage, " \
        "heal for $s3% of that amount over $291843d."
    # inlined into the heal's tooltip: the references name Brush It Off, renumbered once (its third
    # effect, not BfA's third: the dropped -33)
    assert spell_texts(spells, ported, 291843)["Description_lang"] == \
        "Increases damage and healing done by $291628s1% and reduces damage taken by 0.5%. When you take " \
        "damage, heal for $291628s3% of that amount over $291843d."
    assert "${$AP*0.75}" in spell_texts(spells, ported, 287712)["Description_lang"]


def test_versatility_at_the_start_of_a_sentence():
    spells = FakeSpells({1: {"name": "Spirit", "desc": "Versatility increased by $s1%.", "effects": [aura(471, 2)]}})
    ported = {1: _port(spells, 1)[0]}
    assert spell_texts(spells, ported, 1)["Description_lang"] == \
        "Damage and healing done increased by $s1% and damage taken reduced by 1%."


def test_racial_rows_take_the_races_block_and_strip_takes_them_back():
    plan = racials.RacialPlan(2723, "Racial - Kul Tiran", [291417], block=22)
    plan.ported[291417] = PortedSpell(291417, "Rime", rows=[[291417]])
    plan.icons = {5: "Interface\\Icons\\ability_racial_rime"}
    icons = DBCFile(2)
    icons.add_record([1, icons.add_string("Interface\\Icons\\x")])
    sla, srci = DBCFile(14), DBCFile(8)
    sla.add_record([10, 754, 20599, 1, 0, 0, 0, 0, 0, 1, 0, 0, 2, 0])
    srci.add_record([862, 754, 1, 1535, 1170, 0, 0, 0])
    assert racials.add_icons(icons, plan) == {5: 222000}
    racials.add_abilities(sla, 754, plan, 0x600000)
    racials.add_race_class_info(srci, 754, plan, 0x600000)
    assert sla.records[-1][:5] == [222000, 2723, 291417, 0x600000, 0] and srci.records[-1][:3] == [222000, 2723, 0x600000]
    spell = RawDBC(234, 936)
    spell.add_row([291417] + [0] * 233)
    tables = {"Spell": spell, "SpellIcon": icons, "SkillLineAbility": sla, "SkillRaceClassInfo": srci}
    gone = racials.strip_racials(tables, plan)
    assert gone == {"Spell": 1, "SpellIcon": 1, "SkillLineAbility": 1, "SkillRaceClassInfo": 1}
    assert [r[0] for r in icons.records] == [1] and [r[0] for r in sla.records] == [10]


def test_voice_sound_ids_stay_below_the_race_blocks():
    from retroport.race import sounds
    assert sounds.ID_BLOCKS == racials.BLOCK_BASE
    se, csd = DBCFile(30), DBCFile(38)
    se.add_record([500] + [0] * 29)
    se.add_record([222000] + [0] * 29)                     # a Kul Tiran racial's sound
    voice = sounds.Voice()
    voice.kits[7] = None
    ids, _ = sounds.assign_ids(voice, se, csd)
    assert ids == {7: 501}


class FakeVisuals:
    def __init__(self):
        self.visuals = {83140: {"MissileDestinationAttachment": 34, "MissileAttachment": -1}}
        self.events = {83140: [{"StartEvent": 1, "SpellVisualKitID": 1}, {"StartEvent": 3, "SpellVisualKitID": 2},
                               {"StartEvent": 7, "SpellVisualKitID": 3}, {"StartEvent": 4, "SpellVisualKitID": 4}]}
        self.kit_effects = {1: [{"EffectType": 6, "Effect": 364663}], 2: [{"EffectType": 6, "Effect": 364664}],
                            3: [{"EffectType": 6, "Effect": 9178}, {"EffectType": 2, "Effect": 50}]}
        self.attach = {50: {"AttachmentID": 20, "SpellVisualEffectNameID": 60, "Scale": 1.0, "Offset": [0, 0, 0]}}
        self.effect_names = {60: {"ModelFileDataID": 70}}
        # kit effects of type 6 are SpellVisualAnim rows: an anim kit (Haymaker's wind-up one hotfixed
        # out of the install), or an initial and a loop animation (Quaking Palm's)
        self.visual_anims = {364663: {"InitialAnimID": -1, "LoopAnimID": -1, "AnimKitID": 17140},
                             364664: {"InitialAnimID": -1, "LoopAnimID": -1, "AnimKitID": 17141},
                             9178: {"InitialAnimID": -1, "LoopAnimID": -1, "AnimKitID": 2709},
                             1925: {"InitialAnimID": -1, "LoopAnimID": 118, "AnimKitID": 0},
                             18: {"InitialAnimID": -1, "LoopAnimID": 14, "AnimKitID": 0}}
        self.segments = {17141: [{"AnimID": 1444, "OrderIndex": 0}],
                         2709: [{"AnimID": 14, "OrderIndex": 1}, {"AnimID": 121, "OrderIndex": 0}]}


def test_visual_events_become_kits_with_the_spells_own_animations(monkeypatch):
    monkeypatch.setattr(visuals.sounds, "listfile_names", lambda _lf, fdids: {70: "spells/stunswirl_state_head.m2"})
    layouts = {t: DbcLayout(t, DBD) for t in ("SpellVisual", "SpellVisualKit")}
    ids = {t: visuals.BlockIds(222000) for t in ("SpellVisual", "SpellVisualKit", "SpellVisualEffectName", "SoundEntries")}
    pv = visuals.port_visual(FakeVisuals(), 83140, spell_name="Haymaker", stuns=True, layouts=layouts, ids=ids,
                             client=None, client_out="", work="", listfile="", cfs="KulTiran",
                             existing_effect_names={"spells\\stunswirl_state_head.m2": 50})
    LV, LK = layouts["SpellVisual"], layouts["SpellVisualKit"]
    kits = {k[0]: k for k in pv.kits}
    assert [pv.visual[LV.at(s)] for s in ("PrecastKit", "CastKit", "StateKit")] == [222000, 222001, 222002]
    assert [kits[i][LK.at("AnimID")] for i in (222000, 222001, 222002)] == [1446, 1444, 14]   # wind-up, punch, stun
    assert kits[222002][LK.at("HeadEffect")] == 50                                           # 3.3.5's own stun swirl
    assert any("event 4" in n for n in pv.notes)                                            # travel: no 3.3.5 moment
    # the client plays no id past 505: 3.3.5's spare spell animations the race's models leave free
    assert visuals.anim_slots(pv.anims, taken={0, 14, 1444, 1446}) == ({1444: 213, 1446: 214}, [])
    # a Pandaren's models use CustomSpell01-03 already
    assert visuals.anim_slots({1444, 1446, 14}, taken={213, 214, 215}) == ({1444: 216, 1446: 217}, [])
    assert visuals.anim_slots({1444, 1446}, taken=set(range(213, 222))) == ({1444: 222}, [1446])


def test_a_kits_animation_is_the_one_its_moment_starts_or_holds():
    """Quaking Palm: the palm strike (SpecialUnarmed, a loop animation alone) and the target's stun."""
    bv = FakeVisuals()
    assert visuals._kit_anim(bv, 1925, "CastKit", "Quaking Palm", stuns=True) == 118
    assert visuals._kit_anim(bv, 18, "StateKit", "Quaking Palm", stuns=True) == 14
    # an anim kit: a cast starts with its first segment, a state holds its last (knockdown, then stun)
    assert visuals._kit_anim(bv, 9178, "CastKit", "Haymaker", stuns=True) == 121
    assert visuals._kit_anim(bv, 9178, "StateKit", "Haymaker", stuns=True) == 14
    assert visuals._kit_anim(bv, 999, "CastKit", "Quaking Palm", stuns=True) is None


def test_racial_sql_teaches_the_line_and_binds_the_script():
    specs = [RaceSpec(22, ALLIANCE, 1), RaceSpec(23, HORDE, 2)]
    racial = sql.RacialSql(2723, "Racial - Kul Tiran", [125, 754], ap_bonus={287712: 0.75},
                           scripts={291628: "spell_retroport_brush_it_off"}, procs={291628: 664232},
                           no_bonus=[291843], old_spells=[20599])
    install, undo = sql.race_sql(specs, "Kul Tiran", {}, racial)
    assert "AND `skill` NOT IN (125, 754)" in install
    assert "WHERE `skill` IN (125, 754) AND `raceMask` & @BIT" in install
    assert "VALUES (6291456, 0, 2723, 0, 'Racial - Kul Tiran');" in install
    assert "(287712, 0, 0, 0.75, 0," in install and "(291843, 0, 0, 0, 0," in install
    # damage taken: no spell phase (AzerothCore warns about one it won't use)
    assert "(291628, 'spell_retroport_brush_it_off')" in install and "(291628, 664232, 1, 0, 100)" in install
    assert "DELETE FROM `spell_proc` WHERE `SpellId` IN (291628);" in undo
    assert "`cs`.`spell` IN (20599)" in sql.characters_cleanup_sql(specs, "Kul Tiran", racial)


def test_a_script_on_the_spells_a_racial_changes_is_bound_to_all_of_them():
    """Epicurean: every Well Fed buff carries the module's script; the undo takes the rows away."""
    specs = [RaceSpec(24, ALLIANCE, 1)]
    racial = sql.RacialSql(899, "Racial - Pandaren", [754], bound={"spell_retroport_epicurean": [18191, 57079]})
    install, undo = sql.race_sql(specs, "Pandaren", {}, racial)
    assert "DELETE FROM `spell_script_names` WHERE `ScriptName` = 'spell_retroport_epicurean';" in install
    assert "VALUES (18191, 'spell_retroport_epicurean'), (57079, 'spell_retroport_epicurean');" in install
    assert "DELETE FROM `spell_script_names` WHERE `ScriptName` = 'spell_retroport_epicurean';" in undo
    lay = DbcLayout("Spell", DBD)
    table = RawDBC(lay.field_count if hasattr(lay, "field_count") else 234, 4 * 234)
    rows = []
    for sid, name in ((18191, "Well Fed"), (433, "Food"), (57079, "Well Fed"), (46682, '"Well Fed"')):
        row = lay.blank()
        lay.set(row, "ID", sid)
        lay.set_text(table, row, "Name_lang", name)
        table.add_row(row)
    assert racials.spells_named(table, lay, "Well Fed") == [18191, 57079]


def test_a_rebuild_takes_its_own_installed_files_for_its_own(tmp_path):
    import json
    from retroport.race.build import _NotOurs, _previous_owns
    (tmp_path / "installs").mkdir()
    (tmp_path / "installs" / "1.json").write_text(json.dumps({"added": [
        {"target": r"F:/WoW\Data\patch-H.MPQ\Spells\8fx_kultiran_haymaker_cast_base.m2"}]}))
    (tmp_path / "race.json").write_text(json.dumps({"owns": [r"interface\icons\ability_racial_haymaker.blp"]}))
    owns = _previous_owns(str(tmp_path))
    assert owns == {r"spells\8fx_kultiran_haymaker_cast_base.m2", r"interface\icons\ability_racial_haymaker.blp"}

    class Client:
        def exists(self, rel):
            return True

        def find(self, rel):
            return ("patch", b"")
    view = _NotOurs(Client(), owns)
    assert not view.exists(r"Spells\8fx_kultiran_haymaker_cast_base.m2")     # converted again
    assert view.exists(r"Spells\StunSwirl_State_Head.m2")                   # the client's own


def test_core_tables_stay_in_the_package_even_unchanged(tmp_path):
    from retroport.race.build import RaceBuildResult, _save_tables, sha256
    name, rng = DBCFile(4), DBCFile(4)
    name.add_record([1, 0, 1, 0])
    rng.add_record([1, 0, 0, 0])
    res = RaceBuildResult(race="KulTiran", name="Kul Tiran")
    res.bases = {f"client/DBFilesClient/{t}.dbc": {"sha256": sha256(d.to_bytes())}
                 for t, d in (("NameGen", name), ("SpellRange", rng))}
    _save_tables({"NameGen": name, "SpellRange": rng}, str(tmp_path), "client/DBFilesClient", res)
    assert (tmp_path / "NameGen.dbc").exists()                 # installed with the race's names: kept
    assert not (tmp_path / "SpellRange.dbc").exists()          # loaded only for racials, unchanged: left out


def test_a_proc_on_the_owners_own_hits_keeps_the_hit_phase():
    racial = sql.RacialSql(2723, "Racial", [], scripts={1: "x"}, procs={1: 0x10})
    install, _undo = sql.race_sql([RaceSpec(22, ALLIANCE, 1)], "X", {}, racial)
    assert "(1, 16, 1, 2, 100)" in install


def test_a_helper_spell_keeps_bfas_attributes_so_its_aura_shows():
    # Brush It Off's heal: BfA shows its buff ("Heals for $w1."); only companions are hidden
    spells = FakeSpells(KT)
    plan = racials.RacialPlan(2723, "Racial - Kul Tiran", [291628, 291442], helpers=[291843])
    racials.port_racials(plan, spells, layout=DbcLayout("Spell", DBD), index_tables=_index_tables(),
                         range_layout=DbcLayout("SpellRange", DBD), block=22, listfile_names=lambda f: {})
    lay = DbcLayout("Spell", DBD)
    assert plan.ported[291843].rows[0][lay.at("Attributes")] == 0x50
    assert all(r[lay.at("Attributes")] & 0x80 for r in plan.ported[291442].rows[1:])


def scaled(misc):
    """A retail clan buff's rating: no base points, scaled by level (Coefficient on the Item column)."""
    return {"Effect": 6, "EffectAura": 189, "EffectMiscValue": [misc, 0], "Coefficient": 1.716, "ScalingClass": -1,
            "ImplicitTarget": [1, 0]}


MAGHAR = {
    274738: {"name": "Ancestral Call", "attr": [0x10, 0x10000420], "dur": 27,
             "desc": "Invoke the spirits of your ancestors, granting you $274741s1 of one of your two highest "
                     "secondary stat for $274739d.",
             "effects": [{"Effect": 6, "EffectAura": 4, "Coefficient": 1.716, "ScalingClass": -1,
                          "ImplicitTarget": [1, 0]}]},
    273220: {"name": "Savage Blood", "desc": "Reduces the duration of Poisons, Diseases, and Curses by $s1%.",
             "effects": [aura(4, 10), aura(245, -10, 4), aura(245, -10, 2), aura(245, -10, 3)]},
    273216: {"name": "Open Skies", "desc": "Increases mounted speed by $s2%.",
             "effects": [aura(172, 10), aura(4, 10), aura(211, 10)]},
    273217: {"name": "Sympathetic Vigor", "desc": "Increases pet health by $s1%.",
             "effects": [aura(4, 10), aura(382, 10, 1), aura(382, 10, 28)]},
    274739: {"name": "Rictus of the Laughing Skull", "aura": "Increases critical strike by $w1.",
             "effects": [scaled(0x700)]},
    274740: {"name": "Zeal of the Burning Blade", "aura": "Increases haste by $w1.", "effects": [scaled(0xE0000)]},
    274741: {"name": "Ferocity of the Frostwolf", "aura": "Increases mastery by $w1.", "effects": [scaled(1 << 25)]},
    274742: {"name": "Might of the Blackrock", "aura": "Increases versatility by $w1.", "effects": [scaled(0x7 << 28)]},
}


def test_retail_auras_3_3_5_has_under_the_same_id():
    """Savage Blood's dispel-type durations (245), Open Skies' mounted and flight speed (172, 211)."""
    spells = FakeSpells(MAGHAR)
    blood, lay = _port(spells, 273220)
    rows = blood.rows
    assert [(r[lay.at("EffectAura", k)], r[lay.at("EffectMiscValue", k)]) for r in rows for k in range(3)
            if r[lay.at("Effect", k)]] == [(4, 0), (245, 4), (245, 2), (245, 3)]
    assert rows[0][lay.at("EffectBasePoints", 1)] == (-11) & 0xFFFFFFFF and not blood.dropped
    skies, _ = _port(spells, 273216)
    assert [skies.rows[0][lay.at("EffectAura", k)] for k in range(3)] == [172, 4, 211] and not skies.dropped
    assert spell_texts(spells, {273216: skies}, 273216)["Description_lang"] == "Increases mounted speed by $s2%."


def test_a_level_scaled_rating_is_the_percent_it_is_worth_at_the_top_level():
    # 131.08 x 1.716 = 225 rating at level 90 (not 89: the table grows up to there)
    assert scaled_rating_percent(RETAIL_SCALING, RETAIL_RATINGS, "Item", 1.716, 8) == pytest.approx(4.89, abs=0.01)
    assert scaled_rating_percent(RETAIL_SCALING, RETAIL_RATINGS, "Item", 1.716, 28) == pytest.approx(4.17, abs=0.01)
    assert parse_game_table("Level\tItem\n1\t1.5\n2\t2\n") == (["Level", "Item"], [[1.0, 1.5], [2.0, 2.0]])


def test_clan_buffs_crit_and_haste_stay_ratings_mastery_and_versatility_damage_and_healing():
    spells = FakeSpells(MAGHAR)
    ported = {s: _port(spells, s)[0] for s in (274739, 274740, 274741, 274742)}
    lay = DbcLayout("Spell", DBD)
    rictus, zeal = ported[274739].rows[0], ported[274740].rows[0]
    # MOD_RATING with 3.3.5's (the same) bits, holding the percent; the module makes it rating
    assert (rictus[lay.at("EffectAura", 0)], rictus[lay.at("EffectMiscValue", 0)]) == (189, 0x700)
    assert (zeal[lay.at("EffectAura", 0)], zeal[lay.at("EffectMiscValue", 0)]) == (189, 0xE0000)
    assert rictus[lay.at("EffectBasePoints", 0)] == 4 and zeal[lay.at("EffectBasePoints", 0)] == 4     # 5%
    assert ported[274739].script == ported[274740].script == "spell_retroport_rating_pct"
    for s, pct in ((274741, 5), (274742, 4)):
        row = ported[s].rows[0]
        assert [(row[lay.at("EffectAura", k)], row[lay.at("EffectMiscValue", k)]) for k in range(3)] == \
            [(79, 127), (136, 0), (0, 0)]
        assert row[lay.at("EffectBasePoints", 0)] == row[lay.at("EffectBasePoints", 1)] == pct - 1
        assert not ported[s].script and not ported[s].dropped
    assert any("damage taken: 2% less" in n for n in ported[274742].scripted)
    auras = {s: spell_texts(spells, ported, s)["AuraDescription_lang"] for s in ported}
    assert auras == {274739: "Increases critical strike chance by $s1%.", 274740: "Increases haste by $s1%.",
                     274741: "Increases damage and healing done by $s1%.",
                     274742: "Increases damage and healing done by $s1% and reduces damage taken by 2%."}


def test_a_rating_3_3_5_cannot_hold_is_dropped():
    spells = FakeSpells({1: {"name": "Fixed", "effects": [aura(189, 300, (1 << 25) | (1 << 13) | (1 << 8))]}})
    p, lay = _port(spells, 1)
    row = p.rows[0]
    assert (row[lay.at("EffectAura", 0)], row[lay.at("EffectMiscValue", 0)], row[lay.at("Effect", 1)]) == (189, 0x100, 0)
    assert row[lay.at("EffectBasePoints", 0)] == 299 and not p.script            # a fixed rating stays rating
    assert p.dropped == ["aura 189 (rating bit 13 rating: 3.3.5 has none)",
                         "aura 189 (mastery rating: a fixed rating 3.3.5 has no equivalent of)"]


def test_the_pets_health_is_a_pet_spell_azerothcore_casts_on_the_pet():
    """Sympathetic Vigor: retail's pet stat % (382, twice: two pet kinds) is a dummy aura on the
    owner and a pet spell (3.3.5's health %, 133) spell_pet_auras puts on every permanent pet."""
    spells = FakeSpells(MAGHAR)
    plan = racials.RacialPlan(2598, "Racial - Mag'har Orc", [273217])
    racials.port_racials(plan, spells, layout=DbcLayout("Spell", DBD), index_tables=_index_tables(),
                         range_layout=DbcLayout("SpellRange", DBD), block=26, listfile_names=lambda f: {})
    lay = DbcLayout("Spell", DBD)
    vigor = plan.ported[273217]
    row = vigor.rows[0]
    assert [(row[lay.at("Effect", k)], row[lay.at("EffectAura", k)]) for k in range(3)] == [(6, 4), (6, 4), (0, 0)]
    assert row[lay.at("EffectMiscValue", 1)] == 0                          # retail's pet kind means nothing here
    (pet,) = vigor.pet_rows
    assert (pet[lay.at("Effect", 0)], pet[lay.at("EffectAura", 0)], pet[lay.at("EffectBasePoints", 0)]) == (6, 133, 9)
    assert pet[lay.at("ImplicitTargetA", 0)] == 1 and pet[lay.at("Attributes")] & 0x40
    assert vigor.pet_auras == [(273217, 1, pet[0])]
    assert plan.learned == [273217] and pet[0] in plan.spell_ids             # not learned; stripped on a rebuild
    assert any("misc 28" in d for d in vigor.dropped)
    table = RawDBC(234, 4 * 234)
    racials.add_spells(table, lay, plan, spells, {}, None)
    texts = {r[0]: lay.text(table, r, "AuraDescription_lang") for r in map(table.row, table.records)}
    assert texts[pet[0]] == "Health increased by $s1%."
    install, undo = sql.race_sql([RaceSpec(26, HORDE, 2)], "Mag'har Orc", {},
                                 sql.RacialSql(2598, "Racial - Mag'har Orc", [125], pet_auras=vigor.pet_auras))
    assert ("INSERT INTO `spell_pet_auras` (`spell`, `effectId`, `pet`, `aura`) VALUES "
            f"(273217, 1, 0, {pet[0]});") in install
    assert "DELETE FROM `spell_pet_auras` WHERE `spell` IN (273217);" in undo


def test_ancestral_call_ports_all_four_buffs_its_script_casts(monkeypatch):
    spells = FakeSpells(MAGHAR)
    monkeypatch.setattr(racials, "find_racial_line",
                        lambda _bfa, _race: (2598, "Racial - Mag'har Orc", [274738, 273220, 273216, 273217]))
    plan = racials.plan_racials(None, spells, 36)
    assert plan.helpers == [274739, 274740, 274741, 274742]                # its tooltip names two
    racials.port_racials(plan, spells, layout=DbcLayout("Spell", DBD), index_tables=_index_tables(),
                         range_layout=DbcLayout("SpellRange", DBD), block=26, listfile_names=lambda f: {})
    lay = DbcLayout("Spell", DBD)
    call = plan.ported[274738].rows[0]
    assert call[lay.at("AttributesEx")] == 0x10000000                      # its own aura stays off the bar
    assert plan.ported[273220].rows[1][0] in plan.learned                  # Savage Blood's companion
    assert len(set(plan.spell_ids)) == len(plan.spell_ids)
    desc = spell_texts(spells, plan.ported, 274738, racials.TEXTS[274738])["Description_lang"]
    assert desc.startswith("Invoke the spirits of your ancestors, granting you one of their powers at random for "
                           "$274739d: $274739s1% critical strike chance, $274740s1% haste, $274741s1% more")
    assert racials.SCRIPTS[274738] == "spell_retroport_ancestral_call"
