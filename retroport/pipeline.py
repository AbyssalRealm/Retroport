"""End-to-end retroport pipeline (headless).

Chains the stages we own plus the external-tool step, reporting through plain
callbacks so the same logic drives the GUI worker (Qt signals) and a CLI command.
No Qt or UI imports here - this is the testable brain.

Stages (each can be toggled):
    triage    -> classify the model (inspect)
    reinline  -> fold .skel/.anim into the M2 (library)          [.skel models, MultiConverter engine]
    convert   -> MD21->MD20 with the configured engine:
                 wotlkconv (default, native Python: merges .skel/.anim, converts and
                 renames skins/anims itself) or the MultiConverter exe
    fixup     -> restore data MultiConverter overwrote at 0x130, clear stale anim flags,
                 clamp submeshes, clear unusable combiner combos, set nViews
    texbake   -> bake texture paths into empty type-0 slots (library)
    check     -> WotLK render-blocker diagnostics (library)

Safety (from Conjure): the input model's folder is never written to, every
library edit is verified before it replaces a file, and the converted model +
skins are copied to <out>/_backup before fixup/texbake touch them.
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
from .convert import texbake
from .convert import engine
from .formats.verify import VerifyError
from .safeio import backup_files, same_dir
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
    fix_combiner: bool = True            # clear a combiner-combos table WotLK can't use (black-mesh fix)
    do_texbake: bool = True              # bake listfile paths into empty type-0 texture slots
    texture_paths: dict[int, str] = field(default_factory=dict)   # explicit slot -> in-game .blp path (wins)
    engine: str = "wotlkconv"            # "wotlkconv" (native) or "multiconverter" (exe)
    engine_options: dict = field(default_factory=dict)   # wotlkconv Options fields, e.g. {"strip_particles": True}


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
        if cfg.engine not in ("wotlkconv", "multiconverter"):
            res.add(StageResult("config", False, f"unknown engine '{cfg.engine}' (wotlkconv or multiconverter)"))
            self._log(f"Refusing to run: unknown engine '{cfg.engine}'.")
            return res
        model_dir = os.path.dirname(os.path.abspath(cfg.model))
        if os.path.isdir(cfg.out_dir) and same_dir(cfg.out_dir, model_dir):
            res.add(StageResult("triage", False, "output folder is the model's own folder - pick a "
                                "separate folder so the source files are never overwritten"))
            self._log("Refusing to run: the output folder is the input model's folder.")
            return res
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

        native = self._native_engine()

        # --- stage sibling assets (skins, and .anim for non-skel) into out_dir ---
        # MultiConverter needs the .skin files next to the .m2, and the client looks
        # for <stem>NN.skin - so copy them in, renamed to the WotLK convention.
        # wotlkconv finds, converts and renames them itself.
        staged = [] if native else self._stage_assets(model_in, info.uses_skel)
        if native:
            res.add(StageResult("stage", True, "wotlkconv writes the skins/anims", skipped=True))
        elif staged:
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

        # --- reinline (only if .skel-based; wotlkconv merges the skeleton itself) ---
        if native and info.uses_skel:
            res.add(StageResult("reinline", True, "wotlkconv merges the .skel/.anim", skipped=True))
        elif cfg.do_reinline and info.uses_skel:
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
            if not os.path.exists(working_m2) and not native:
                shutil.copy2(model_in, working_m2)
            reason = "no .skel" if not info.uses_skel else "disabled"
            res.add(StageResult("reinline", True, f"skipped ({reason})", skipped=True))

        # TXID (texture FileDataIDs) is gone after MD21->MD20, so read it now for texbake.
        txid = texbake.read_txid(model_in if native else working_m2) or texbake.read_txid(cfg.model)
        # MultiConverter can overwrite the start of the model's data with the combiner-combos
        # header field; fixup restores it from the model as MultiConverter received it.
        pre_convert = b""

        # --- convert (wotlkconv) ---
        if cfg.do_convert and native:
            self._progress(50)
            stage = self._convert_native(model_in, working_m2)
            res.add(stage)
            if not stage.ok:
                return res
        elif native:
            # Nothing converted: keep the model in out_dir so fixup/check have a file to look at.
            if not os.path.exists(working_m2):
                shutil.copy2(model_in, working_m2)

        # --- convert (MultiConverter) ---
        if cfg.do_convert and not native:
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
                with open(working_m2, "rb") as fh:
                    pre_convert = fh.read()
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

        if cfg.do_fixup or cfg.do_texbake:
            kept = backup_files([working_m2, *self._skins(working_m2)],
                                os.path.join(cfg.out_dir, "_backup"))
            if kept:
                self._log(f"Backed up {len(kept)} file(s) to _backup/ before editing.")

        # --- fixup ---
        if cfg.do_fixup:
            self._log("Fixup: cleaning flags / clamping submeshes...")
            self._progress(70)
            notes, rejected = self._fixup(working_m2, pre_convert)
            detail = "; ".join(notes) or "no changes"
            if rejected:
                detail += f" | {len(rejected)} edit(s) rejected by verification"
            res.add(StageResult("fixup", not rejected, detail))

        # --- texbake ---
        if cfg.do_texbake:
            self._progress(80)
            res.add(self._texbake(working_m2, txid))

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

    def _native_engine(self) -> bool:
        return self.cfg.engine == "wotlkconv"

    def _convert_native(self, model_in: str, working_m2: str) -> StageResult:
        """MD21 -> MD20 with wotlkconv; its skins/anims land in out_dir under the 3.3.5 names."""
        if not engine.available():
            self._log(f"Convert: {engine.INSTALL_HINT}")
            return StageResult("convert", False, "wotlkconv not installed")
        self._log("Convert: wotlkconv (MD21->MD20, skeleton, skins, anims)...")
        roots: list[str] = []
        for r in (os.path.dirname(os.path.abspath(model_in)), os.path.dirname(os.path.abspath(self.cfg.model)),
                  self.cfg.source):
            if r and os.path.isdir(r) and r not in roots:
                roots.append(r)
        lf = self.cfg.tools.get("listfile")
        r = engine.convert_with_wotlkconv(
            model_in, self.cfg.out_dir,
            output_stem=os.path.splitext(os.path.basename(working_m2))[0],
            listfile_path=lf if lf and os.path.isfile(lf) else None,
            overrides_path=self._overrides_csv(), search_roots=roots,
            options=self.cfg.engine_options, on_log=self._log)
        model_name = os.path.basename(model_in)
        for n in r.notes:
            if n.level != "info":
                where = "" if n.file == model_name else f" ({n.file})"
                self._log(f"  [{n.level}] {n.code}: {n.message}{where}")
        infos = sum(1 for n in r.notes if n.level == "info")
        if infos:
            self._log(f"  ({infos} info note(s): {', '.join(sorted({n.code for n in r.notes if n.level == 'info'})[:6])})")
        if not r.ok:
            self._log(f"  wotlkconv failed: {r.error}")
            return StageResult("convert", False, r.error)
        post = classify_file(working_m2)
        self._log(f"  wrote {len(r.written)} file(s); post-convert: chunked={post.chunked} version={post.version}")
        if post.chunked or post.version != 264:
            return StageResult("convert", False, f"output is not MD20 v264 (chunked={post.chunked}, v{post.version})")
        return StageResult("convert", True, f"wotlkconv {r.status}: MD20 v{post.version}, {len(r.written)} file(s)")

    def _skins(self, m2_path: str) -> list[str]:
        out_dir = self.cfg.out_dir
        stem = os.path.splitext(os.path.basename(m2_path))[0]
        return [os.path.join(out_dir, n) for n in sorted(os.listdir(out_dir))
                if n.lower().startswith(stem.lower()) and n.lower().endswith(".skin")]

    def _texbake(self, m2_path: str, txid: list[int]) -> StageResult:
        """Bake in-game paths into type-0 slots MultiConverter left empty (resolved
        from the pre-convert TXID via the listfile), plus any explicit slot paths."""
        try:
            with open(m2_path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            return StageResult("texbake", False, str(exc))
        if data[:4] != b"MD20":
            self._log("Texbake: SKIPPED (model is not MD20 yet - MultiConverter has to run first)")
            return StageResult("texbake", True, "model not converted yet", skipped=True)

        needed = texbake.empty_slot_fdids(data, txid)
        known: dict[int, str] = {}
        if needed:
            lf = self.cfg.tools.get("listfile")
            self._log(f"Texbake: resolving {len(needed)} texture FileDataID(s) via the listfile...")
            known = texbake.resolve_fdids(needed, lf if lf and os.path.isfile(lf) else None,
                                          self._overrides_csv())
        plan = texbake.plan_bake(data, txid, known, self.cfg.texture_paths)

        for slot, fdid in plan.unresolved:
            self._log(f"  slot {slot}: FileDataID {fdid} not in the listfile - add it to "
                      "data/overrides.csv or give an explicit path")
        for slot in plan.no_id:
            self._log(f"  slot {slot}: empty type-0 slot with no TXID entry - give an explicit path")
        if not plan.paths:
            detail = "no empty type-0 slots" if not (plan.unresolved or plan.no_id) else                 f"{len(plan.unresolved) + len(plan.no_id)} slot(s) left unresolved"
            self._log(f"Texbake: nothing to bake ({detail})")
            return StageResult("texbake", True, detail, skipped=True)

        try:
            texbake.bake_file(m2_path, plan.paths)
        except ValueError as exc:
            self._log(f"Texbake: {exc}")
            return StageResult("texbake", False, str(exc).splitlines()[0])

        files = texbake.manifest_texture_files(find_manifest(self.cfg.model))
        for slot, path in sorted(plan.paths.items()):
            local = files.get(plan.fdids.get(slot, -1))
            self._log(f"  slot {slot} -> {path}" + (f"   (pack wow.export's {local} there)" if local else ""))
        left = len(plan.unresolved) + len(plan.no_id)
        detail = f"baked {len(plan.paths)} slot(s)" + (f", {left} unresolved" if left else "")
        return StageResult("texbake", True, detail)

    def _overrides_csv(self) -> str | None:
        """Manual FileDataID -> path fixes: Settings value, else the repo's data/overrides.csv."""
        p = self.cfg.tools.get("overrides") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "overrides.csv")
        return p if os.path.isfile(p) else None

    def _fixup(self, m2_path: str, pre_convert: bytes = b"") -> tuple[list[str], list[str]]:
        """Run each fixup; returns (notes, rejected). An edit whose output fails
        verification is not applied - the file stays as it was - and is reported.
        ``pre_convert`` is the model as MultiConverter received it, for the header repair."""
        notes: list[str] = []
        rejected: list[str] = []
        skins = self._skins(m2_path)

        def attempt(label: str, fn):
            try:
                return fn()
            except VerifyError as exc:
                rejected.append(f"{label}: {exc}")
                self._log(f"  REJECTED {label} - {exc} (file left unchanged)")
            except ValueError:
                pass            # not applicable to this file (wrong magic etc.)
            return None

        # First, so the combiner decision below sees the repaired header.
        if pre_convert:
            try:
                repaired = fixup_mod.repair_combiner_header(m2_path, pre_convert)
            except VerifyError as exc:
                repaired = []
                rejected.append(f"combiner header: {exc}")
                self._log(f"  REJECTED combiner header repair - {exc} (file left unchanged)")
            except ValueError as exc:
                repaired = []
                self._log(f"  MultiConverter overwrote model data with the combiner-combos header and it "
                          f"could not be restored: {exc}")
            if repaired:
                notes.append(f"restored {', '.join(repaired)} overwritten by MultiConverter's combiner header")

        if attempt("anim flags", lambda: fixup_mod.clear_stale_anim_flags(m2_path)):
            notes.append("cleared stale anim flags")

        for sp in skins:
            new_n = attempt(f"clamp {os.path.basename(sp)}",
                            lambda sp=sp: fixup_mod.clamp_skin_submeshes(sp, self.cfg.clamp_submeshes_to))
            if new_n is not None:
                notes.append(f"{os.path.basename(sp)}->{new_n} submeshes")

        if self.cfg.fix_combiner:
            status = attempt("combiner check", lambda: fixup_mod.combiner_status(m2_path, skins))
            r = attempt("combiner fix", lambda: fixup_mod.clear_combiner_combos(m2_path, skins))
            if r and (r[0] or r[1]):
                why = f" ({status})" if status else ""
                notes.append(f"combiner flag cleared{why}, {r[1]} shader(s) reset")
            elif status == "":
                notes.append("combiner combos kept (valid WotLK table)")

        if self.cfg.expected_skins is not None:
            n = self.cfg.expected_skins
            if attempt("nViews", lambda: fixup_mod.set_num_skin_profiles(m2_path, n) or True):
                notes.append(f"nViews={n}")
        return notes, rejected
