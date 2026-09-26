"""Decode WoW client crash addresses to likely causes.

A curated lookup from wowdev's Client Error Analysis (Win32 3.3.5a), narrowed to the
errors that matter for model/character porting - M2 tracks, .skin bones, particles,
shaders, textures, CharSections, CreatureDisplayInfo. Given a WoWError, it extracts the
crash address and returns the known cause, so a bug report says *why* it crashed, not
just that it did.

Source: https://wowdev.wiki/Client_Error_Analysis (verify against your exact client build).
"""

from __future__ import annotations

import re

# normalized 8-hex address -> likely cause (Win32 3.3.5a instruction addresses)
_KNOWN: dict[str, str] = {
    "006844e8": "empty M2 keyBoneLookup table",
    "00684527": ".skin bone/keybone issue - bad bone structure or indices; e.g. a parent bone with a "
                "higher index than its child, or the root bone not at index 0 (root should be 0). May "
                "show as model 'jitter' before crashing",
    "0068453d": ".skin submesh nBones (bone count) too high",
    "006a4d91": "too many timestamps in the transparency block, or a bad shader in the M2",
    "0081f57d": "M2 missing a default TexAnimLookup (Texture Transforms lookup table)",
    "00821edc": "M2 references a non-existent transparency lookup table entry",
    "00821ee9": "M2 transparency lookup references a non-existent transparency block",
    "0082639c": "M2 has nAnimations = 0",
    "008285eb": "bad offsets/values inside an M2Track",
    "0082874b": "issue within M2AnimateTrack",
    "00829d11": "M2 bone lookup table missing or has invalid ids",
    "0082b119": "bad offsets/values inside an M2Track",
    "0082b2be": "two attachment points share offsetToTimeStampPairs (often a copied particle emitter)",
    "008310ac": "M2 event track timestamps (ProcessEventCallbacks)",
    "00832cf0": "M2 animation error - nAnimations too high (a static item with hundreds of anims)",
    "00833bde": "particle EMITTER_TYPE set to an unknown type (WotLK knows only 1, 2, 3)",
    "00833beb": "M2 particles",
    "00833bf5": "a particle block was duplicated at EOF (copied emitter with overlapping data)",
    "00834816": ".skin submesh or texture unit",
    "00836d11": "bad shader type (WotLK supports only 0, 1, 2, 3)",
    "008369e7": "Texture_unit_lookup_table not present in the M2",
    "0083893d": ".skin related",
    "0087307d": "invalid/non-existent shader (bad vertexPermute or missing effect)",
    "006dc86a": "unknown DisplayInfo for the race/sex",
    "0072da70": "unknown CreatureModelData.ID - CreatureDisplayInfo points to a model id not in CreatureModelData.dbc",
    "0072dac8": "CreatureModelData.dbc error",
    "0082c7c9": "CreatureDisplayInfoExtra has an empty BakeName column",
    "77664584": "CCharacterComponent::RenderPrepSections - character section (CharSections) rendering",
    "004dfedd": "CharacterCreation: race/class/gender selection object missing (often a CharSectionsRec issue "
                "or a non-existent/hardcoded race - only reusing existing races works)",
    "004e0a9b": "CharacterCreation: race/class/gender selection object does not exist",
    "004e4191": "CharacterCreation: SelectCharacter (GlueXML) - selection object issue",
    "776b2a83": "MAX_RACES (charactercreate.lua) does not match the button count in charactercreate.xml",
}

_HEX = re.compile(r"[0-9a-fA-F]{6,8}")


def decode_error(error_text: str) -> list[tuple[str, str]]:
    """Return [(0xADDRESS, cause)] for known crash addresses found in the error text."""
    seen: dict[str, str] = {}
    for m in _HEX.finditer(error_text or ""):
        addr = m.group(0).lower().lstrip("0").zfill(8)
        if addr in _KNOWN and addr not in seen:
            seen[addr] = _KNOWN[addr]
    return [(f"0x{a}", c) for a, c in seen.items()]
