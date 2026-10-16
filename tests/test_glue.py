"""`glue`: BfA's character create for 3.3.5 - atlas pieces, carried race icons, scene music, how a
race package lands on the new screen, and installing a glue package."""

import json
import re
import os
import xml.etree.ElementTree as ET

import pytest

from retroport.glue import build as gb
from retroport.race import glue as raceglue
from retroport.race import install
from retroport.race.build import sha256

ATLAS = {"corner": ("Interface\\Glues\\CharacterCreate\\Retroport\\Atlas1", 0.25, 0.5, 0.0, 0.75)}


def test_atlas_pieces_resolve_to_their_texture_and_mirrored_coordinates():
    xml = ('<Texture file="@ATLAS:corner@"><TexCoords @COORDS:corner@/></Texture>'
           '<TexCoords @COORDS_H:corner@/><TexCoords @COORDS_V:corner@/><TexCoords @COORDS_HV:corner@/>')
    out = gb.fill_atlas(xml, ATLAS)
    assert 'file="Interface\\Glues\\CharacterCreate\\Retroport\\Atlas1"' in out
    coords = [dict(e.attrib) for e in ET.fromstring(f"<x>{out}</x>").iter("TexCoords")]
    as_float = [{k: float(v) for k, v in c.items()} for c in coords]
    assert as_float[0] == {"left": 0.25, "right": 0.5, "top": 0.0, "bottom": 0.75}
    assert as_float[1] == {"left": 0.5, "right": 0.25, "top": 0.0, "bottom": 0.75}      # mirrored left-right
    assert as_float[2] == {"left": 0.25, "right": 0.5, "top": 0.75, "bottom": 0.0}      # upside down
    assert as_float[3] == {"left": 0.5, "right": 0.25, "top": 0.75, "bottom": 0.0}
    assert gb.atlas_members(xml) == ["corner"]


def test_an_unknown_atlas_piece_is_refused():
    with pytest.raises(gb.GlueError):
        gb.fill_atlas('<Texture file="@ATLAS:nope@"/>', ATLAS)


def test_the_screen_template_is_xml_once_its_atlas_pieces_are_filled():
    with open(os.path.join(gb.UI, "CharacterCreate.xml"), encoding="utf-8") as fh:
        xml = fh.read()
    names = gb.atlas_members(xml)
    assert "ui-charactercreate-metal-finery-corner" in names and "alliedraces-alliancehordebanner" in names
    filled = gb.fill_atlas(xml, {n: (f"Atlas\\{n}", 0.0, 1.0, 0.0, 1.0) for n in names})
    root = ET.fromstring(filled)
    tag = "{http://www.blizzard.com/wow/ui/}"
    named = {e.get("name") for e in root.iter() if e.get("name")}
    # the frames 3.3.5's other glue files and its C++ reach by name
    assert {"CharacterCreate", "CharacterCreateNameEdit", "CharacterCreateTooltip"} <= named
    assert {f"CharacterCustomizationButtonFrame{i}" for i in range(1, 6)} <= named
    assert root.find(f"{tag}Script").get("file") == "CharacterCreate.lua"
    with open(os.path.join(gb.UI, "CharacterCreate.lua"), encoding="utf-8") as fh:
        assert raceglue.bfa_style(fh.read())


def test_nothing_shares_the_nav_buttons_overlay_with_their_edges():
    """BfA draws the Back / Finish arrows and More Info's glows on OVERLAY sublevel 1, over the nav
    template's edges; 3.3.5 has no sublevels and draws one layer in no fixed order (the Finish
    arrow came and went with the name's cursor), so they sit on a child frame, drawn over it."""
    with open(os.path.join(gb.UI, "CharacterCreate.xml"), encoding="utf-8") as fh:
        xml = fh.read()
    names = gb.atlas_members(xml)
    root = ET.fromstring(gb.fill_atlas(xml, {n: (f"Atlas\\{n}", 0.0, 1.0, 0.0, 1.0) for n in names}))
    tag = "{http://www.blizzard.com/wow/ui/}"
    buttons = [b for b in root.iter(f"{tag}Button") if b.get("inherits") == "CharCreateNavButtonTemplate"]
    assert len(buttons) >= 5
    for button in buttons:
        own = [layer.get("level") for layers in button.findall(f"{tag}Layers") for layer in layers]
        assert "OVERLAY" not in own, button.get("name")
    front = {e.get("name") for b in buttons for f in b.iter(f"{tag}Frame") for e in f.iter() if e.get("name")}
    assert {"CharCreateOkayButtonArrow", "CharCreateBackButtonArrow", "CharCreateMoreInfoButtonTopGlow",
            "CharCreateMoreInfoButtonBottomGlow", "CharCreateOkayButtonTopGlow", "CharCreateOkayButtonBottomGlow"} <= front


