"""Core data models shared across the retroport pipeline.

These are deliberately small and serialisable (pydantic v2) so they can be
dumped to JSON for a run ledger, passed between processes, or fed to a UI.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    M2 = "m2"
    SKIN = "skin"
    ANIM = "anim"
    SKEL = "skel"
    BLP = "blp"
    WMO = "wmo"
    ADT = "adt"
    WDT = "wdt"
    UNKNOWN = "unknown"


class AssetInfo(BaseModel):
    """Everything the scanner can learn about a single file without converting it."""

    path: str
    type: AssetType = AssetType.UNKNOWN
    size: int | None = None

    # M2-specific
    version: int | None = None          # raw M2 version word (264 == WotLK)
    expansion: str | None = None        # coarse mapping from the version word
    chunked: bool = False               # MD21 (Legion+) vs MD20 (<= WoD)
    uses_skel: bool = False             # SKID chunk present -> external .skel skeleton
    uses_fdid: bool = False             # FileDataID chunks present (TXID/SFID/AFID/MDID...)
    chunks: list[str] = Field(default_factory=list)

    # texture-specific
    width: int | None = None
    height: int | None = None
    compression: str | None = None      # DXT1/DXT3/DXT5/PAL/BGRA/JPEG
    has_mipmaps: bool | None = None

    # terrain-specific
    split_adt: bool = False             # has _tex0/_obj0 companions next to it

    notes: list[str] = Field(default_factory=list)

    @property
    def wotlk_ready(self) -> bool:
        """True if this asset already looks 3.3.5a-compatible as-is."""
        if self.type == AssetType.M2:
            return (not self.chunked) and self.version == 264 and not self.uses_skel
        if self.type == AssetType.BLP:
            def _pow2(n: int | None) -> bool:
                return bool(n) and (n & (n - 1)) == 0
            return (self.compression in {"DXT1", "DXT3", "DXT5", "PAL", "BGRA"}
                    and _pow2(self.width) and _pow2(self.height))
        # WMO/ADT/SKIN/ANIM readiness is decided after structural conversion.
        return False


class Severity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Finding(BaseModel):
    check: str
    severity: Severity
    detail: str = ""


class ValidationReport(BaseModel):
    asset: str
    findings: list[Finding] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.severity is not Severity.FAIL for f in self.findings)

    @property
    def worst(self) -> Severity:
        if any(f.severity is Severity.FAIL for f in self.findings):
            return Severity.FAIL
        if any(f.severity is Severity.WARN for f in self.findings):
            return Severity.WARN
        return Severity.PASS


class ConversionResult(BaseModel):
    """Produced by each convert stage. Used from Phase 1 onward."""

    source: str
    output: str | None = None
    stage: str | None = None
    success: bool = False
    quarantined: bool = False
    stderr: str | None = None
    report: ValidationReport | None = None
