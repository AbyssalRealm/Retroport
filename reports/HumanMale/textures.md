**Texture composition - `HumanMale.M2`**
4 texture slot(s) | no manifest found

[0] type 1 (body skin) -> RUNTIME -> CharSections.dbc (body/skin)
[1] type 6 (hair) -> RUNTIME -> CharSections.dbc (hair)
[2] type 0 (hardcoded) -> IN-MODEL path -> `CHARACTER\BLOODELF\FEMALE\DEATHKNIGHTEYEGLOW.BLP`
[3] type 2 (object/cape) -> RUNTIME -> equipment display

**Summary:** 1 in-model | 2 CharSections (body/hair/skin) | 1 equipment | 0 unresolved type-0

[!] **This model has CharSections-driven textures** (empty in the M2 by design). If you port it by overwriting a race, the client fills these from THAT race's CharSections - so the mesh renders with the overwritten race's skin, not this model's. To get correct textures you must either (a) replace the overwritten race's skin BLPs with this model's, laid out to the WotLK CharSections regions, or (b) bake one body texture and hardcode it as a type-0 path in the M2. TXID-remove does NOT fix these - they're runtime, not in-model.
