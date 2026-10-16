"""The workbench on anyone's machine: game data from wago.tools, the kit and Equip Plus fetched from
their GitHub, the kit shown open-folder patches, settings with their defaults."""

import io
import json
import os
import types
import zipfile

import pytest

from retroport.workbench import catalog as C
from retroport.workbench import deps as D
from retroport.workbench import kit as K
from retroport.workbench import kitlocal as L
from retroport.workbench import server as SV
from retroport.workbench import wago as W


# -- wago.tools -----------------------------------------------------------------------------
def test_wagos_csv_reads_as_the_local_readers_rows():
    text = ('ID,Name_lang,Flags,Scale,ModelResourcesID_0,ModelResourcesID_1,Offset_0,Offset_1,Field_9_1_0_38312_030\n'
            '7,"Season 2",3,0.5,100,0,0,1.25,4\n'
            '9,1234,0,2,0,0,0,0,0\n')
    rows = W.rows_from_csv(text)
    assert [i for i, _r in rows] == [7, 9]
    r = rows[0][1]
    assert r == {"ID": 7, "Name_lang": "Season 2", "Flags": 3, "Scale": 0.5, "ModelResourcesID": [100, 0],
                 "Offset": [0.0, 1.25], "Field_9_1_0_38312_030": 4}
    assert rows[1][1]["Name_lang"] == "1234" and isinstance(rows[1][1]["Offset"][0], float)   # text stays text
    # a table keyed by its file (ModelFileData): no ID column, the FileDataID is the row's id
    assert W.rows_from_csv("FileDataID,ModelResourcesID\n116902,18251\n") == [(116902, {"FileDataID": 116902,
                                                                                          "ModelResourcesID": 18251})]


def test_a_wago_source_keeps_what_it_fetched_and_knows_missing_and_encrypted_files(tmp_path):
    asked = []
    answers = {"db2/ItemSet": b"ID,Name_lang\n1,\"The Gladiator\"\n", "casc/5?": b"BLP2", "casc/6?": b"\0" * 64}

    def fetch(url, dest):
        asked.append(url)
        for k, v in answers.items():
            if k in url:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                open(dest, "wb").write(v)
                return
        raise W.WagoMissing("no such file")
    spec = C.SourceSpec("retail", "Retail", kind="wago", build="12.1.5.70391")
    src = W.WagoSource(spec, str(tmp_path), fetch)
    assert src.table("ItemSet") == [(1, {"ID": 1, "Name_lang": "The Gladiator"})]
    assert src.read(5) == b"BLP2" and src.read(5) == b"BLP2"
    with pytest.raises(W.WagoError, match="encrypted"):
        src.read(6)
    for _ in range(2):
        with pytest.raises(LookupError):
            src.read(7)                                                  # not in this build
    assert sum("casc/7?" in u for u in asked) == 1                       # ...asked once, remembered
    assert sum("db2/ItemSet" in u for u in asked) == 1
    src.table("ItemSet")
    assert sum("db2/ItemSet" in u for u in asked) == 1                   # kept on disk


def test_the_newest_build_is_asked_for_now_and_then_and_remembered_while_wago_is_down(tmp_path):
    def fetch(url, dest):
        open(dest, "w").write(json.dumps({"wow": [{"version": "12.1.5.70391"}], "wowt": [{"version": "12.1.7.1"}]}))
    assert W.latest_build(str(tmp_path), "wow", fetch) == "12.1.5.70391"

    def down(url, dest):
        raise W.WagoError("down")
    assert W.latest_build(str(tmp_path), "wow", down, max_age=0) == "12.1.5.70391"


# -- the kit and Equip Plus from GitHub ------------------------------------------------------
def _archive(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_a_github_archive_unpacks_without_its_top_folder_and_never_outside(tmp_path):
    D.unpack(_archive({"repo-abc/": b"", "repo-abc/tools/a.py": b"print(1)", "repo-abc/README.md": b"hi"}), str(tmp_path / "x"))
    assert (tmp_path / "x" / "tools" / "a.py").read_bytes() == b"print(1)"
    with pytest.raises(RuntimeError, match="outside"):
        D.unpack(_archive({"repo-abc/../../evil.py": b"x"}), str(tmp_path / "y"))
    assert not (tmp_path / "evil.py").exists()


def test_the_kit_is_fetched_once_at_its_pinned_commit(tmp_path):
    got = []

    def get(url, dest):
        got.append(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(_archive({"r-1/wow-retail-backport/scripts/bp.py": b"# kit"}))
    path = D.fetch(str(tmp_path), "kit", say=lambda m: None, get=get)
    assert open(path).read() == "# kit" and D.have(str(tmp_path), "kit")
    D.fetch(str(tmp_path), "kit", say=lambda m: None, get=get)
    assert got == [f"https://codeload.github.com/Kirazul/wow-retail-backport/zip/{D.PINNED['kit']['ref']}"]


def test_without_the_backport_helper_the_kit_uses_the_workbenchs_own_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "HELPER_SETTINGS", str(tmp_path / "none.json"))
    monkeypatch.setattr(K, "REPO", str(tmp_path / "no-repo"))
    data = str(tmp_path / "wb")
    kit = K.Kit({}, None, "", "", data)
    assert kit.cfg.bp == D.entry(data, "kit") and kit.cfg.work == os.path.join(data, "backport-project")
    assert kit.fetched("kit") and kit.fetched("equip-plus")
    assert not any("bp.py" in p for p in kit.problems())                 # Install fetches it first


# -- open-folder patches ---------------------------------------------------------------------
def test_the_kit_is_shown_open_folder_patches(tmp_path):
    folder = tmp_path / "Data" / "patch-Z.MPQ" / "Item" / "Objects"
    folder.mkdir(parents=True)
    (folder / "a.m2").write_bytes(b"MD20")
    opened = []
    client = types.SimpleNamespace(os=os, MpqReader=lambda p: opened.append(p) or "mpq")
    assert L.show_folder_patches(client)
    patch = str(tmp_path / "Data" / "patch-Z.MPQ")
    assert client.os.path.isfile(patch) and not client.os.path.isfile(str(tmp_path / "Data"))
    arc = client.MpqReader(patch)
    assert arc.read("item\\objects\\A.M2") == b"MD20" and arc.read("(listfile)") == b"Item\\Objects\\a.m2"
    assert client.MpqReader(str(tmp_path / "x.MPQ")) == "mpq" and opened == [str(tmp_path / "x.MPQ")]
    assert not L.show_folder_patches(types.SimpleNamespace(FolderArchive=object))   # a kit that knows them


# -- settings --------------------------------------------------------------------------------
def test_fresh_settings_read_retail_and_bfas_bodies_from_wago():
    st = SV.Settings()
    specs = {s.key: s for s in st.effective_sources()}
    assert specs["retail"].kind == "wago" and specs["retail"].browse
    assert specs["bfa"].kind == "wago" and specs["bfa"].build == "8.3.7.35435" and not specs["bfa"].browse
    assert st.problems() == ["choose your 3.3.5 client folder in Settings"]     # wago needs no DBDefs or listfile
    st.set_source("retail", "Retail", "X:\\nowhere", product="wow")
    assert any("not a game install" in p for p in st.problems())