def test_the_select_screen_keeps_what_the_rest_of_glue_and_the_client_reach():
    with open(os.path.join(gb.UI, "CharacterSelect.xml"), encoding="utf-8") as fh:
        xml = fh.read()
    names = gb.atlas_members(xml)
    assert {"characterselection_alliance_icon", "characterselection_horde_icon", "services-icon-goldborder"} <= set(names)
    root = ET.fromstring(gb.fill_atlas(xml, {n: (f"Atlas\\{n}", 0.0, 1.0, 0.0, 1.0) for n in names}))
    named = {e.get("name") for e in root.iter() if e.get("name")}
    # AddonList, GlueParent and GlueDialog reach these; the client the model frame; the dialogs are 3.3.5's
    assert {"CharacterSelect", "CharacterSelectAddonsButton", "CharacterSelectRealmSplitButton", "RealmSplitCurrentChoice",
            "RealmSplitPending", "CharacterDeleteDialog", "CharacterDeleteEditBox", "CharacterRenameDialog",
            "CharSelectCharacterName", "CharSelectRealmName", "CharacterSelectLogo", "CharSelectCreateCharacterButton",
            "GameRoomBillingFrame", "CharSelectEnterWorldButton"} <= named
    assert {f"CharSelectCharacterButton{i}" for i in range(1, 11)} <= named
    assert {f"CharSelectPaidService{i}" for i in range(1, 11)} <= named
    with open(os.path.join(gb.UI, "CharacterSelect.lua"), encoding="utf-8") as fh:
        lua = fh.read()
    for fn in ("CharacterSelect_ChangeRealm", "RealmSplit_SetChoiceText", "UpdateCharacterList", "CharacterSelect_SelectCharacter",
               "CharacterSelect_PaidServiceOnClick", "CharacterDeleteDialog_OnShow"):
        assert f"function {fn}(" in lua
    assert "CHARACTER_ROTATION_CONSTANT = 0.6;" in lua


CREATE_ONLY = re.compile(r"\b(GetAvailableRaces|GetAvailableClasses|GetFactionForRace|GetNameForRace|GetSelected(?:Race|Class|Sex)"
                         r"|SetSelected(?:Race|Class|Sex)|GetCreateBackgroundModel|IsRaceClassValid|GetHairCustomization"
                         r"|GetFacialHairCustomization|CycleCharCustomization|RandomizeCharCustomization|ResetCharCustomize"
                         r"|[GS]etCharacterCreateFacing|CreateCharacter|CustomizeExistingCharacter|PaidChange_\w+"
                         r"|UpdateCustomizationScene)\s*\(")


def test_the_select_screen_never_asks_character_create():
    # before character create has been set up its functions read a character that isn't there:
    # GetAvailableRaces on character select was error #132 at 0x4E18CF, past any pcall
    for name in ("CharacterSelect.lua", "CharacterSelect.xml"):
        with open(os.path.join(gb.UI, name), encoding="utf-8") as fh:
            code = "\n".join(line.split("--", 1)[0] for line in fh.read().splitlines())
        assert not CREATE_ONLY.findall(code), f"{name} calls {CREATE_ONLY.findall(code)}"


def test_the_race_icons_of_the_installed_screen_are_carried_over():
    client = ("MAX_RACES = 11;\n"
              "-- [retroport] begin icons KULTIRAN\nRACE_ICON_TCOORDS[\"KULTIRAN_MALE\"] = {0.625, 0.75, 0, 0.25};\n"
              "-- [retroport] end icons KULTIRAN\n"
              "-- [retroport] begin race layout\nfunction CharacterCreate_LayoutRaceButtons() end\n"
              "-- [retroport] end race layout\n")
    out, carried = gb.carry_race_icons("RETROPORT_GLUE_STYLE = \"bfa\";\n", client)
    assert carried == ["icons KULTIRAN"]
    assert 'RACE_ICON_TCOORDS["KULTIRAN_MALE"]' in out
    assert "LayoutRaceButtons" not in out                 # 3.3.5's layout stays behind


