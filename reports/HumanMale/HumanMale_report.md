# retroport report: HumanMale.M2

Source: `I:\WoW Modding\In-Game Modifier Tools\MPQEditor 4.0.0.881\x64\Work\Character\Human\Male\HumanMale.M2`

## Check (WotLK compatibility)

version 264   flags 0x0   nViews 3
bones 138   vertices 5264   materials 4   textures 4
  texture type 1 (body skin): (runtime-filled)
  texture type 6 (hair): (runtime-filled)
  texture type 0 (hardcoded): CHARACTER\BLOODELF\FEMALE\DEATHKNIGHTEYEGLOW.BLP
  texture type 2 (object/cape): (runtime-filled)
  HumanMale00.skin: 61 geosets, 6356 tris, 5264 verts  | 61 batches shaders [0]  | boneMax 64
  HumanMale01.skin: 61 geosets, 6356 tris, 5264 verts  | 61 batches shaders [0]  | boneMax 53
  HumanMale02.skin: 63 geosets, 6356 tris, 5270 verts  | 63 batches shaders [0]  | boneMax 21

## Texture composition

**Texture composition - `HumanMale.M2`**
4 texture slot(s) | no manifest found

[0] type 1 (body skin) -> RUNTIME -> CharSections.dbc (body/skin)
[1] type 6 (hair) -> RUNTIME -> CharSections.dbc (hair)
[2] type 0 (hardcoded) -> IN-MODEL path -> `CHARACTER\BLOODELF\FEMALE\DEATHKNIGHTEYEGLOW.BLP`
[3] type 2 (object/cape) -> RUNTIME -> equipment display

**Summary:** 1 in-model | 2 CharSections (body/hair/skin) | 1 equipment | 0 unresolved type-0

[!] **This model has CharSections-driven textures** (empty in the M2 by design). If you port it by overwriting a race, the client fills these from THAT race's CharSections - so the mesh renders with the overwritten race's skin, not this model's. To get correct textures you must either (a) replace the overwritten race's skin BLPs with this model's, laid out to the WotLK CharSections regions, or (b) bake one body texture and hardcode it as a type-0 path in the M2. TXID-remove does NOT fix these - they're runtime, not in-model.

## Bug report (crash triage)

**Model:** `HumanMale.M2`
**Format:** MD20 (WotLK) | version 264 | uses_skel=False
**Bones:** 138 | **Vertices:** 5264 | **Materials:** 4 | **nViews:** 3
**Triangles (skin00):** 6356 / cap 21845
**Geoset groups:** [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 15, 17, 18]
**Texture paths:** `CHARACTER\BLOODELF\FEMALE\DEATHKNIGHTEYEGLOW.BLP`
