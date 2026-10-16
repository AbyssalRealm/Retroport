"""`install`: never overwrite a file changed since the build; back up, receipt, undo.
Also the cloak emblem-layer fixup."""

import json
import os
import struct

import pytest

from retroport.convert.fixup import strip_unfilled_texture_layers
from retroport.formats import structs as S
from retroport.race import install
from retroport.race.build import sha256


class _FakeClient:
    """Stands in for ClientData: the client loads `files[rel]` from `label`; `above` the files a
    patch outranking the install's patch holds (rel -> its label)."""
    files: dict = {}
    above: dict = {}

    def __init__(self, _client_dir, top=None):
        self.top = top

    def find(self, rel):
        data = self.files.get(rel.replace("/", "\\"))
        return ("patch-H (folder)", data) if data is not None else None

    def shadowed_by(self, rel):
        return self.above.get(rel.replace("/", "\\"))


def _package(tmp_path, bases):
    pkg = tmp_path / "pkg"
    (pkg / "client" / "DBFilesClient").mkdir(parents=True)
    (pkg / "client" / "Character" / "KulTiran" / "Male").mkdir(parents=True)
    (pkg / "server" / "dbc").mkdir(parents=True)
    (pkg / "client" / "DBFilesClient" / "ChrRaces.dbc").write_bytes(b"new chr")
    (pkg / "client" / "DBFilesClient" / "CreatureDisplayInfo.dbc").write_bytes(b"new cdi")
    (pkg / "client" / "Character" / "KulTiran" / "Male" / "KulTiranMale.m2").write_bytes(b"model v2")
    (pkg / "server" / "dbc" / "ChrRaces.dbc").write_bytes(b"new server chr")
    (pkg / "race.json").write_text(json.dumps({"race": "KulTiran", "bases": bases}))
    return pkg


def _client(tmp_path):
    root = tmp_path / "wow" / "Data" / "patch-H.MPQ"
    (root / "DBFilesClient").mkdir(parents=True)
    (root / "Character" / "KulTiran" / "Male").mkdir(parents=True)
    (root / "DBFilesClient" / "CreatureDisplayInfo.dbc").write_bytes(b"my cdi")
    (root / "Character" / "KulTiran" / "Male" / "KulTiranMale.m2").write_bytes(b"model v1")
    (root / "Character" / "KulTiran" / "Hair00_00.blp").write_bytes(b"old race-level hair")
    server = tmp_path / "server"
    server.mkdir()
    (server / "ChrRaces.dbc").write_bytes(b"server chr")
    return tmp_path / "wow", root, server


def test_a_file_a_higher_patch_holds_is_installed_and_reported_shadowed(tmp_path, monkeypatch):
    """The retail backport kit's patch-I.MPQ, made on top of ours, outranks patch-H: our Item
    tables go in all the same, the plan says the client loads the kit's copy until it is rebuilt."""
    wow, _root, _server = _client(tmp_path)
    pkg = _package(tmp_path, {"client/DBFilesClient/CreatureDisplayInfo.dbc": {"source": "patch-H",
                                                                             "sha256": sha256(b"my cdi")}})
    _FakeClient.files = {"DBFilesClient\CreatureDisplayInfo.dbc": b"my cdi"}
    _FakeClient.above = {"DBFilesClient\CreatureDisplayInfo.dbc": "patch-I.MPQ"}
    monkeypatch.setattr(install, "ClientData", _FakeClient)
    try:
        plan = install.plan_install(str(pkg), str(wow))
    finally:
        _FakeClient.above = {}
    kinds = {a.rel: a.kind for a in plan.actions}
    assert kinds["client/DBFilesClient/CreatureDisplayInfo.dbc"] == "replace" and not plan.blocked
    assert plan.shadowed == {"client/DBFilesClient/CreatureDisplayInfo.dbc": "patch-I.MPQ"}