def test_the_scenes_get_music_and_bfas_lighting_once():
    lua = ('CharModelFogInfo = { };\nCharModelFogInfo["SCOURGE"] = { r=0, g=0.22, b=0.22, far=26 };\n'
           'CharModelGlowInfo = { };\nGlueAmbienceTracks = { };\nGlueAmbienceTracks["HUMAN"] = "GlueScreenHuman";\n'
           'RaceLights = { HUMAN = { {1, 0, 0, 0, -1, 1, 0.27, 0.27, 0.27, 1, 0, 0, 0} } };\n')
    once = gb.patch_glue_parent(lua)
    assert 'GlueAmbienceTracks["ALLIANCE"] = GlueAmbienceTracks["ALLIANCE"] or GlueAmbienceTracks["HUMAN"];' in once
    assert 'GlueAmbienceTracks["HORDE"]' in once
    # 3.3.5 stood a Gnome in the Dwarf's scene, to its music; BfA gives it its own scene
    assert 'GlueAmbienceTracks["GNOME"] = GlueAmbienceTracks["GNOME"] or GlueAmbienceTracks["DWARF"];' in once
    # BfA's select scenes light themselves: 3.3.5's tables for its own scenes go, BfA's fog stays
    lighting = once[once.index("begin bfa select lighting"):]
    assert '"HUMAN"' in lighting and '"BLOODELF"' in lighting and "RaceLights[race]" in lighting
    assert 'CharModelFogInfo["SCOURGE"] = { r = 0, g = 0.22, b = 0.22, far = 18 };' in lighting
    assert lighting.index("RaceLights[race]") < lighting.index('CharModelFogInfo["SCOURGE"]')
    assert gb.patch_glue_parent(once) == once
    with pytest.raises(gb.GlueError):
        gb.patch_glue_parent("-- not GlueParent\n")


def test_bfa_race_icons_are_keyed_the_way_the_screen_names_races():
    assert gb.race_icon_key("raceicon-human-male") == "HUMAN_MALE"
    assert gb.race_icon_key("RaceIcon-KulTiran-Female") == "KULTIRAN_FEMALE"
    assert gb.race_icon_key("raceicon-undead-female") == "SCOURGE_FEMALE"           # ClientFileString
    assert gb.race_icon_key("raceicon-highmountain-male") == "HIGHMOUNTAINTAUREN_MALE"
    assert gb.race_icon_key("raceicon128-human-male") is None
    assert gb.race_icon_key("raceicon-human-male-disabled") is None


def test_the_race_icon_atlas_upscales_each_icon_into_its_own_cell(tmp_path):
    from PIL import Image
    from retroport.formats import blp
    icons = {f"RACE{i}_MALE": Image.new("RGBA", (64, 64), (i * 20, 255 - i * 20, 7, 255)) for i in range(10)}
    sheet, coords = gb.race_icon_atlas(icons)
    assert sheet.size == (gb.ICON_CELL * gb.ICON_COLUMNS, 256)          # two rows, a power of two tall
    left, right, top, bottom = coords["RACE9_MALE"]                      # the tenth: row 1, column 1
    assert (left, right, top, bottom) == (0.125, 0.25, 0.5, 1.0)
    cx, cy = int((left + right) / 2 * sheet.width), int((top + bottom) / 2 * sheet.height)
    assert sheet.getpixel((cx, cy)) == (180, 75, 7, 255)
    lua = gb.lua_race_icons("Interface\\Glues\\X", coords)
    assert lua.startswith('RACE_ICON_ATLAS_FILE = "Interface\\\\Glues\\\\X";')
    assert 'RACE_ICON_ATLAS["RACE9_MALE"] = {0.125000, 0.250000, 0.500000, 1.000000};' in lua
    # written uncompressed, it reads back exactly
    path = tmp_path / "icons.blp"
    path.write_bytes(blp.encode_bgra(sheet))
    h = blp.header(path.read_bytes())
    assert (h["encoding"], h["alpha_depth"], h["width"], h["height"]) == (3, 8, 1024, 256)
    blp.verify_blp(str(path))
    assert blp.decode(str(path)).getpixel((cx, cy)) == (180, 75, 7, 255)


