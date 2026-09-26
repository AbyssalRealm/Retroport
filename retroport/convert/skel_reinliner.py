"""`.skel` re-inliner (Milestones A + B).

Turns a Shadowlands-TWW `.skel`-based M2 into a self-contained M2 that
MultiConverter can then downgrade to MD20. Nothing off-the-shelf does this.

Milestone A - structural re-inline:
  * relocate SKB1/SKA1/SKS1 raw regions into the MD21 data block,
  * populate the MD20 header arrays (bones, key_bone_lookup, attachments,
    attachment_lookup, sequences, global_loops, sequence_lookups, name),
  * rebase chunk-relative offsets, including each bone/attachment M2Track's
    nested per-animation keyframe arrays,
  * drop SKID; keep BFID; regenerate the model name.

Milestone B - animation pass (when an ``anim_loader`` is supplied):
  * for every sequence that has an external `.anim` file (an AFID entry),
    pull its AFSB (bone) / AFSA (attachment) keyframe data into the M2 and
    rebase the corresponding external track offsets to point at it.
  * result: a fully inline model with no `.anim`/`.skel` dependency.

The rebasing is exact because every region is copied verbatim: an offset that
was relative to a chunk's content maps to ``new_base + offset``. Whether a track
array is inline (in SKB1) or external (in a `.anim` AFSB) is decided per sequence
by AFID membership - authoritative, not a guess. Without sequence info (only in
degenerate inputs) it falls back to a range check against the source region.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from ..models import ConversionResult, Finding, Severity, ValidationReport
from ..formats import structs as S
from ..formats.m2 import M2File, M2Error
from ..formats.skel import ArrayRef, SkelData, parse_skel
from ..formats.anim import parse_anim

# header field -> record size, for the post-relocation bounds check
_HEADER_RECORD_SIZES = {
    S.HDR_GLOBAL_LOOPS: S.M2LOOP,
    S.HDR_SEQUENCES: S.M2SEQUENCE,
    S.HDR_SEQUENCE_LOOKUPS: 2,
    S.HDR_BONES: S.M2COMPBONE,
    S.HDR_KEY_BONE_LOOKUP: 2,
    S.HDR_ATTACHMENTS: S.M2ATTACHMENT,
    S.HDR_ATTACHMENT_LOOKUP: 2,
}


@dataclass
class ReinlineResult:
    success: bool = False
    fully_inline: bool = False
    external_tracks: int = 0       # external arrays left unresolved (no .anim data)
    inlined_anims: int = 0         # .anim files pulled in during the animation pass
    marked_internal: int = 0       # sequences flagged 0x20 (internal storage)
    missing_anims: list[int] = field(default_factory=list)  # file ids that wouldn't load
    bones: int = 0
    attachments: int = 0
    sequences: int = 0
    parent_skel_file_id: int | None = None
    findings: list[Finding] = field(default_factory=list)

    def report(self, asset: str) -> ValidationReport:
        return ValidationReport(asset=asset, findings=self.findings)


# --- small parsers ----------------------------------------------------------
def _parse_sequences(skel: SkelData) -> list[tuple[int, int]]:
    """Return [(animation_id, variation_index), ...] from SKS1, in index order."""
    out: list[tuple[int, int]] = []
    if skel.sequences and skel.sequences.count and skel.sks1:
        c = skel.sks1
        base = skel.sequences.offset
        for i in range(skel.sequences.count):
            off = base + i * S.M2SEQUENCE
            if off + 4 <= len(c):
                out.append(struct.unpack_from("<HH", c, off))
    return out


def _parse_afid(content: bytes) -> list[tuple[int, int, int]]:
    out = []
    for i in range(0, len(content) - 7, 8):
        out.append(struct.unpack_from("<HHI", content, i))
    return out


def _afid_map(skel: SkelData, m2_afid: bytes | None) -> dict[tuple[int, int], int]:
    """(animation_id, variation_index) -> .anim FileDataID."""
    m: dict[tuple[int, int], int] = {}
    for aid, sub, fid in skel.afid:
        if fid:
            m[(aid, sub)] = fid
    if m2_afid:
        for aid, sub, fid in _parse_afid(m2_afid):
            if fid:
                m.setdefault((aid, sub), fid)
    return m


# --- offset placement -------------------------------------------------------
def _make_placer(region_base: int, region_len: int, ext_base: dict[int, int],
                 sequences: list[tuple[int, int]], afid_map: dict[tuple[int, int], int]):
    """Return ``place(anim_index, inner_offset) -> new_abs_offset | None``.

    Inline arrays rebase into the relocated structural region; external arrays
    rebase into the inlined `.anim` region for that animation, or return None
    (leave + count) when its `.anim` data was not available.
    """
    def place(anim_index: int, irel: int):
        seq = sequences[anim_index] if 0 <= anim_index < len(sequences) else None
        if seq is not None:
            external = seq in afid_map
        else:
            external = not (0 <= irel < region_len)   # fallback when no sequence info
        if not external:
            return region_base + irel
        base = ext_base.get(anim_index)
        return None if base is None else base + irel
    return place


def _rebase_track(md20: bytearray, track_abs: int, region_base: int, region_len: int,
                  place, findings: list[Finding]) -> int:
    """Rebase one M2Track's nested arrays. Returns count of unresolved externals."""
    unresolved = 0
    for field_off in (S.TRACK_TIMESTAMPS, S.TRACK_VALUES):
        cnt, rel = S.read_m2array(md20, track_abs + field_off)
        if cnt == 0:
            continue
        # The outer array (per-animation inner-array list) is always in-region.
        if rel < 0 or rel + cnt * S.M2ARRAY > region_len:
            findings.append(Finding(check="track_outer_oob", severity=Severity.FAIL,
                                    detail=f"track outer array (count {cnt}, off {rel}) escapes region"))
            return unresolved
        outer_abs = region_base + rel
        S.write_m2array(md20, track_abs + field_off, cnt, outer_abs)
        for i in range(cnt):
            inner_abs = outer_abs + i * S.M2ARRAY
            icnt, irel = S.read_m2array(md20, inner_abs)
            if icnt == 0:
                continue
            target = place(i, irel)
            if target is None:
                unresolved += 1
            else:
                S.write_m2array(md20, inner_abs, icnt, target)
    return unresolved