def test_install_replaces_only_what_it_was_built_on(tmp_path, monkeypatch):
    wow, root, server = _client(tmp_path)
    bases = {"client/DBFilesClient/CreatureDisplayInfo.dbc": {"source": "patch-H", "sha256": sha256(b"my cdi")},
             "client/DBFilesClient/ChrRaces.dbc": {"source": "patch-enUS-3", "sha256": sha256(b"stock chr")},
             "server/dbc/ChrRaces.dbc": {"source": "server", "sha256": sha256(b"server chr")}}
    pkg = _package(tmp_path, bases)
    _FakeClient.files = {"DBFilesClient\\CreatureDisplayInfo.dbc": b"my cdi", "DBFilesClient\\ChrRaces.dbc": b"stock chr"}
    monkeypatch.setattr(install, "ClientData", _FakeClient)

    plan = install.plan_install(str(pkg), str(wow), server_dbc_dir=str(server), clean=True)
    kinds = {a.rel: a.kind for a in plan.actions}
    assert kinds["client/DBFilesClient/CreatureDisplayInfo.dbc"] == "replace"
    assert kinds["client/DBFilesClient/ChrRaces.dbc"] == "add"
    assert kinds["client/Character/KulTiran/Male/KulTiranMale.m2"] == "replace"
    assert kinds["client/Character/KulTiran/Hair00_00.blp"] == "leftover"
    assert kinds["server/dbc/ChrRaces.dbc"] == "replace"
    assert not plan.blocked

    receipt = install.apply_install(plan, clean=True)
    assert (root / "DBFilesClient" / "CreatureDisplayInfo.dbc").read_bytes() == b"new cdi"
    _FakeClient.files = {"DBFilesClient\\CreatureDisplayInfo.dbc": b"new cdi", "DBFilesClient\\ChrRaces.dbc": b"new chr"}
    again = install.plan_install(str(pkg), str(wow), server_dbc_dir=str(server))
    assert {a.kind for a in again.actions} == {"same"}                 # installed: nothing to do, nothing stale
    assert (server / "ChrRaces.dbc").read_bytes() == b"new server chr"
    assert not (root / "Character" / "KulTiran" / "Hair00_00.blp").exists()
    r = json.loads(open(receipt).read())
    assert open(r["replaced"][0]["backup"], "rb").read() in (b"my cdi", b"model v1", b"server chr")

    assert install.uninstall(receipt) == {"restored": 4, "removed": 1}
    assert (root / "DBFilesClient" / "CreatureDisplayInfo.dbc").read_bytes() == b"my cdi"
    assert (root / "Character" / "KulTiran" / "Hair00_00.blp").read_bytes() == b"old race-level hair"
    assert not (root / "DBFilesClient" / "ChrRaces.dbc").exists()
    assert (server / "ChrRaces.dbc").read_bytes() == b"server chr"


def test_install_refuses_files_changed_since_the_build(tmp_path, monkeypatch):
    wow, root, server = _client(tmp_path)
    bases = {"client/DBFilesClient/CreatureDisplayInfo.dbc": {"source": "patch-H", "sha256": sha256(b"older cdi")},
             "server/dbc/ChrRaces.dbc": {"source": "server", "sha256": sha256(b"older server chr")}}
    pkg = _package(tmp_path, bases)
    _FakeClient.files = {"DBFilesClient\\CreatureDisplayInfo.dbc": b"my cdi"}     # customized after the build
    monkeypatch.setattr(install, "ClientData", _FakeClient)
    plan = install.plan_install(str(pkg), str(wow), server_dbc_dir=str(server))
    assert {a.rel for a in plan.blocked} == {"client/DBFilesClient/CreatureDisplayInfo.dbc", "server/dbc/ChrRaces.dbc"}
    with pytest.raises(ValueError, match="block the install"):
        install.apply_install(plan)
    assert (root / "DBFilesClient" / "CreatureDisplayInfo.dbc").read_bytes() == b"my cdi"
    assert not os.path.exists(pkg / "installs")


def test_uninstall_refuses_after_later_edits(tmp_path, monkeypatch):
    wow, root, server = _client(tmp_path)
    pkg = _package(tmp_path, {"client/DBFilesClient/CreatureDisplayInfo.dbc":
                              {"source": "patch-H", "sha256": sha256(b"my cdi")}})
    _FakeClient.files = {"DBFilesClient\\CreatureDisplayInfo.dbc": b"my cdi"}
    monkeypatch.setattr(install, "ClientData", _FakeClient)
    receipt = install.apply_install(install.plan_install(str(pkg), str(wow)))
    (root / "DBFilesClient" / "ChrRaces.dbc").write_bytes(b"edited after install")
    with pytest.raises(ValueError, match="changed after the install"):
        install.uninstall(receipt)


