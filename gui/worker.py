"""Background worker: runs the headless Pipeline and bridges its callbacks to Qt
signals so the UI stays responsive (same pattern as RaceBuilderWorker)."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from retroport.pipeline import Pipeline, PipelineConfig, PipelineResult


class PipelineWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    done = pyqtSignal(bool, str)

    def __init__(self, cfg: PipelineConfig):
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:
        try:
            pipeline = Pipeline(self.cfg, on_log=self.log.emit, on_progress=self.progress.emit)
            result = pipeline.run()
            self.done.emit(result.ok, self._summary(result))
        except Exception as exc:  # never let the thread die silently
            self.log.emit(f"CRITICAL PIPELINE ERROR: {exc}")
            self.done.emit(False, str(exc))

    @staticmethod
    def _summary(result: PipelineResult) -> str:
        lines = [f"{'OK' if s.ok else ('SKIP' if s.skipped else 'FAIL')}  {s.name}: {s.detail}"
                 for s in result.stages]
        if result.output_m2:
            lines.append(f"\nOutput: {result.output_m2}")
        return "\n".join(lines)
