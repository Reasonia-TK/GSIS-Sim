"""結果の画面の「線上の分布」: 任意の 2 点を結ぶ線の上の分布をグラフにし、CSV に書き出す。

値はセル値を点へ補間したもの(VTK の probe)。中心線(Fig.3)のタブの、軸の近くのセルに当てはめて r = 0 に
外挿した値とは少し違う(こちらは補間した値そのもの)。計算は別スレッド(JobRunner)で行う。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)

from ..context import AppContext
from ..fields import LINE_PRESETS, LineProfile, ResultBundle, list_fields, profile_scale, sample_line, write_profile_csv
from ..widgets.common import StatusLabel, muted, primary_button
from ..widgets.plots import PlotPanel
from ..workers import JobRunner
from .results_3d import Vec3Field

X_AXES = {"s": "s(点 A からの距離)", "x": "x", "y": "y", "z": "z", "X": "X = x − 8(管の入口 0、出口 10)"}
DEFAULT_KEYS = ("p", "u_x", "U_x", "T")
MAX_PLOTS = 6


def _canon(key: str) -> str:
    """選択を引き継ぐときの比べ方(速度の名前 U と u を同じにする)。"""
    if key.startswith("U_") or key == "|U|":
        return key.replace("U", "u", 1)
    return key


def profile_keys(bundle: ResultBundle) -> list[str]:
    """線上の分布で描ける量のキー(VTU のセル値の名前から。ベクトルは成分と大きさに分ける)。"""
    keys = []
    for spec in list_fields(bundle.grid):
        if spec.n_comp == 1:
            keys.append(spec.name)
        elif spec.n_comp == 3:
            keys += [f"{spec.name}_x", f"{spec.name}_y", f"{spec.name}_z", f"|{spec.name}|"]
        elif spec.n_comp == 6 and spec.name == "sigma":
            keys += [f"sigma_{c}" for c in ("xx", "yy", "zz", "xy", "yz", "xz")]
    return keys


class LineProfileTab(QWidget):
    """「線上の分布」のタブ。"""

    status = Signal(str)
    line_changed = Signal()

    def __init__(self, ctx: AppContext, runner: JobRunner, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self.bundle: ResultBundle | None = None
        self.profile: LineProfile | None = None
        self._build()

    def _build(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        host = QWidget()
        f = QFormLayout(host)
        self.preset = QComboBox()
        for k, (label, _a, _b) in LINE_PRESETS.items():
            self.preset.addItem(label, k)
        self.a_field = Vec3Field(LINE_PRESETS["axis_tube"][1])
        self.b_field = Vec3Field(LINE_PRESETS["axis_tube"][2])
        self.n_spin = QSpinBox()
        self.n_spin.setRange(2, 20000)
        self.n_spin.setValue(300)
        self.qty_list = QListWidget()
        self.qty_list.setMinimumHeight(150)
        self.nondim_check = QCheckBox("無次元で表示(p/p_in、u/√(2RT0)、T/T0 など)")
        self.nondim_check.setChecked(True)
        self.xaxis = QComboBox()
        for k, v in X_AXES.items():
            self.xaxis.addItem(v, k)
        self.show3d_check = QCheckBox("3D 表示に線を出す")
        self.show3d_check.setChecked(False)
        self.calc_btn = primary_button("計算")
        self.fig_btn = QPushButton("図を保存…")
        self.csv_btn = QPushButton("CSV を書き出す…")
        self.info = StatusLabel("")
        f.addRow("プリセット", self.preset)
        f.addRow("点 A [L_ref]", self.a_field)
        f.addRow("点 B [L_ref]", self.b_field)
        f.addRow("点の数", self.n_spin)
        f.addRow("描く量(最大 6)", self.qty_list)
        f.addRow(self.nondim_check)
        f.addRow("横軸", self.xaxis)
        f.addRow(self.show3d_check)
        f.addRow(self.calc_btn)
        btns = QHBoxLayout()
        btns.addWidget(self.fig_btn)
        btns.addWidget(self.csv_btn)
        f.addRow(btns)
        f.addRow(self.info)
        f.addRow(muted("座標は L_ref(管半径)単位。管の入口 x = 8、出口 x = 18。値はセル値を点へ補間したもので、"
                       "領域の外の点は描かない。中心線の Fig.3 との比較は「中心線(Fig.3)」のタブ(r → 0 に外挿した値)。"))
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(host)
        sa.setMaximumWidth(400)
        sa.setMinimumWidth(340)
        lay.addWidget(sa)
        self.plot = PlotPanel()
        lay.addWidget(self.plot, 1)
        self.preset.currentIndexChanged.connect(self._on_preset)
        self.a_field.edited.connect(self._on_points_edited)
        self.b_field.edited.connect(self._on_points_edited)
        self.calc_btn.clicked.connect(self.compute)
        self.qty_list.itemChanged.connect(lambda _i: self.redraw())
        self.nondim_check.toggled.connect(lambda _b: self.redraw())
        self.xaxis.currentIndexChanged.connect(lambda _i: self.redraw())
        self.show3d_check.toggled.connect(lambda _b: self.line_changed.emit())
        self.fig_btn.clicked.connect(self._save_figure)
        self.csv_btn.clicked.connect(self._save_csv)
        self.redraw()

    # ------------------------------------------------------------------ 状態
    def set_bundle(self, b: ResultBundle | None) -> None:
        self.bundle = b
        self.profile = None
        tube = b is not None and b.run.is_tube_example
        for i in range(self.preset.count()):
            self.preset.model().item(i).setEnabled(tube or self.preset.itemData(i) == "free")
        self.xaxis.model().item(self.xaxis.findData("X")).setEnabled(tube)
        if b is not None and not tube and b.bounds is not None:
            bounds = np.asarray(b.bounds).reshape(3, 2) / b.length
            centre = bounds.mean(axis=1)
            axis = int(np.argmax(bounds[:, 1] - bounds[:, 0]))
            a, end = centre.copy(), centre.copy()
            a[axis], end[axis] = bounds[axis]
            self.set_points(a, end, compute=False)
            if self.xaxis.currentData() == "X":
                self.xaxis.setCurrentIndex(0)
        # 前の選択を引き継ぐ(cis-run の u と pbc-run の U は同じものとみなす)
        prev = {_canon(k) for k in self.selected_keys()}
        with QSignalBlocker(self.qty_list):
            self.qty_list.clear()
            if b is not None and b.grid is not None:
                for k in profile_keys(b):
                    it = QListWidgetItem(k)
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    checked = _canon(k) in prev if prev else k in DEFAULT_KEYS
                    it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                    self.qty_list.addItem(it)
        enabled = b is not None and b.grid is not None
        for w in (self.calc_btn, self.fig_btn, self.csv_btn):
            w.setEnabled(enabled)
        if not enabled:
            self.info.set_status(None, "VTU がないので線上の分布は作れない" if b is not None else "")
            self.redraw()
            return
        self.compute()

    def selected_keys(self) -> list[str]:
        out = []
        for i in range(self.qty_list.count()):
            it = self.qty_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.text())
        return out[:MAX_PLOTS]

    def points(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        return self.a_field.value(), self.b_field.value()

    def line_for_3d(self) -> tuple | None:
        """3D 表示に出す線(出さないなら None)。"""
        if not self.show3d_check.isChecked():
            return None
        try:
            a, b = self.points()
        except ValueError:
            return None
        return None if np.allclose(a, b) else (a, b)

    def set_points(self, a, b, preset: str = "free", compute: bool = True) -> None:
        self.a_field.setValue(a)
        self.b_field.setValue(b)
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(max(self.preset.findData(preset), 0))
        self.line_changed.emit()
        if compute:
            self.compute()

    def _on_preset(self, *_a) -> None:
        key = self.preset.currentData()
        _label, a, b = LINE_PRESETS[key]
        if a is None:
            return
        self.set_points(a, b, key)

    def _on_points_edited(self) -> None:
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(self.preset.findData("free"))
        self.line_changed.emit()

    # ------------------------------------------------------------------ 計算と描画
    def compute(self) -> bool:
        b = self.bundle
        if b is None or b.grid is None:
            return False
        try:
            a, c = self.points()
        except ValueError as exc:
            self.info.set_status("error", f"入力の誤り: {exc}")
            return False
        if np.allclose(a, c):
            self.info.set_status("error", "点 A と点 B が同じです")
            return False
        self.info.set_status("info", "計算しています…")
        self.runner.submit("profile", sample_line, b, a, c, self.n_spin.value(), on_done=self._on_done,
                           on_error=lambda msg, _tb: self.info.set_status("error", f"作れませんでした: {msg}"))
        return True

    def _on_done(self, prof: LineProfile) -> None:
        self.profile = prof
        n, nv = prof.s.size, prof.n_valid
        msg = f"{n} 点(領域の中 {nv} 点)、線の長さ {prof.s[-1]:.4g} L_ref、{prof.elapsed:.2f} 秒"
        self.info.set_status("warning" if nv == 0 else "ok", msg + ("。線が領域の中を通っていません" if nv == 0 else ""))
        self.redraw()
        self.line_changed.emit()

    def _x(self, prof: LineProfile) -> tuple[np.ndarray, str]:
        k = self.xaxis.currentData()
        if k == "s":
            return prof.s, "点 A からの距離 s / L_ref"
        if k == "X":
            return prof.points[:, 0] - 8.0, "X = x/L_ref − 8"
        i = "xyz".index(k)
        return prof.points[:, i], f"{k} / L_ref"

    def redraw(self) -> None:
        t = self.ctx.theme.tokens()
        colors = self.ctx.theme.series()
        fig = self.plot.figure
        fig.clear()
        prof = self.profile
        keys = [k for k in self.selected_keys() if prof is None or k in prof.values]
        if prof is None or not keys:
            ax = fig.subplots(1, 1)
            ax.text(0.5, 0.5, "量を選んで「計算」を押してください" if prof is None else "描く量を選んでください",
                    ha="center", va="center", transform=ax.transAxes, color=t["text_muted"])
            self.plot.draw()
            return
        axes = fig.subplots(len(keys), 1, sharex=True, squeeze=False)[:, 0]
        x, xlabel = self._x(prof)
        scales = self.bundle.run.scales if self.bundle else None
        for i, (ax, k) in enumerate(zip(axes, keys)):
            f, label = profile_scale(k, scales, self.nondim_check.isChecked())
            if self.bundle and not self.bundle.run.is_tube_example:
                label = label.replace("p_in", "p_ref").replace("ρ_in", "ρ_ref")
            y = prof.values[k] * f
            ax.plot(x, y, color=colors[i % len(colors)], linewidth=1.6)
            ax.set_ylabel(label)
        axes[-1].set_xlabel(xlabel)
        a, b = prof.a, prof.b
        axes[0].set_title("A = ({:.3g}, {:.3g}, {:.3g}) → B = ({:.3g}, {:.3g}, {:.3g})".format(*a, *b), fontsize=9)
        self.plot.draw()

    def restyle(self, scheme: str) -> None:
        self.plot.restyle(scheme)
        self.redraw()

    # ------------------------------------------------------------------ 書き出し
    def _default_stem(self) -> str:
        name = self.bundle.run.name if self.bundle else "line"
        return f"{name}_line_{self.preset.currentData()}"

    def _save_figure(self) -> None:
        if self.profile is None:
            return
        start = str((self.bundle.run.directory if self.bundle else self.ctx.root) / f"{self._default_stem()}.png")
        p, _ = QFileDialog.getSaveFileName(self, "図を保存", start, "PNG 画像 (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if p:
            self.plot.save(p, dpi=170)
            self.status.emit(f"図を保存しました: {p}")

    def _save_csv(self) -> None:
        if self.profile is None:
            return
        start = str((self.bundle.run.directory if self.bundle else self.ctx.root) / f"{self._default_stem()}.csv")
        p, _ = QFileDialog.getSaveFileName(self, "線上の分布の CSV", start, "CSV (*.csv)")
        if p:
            self.export_csv(p)
            self.status.emit(f"書き出しました: {p}")

    def export_csv(self, path) -> object:
        return write_profile_csv(path, self.profile, self.bundle.run.scales if self.bundle else None,
                                 self.nondim_check.isChecked(),
                                 inlet_reference=self.bundle is not None and self.bundle.run.is_tube_example)


__all__ = ["LineProfileTab", "profile_keys"]