def test_the_races_own_helmets_are_race_files(tmp_path, monkeypatch):
    wow, root, server = _client(tmp_path)
    pkg = _package(tmp_path, {})
    meta = json.loads((pkg / "race.json").read_text())
    (pkg / "race.json").write_text(json.dumps({**meta, "helmet_prefix": "Kt"}))
    head = pkg / "client" / "Item" / "ObjectComponents" / "Head"
    head.mkdir(parents=True)
    (head / "Helm_A_KtM.m2").write_bytes(b"helm v2")
    (head / "Helm_A_KtM00.skin").write_bytes(b"skin v2")
    (head / "Helm_A_HuM.m2").write_bytes(b"not ours")
    installed = root / "Item" / "ObjectComponents" / "Head"
    installed.mkdir(parents=True)
    (installed / "Helm_A_KtM.m2").write_bytes(b"helm v1")            # an earlier build's
    (installed / "Helm_A_HuM.m2").write_bytes(b"Human helm")         # the client's own
    (installed / "Helm_Gone_KtF00.skin").write_bytes(b"old")         # an earlier build's, not in this one
    (installed / "Helm_Gone_HuF00.skin").write_bytes(b"theirs")
    _FakeClient.files = {}
    monkeypatch.setattr(install, "ClientData", _FakeClient)
    plan = install.plan_install(str(pkg), str(wow), clean=True)
    kinds = {a.rel: a.kind for a in plan.actions}
    assert kinds["client/Item/ObjectComponents/Head/Helm_A_KtM.m2"] == "replace"
    assert kinds["client/Item/ObjectComponents/Head/Helm_A_KtM00.skin"] == "add"
    assert kinds["client/Item/ObjectComponents/Head/Helm_A_HuM.m2"] == "conflict"
    assert kinds["client/Item/ObjectComponents/Head/Helm_Gone_KtF00.skin"] == "leftover"
    assert "client/Item/ObjectComponents/Head/Helm_Gone_HuF00.skin" not in kinds


def test_the_races_select_scene_folder_is_its_own(tmp_path, monkeypatch):
    wow, root, server = _client(tmp_path)
    pkg = _package(tmp_path, {})
    scene = pkg / "client" / "Interface" / "Glues" / "Models" / "UI_KulTiran"
    scene.mkdir(parents=True)
    (scene / "UI_KulTiran.m2").write_bytes(b"docks v2")
    other = pkg / "client" / "Interface" / "Glues" / "Models" / "UI_Human"
    other.mkdir(parents=True)
    (other / "UI_Human.m2").write_bytes(b"not ours")
    installed = root / "Interface" / "Glues" / "Models"
    (installed / "UI_KulTiran").mkdir(parents=True)
    (installed / "UI_KulTiran" / "UI_KulTiran.m2").write_bytes(b"docks v1")
    (installed / "UI_KulTiran" / "old_rope.blp").write_bytes(b"an earlier build's")
    (installed / "UI_Human").mkdir(parents=True)
    (installed / "UI_Human" / "UI_Human.m2").write_bytes(b"the client's")
    _FakeClient.files = {}
    monkeypatch.setattr(install, "ClientData", _FakeClient)
    kinds = {a.rel: a.kind for a in install.plan_install(str(pkg), str(wow), clean=True).actions}
    assert kinds["client/Interface/Glues/Models/UI_KulTiran/UI_KulTiran.m2"] == "replace"
    assert kinds["client/Interface/Glues/Models/UI_KulTiran/old_rope.blp"] == "leftover"
    assert kinds["client/Interface/Glues/Models/UI_Human/UI_Human.m2"] == "conflict"


