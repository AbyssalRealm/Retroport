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
    # character select plays the scene music by the race's own name (third client test)
    assert 'GlueAmbienceTracks["KULTIRAN"] = GlueAmbienceTracks["KULTIRAN"] or GlueAmbienceTracks["HUMAN"];' in p1

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


def test_round_portrait_atlas_shares_the_cell_and_the_circle():
    # Blizzard's round atlas has Cataclysm's Worgen in column 5: a new race skips it there too
    races = Image.new("RGBA", (512, 256), (0, 0, 0, 0))
    races.paste((0, 0, 255, 255), (0, 0, 320, 256))
    rounds = races.copy()
    rounds.paste((90, 90, 90, 255), (320, 0, 384, 256))
    assert icons.free_column(races, set()) == 5 and icons.free_column(races, set(), rounds) == 6
    # the round portrait takes the mask's alpha exactly; the square icon's frame is zoomed out
    mask = Image.new("L", (64, 64), 0)
    mask.paste(255, (16, 16, 48, 48))
    square = Image.new("RGBA", (64, 64), (200, 0, 0, 255))
    square.paste((0, 0, 0, 255), (0, 0, 64, 4))                   # a dark frame line along the top
    out = icons.round_icon(square, mask)
    assert out.getchannel("A").tobytes() == mask.tobytes()
    r, g, b, _a = out.getpixel((32, 2))
    assert r > 180 and g < 10 and b < 10                          # the frame fell outside (resampled)


def test_sql_mirrors_the_template_race():
    install, undo = race_sql(SPECS, "Kul Tiran", {9001: 49, 9002: 50})
    assert "SET @RACE := 22, @TEMPLATE := 1, @BIT := 2097152, @TEMPLATE_BIT := 1;" in install
    assert "SET @RACE := 23, @TEMPLATE := 2, @BIT := 4194304, @TEMPLATE_BIT := 2;" in install
    assert "UPDATE `quest_template` SET `AllowableRaces` = `AllowableRaces` | @BIT" in install
    assert "`AllowableRace` > 0" in install                         # -1 already means every race
    assert "SELECT 9001, `BoundingRadius`" in install and "FROM `creature_model_info` WHERE `DisplayID` = 49" in install
    assert "DELETE FROM `playercreateinfo` WHERE `race` = @RACE;" in undo
    assert "`AllowableRaces` & ~@BIT" in undo


# --------------------------------------------------------------------------- in-game fixes (first client test)

def test_unmanaged_geoset_groups_keep_one_default_variant():
    from retroport.character.convert import unmanaged_geoset_remap
    assert unmanaged_geoset_remap({0, 401, 1702, 2001, 2002, 2201, 2202, 2302, 2303}) == {
        2001: 0, 2002: None, 2201: 0, 2202: None, 2302: 0, 2303: None}


def test_facial_styles_fold_in_piercings_and_switch_eyes_on():
    from retroport.character.convert import facial_styles
    female = facial_styles([{"VariationID": 0, "Geoset": [-1] * 5}],
                           [{"VariationID": 1, "GeosetID": 3, "GeosetType": 2},
                            {"VariationID": 1, "GeosetID": 4, "GeosetType": 3}], eyes_geoset=2)
    # slot 1 -> 3xx, slot 2 -> 2xx, slot 4 -> 17xx (as 3.3.5's own Human/Blood Elf rows)
    assert female == [{"VariationID": 0, "Geoset": [0, 0, 0, 0, 2]}, {"VariationID": 1, "Geoset": [0, 4, 3, 0, 2]}]
    male = facial_styles([{"VariationID": 0, "Geoset": [0, -1, -1, -1, -1]},
                          {"VariationID": 1, "Geoset": [2, -1, -1, -1, -1]}], [], eyes_geoset=None)
    assert male == [{"VariationID": 0, "Geoset": [0, 0, 0, 0, 0]}, {"VariationID": 1, "Geoset": [2, 0, 0, 0, 0]}]


def test_remap_geosets_renumbers_and_hides(tmp_path):
    from retroport.convert.fixup import remap_geosets
    from retroport.formats import structs as S
    s = bytearray(0x30)
    s[0:4] = b"SKIN"
    so = len(s)
    for sid in (0, 2001, 2002):
        rec = bytearray(48)
        struct.pack_into("<H", rec, 0, sid)
        s += rec
    S.write_m2array(s, S.SKIN_SUBMESHES, 3, so)
    bo = len(s)
    for sub in (0, 1, 2):
        rec = bytearray(24)
        struct.pack_into("<H", rec, 4, sub)
        s += rec
    S.write_m2array(s, S.SKIN_BATCHES, 3, bo)
    p = tmp_path / "x00.skin"
    p.write_bytes(bytes(s))
    assert remap_geosets([str(p)], {2001: 0, 2002: None}) == {2001: 1, 2002: 1}
    out = p.read_bytes()
    assert [struct.unpack_from("<H", out, so + i * 48)[0] for i in range(3)] == [0, 0, 2002]
    n, off = S.read_m2array(out, S.SKIN_BATCHES)
    assert [struct.unpack_from("<H", out, off + i * 24 + 4)[0] for i in range(n)] == [0, 1]


