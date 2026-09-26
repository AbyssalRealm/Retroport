"""Texture-composition report for a character/creature model.

Cross-references the M2's texture list (each has a *type*) with the wow.export manifest
so you can see, per texture, whether the client sources it from the MODEL (hardcoded,
type 0) or fills it at RUNTIME - and for characters, specifically from CharSections.dbc
(body/hair/skin). This is the tool for the "why is my ported character mis-textured"
question: the runtime textures are supposed to be empty in the M2, and when you overwrite
a race, the client fills them from THAT race's CharSections.
"""

from __future__ import annotations

import json
import os

from .check import analyze_m2, _TEXTURE_TYPES
from .convert.loaders import find_manifest

# M2Texture.type buckets
_CHARSECTIONS = {1: "body/skin", 6: "hair", 8: "skin extra"}   # filled from CharSections.dbc
_EQUIP = {2: "object/cape"}                                     # filled from equipment display
_MONSTER = {11: "monster1", 12: "monster2", 13: "monster3"}     # filled from CreatureDisplayInfo


def _manifest_textures(manifest_path: str | None) -> list[str]:
    """Return the wow.export manifest's texture files, in order (best-effort)."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return []
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for entry in data.get("textures", []) or []:
        if isinstance(entry, dict):
            fdid = entry.get("fileDataID", "?")
            f = entry.get("file", "?")
            out.append(f"{f} (fdid {fdid})")
    return out


def build_texture_report(model_path: str, manifest_path: str | None = None) -> str:
    res = analyze_m2(model_path)
    man = _manifest_textures(manifest_path or find_manifest(model_path))

    lines = [f"**Texture composition - `{os.path.basename(model_path)}`**",
             f"{len(res.textures)} texture slot(s)"
             + (f" | manifest lists {len(man)}" if man else " | no manifest found"), ""]

    n_charsec = n_equip = n_hardcoded = n_empty0 = 0
    for i, (ttype, path) in enumerate(res.textures):
        role = _TEXTURE_TYPES.get(ttype, "?")
        if ttype == 0:
            if path:
                src = f"IN-MODEL path -> `{path}`"
                n_hardcoded += 1
            else:
                src = "IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)"
                n_empty0 += 1
        elif ttype in _CHARSECTIONS:
            src = f"RUNTIME -> CharSections.dbc ({_CHARSECTIONS[ttype]})"
            n_charsec += 1
        elif ttype in _EQUIP:
            src = "RUNTIME -> equipment display"
            n_equip += 1
        elif ttype in _MONSTER:
            src = "RUNTIME -> CreatureDisplayInfo (creature skin)"
        else:
            src = "RUNTIME -> client-filled"
        extra = f"   |   wow.export[{i}]: {man[i]}" if i < len(man) else ""
        lines.append(f"[{i}] type {ttype} ({role}) -> {src}{extra}")

    lines.append("")
    lines.append(f"**Summary:** {n_hardcoded} in-model | {n_charsec} CharSections (body/hair/skin) | "
                 f"{n_equip} equipment | {n_empty0} unresolved type-0")

    if n_charsec:
        lines += [
            "",
            "[!] **This model has CharSections-driven textures** (empty in the M2 by design). If you "
            "port it by overwriting a race, the client fills these from THAT race's CharSections - so "
            "the mesh renders with the overwritten race's skin, not this model's. To get correct "
            "textures you must either (a) replace the overwritten race's skin BLPs with this model's, "
            "laid out to the WotLK CharSections regions, or (b) bake one body texture and hardcode it "
            "as a type-0 path in the M2. TXID-remove does NOT fix these - they're runtime, not in-model.",
        ]
    return "\n".join(lines)
