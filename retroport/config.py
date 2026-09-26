"""Pipeline configuration.

Plain pydantic model (no pydantic-settings dependency). Sensible defaults, plus
``load`` to read a JSON or TOML config file. Tool paths are filled in once and
referenced by the convert stages from Phase 1 onward.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class OutputMode(str, Enum):
    LOOSE = "loose"   # write a Data/ patch folder (fast iteration)
    MPQ = "mpq"       # build Patch-A.mpq


class ToolPaths(BaseModel):
    multiconverter: str | None = None   # MultiConverter executable
    texconv: str | None = None          # DirectXTex texconv.exe
    blpconverter: str | None = None     # BLPConverter
    blender: str | None = None          # blender.exe (if not using the bpy module)


class Config(BaseModel):
    input_dir: str = "."                       # a wow.export extraction folder
    output_dir: str = "./out"
    output_mode: OutputMode = OutputMode.LOOSE

    listfile: str | None = "data/listfile.csv"
    overrides: str | None = "data/overrides.csv"

    tools: ToolPaths = Field(default_factory=ToolPaths)

    run_blender: bool = False                  # optional repair stage
    workers: int = 4                           # per-asset process pool size
    size_floor_bytes: int = 64                 # outputs smaller than this are 0-byte failures
    ledger: str | None = "out/ledger.json"     # skip already-good assets on re-run

    @classmethod
    def load(cls, path: str) -> "Config":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".toml":
            import tomllib
            data = tomllib.loads(text)
        else:
            data = json.loads(text)
        return cls.model_validate(data)
