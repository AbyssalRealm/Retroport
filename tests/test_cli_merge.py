"""CLI veneer for the merged Conjure features: `bake` and `register` (JSON contract)."""

import json

from typer.testing import CliRunner

from retroport.cli import app
from retroport.formats.md20 import read_texture_slots

from test_dbc import _creature_dbcs
from test_texbake import SLOTS, build_md20_textures, build_md21_with_txid

runner = CliRunner()


def test_cli_bake_auto_and_explicit(tmp_path):
    model = tmp_path / "sword.m2"; model.write_bytes(build_md20_textures(SLOTS))
    src = tmp_path / "modern.m2"; src.write_bytes(build_md21_with_txid([111, 0, 0, 0]))
    lf = tmp_path / "listfile.csv"; lf.write_text("111;Item\\A\\a.blp\n")
    out = tmp_path / "baked"
    r = runner.invoke(app, ["bake", str(model), "--source-m2", str(src), "--listfile", str(lf),
                            "--slot", "3=Item\\D\\d.blp", "--out", str(out), "--json"])
    assert r.exit_code == 0, r.output
    env = json.loads(r.output)
    assert env["ok"] and env["result"]["output"].endswith("sword.m2")
    slots = read_texture_slots((out / "sword.m2").read_bytes())
    assert slots[0].name == "Item\\A\\a.blp" and slots[3].name == "Item\\D\\d.blp"


def test_cli_bake_refuses_disk_path_and_own_folder(tmp_path):
    model = tmp_path / "sword.m2"; model.write_bytes(build_md20_textures(SLOTS))
    r = runner.invoke(app, ["bake", str(model), "--slot", "0=C:\\export\\a.blp", "--json"])
    assert r.exit_code == 1 and "your computer" in json.loads(r.output)["error"]
    r = runner.invoke(app, ["bake", str(model), "--slot", "0=Item\\A\\a.blp", "--out", str(tmp_path), "--json"])
    assert r.exit_code == 2 and "own folder" in json.loads(r.output)["error"]


def test_cli_register_json(tmp_path):
    dbc_dir = _creature_dbcs(tmp_path)
    r = runner.invoke(app, ["register", "--dbc-dir", str(dbc_dir), "--out", str(tmp_path / "out"),
                            "--path", "Creature\\Foo\\foo.m2", "--texvar", "a,b", "--entries", "42", "--json"])
    assert r.exit_code == 0, r.output
    res = json.loads(r.output)["result"]
    assert res["display_id"] == 21 and res["model_id"] == 11
    assert "WHERE CreatureID IN (42)" in res["sql"]
