"""retroport race: the race's own voice (BfA sound kits -> 3.3.5 SoundEntries and the rows using them)."""

from retroport.dbc import DBCFile, u32_to_float
from retroport.race import sounds
from retroport.race.tables import strip_race

BFA = {
    "SoundKit": [(k, {"ID": k, "SoundType": t, "VolumeFloat": v, "Flags": 0, "MinDistance": 8.0,
                      "DistanceCutoff": 45.0, "EAXDef": 0})
                 for k, t, v in ((100, 29, 1.0), (101, 29, 1.0), (200, 12, 0.69), (201, 12, 0.69), (300, 10, 0.8),
                                 (301, 10, 0.8), (400, 29, 1.0))],
    "SoundKitEntry": [(i, {"ID": i, "SoundKitID": k, "FileDataID": f, "Frequency": 1})
                      for i, (k, f) in enumerate(((100, 1000), (100, 1001), (101, 1010), (200, 2000), (201, 2010),
                                                  (300, 3000), (300, 3001), (301, 3010)), 1)],
    "EmotesText": [(11, {"Name": "LAUGH"}), (507, {"Name": "FORTHEALLIANCE"})],
    "EmotesTextSound": [(1, {"RaceID": 32, "ClassID": 0, "SexID": 0, "SoundID": 100, "EmotesTextID": 11}),
                        (2, {"RaceID": 32, "ClassID": 0, "SexID": 1, "SoundID": 101, "EmotesTextID": 11}),
                        (3, {"RaceID": 32, "ClassID": 0, "SexID": 0, "SoundID": 100, "EmotesTextID": 507}),
                        (4, {"RaceID": 32, "ClassID": 5, "SexID": 0, "SoundID": 400, "EmotesTextID": 11}),
                        (5, {"RaceID": 1, "ClassID": 0, "SexID": 0, "SoundID": 400, "EmotesTextID": 11})],
    "VocalUISounds": [(1, {"VocalUIEnum": 0, "RaceID": 32, "ClassID": 0, "NormalSoundID": [200, 201]}),
                      (2, {"VocalUIEnum": 99, "RaceID": 32, "ClassID": 0, "NormalSoundID": [200, 201]})],
    "ChrRaces": [(32, {"MaleDisplayID": 1, "FemaleDisplayID": 2})],
    "CreatureDisplayInfo": [(1, {"SoundID": 50, "ModelID": 9}), (2, {"SoundID": 0, "ModelID": 10})],
    "CreatureModelData": [(9, {"SoundID": 0}), (10, {"SoundID": 0})],
    "CreatureSoundData": [(50, {"SoundExertionID": 300, "SoundDeathID": 301, "SoundFidget": [0] * 5,
                                "CustomAttack": [0] * 4, "BattleShoutSoundID": 999})],
}
NAMES = {1000: r"sound\character\kt_male\laugh01.ogg", 1001: r"sound\character\kt_male\laugh02.ogg",
         1010: r"sound\character\kt_female\laugh01.ogg", 2000: r"sound\character\kt_male\err_full.ogg",
         2010: r"sound\character\kt_female\err_full.ogg", 3000: r"sound\character\kt_male\attack01.ogg",
         3001: r"sound\character\shared\attack02.ogg", 3010: r"sound\character\kt_male\death.ogg"}


def _voice():
    return sounds.collect_voice(lambda t: BFA[t], 32, "KulTiran", emotes={11}, vocal_enums={0},
                                names=lambda ids: {i: NAMES[i] for i in ids if i in NAMES})


def test_collect_voice_maps_emotes_errors_and_combat_sounds():
    v = _voice()
    assert v.emotes == {(11, 0): 100, (11, 1): 101}               # class rows and other races ignored
    assert v.vocal == {0: (200, 201)}
    assert v.creature[0][1] == 300 and v.creature[0][6] == 301 and v.creature[0][10] == 0
    assert 1 not in v.creature                                    # the female display has no sounds
    assert any("forthealliance" in s for s in v.skipped) and any("99" in s for s in v.skipped)
    assert v.kits[100].name == "KulTiranMaleEmoteLaugh" and v.kits[200].name == "KulTiranMaleError00"
    assert v.kits[300].name == "KulTiranMalePlayerExertion"
    # one SoundEntries row has one directory: a kit spread over two moves to the race's folder
    assert [p for _f, p, _w in v.kits[300].files] == [r"Sound\Character\KulTiran\attack01.ogg",
                                                     r"Sound\Character\KulTiran\attack02.ogg"]
    assert v.kits[100].directory == r"sound\character\kt_male"


