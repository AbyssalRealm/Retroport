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