def test_lettered_patches_rank_above_lettered_locale_patches(tmp_path, monkeypatch):
    from retroport import mpq
    data = tmp_path / "Data"
    (data / "enUS").mkdir(parents=True)
    for p in ("enUS/locale-enUS.MPQ", "enUS/patch-enUS-x.MPQ", "patch-E.MPQ", "patch-4.MPQ"):
        (data / p).write_bytes(b"")
    (data / "patch-H.MPQ").mkdir()
    monkeypatch.setattr(mpq, "MPQ", lambda path: path)
    order = [label for label, _src in mpq.ClientData(str(tmp_path)).sources]
    assert order == [r"Data\patch-H.MPQ (folder)", "patch-E.MPQ", "patch-4.MPQ",
                     r"enUS\patch-enUS-x.MPQ", r"enUS\locale-enUS.MPQ"]


# --------------------------------------------------------------------------- second client test

def test_unmanaged_groups_keep_only_a_piece_that_fills_a_hole():
    from retroport.character.convert import unmanaged_geoset_remap
    # belts are the client's (1801 + the belt's geoset value, as BfA): left alone (fourth test -
    # hiding them took the band away from belts that ask for it)
    assert unmanaged_geoset_remap({0, 1802, 1803}, {1802: 0.5, 1803: 0.0}) == {}
    # a group whose pieces all lie on a closed body hides whole
    assert unmanaged_geoset_remap({0, 2302, 2303}, {2302: 0.5, 2303: 0.0}) == {2302: None, 2303: None}
    # feet: both fill the hole -> the default 2001, the alternative hides
    assert unmanaged_geoset_remap({0, 2001, 2002}, {2001: 1.0, 2002: 0.83}) == {2001: 0, 2002: None}
    # no default, one filler -> that one
    assert unmanaged_geoset_remap({0, 1902, 1903}, {1902: 0.2, 1903: 1.0}) == {1902: None, 1903: 0}
    assert unmanaged_geoset_remap({0, 1902, 1903}, {}) == {1902: None, 1903: None}


def test_hole_fill_ratios_tell_a_patch_from_a_band_and_a_closed_piece(tmp_path):
    from retroport.character.convert import hole_fill_ratios
    from retroport.formats import structs as S
    # a 3x3 grid of quads with the middle one missing (the body), the missing quad (2001), a band
    # standing on one edge of the hole (1802) and a closed tetrahedron (1803)
    pts = [(i, j, 0.0) for j in range(4) for i in range(4)] + [(1, 1, 1.0), (2, 1, 1.0)] + \
          [(5, 5, 0.0), (6, 5, 0.0), (5, 6, 0.0), (5, 5, 1.0)]

    def quad(a, b, c, d):
        return [a, b, c, a, c, d]

    def grid(i, j):
        return quad(j * 4 + i, j * 4 + i + 1, (j + 1) * 4 + i + 1, (j + 1) * 4 + i)

    pieces = {0: [k for j in range(3) for i in range(3) if (i, j) != (1, 1) for k in grid(i, j)],
              2001: grid(1, 1), 1802: quad(5, 6, 17, 16),
              1803: [18, 19, 20, 18, 21, 19, 19, 21, 20, 20, 21, 18]}
    m2 = bytearray(0x200)
    S.write_m2array(m2, S.HDR_VERTICES, len(pts), len(m2))
    for p in pts:
        rec = bytearray(48)
        struct.pack_into("<3f", rec, 0, *p)
        m2 += rec
    skin = bytearray(0x30)
    skin[0:4] = b"SKIN"
    S.write_m2array(skin, S.SKIN_VERTICES, len(pts), len(skin))
    skin += struct.pack(f"<{len(pts)}H", *range(len(pts)))
    corners = [k for t in pieces.values() for k in t]
    S.write_m2array(skin, S.SKIN_INDICES, len(corners), len(skin))
    skin += struct.pack(f"<{len(corners)}H", *corners)
    S.write_m2array(skin, S.SKIN_SUBMESHES, len(pieces), len(skin))
    start = 0
    for sid, t in pieces.items():
        skin += struct.pack("<HHHHHH", sid, 0, 0, len(pts), start, len(t)) + bytes(36)
        start += len(t)
    (tmp_path / "x.m2").write_bytes(bytes(m2))
    (tmp_path / "x00.skin").write_bytes(bytes(skin))
    fills = hole_fill_ratios(str(tmp_path / "x.m2"), str(tmp_path / "x00.skin"), {1802, 1803, 2001})
    assert fills == {2001: 1.0, 1802: 0.25, 1803: 0.0}