def test_write_voice_files_drops_what_is_not_ogg(tmp_path):
    v = _voice()
    files = sounds.write_voice_files(lambda fdid: b"\0\0\0\0" if fdid == 3010 else b"OggS" + bytes(8), v, str(tmp_path))
    assert (tmp_path / "sound" / "character" / "kt_male" / "laugh02.ogg").read_bytes()[:4] == b"OggS"
    assert len(files) == 7
    assert 301 not in v.kits and v.creature[0][6] == 0            # the death sound could not be extracted
    assert any("3010" in s for s in v.skipped)


def _client_tables():
    se, csd, cmd, cdi = DBCFile(30), DBCFile(38), DBCFile(28), DBCFile(16)
    ets, vui, chr_races = DBCFile(5), DBCFile(7), DBCFile(69)
    se.add_record([5, 29, se.add_string("HumanMaleLaugh")] + [0] * 27)
    csd.add_record([49] + [0] * 8 + [7] + [0] * 28)               # Human male: footsteps 7
    for model, path in ((900, "KulTiranMale.mdx"), (901, "KulTiranFemale.mdx"), (49, "HumanMale.mdx")):
        cmd.add_record([model, 0, cmd.add_string(path)] + [0] * 10 + [49] + [0] * 14)
    for display, model in ((800, 900), (801, 901), (49, 49)):
        cdi.add_record([display, model] + [0] * 14)
    ets.add_record([1, 11, 1, 0, 5])
    vui.add_record([1, 0, 1, 0, 0, 0, 0])
    for race, displays, cfs in ((1, (49, 49), "Human"), (22, (800, 801), "KulTiran"), (23, (800, 801), "KulTiran")):
        row = [0] * 69
        row[0], row[4], row[5], row[11] = race, displays[0], displays[1], chr_races.add_string(cfs)
        chr_races.add_record(row)
    return {"SoundEntries": se, "CreatureSoundData": csd, "CreatureModelData": cmd, "CreatureDisplayInfo": cdi,
            "EmotesTextSound": ets, "VocalUISounds": vui, "ChrRaces": chr_races}


def test_voice_rows_and_their_removal_on_rebuild(tmp_path):
    v = _voice()
    sounds.write_voice_files(lambda fdid: b"OggS" + bytes(8), v, str(tmp_path))
    t = _client_tables()
    before = {k: [list(r) for r in d.records] for k, d in t.items()}
    sound_ids, csd_ids = sounds.assign_ids(v, t["SoundEntries"], t["CreatureSoundData"])
    assert sorted(sound_ids.values()) == list(range(6, 6 + len(v.kits))) and csd_ids == {0: 50}

    assert sounds.add_sound_entries(t["SoundEntries"], v, sound_ids) == len(v.kits)
    se = t["SoundEntries"]
    laugh = se.find_by_id(sound_ids[100])
    assert se.get_string(laugh[2]) == "KulTiranMaleEmoteLaugh"
    assert [se.get_string(o) for o in laugh[3:5]] == ["laugh01.ogg", "laugh02.ogg"] and laugh[5] == 0
    assert laugh[13:15] == [1, 1] and se.get_string(laugh[23]) == r"sound\character\kt_male"
    assert laugh[1] == 29 and u32_to_float(laugh[24]) == 1.0 and u32_to_float(laugh[27]) == 45.0

    assert sounds.add_creature_sounds(t["CreatureSoundData"], t["CreatureModelData"], v, sound_ids, csd_ids,
                                      (900, 901)) == 1
    row = t["CreatureSoundData"].find_by_id(50)
    assert row[1] == sound_ids[300] and row[6] == sound_ids[301] and row[9] == 7 and row[10] == 0
    assert t["CreatureModelData"].find_by_id(900)[13] == 50       # the male model uses it ...
    assert t["CreatureModelData"].find_by_id(901)[13] == 49       # ... the female keeps the template's

    for race in (22, 23):
        assert sounds.add_emote_sounds(t["EmotesTextSound"], v, race, sound_ids) == 2
        assert sounds.add_vocal_sounds(t["VocalUISounds"], v, race, sound_ids) == 1
    assert [r[1:] for r in t["EmotesTextSound"].records if r[2] == 22] == [[11, 22, 0, sound_ids[100]],
                                                                          [11, 22, 1, sound_ids[101]]]
    assert [r[1:5] for r in t["VocalUISounds"].records if r[2] == 23] == [[0, 23, sound_ids[200], sound_ids[201]]]

    # a rebuild (--replace) strips both races: their voice goes with the last of them, Human's stays
    strip_race(t, 22)
    assert len(t["SoundEntries"].records) == 1 + len(v.kits)     # race 23 still uses them
    strip_race(t, 23)
    for k in ("SoundEntries", "CreatureSoundData", "EmotesTextSound", "VocalUISounds"):
        assert [list(r) for r in t[k].records] == before[k], k
    assert [r[0] for r in t["CreatureModelData"].records] == [49]