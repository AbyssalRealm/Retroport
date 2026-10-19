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
    monkeypatch.setattr(K.deps, "have", lambda data_dir, name: True)        # the own copies are here
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


def test_a_piece_left_out_is_a_new_selection_of_the_same_basket(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    ran = []

    def kit_step(args, step="", local=None, cwd=None):
        ran.append(args)
        if args[0] == "select":
            (work / "selection.json").write_text(json.dumps({"n": len(ran)}))
        return 0
    monkeypatch.setattr(kit, "_run", kit_step)
    basket = [{"kind": "set", "source": "retail", "id": 5, "name": "A"}]
    assert kit.go(basket) and ran[-1][-2:] == ["--set", "5"]
    assert kit.selected_matches(basket)
    left = [dict(basket[0], leave_out=["1"])]
    assert not kit.selected_matches(left, ["--display", "2"])             # the same entry, a piece fewer
    assert kit.go(left, None, ["--display", "2"]) and ran[-1][-2:] == ["--display", "2"]
    assert kit.selected_matches(left, ["--display", "2"]) and kit.last_install()["basket"] == left


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


# -- what kitlocal changes in the kit's run (in its process only) ---------------------------
def test_a_table_keyed_by_its_file_has_no_id_column_as_on_wago():
    rows = [(116902, {"GeoBox": [0, 0], "FileDataID": 116902, "Flags": 0, "ModelResourcesID": 18251})]
    assert L.table_csv(rows).splitlines() == ["GeoBox_0,GeoBox_1,FileDataID,Flags,ModelResourcesID", "0,0,116902,0,18251"]
    assert L.table_csv([(7, {"Name_lang": "x"})]).splitlines()[0] == "ID,Name_lang"     # the others keep theirs


def test_tables_cached_with_an_id_column_wagos_lack_are_put_right(tmp_path):
    db2 = tmp_path / "db2" / "12.1.5.70391"
    db2.mkdir(parents=True)
    bad = "ID,GeoBox_0,FileDataID,Flags\n116902,0,116902,0\n"
    (db2 / "ModelFileData.csv").write_text(bad)
    (db2 / "TextureFileData.csv").write_text("FileDataID,UsageType\n116922,0\n")        # right already
    (db2 / "ChrRaces.csv").write_text("ID,Name_lang\n1,Human\n")
    assert L.repair_cached_tables(str(tmp_path)) == ["12.1.5.70391/ModelFileData"]
    assert (db2 / "ModelFileData.csv").read_text() == "GeoBox_0,FileDataID,Flags\n0,116902,0\n"
    assert (db2 / "ModelFileData.csv.retroport-backup").read_text() == bad
    assert (db2 / "ChrRaces.csv").read_text() == "ID,Name_lang\n1,Human\n"
    assert L.repair_cached_tables(str(tmp_path)) == []                                  # once


def _sequences(*flags_and_ids):
    """A model holding only its sequences: (flags, animation id) each."""
    import struct
    b = bytearray(0x100 + 64 * len(flags_and_ids))
    struct.pack_into("<II", b, 0x1C, len(flags_and_ids), 0x100)
    for i, (fl, aid) in enumerate(flags_and_ids):
        struct.pack_into("<HHIfI", b, 0x100 + i * 64, aid, 0, 0, 0.0, fl)
    return bytes(b)


def _flags(model):
    import struct
    n, o = struct.unpack_from("<II", model, 0x1C)
    return [struct.unpack_from("<I", model, o + i * 64 + 0x0C)[0] for i in range(n)]


def _fake_kit_modules(keyed):
    """The kit's convert / m2edit / m2model as far as keep_needed_anims uses them."""
    import struct
    import types

    def external_set(md20):
        n, o = struct.unpack_from("<II", md20, 0x1C)
        out = []
        for i in range(n):
            aid, var, _d, _s, fl = struct.unpack_from("<HHIfI", md20, o + i * 64)
            if not fl & 0x60:
                out.append((i, aid, var))
        return out

    class Builder:                                  # the kit's copy_anims: every .anim of the source
        def __init__(self, built, man):
            self.built, self.man, self.written = built, man, []

        def read_built(self, rel):
            return self.built[rel]

        def have(self, path):
            return False

        def write(self, dst, data, kind, meta):
            self.written.append(dst)

        def copy_anims(self, src_rel, rel):
            for k, v in self.man.items():
                if k.endswith(".anim"):
                    self.write(rel[:-3] + v["path"][len(src_rel) - 3:], b"", "anim", {})
    convert = types.SimpleNamespace(MAX_ANIM_ID=505, Builder=Builder,
                                    required_external_animations=lambda b: [x for x in external_set(b) if x[0] in keyed])
    m2edit = types.SimpleNamespace(repack=lambda b: b, union_bounds=lambda b: (b, False))
    m2model = types.SimpleNamespace(external_set=external_set,
                                    anim_name=lambda path, a, v: "%s%04d-%02d.anim" % (path[:-3], a, v))
    return convert, m2edit, m2model


def test_a_sequence_that_keys_nothing_is_made_resident_and_only_streamed_anims_are_copied():
    convert, m2edit, m2model = _fake_kit_modules(keyed={1})
    assert L.keep_needed_anims(convert, m2edit, m2model)
    # 0 resident, 1 external and keyed, 2 external keying nothing, 3 an alias, 4 beyond 3.3.5's ids
    model = _sequences((0x20, 0), (0, 4), (0, 5), (0x40, 6), (0, 600))
    assert _flags(m2edit.repack(model)) == [0x20, 0, 0x20, 0x40, 0]
    assert _flags(m2edit.union_bounds(model)[0]) == [0x20, 0, 0x20, 0x40, 0]
    b = convert.Builder({"Helm_PaM.m2": m2edit.repack(model)},
                        {"helm.m2": {"path": "Helm.m2"}, "helm0004-00.anim": {"path": "Helm0004-00.anim"},
                         "helm0005-00.anim": {"path": "Helm0005-00.anim"}})
    b.copy_anims("Helm.m2", "Helm_PaM.m2")
    assert b.written == ["Helm_PaM0004-00.anim"] and "write" not in b.__dict__
    convert.mark_unused_anims_resident = object()                         # a kit that does it itself
    assert not L.keep_needed_anims(convert, m2edit, m2model)


def test_the_build_plan_forgets_displays_no_longer_selected(tmp_path):
    import types
    (tmp_path / "build").mkdir()
    (tmp_path / "selection.json").write_text(json.dumps({"displays": {"1": {}, "2": {}}}))
    (tmp_path / "build" / "plan.json").write_text(json.dumps({"1": {"ok": True}, "3": {"ok": True}}))
    bpcore = types.SimpleNamespace(load_json=lambda p, d=None: json.load(open(p)) if os.path.isfile(p) else d,
                                   save_json=lambda p, o: open(p, "w").write(json.dumps(o)))
    prj = types.SimpleNamespace(path=lambda *parts: os.path.join(str(tmp_path), *parts))
    seen, said = [], []
    build = types.SimpleNamespace(run=lambda prj, log, only, force, extension: seen.append(
        json.load(open(prj.path("build", "plan.json")))) or 0)
    L.plan_follows_selection(build, bpcore)
    assert build.run(prj, said.append, set(), False, True) == 0
    assert seen == [{"1": {"ok": True}}] and "1 display(s) no longer selected" in said[0]
    build.run(prj, said.append, {"3"}, False, True)                       # a partial build leaves the plan alone
    assert len(said) == 1


def test_a_helm_a_little_off_is_a_warning_and_other_failures_still_fail():
    import types

    class Report:
        def __init__(self):
            self.fails, self.notes = {}, {}

        def fail(self, gate, what):
            self.fails.setdefault(gate, []).append(what)

        def note(self, gate, what):
            self.notes.setdefault(gate, []).append(what)

    def placement(prj, rep, plan, sc):
        rep.fail("P1 placement", "helm_PaM.m2: centroid 0.13 m off retail's")
        rep.fail("C1/C2 closure", "x.anim missing")
    gates = types.SimpleNamespace(placement=placement)
    L.placement_warns(gates)
    rep = Report()
    gates.placement(None, rep, {}, {})
    assert rep.notes == {L.PLACEMENT_WARNING: ["helm_PaM.m2: centroid 0.13 m off retail's"]}
    assert rep.fails == {"C1/C2 closure": ["x.anim missing"]} and "fail" not in rep.__dict__


# -- the kit's gates, as the workbench answers them --------------------------------------
def test_newtech_stopping_is_answered_by_learning_and_the_stats_follow_the_tables(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    led = {"scan": {"ok": True}, "select": {"ok": True}}
    (work / "reports" / "steps.json").write_text(json.dumps(led))
    ran, learned, hooked = [], [], []

    def kit_step(args, step="", local=None, cwd=None):
        ran.append(" ".join(a for a in args if not a.startswith(str(tmp_path)) and a != "--work"))
        if args[0] == "newtech" and "--learn" in args:
            learned.append(1)
            return 0
        ok = args[0] != "newtech" or bool(learned)
        if args[0] == "newtech" and not ok:
            (work / "reports" / "newtech.json").write_text(json.dumps({"new": {"m2.material_flags": ["2048"]}}))
        led[args[0]] = {"ok": ok}
        (work / "reports" / "steps.json").write_text(json.dumps(led))
        return 0 if ok else 1
    monkeypatch.setattr(kit, "_run", kit_step)
    kit.hooks["tables"] = lambda k: hooked.append(list(ran)) or True
    assert kit.build()
    assert ran[:4] == ["fetch", "newtech", "newtech --learn", "newtech"] and ran[-1] == "sheet"
    assert hooked == [ran[:ran.index("tables") + 1]]                     # right after the tables
    assert any("m2.material_flags 2048" in line and "learns it" in line for line in kit.lines)


def test_the_audit_says_which_piece_and_body_a_line_is_about(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    (work / "build").mkdir()
    plan = {"710295": {"items": [{"name": "Astral Gladiator's Chain Helm"}],
                       "row": {"model": ["Backport\\helm_mail_raidshaman_d_01__c532.mdx", ""], "tex": ["Backport\\7113806", ""]}},
            "751019": {"items": [{"name": "Some Helm"}], "row": {"model": ["", ""]}}}
    (work / "build" / "plan.json").write_text(json.dumps(plan))
    warn = "Item\\ObjectComponents\\Head\\Backport\\helm_mail_raidshaman_d_01__c532_PaM.m2: centroid 0.13 m off retail's"
    (work / "reports" / "audit.json").write_text(json.dumps({
        "notes": {K.PLACEMENT_WARNING: [warn, warn]},
        "fails": {"C1/C2 closure": ["Item\\x_BeF0060-00.anim missing (d751019 helm anim)"]}}))
    got = kit.audit_notes()
    assert got["warnings"] == [{"display": "710295", "name": "Astral Gladiator's Chain Helm", "lines": [warn],
                                "bodies": ["Pandaren male"], "gates": []}]
    assert got["fails"] == []                                             # the ledger says the audit passed
    (work / "reports" / "steps.json").write_text(json.dumps({"audit": {"ok": False}}))
    fails = kit.audit_notes()["fails"]
    assert [(f["display"], f["name"], f["gates"]) for f in fails] == [("751019", "Some Helm", ["C1/C2 closure"])]
    assert K.display_of("display 710295 (chest): retail 3D", plan) == "710295" and K.display_of("nothing", plan) == ""


def test_measuring_waits_for_something_worn_on_the_body(tmp_path):
    import types
    weapons = {"displays": {"1": {"slot": "mainhand"}, "2": {"slot": "held"}}}
    assert not L.wears_on_body(weapons) and L.wears_on_body({"displays": {"1": {"slot": "head"}}})
    (tmp_path / "selection.json").write_text(json.dumps(weapons))
    ran, said = [], []
    measure = types.SimpleNamespace(run=lambda prj, log, only=None: ran.append(only) or 0)
    bpcore = types.SimpleNamespace(load_json=lambda p, d=None: json.load(open(p)) if os.path.isfile(p) else d)
    prj = types.SimpleNamespace(path=lambda *parts: os.path.join(str(tmp_path), *parts))
    L.measure_when_worn(measure, bpcore)
    assert measure.run(prj, said.append) == 0 and ran == [] and "weapons only" in said[0]
    (tmp_path / "selection.json").write_text(json.dumps({"displays": {"1": {"slot": "chest"}}}))
    assert measure.run(prj, said.append) == 0 and ran == [None]
    assert not L.measured(str(tmp_path))
    (tmp_path / "client").mkdir()
    for n in ("helm_shift.json", "geoset_map.json"):
        (tmp_path / "client" / n).write_text("{}")
    assert L.measured(str(tmp_path))


def test_each_install_ticks_its_own_steps_and_measures_when_armor_comes(tmp_path, monkeypatch):
    kit, work, data = _project(tmp_path, monkeypatch)
    (work / "selection.json").write_text(json.dumps({"displays": {"1": {"slot": "chest"}}}))
    ran = []

    def kit_step(args, step="", local=None, cwd=None):
        ran.append(args[0])
        return 0
    monkeypatch.setattr(kit, "_run", kit_step)
    kit.job = K.Job("build")
    assert kit.build()                                         # every step passed before: none runs...
    assert ran == ["measure"]                                  # ...but measuring, which never measured
    assert kit.job.done == ["scan", *K.STEPS]
    (work / "client").mkdir()
    for n in ("helm_shift.json", "geoset_map.json"):
        (work / "client" / n).write_text("{}")
    kit.job = K.Job("build")
    assert kit.build() and ran == ["measure"] and kit.job.done == ["scan", *K.STEPS]
    assert kit.status()["measure"] == {"needed": True, "done": True}