def test_what_an_earlier_install_added_and_the_package_no_longer_holds_is_moved_out(tmp_path, monkeypatch):
    wow, root, server = _client(tmp_path)
    pkg = _package(tmp_path, {"client/DBFilesClient/CreatureDisplayInfo.dbc":
                              {"source": "patch-H", "sha256": sha256(b"my cdi")}})
    _FakeClient.files = {"DBFilesClient\\CreatureDisplayInfo.dbc": b"my cdi"}
    monkeypatch.setattr(install, "ClientData", _FakeClient)
    folder = pkg / "client" / "Item" / "TextureComponents" / "TorsoLowerTexture"
    folder.mkdir(parents=True)
    (folder / "Shirt_TL_U.blp").write_bytes(b"fixed shirt")
    (folder / "Robe_TL_U.blp").write_bytes(b"fixed robe")
    first = install.apply_install(install.plan_install(str(pkg), str(wow)))
    installed = root / "Item" / "TextureComponents" / "TorsoLowerTexture"
    assert (installed / "Shirt_TL_U.blp").read_bytes() == b"fixed shirt"
    _FakeClient.files = {"DBFilesClient\\CreatureDisplayInfo.dbc": b"new cdi"}
    (installed / "Robe_TL_U.blp").write_bytes(b"robe the user edited since")
    for f in folder.iterdir():                                          # the next build makes none
        f.unlink()
    plan = install.plan_install(str(pkg), str(wow), clean=True)
    kinds = {a.rel: a.kind for a in plan.actions}
    assert kinds["client/Item/TextureComponents/TorsoLowerTexture/Shirt_TL_U.blp"] == "leftover"
    assert "client/Item/TextureComponents/TorsoLowerTexture/Robe_TL_U.blp" not in kinds   # changed: theirs now
    second = install.apply_install(plan, clean=True)
    assert not (installed / "Shirt_TL_U.blp").exists() and (installed / "Robe_TL_U.blp").exists()
    install.uninstall(second)
    assert (installed / "Shirt_TL_U.blp").read_bytes() == b"fixed shirt"
    assert json.loads(open(first).read())["added"]


def test_a_file_added_then_replaced_is_known_by_its_last_copy_however_its_path_was_written(tmp_path):
    # the install that added AnimationData.dbc wrote F:/WoW\Data\..., the one that replaced it F:\WoW\Data\...
    (tmp_path / "installs").mkdir()
    (tmp_path / "installs" / "1.json").write_text(json.dumps(
        {"added": [{"target": "F:/WoW\\Data\\patch-H.MPQ\\DBFilesClient\\AnimationData.dbc", "sha256": "first"}]}))
    (tmp_path / "installs" / "2.json").write_text(json.dumps(
        {"replaced": [{"target": "F:\\WoW\\Data\\patch-H.MPQ\\DBFilesClient\\AnimationData.dbc", "sha256": "second"}]}))
    assert install._added_earlier(str(tmp_path)) == {
        os.path.normpath("F:/WoW/Data/patch-H.MPQ/DBFilesClient/AnimationData.dbc"): "second"}


# --------------------------------------------------------------------------- cloak emblem layer

def test_strip_unfilled_texture_layers(tmp_path):
    m = bytearray(0x138)
    m[0:4] = b"MD20"
    struct.pack_into("<I", m, 4, 264)
    to = len(m)
    m += struct.pack("<4I", 2, 0, 0, 0) + struct.pack("<4I", 11, 0, 0, 0)      # cape, empty creature skin
    S.write_m2array(m, S.HDR_TEXTURES, 2, to)
    lo = len(m)
    m += struct.pack("<2h", 0, 1)
    S.write_m2array(m, S.HDR_TEXTURE_LOOKUP, 2, lo)
    (tmp_path / "c.m2").write_bytes(bytes(m))
    s = bytearray(0x30)
    s[0:4] = b"SKIN"
    bo = len(s)
    for count in (2, 1):
        rec = bytearray(24)
        struct.pack_into("<HH", rec, 0x0E, count, 0)
        s += rec
    S.write_m2array(s, S.SKIN_BATCHES, 2, bo)
    (tmp_path / "c00.skin").write_bytes(bytes(s))
    assert strip_unfilled_texture_layers(str(tmp_path / "c.m2"), [str(tmp_path / "c00.skin")]) == 1
    out = (tmp_path / "c00.skin").read_bytes()
    assert [struct.unpack_from("<H", out, bo + i * 24 + 0x0E)[0] for i in range(2)] == [1, 1]
    assert strip_unfilled_texture_layers(str(tmp_path / "c.m2"), [str(tmp_path / "c00.skin")]) == 0
