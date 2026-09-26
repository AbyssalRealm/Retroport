"""Validation rules.

Two jobs:

* **Correctness** of an output (does this BLP have power-of-two dims, mips, a
  supported compression).
* **Readiness** - is this asset already 3.3.5a-compatible, or does it still need
  a conversion step. The readiness findings double as the router's justification
  ("still MD21", "uses .skel", ...).

In Phase 0 we validate BLP fully and emit readiness findings for everything
else; per-format structural validators (M2 offsets, WMO shader IDs, ADT chunk
integrity) arrive with their conversion phases.
"""

from __future__ import annotations

from ..models import AssetInfo, AssetType, Finding, Severity, ValidationReport

_SUPPORTED_BLP = {"DXT1", "DXT3", "DXT5", "PAL", "BGRA"}


def _is_pow2(n: int | None) -> bool:
    return bool(n) and (n & (n - 1)) == 0


def validate_blp(info: AssetInfo) -> list[Finding]:
    f: list[Finding] = []
    if any("BLP1" in n for n in info.notes):
        f.append(Finding(check="blp_version", severity=Severity.WARN,
                         detail="BLP1; WotLK prefers BLP2, re-encode recommended"))
    if not _is_pow2(info.width) or not _is_pow2(info.height):
        f.append(Finding(check="blp_pow2", severity=Severity.FAIL,
                         detail=f"dimensions {info.width}x{info.height} are not power-of-two"))
    if info.width and info.width > 4096:
        f.append(Finding(check="blp_size", severity=Severity.WARN,
                         detail=f"width {info.width} is large for older GPUs"))
    if info.has_mipmaps is False:
        f.append(Finding(check="blp_mips", severity=Severity.WARN,
                         detail="no mipmaps; client expects a full mip chain"))
    if info.compression and info.compression not in _SUPPORTED_BLP:
        f.append(Finding(check="blp_compression", severity=Severity.FAIL,
                         detail=f"unsupported compression {info.compression}; re-encode to DXT1/DXT5"))
    return f


def validate_m2_readiness(info: AssetInfo) -> list[Finding]:
    f: list[Finding] = []
    if info.chunked:
        f.append(Finding(check="m2_chunked", severity=Severity.WARN,
                         detail="MD21 (Legion+); needs structural downgrade to MD20"))
    if info.version is not None and info.version != 264:
        f.append(Finding(check="m2_version", severity=Severity.WARN,
                         detail=f"version {info.version}; WotLK is 264"))
    if info.uses_skel:
        f.append(Finding(check="m2_skel", severity=Severity.WARN,
                         detail="uses external .skel; requires re-inlining (no off-the-shelf tool)"))
    if info.uses_fdid:
        f.append(Finding(check="m2_fdid", severity=Severity.WARN,
                         detail="references FileDataIDs; needs resolution to embedded path strings"))
    return f


def validate_terrain_readiness(info: AssetInfo) -> list[Finding]:
    f: list[Finding] = []
    if info.split_adt:
        f.append(Finding(check="adt_split", severity=Severity.WARN,
                         detail="split ADT; root/_tex0/_obj0 must be merged into one WotLK ADT"))
    if info.uses_fdid:
        f.append(Finding(check="adt_fdid", severity=Severity.WARN,
                         detail="MDID/MHID FileDataID textures; need resolution + MTEX conversion"))
    return f


def validate(info: AssetInfo) -> ValidationReport:
    report = ValidationReport(asset=info.path)
    if info.type == AssetType.BLP:
        report.findings += validate_blp(info)
    elif info.type == AssetType.M2:
        report.findings += validate_m2_readiness(info)
    elif info.type in (AssetType.ADT, AssetType.WDT):
        report.findings += validate_terrain_readiness(info)
    elif info.type == AssetType.SKEL:
        report.findings.append(Finding(check="skel_standalone", severity=Severity.WARN,
                                        detail="standalone .skel; data must be merged into its M2"))

    # WMO/ADT/SKIN/ANIM readiness can't be proven from the header alone in
    # Phase 0 - flag them as needing the converter rather than reporting nothing.
    if info.type in (AssetType.WMO, AssetType.ADT, AssetType.WDT,
                     AssetType.SKIN, AssetType.ANIM) and not report.findings:
        report.findings.append(Finding(
            check="needs_conversion", severity=Severity.WARN,
            detail="structural conversion required (not verifiable from header alone)"))

    if not report.findings:
        report.findings.append(Finding(check="ready", severity=Severity.PASS,
                                        detail="no conversion blockers detected"))
    return report
