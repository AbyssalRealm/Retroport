"""retroport - automate porting modern WoW assets down to 3.3.5a.

Phase 0 surface: classify assets and report WotLK-readiness. Conversion stages
(MultiConverter wrapper, .skel re-inliner, BLP recompress, Blender repair,
exporter) plug in from Phase 1 onward.
"""

from .models import (
    AssetInfo,
    AssetType,
    ConversionResult,
    Finding,
    Severity,
    ValidationReport,
)
from .scan.scanner import classify_file, classify_stream
from .formats import M2File, M2Error, SkelData, parse_skel, AnimData, parse_anim
from .convert.skel_reinliner import reinline, reinline_file
from .convert.loaders import make_file_id_loader, make_manifest_loader, find_manifest
from .check import check_model, CheckResult
from .pipeline import Pipeline, PipelineConfig, PipelineResult
from .dbc import DBC, register_creature_model

__version__ = "0.1.0"

__all__ = [
    "AssetInfo",
    "AssetType",
    "ConversionResult",
    "Finding",
    "Severity",
    "ValidationReport",
    "classify_file",
    "classify_stream",
    "M2File",
    "M2Error",
    "SkelData",
    "parse_skel",
    "AnimData",
    "parse_anim",
    "reinline",
    "reinline_file",
    "make_file_id_loader",
    "make_manifest_loader",
    "find_manifest",
    "check_model",
    "CheckResult",
    "Pipeline",
    "PipelineConfig",
    "PipelineResult",
    "DBC",
    "register_creature_model",
    "__version__",
]
from .bugreport import build_report
from .texreport import build_texture_report
from .errordb import decode_error