def test_a_block_is_replaced_where_it_stands():
    text = raceglue.put_block("x = 1;\n", "first", "a = 1;")
    text = raceglue.put_block(text, "second", "b = 2;")
    again = raceglue.put_block(text, "first", "a = 3;")
    assert again.index("begin first") < again.index("begin second")      # not moved to the end
    assert "a = 3;" in again and "a = 1;" not in again
    assert raceglue.put_block(again, "first", "a = 3;") == again


def test_a_race_on_the_bfa_screen_adds_its_icons_and_nothing_else():
    lua = 'RETROPORT_GLUE_STYLE = "bfa";\nMAX_RACES = 32;\n'
    icons = {"KULTIRAN_MALE": (0.625, 0.75, 0, 0.25), "KULTIRAN_FEMALE": (0.625, 0.75, 0.5, 0.75)}
    out = raceglue.patch_character_create_lua(lua, 12, icons, "KULTIRAN")
    assert 'RACE_ICON_TCOORDS["KULTIRAN_FEMALE"]' in out
    assert "race layout" not in out and "MAX_RACES = 32;" in out
    xml = '<CheckButton name="CharCreateRaceButtonTemplate" virtual="true"/>'
    assert raceglue.patch_character_create_xml(xml, 12) == xml
    # and the stock screen still gets 3.3.5's layout
    stock = raceglue.patch_character_create_lua("MAX_RACES = 10;\n", 12, icons, "KULTIRAN")
    assert "MAX_RACES = 12;" in stock and "race layout" in stock


class _FakeClient:
    files: dict = {}

    def __init__(self, _client_dir, top=None):
        pass

    def find(self, rel):
        data = self.files.get(rel.replace("/", "\\"))
        return ("patch-H (folder)", data) if data is not None else None

    def shadowed_by(self, rel):
        return None


def test_a_glue_package_owns_its_folders(tmp_path, monkeypatch):
    root = tmp_path / "wow" / "Data" / "patch-H.MPQ"
    owned = root / "Interface" / "Glues" / "Models" / "UI_Alliance"
    owned.mkdir(parents=True)
    (owned / "UI_Alliance.m2").write_bytes(b"scene v1")
    (owned / "old_texture.blp").write_bytes(b"left over")
    glue_xml = root / "Interface" / "GlueXML"
    glue_xml.mkdir(parents=True)
    (glue_xml / "CharacterCreate.lua").write_bytes(b"race-patched screen")
    other = root / "Interface" / "Glues" / "Common"
    other.mkdir(parents=True)
    (other / "Shared.blp").write_bytes(b"someone else's")

    pkg = tmp_path / "glue"
    for rel, data in (("Interface/Glues/Models/UI_Alliance/UI_Alliance.m2", b"scene v2"),
                      ("Interface/GlueXML/CharacterCreate.lua", b"bfa screen"),
                      ("Interface/Glues/Common/Shared.blp", b"ours")):
        p = pkg / "client" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (pkg / "package.json").write_text(json.dumps({
        "name": "BfA character create and select",
        "bases": {"client/Interface/GlueXML/CharacterCreate.lua": {"source": "patch-H", "sha256": sha256(b"race-patched screen")}},
        "owns": ["Interface\\Glues\\Models\\UI_Alliance\\"]}))
    _FakeClient.files = {"Interface\\GlueXML\\CharacterCreate.lua": b"race-patched screen"}
    monkeypatch.setattr(install, "ClientData", _FakeClient)

    plan = install.plan_install(str(pkg), str(tmp_path / "wow"), clean=True)
    kinds = {a.rel: (a.kind, a.detail) for a in plan.actions}
    assert kinds["client/Interface/Glues/Models/UI_Alliance/UI_Alliance.m2"] == ("replace", "package file")
    assert kinds["client/Interface/GlueXML/CharacterCreate.lua"] == ("replace", "built on this copy")
    assert kinds["client/Interface/Glues/Common/Shared.blp"][0] == "conflict"       # not its folder
    assert kinds["client/Interface/Glues/Models/UI_Alliance/old_texture.blp"][0] == "leftover"
    assert plan.race == "BfA character create and select"


def test_a_folder_that_is_no_package_is_refused(tmp_path):
    with pytest.raises(ValueError):
        install.plan_install(str(tmp_path), None)
