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
    """Stands in for ClientData: the client loads `files[rel]` from `label`."""
    files: dict = {}

    def __init__(self, _client_dir):
        pass

    def find(self, rel):
        data = self.files.get(rel.replace("/", "\\"))
        return ("patch-H (folder)", data) if data is not None else None


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
