"""Character texture layouts and the remap between them.

A character's body texture is a composite: the client pastes each CharSections
texture into a fixed rectangle ("section") of one texture, and the model's UVs
sample those rectangles. The rectangles differ between layouts:

* 3.3.5 classic - 256x256; HD clients (the 3.3.5 HD patch) use the same layout at
  512x512, which is exactly BfA's layout 1.
* BfA layout 2 (1024x512, every allied race, Goblin, Worgen, Pandaren) keeps layout
  1's body sections at the same pixel positions in the left half and gives the face
  the whole right half (FaceUpper and FaceLower share one 512x512 rectangle).
  Shadowlands+ models sample the face from the same right half.

The remap moves every point of a source section into the matching destination
section. Source sections that share a rectangle (layout 2's face) are mapped onto
the bounding box of their destination sections, so a single face texture is
squeezed into 3.3.5's FaceUpper+FaceLower block and then split. The same mapping is
applied to model UVs and to textures, which keeps every feature aligned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SECTION_NAMES = {0: "ArmUpper", 1: "ArmLower", 2: "Hand", 3: "TorsoUpper", 4: "TorsoLower",
                 5: "LegUpper", 6: "LegLower", 7: "Foot", 8: "Accessory", 9: "FaceUpper", 10: "FaceLower"}


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float, layout_w: int, layout_h: int) -> bool:
        """Half-open, except on the layout's own right/bottom edge."""
        right = px < self.x + self.w or (self.x + self.w == layout_w and px <= layout_w)
        bottom = py < self.y + self.h or (self.y + self.h == layout_h and py <= layout_h)
        return self.x <= px and self.y <= py and right and bottom

    def scaled(self, f: float) -> "Rect":
        return Rect(round(self.x * f), round(self.y * f), round(self.w * f), round(self.h * f))


@dataclass
class Layout:
    width: int
    height: int
    sections: dict[int, Rect] = field(default_factory=dict)

    def scaled(self, f: float) -> "Layout":
        return Layout(round(self.width * f), round(self.height * f),
                      {t: r.scaled(f) for t, r in self.sections.items()})


# Blizzard's 3.3.5 layout (256x256); x2 is the HD layout and equals BfA layout 1.
CLASSIC_335 = Layout(256, 256, {
    0: Rect(0, 0, 128, 64), 1: Rect(0, 64, 128, 64), 2: Rect(0, 128, 128, 32),
    9: Rect(0, 160, 128, 32), 10: Rect(0, 192, 128, 64),
    3: Rect(128, 0, 128, 64), 4: Rect(128, 64, 128, 32), 5: Rect(128, 96, 128, 64),
    6: Rect(128, 160, 128, 64), 7: Rect(128, 224, 128, 32),
})
HD_335 = CLASSIC_335.scaled(2)
TARGETS = {"classic": CLASSIC_335, "hd": HD_335}


def layout_from_tables(layouts: dict[int, dict], sections: list[dict], layout_id: int) -> Layout:
    """Build a layout from CharComponentTextureLayouts / CharComponentTextureSections rows.
    Overlay sections (OverlapSectionMask != 0: whole-texture blends) are not regions."""
    row = layouts[layout_id]
    out = Layout(row["Width"], row["Height"])
    for s in sections:
        if s["CharComponentTextureLayoutID"] == layout_id and not s.get("OverlapSectionMask") \
                and s["SectionType"] in SECTION_NAMES:
            out.sections[s["SectionType"]] = Rect(s["X"], s["Y"], s["Width"], s["Height"])
    return out


@dataclass(frozen=True)
class RemapGroup:
    section_types: tuple[int, ...]
    src: Rect                       # in source layout pixels
    dst: Rect                       # in destination layout pixels (bounding box of the types' rects)


def build_groups(src: Layout, dst: Layout) -> list[RemapGroup]:
    """One group per distinct source rectangle whose sections exist in the destination."""
    by_rect: dict[Rect, list[int]] = {}
    for t, r in sorted(src.sections.items()):
        if t in dst.sections:
            by_rect.setdefault(r, []).append(t)
    groups = []
    for rect, types in by_rect.items():
        ds = [dst.sections[t] for t in types]
        x0, y0 = min(d.x for d in ds), min(d.y for d in ds)
        x1, y1 = max(d.x + d.w for d in ds), max(d.y + d.h for d in ds)
        groups.append(RemapGroup(tuple(types), rect, Rect(x0, y0, x1 - x0, y1 - y0)))
    return groups


def is_identity(src: Layout, dst: Layout) -> bool:
    """True when the layouts are the same shape (only the resolution may differ)."""
    if src.width * dst.height != dst.width * src.height:
        return False
    f = dst.width / src.width
    return all(t in dst.sections and r.scaled(f) == dst.sections[t] for t, r in src.sections.items()) \
        and set(src.sections) == set(dst.sections)


EDGE_TOLERANCE = 0.01   # UVs this far outside 0..1 (float noise, e.g. v = -0.001) count as on the edge


def find_group(u: float, v: float, src: Layout, groups: list[RemapGroup]) -> RemapGroup | None:
    if -EDGE_TOLERANCE <= u < 0 or 1 < u <= 1 + EDGE_TOLERANCE:
        u = min(max(u, 0.0), 1.0)
    if -EDGE_TOLERANCE <= v < 0 or 1 < v <= 1 + EDGE_TOLERANCE:
        v = min(max(v, 0.0), 1.0)
    px, py = u * src.width, v * src.height
    for g in groups:
        if g.src.contains(px, py, src.width, src.height):
            return g
    return None


def remap_uv(u: float, v: float, src: Layout, dst: Layout, groups: list[RemapGroup]
             ) -> tuple[float, float] | None:
    """Map a normalised source UV into the destination layout, or None if it lies in
    no mapped section (left unchanged by callers)."""
    g = find_group(u, v, src, groups)
    if g is None:
        return None
    px, py = u * src.width, v * src.height
    nx = g.dst.x + (px - g.src.x) / g.src.w * g.dst.w
    ny = g.dst.y + (py - g.src.y) / g.src.h * g.dst.h
    return nx / dst.width, ny / dst.height
