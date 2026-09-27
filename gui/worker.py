"""Background worker: runs the headless Pipeline and bridges its callbacks to Qt
signals so the UI stays responsive (same pattern as RaceBuilderWorker)."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from retroport.pipeline import Pipeline, PipelineConfig, PipelineResult


class PipelineWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    done = pyqtSignal(bool, str)

    def __init__(self, cfg: PipelineConfig, helm_client: str | None = None):
        """``helm_client`` ("classic"/"hd") runs every 3.3.5 race/gender variant of
        the helm in ``cfg.model`` instead of the single model."""
        super().__init__()
        self.cfg = cfg
        self.helm_client = helm_client

    def run(self) -> None:
        try:
            if self.helm_client:
                self._run_helm_set()
                return
            pipeline = Pipeline(self.cfg, on_log=self.log.emit, on_progress=self.progress.emit)
            result = pipeline.run()
            self.done.emit(result.ok, self._summary(result))
        except Exception as exc:  # never let the thread die silently
            self.log.emit(f"CRITICAL PIPELINE ERROR: {exc}")
            self.done.emit(False, str(exc))

    def _run_helm_set(self) -> None:
        from dataclasses import asdict
        from retroport.helms import convert_helm_set

        opts = {k: v for k, v in asdict(self.cfg).items() if k not in ("model", "out_dir", "tools")}
        hs, results = convert_helm_set(self.cfg.model, self.cfg.out_dir, client_models=self.helm_client,
                                       tools=self.cfg.tools, on_log=self.log.emit, **opts)
        lines = [f"{'OK  ' if r.ok else 'FAIL'}  {hs.base}_{code}" +
                 ("" if r.ok else ": " + "; ".join(f"{s.name} {s.detail}" for s in r.stages if not s.ok and not s.skipped))
                 for code, r in sorted(results.items())]
        if hs.missing:
            lines.append(f"\nMissing 3.3.5 variants (no helm shown): {', '.join(hs.missing)}")
        lines.append(f"\nItemDisplayInfo model name: {hs.dbc_model}")
        self.progress.emit(100)
        self.done.emit(all(r.ok for r in results.values()), "\n".join(lines))

    @staticmethod
    def _summary(result: PipelineResult) -> str:
        lines = [f"{'OK' if s.ok else ('SKIP' if s.skipped else 'FAIL')}  {s.name}: {s.detail}"
                 for s in result.stages]
        if result.output_m2:
            lines.append(f"\nOutput: {result.output_m2}")
        return "\n".join(lines)
