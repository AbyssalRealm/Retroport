"""End-to-end retroport pipeline (headless).

Chains the stages we own plus the external-tool step, reporting through plain
callbacks so the same logic drives the GUI worker (Qt signals) and a CLI command.
No Qt or UI imports here - this is the testable brain.

Stages (each can be toggled):
    triage    -> classify the model (inspect)
    reinline  -> fold .skel/.anim into the M2 (library)          [.skel models]
    convert   -> MultiConverter MD21->MD20 (external exe)
    fixup     -> clear stale anim flags, clamp submeshes, set nViews
    check     -> WotLK render-blocker diagnostics (library)
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable

from .scan.scanner import classify_file
from .models import AssetType
from .convert.skel_reinliner import reinline_file
from .convert.loaders import make_manifest_loader, make_file_id_loader, find_manifest
from .convert.multiconverter import run_multiconverter
from .convert.m2mod import reduce_model, txid_remove
from .convert import fixup as fixup_mod
from .check import check_model, CheckResult, analyze_skin


@dataclass
class PipelineConfig:
    model: str                          # input .m2 (modern, possibly .skel-based)
    out_dir: str                        # where converted files are written
    source: str = ""                    # extraction root (fallback dep loader)
    tools: dict = field(default_factory=dict)   # {'multiconverter': path, ...}

    do_reinline: bool = True
    do_convert: bool = True
    do_fixup: bool = True
    do_check: bool = True
    do_polyreduce: bool = False          # M2Mod->Blender->M2Mod poly reduction (needs both exes)
    do_txid_remove: bool = False         # M2Mod --txid-remove: embed texture paths from the listfile

    clamp_submeshes_to: int = 59
    poly_target_tris: int = 21000        # decimate target (headroom under the 21845 cap)
    poly_hair_ratio: float = 0.4         # decimate hair geosets to this (0 = flat)
    expected_skins: int | None = None   # set nViews to this if given
    multiconverter_args: list[str] = field(default_factory=list)   # just the path by default; add -fixhelm etc. if needed
    fix_combiner: bool = True            # clear the combiner-combos flag (black-mesh fix)


@dataclass
class StageResult:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class PipelineResult:
    ok: bool = True
    output_m2: str | None = None
    stages: list[StageResult] = field(default_factory=list)
    check: CheckResult | None = None

    def add(self, s: StageResult) -> None:
        self.stages.append(s)
        if not s.ok and not s.skipped:
            self.ok = False


LogFn = Callable[[str], None]
ProgressFn = Callable[[int], None]


class Pipeline:
    def __init__(self, cfg: PipelineConfig,
                 on_log: LogFn | None = None, on_progress: ProgressFn | None = None):
        self.cfg = cfg
        self._log = on_log or (lambda m: None)
        self._progress = on_progress or (lambda p: None)

    def run(self) -> PipelineResult:
        cfg = self.cfg
        res = PipelineResult()
        os.makedirs(cfg.out_dir, exist_ok=True)

        # --- triage ---
        self._log("Triage: classifying model...")
        self._progress(5)
        info = classify_file(cfg.model)
        if info.type is not AssetType.M2:
            res.add(StageResult("triage", False, f"not an M2 ({info.type.value})"))
            return res
        self._log(f"  M2 v{info.version}, chunked={info.chunked}, uses_skel={info.uses_skel}")
        res.add(StageResult("triage", True,
                            f"v{info.version} skel={info.uses_skel} fdid={info.uses_fdid}"))

        model_in = cfg.model            # may be replaced by the reduced model below

        # --- poly-reduce (M2Mod .m2i -> Blender/LunNova decimate -> M2Mod, BEFORE convert) ---
        if cfg.do_polyreduce:
            m2mod = cfg.tools.get("m2mod")
            blender = cfg.tools.get("blender")
            if not (m2mod and blender):
                self._log("Poly-reduce: SKIPPED (set both M2Mod and Blender paths)")
                res.add(StageResult("polyreduce", True, "tools not set", skipped=True))
            else:
                self._log(f"Poly-reduce: M2Mod -> Blender(.m2i) decimate (hair x{cfg.poly_hair_ratio}) -> M2Mod...")
                self._progress(12)
                rr = reduce_model(m2mod, blender, model_in, os.path.join(cfg.out_dir, "_work"),
                                  target_tris=cfg.poly_target_tris, mappings_dir=self._mappings_dir(),
                                  working_dir=(cfg.source or None), hair_ratio=cfg.poly_hair_ratio)
                self._log(f"  {rr.detail}")
                if rr.ok and rr.reduced_m2:
                    model_in = rr.reduced_m2
                    res.add(StageResult("polyreduce", True, rr.detail))
                else:
                    res.add(StageResult("polyreduce", False, rr.detail))
                    self._log("  Continuing un-reduced; check will still flag the poly wall.")

        # --- stage sibling assets (skins, and .anim for non-skel) into out_dir ---
        # MultiConverter needs the .skin files next to the .m2, and the client looks
        # for <stem>NN.skin - so copy them in, renamed to the WotLK convention.
        staged = self._stage_assets(model_in, info.uses_skel)
        if staged:
            self._log(f"Staged {len(staged)} asset(s): {', '.join(staged[:6])}"
                      + (" ..." if len(staged) > 6 else ""))
            res.add(StageResult("stage", True, f"{len(staged)} file(s)"))
        else:
            self._log("Stage: no .skin/.anim siblings found (check the extraction folder)")
            res.add(StageResult("stage", True, "nothing to stage", skipped=True))

        working_m2 = os.path.join(cfg.out_dir, os.path.basename(model_in))

        # --- txid-remove (embed texture paths from the listfile) ---
        # MUST run BEFORE re-inline, on the stock modern M2. Our re-inliner produces a
        # non-standard MD21 (skeleton + .anim folded in, SKID dropped, sequences flagged
        # internal); M2Mod can read that but its writer chokes on it -> "Failed to save
        # M2: unspecified error". The proven porting flow is txid-remove first, then the
        # skeleton work. Output must keep the .m2 name, so use a scratch dir.
        if cfg.do_txid_remove:
            m2mod = cfg.tools.get("m2mod")
            if not m2mod:
                self._log("TXID-remove: SKIPPED (set M2Mod path)")
                res.add(StageResult("txid", True, "M2Mod not set", skipped=True))
            else:
                self._log("TXID-remove: resolving texture paths via M2Mod + listfile...")
                self._progress(16)
                txid_dir = os.path.join(cfg.out_dir, "_txid")
                os.makedirs(txid_dir, exist_ok=True)
                txid_out = os.path.join(txid_dir, os.path.basename(model_in))
                tr = txid_remove(m2mod, model_in, txid_out, mappings_dir=self._mappings_dir(),
                                 working_dir=(cfg.source or None))
                if tr.stdout.strip():
                    self._log("  M2Mod said: " + tr.stdout.strip().replace("\n", " | "))
                if tr.stderr.strip():
                    self._log(f"  {tr.stderr.strip()}")
                if tr.ok and os.path.isfile(txid_out):
                    model_in = txid_out          # everything downstream uses the fixed M2
                    res.add(StageResult("txid", True, "texture paths embedded"))
                else:
                    res.add(StageResult("txid", False, tr.stderr or "txid produced no output"))
                    self._log("  Continuing with un-fixed texture paths.")

        # --- reinline (only if .skel-based) ---
        if cfg.do_reinline and info.uses_skel:
            self._log("Re-inline: folding .skel/.anim into the M2...")
            self._progress(20)
            skel_loader, anim_loader = self._loaders()
            cr = reinline_file(model_in, skel_loader, cfg.out_dir, anim_loader=anim_loader)
            for f in (cr.report.findings if cr.report else []):
                self._log(f"  [{f.severity.value}] {f.check}: {f.detail}")
            if not cr.success:
                res.add(StageResult("reinline", False, cr.stderr or "re-inline failed"))
                return res
            working_m2 = cr.output or working_m2
            res.add(StageResult("reinline", True, os.path.basename(working_m2)))
        else:
            if not os.path.exists(working_m2):
                shutil.copy2(model_in, working_m2)
            reason = "no .skel" if not info.uses_skel else "disabled"
            res.add(StageResult("reinline", True, f"skipped ({reason})", skipped=True))

        # --- convert (MultiConverter) ---
        if cfg.do_convert:
            exe = cfg.tools.get("multiconverter")
            if not exe:
                res.add(StageResult("convert", False, "no MultiConverter path set", skipped=True))
                self._log("Convert: SKIPPED (set MultiConverter in Settings)")
            else:
                self._log("Convert: running MultiConverter (MD21->MD20)...")
                self._progress(50)
                # Only expect file growth when MultiConverter itself re-inlines the
                # skeleton - i.e. we did NOT already re-inline it ourselves.
                mc_reinlines = info.uses_skel and not cfg.do_reinline
                self._log(f"  args: {' '.join([os.path.basename(working_m2), *cfg.multiconverter_args])}")
                tr = run_multiconverter(exe, working_m2, extra_args=cfg.multiconverter_args,
                                        expect_growth=mc_reinlines)
                if tr.stdout.strip():
                    self._log("  MultiConverter said: " + tr.stdout.strip().replace("\n", " | "))
                if tr.stderr:
                    self._log(f"  {tr.stderr}")
                if not tr.ok:
                    res.add(StageResult("convert", False, tr.stderr or "MultiConverter failed"))
                    return res
                self._log("  MultiConverter finished.")
                # Verify it actually downgraded MD21 -> MD20 in place.
                post = classify_file(working_m2)
                self._log(f"  post-convert: chunked={post.chunked} version={post.version}")
                if post.chunked:
                    self._log("  NOTE: model is STILL MD21 after MultiConverter - it did not convert "
                              "in place. Its output likely went to another folder (the exe dir or a "
                              "subfolder). Find that file; that's the real converted model.")
                    res.add(StageResult("convert", False, "output still MD21 (not converted in place)"))
                else:
                    res.add(StageResult("convert", True, f"converted to MD20 (v{post.version})"))

        # --- fixup ---
        if cfg.do_fixup:
            self._log("Fixup: cleaning flags / clamping submeshes...")
            self._progress(70)
            notes = self._fixup(working_m2)
            res.add(StageResult("fixup", True, "; ".join(notes) or "no changes"))

        # --- check ---
        if cfg.do_check:
            self._log("Check: WotLK compatibility...")
            self._progress(90)
            try:
                chk = check_model(working_m2, skin_dir=cfg.out_dir)
                res.check = chk
                blockers = [f for f in chk.report().findings if f.severity.value == "fail"]
                for f in chk.report().findings:
                    self._log(f"  [{f.severity.value}] {f.check}: {f.detail}")
                res.add(StageResult("check", not blockers,
                                    f"{len(blockers)} blocker(s)" if blockers else "clean"))
            except ValueError as exc:
                res.add(StageResult("check", False, str(exc)))

        res.output_m2 = working_m2
        shutil.rmtree(os.path.join(cfg.out_dir, "_txid"), ignore_errors=True)
        self._progress(100)
        self._log("Done." if res.ok else "Done (with blockers).")
        return res

    # --- helpers ---
    def _primary_skin_tris(self, m2_path: str) -> int:
        """Triangle count of the model's skin00 (0 if not found), to gate decimation."""
        stem = os.path.splitext(os.path.basename(m2_path))[0]
        skin = os.path.join(self.cfg.out_dir, f"{stem}00.skin")
        if not os.path.isfile(skin):
            skin = os.path.join(os.path.dirname(m2_path) or ".", f"{stem}00.skin")
        try:
            return analyze_skin(skin).triangles
        except (OSError, ValueError):
            return 0

    def _mappings_dir(self) -> str | None:
        """M2Mod's --mappings-dir: the folder holding the (converted) listfile."""
        lf = self.cfg.tools.get("listfile")
        if lf and os.path.isfile(lf):
            return os.path.dirname(lf)
        if lf and os.path.isdir(lf):
            return lf
        return None

    def _stage_assets(self, model_path: str, uses_skel: bool) -> list[str]:
        """Copy the model's .skin files into out_dir as <stem>NN.skin (WotLK order),
        plus external .anim files for non-skel models. Skel models inline their anims,
        so those aren't copied. Returns the staged filenames."""
        src_dir = os.path.dirname(model_path) or "."
        stem = os.path.splitext(os.path.basename(model_path))[0]
        staged: list[str] = []

        # skins, in manifest order (main skins then LODs), renamed to ascending NN
        skin_files: list[str] = []
        meta = find_manifest(model_path)
        if meta and os.path.isfile(meta):
            try:
                data = json.loads(open(meta, encoding="utf-8").read())
                for section in ("skins", "lodSkins"):
                    for entry in data.get(section, []):
                        f = entry.get("file")
                        if f:
                            skin_files.append(os.path.basename(f))
            except (ValueError, OSError):
                skin_files = []
        if not skin_files:
            skin_files = sorted(f for f in os.listdir(src_dir)
                                if f.lower().startswith(stem.lower()) and f.lower().endswith(".skin"))
        for i, fname in enumerate(skin_files):
            src = os.path.join(src_dir, fname)
            if os.path.isfile(src):
                dst = os.path.join(self.cfg.out_dir, f"{stem}{i:02d}.skin")
                shutil.copy2(src, dst)
                staged.append(os.path.basename(dst))

        # external .anim files (non-skel only; skel models inline them)
        if not uses_skel:
            for fname in sorted(os.listdir(src_dir)):
                if fname.lower().startswith(stem.lower()) and fname.lower().endswith(".anim"):
                    shutil.copy2(os.path.join(src_dir, fname), os.path.join(self.cfg.out_dir, fname))
                    staged.append(fname)
        return staged

    def _loaders(self):
        meta = find_manifest(self.cfg.model)
        if meta and os.path.isfile(meta):
            self._log(f"  using manifest {os.path.basename(meta)}")
            loader = make_manifest_loader(meta)
            return loader, loader
        root = self.cfg.source or os.path.dirname(self.cfg.model)
        return make_file_id_loader(root, ".skel"), make_file_id_loader(root, ".anim")

    def _fixup(self, m2_path: str) -> list[str]:
        notes: list[str] = []
        out_dir = self.cfg.out_dir
        stem = os.path.splitext(os.path.basename(m2_path))[0]
        skins = [os.path.join(out_dir, n) for n in sorted(os.listdir(out_dir))
                 if n.lower().startswith(stem.lower()) and n.lower().endswith(".skin")]

        try:
            if fixup_mod.clear_stale_anim_flags(m2_path):
                notes.append("cleared stale anim flags")
        except ValueError:
            pass

        for sp in skins:
            try:
                new_n = fixup_mod.clamp_skin_submeshes(sp, self.cfg.clamp_submeshes_to)
                notes.append(f"{os.path.basename(sp)}->{new_n} submeshes")
            except ValueError:
                pass

        if self.cfg.fix_combiner:
            try:
                cleared, changed = fixup_mod.clear_combiner_combos(m2_path, skins)
                if cleared or changed:
                    notes.append(f"combiner flag cleared, {changed} shader(s) reset")
            except ValueError:
                pass

        if self.cfg.expected_skins is not None:
            try:
                fixup_mod.set_num_skin_profiles(m2_path, self.cfg.expected_skins)
                notes.append(f"nViews={self.cfg.expected_skins}")
            except ValueError:
                pass
        return notes
