"""Porting playable-race character models from a BfA (8.x) install to 3.3.5a.

BfA is the last expansion whose characters use the 3.3.5 customization system
(CharSections textures composited into one body texture, CharHairGeosets,
CharacterFacialHairStyles), so a BfA race converts with three pieces of work: the
model (wotlkconv), a texture-layout remap (layout 2 -> 3.3.5), and the DBC rows.
"""
