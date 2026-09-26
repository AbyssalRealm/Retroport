# retroport report: kultiranmale.m2

Source: `I:\WoWDownPort\character\kultiran\male\kultiranmale.m2`

## Check (WotLK compatibility)

version 274   flags 0x302080   nViews 1
bones 0   vertices 131875   materials 10   textures 11
  texture type 2 (object/cape): (runtime-filled)
  texture type 1 (body skin): (runtime-filled)
  texture type 6 (hair): (runtime-filled)
  texture type 15 (?): (runtime-filled)
  texture type 16 (?): (runtime-filled)
  texture type 18 (?): (runtime-filled)
  texture type 17 (?): (runtime-filled)
  texture type 0 (hardcoded): (runtime-filled)
  texture type 19 (?): (runtime-filled)
  texture type 0 (hardcoded): (runtime-filled)
  texture type 0 (hardcoded): (runtime-filled)
  kultiranmale00.skin: 81 geosets, 30969 tris, 21889 verts  | 81 batches shaders [0, 16, 144, 16401, 16403, 16404, 32796]  | boneMax 0

## Texture composition

**Texture composition - `kultiranmale.m2`**
11 texture slot(s) | manifest lists 3

[0] type 2 (object/cape) -> RUNTIME -> equipment display   |   wow.export[0]: kultiranmale_3551585.blp (fdid 3551585)
[1] type 1 (body skin) -> RUNTIME -> CharSections.dbc (body/skin)   |   wow.export[1]: kultiranmale_4531012.blp (fdid 4531012)
[2] type 6 (hair) -> RUNTIME -> CharSections.dbc (hair)   |   wow.export[2]: kultiranmale_eyereflect.blp (fdid 5210066)
[3] type 15 (?) -> RUNTIME -> client-filled
[4] type 16 (?) -> RUNTIME -> client-filled
[5] type 18 (?) -> RUNTIME -> client-filled
[6] type 17 (?) -> RUNTIME -> client-filled
[7] type 0 (hardcoded) -> IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)
[8] type 19 (?) -> RUNTIME -> client-filled
[9] type 0 (hardcoded) -> IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)
[10] type 0 (hardcoded) -> IN-MODEL but PATH EMPTY -> unresolved (TXID-remove / listfile)

**Summary:** 0 in-model | 2 CharSections (body/hair/skin) | 1 equipment | 3 unresolved type-0

[!] **This model has CharSections-driven textures** (empty in the M2 by design). If you port it by overwriting a race, the client fills these from THAT race's CharSections - so the mesh renders with the overwritten race's skin, not this model's. To get correct textures you must either (a) replace the overwritten race's skin BLPs with this model's, laid out to the WotLK CharSections regions, or (b) bake one body texture and hardcode it as a type-0 path in the M2. TXID-remove does NOT fix these - they're runtime, not in-model.

## Bug report (crash triage)

**Model:** `kultiranmale.m2`
**Format:** modern/raw MD21 | version 274 | uses_skel=True
**Bones:** 0 | **Vertices:** 131875 | **Materials:** 10 | **nViews:** 1
**Triangles (skin00):** 30969 / cap 21845  [!] **OVER CAP**
**Geoset groups:** [0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 15, 17, 18, 20, 22, 32, 33, 51]
[!] **Later-expansion geoset groups not in 3.3.5: [22, 32, 33, 51]** - these were added post-WotLK (Cata+/SL+); the 3.3.5 client has no geometry for them, a likely render/crash cause
[!] **3 hardcoded (type-0) texture(s) with no path** - unresolved (FileDataID-only); run TXID-remove or check the listfile
