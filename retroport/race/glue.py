"""Patch the 3.3.5a character-create GlueXML for extra races.

Changes are additive and re-runnable: everything retroport adds sits between
``-- [retroport] begin <tag>`` / ``-- [retroport] end <tag>`` lines, and a rebuild
replaces blocks with the same tag instead of adding another copy.

* CharacterCreate.lua - ``MAX_RACES`` raised to the playable-race count; icon
  coordinates for the new race; the race buttons laid out by faction at runtime
  (Alliance left, Horde right, rows tightened when a column holds more than five), so the
  layout is right whatever order the client lists races in; faction-specific racial
  text (``ABILITY_INFO_<RACE>_<FACTION><n>``) for races that exist on both sides.
* CharacterCreate.xml - ``CharacterCreateRaceButton<n>`` up to ``MAX_RACES`` (the
  client indexes them directly; a missing one is a crash).
* GlueParent.lua - races without their own create/select scene borrow another's.
* GlueStrings.lua - description, ability and "disabled" strings.
"""

from __future__ import annotations

import re

BEGIN, END = "-- [retroport] begin {}", "-- [retroport] end {}"

_MAX_RACES = re.compile(r"^(\s*MAX_RACES\s*=\s*)(\d+)(\s*;?)", re.M)
_BUTTON = re.compile(r'name="CharacterCreateRaceButton(\d+)"')
_ICON = re.compile(r'\["(\w+)"\]\s*=\s*\{\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\}')

LAYOUT_LUA = """
-- Lay the race buttons out by faction (Alliance left, Horde right) whatever order the
-- client lists races in, and tighten the rows when a column holds more than five.
function CharacterCreate_LayoutRaceButtons()
	local columns = { Alliance = {}, Horde = {} };
	for i = 1, CharacterCreate.numRaces do
		local _, faction = GetFactionForRace(i);
		table.insert(columns[faction] or columns["Horde"], i);
	end
	local rows = math.max(#columns["Alliance"], #columns["Horde"]);
	local top, gap = -61, 21;
	if ( rows > 5 ) then
		top = -58;
		gap = math.max(2, math.floor((313 - rows * 38) / (rows - 1)));
	end
	for faction, x in pairs({ Alliance = -50, Horde = 50 }) do
		for row, index in ipairs(columns[faction]) do
			local button = _G["CharacterCreateRaceButton"..index];
			button:ClearAllPoints();
			button:SetPoint("TOP", button:GetParent(), "TOP", x, top - (row - 1) * (38 + gap));
		end
	end
end

local retroport_EnumerateRaces = CharacterCreateEnumerateRaces;
function CharacterCreateEnumerateRaces(...)
	retroport_EnumerateRaces(...);
	CharacterCreate_LayoutRaceButtons();
end

-- A race on both sides shares one ClientFileString; its racial text can differ by faction.
local retroport_SetCharacterRace = SetCharacterRace;
function SetCharacterRace(id)
	retroport_SetCharacterRace(id);
	local _, faction = GetFactionForRace(CharacterCreate.selectedRace);
	local _, fileString = GetNameForRace();
	local key = "ABILITY_INFO_"..strupper(fileString or "").."_"..strupper(faction or "");
	if ( _G[key.."1"] ) then
		local text, i = "", 1;
		while ( _G[key..i] ) do
			text = text.._G[key..i].."\\n\\n";
			i = i + 1;
		end
		CharacterCreateRaceAbilityText:SetText(text);
	end
end
"""

BACKGROUND_LUA = """
-- Races without their own create/select scene borrow another race's (see GLUE_BACKGROUND_ALIASES).
GLUE_BACKGROUND_ALIASES = GLUE_BACKGROUND_ALIASES or {};
local retroport_SetBackgroundModel = SetBackgroundModel;
function SetBackgroundModel(model, name)
	local alias = name and GLUE_BACKGROUND_ALIASES[strupper(name)];
	retroport_SetBackgroundModel(model, alias or name);
end
"""


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def strip_block(text: str, tag: str) -> str:
    pat = re.compile(r"[ \t]*" + re.escape(BEGIN.format(tag)) + r".*?" + re.escape(END.format(tag)) + r"[ \t]*\r?\n?",
                     re.S)
    return pat.sub("", text)


