"""Converting CharSections textures between layouts.

Two kinds of texture:

* **composites** (CharSections section 0: the base skin and its "extra"): a whole
  layout. Every remap group's source rectangle is cut out, resized to its
  destination rectangle and pasted into a destination-sized image.
* **region textures** (faces, facial hair, scalp, underwear): one texture per section.
  On layout 2 a face texture covers the whole face group, so it is resized to the
  group's destination box and split into that group's sections (FaceUpper/FaceLower).
  Several textures for one group (e.g. a lower and an upper overlay) are layered
  first, then split.
"""

from __future__ import annotations

from PIL import Image

from .layouts import Layout, RemapGroup

_RESAMPLE = Image.Resampling.LANCZOS


def _scale(img: Image.Image, layout: Layout) -> tuple[float, float]:
    return img.width / layout.width, img.height / layout.height


def convert_composite(img: Image.Image, src: Layout, dst: Layout, groups: list[RemapGroup]) -> Image.Image:
    """Re-lay a whole-layout texture (any resolution of ``src``) into ``dst``."""
    img = img.convert("RGBA")
    sx, sy = _scale(img, src)
    out = Image.new("RGBA", (dst.width, dst.height), (0, 0, 0, 0))
    for g in groups:
        box = (round(g.src.x * sx), round(g.src.y * sy),
               round((g.src.x + g.src.w) * sx), round((g.src.y + g.src.h) * sy))
        out.paste(img.crop(box).resize((g.dst.w, g.dst.h), _RESAMPLE), (g.dst.x, g.dst.y))
    return out


def group_for(section: int, groups: list[RemapGroup]) -> RemapGroup | None:
    return next((g for g in groups if section in g.section_types), None)


def convert_region(layers: list[Image.Image], group: RemapGroup, dst: Layout) -> dict[int, Image.Image]:
    """Layer the textures of one group (bottom first), fit them to the group's destination
    box and split it into the destination sections. Returns {section type: image}."""
    box = Image.new("RGBA", (group.dst.w, group.dst.h), (0, 0, 0, 0))
    for layer in layers:
        box.alpha_composite(layer.convert("RGBA").resize((group.dst.w, group.dst.h), _RESAMPLE))
    out = {}
    for t in group.section_types:
        r = dst.sections[t]
        out[t] = box.crop((r.x - group.dst.x, r.y - group.dst.y, r.x - group.dst.x + r.w, r.y - group.dst.y + r.h))
    return out
