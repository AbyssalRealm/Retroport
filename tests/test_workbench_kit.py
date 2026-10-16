"""The workbench's kit side: local retail data for the kit (kitlocal), the runner's install and
undo, and the 3D preview's model resolution."""

import csv
import io
import json
import os

from retroport.workbench import kit as K
from retroport.workbench import kitlocal as L
from retroport.workbench import models as M


# -- kitlocal -----------------------------------------------------------------------------
def test_tables_come_out_as_wagos_csv():
    rows = [(5, {"Name_lang": "Season 10, Elite", "Color": -16711936, "Offset": [-1.7000000476837158, 0.0, 2.0850000381469727],
                 "Scale": 0.07699999958276749})]
    text = L.table_csv(rows)
    got = next(csv.DictReader(io.StringIO(text)))
    assert text.splitlines()[0] == "ID,Name_lang,Color,Offset_0,Offset_1,Offset_2,Scale"
    # wago's own text for these (12.1.5 ChrRaces): 11 decimals, trailing zeros dropped
    assert got == {"ID": "5", "Name_lang": "Season 10, Elite", "Color": "-16711936", "Offset_0": "-1.70000004768",
                   "Offset_1": "0", "Offset_2": "2.08500003815", "Scale": "0.07699999958"}


class FakeCasc:
    def __init__(self, files):
        self.files = files

    def __contains__(self, fdid):
        return fdid in self.files


class FakeSource:
    build = "12.1.5.70391"

    class spec:
        path = "X:\\retail"

    def __init__(self):
        self.casc = FakeCasc({100: b"BLP2data", 200: None})
        self._db2 = {"itemnamedescription": 1}

    def open(self):
        return self.casc

    def table(self, name):
        return [(2, {"Description_lang": "Heroic", "Color": 65280})]

    def read(self, fdid):
        if self.casc.files[fdid] is None:
            raise RuntimeError("chunk is encrypted")
        return self.casc.files[fdid]


def test_the_kits_requests_are_answered_locally_at_the_installs_build(tmp_path):
    local = L.LocalRetail(FakeSource())
    asked = []
    get = local.wrap(lambda url, dest, *a, **k: asked.append(url) or "ok")
    t = str(tmp_path / "db2" / "ItemNameDescription.csv")
    assert get("https://wago.tools/db2/ItemNameDescription/csv?build=12.1.5.70391", t) == "ok"
    assert open(t, encoding="utf-8").read() == "ID,Description_lang,Color\n2,Heroic,65280\n"
    f = str(tmp_path / "cache" / "100")
    assert get("https://wago.tools/api/casc/100?download&version=12.1.5.70391", f) == "ok" and open(f, "rb").read() == b"BLP2data"
    # not in this build: "missing" without asking anyone; encrypted here: wago answers (zeros = the kit's "encrypted")
    assert get("https://wago.tools/api/casc/300?download&version=12.1.5.70391", str(tmp_path / "x")) == "missing"
    get("https://wago.tools/api/casc/200?download&version=12.1.5.70391", str(tmp_path / "y"))
    # another build, a table this install can't name, the listfile: the network as before
    get("https://wago.tools/api/casc/100?download&version=12.1.0.69933", str(tmp_path / "z"))
    get("https://wago.tools/db2/ChrModel/csv?build=12.1.5.70391", str(tmp_path / "w"))
    assert asked == ["https://wago.tools/api/casc/200?download&version=12.1.5.70391",
                     "https://wago.tools/api/casc/100?download&version=12.1.0.69933",
                     "https://wago.tools/db2/ChrModel/csv?build=12.1.5.70391"]
    assert local.stats["tables"] == 1 and local.stats["files"] == 1 and local.stats["missing"] == 1


# -- the runner -----------------------------------------------------------------------------
def test_the_basket_becomes_the_kits_select_arguments():
    basket = [{"kind": "set", "id": 5}, {"kind": "itemset", "id": 9, "items": [1, 2]}, {"kind": "item", "id": 2}]
    assert K.basket_args(basket) == ["--set", "5", "--item", "1", "--item", "2"]


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "HELPER_SETTINGS", str(tmp_path / "helper.json"))
    monkeypatch.setattr(K, "wow_running", lambda: False)
    work, data = tmp_path / "work", tmp_path / "Data"
    (work / "reports").mkdir(parents=True)
    (work / "out").mkdir()
    data.mkdir()
    (work / "bp.json").write_text(json.dumps({"build": "12.1.5.70391"}))
    (work / "reports" / "steps.json").write_text(json.dumps({s: {"ok": True, "at": "x"} for s in K.ORDER}))
    (work / "out" / "patch-I.MPQ").write_bytes(b"new patch")
    (data / "patch-I.MPQ").write_bytes(b"old patch")                       # an earlier install of it
    (tmp_path / "helper.json").write_text(json.dumps({"work": str(work), "client_data": str(data)}))
    kit = K.Kit({"bp": str(tmp_path / "bp.py"), "tools": str(tmp_path / "no-tools")}, None, "", "", str(tmp_path / "wb"))
    kit.job = K.Job("test")
    return kit, work, data


