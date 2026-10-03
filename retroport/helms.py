"""Helm sets: one model per race and gender.

A 3.3.5 helm is ``Item\\ObjectComponents\\Head\\<Base>_<Race><Gender>.m2`` (e.g.
``Helm_Leather_D_01_BeF.m2``), and ItemDisplayInfo names only ``<Base>.mdx`` - the
client appends the race/gender of whoever wears it. Modern helms use the same
suffixes for the classic races (checked against the listfile: ``_hum``, ``_bef``,
``_scm``...), plus differently-named files for races 3.3.5 doesn't have
(``_za_m``, ``_kt_f``, ``_vu_m``...), so converting a helm set is: find the
siblings, keep the 3.3.5 race/gender variants, run each through the pipeline.

Modern helms are fitted to modern character heads. MultiConverter's ``-fixhelm``
refits them for the classic 3.3.5 character models; skip it when the client runs
HD character models (e.g. the 3.3.5 HD patch), which are shaped like the modern ones.
The wotlkconv engine has no such refit, so classic-model helms need the MultiConverter
engine.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

from .pipeline import Pipeline, PipelineConfig, PipelineResult

RACES_335 = {"hu": "Human", "or": "Orc", "dw": "Dwarf", "ni": "Night Elf", "sc": "Undead",
             "ta": "Tauren", "gn": "Gnome", "tr": "Troll", "be": "Blood Elf", "dr": "Draenei"}
NPC_RACES_335 = {"go": "Goblin"}          # NPC-only in 3.3.5; converted if present, never required
_VARIANT = re.compile(r"^(?P<base>.+)_(?P<race>[a-z]{2})(?P<gender>[mf])$", re.I)


@dataclass
class HelmSet:
    base: str                                           # e.g. helm_plate_raidwarrior_q_01
    folder: str
    variants: dict[str, str] = field(default_factory=dict)   # "hum" -> path, 3.3.5 races only
    missing: list[str] = field(default_factory=list)          # required 3.3.5 race/genders not found
    skipped: list[str] = field(default_factory=list)          # modern-only race files ignored

    @property
    def dbc_model(self) -> str:
        return self.base + ".mdx"


def helm_base(path: str) -> str | None:
    m = _VARIANT.match(os.path.splitext(os.path.basename(path))[0])
    return m.group("base") if m else None


def find_helm_set(model_path: str, include_npc_races: bool = True) -> HelmSet:
    """From any one variant (or the base name), find every race/gender sibling."""
    folder = os.path.dirname(os.path.abspath(model_path))
    stem = os.path.splitext(os.path.basename(model_path))[0]
    base = helm_base(model_path) or stem
    hs = HelmSet(base=base, folder=folder)
    wanted = {**RACES_335, **(NPC_RACES_335 if include_npc_races else {})}
    prefix = base.lower() + "_"
    for name in sorted(os.listdir(folder)):
        low = name.lower()
        if not (low.startswith(prefix) and low.endswith(".m2")):
            continue
        m = _VARIANT.match(os.path.splitext(name)[0])
        if m and m.group("base").lower() == base.lower() and m.group("race").lower() in wanted:
            hs.variants[(m.group("race") + m.group("gender")).lower()] = os.path.join(folder, name)
        else:
            hs.skipped.append(name)
    hs.missing = [r + g for r in RACES_335 for g in "mf" if r + g not in hs.variants]
    return hs


def convert_helm_set(model_path: str, out_dir: str, *, client_models: str = "classic",
                     tools: dict | None = None, include_npc_races: bool = True,
                     on_log: Callable[[str], None] | None = None,
                     **pipeline_options) -> tuple[HelmSet, dict[str, PipelineResult]]:
    """Run every 3.3.5 variant of a helm through the pipeline into ``out_dir``.
    ``client_models``: "classic" adds MultiConverter's -fixhelm, "hd" doesn't."""
    if client_models not in ("classic", "hd"):
        raise ValueError(f"client_models must be 'classic' or 'hd', got '{client_models}'")
    log = on_log or (lambda m: None)
    hs = find_helm_set(model_path, include_npc_races)
    if not hs.variants:
        raise ValueError(f"no 3.3.5 race/gender variants of '{hs.base}' found in {hs.folder} "
                         "(expected files like <base>_hum.m2, <base>_bef.m2)")
    mc_args = list(pipeline_options.pop("multiconverter_args", []))
    if client_models == "classic" and "-fixhelm" not in mc_args:
        mc_args.append("-fixhelm")
        if pipeline_options.get("engine", "wotlkconv") == "wotlkconv":
            log("NOTE: wotlkconv has no equivalent of MultiConverter's -fixhelm, so these helms keep "
                "the modern head fit. That suits HD character models; for classic models, convert "
                "helms with the MultiConverter engine.")
    results: dict[str, PipelineResult] = {}
    for code, path in sorted(hs.variants.items()):
        log(f"=== {hs.base}_{code} ({RACES_335.get(code[:2], NPC_RACES_335.get(code[:2]))} "
            f"{'male' if code[2] == 'm' else 'female'}) ===")
        cfg = PipelineConfig(model=path, out_dir=out_dir, tools=dict(tools or {}),
                             multiconverter_args=mc_args, **pipeline_options)
        results[code] = Pipeline(cfg, on_log=log).run()
    if hs.missing:
        log(f"Missing 3.3.5 variants (those characters show no helm): {', '.join(hs.missing)}")
    if hs.skipped:
        log(f"Ignored {len(hs.skipped)} file(s) for races 3.3.5 doesn't have: {', '.join(hs.skipped[:6])}"
            + (" ..." if len(hs.skipped) > 6 else ""))
    return hs, results
