"""Resize/re-encode modern textures for the 3.3.5a client.

Two kinds of texture, treated differently:

* **Armor component textures** (Item\\TextureComponents\\<Section>Texture\\*_U/_M/_F.blp)
  are composited by the client onto the character's body skin, into a fixed region
  per body section. They are resized to that region exactly and written the way
  Blizzard (and the 3.3.5 HD patch) ship them: palettized, 8-bit alpha when needed,
  full mips. Region sizes are for the base body texture the client uses:

      classic 3.3.5 models - 256x256 skin:  ArmUpper/ArmLower/TorsoUpper/LegUpper/LegLower
                                            128x64, Hand/TorsoLower/Foot 128x32
      HD models (e.g. the 3.3.5 HD patch) - 512x512 skin: every region doubled

  (Checked against the HD patch: 512x512 body skins, 256x128 faces / pelvis.)

* **Object textures** (weapons, helms, shoulders, capes...) are free-sized; they're
  only shrunk if over ``max_object`` (both sides stay powers of two) and re-encoded
  DXT1/DXT5. Anything already small and client-compatible is copied unchanged.
"""

from __future__ import annotations

import os
import re
import shutil

from pydantic import BaseModel

from .formats import blp
from .safeio import write_verified

CLASSIC_REGIONS = {
    "ArmUpper": (128, 64), "ArmLower": (128, 64), "Hand": (128, 32), "TorsoUpper": (128, 64),
    "TorsoLower": (128, 32), "LegUpper": (128, 64), "LegLower": (128, 64), "Foot": (128, 32),
}
TARGET_SCALE = {"classic": 1, "hd": 2}
SECTION_CODES = {"au": "ArmUpper", "al": "ArmLower", "ha": "Hand", "tu": "TorsoUpper",
                 "tl": "TorsoLower", "lu": "LegUpper", "ll": "LegLower", "fo": "Foot"}
_CODE_RE = re.compile(r"_(au|al|ha|tu|tl|lu|ll|fo)(?:_([umf]))?$", re.I)
_GENDER_RE = re.compile(r"_([umf])$", re.I)


class TextureResult(BaseModel):
    source: str
    output: str
    kind: str                         # "component" | "object"
    section: str | None = None
    size: tuple[int, int]
    source_size: tuple[int, int]
    format: str                       # "palettized", "DXT1", "DXT5", "unchanged"
    dbc_name: str | None = None       # component name for ItemDisplayInfo (no _U/_M/_F)
    warnings: list[str] = []


def region_size(section: str, target: str = "classic") -> tuple[int, int]:
    if section not in CLASSIC_REGIONS:
        raise ValueError(f"unknown section '{section}' - use one of {', '.join(CLASSIC_REGIONS)}")
    if target not in TARGET_SCALE:
        raise ValueError(f"target must be 'classic' or 'hd', got '{target}'")
    w, h = CLASSIC_REGIONS[section]
    return w * TARGET_SCALE[target], h * TARGET_SCALE[target]


def detect_section(path: str) -> str | None:
    """Body section from the <Section>Texture folder, else the _tu/_lu... name code."""
    folder = os.path.basename(os.path.dirname(os.path.abspath(path))).lower()
    for section in CLASSIC_REGIONS:
        if folder == f"{section.lower()}texture":
            return section
    m = _CODE_RE.search(os.path.splitext(os.path.basename(path))[0])
    return SECTION_CODES[m.group(1).lower()] if m else None


def component_names(path: str) -> tuple[str, str]:
    """(file stem to write, bare ItemDisplayInfo name). A gender-less file gets _U
    (unisex), which is how the client looks up a name with no suffix."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _GENDER_RE.search(stem)
    if m:
        return stem, stem[:m.start()]
    return stem + "_U", stem


def _pow2_floor(n: int) -> int:
    return 1 << (max(1, n).bit_length() - 1)


def convert_component(src: str, out_root: str, section: str | None = None,
                      target: str = "classic") -> TextureResult:
    section = section or detect_section(src)
    if not section:
        raise ValueError(f"{os.path.basename(src)}: can't tell which body section this is - "
                         "pass the section (ArmUpper, TorsoUpper, ...)")
    size = region_size(section, target)
    img = blp.decode(src)
    warnings = []
    if img.width * size[1] != img.height * size[0]:
        warnings.append(f"aspect {img.width}x{img.height} differs from the {section} region "
                        f"{size[0]}x{size[1]} - stretched; check the layout in game")
    stem, dbc_name = component_names(src)
    out = os.path.join(out_root, "Item", "TextureComponents", f"{section}Texture", stem + ".blp")
    data = blp.encode_palettized(img.resize(size, blp.Image.Resampling.LANCZOS))
    write_verified(out, data, lambda p: blp.verify_blp(p, size))
    return TextureResult(source=src, output=out, kind="component", section=section, size=size,
                         source_size=img.size, format="palettized", dbc_name=dbc_name, warnings=warnings)


def convert_object(src: str, out_dir: str, max_object: int | None = None) -> TextureResult:
    img = blp.decode(src)
    w, h = img.size
    tw, th = _pow2_floor(w), _pow2_floor(h)
    if max_object:
        while max(tw, th) > max_object:
            tw, th = max(1, tw // 2), max(1, th // 2)
    out = os.path.join(out_dir, os.path.basename(src))
    warnings = []
    if (w, h) != (_pow2_floor(w), _pow2_floor(h)):
        warnings.append(f"{w}x{h} is not power-of-two; resized down to {tw}x{th}")
    if (tw, th) == (w, h) and _client_compatible(src):
        os.makedirs(out_dir, exist_ok=True)
        if os.path.abspath(src) != os.path.abspath(out):
            shutil.copy2(src, out)
        return TextureResult(source=src, output=out, kind="object", size=(w, h), source_size=(w, h),
                             format="unchanged", warnings=warnings)
    small = img.resize((tw, th), blp.Image.Resampling.LANCZOS)
    alpha = blp.has_alpha(small)
    write_verified(out, blp.encode_dxt(small, alpha), lambda p: blp.verify_blp(p, (tw, th)))
    return TextureResult(source=src, output=out, kind="object", size=(tw, th), source_size=(w, h),
                         format="DXT5" if alpha else "DXT1", warnings=warnings)


def _client_compatible(path: str) -> bool:
    """BLP2 palettized / DXT1/3/5 / BGRA with a mip chain - what the 3.3.5 client reads."""
    try:
        with open(path, "rb") as fh:
            h = blp.header(fh.read(blp.DATA_START))
    except (OSError, blp.BLPError):
        return False
    if h["encoding"] == 2:
        return h["alpha_encoding"] in (0, 1, 7) and h["has_mips"] > 0
    return h["encoding"] in (1, 3) and h["has_mips"] > 0


def convert_textures(paths: list[str], out_dir: str, target: str = "classic",
                     max_object: int | None = None, section: str | None = None) -> list[TextureResult]:
    """Convert every .blp in ``paths`` (files or folders): components into
    <out>/Item/TextureComponents/<Section>Texture/, objects flat into <out>."""
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".blp"))
        else:
            files.append(p)
    results = []
    for f in files:
        sec = section or detect_section(f)
        results.append(convert_component(f, out_dir, sec, target) if sec else convert_object(f, out_dir, max_object))
    return results