def test_customization_rows_are_written_as_contiguous_sections():
    from retroport.character.convert import append_customization, charsections_layout_problems
    cs, hg, fh = DBCFile(10), DBCFile(6), DBCFile(8)
    cs.add_record([1, 1, 0, 0, 0, 0, 0, 1, 0, 0])
    interleaved = [{"section": s, "variation": v, "color": c, "flags": 1, "textures": ["", "", ""]}
                   for c in range(2) for v in range(2) for s in (0, 1, 3)]       # BfA's id order
    append_customization(cs, hg, fh, 22, 0, interleaved,
                         [{"VariationID": 1, "GeosetID": 2, "Showscalp": 0}, {"VariationID": 0, "GeosetID": 0, "Showscalp": 1}],
                         [])
    assert charsections_layout_problems(cs) == []
    rows = [(r[3], r[8], r[9]) for r in cs.records if r[1] == 22]
    assert rows == sorted(rows)
    assert [r[3] for r in hg.records] == [0, 1]                                    # by VariationID
    broken = DBCFile(10)
    for sec in (0, 1, 0):
        broken.add_record([len(broken.records) + 1, 22, 0, sec, 0, 0, 0, 1, 0, 0])
    assert len(charsections_layout_problems(broken)) == 1


# --------------------------------------------------------------------------- fifth client test

def _wide_skin(tmp_path, parts):
    """A skin whose submeshes are ``parts`` = [(geoset id, index count, drawn)], laid out in order
    (index values tag their submesh: i + 1), with the high start word in ``level`` like the converter."""
    from retroport.formats import structs as S
    skin = bytearray(0x30)
    skin[0:4] = b"SKIN"
    S.write_m2array(skin, S.SKIN_VERTICES, 64, len(skin))
    skin += bytes(128)
    total = sum(c for _g, c, _d in parts)
    S.write_m2array(skin, S.SKIN_INDICES, total, len(skin))
    for i, (_g, count, _d) in enumerate(parts):
        skin += struct.pack(f"<{count}H", *([i + 1] * count))
    S.write_m2array(skin, S.SKIN_SUBMESHES, len(parts), len(skin))
    start = 0
    for gid, count, _d in parts:
        skin += struct.pack("<HHHHHH", gid, start >> 16, 0, 64, start & 0xFFFF, count) + bytes(36)
        start += count
    drawn = [i for i, (_g, _c, d) in enumerate(parts) if d]
    S.write_m2array(skin, S.SKIN_BATCHES, len(drawn), len(skin))
    for i in drawn:
        rec = bytearray(24)
        struct.pack_into("<H", rec, 4, i)
        skin += rec
    p = tmp_path / "w00.skin"
    p.write_bytes(bytes(skin))
    return p


def test_wide_skins_put_switchable_geosets_below_the_16_bit_line(tmp_path):
    from retroport.convert.fixup import order_skin_indices
    # body first, an eye glow and a cape past index 65,535, an undrawn torso variant
    p = _wide_skin(tmp_path, [(0, 60000, True), (1703, 3000, True), (2202, 9000, False), (1502, 3000, True)])
    rep, = order_skin_indices([str(p)])
    assert rep["past_line"] == [] and rep["switchable"] == 6000
    data = p.read_bytes()
    n_t, t_off = struct.unpack_from("<II", data, 0x0C)
    _n, s_off = struct.unpack_from("<II", data, 0x1C)
    idx = struct.unpack_from(f"<{n_t}H", data, t_off)
    subs = [struct.unpack_from("<HHHHHH", data, s_off + i * 48) for i in range(4)]
    starts = {sid: (level << 16) | start for sid, level, _vs, _vc, start, _c in subs}
    assert starts[1703] == 0 and starts[1502] == 3000                 # switchable first, in order
    assert starts[0] == 6000 and starts[2202] == 66000                # then the body, then undrawn
    for i, (sid, level, _vs, vc, start, count) in enumerate(subs):    # each draws what it drew
        f = (level << 16) | start
        assert set(idx[f:f + count]) == {i + 1} and vc == 64
    # too much switchable geometry: what still STARTS past the line is reported (a submesh that only
    # ends past it is fine - the id the client matches on comes from its start)
    p2 = _wide_skin(tmp_path, [(0, 1000, True), (2, 40000, True), (3, 30000, True), (4, 5000, True)])
    assert order_skin_indices([str(p2)])[0]["past_line"] == [4]


def test_every_facial_style_gets_a_facial_hair_texture_row():
    from retroport.character.convert import facial_feature_sections
    rows = [{"section": 2, "variation": 0, "color": c, "flags": 1, "textures": ["a", f"b{c}", ""]} for c in (0, 1)]
    rows.append({"section": 3, "variation": 0, "color": 0, "flags": 1, "textures": ["h", "", ""]})
    out = facial_feature_sections(rows, [{"VariationID": v} for v in range(3)])
    added = sorted((r["variation"], r["color"], r["textures"][1]) for r in out if r["section"] == 2)
    assert added == [(0, 0, "b0"), (0, 1, "b1"), (1, 0, "b0"), (1, 1, "b1"), (2, 0, "b0"), (2, 1, "b1")]
    assert len(out) == 7
    # a race with no section-2 rows keeps counting its CharacterFacialHairStyles rows
    hair_only = [r for r in rows if r["section"] == 3]
    assert facial_feature_sections(hair_only, [{"VariationID": v} for v in range(3)]) == hair_only