def _rebase_bone(md20: bytearray, bone_abs: int, region_base: int, region_len: int,
                 place, findings: list[Finding]) -> int:
    ext = 0
    for track_off in (S.BONE_TRANSLATION, S.BONE_ROTATION, S.BONE_SCALE):
        ext += _rebase_track(md20, bone_abs + track_off, region_base, region_len, place, findings)
    return ext


def _set_array(m2: M2File, field_off: int, ref: ArrayRef | None, base: int) -> None:
    if ref is None or ref.count == 0:
        m2.set_header_array(field_off, 0, 0)
    else:
        m2.set_header_array(field_off, ref.count, base + ref.offset)


def _verify(md20: bytearray, findings: list[Finding]) -> bool:
    ok = True
    n = len(md20)
    for field_off, rec in _HEADER_RECORD_SIZES.items():
        cnt, off = S.read_m2array(md20, field_off)
        if cnt and (off >= n or off + cnt * rec > n):
            findings.append(Finding(check="header_array_oob", severity=Severity.FAIL,
                                    detail=f"array @0x{field_off:X}: {cnt} recs at off {off} exceed data block ({n})"))
            ok = False
    return ok


# --- main -------------------------------------------------------------------
def reinline(m2: M2File, skel: SkelData, basename: str = "", *,
             anim_loader=None, m2_afid: bytes | None = None) -> ReinlineResult:
    res = ReinlineResult(parent_skel_file_id=skel.parent_skel_file_id)

    for label, off in (("bones", S.HDR_BONES), ("attachments", S.HDR_ATTACHMENTS),
                       ("sequences", S.HDR_SEQUENCES)):
        cnt, _ = m2.read_header_array(off)
        if cnt:
            res.findings.append(Finding(check="not_empty", severity=Severity.WARN,
                                        detail=f"M2 already has {cnt} {label}; may not be .skel-based"))

    if not skel.skb1 or skel.bones is None or skel.bones.count == 0:
        res.findings.append(Finding(check="no_bones", severity=Severity.FAIL,
                                    detail="no SKB1/bone data in .skel"))
        return res

    # --- structural regions ---
    base_s = None
    if skel.sks1:
        base_s = m2.append_data(skel.sks1)
        _set_array(m2, S.HDR_GLOBAL_LOOPS, skel.global_loops, base_s)
        _set_array(m2, S.HDR_SEQUENCES, skel.sequences, base_s)
        _set_array(m2, S.HDR_SEQUENCE_LOOKUPS, skel.sequence_lookups, base_s)
        res.sequences = skel.sequences.count if skel.sequences else 0

    base_b = m2.append_data(skel.skb1)
    rlen_b = len(skel.skb1)
    _set_array(m2, S.HDR_BONES, skel.bones, base_b)
    _set_array(m2, S.HDR_KEY_BONE_LOOKUP, skel.key_bone_lookup, base_b)
    res.bones = skel.bones.count

    base_a = rlen_a = None
    if skel.ska1 and skel.attachments and skel.attachments.count:
        base_a = m2.append_data(skel.ska1)
        rlen_a = len(skel.ska1)
        _set_array(m2, S.HDR_ATTACHMENTS, skel.attachments, base_a)
        _set_array(m2, S.HDR_ATTACHMENT_LOOKUP, skel.attachment_lookup, base_a)
        res.attachments = skel.attachments.count

    # --- animation pass: inline external .anim AFSB/AFSA data ---
    sequences = _parse_sequences(skel)
    afid_map = _afid_map(skel, m2_afid)
    afsb_base: dict[int, int] = {}
    afsa_base: dict[int, int] = {}
    inlined_seq: set[int] = set()

    if anim_loader is not None and afid_map and sequences:
        for i, seq in enumerate(sequences):
            fid = afid_map.get(seq)
            if not fid:
                continue
            data = anim_loader(fid)
            if not data:
                res.missing_anims.append(fid)
                continue
            anim = parse_anim(data)
            pulled = False
            if anim.afsb:
                afsb_base[i] = m2.append_data(anim.afsb)
                pulled = True
            if anim.afsa:
                afsa_base[i] = m2.append_data(anim.afsa)
                pulled = True
            if pulled:
                res.inlined_anims += 1
                inlined_seq.add(i)

    # --- rebase bone tracks ---
    place_bone = _make_placer(base_b, rlen_b, afsb_base, sequences, afid_map)
    bones_abs = base_b + skel.bones.offset
    external = 0
    for i in range(skel.bones.count):
        external += _rebase_bone(m2.md20, bones_abs + i * S.M2COMPBONE, base_b, rlen_b,
                                 place_bone, res.findings)

    # --- rebase attachment tracks ---
    if base_a is not None:
        place_att = _make_placer(base_a, rlen_a, afsa_base, sequences, afid_map)
        att_abs = base_a + skel.attachments.offset
        for i in range(skel.attachments.count):
            external += _rebase_track(m2.md20, att_abs + i * S.M2ATTACHMENT + S.ATTACH_TRACK,
                                      base_a, rlen_a, place_att, res.findings)
    res.external_tracks = external

    # Mark inlined sequences as internally-stored (flag 0x20). Without this the
    # client would try to load the now-gone .anim file AND would not parse the
    # inline bone animation blocks we just wrote (wowdev.wiki/M2).
    if base_s is not None and skel.sequences and inlined_seq:
        seq_abs = base_s + skel.sequences.offset
        for i in inlined_seq:
            if i < skel.sequences.count:
                foff = seq_abs + i * S.M2SEQUENCE + S.M2SEQUENCE_FLAGS
                flags = S.read_u32(m2.md20, foff)
                S.write_u32(m2.md20, foff, flags | S.SEQ_FLAG_INTERNAL_0x20)
        res.marked_internal = len(inlined_seq)

    # --- name, drop skeleton link ---
    name = basename or (skel.name.decode("ascii", "replace") if skel.name else "")
    if name:
        nb = name.encode("ascii", "replace") + b"\x00"
        off = m2.append_data(nb, align=1)
        m2.set_header_array(S.HDR_NAME, len(nb), off)
    m2.remove_chunk("SKID")

    # --- reporting ---
    chunked_anim = bool(m2.global_flags & (S.FLAG_CHUNKED_ANIM_0x2000 | S.FLAG_UPGRADED_ANIM_0x200000))
    anim_indicators = bool(skel.afid or m2.has_chunk("AFID") or chunked_anim or skel.has_parent)

    if anim_loader is None:
        res.fully_inline = (external == 0 and not anim_indicators)
    else:
        res.fully_inline = (external == 0 and not res.missing_anims)

    if skel.has_parent:
        res.findings.append(Finding(check="parent_skel", severity=Severity.WARN,
                                    detail=f"parent skeleton fdid {skel.parent_skel_file_id} shares .anim files; resolve its AFID too"))
    if res.missing_anims:
        res.findings.append(Finding(check="missing_anim", severity=Severity.WARN,
                                    detail=f".anim file(s) not found: {res.missing_anims}"))
    if res.inlined_anims:
        res.findings.append(Finding(check="anim_inlined", severity=Severity.PASS,
                                    detail=f"inlined {res.inlined_anims} external .anim file(s); marked {res.marked_internal} sequence(s) internal (flag 0x20)"))

    if res.fully_inline:
        res.findings.append(Finding(check="fully_inline", severity=Severity.PASS,
                                    detail="fully self-contained; ready for MultiConverter (no .anim/.skel needed)"))
    else:
        res.findings.append(Finding(check="needs_anim_pass", severity=Severity.WARN,
                                    detail=f"{external} external track(s) unresolved; supply an anim_loader / parent AFID"))

    res.success = _verify(m2.md20, res.findings) and all(f.severity is not Severity.FAIL for f in res.findings)
    return res


