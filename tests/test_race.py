"""retroport race: MPQ priority, race DBC edits (and their removal), GlueXML patches, icon splice, SQL."""

import struct

import pytest
from PIL import Image

from retroport.dbc import DBCError, DBCFile, RawDBC
from retroport.formats import blp
from retroport.mpq import _patch_rank, hash_string
from retroport.race import glue, icons
from retroport.race.build import _apply
from retroport.race.sql import race_sql
from retroport.race.tables import ALLIANCE, HORDE, RaceSpec, race_bit, strip_race


def test_mpq_hashes_and_patch_order():
    # Storm's well-known table keys
    assert hash_string("(hash table)", 3) == 0xC3AF3770
    assert hash_string("(block table)", 3) == 0xEC83B3A3
    assert _patch_rank("3") is None and _patch_rank("4") < _patch_rank("9") < _patch_rank("a") < _patch_rank("z")
    assert _patch_rank("x") > _patch_rank("h")                  # letters rank by letter, locale or not


def test_race_ids_must_fit_a_32_bit_mask():
    assert race_bit(22) == 1 << 21
    with pytest.raises(DBCError, match="32-bit"):
        race_bit(53)


# --------------------------------------------------------------------------- synthetic client tables

def _chr_races():
    d = DBCFile(69)
    for rid, team, lang, cfs, prefix, disp in ((1, 0, 7, "Human", "Hu", (49, 50)), (2, 1, 1, "Orc", "Or", (51, 52))):
        r = [0] * 69
        r[0], r[1], r[2], r[4], r[5], r[7], r[13] = rid, 0xC, rid, disp[0], disp[1], lang, team
        r[6], r[11], r[14] = d.add_string(prefix), d.add_string(cfs), d.add_string(cfs)
        r[65], r[66], r[67] = d.add_string("NORMAL"), d.add_string("PIERCINGS"), d.add_string("NORMAL")
        d.add_record(r)
    return d


def _tables():
    cmd, cdi = DBCFile(28), DBCFile(16)
    for i in (49, 50, 51, 52):
        r = [0] * 28
        r[0], r[2], r[4] = i, cmd.add_string(f"Character\\X\\{i}.mdx"), 0x3F800000
        cmd.add_record(r)
        cdi.add_record([i, i, 0, 0, 0x3F800000, 255] + [0] * 10)
    base = RawDBC(2, 2)
    for race in (1, 2):
        for cls in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11):
            base.add_record(struct.pack("<BB", race, cls))
    outfit = RawDBC(77, 296)
    for i, (race, cls, sex) in enumerate(((1, 1, 0), (1, 1, 1), (2, 1, 0)), 1):
        outfit.add_record(struct.pack("<I4B", i, race, cls, sex, 0) + struct.pack("<72I", *([25] * 72)))
    srci, sla, fac = DBCFile(8), DBCFile(14), DBCFile(57)
    srci.add_record([1, 98, 0x44D, 0, 0, 0, 0, 0])               # Common: Alliance races
    srci.add_record([2, 109, 0x2B2, 0, 0, 0, 0, 0])              # Orcish: Horde races
    srci.add_record([3, 43, 0xFFFFFFFF, 0, 0, 0, 0, 0])          # everyone
    sla.add_record([1, 754, 20599, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0])
    fac.add_record([72, 1, 0x44D, 0x2B2, 0, 0] + [0] * 51)
    emotes = DBCFile(5)
    emotes.add_record([1, 10, 1, 0, 500])
    emotes.add_record([2, 10, 2, 0, 600])
    cs, hg, fh = DBCFile(10), DBCFile(6), DBCFile(8)
    cs.add_record([1, 1, 0, 0, 0, 0, 0, 1, 0, 0])
    return {"ChrRaces": _chr_races(), "CreatureModelData": cmd, "CreatureDisplayInfo": cdi, "CharBaseInfo": base,
            "CharStartOutfit": outfit, "SkillRaceClassInfo": srci, "SkillLineAbility": sla, "Faction": fac,
            "EmotesTextSound": emotes, "CharSections": cs, "CharHairGeosets": hg, "CharacterFacialHairStyles": fh}