def put_block(text: str, tag: str, body: str, *, after: re.Pattern | None = None) -> str:
    """Replace (or add) the tagged block: after the first ``after`` match, else at the end."""
    text = strip_block(text, tag)
    eol = _eol(text)
    block = eol.join([BEGIN.format(tag), *body.strip("\n").split("\n"), END.format(tag)]) + eol
    if after is not None:
        m = after.search(text)
        if not m:
            raise ValueError(f"anchor for block '{tag}' not found")
        end = text.index("\n", m.end()) + 1
        return text[:end] + block + text[end:]
    if not text.endswith(("\n", "\r")):
        text += eol
    return text + block


def lua_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n") + '"'


# --------------------------------------------------------------------------- CharacterCreate.lua

def icon_coords(lua: str) -> dict[str, tuple[float, float, float, float]]:
    m = re.search(r"RACE_ICON_TCOORDS\s*=\s*\{(.*?)\n\s*\};", lua, re.S)
    body = m.group(1) if m else ""
    found = {k: tuple(float(v) for v in vals) for k, *vals in _ICON.findall(body)}
    for k, *vals in _ICON.findall("\n".join(re.findall(r'RACE_ICON_TCOORDS(\["\w+"\]\s*=\s*\{[^}]*\})', lua))):
        found[k] = tuple(float(v) for v in vals)
    return found


def patch_character_create_lua(lua: str, max_races: int, icons: dict[str, tuple[float, float, float, float]],
                               tag: str) -> str:
    m = _MAX_RACES.search(lua)
    if not m:
        raise ValueError("MAX_RACES not found in CharacterCreate.lua")
    if int(m.group(2)) < max_races:
        lua = lua[:m.start()] + f"{m.group(1)}{max_races}{m.group(3)}" + lua[m.end():]
    lines = [f'RACE_ICON_TCOORDS["{k}"] = {{{", ".join(f"{v:g}" for v in c)}}};' for k, c in icons.items()]
    lua = put_block(lua, f"icons {tag}", "\n".join(lines))
    return put_block(lua, "race layout", LAYOUT_LUA)


# --------------------------------------------------------------------------- CharacterCreate.xml

def patch_character_create_xml(xml: str, max_races: int) -> str:
    buttons = sorted(int(n) for n in set(_BUTTON.findall(xml)))
    if not buttons:
        raise ValueError("no CharacterCreateRaceButton<n> in CharacterCreate.xml")
    highest = max(buttons)
    if highest >= max_races:
        return xml
    m = re.search(r'([ \t]*)<CheckButton name="CharacterCreateRaceButton%d".*?</CheckButton>\r?\n' % highest, xml, re.S)
    if not m:
        raise ValueError(f"CharacterCreateRaceButton{highest} block not found")
    ind, eol = m.group(1), _eol(xml)
    add = "".join(
        f'{ind}<CheckButton name="CharacterCreateRaceButton{n}" inherits="CharacterCreateRaceButtonTemplate" id="{n}">{eol}'
        f'{ind}\t<Anchors>{eol}'
        f'{ind}\t\t<Anchor point="TOP" relativePoint="TOP" x="0" y="-61"/>{eol}'   # CharacterCreate_LayoutRaceButtons places it
        f'{ind}\t</Anchors>{eol}'
        f'{ind}</CheckButton>{eol}' for n in range(highest + 1, max_races + 1))
    return xml[:m.end()] + add + xml[m.end():]


# --------------------------------------------------------------------------- GlueParent.lua / GlueStrings.lua

def patch_glue_parent(lua: str, aliases: dict[str, str], tag: str) -> str:
    if "function SetBackgroundModel" not in lua:
        raise ValueError("SetBackgroundModel not found in GlueParent.lua")
    lua = put_block(lua, "background aliases", BACKGROUND_LUA)
    lines = [f'GLUE_BACKGROUND_ALIASES["{k.upper()}"] = {lua_string(v)};' for k, v in aliases.items()]
    return put_block(lua, f"background {tag}", "\n".join(lines))


def read_strings(lua: str, prefix: str) -> dict[str, str]:
    """Simple ``NAME = "text";`` assignments whose name starts with ``prefix``."""
    out = {}
    for m in re.finditer(r'^(%s\w*)\s*=\s*"((?:[^"\\]|\\.)*)";' % re.escape(prefix), lua, re.M):
        out[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    return out


def patch_glue_strings(lua: str, strings: dict[str, str], tag: str) -> str:
    return put_block(lua, f"strings {tag}", "\n".join(f"{k} = {lua_string(v)};" for k, v in strings.items()))
