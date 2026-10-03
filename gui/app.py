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
    QFileDialog, QMessageBox, QGroupBox, QComboBox,
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
        tabs.addTab(self._item_tab(), "Item")
        tabs.addTab(self._textures_tab(), "Textures")
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
        self.cb_convert = QCheckBox("Convert (MD21->MD20)"); self.cb_convert.setChecked(True)
        self.cb_convert.setToolTip("Runs the conversion engine chosen in Settings (wotlkconv or MultiConverter)")
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
        self.cb_helmset = QCheckBox("Helm set: convert every 3.3.5 race/gender variant of this helm "
                                    "(-fixhelm for classic models, see Settings)")
        self.cb_helmset.setToolTip("Picks up <base>_hum.m2, <base>_bef.m2 ... next to the chosen model")
        form.addRow("", self.cb_helmset)

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

    def _item_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        form = QFormLayout()
        self.item_dbc_dir = QLineEdit(self.settings.value("item_dbc_dir", ""))
        form.addRow("DBC folder (ItemDisplayInfo, Item):",
                    _row(self.item_dbc_dir, lambda: self._pick_dir(self.item_dbc_dir, "DBC folder")))
        self.item_out_dir = QLineEdit(self.settings.value("item_out_dir", ""))
        form.addRow("Output DBC folder:", _row(self.item_out_dir, lambda: self._pick_dir(self.item_out_dir, "Output")))
        self.item_like_display = QSpinBox(); self.item_like_display.setRange(0, 2_000_000_000)
        self.item_like_display.setSpecialValueText("none (fresh row)")
        self.item_like_display.setToolTip("Clone an existing display of the same kind (e.g. a Blizzard 2H axe) "
                                          "to inherit its sound group and flags; blank fields below keep its values")
        form.addRow("Clone DisplayID:", self.item_like_display)
        self.item_m2 = QLineEdit()
        self.item_m2.setPlaceholderText("(optional) converted .m2 - 'Fill' reads model + texture from it")
        fill = QHBoxLayout()
        fill.addLayout(_row(self.item_m2, lambda: self._pick_file(self.item_m2, "Converted .m2", "M2 models (*.m2)")))
        fill_btn = QPushButton("Fill"); fill_btn.setFixedWidth(60); fill_btn.clicked.connect(self._item_fill)
        fill.addWidget(fill_btn)
        form.addRow("From model:", fill)
        self.item_model = QLineEdit(); self.item_model.setPlaceholderText("Axe_2H_Foo.mdx")
        self.item_model_r = QLineEdit(); self.item_model_r.setPlaceholderText("(shoulders) RShoulder_Foo.mdx")
        self.item_tex = QLineEdit(); self.item_tex.setPlaceholderText("bare .blp name in the model's folder")
        self.item_tex_r = QLineEdit()
        self.item_icon = QLineEdit(); self.item_icon.setPlaceholderText("INV_Axe_04")
        form.addRow("Model:", self.item_model)
        form.addRow("Right model:", self.item_model_r)
        form.addRow("Model texture:", self.item_tex)
        form.addRow("Right texture:", self.item_tex_r)
        form.addRow("Icon:", self.item_icon)
        nums = QHBoxLayout()
        self.item_sound = QSpinBox(); self.item_sound.setRange(-1, 1000); self.item_sound.setValue(-1)
        self.item_sound.setSpecialValueText("keep")
        self.item_visual = QSpinBox(); self.item_visual.setRange(-1, 100000); self.item_visual.setValue(-1)
        self.item_visual.setSpecialValueText("keep")
        self.item_floor = QSpinBox(); self.item_floor.setRange(0, 2_000_000_000)
        self.item_floor.setSpecialValueText("next free")
        for label, sb in (("Sound group:", self.item_sound), ("   ItemVisual:", self.item_visual),
                          ("   DisplayID from:", self.item_floor)):
            nums.addWidget(QLabel(label)); nums.addWidget(sb)
        form.addRow("", nums)

        armor = QGroupBox("Armor body textures (bare names, no _U/_M/_F)")
        af = QFormLayout()
        from retroport.itemdbc import COMPONENT_SECTIONS
        self.item_comp = {}
        for section in COMPONENT_SECTIONS:
            le = QLineEdit(); self.item_comp[section] = le
            af.addRow(section + ":", le)
        armor.setLayout(af)
        armor.setCheckable(True); armor.setChecked(False)
        armor.toggled.connect(lambda on: [le.setEnabled(on) for le in self.item_comp.values()])
        for le in self.item_comp.values():
            le.setEnabled(False)
        self.item_armor_box = armor

        items = QFormLayout()
        self.item_entries = QLineEdit(); self.item_entries.setPlaceholderText("(optional) item_template entries, e.g. 90001, 90002")
        self.item_like = QSpinBox(); self.item_like.setRange(0, 2_000_000_000)
        self.item_like.setSpecialValueText("none")
        self.item_like.setToolTip("Existing item entry whose Item.dbc row is copied for entries not yet in Item.dbc "
                                  "(needed for creature_equip_template)")
        items.addRow("Item entries to repoint:", self.item_entries)
        items.addRow("New entries copy Item.dbc row of:", self.item_like)

        btn = QPushButton("Add ItemDisplayInfo")
        btn.clicked.connect(self._register_item)
        self.item_out = QTextEdit(); self.item_out.setReadOnly(True)
        self.item_out.setStyleSheet("font-family:Consolas;")
        v.addLayout(form)
        v.addWidget(armor)
        v.addLayout(items)
        v.addWidget(btn)
        v.addWidget(self.item_out)
        w.setLayout(v)
        return w

    def _textures_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        form = QFormLayout()
        self.tex_inputs = QTextEdit()
        self.tex_inputs.setPlaceholderText(".blp files or folders, one per line (or use the buttons)")
        self.tex_inputs.setFixedHeight(90)
        btns = QHBoxLayout()
        add_files = QPushButton("Add files..."); add_files.clicked.connect(self._tex_add_files)
        add_dir = QPushButton("Add folder..."); add_dir.clicked.connect(self._tex_add_dir)
        btns.addWidget(add_files); btns.addWidget(add_dir); btns.addStretch()
        form.addRow("Inputs:", self.tex_inputs)
        form.addRow("", btns)
        self.tex_out = QLineEdit(self.settings.value("tex_out", ""))
        form.addRow("Output folder:", _row(self.tex_out, lambda: self._pick_dir(self.tex_out, "Output")))
        self.tex_target = QComboBox()
        self.tex_target.addItem("Classic (armor regions 128x64 / 128x32)", "classic")
        self.tex_target.addItem("HD (armor regions 256x128 / 256x64)", "hd")
        self.tex_target.setCurrentIndex(max(0, self.tex_target.findData(self.settings.value("client_models", "classic"))))
        form.addRow("Target:", self.tex_target)
        self.tex_max = QSpinBox(); self.tex_max.setRange(0, 4096); self.tex_max.setSingleStep(256)
        self.tex_max.setSpecialValueText("keep size"); self.tex_max.setValue(int(self.settings.value("tex_max", 0) or 0))
        form.addRow("Shrink weapon/helm textures above:", self.tex_max)
        run = QPushButton("Convert textures"); run.clicked.connect(self._run_textures)
        self.tex_log = QTextEdit(); self.tex_log.setReadOnly(True); self.tex_log.setStyleSheet("font-family:Consolas;")
        note = QLabel("Armor textures (…_tu_m.blp, or in a <Section>Texture folder) are resized to their body "
                      "region and saved palettized like Blizzard's; everything else is treated as an object "
                      "texture and only shrunk/re-encoded when needed.")
        note.setWordWrap(True)
        v.addLayout(form)
        v.addWidget(note)
        v.addWidget(run)
        v.addWidget(self.tex_log)
        w.setLayout(v)
        return w

    def _tex_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "BLP textures", "", "BLP textures (*.blp)")
        for f in files:
            self.tex_inputs.append(f)

    def _tex_add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Folder of .blp textures")
        if d:
            self.tex_inputs.append(d)

    def _run_textures(self):
        from retroport.textures import convert_textures
        paths = [p.strip() for p in self.tex_inputs.toPlainText().splitlines() if p.strip()]
        out = self.tex_out.text().strip()
        if not (paths and out):
            QMessageBox.warning(self, "Missing fields", "Add at least one input and pick an output folder.")
            return
        self.settings.setValue("tex_out", out)
        self.settings.setValue("tex_max", self.tex_max.value())
        try:
            results = convert_textures(paths, out, target=self.tex_target.currentData(),
                                       max_object=self.tex_max.value() or None)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Texture conversion failed", str(exc))
            return
        lines = []
        for r in results:
            lines.append(f"{r.kind:9} {(r.section or ''):10} {r.source_size[0]}x{r.source_size[1]} -> "
                         f"{r.size[0]}x{r.size[1]} {r.format:10} {r.output}")
            if r.dbc_name:
                lines.append(f"{'':21}ItemDisplayInfo name: {r.dbc_name}")
            lines += [f"{'':21}! {x}" for x in r.warnings]
        self.tex_log.setPlainText("\n".join(lines) or "No .blp files found.")

    def _settings_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        form = QFormLayout()
        self.tool_inputs: dict[str, QLineEdit] = {}
        for key, label, _is_file in TOOL_FIELDS:
            le = QLineEdit(self.settings.value(f"tool_{key}", ""))
            self.tool_inputs[key] = le
            form.addRow(label + ":", _row(le, lambda _=False, k=key: self._browse_tool(k)))
        from retroport.convert import engine as _engine
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("wotlkconv (built-in Python converter)"
                                  + ("" if _engine.available() else "  - NOT INSTALLED"), "wotlkconv")
        self.engine_combo.addItem("MultiConverter (external exe above)", "multiconverter")
        self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(self.settings.value("engine", "wotlkconv"))))
        self.engine_combo.setToolTip("wotlkconv also merges .skel skeletons and converts skins/.anim files itself; "
                                     "MultiConverter is needed only for classic-model helms (-fixhelm)")
        form.addRow("Conversion engine:", self.engine_combo)
        self.client_models = QComboBox()
        self.client_models.addItem("Classic 3.3.5 character models (256 skin)", "classic")
        self.client_models.addItem("HD character models, e.g. 3.3.5 HD patch (512 skin)", "hd")
        self.client_models.setCurrentIndex(max(0, self.client_models.findData(self.settings.value("client_models", "classic"))))
        self.client_models.setToolTip("Sets the armor texture size (Textures tab) and whether helms get "
                                      "MultiConverter's -fixhelm (classic only)")
        form.addRow("Client character models:", self.client_models)
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
            from retroport.helms import helm_base
            self.model_input.setText(f)
            self.cb_helmset.setChecked(bool(helm_base(f)) and os.path.basename(f).lower().startswith("helm"))
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

    def _pick_dir(self, le: QLineEdit, title: str):
        d = QFileDialog.getExistingDirectory(self, title)
        if d: le.setText(d)

    def _pick_file(self, le: QLineEdit, title: str, filt: str):
        f, _ = QFileDialog.getOpenFileName(self, title, "", filt)
        if f: le.setText(f)

    def _item_fill(self):
        from retroport.itemdbc import suggest_from_model
        m2 = self.item_m2.text().strip()
        if not os.path.isfile(m2):
            QMessageBox.warning(self, "No model", "Pick the converted .m2 first.")
            return
        hint = suggest_from_model(m2)
        self.item_model.setText(hint["model"])
        if hint["texture"]:
            self.item_tex.setText(hint["texture"])

    def _register_item(self):
        from retroport.itemdbc import register_item_display
        dbc_dir, out_dir = self.item_dbc_dir.text().strip(), self.item_out_dir.text().strip()
        if not (os.path.isdir(dbc_dir) and out_dir):
            QMessageBox.warning(self, "Missing fields", "Fill the DBC folder and the output folder.")
            return
        self.settings.setValue("item_dbc_dir", dbc_dir)
        self.settings.setValue("item_out_dir", out_dir)

        def text(le: QLineEdit):
            t = le.text().strip()
            return t or None                      # blank = keep the cloned value / leave empty

        comps = ({k: le.text().strip() for k, le in self.item_comp.items() if le.text().strip()}
                 if self.item_armor_box.isChecked() else None)
        try:
            r = register_item_display(
                dbc_dir, out_dir,
                template_display_id=self.item_like_display.value() or None,
                model=text(self.item_model), model_right=text(self.item_model_r),
                texture=text(self.item_tex), texture_right=text(self.item_tex_r), icon=text(self.item_icon),
                component_textures=comps or None,
                sound_group=None if self.item_sound.value() < 0 else self.item_sound.value(),
                item_visual=None if self.item_visual.value() < 0 else self.item_visual.value(),
                display_floor=self.item_floor.value() or None,
                item_entries=self.item_entries.text(), item_like=self.item_like.value() or None)
        except (OSError, ValueError, SQLInputError) as exc:
            QMessageBox.critical(self, "Item registration failed", str(exc))
            return
        lines = [f"ItemDisplayInfo id: {r.display_id}   <- item_template.displayid",
                 "Row: " + ", ".join(f"{k}={v}" for k, v in r.row.items())]
        if r.items_updated:
            lines.append(f"Item.dbc repointed: {', '.join(map(str, r.items_updated))}")
        if r.items_added:
            lines.append(f"Item.dbc added: {', '.join(map(str, r.items_added))}")
        if r.items_missing:
            lines.append(f"NOT in Item.dbc (NPC equipment will drop them): {', '.join(map(str, r.items_missing))}")
        lines += [f"Patched DBCs written to: {out_dir}", "", "Pack into the patch MPQ:"]
        lines += [f"  {p}" for p in r.pack]
        lines += ["", r.sql]
        self.item_out.setPlainText("\n".join(lines))

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
        self.settings.setValue("client_models", self.client_models.currentData())
        self.settings.setValue("engine", self.engine_combo.currentData())
        self.tex_target.setCurrentIndex(self.tex_target.findData(self.client_models.currentData()))
        QMessageBox.information(self, "Saved", "Settings saved.")

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
            engine=self.settings.value("engine", "wotlkconv") or "wotlkconv",
            do_texbake=self.cb_texbake.isChecked(),
        )
        self.run_btn.setEnabled(False)
        self.console.clear()
        self.progress.setValue(0)
        helm_client = self.settings.value("client_models", "classic") if self.cb_helmset.isChecked() else None
        self.worker = PipelineWorker(cfg, helm_client=helm_client)
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
