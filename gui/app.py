"""Retroport desktop GUI.

A thin shell over retroport's headless pipeline. Three tabs:
  * Convert  - pick a model, toggle stages, run the end-to-end pipeline, watch logs.
  * Register - append CreatureModelData/CreatureDisplayInfo for a converted model
               (ID floors, TextureVariations, GeoBox from the model, repoint SQL).
  * Settings - point at the external tool exes (persisted with QSettings).

All heavy work runs in a background thread (PipelineWorker); the tool paths flow
into PipelineConfig.tools so the pipeline can call MultiConverter etc.
"""

from __future__ import annotations

import os
import shlex
import sys

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QTabWidget, QTextEdit, QProgressBar,
    QFileDialog, QMessageBox, QGroupBox,
)

from retroport.pipeline import PipelineConfig
from retroport.dbc import register_creature_model
from retroport.formats.md20 import read_bounds
from retroport.formats.verify import m2_data_block
from retroport.sql import SQLInputError
from .worker import PipelineWorker

ORG, APP = "Retroport", "RetroportGUI"

# settings key -> (label, is_file)
TOOL_FIELDS = [
    ("multiconverter", "MultiConverter_Console.exe (console build!)", True),
    ("m2mod", "M2Mod.exe (poly reduction)", True),
    ("blpconverter", "BLPConverter.exe", True),
    ("mpqeditor", "MPQEditor.exe", True),
    ("blender", "blender.exe", True),
    ("listfile", "listfile.csv", True),
]


def _row(line_edit: QLineEdit, on_browse) -> QHBoxLayout:
    h = QHBoxLayout()
    btn = QPushButton("Browse...")
    btn.setFixedWidth(90)
    btn.clicked.connect(on_browse)
    h.addWidget(line_edit)
    h.addWidget(btn)
    return h


class RetroportGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Retroport - Modern WoW → 3.3.5a")
        self.resize(820, 720)
        self.settings = QSettings(ORG, APP)
        self.worker: PipelineWorker | None = None
        self._build()

    # ---------------- UI ----------------
    def _build(self) -> None:
        layout = QVBoxLayout()
        tabs = QTabWidget()
        tabs.addTab(self._convert_tab(), "Convert")
        tabs.addTab(self._register_tab(), "Register")
        tabs.addTab(self._settings_tab(), "Settings")
        layout.addWidget(tabs)
        self.setLayout(layout)

    def _convert_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()

        form = QFormLayout()
        self.model_input = QLineEdit()
        form.addRow("Model (.m2):", _row(self.model_input, self._browse_model))
        self.out_input = QLineEdit(self.settings.value("last_out", ""))
        form.addRow("Output folder:", _row(self.out_input, self._browse_out))
        self.source_input = QLineEdit()
        form.addRow("Extraction root (optional):", _row(self.source_input, self._browse_source))

        stages = QGroupBox("Stages")
        sh = QHBoxLayout()
        self.cb_reinline = QCheckBox("Re-inline .skel"); self.cb_reinline.setChecked(True)
        self.cb_polyreduce = QCheckBox("Poly-reduce (M2i, Blender 3.4)"); self.cb_polyreduce.setChecked(False)
        self.cb_txid = QCheckBox("TXID textures (M2Mod)"); self.cb_txid.setChecked(False)
        self.cb_convert = QCheckBox("MultiConverter"); self.cb_convert.setChecked(True)
        self.cb_fixup = QCheckBox("Fixup"); self.cb_fixup.setChecked(True)
        self.cb_combiner = QCheckBox("Fix combiner flag"); self.cb_combiner.setChecked(True)
        self.cb_texbake = QCheckBox("Bake textures"); self.cb_texbake.setChecked(True)
        self.cb_texbake.setToolTip("After MultiConverter: fill empty type-0 texture slots from the "
                                   "model's TXID via the listfile (Settings)")
        self.cb_check = QCheckBox("Check"); self.cb_check.setChecked(True)
        for cb in (self.cb_reinline, self.cb_polyreduce, self.cb_txid, self.cb_convert, self.cb_fixup,
                   self.cb_combiner, self.cb_texbake, self.cb_check):
            sh.addWidget(cb)
        stages.setLayout(sh)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Clamp submeshes to:"))
        self.clamp_spin = QSpinBox(); self.clamp_spin.setRange(1, 256); self.clamp_spin.setValue(59)
        opts.addWidget(self.clamp_spin)
        opts.addWidget(QLabel("   nViews (0 = leave):"))
        self.nviews_spin = QSpinBox(); self.nviews_spin.setRange(0, 8); self.nviews_spin.setValue(0)
        opts.addWidget(self.nviews_spin)
        opts.addWidget(QLabel("   poly target:"))
        self.polytris_spin = QSpinBox(); self.polytris_spin.setRange(1000, 21845)
        self.polytris_spin.setSingleStep(500); self.polytris_spin.setValue(21000)
        opts.addWidget(self.polytris_spin)
        opts.addWidget(QLabel("   hair ratio:"))
        self.hairratio_spin = QDoubleSpinBox(); self.hairratio_spin.setRange(0.0, 1.0)
        self.hairratio_spin.setSingleStep(0.05); self.hairratio_spin.setValue(0.40)
        self.hairratio_spin.setToolTip("Decimate hair geosets to this ratio (0 = flat, same everywhere)")
        opts.addWidget(self.hairratio_spin)
        opts.addStretch()

        self.run_btn = QPushButton("Run Pipeline")
        self.run_btn.setFixedHeight(36)
        self.run_btn.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.run_btn.clicked.connect(self._run)

        self.progress = QProgressBar(); self.progress.setValue(0)
        self.console = QTextEdit(); self.console.setReadOnly(True)
        self.console.setStyleSheet("background-color:#1e1e1e; color:#00FF66; font-family:Consolas;")
        self.console.setPlaceholderText("Pipeline logs stream here...")

        v.addLayout(form)
        v.addWidget(stages)
        v.addLayout(opts)
        v.addWidget(self.run_btn)
        v.addWidget(self.progress)
        v.addWidget(self.console)
        w.setLayout(v)
        return w

    def _register_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        form = QFormLayout()
        self.reg_dbc_dir = QLineEdit()
        form.addRow("DBC folder (extracted):", _row(self.reg_dbc_dir, self._browse_dbc))
        self.reg_out_dir = QLineEdit()
        form.addRow("Output DBC folder:", _row(self.reg_out_dir, self._browse_regout))
        self.reg_path = QLineEdit()
        self.reg_path.setPlaceholderText(r"Creature\MyModel\mymodel.m2")
        form.addRow("In-game model path:", self.reg_path)
        self.reg_m2 = QLineEdit()
        self.reg_m2.setPlaceholderText("(optional) converted .m2 - fills the GeoBox from its bounds")
        form.addRow("Converted model:", _row(self.reg_m2, self._browse_reg_m2))
        self.reg_scale = QDoubleSpinBox(); self.reg_scale.setRange(0.01, 100.0); self.reg_scale.setValue(1.0)
        form.addRow("Model scale:", self.reg_scale)
        floors = QHBoxLayout()
        self.reg_display_floor = QSpinBox(); self.reg_display_floor.setRange(0, 2_000_000_000)
        self.reg_model_floor = QSpinBox(); self.reg_model_floor.setRange(0, 2_000_000_000)
        for sb, key in ((self.reg_display_floor, "reg_display_floor"), (self.reg_model_floor, "reg_model_floor")):
            sb.setValue(int(self.settings.value(key, 0) or 0))
            sb.setSpecialValueText("next free (max + 1)")
        floors.addWidget(QLabel("DisplayID from:")); floors.addWidget(self.reg_display_floor)
        floors.addWidget(QLabel("   ModelData ID from:")); floors.addWidget(self.reg_model_floor)
        form.addRow("ID floors:", floors)
        tv = QHBoxLayout()
        self.reg_tv = [QLineEdit() for _ in range(3)]
        for i, le in enumerate(self.reg_tv, 1):
            le.setPlaceholderText(f"TextureVariation{i} (bare name)")
            tv.addWidget(le)
        form.addRow("Texture variations:", tv)
        self.reg_geoset = QSpinBox(); self.reg_geoset.setRange(0, 2_000_000_000)
        form.addRow("CreatureGeosetData:", self.reg_geoset)
        self.reg_entries = QLineEdit(); self.reg_entries.setPlaceholderText("(optional) e.g. 1234, 5678")
        form.addRow("Creature entries to repoint:", self.reg_entries)
        self.reg_search = QLineEdit(); self.reg_search.setPlaceholderText("(optional) creature name for a search query")
        form.addRow("Creature name:", self.reg_search)
        self.reg_btn = QPushButton("Add CreatureModelData + DisplayInfo")
        self.reg_btn.clicked.connect(self._register)
        self.reg_out = QTextEdit(); self.reg_out.setReadOnly(True)
        self.reg_out.setStyleSheet("font-family:Consolas;")
        v.addLayout(form)
        v.addWidget(self.reg_btn)
        v.addWidget(self.reg_out)
        v.addStretch()
        w.setLayout(v)
        return w

    def _settings_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        form = QFormLayout()
        self.tool_inputs: dict[str, QLineEdit] = {}
        for key, label, _is_file in TOOL_FIELDS:
            le = QLineEdit(self.settings.value(f"tool_{key}", ""))
            self.tool_inputs[key] = le
            form.addRow(label + ":", _row(le, lambda _=False, k=key: self._browse_tool(k)))
        self.mc_args_input = QLineEdit(self.settings.value("multiconverter_args", ""))
        self.mc_args_input.setPlaceholderText("(optional) e.g. -fixhelm    -    use {file} to control where the path goes")
        form.addRow("MultiConverter args:", self.mc_args_input)
        save = QPushButton("Save Settings")
        save.clicked.connect(self._save_settings)
        v.addLayout(form)
        v.addWidget(save)
        v.addStretch()
        w.setLayout(v)
        return w

    # ---------------- browse helpers ----------------
    def _browse_model(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select .m2", "", "M2 models (*.m2)")
        if f:
            self.model_input.setText(f)
            if not self.out_input.text():
                self.out_input.setText(os.path.join(os.path.dirname(f), "converted"))

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d: self.out_input.setText(d)

    def _browse_source(self):
        d = QFileDialog.getExistingDirectory(self, "Extraction root")
        if d: self.source_input.setText(d)

    def _browse_dbc(self):
        d = QFileDialog.getExistingDirectory(self, "DBC folder")
        if d: self.reg_dbc_dir.setText(d)

    def _browse_reg_m2(self):
        f, _ = QFileDialog.getOpenFileName(self, "Converted .m2", "", "M2 models (*.m2)")
        if f: self.reg_m2.setText(f)

    def _browse_regout(self):
        d = QFileDialog.getExistingDirectory(self, "Output DBC folder")
        if d: self.reg_out_dir.setText(d)

    def _browse_tool(self, key: str):
        f, _ = QFileDialog.getOpenFileName(self, f"Select {key}", "", "All files (*.*)")
        if f: self.tool_inputs[key].setText(f)

    def _save_settings(self):
        for key, le in self.tool_inputs.items():
            self.settings.setValue(f"tool_{key}", le.text().strip())
        self.settings.setValue("multiconverter_args", self.mc_args_input.text().strip())
        QMessageBox.information(self, "Saved", "Tool paths saved.")

    def _tools(self) -> dict:
        return {key: self.settings.value(f"tool_{key}", "") for key, _l, _f in TOOL_FIELDS}

    # ---------------- actions ----------------
    def _run(self):
        model = self.model_input.text().strip()
        out = self.out_input.text().strip()
        if not os.path.isfile(model):
            QMessageBox.warning(self, "Missing model", "Pick a valid .m2 file.")
            return
        if not out:
            QMessageBox.warning(self, "Missing output", "Pick an output folder.")
            return
        self.settings.setValue("last_out", out)

        cfg = PipelineConfig(
            model=model, out_dir=out, source=self.source_input.text().strip(),
            tools=self._tools(),
            do_reinline=self.cb_reinline.isChecked(),
            do_convert=self.cb_convert.isChecked(),
            do_fixup=self.cb_fixup.isChecked(),
            do_check=self.cb_check.isChecked(),
            do_polyreduce=self.cb_polyreduce.isChecked(),
            do_txid_remove=self.cb_txid.isChecked(),
            clamp_submeshes_to=self.clamp_spin.value(),
            poly_target_tris=self.polytris_spin.value(),
            poly_hair_ratio=self.hairratio_spin.value(),
            expected_skins=self.nviews_spin.value() or None,
            multiconverter_args=shlex.split(self.settings.value("multiconverter_args", "") or ""),
            fix_combiner=self.cb_combiner.isChecked(),
            do_texbake=self.cb_texbake.isChecked(),
        )
        self.run_btn.setEnabled(False)
        self.console.clear()
        self.progress.setValue(0)
        self.worker = PipelineWorker(cfg)
        self.worker.log.connect(self.console.append)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.done.connect(self._finished)
        self.worker.start()

    def _finished(self, ok: bool, summary: str):
        self.run_btn.setEnabled(True)
        self.console.append("\n=== SUMMARY ===\n" + summary)
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "Pipeline complete" if ok else "Finished with blockers", summary[:600])

    def _register(self):
        dbc_dir = self.reg_dbc_dir.text().strip()
        out_dir = self.reg_out_dir.text().strip()
        path = self.reg_path.text().strip()
        if not (os.path.isdir(dbc_dir) and out_dir and path):
            QMessageBox.warning(self, "Missing fields", "Fill DBC folder, output folder, and in-game path.")
            return
        self.settings.setValue("reg_display_floor", self.reg_display_floor.value())
        self.settings.setValue("reg_model_floor", self.reg_model_floor.value())
        try:
            bounds = None
            m2 = self.reg_m2.text().strip()
            if m2:
                with open(m2, "rb") as fh:
                    bounds = read_bounds(m2_data_block(fh.read()))
            r = register_creature_model(
                dbc_dir, out_dir, path, self.reg_scale.value(),
                display_floor=self.reg_display_floor.value() or None,
                modeldata_floor=self.reg_model_floor.value() or None,
                texture_variations=tuple(le.text().strip() for le in self.reg_tv),
                geoset_data=self.reg_geoset.value(), bounds=bounds,
                creature_entries=self.reg_entries.text(), search_term=self.reg_search.text())
        except (OSError, ValueError, SQLInputError) as exc:
            QMessageBox.critical(self, "Registration failed", str(exc))
            return
        self.reg_out.setPlainText(
            f"CreatureDisplayInfo id: {r.display_id}   <- creature_template_model.CreatureDisplayID\n"
            f"CreatureModelData id : {r.model_id}  -> {r.model_name}\n"
            f"Patched DBCs written to: {out_dir}\n"
            "Put BOTH DBCs in the client patch (DBFilesClient\\) AND the server's dbc\\ folder.\n\n"
            f"{r.sql}")


def main() -> None:
    app = QApplication(sys.argv)
    win = RetroportGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
