"""Loader tests - manifest resolution (the wow.export dependency map) and the
directory FileDataID scanner.
"""

import json
import os

from retroport.convert.loaders import make_manifest_loader, find_manifest, make_file_id_loader


def _write(path, content=b"x"):
    with open(path, "wb") as fh:
        fh.write(content)


def test_manifest_loader_resolves_by_filedataid(tmp_path):
    # mimic wow.export: files named in-game, a manifest mapping ids -> those names
    manifest = {
        "fileDataID": 1721003,
        "skeleton": {"fileDataID": 2175552, "file": "kultiranmale.skel"},
        "skelAnims": [
            {"fileDataID": 1721004, "file": "kultiranmale0061-00.anim", "animID": 61, "subAnimID": 0},
            {"fileDataID": 1721005, "file": "kultiranmale0096-00.anim", "animID": 96, "subAnimID": 0},
        ],
        "skelBones": [{"fileDataID": 2829521, "file": "kultiranmale_02.bone"}],
    }
    mpath = tmp_path / "kultiranmale.manifest.json"
    mpath.write_text(json.dumps(manifest))
    _write(tmp_path / "kultiranmale.skel", b"SKEL-BYTES")
    _write(tmp_path / "kultiranmale0061-00.anim", b"ANIM-61")
    _write(tmp_path / "kultiranmale0096-00.anim", b"ANIM-96")

    loader = make_manifest_loader(str(mpath))
    assert loader(2175552) == b"SKEL-BYTES"     # the id that failed by name-guessing
    assert loader(1721004) == b"ANIM-61"
    assert loader(1721005) == b"ANIM-96"
    assert loader(999999) is None               # not in manifest
    assert loader(2829521) is None              # listed but file not on disk


def test_find_manifest_next_to_model(tmp_path):
    _write(tmp_path / "kultiranmale.m2")
    mpath = tmp_path / "kultiranmale.manifest.json"
    mpath.write_text("{}")
    assert find_manifest(str(tmp_path / "kultiranmale.m2")) == str(mpath)


def test_find_manifest_glob_fallback(tmp_path):
    _write(tmp_path / "model.m2")
    mpath = tmp_path / "someexport.manifest.json"   # name doesn't match the m2 stem
    mpath.write_text("{}")
    assert find_manifest(str(tmp_path / "model.m2")) == str(mpath)


def test_find_manifest_absent(tmp_path):
    _write(tmp_path / "model.m2")
    assert find_manifest(str(tmp_path / "model.m2")) is None


def test_file_id_loader_directory_scan(tmp_path):
    _write(tmp_path / "2175552.skel", b"BY-ID")
    loader = make_file_id_loader(str(tmp_path), ".skel")
    assert loader(2175552) == b"BY-ID"
    assert loader(123) is None