class _Char:
    sections = [{"section": 0, "variation": 0, "color": 0, "flags": 1,
                 "textures": ["Character\\KulTiran\\Male\\KulTiranMaleSkin00_00.blp", "", ""]}]
    hair = [{"VariationID": 0, "GeosetID": 1, "Showscalp": 0}]
    facial = [{"VariationID": 0, "Geoset": [0, 0, 0, -1, -1]}]


SPECS = [RaceSpec(22, ALLIANCE, 1), RaceSpec(23, HORDE, 2)]


def test_apply_adds_both_factions_and_strip_undoes_it():
    t = _tables()
    before = {k: [list(r) if isinstance(r, list) else r for r in v.records] for k, v in t.items()}
    counts = _apply(t, SPECS, name="Kul Tiran", cfs="KulTiran", look=1, ids=(7001, 7002, 9001, 9002),
                    characters={0: _Char()})
    chr_races = t["ChrRaces"]
    a, h = chr_races.find_by_id(22), chr_races.find_by_id(23)
    assert (a[13], a[7], a[2]) == (0, 7, 1) and (h[13], h[7], h[2]) == (1, 1, 2)    # faction from the template
    assert chr_races.get_string(a[11]) == chr_races.get_string(h[11]) == "KulTiran"
    assert chr_races.get_string(a[6]) == chr_races.get_string(h[6]) == "Hu"          # look template's helmets
    assert chr_races.get_string(a[31]) == chr_races.get_string(a[48]) == "Kul Tiran"
    assert (a[4], a[5]) == (h[4], h[5]) == (9001, 9002)
    m = t["CreatureModelData"].find_by_id(7001)
    assert t["CreatureModelData"].get_string(m[2]) == "Character\\KulTiran\\Male\\KulTiranMale.mdx"
    assert t["CreatureDisplayInfo"].find_by_id(9002)[1] == 7002
    assert counts["CharBaseInfo"] == 20                                               # ten classes each
    races = [r[4] for r in t["CharStartOutfit"].records]
    assert races.count(22) == 2 and races.count(23) == 1
    srci = {r[1]: r[2] for r in t["SkillRaceClassInfo"].records}
    assert srci[98] & race_bit(22) and not srci[98] & race_bit(23)                  # Common: Alliance twin
    assert srci[109] & race_bit(23) and not srci[109] & race_bit(22)                # Orcish: Horde twin
    assert srci[43] == 0xFFFFFFFF
    assert t["Faction"].records[0][2] & race_bit(22) and t["Faction"].records[0][3] & race_bit(23)
    assert sorted(r[2] for r in t["EmotesTextSound"].records) == [1, 2, 22, 23]       # voices from the look template
    assert [r[1] for r in t["CharSections"].records].count(22) == 1
    assert t["CharacterFacialHairStyles"].records[-1][3:] == [0, 0, 0, 0, 0]

    with pytest.raises(DBCError, match="already exists"):
        _apply(t, SPECS[:1], name="x", cfs="KulTiran", look=1, ids=(7003, 7004, 9003, 9004), characters=None)
    for s in SPECS:
        strip_race(t, s.race_id)
    after = {k: [list(r) if isinstance(r, list) else r for r in v.records] for k, v in t.items()}
    assert after == before


# --------------------------------------------------------------------------- GlueXML

LUA = """MAX_RACES = 10;\r\nRACE_ICON_TCOORDS = {\r\n\t["HUMAN_MALE"]\t\t= {0, 0.125, 0, 0.25},\r\n};\r\n"""
XML = ("\t\t<Frames>\r\n" + "".join(
    f'\t\t\t<CheckButton name="CharacterCreateRaceButton{n}" inherits="CharacterCreateRaceButtonTemplate" id="{n}">\r\n'
    f"\t\t\t</CheckButton>\r\n" for n in range(1, 11)) + "\t\t</Frames>\r\n")