def test_install_backs_up_what_it_replaces_and_undo_puts_it_back(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    assert kit.install()
    assert (data / "patch-I.MPQ").read_bytes() == b"new patch"
    rec = kit.last_install()
    assert rec["client"] == ["patch-I.MPQ"] and rec["added"] == [] and rec["server"] == []
    assert open(os.path.join(rec["backup"], "client", "patch-I.MPQ"), "rb").read() == b"old patch"
    # the helper's Undo knows this install too
    assert json.loads((tmp_path / "helper.json").read_text())["last_backup"] == rec["backup"]
    assert kit.undo()
    assert (data / "patch-I.MPQ").read_bytes() == b"old patch"
    assert kit.last_install() is None and json.loads((tmp_path / "helper.json").read_text())["last_backup"] == ""


def test_install_builds_a_changed_basket_only_and_remembers_what_it_put_in(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    led = {s: {"ok": True, "at": "2026-10-14 07:30:34"} for s in K.ORDER}
    (work / "reports" / "steps.json").write_text(json.dumps(led))
    ran = []

    def kit_step(args, step="", local=None, cwd=None):          # the kit, as far as the ledger goes
        ran.append(args[0])
        if args[0] == "select":
            (work / "selection.json").write_text(json.dumps({"n": len(ran)}))
        return 0
    monkeypatch.setattr(kit, "_run", kit_step)
    basket = [{"kind": "set", "source": "retail", "id": 5, "name": "A"}]
    assert not kit.status(basket)["built"]                     # selected from elsewhere: not this basket
    assert kit.go(basket) and ran == ["select"]
    assert kit.last_install()["basket"] == basket
    st = kit.status(basket)
    assert st["built"] and st["installed"]
    assert kit.go(basket) and ran == ["select"]                # unchanged: no new selection
    more = basket + [{"kind": "item", "source": "retail", "id": 7, "name": "B"}]
    assert not kit.status(more)["built"]
    assert kit.go(more) and ran == ["select", "select"]
    sel = work / "selection.json"                              # the console helper selected something else
    sel.write_text("{}")
    os.utime(sel, (sel.stat().st_atime, sel.stat().st_mtime + 5))
    assert kit.selected() is None and not kit.selected_matches(more)


def test_install_waits_for_a_packed_build(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    (work / "reports" / "steps.json").write_text(json.dumps({"scan": {"ok": True}}))
    assert not kit.install()
    assert (data / "patch-I.MPQ").read_bytes() == b"old patch"


# -- preview resolution ---------------------------------------------------------------------
def _row(mr=(0, 0), mm=(0, 0), types=(-1, -1), geosets=(0,) * 6, attach=(0,) * 6, helm=(0, 0)):
    """An index display row: model resources, materials, model types, GeosetGroup, AttachmentGeosetGroup,
    HelmetGeosetVis."""
    return [*mr, *mm, *types, *geosets, *attach, *helm]


def _index():
    return M.DisplayIndex({
        "displays": {"10": _row((500, 501), (700, 700), (0, 0)), "11": _row((600, 0), (701, 0), (0, -1), helm=(77, 77)),
                     "12": _row(), "13": _row((0, 610), (0, 702), (-1, 1))},
        # a shoulder pair: each resource lists both sides; a helm: one file per race and sex; a collection
        "model_files": {"500": [1, 2], "501": [1, 2], "600": [3, 4, 5], "610": [6]},
        "comp_models": {"1": [0, 2, 0, 0], "2": [0, 2, 0, 1], "3": [1, 0, 0, -1], "4": [1, 1, 0, -1], "5": [2, 0, 0, -1]},
        "tex_files": {"700": [[90, 0]], "701": [[91, 0], [92, 0]], "702": [[94, 0]], "800": [[93, 0]]},
        "comp_tex": {"91": [1, 0, 0], "92": [2, 0, 0]},
        "sections": {"12": [[3, 800]]}, "model_mat": {},
        "helmet_hides": {"77": {"1": [0, 7]}},
        # a Nightborne (27) wears a Night Elf's (4) models and textures
        "fallbacks": {"27": [4, 0, 4, 1, 4, 0, 4, 1]}, "prefixes": {"hu": 1, "nb": 27}})


def test_a_preview_takes_each_shoulder_once_and_the_races_own_helm():
    idx = _index()
    assert [(m["fdid"], m["position"]) for m in idx.models(10, 1, 0)] == [(1, 0), (2, 1)]
    assert [(m["fdid"], m["textures"][2]) for m in idx.models(11, 1, 0)] == [(3, 91)]
    assert [m["fdid"] for m in idx.models(11, 1, 1)] == [4]
    assert [(m["fdid"], m["textures"][2]) for m in idx.models(11, 2, 0)] == [(5, 92)]
    assert idx.body_textures(12, 1, 0) == [{"section": 3, "name": "Upper torso", "fdid": 93}]


def test_a_race_without_its_own_model_wears_its_fallback_races():
    idx = _index()
    assert idx.models(11, 27, 1) == idx.models(11, 4, 1) == []               # nobody made one for race 4 either
    idx.comp_models["4"] = [4, 1, 0, -1]
    idx.comp_models = {int(k): v for k, v in idx.comp_models.items()}
    assert [m["fdid"] for m in idx.models(11, 27, 1)] == [4]


def test_a_display_says_how_its_models_are_worn_and_what_a_helm_hides():
    idx = _index()
    coll = idx.models(13, 1, 0)
    assert [(m["fdid"], m["type"], m["textures"]) for m in coll] == [(6, M.COLLECTION, {2: 94, 3: 94})]  # 3 falls back to 2
    assert idx.models(11, 1, 0)[0]["type"] == M.ATTACHED
    assert idx.helmet_hidden(11, 1, 0) == [0, 7] and idx.helmet_hidden(11, 2, 0) == []
    assert idx.row(13)["model_types"] == [-1, 1] and idx.prefixes["nb"] == 27


def test_a_display_made_for_some_races_says_which():
    idx = _index()
    assert idx.model_races(11) == [1, 2]                                 # one helm file per race and sex
    assert idx.model_races(10) == [] and idx.model_races(12) == []       # fits everyone; no model


def test_stand_follows_an_alias_and_quaternions_unpack():
    seqs = [{"id": 4, "variation_index": 0, "flags": 0}, {"id": 0, "variation_index": 0, "flags": M.SEQ_ALIAS, "alias_next": 2},
            {"id": 0, "variation_index": 1, "flags": 0x20}]
    assert M.stand_index(seqs) == 2
    from wotlkconv.m2.types import Track
    # M2CompQuat: (c < 0 ? c + 32768 : c - 32767) / 32767 - the identity is stored (32767, 32767, 32767, -1)
    tr = Track(kind="quat16", interpolation=1, timestamps=[[0, 100]], values=[[(32767, 32767, 32767, -1), (0, 0, 0, 32767)]])
    t = M._track(tr, 0, None, "quat16")
    assert t["t"] == [0, 100] and t["v"][:4] == [0.0, 0.0, 0.0, 1.0] and t["v"][4:] == [-1.0, -1.0, -1.0, 0.0]


def test_a_batchs_texture_layers_keep_their_slots_animations_and_weights():
    class Model:
        texture_combos = [0, 9, 1]                      # the batch's textures: its own, one past the table, a replaceable one
        textures = [{"type": 0, "flags": 0}, {"type": 2, "flags": 3}]
        texture_file_ids = [100, 0]
        texture_transform_combos = [0xFFFF, 0xFFFF, 4]
        texture_weight_combos = [0, 1, 2]
    assert M._layers(Model, {2: 200}, combo=0, tcombo=0, wcombo=0, count=3) == [
        {"texture": 0, "type": 0, "uvanim": -1, "weight": 1, "wrap": [False, False]},     # kept in its slot
        {"texture": 200, "type": 2, "uvanim": 4, "weight": 2, "wrap": [True, True]}]


def test_particle_tracks_run_over_a_life_and_packed_gravity_pulls_along_its_direction():
    class Life:
        times = [0, 32767]
        values = [(255, 0, 0), (0, 255, 0)]
    assert M._part(Life, 1 / 255.0) == {"t": [0.0, 1.0], "v": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]}
    assert M._part(None) is None
    down = M._gravity((0, 0, 100))                     # no x or y: straight along z
    assert down[:2] == [0.0, 0.0] and abs(down[2] + 100 * 0.04238648) < 1e-9


def test_only_sections_5_to_9_make_a_body_hd():
    from retroport.workbench import character as CH
    rows = lambda *bases: [{"BaseSection": b} for b in bases]
    assert CH.hd_skin(rows(0, 1, 5, 6)) and CH.hd_skin(rows(9))
    assert not CH.hd_skin(rows(0, 1, 2, 3, 4, 10, 11, 15))           # Pandaren's: its own skins, no HD