def reinline_file(m2_path: str, skel_loader, out_dir: str, basename: str | None = None,
                  anim_loader=None) -> ConversionResult:
    """Read an M2, resolve its `.skel` via ``skel_loader(file_id)`` (and, if given,
    its `.anim` files via ``anim_loader``), re-inline, and write the result."""
    cr = ConversionResult(source=m2_path, stage="skel_reinline")
    try:
        with open(m2_path, "rb") as fh:
            m2 = M2File(fh.read())
    except (M2Error, OSError) as exc:
        cr.stderr = str(exc)
        cr.report = ValidationReport(asset=m2_path, findings=[
            Finding(check="parse_error", severity=Severity.FAIL, detail=str(exc))])
        return cr

    fid = m2.skel_file_id()
    if fid is None:
        cr.success = True
        cr.output = m2_path
        cr.report = ValidationReport(asset=m2_path, findings=[
            Finding(check="no_skel", severity=Severity.PASS, detail="model does not use a .skel")])
        return cr

    skel_bytes = skel_loader(fid)
    if not skel_bytes:
        cr.stderr = f"could not load .skel for fdid {fid}"
        cr.report = ValidationReport(asset=m2_path, findings=[
            Finding(check="skel_missing", severity=Severity.FAIL, detail=cr.stderr)])
        return cr

    skel = parse_skel(skel_bytes)
    m2_afid = bytes(m2.chunk("AFID")) if m2.has_chunk("AFID") else None
    name = basename if basename is not None else os.path.splitext(os.path.basename(m2_path))[0]
    res = reinline(m2, skel, basename=name, anim_loader=anim_loader, m2_afid=m2_afid)

    out_path = os.path.join(out_dir, os.path.basename(m2_path))
    if res.success:
        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(m2.to_bytes())
        cr.output = out_path
    cr.success = res.success
    cr.report = res.report(m2_path)
    return cr
