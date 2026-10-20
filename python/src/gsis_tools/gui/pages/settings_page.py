"""汎用アプリの設定画面。導出量は編集のたびに更新する。"""

from __future__ import annotations

from dataclasses import fields

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QScrollArea, QSpinBox, QTableWidget, QVBoxLayout, QWidget)

from ..casefile import KNOWN_MODULE_KEYS
from ..boundaries import boundary_notes
from ..check_case import CheckCasePanel, TET_WARNING
from ..general_settings import GASES, GeneralSettings, derived
from ..widgets.common import FloatField, KeyValueTable, StatusLabel, heading, muted, primary_button, readonly_item


class SettingsPage(QWidget):
    status = Signal(str)
    changed = Signal()
    accepted = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._loading = False
        self._base = GeneralSettings()
        self.inputs = {}
        root = QVBoxLayout(self)
        root.addWidget(heading("設定"))
        root.addWidget(muted("基準の温度・圧力はここで入力できます。境界条件から更新する窓口も用意しています。"))
        split = QHBoxLayout()
        root.addLayout(split, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        split.addWidget(scroll, 3)
        self.mode = QComboBox()
        for key, label in (("gsis", "GSIS-PBC"), ("cis", "CIS 単独"), ("nsf", "NSF のみ(PBC 単独)")):
            self.mode.addItem(label, key)
        form.addRow("計算モード", self.mode)
        self.gas = QComboBox()
        self.gas.addItems([*GASES, "任意"])
        form.addRow("気体", self.gas)
        self.gas_note = muted("")
        form.addRow(self.gas_note)
        for key, label in (("molecular_mass", "分子の質量 [kg]"), ("mu_ref", "μ_ref [Pa·s]"),
                           ("T_ref", "粘性の基準温度 [K]"), ("omega", "粘性のべき指数 ω")):
            self._float(form, key, label)
        self.length_auto = QCheckBox("形状の外接箱の最小辺を使う")
        form.addRow(self.length_auto)
        self._float(form, "wall_temperature", "既定の壁温度 [K]")
        for key, label in (("L_ref", "基準長さ L_ref [m]"), ("T0", "基準温度 T0 [K]"), ("p_ref", "基準圧力 [Pa]"),
                           ("T_min", "最低温度 [K]"), ("T_max", "最高温度 [K]"), ("u_max", "境界の最大速度 [m/s]")):
            self._float(form, key, label, zero=key == "u_max")
        for key, label in (("n_cis", "GSIS: n_cis"), ("max_pbc", "GSIS: max_pbc")):
            self._integer(form, key, label, 1)
        self._float(form, "cfl_pbc", "GSIS: cfl_pbc")
        self._float(form, "tolerance", "収束の許容誤差")
        self.advanced_toggle = QCheckBox("上級の設定を開く")
        form.addRow(self.advanced_toggle)
        self.advanced = QGroupBox("上級 / メモリの見積もり")
        af = QFormLayout(self.advanced)
        self._integer(af, "velocity_chunk", "速度チャンク(0: 自動)", 0)
        self._integer(af, "estimated_cells", "見積もりのセル数", 1)
        self._float(af, "free_vram_gib", "空き VRAM の仮定 [GiB]")
        self.kinetic_table = KeyValueTable({k: v for k, v in KNOWN_MODULE_KEYS["kinetic"].items()
                                           if k not in ("velocity_chunk", "precision")})
        self.macro_table = KeyValueTable(KNOWN_MODULE_KEYS["macro"])
        af.addRow("運動論のパラメータ(FP32)", self.kinetic_table)
        af.addRow("PBC のパラメータ", self.macro_table)
        self.kinetic_table.changed.connect(self._edited)
        self.macro_table.changed.connect(self._edited)
        ctx.theme.changed.connect(self._restyle)
        form.addRow(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.velocity_auto = QCheckBox("速度格子を自動で決める")
        form.addRow(self.velocity_auto)
        self.manual = QGroupBox("手動の速度格子(範囲は sqrt(R*T0) 単位)")
        mf = QFormLayout(self.manual)
        self.vel_n, self.vel_lo, self.vel_hi = [], [], []
        for axis in "xyz":
            row = QHBoxLayout()
            n = QSpinBox()
            n.setRange(2, 10000)
            lo, hi = FloatField(-5), FloatField(5)
            for w in (n, lo, hi):
                row.addWidget(w)
            mf.addRow(axis + ": 点数・下限・上限", row)
            self.vel_n.append(n)
            self.vel_lo.append(lo)
            self.vel_hi.append(hi)
            n.valueChanged.connect(self._edited)
            lo.valueChanged.connect(self._edited)
            hi.valueChanged.connect(self._edited)
        form.addRow(self.manual)
        right = QVBoxLayout()
        split.addLayout(right, 2)
        right.addWidget(heading("導出量"))
        self.derived_table = QTableWidget(0, 2)
        self.derived_table.setHorizontalHeaderLabels(["量", "値"])
        self.derived_table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.derived_table)
        self.note = StatusLabel("")
        right.addWidget(self.note)
        self.check_panel = CheckCasePanel()
        self.boundary_note = StatusLabel()
        right.addWidget(self.boundary_note)
        right.addWidget(self.check_panel, 1)
        self.accept_btn = primary_button("設定を確定して境界条件へ")
        right.addWidget(self.accept_btn)
        self.accept_btn.clicked.connect(self.accepted)
        self.mode.currentIndexChanged.connect(self._edited)
        self.gas.currentTextChanged.connect(self._preset)
        self.length_auto.toggled.connect(self._edited)
        self.velocity_auto.toggled.connect(self._edited)
        self.load_settings(self._base)

    def _float(self, form, key, label, zero=False):
        w = FloatField(1, positive=not zero, non_negative=zero)
        self.inputs[key] = w
        form.addRow(label, w)
        w.valueChanged.connect(self._edited)

    def _integer(self, form, key, label, minimum):
        w = QSpinBox()
        w.setRange(minimum, 2_000_000_000)
        self.inputs[key] = w
        form.addRow(label, w)
        w.valueChanged.connect(self._edited)

    def load_settings(self, s: GeneralSettings) -> None:
        self._loading = True
        self._base = s
        self.mode.setCurrentIndex(self.mode.findData(s.mode))
        self.gas.setCurrentText(s.gas if s.gas in GASES else "任意")
        for key, w in self.inputs.items():
            w.setValue(getattr(s, key))
        self.length_auto.setChecked(s.length_auto)
        self.velocity_auto.setChecked(s.velocity_auto)
        self.kinetic_table.set_values({k: v for k, v in s.kinetic.items() if k != "precision"})
        self.macro_table.set_values(s.macro)
        for i in range(3):
            self.vel_n[i].setValue(s.vel_n[i])
            self.vel_lo[i].setValue(s.vel_range[i][0])
            self.vel_hi[i].setValue(s.vel_range[i][1])
        self._loading = False
        self._restyle()
        self.refresh()

    def collect(self) -> GeneralSettings:
        values = {f.name: getattr(self._base, f.name) for f in fields(GeneralSettings)}
        for key, w in self.inputs.items():
            if isinstance(w, FloatField) and not w.is_valid():
                raise ValueError(f"{key} の入力を確認してください")
            values[key] = w.value()
        values.update(mode=self.mode.currentData(), gas=self.gas.currentText(), length_auto=self.length_auto.isChecked(),
                      velocity_auto=self.velocity_auto.isChecked(), vel_n=[w.value() for w in self.vel_n])
        for table in (self.kinetic_table, self.macro_table):
            problems = table.problems()
            if problems:
                raise ValueError("・".join(problems))
        values["kinetic"] = self.kinetic_table.values()
        if "precision" in self._base.kinetic:
            # 精度の選択は実行画面。読み込んだケースの値は編集対象外でも保持する。
            values["kinetic"]["precision"] = self._base.kinetic["precision"]
        values["macro"] = self.macro_table.values()
        if not values["velocity_auto"] and any(not w.is_valid() for w in self.vel_lo + self.vel_hi):
            raise ValueError("速度格子の範囲の入力を確認してください")
        values["vel_range"] = (self._base.vel_range if values["velocity_auto"] else
                               [[lo.value(), hi.value()] for lo, hi in zip(self.vel_lo, self.vel_hi)])
        s = GeneralSettings(**values)
        s.validate()
        return s

    def _preset(self, key):
        if self._loading:
            return
        self._loading = True
        for name, value in GASES.get(key, {}).items():
            self.inputs[name].setValue(value)
        self._loading = False
        self._edited()

    def _restyle(self, *_):
        for table in (self.kinetic_table, self.macro_table):
            table.restyle(self.ctx.theme.tokens())

    def _edited(self, *_):
        if not self._loading:
            self.refresh()
            self.changed.emit()

    def refresh(self):
        self.manual.setEnabled(not self.velocity_auto.isChecked())
        self.inputs["L_ref"].setEnabled(not self.length_auto.isChecked())
        for key in ("molecular_mass", "mu_ref", "T_ref", "omega"):
            self.inputs[key].setEnabled(self.gas.currentText() == "任意")
        self.gas_note.setText("He は v1 の物性値です。" if self.gas.currentText() == "He" else
                              "Ne・Ar・Kr・Xe: Bird (1994) 表 A1、273 K (VHS)。値は統括役の提供。")
        try:
            s = self.collect()
            notes = boundary_notes(s, self.ctx.project.boundaries) if getattr(self.ctx, "project", None) else []
            self.boundary_note.set_status("warning" if any("NSF" in n for n in notes) else "info", "\n".join(notes))
            self.boundary_note.setVisible(bool(notes))
            d = derived(s)
            m = d["memory"]
            rows = [("R [J/(kg·K)]", f"{d['R']:.7g}"), ("μ(T0) [Pa·s]", f"{d['mu']:.7g}"),
                    ("平均自由行程 [m]", f"{d['mean_free_path']:.7g}"), ("Kn", f"{d['Kn']:.7g}"),
                    ("速度格子", " × ".join(map(str, d['n']))), ("速度点の総数", str(d['n_velocity'])),
                      ("各軸の範囲 / sqrt(R*T0)", "; ".join("[" + ", ".join(f"{v:.6g}" for v in pair) + "]" for pair in d["ranges"])),
                    ("f [GiB]", f"{m['distribution'] / 2**30:.3f}"),
                    ("作業領域 [GiB]", f"{m['workspace'] / 2**30:.3f}"), ("チャンク幅", str(m['chunk'])),
                    ("合計の概算 [GiB]", f"{m['total'] / 2**30:.3f}"), ("予算 [GiB]", f"{m['budget'] / 2**30:.3f}")]
            self.derived_table.setRowCount(len(rows))
            for r, pair in enumerate(rows):
                for c, text in enumerate(pair):
                    self.derived_table.setItem(r, c, readonly_item(text))
            self.derived_table.resizeColumnToContents(0)
            note = "Python の VRAM は概算です。固定費は暫定値。C++ の検査結果は下に表示します。"
            if not m["fits"]:
                note = "Python の VRAM 概算が予算を超えています。実際のメッシュを C++ で再検査します。"
            tet_warning = s.mode == "gsis" and getattr(self.ctx, "project", None) and self.ctx.project.mesh.get("contains_tetrahedra")
            if tet_warning:
                note += " / " + TET_WARNING
            self.note.set_status("warning" if not m["fits"] or tet_warning else "info", note)
            self.accept_btn.setEnabled(True)
        except (ValueError, OverflowError, TypeError) as exc:
            self.derived_table.setRowCount(0)
            self.note.set_status("error", str(exc))
            self.accept_btn.setEnabled(False)
