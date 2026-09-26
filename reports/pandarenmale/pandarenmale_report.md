# retroport report: pandarenmale.m2

Source: `I:\WoWDownPort\character\pandaren\male\pandarenmale.m2`

## Check (WotLK compatibility)

version 274   flags 0x202280   nViews 1
bones 208   vertices 166361   materials 10   textures 11
  texture type 1 (body skin): (runtime-filled)
  texture type 2 (object/cape): (runtime-filled)
  texture type 15 (?): (runtime-filled)
  texture type 16 (?): (runtime-filled)
  texture type 18 (?): (runtime-filled)
  texture type 17 (?): (runtime-filled)
  texture type 8 (skin extra): (runtime-filled)
  texture type 0 (hardcoded): (runtime-filled)
  texture type 19 (?): (runtime-filled)
  texture type 0 (hardcoded): (runtime-filled)
  texture type 0 (hardcoded): (runtime-filled)
  pandarenmale00.skin: 110 geosets, 31540 tris, 27285 verts  | 110 batches shaders [0, 16, 144, 16401, 16403, 16404, 32796]  | boneMax 0

## Texture composition

**Texture composition - `pandarenmale.m2`**
11 texture slot(s) | manifest lists 3

[0] type 1 (body skin) -> RUNTIME -> CharSections.dbc (body/skin)   |   wow.export[0]: pandarenmale_3550710.blp (fdid 3550710)
[1] type 2 (object/cape) -> RUNTIME -> equipment display   |   wow.export[1]: pandarenmale_4531029.blp (fdid 4531029)
[2] type 15 (?) -> RUNTIME -> client-filled   |   wow.export[2]: pandarenmale_eyereflect.blp (fdid 5210129)
[3] type 16 (?) -> RUNTIME -> client-filled
[4] type 18 (?) -> RUNTIME -> client-filled
[5] type 17 (?) -> RUNTIME -> client-filled
[6] type 8 (skin extra) -> RUNTIME -> CharSections.dbc (skin extra)
[7] type 0 (hardcoded) -> IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)
[8] type 19 (?) -> RUNTIME -> client-filled
[9] type 0 (hardcoded) -> IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)
[10] type 0 (hardcoded) -> IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)

**Summary:** 0 in-model | 2 CharSections (body/hair/skin) | 1 equipment | 3 unresolved type-0

[!] **This model has CharSections-driven textures** (empty in the M2 by design). If you port it by overwriting a race, the client fills these from THAT race's CharSections - so the mesh renders with the overwritten race's skin, not this model's. To get correct textures you must either (a) replace the overwritten race's skin BLPs with this model's, laid out to the WotLK CharSections regions, or (b) bake one body texture and hardcode it as a type-0 path in the M2. TXID-remove does NOT fix these - they're runtime, not in-model.

## Bug report (crash triage)

**Model:** `pandarenmale.m2`
**Format:** modern/raw MD21 | version 274 | uses_skel=False
**Bones:** 208 | **Vertices:** 166361 | **Materials:** 10 | **nViews:** 1
**Triangles (skin00):** 31540 / cap 21845  [!] **OVER CAP**
**Geoset groups:** [0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 15, 17, 18, 22, 32, 33, 34, 51]
[!] **Later-expansion geoset groups not in 3.3.5: [22, 32, 33, 34, 51]** - these were added post-WotLK (Cata+/SL+); the 3.3.5 client has no geometry for them, a likely render/crash cause
[!] **3 hardcoded (type-0) texture(s) with no path** - unresolved (FileDataID-only); run TXID-remove or check the listfile
