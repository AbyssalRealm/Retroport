"""Generate a WarcraftXL #Bug-Report-ready summary for a model + its WoWError.

For WXL testing you feed a RAW modern model to the client (WXL loads it natively);
this does NOT convert anything. It runs `check` for diagnosis and formats a compact,
high-signal report: model name, format, poly/bones, geoset groups with later-expansion
(post-WotLK) groups flagged, texture references, and the WoWError verbatim. Paste the
output straight into #Bug-Report.
"""

from __future__ import annotations

import os

from .check import check_model, _GEOSET_GROUPS
from .scan.scanner import classify_file
from .errordb import decode_error

WOTLK_TRI_CAP = 21845
# geoset groups the 3.3.5 client knows; anything else was added post-WotLK (Cata+/SL+)
_WOTLK_GROUPS = set(_GEOSET_GROUPS.keys())


def build_report(model_path: str, error_text: str = "", skin_dir: str | None = None) -> str:
    """Return a markdown bug-report summary for ``model_path`` + optional WoWError."""
    name = os.path.basename(model_path)
    lines: list[str] = [f"**Model:** `{name}`"]

    # format / version (modern-raw vs converted) from the chunk classifier
    try:
        info = classify_file(model_path)
        fmt = "modern/raw MD21" if getattr(info, "chunked", False) else "MD20 (WotLK)"
        lines.append(f"**Format:** {fmt} | version {getattr(info, 'version', '?')} | "
                     f"uses_skel={getattr(info, 'uses_skel', '?')}")
    except (OSError, ValueError) as exc:
        lines.append(f"**Format:** (could not classify: {exc})")

    # detailed check
    try:
        res = check_model(model_path, skin_dir=skin_dir)
    except (OSError, ValueError) as exc:
        lines.append(f"**check:** failed to parse ({exc})")
        return _with_error(lines, error_text)

    lines.append(f"**Bones:** {res.bones} | **Vertices:** {res.vertices} | "
                 f"**Materials:** {res.materials} | **nViews:** {res.n_views}")
    if res.combiner_combos:
        lines.append(f"[!] **Combiner flag 0x08 SET** ({res.combiner_combo_count} combos) - "
                     "batch shaders read as combo indices (black-mesh risk)")

    primary = res.skins[0] if res.skins else None
    if primary and primary.present and primary.magic_ok:
        over = primary.triangles > WOTLK_TRI_CAP
        lines.append(f"**Triangles (skin00):** {primary.triangles} / cap {WOTLK_TRI_CAP}"
                     + ("  [!] **OVER CAP**" if over else ""))
        groups = sorted({g // 100 for g in primary.geosets})
        later = [g for g in groups if g not in _WOTLK_GROUPS]
        lines.append(f"**Geoset groups:** {groups}")
        if later:
            lines.append(f"[!] **Later-expansion geoset groups not in 3.3.5: {later}** - "
                         "these were added post-WotLK (Cata+/SL+); the 3.3.5 client has no "
                         "geometry for them, a likely render/crash cause")
    elif primary and not primary.present:
        lines.append("**skin00:** not found next to the model (skins must sit beside the .m2)")

    # textures
    paths = [p for t, p in res.textures if p]
    empty_type0 = [1 for t, p in res.textures if t == 0 and not p]
    if paths:
        shown = ", ".join(f"`{p}`" for p in paths[:6])
        more = f" (+{len(paths) - 6} more)" if len(paths) > 6 else ""
        lines.append(f"**Texture paths:** {shown}{more}")
    if empty_type0:
        lines.append(f"[!] **{len(empty_type0)} hardcoded (type-0) texture(s) with no path** - "
                     "unresolved (FileDataID-only); run TXID-remove or check the listfile")

    return _with_error(lines, error_text)


def _with_error(lines: list[str], error_text: str) -> str:
    if error_text and error_text.strip():
        lines += ["", "**WoWError:**", "```", error_text.strip(), "```"]
        decoded = decode_error(error_text)
        if decoded:
            lines.append("")
            lines.append("**Likely cause(s)** (wowdev Client Error Analysis, verify against your client):")
            for addr, cause in decoded:
                lines.append(f"- `{addr}` -> {cause}")
    return "\n".join(lines)
