"""Safe-write layer: atomic verified writes, and fixups that refuse to corrupt."""

import struct

import pytest

from retroport.convert import fixup
from retroport.formats import structs as S
from retroport.formats.verify import VerifyError, m2_problems, no_new_problems, skin_problems
from retroport.safeio import write_verified

from test_check import build_md20, build_skin


def test_write_verified_keeps_old_file_when_verify_fails(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"original")

    def reject(_path):
        raise VerifyError("nope")

    with pytest.raises(VerifyError):
        write_verified(str(p), b"new data", reject)
    assert p.read_bytes() == b"original"                     # untouched
    assert [x.name for x in tmp_path.iterdir()] == ["f.bin"]  # no temp file left behind

    write_verified(str(p), b"new data", lambda _p: None)
    assert p.read_bytes() == b"new data"


def test_problem_detectors():
    assert m2_problems(b"junk") == {"not an M2 (no MD20/MD21)"}
    good = bytearray(build_skin())
    assert skin_problems(bytes(good)) == set()
    # point batch 0 at a submesh that doesn't exist
    _cnt, b_off = S.read_m2array(good, S.SKIN_BATCHES)
    struct.pack_into("<H", good, b_off + 0x04, 99)
    assert "a batch references a submesh that does not exist" in skin_problems(bytes(good))


def test_verifier_tolerates_preexisting_problems(tmp_path):
    # build_md20's fixture arrays (bones/vertices at offset 0) are already out of range;
    # an edit that leaves them alone must still pass.
    data = build_md20()
    assert m2_problems(data)                                 # baseline has problems
    p = tmp_path / "m.m2"; p.write_bytes(data)
    no_new_problems(data, m2_problems)(str(p))               # same problems -> OK

    broken = bytearray(data)
    S.write_m2array(broken, S.HDR_TEXTURES, 9999, len(broken))
    p.write_bytes(bytes(broken))
    with pytest.raises(VerifyError, match="textures"):
        no_new_problems(data, m2_problems)(str(p))


def test_fixups_still_write_through_verification(tmp_path):
    m2 = tmp_path / "model.m2"; m2.write_bytes(build_md20())      # combiner flag 0x08 set
    skin = tmp_path / "model00.skin"; skin.write_bytes(build_skin())
    cleared, _changed = fixup.clear_combiner_combos(str(m2), [str(skin)])
    assert cleared
    flags = S.read_u32(m2.read_bytes(), S.HDR_GLOBAL_FLAGS)
    assert not flags & S.FLAG_USE_COMBINER_COMBOS
