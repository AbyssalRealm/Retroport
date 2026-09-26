"""Native texture baking: TXID + listfile resolution, path rules, verified bake."""

import struct

import pytest

from retroport.convert import texbake
from retroport.formats import structs as S
from retroport.formats.md20 import bake_texture_paths, read_texture_slots


def build_md20_textures(slots):
    """MD20 whose texture slots are [(type, name)], names stored after the records."""
    b = bytearray(0x138)
    b[0:4] = b"MD20"
    struct.pack_into("<I", b, S.MD20_VERSION, 264)
    tex_off = len(b)
    recs = bytearray(len(slots) * S.M2TEXTURE)
    names = bytearray()
    for i, (ttype, name) in enumerate(slots):
        struct.pack_into("<II", recs, i * S.M2TEXTURE, ttype, 0)
        if name:
            raw = name.encode() + b"\x00"
            S.write_m2array(recs, i * S.M2TEXTURE + S.TEXTURE_FILENAME, len(raw),
                            tex_off + len(recs) + len(names))
            names += raw
    b += recs + names
    S.write_m2array(b, S.HDR_TEXTURES, len(slots), tex_off)
    return bytes(b)


def build_md21_with_txid(fdids):
    md20 = bytearray(0x138)
    md20[0:4] = b"MD20"
    txid = struct.pack(f"<{len(fdids)}I", *fdids)
    return (b"MD21" + struct.pack("<I", len(md20)) + bytes(md20)
            + b"TXID" + struct.pack("<I", len(txid)) + txid)


SLOTS = [(0, ""), (1, ""), (0, "Item\\Existing\\keep.blp"), (0, "")]


def test_ingame_path_rules():
    ok = "Item\\ObjectComponents\\Weapon\\sword_1h.blp"
    assert texbake.ingame_path_problem(ok) == ""
    assert "your computer" in texbake.ingame_path_problem("I:\\export\\sword.blp")
    assert "UNC" in texbake.ingame_path_problem("\\\\server\\share\\a.blp")
    assert "forward slashes" in texbake.ingame_path_problem("Item/Weapon/a.blp")
    assert ".blp" in texbake.ingame_path_problem("Item\\Weapon\\a.png")


def test_read_txid(tmp_path):
    p = tmp_path / "m.m2"
    p.write_bytes(build_md21_with_txid([111, 0, 333]))
    assert texbake.read_txid(str(p)) == [111, 0, 333]
    p.write_bytes(build_md20_textures(SLOTS))
    assert texbake.read_txid(str(p)) == []                   # MD20 has no chunks


def test_resolve_fdids_streams_and_overrides_win(tmp_path):
    lf = tmp_path / "listfile.csv"
    lf.write_text("111;item/weapon/a.blp\n222;item/weapon/b.blp\n999;other.blp\n")
    ov = tmp_path / "overrides.csv"
    ov.write_text("# comment\n222,Item\\Weapon\\b_fixed.blp\n")
    found = texbake.resolve_fdids([111, 222, 404], str(lf), str(ov))
    assert found == {111: "item\\weapon\\a.blp", 222: "Item\\Weapon\\b_fixed.blp"}


def test_plan_bake_only_touches_empty_type0_slots():
    md20 = build_md20_textures(SLOTS)
    plan = texbake.plan_bake(md20, [111, 0, 222, 444], {111: "Item\\A\\a.blp", 444: "Item\\D\\d.blp"})
    assert plan.paths == {0: "Item\\A\\a.blp", 3: "Item\\D\\d.blp"}   # slot 1 runtime, slot 2 already named
    assert plan.fdids == {0: 111, 3: 444}

    plan = texbake.plan_bake(md20, [111, 0, 222, 444], {111: "Item\\A\\a.blp"},
                             explicit={3: "Creature\\Mine\\d.blp"})
    assert plan.paths[3] == "Creature\\Mine\\d.blp" and plan.unresolved == []

    plan = texbake.plan_bake(md20, [], {})
    assert plan.no_id == [0, 3] and not plan.paths


def test_bake_file_verifies_and_never_touches_other_slots(tmp_path):
    p = tmp_path / "m.m2"
    p.write_bytes(build_md20_textures(SLOTS))
    out = tmp_path / "baked.m2"
    texbake.bake_file(str(p), {0: "Item\\A\\a.blp", 1: "Creature\\X\\skin.blp"}, str(out))

    slots = read_texture_slots(out.read_bytes())
    assert (slots[0].type, slots[0].name) == (0, "Item\\A\\a.blp")
    assert (slots[1].type, slots[1].name) == (0, "Creature\\X\\skin.blp")   # baked -> type 0
    assert (slots[2].type, slots[2].name) == (0, "Item\\Existing\\keep.blp")
    assert slots[3].name == ""
    assert p.read_bytes() == build_md20_textures(SLOTS)                       # input untouched


def test_bake_file_refuses_bad_paths_before_writing(tmp_path):
    p = tmp_path / "m.m2"
    original = build_md20_textures(SLOTS)
    p.write_bytes(original)
    with pytest.raises(ValueError, match="slot 0.*your computer"):
        texbake.bake_file(str(p), {0: "I:\\wowexport\\a.blp"})
    assert p.read_bytes() == original


def test_bake_needs_md20():
    with pytest.raises(ValueError, match="MD20"):
        bake_texture_paths(build_md21_with_txid([1]), {0: "Item\\A\\a.blp"})
    with pytest.raises(ValueError, match="out of range"):
        bake_texture_paths(build_md20_textures(SLOTS), {9: "Item\\A\\a.blp"})