def test_glue_patches_are_rerunnable():
    icons_ = {"KULTIRAN_MALE": (0.625, 0.75, 0.0, 0.25), "KULTIRAN_FEMALE": (0.625, 0.75, 0.5, 0.75)}
    once = glue.patch_character_create_lua(LUA, 12, icons_, "KULTIRAN")
    assert "MAX_RACES = 12;" in once and once.count("CharacterCreate_LayoutRaceButtons()") == 2
    assert glue.patch_character_create_lua(once, 12, icons_, "KULTIRAN") == once
    assert glue.icon_coords(once)["KULTIRAN_FEMALE"] == (0.625, 0.75, 0.0 + 0.5, 0.75)
    assert glue.icon_coords(once)["HUMAN_MALE"] == (0, 0.125, 0, 0.25)
    assert "\r\n-- [retroport] begin icons KULTIRAN\r\n" in once                       # keeps CRLF

    xml = glue.patch_character_create_xml(XML, 12)
    assert sorted(int(n) for n in glue._BUTTON.findall(xml)) == list(range(1, 13))
    assert glue.patch_character_create_xml(xml, 12) == xml

    parent = "function SetBackgroundModel(model, name)\nend\n"
    p1 = glue.patch_glue_parent(parent, {"KULTIRAN": "Human"}, "KULTIRAN")
    assert glue.patch_glue_parent(p1, {"KULTIRAN": "Human"}, "KULTIRAN") == p1
    assert 'GLUE_BACKGROUND_ALIASES["KULTIRAN"] = "Human";' in p1

    s1 = glue.patch_glue_strings('ABILITY_INFO_HUMAN1 = "- One.";\n', {"RACE_INFO_KULTIRAN": 'Say "ahoy"'}, "KULTIRAN")
    assert 'RACE_INFO_KULTIRAN = "Say \\"ahoy\\"";' in s1
    assert glue.read_strings(s1, "RACE_INFO_KULTIRAN") == {"RACE_INFO_KULTIRAN": 'Say "ahoy"'}
    assert glue.read_strings(s1, "ABILITY_INFO_HUMAN") == {"ABILITY_INFO_HUMAN1": "- One."}


# --------------------------------------------------------------------------- icon atlas

def test_icon_splice_touches_only_its_cell(tmp_path):
    atlas = Image.new("RGBA", (512, 256), (0, 0, 0, 0))
    atlas.paste((0, 0, 255, 255), (0, 0, 320, 256))               # columns 0-4 taken
    data = blp.encode_dxt(atlas)
    path = tmp_path / "a.blp"
    path.write_bytes(data)
    img = blp.decode(str(path))
    assert icons.free_column(img, set()) == 5
    assert icons.free_column(img, {(5, 0)}) == 6
    out = icons.splice(data, (5, 2), Image.new("RGBA", (64, 64), (255, 0, 0, 255)))
    path.write_bytes(out)
    new = blp.decode(str(path))
    r, g, b, _a = new.getpixel((352, 160))
    assert r > 240 and g < 8 and b < 8                            # DXT stores 5-6-5 colour
    assert new.crop((0, 0, 320, 256)).tobytes() == img.crop((0, 0, 320, 256)).tobytes()
    assert new.getpixel((352, 32))[3] == 0                        # row 0 untouched
    assert icons.coords(5, 2, (512, 256)) == (0.625, 0.75, 0.5, 0.75)


def test_sql_mirrors_the_template_race():
    install, undo = race_sql(SPECS, "Kul Tiran", {9001: 49, 9002: 50})
    assert "SET @RACE := 22, @TEMPLATE := 1, @BIT := 2097152, @TEMPLATE_BIT := 1;" in install
    assert "SET @RACE := 23, @TEMPLATE := 2, @BIT := 4194304, @TEMPLATE_BIT := 2;" in install
    assert "UPDATE `quest_template` SET `AllowableRaces` = `AllowableRaces` | @BIT" in install
    assert "`AllowableRace` > 0" in install                         # -1 already means every race
    assert "SELECT 9001, `BoundingRadius`" in install and "FROM `creature_model_info` WHERE `DisplayID` = 49" in install
    assert "DELETE FROM `playercreateinfo` WHERE `race` = @RACE;" in undo
    assert "`AllowableRaces` & ~@BIT" in undo
