"""Retail characters: ChrModel + ChrCustomization projected onto 3.3.5's customization (retail.py)."""

import pytest

from retroport.character import layouts as L
from retroport.character import retail as R
from retroport.race import racials, sounds
from retroport.race.build import _barber_styles
from retroport.race.tables import HORDE, RaceSpec

from test_character import BFA_SECTIONS

ANY, NOT_DK, DK, TRANSMOG, UNLOCK = 141, 146, 142, 10, 12
REQS = {ANY: dict(ReqType=3, ClassMask=-1), NOT_DK: dict(ReqType=3, ClassMask=32735),
        DK: dict(ReqType=3, ClassMask=32), TRANSMOG: dict(ReqType=4, ClassMask=0), UNLOCK: dict(ReqType=2, ClassMask=0)}
# layout 147 (Mag'har male): target -> (TextureType, Layer, section mask)
LAYERS = {1: (1, 0, -1), 5: (1, 4, 0x400), 10: (6, 2, -1), 13: (1, 5, 0x20), 25: (19, 10, -1), 36: (1, 11, 0x200)}


class Tables:
    """A tiny retail database: one ChrModel (71, male, layout 147) and the options it is given."""

    def __init__(self):
        self.t = {n: {} for n in R.DB2_TABLES}
        self.t["ChrCustomizationReq"] = {i: dict(r, ID=i) for i, r in REQS.items()}
        self.t["ChrRaceXChrModel"] = {1: dict(ChrRacesID=36, ChrModelID=71, Sex=0)}
        self.t["ChrModel"] = {71: dict(Sex=0, DisplayID=500, CharComponentTextureLayoutID=147)}
        self.t["CreatureDisplayInfo"] = {500: dict(ModelID=600)}
        self.t["CreatureModelData"] = {600: dict(FileDataID=917116), 601: dict(FileDataID=1968587)}
        self.t["ChrCustomizationCondModel"] = {2: dict(CreatureModelDataID=601)}
        self.t["ChrModelTextureLayer"] = {
            i: dict(CharComponentTextureLayoutsID=147, ChrModelTextureTargetID=[t, 0], TextureType=tt, Layer=layer,
                    TextureSectionTypeBitMask=mask) for i, (t, (tt, layer, mask)) in enumerate(LAYERS.items())}
        self._ids = {"opt": 0, "cho": 0, "ele": 0, "geo": 0, "mat": 0}

    def _next(self, k):
        self._ids[k] += 1
        return self._ids[k]

    def option(self, name, choices, order=0):
        """choices: [(name, req, ui, elements)]; an element is ("geo", id) | ("mat", target, mat[, related
        choice name]) | ("cond", condmodel). Returns {choice name: id}."""
        oid = self._next("opt")
        self.t["ChrCustomizationOption"][oid] = dict(Name_lang=name, ChrModelID=71, OrderIndex=order or oid)
        ids = {}
        for k, (cname, req, ui, elements) in enumerate(choices):
            cid = 1000 * oid + k
            ids[cname] = cid
            self.t["ChrCustomizationChoice"][cid] = dict(Name_lang=cname, ChrCustomizationOptionID=oid, OrderIndex=k,
                                                        UiOrderIndex=ui, ChrCustomizationReqID=req)
            for el in elements:
                e = dict(ChrCustomizationChoiceID=cid, RelatedChrCustomizationChoiceID=0, ChrCustomizationGeosetID=0,
                         ChrCustomizationMaterialID=0, ChrCustomizationCondModelID=0)
                if el[0] == "geo":
                    gid = self._next("geo")
                    self.t["ChrCustomizationGeoset"][gid] = dict(GeosetType=el[1] // 100, GeosetID=el[1] % 100)
                    e["ChrCustomizationGeosetID"] = gid
                elif el[0] == "mat":
                    mid = self._next("mat")
                    self.t["ChrCustomizationMaterial"][mid] = dict(ChrModelTextureTargetID=el[1], MaterialResourcesID=el[2])
                    e["ChrCustomizationMaterialID"] = mid
                    if len(el) > 3:
                        e["RelatedChrCustomizationChoiceID"] = el[3]
                else:
                    e["ChrCustomizationCondModelID"] = el[1]
                self.t["ChrCustomizationElement"][self._next("ele")] = e
        return ids

    def __call__(self, name):
        return list(self.t[name].items())


def _maghar():
    db = Tables()
    skins = db.option("Skin Color", [("Dark", ANY, 0, [("mat", 1, 100), ("mat", 13, 110)]),
                                     ("Light", ANY, 1, [("mat", 1, 101), ("mat", 13, 111)]),
                                     ("Transmog", TRANSMOG, 100, [("mat", 1, 199)])])
    db.option("Face", [(f"Face {f}", ANY, 0, [("mat", 5, 200 + 10 * f + s, sid) for s, sid in enumerate(skins.values())])
                       for f in range(2)])
    db.option("Hair Style", [("Bald", ANY, 0, [("geo", 0)]), ("Majestic", ANY, 1, [("geo", 16)])])
    db.option("Hair Color", [("", ANY, 1, [("mat", 10, 300)]), ("", ANY, 0, [("mat", 10, 301)])])
    db.option("Beard", [("None", ANY, 0, [("geo", 100)]), ("Full", ANY, 1, [("geo", 113)])])
    db.option("Sideburns", [("None", ANY, 0, [("geo", 200)]), ("Chops", ANY, 2, [("geo", 212)]),
                            ("Medium", ANY, 1, [("geo", 209)])])
    db.option("Eye Color", [("", NOT_DK, 1, [("mat", 25, 400)]), ("", DK, 70, [("geo", 1701), ("mat", 25, 401)]),
                            ("Primal", UNLOCK, 2, [("geo", 1702), ("mat", 25, 402)])])
    db.option("Tusks", [("Natural", ANY, 0, [("geo", 4001)]), ("Broken", ANY, 1, [("geo", 4003)])])
    db.option("Earrings", [("None", ANY, 0, [("geo", 3500), ("geo", 702)]), ("Upper", ANY, 1, [("geo", 3501), ("geo", 702)]),
                           ("Plugs", ANY, 2, [("geo", 3503), ("geo", 703)])])
    db.option("Hunched", [("Hunched", ANY, 1, []), ("Upright", ANY, 0, [("cond", 2)])])
    return db


MODEL_GEOSETS = {0, 1, 16, 113, 209, 212, 401, 702, 703, 1701, 1702, 1703, 2001, 2002, 3201, 3202, 3203, 3301,
                 3501, 3503, 4001, 4003}


def test_skins_faces_hair_and_eyes():
    ch = R.read_character(_maghar(), 36, 0, "MagharOrc", choose={})
    assert (ch.chr_model, ch.layout_id, ch.model_fdid) == (71, 147, 917116)
    rows = {(s["BaseSection"], s["VariationIndex"], s["ColorIndex"]): s["MaterialResourcesID"] for s in ch.sections}
    # the transmog skin is left out; underwear (LegUpper) rides on the skin rows
    assert [k for k in rows if k[0] == 0] == [(0, 0, 0), (0, 0, 1)]
    assert rows[(0, 0, 1)] == [101, 0, 0] and rows[(4, 0, 1)] == [111, 0, 0]
    # one face row per face and skin colour, from the element related to that colour
    assert rows[(1, 1, 0)] == [210, 0, 0] and rows[(1, 1, 1)] == [211, 0, 0]
    # hair colours in retail's UI order: the second choice (UiOrderIndex 0) is colour 0
    assert rows[(3, 1, 0)] == [301, 0, 0] and rows[(3, 0, 1)] == [300, 0, 0]
    assert ch.hair == [{"VariationID": 0, "GeosetID": 0, "GeosetType": 0}, {"VariationID": 1, "GeosetID": 16, "GeosetType": 0}]
    assert ch.names["hair"] == {0: "Bald", 1: "Majestic"}
    assert ch.eye_material == 400                     # the first colour any class may pick, not the DK's
    assert all(s["Flags"] == R.PLAYABLE_FLAGS for s in ch.sections)


def test_body_choice_picks_the_conditional_model():
    ch = R.read_character(_maghar(), 36, 0, "MagharOrc")            # BODY_CHOICES: the user's upright male
    assert ch.model_fdid == 1968587 and ch.model_note == "Hunched = Upright"
    with pytest.raises(R.RetailError):
        R.read_character(_maghar(), 36, 0, "MagharOrc", choose={"Hunched": "Sideways"})


def test_features_combine_two_options_first_major():
    ch = R.read_character(_maghar(), 36, 0, "MagharOrc", choose={})
    # Beard x Sideburns (FEATURES), sideburns in UI order: None, Medium, Chops
    assert [r["Geoset"] for r in ch.facial] == [[0, 0, 0, 0, 0], [0, 0, 9, 0, 0], [0, 0, 12, 0, 0],
                                                [13, 0, 0, 0, 0], [13, 0, 9, 0, 0], [13, 0, 12, 0, 0]]
    assert ch.names["facial"][0] == "None" and ch.names["facial"][5] == "Full, Chops"


def test_a_feature_outside_the_facial_groups_is_renumbered():
    ch = R.read_character(_maghar(), 36, 0, "MagharOrc", choose={}, features=("Earrings",))
    assert ch.geosets[3501] == 301 and ch.geosets[3503] == 303
    # Plugs needs the stretched ears (703); 3.3.5 always shows 702
    assert [r["Geoset"] for r in ch.facial] == [[0, 0, 0, 0, 0], [0, 1, 0, 0, 0]]
    assert any("Plugs" in n and "703" in n for n in ch.notes)


def test_geosets_3335_cannot_switch_keep_retails_defaults():
    ch = R.read_character(_maghar(), 36, 0, "MagharOrc", MODEL_GEOSETS, choose={})
    g = ch.geosets
    assert g[4001] == 0 and g[4003] is None                       # Tusks: its default choice, Natural
    assert g[3501] is None and g[3503] is None                    # Earrings (not a feature): None
    assert g[2001] == 0 and g[2002] is None                       # no option: xx01
    assert g[3201] == 0 and g[3202] == 0 and g[3203] is None      # the face: shared piece + first shape
    assert g[3301] == 0                                           # the eyeballs
    assert g[1701] == R.CLIENT_DK_GLOW and g[1702] is None and g[1703] is None
    assert g[1] is None and 16 not in g                           # a hair style no row offers is hidden
    assert 113 not in g and 209 not in g and 212 not in g         # the offered beards and sideburns stay


def test_a_renumbered_feature_may_not_land_on_the_models_own_geosets():
    with pytest.raises(R.RetailError):
        R.read_character(_maghar(), 36, 0, "MagharOrc", MODEL_GEOSETS | {302}, choose={}, features=("Earrings",))


def test_playable_requirements():
    assert R._playable(None) and R._playable(REQS[ANY]) and R._playable(REQS[NOT_DK])
    assert not R._playable(REQS[DK]) and not R._playable(REQS[TRANSMOG]) and not R._playable(REQS[UNLOCK])
    assert not R._playable(dict(REQS[ANY], ReqAchievementID=13077))


def test_retail_layout_147_is_bfa_layout_2_doubled():
    # retail's sections for layout 147 (2048x1024), read from 12.1.5
    doubled = [dict(s, CharComponentTextureLayoutID=147, X=s["X"] * 2, Y=s["Y"] * 2, Width=s["Width"] * 2,
                    Height=s["Height"] * 2) for s in BFA_SECTIONS if s["CharComponentTextureLayoutID"] == 2]
    lay = L.layout_from_tables({147: {"Width": 2048, "Height": 1024}}, doubled, 147)
    bfa = L.layout_from_tables({2: {"Width": 1024, "Height": 512}}, BFA_SECTIONS, 2)
    assert lay.sections == bfa.scaled(2).sections


def test_race_bits_and_masks():
    assert racials.source_race_bit({"PlayableRaceBit": 13}, 36) == 1 << 13         # retail Mag'har
    assert racials.source_race_bit({}, 32) == 1 << 31                              # BfA Kul Tiran
    assert racials.source_race_mask({"RaceMask": -1}) == (1 << 64) - 1
    assert racials.source_race_mask({"RaceMasks": [1 << 13, 1]}) == (1 << 13) | (1 << 32)


def test_voice_finds_retail_displays_through_chr_model():
    db = _maghar()

    def table(name):
        return db(name) if name in db.t else []
    assert sounds.race_displays(table, 36, {}) == {0: 500, 1: 0}
    assert sounds.race_displays(table, 36, {"MaleDisplayID": 7, "FemaleDisplayID": 8}) == {0: 7, 1: 8}


def test_barber_rows_from_retail_choice_names():
    class Ch:
        source = "retail"

        def __init__(self, names):
            self.names = names
    class Barber:
        def max_id(self):
            return 400
    chars = {0: Ch({"hair": {"0": "Bald", "1": "Majestic"}, "facial": {"0": "None"}}), 1: Ch({"hair": {"0": "Savage"}})}
    warnings = []
    rows = _barber_styles(None, "MagharOrc", chars, [RaceSpec(26, HORDE, 2)], [Barber()], warnings)
    got = [(b.sex, b.type, b.data, b.name) for b in rows[26]]
    assert got == [(0, 0, 0, "Bald"), (0, 0, 1, "Majestic"), (0, 2, 0, "None"), (1, 0, 0, "Savage")]
    assert [b.id for b in rows[26]] == [401, 402, 403, 404] and not warnings
