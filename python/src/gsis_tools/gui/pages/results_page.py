"""結果の画面: 出力フォルダの VTU・summary.json・history.csv を読み、論文と比べる。

- 3D 表示(:mod:`results_3d`): 場と成分、任意の断面(原点と法線、平面ウィジェット)、等値線、境界面、流線
- 線上の分布(:mod:`line_profile`): 任意の 2 点を結ぶ線の上の分布のグラフと CSV
- 中心線: p/p_in と u_x/√(2RT0)(横軸 X = x/L_ref − 8、管は X = 0〜10)を Fig.3 のデジタイズ値と重ね、差を表にする
- 質量流量: summary.json の Ṁ を Table 1 と並べる(「比べる計算」に加えた計算も並べる)
- 図(PNG・SVG・PDF)と CSV を書き出せる

VTK の重い処理(断面・流線・線上の分布・中心線の作り直し)は :class:`~gsis_tools.gui.workers.JobRunner` で
1 つずつ別スレッドに出す(同じ格子を 2 つのスレッドで同時に触らない)。
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..centerline import TUBE_LENGTH, TUBE_X_OFFSET, Centerline
from ..context import AppContext
from ..fields import ResultBundle, compute_centerline, load_bundle
from ..paths import display_path, runs_dir
from ..reference import (
    FIG3_DELTAS,
    FIG3_X_MIN,
    compare_series,
    load_fig3,
    load_table1,
    paper_delta,
    relative_error,
)
from ..results import discover_runs, write_centerline_csv
from ..widgets.common import FloatField, StatusLabel, heading, muted, primary_button, readonly_item
from ..widgets.history_plot import HistoryPlot
from ..widgets.plots import PlotPanel
from ..workers import JobRunner, start_task
from .line_profile import LineProfileTab
from .results_3d import View3DTab

FIG_FILTER = "PNG 画像 (*.png);;SVG (*.svg);;PDF (*.pdf)"


@dataclass
class PinnedRun:
    """比べる計算(中心線と Ṁ だけを持つ)。"""

    label: str
    directory: Path
    delta: float | None
    kind_label: str
    mass_flow: dict
    iterations: int | None
    converged: bool | None
    centerline: Centerline | None


def pinned_from_bundle(b: ResultBundle) -> PinnedRun:
    r = b.run
    return PinnedRun(r.name, r.directory, r.delta, r.kind_label, r.mass_flow(), r.iterations, r.converged, b.centerline)


class ResultsPage(QWidget):
    """結果の画面。"""

    status = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, view_backend: str | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self.bundle: ResultBundle | None = None
        self.pinned: list[PinnedRun] = []
        self._busy = False
        self.runner = JobRunner(self)
        try:
            self.table1 = load_table1()
        except OSError:
            self.table1 = []
        self._build(view_backend)
        self.ctx.theme.changed.connect(self._restyle)
        self.refresh_run_list()

    # ------------------------------------------------------------------ 画面
    def _build(self, view_backend: str | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(heading("結果"))
        top.addSpacing(12)
        self.run_combo = QComboBox()
        self.run_combo.setEditable(True)
        self.run_combo.setMinimumWidth(420)
        self.run_combo.lineEdit().setPlaceholderText("出力フォルダ(runs/… の下を探して並べる)")
        self.browse_btn = QPushButton("参照…")
        self.rescan_btn = QPushButton("一覧を更新")
        self.load_btn = primary_button("読み込む")
        top.addWidget(QLabel("出力フォルダ"))
        top.addWidget(self.run_combo, 1)
        for w in (self.browse_btn, self.rescan_btn, self.load_btn):
            top.addWidget(w)
        root.addLayout(top)
        self.info = StatusLabel("出力フォルダを選んで「読み込む」を押してください")
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.info)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.view3d = View3DTab(self.ctx, self.runner, view_backend)
        self.view = self.view3d.view  # 3D 表示の部品(スクリーンショットなどで使う)
        self.profile = LineProfileTab(self.ctx, self.runner)
        self.view3d.line_provider = self.profile.line_for_3d
        self.profile.line_changed.connect(self.view3d.request_update)
        self.view3d.status.connect(self.status)
        self.profile.status.connect(self.status)
        self.tabs.addTab(self.view3d, "3D 表示")
        self.tabs.addTab(self.profile, "線上の分布")
        self.tabs.addTab(self._build_centerline(), "中心線(Fig.3)")
        self.tabs.addTab(self._build_table1(), "質量流量(Table 1)")
        self.history_plot = HistoryPlot()
        self.tabs.addTab(self.history_plot, "収束の履歴")
        self.summary_tree = QTreeWidget()
        self.summary_tree.setHeaderLabels(["キー", "値"])
        self.summary_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tabs.addTab(self.summary_tree, "要約(summary.json)")

        self.browse_btn.clicked.connect(self._browse)
        self.rescan_btn.clicked.connect(self.refresh_run_list)
        self.load_btn.clicked.connect(lambda: self.load(self._combo_path()))
        self.run_combo.lineEdit().returnPressed.connect(lambda: self.load(self._combo_path()))

    def _build_centerline(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        ctl = QGroupBox("比べ方")
        ctl.setMaximumWidth(360)
        f = QFormLayout(ctl)
        self.delta_combo = QComboBox()
        self.range_combo = QComboBox()
        self.range_combo.addItem("管の中(X = 0〜10)", "tube")
        self.range_combo.addItem("全体(槽を含む)", "all")
        self.rmax_field = FloatField(0.3, positive=True)
        self.show_gsis = QCheckBox("論文 GSIS-PBC(線)")
        self.show_cis = QCheckBox("論文 CIS(記号)")
        self.show_dsmc = QCheckBox("論文 DSMC(記号、δ = 100 だけ)")
        self.show_pinned = QCheckBox("「比べる計算」も描く")
        for c in (self.show_gsis, self.show_cis, self.show_dsmc, self.show_pinned):
            c.setChecked(True)
        f.addRow("比べる δ_rp", self.delta_combo)
        f.addRow("横軸の範囲", self.range_combo)
        f.addRow("当てはめの半径 r_max [L_ref]", self.rmax_field)
        f.addRow(self.show_gsis)
        f.addRow(self.show_cis)
        f.addRow(self.show_dsmc)
        f.addRow(self.show_pinned)
        f.addRow(muted("中心線の値は、軸の近く(r < r_max)のセル値に a + b r² を当てた r = 0 の値。"
                       "差は論文の点の X で計算の値を線形補間して求める(X < 0.15 は使わない)。"))
        self.cl_source = muted("")
        f.addRow(self.cl_source)
        self.save_cl_fig = QPushButton("図を保存…")
        self.save_cl_csv = QPushButton("CSV を書き出す…")
        f.addRow(self.save_cl_fig)
        f.addRow(self.save_cl_csv)
        lay.addWidget(ctl)
        right = QSplitter(Qt.Orientation.Vertical)
        self.cl_plot = PlotPanel()
        right.addWidget(self.cl_plot)
        self.cl_table = QTableWidget(0, 7)
        self.cl_table.setHorizontalHeaderLabels(["量", "比べる相手", "点数", "差の RMS", "最大の差", "最大の位置 X",
                                                 "平均の差(計算 − 論文)"])
        self.cl_table.verticalHeader().setVisible(False)
        self.cl_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.cl_table.horizontalHeader().setStretchLastSection(True)
        self.cl_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        right.addWidget(self.cl_table)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([620, 190])
        lay.addWidget(right, 1)

        self.delta_combo.currentIndexChanged.connect(lambda _i: self.update_centerline())
        self.range_combo.currentIndexChanged.connect(lambda _i: self.update_centerline())
        for c in (self.show_gsis, self.show_cis, self.show_dsmc, self.show_pinned):
            c.toggled.connect(lambda _b: self.update_centerline())
        self.rmax_field.editingFinished.connect(self._recompute_centerline)
        self.save_cl_fig.clicked.connect(lambda: self._save_figure(self.cl_plot, "centerline"))
        self.save_cl_csv.clicked.connect(self._save_centerline_csv)
        return w

    def _build_table1(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        bar = QHBoxLayout()
        self.pin_btn = QPushButton("今の計算を「比べる計算」に加える")
        self.unpin_btn = QPushButton("選んだ行の計算を外す")
        self.save_t1_btn = QPushButton("CSV を書き出す…")
        for b in (self.pin_btn, self.unpin_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.save_t1_btn)
        lay.addLayout(bar)
        self.t1_table = QTableWidget(0, 11)
        self.t1_table.setHorizontalHeaderLabels([
            "δ_rp", "参照(論文)", "Ṁ 参照", "Ṁ 論文 GSIS-PBC", "Ṁ 論文 CIS", "計算", "種類", "Ṁ 計算",
            "差 対 GSIS-PBC", "差 対 参照", "反復(計算 / 論文 GSIS / 論文 CIS)",
        ])
        self.t1_table.verticalHeader().setVisible(False)
        self.t1_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.t1_table.horizontalHeader().setStretchLastSection(True)
        self.t1_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.t1_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.t1_table, 1)
        lay.addWidget(muted(
            "Ṁ = ṁ / (√π L_ref² p_in / √(2RT0))(論文の式)。計算の値は summary.json の M_dot(入口と出口の平均)。"
            "受け入れ基準は論文の GSIS-PBC 値との差 ±2%(docs/spec.md §1.4)。参照は δ = 1〜100 が DSMC、1000 が NSF(FLUENT)。"
            "六面体メッシュは円を 64 角形で近似するので断面積が約 0.16% 小さい(spec §5.3)。"))
        self.pin_btn.clicked.connect(self.pin_current)
        self.unpin_btn.clicked.connect(self._unpin_selected)
        self.save_t1_btn.clicked.connect(self._save_table1_csv)
        return w

    # ------------------------------------------------------------------ 出力フォルダの一覧
    def refresh_run_list(self) -> None:
        current = self.run_combo.currentText()
        runs = discover_runs(runs_dir(self.ctx.root), max_depth=4)
        extra = [Path(p) for p in self.ctx.recent("run") if Path(p).is_dir()]
        seen = set()
        with QSignalBlocker(self.run_combo):
            self.run_combo.clear()
            for p in extra + runs:
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                self.run_combo.addItem(display_path(p, self.ctx.root), key)
            self.run_combo.setCurrentText(current)

    def _combo_path(self) -> Path | None:
        text = self.run_combo.currentText().strip()
        if not text:
            return None
        idx = self.run_combo.findText(text)
        if idx >= 0 and self.run_combo.itemData(idx):
            return Path(self.run_combo.itemData(idx))
        p = Path(text)
        return p if p.is_absolute() else self.ctx.root / p

    def _browse(self) -> None:
        start = str(self._combo_path() or runs_dir(self.ctx.root))
        p = QFileDialog.getExistingDirectory(self, "出力フォルダを選ぶ", start)
        if p:
            self.run_combo.setCurrentText(p)
            self.load(Path(p))

    # ------------------------------------------------------------------ 読み込み
    def load(self, directory: str | Path | None) -> bool:
        if directory is None or self._busy:
            return False
        d = Path(directory)
        if not d.is_dir():
            QMessageBox.warning(self, "出力フォルダ", f"フォルダがありません:\n{d}")
            return False
        self._busy = True
        self.load_btn.setEnabled(False)
        self.info.set_status("info", f"読み込んでいます: {display_path(d, self.ctx.root)}(VTU が大きいと数秒かかる)")
        self.run_combo.setCurrentText(display_path(d, self.ctx.root))
        rmax = self.rmax_field.value() or 0.3
        start_task(self, load_bundle, d, self.ctx.root, r_max=rmax, x_offset=TUBE_X_OFFSET,
                   on_done=self._on_loaded, on_error=self._on_failed)
        return True

    def _on_failed(self, msg: str, tb: str) -> None:
        self._busy = False
        self.load_btn.setEnabled(True)
        self.info.set_status("error", f"読み込めませんでした: {msg}")

    def _on_loaded(self, b: ResultBundle) -> None:
        self._busy = False
        self.load_btn.setEnabled(True)
        self.bundle = b
        r = b.run
        self.ctx.add_recent("run", r.directory)
        parts = [f"{r.name}({r.kind_label})"]
        if r.delta is not None:
            parts.append(f"δ = {r.delta:.6g}")
        if r.iterations is not None:
            parts.append(f"{r.iterations:,} 反復で" + ("収束" if r.converged else "止まった(収束していない)"))
        mf = r.mass_flow()
        if "M_dot" in mf:
            parts.append(f"Ṁ = {mf['M_dot']:.5f}(入口 {mf.get('M_dot_inlet', math.nan):.5f}、出口 {mf.get('M_dot_outlet', math.nan):.5f})")
        if r.wall_time_s is not None:
            parts.append(f"計算 {r.wall_time_s:.1f} 秒")
        parts.append(f"読み込み {b.load_time:.1f} 秒")
        notes = r.notes + b.errors
        self.info.set_status("warning" if b.errors else "ok", "、".join(parts) + ("。注意: " + " / ".join(notes) if notes else ""))
        self.runner.cancel_pending()  # 前の結果への計算の待ちは捨てる
        self.view3d.set_bundle(b)
        self.profile.set_bundle(b)
        self._fill_delta_combo()
        self.update_centerline()
        self.update_table1()
        self._fill_history()
        self._fill_summary()
        self.status.emit(f"結果を読み込みました: {display_path(r.directory, self.ctx.root)}")

    # ------------------------------------------------------------------ 中心線
    def _fill_delta_combo(self) -> None:
        with QSignalBlocker(self.delta_combo):
            self.delta_combo.clear()
            auto = paper_delta(self.bundle.run.delta, FIG3_DELTAS) if self.bundle else None
            self.delta_combo.addItem(f"自動(δ = {auto:g})" if auto else "自動(合う図がない)", auto)
            for d in FIG3_DELTAS:
                self.delta_combo.addItem(f"δ_rp = {d:g}", d)
            self.delta_combo.addItem("比べない", -1.0)

    def _compare_delta(self) -> float | None:
        d = self.delta_combo.currentData()
        return None if d is None or d < 0 else float(d)

    def _recompute_centerline(self) -> None:
        """当てはめの半径を変えたときに中心線を作り直す(別スレッド)。"""
        b = self.bundle
        if b is None:
            return
        self.cl_source.setText("中心線を作り直しています…")

        def done(_res, bundle=b) -> None:
            if bundle is self.bundle:
                self.update_centerline()

        self.runner.submit("centerline", compute_centerline, b, r_max=self.rmax_field.value() or 0.3,
                           x_offset=TUBE_X_OFFSET, on_done=done,
                           on_error=lambda msg, _tb: self.cl_source.setText(f"中心線を作れません: {msg}"))

    def update_centerline(self) -> None:
        t = self.ctx.theme.tokens()
        colors = self.ctx.theme.series()
        fig = self.cl_plot.figure
        fig.clear()
        ax_p, ax_u = fig.subplots(1, 2)
        cl = self.bundle.centerline if self.bundle else None
        self.cl_source.setText(f"中心線の元: {self.bundle.centerline_source}" if self.bundle else "")
        rng = self.range_combo.currentData()
        d = self._compare_delta()
        rows: list[tuple] = []

        def draw_model(c: Centerline, label: str, color: str, lw: float, ls: str = "-") -> None:
            cc = c.clipped(-0.05, TUBE_LENGTH + 0.05) if rng == "tube" else c
            ax_p.plot(cc.X, cc.values["p"], color=color, linewidth=lw, linestyle=ls, label=label)
            ax_u.plot(cc.X, cc.values["ux"], color=color, linewidth=lw, linestyle=ls, label=label)

        if cl is not None and len(cl):
            draw_model(cl, f"この計算({self.bundle.run.name})", colors[0], 2.0)
        if self.show_pinned.isChecked():
            for i, pr in enumerate(self.pinned):
                if pr.centerline is not None and (self.bundle is None or pr.directory != self.bundle.run.directory):
                    draw_model(pr.centerline, pr.label, colors[(i + 3) % len(colors)], 1.3, "--")
        if d is not None:
            styles = (("gsis", self.show_gsis, "線"), ("cis", self.show_cis, "記号"), ("dsmc", self.show_dsmc, "記号"))
            for quantity, ax, col in (("pressure", ax_p, "p"), ("velocity", ax_u, "ux")):
                for method, check, _kind in styles:
                    if not check.isChecked():
                        continue
                    ref = load_fig3(quantity, method, d)
                    if ref is None:
                        continue
                    X, V = ref
                    name = {"gsis": "論文 GSIS-PBC", "cis": "論文 CIS", "dsmc": "論文 DSMC"}[method]
                    if method == "gsis":
                        ax.plot(X, V, linestyle="none", marker=".", markersize=2.2, color=t["text_muted"],
                                label=f"{name}(δ={d:g})")
                    elif method == "cis":
                        ax.plot(X, V, linestyle="none", marker="s", markersize=5, markerfacecolor="none",
                                markeredgecolor=colors[1], label=f"{name}(δ={d:g})")
                    else:
                        ax.plot(X, V, linestyle="none", marker="o", markersize=5, markerfacecolor="none",
                                markeredgecolor=colors[2], label=f"{name}(δ={d:g})")
                    if cl is not None and len(cl):
                        diff = compare_series(cl.X, cl.values[col], X, V)
                        if diff is not None:
                            rows.append((("p / p_in" if col == "p" else "u_x / √(2RT0)"), f"{name}(δ={d:g})", diff))
        for ax, ylab in ((ax_p, r"$p\,/\,p_{\rm in}$"), (ax_u, r"$u_x\,/\,\sqrt{2RT_0}$")):
            ax.set_xlabel(r"$X = x/L_{\rm ref}$(管の入口 0、出口 10)")
            ax.set_ylabel(ylab)
            if rng == "tube":
                # 論文の線が欠けていて比べない範囲(X < 0.15)
                ax.axvspan(-100, FIG3_X_MIN, color=t["surface_2"], alpha=0.6, linewidth=0)
            if rng == "all":
                for xv in (0.0, TUBE_LENGTH):
                    ax.axvline(xv, color=t["border"], linewidth=1.0)
        if rng == "tube":
            ax_p.set_xlim(-0.3, TUBE_LENGTH + 0.3)
            ax_u.set_xlim(-0.3, TUBE_LENGTH + 0.3)
        ax_p.set_title("中心線の圧力", fontsize=10)
        ax_u.set_title("中心線の軸方向速度", fontsize=10)
        handles, labels = ax_p.get_legend_handles_labels()
        if handles:
            ax_p.legend(loc="lower left", fontsize=8)
        if cl is None or not len(cl):
            for ax in (ax_p, ax_u):
                ax.text(0.5, 0.5, "中心線がありません", transform=ax.transAxes, ha="center", va="center",
                        color=t["text_muted"])
        self.cl_plot.draw()
        self.cl_table.setRowCount(len(rows))
        for r, (q, who, diff) in enumerate(rows):
            vals = [q, who, f"{diff.n}", f"{diff.rms:.4f}", f"{diff.max_abs:.4f}", f"{diff.x_at_max:.2f}", f"{diff.mean:+.4f}"]
            for c, v in enumerate(vals):
                self.cl_table.setItem(r, c, readonly_item(v, align_right=c >= 2))
        self._cl_rows = rows

    def _save_centerline_csv(self) -> None:
        if self.bundle is None or self.bundle.centerline is None:
            return
        name = self.bundle.run.name
        p, _ = QFileDialog.getSaveFileName(self, "中心線の CSV", str(self.bundle.run.directory / f"{name}_centerline_gui.csv"),
                                           "CSV (*.csv)")
        if not p:
            return
        self.export_centerline(Path(p))
        self.status.emit(f"中心線を書き出しました: {p}")

    def export_centerline(self, path: Path) -> list[Path]:
        """中心線の CSV(論文 GSIS-PBC の線をこの X で補間した列付き)と差の CSV を書く。"""
        cl = self.bundle.centerline
        extra = {}
        d = self._compare_delta()
        if d is not None:
            for quantity, col in (("pressure", "p_paper_gsis"), ("velocity", "ux_paper_gsis")):
                ref = load_fig3(quantity, "gsis", d)
                if ref is not None:
                    X, V = ref
                    v = np.interp(cl.X, X, V, left=np.nan, right=np.nan)
                    v[cl.X < FIG3_X_MIN] = np.nan
                    extra[col] = v
        out = [write_centerline_csv(path, cl, extra)]
        rows = getattr(self, "_cl_rows", [])
        if rows:
            dp = path.with_name(path.stem + "_diff.csv")
            with open(dp, "w", encoding="utf-8", newline="") as fh:
                wr = csv.writer(fh, lineterminator="\n")
                wr.writerow(["quantity", "reference", "n", "rms", "max_abs", "x_at_max", "mean"])
                for q, who, diff in rows:
                    wr.writerow([q, who, diff.n, f"{diff.rms:.6g}", f"{diff.max_abs:.6g}", f"{diff.x_at_max:.4g}",
                                 f"{diff.mean:.6g}"])
            out.append(dp)
        return out

    # ------------------------------------------------------------------ Table 1
    def pin_current(self) -> None:
        if self.bundle is None:
            return
        pr = pinned_from_bundle(self.bundle)
        self.pinned = [p for p in self.pinned if p.directory != pr.directory] + [pr]
        self.update_table1()
        self.update_centerline()

    def _unpin_selected(self) -> None:
        rows = {i.row() for i in self.t1_table.selectedIndexes()}
        dirs = set()
        for r in rows:
            it = self.t1_table.item(r, 5)
            if it is not None and it.data(Qt.ItemDataRole.UserRole):
                dirs.add(it.data(Qt.ItemDataRole.UserRole))
        self.pinned = [p for p in self.pinned if str(p.directory) not in dirs]
        self.update_table1()
        self.update_centerline()

    def _table1_runs(self) -> list[PinnedRun]:
        runs = list(self.pinned)
        if self.bundle is not None and all(p.directory != self.bundle.run.directory for p in runs):
            runs.append(pinned_from_bundle(self.bundle))
        return runs

    def table1_rows(self) -> list[dict]:
        """表の行(論文の 4 条件 × 計算。δ が論文の条件に合わない計算は最後に並べる)。"""
        runs = self._table1_runs()
        out = []
        by_delta: dict[float | None, list[PinnedRun]] = {}
        for pr in runs:
            by_delta.setdefault(paper_delta(pr.delta), []).append(pr)
        for row in self.table1:
            mine = by_delta.get(row.delta_rp, []) or [None]
            for pr in mine:
                out.append({"paper": row, "run": pr})
        for pr in by_delta.get(None, []):
            out.append({"paper": None, "run": pr})
        return out

    def update_table1(self) -> None:
        rows = self.table1_rows()
        t = self.ctx.theme.tokens()
        self.t1_table.setRowCount(len(rows))
        cur = self.bundle.run.directory if self.bundle else None
        for r, item in enumerate(rows):
            pp, pr = item["paper"], item["run"]
            m = pr.mass_flow.get("M_dot", math.nan) if pr else math.nan
            e_g = relative_error(m, pp.gsis_pbc_M) if pp else math.nan
            e_r = relative_error(m, pp.M_ref) if pp else math.nan
            its = "—"
            if pr is not None or pp is not None:
                a = f"{pr.iterations:,}" if pr and pr.iterations is not None else "—"
                b = f"{pp.gsis_pbc_iter:g}" if pp else "—"
                c = f"{pp.cis_iter:g}" if pp else "—"
                its = f"{a} / {b} / {c}"
            cells = [
                f"{pp.delta_rp:g}" if pp else (f"{pr.delta:.4g}" if pr and pr.delta else "—"),
                pp.ref_method if pp else "—",
                f"{pp.M_ref:.3f}" if pp else "—",
                f"{pp.gsis_pbc_M:.3f}" if pp else "—",
                f"{pp.cis_M:.3f}" if pp else "—",
                pr.label if pr else "",
                pr.kind_label if pr else "",
                f"{m:.5f}" if math.isfinite(m) else "—",
                f"{100 * e_g:+.2f}%" if math.isfinite(e_g) else "—",
                f"{100 * e_r:+.2f}%" if math.isfinite(e_r) else "—",
                its,
            ]
            for c, v in enumerate(cells):
                it = readonly_item(v, align_right=c in (0, 2, 3, 4, 7, 8, 9))
                if c == 5 and pr is not None:
                    it.setData(Qt.ItemDataRole.UserRole, str(pr.directory))
                    if pr.directory == cur:
                        f = it.font()
                        f.setBold(True)
                        it.setFont(f)
                if c == 8 and math.isfinite(e_g):
                    it.setForeground(QColor(t["success"] if abs(e_g) <= 0.02 else t["warning"]))
                self.t1_table.setItem(r, c, it)

    def _save_table1_csv(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Table 1 との比較", str(self.ctx.root / "table1_comparison.csv"),
                                           "CSV (*.csv)")
        if p:
            self.export_table1(Path(p))
            self.status.emit(f"書き出しました: {p}")

    def export_table1(self, path: Path) -> Path:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            wr = csv.writer(fh, lineterminator="\n")
            wr.writerow(["delta_rp", "ref_method", "M_ref", "paper_gsis_pbc_M", "paper_cis_M", "run", "run_kind",
                         "run_dir", "M_dot", "M_dot_inlet", "M_dot_outlet", "rel_diff_vs_gsis_pbc", "rel_diff_vs_ref",
                         "iterations", "converged"])
            for item in self.table1_rows():
                pp, pr = item["paper"], item["run"]
                mf = pr.mass_flow if pr else {}
                m = mf.get("M_dot", math.nan)
                wr.writerow([
                    pp.delta_rp if pp else (pr.delta if pr else ""), pp.ref_method if pp else "",
                    pp.M_ref if pp else "", pp.gsis_pbc_M if pp else "", pp.cis_M if pp else "",
                    pr.label if pr else "", pr.kind_label if pr else "", str(pr.directory) if pr else "",
                    f"{m:.8g}" if math.isfinite(m) else "", mf.get("M_dot_inlet", ""), mf.get("M_dot_outlet", ""),
                    f"{relative_error(m, pp.gsis_pbc_M):.6g}" if pp and math.isfinite(m) else "",
                    f"{relative_error(m, pp.M_ref):.6g}" if pp and math.isfinite(m) else "",
                    pr.iterations if pr and pr.iterations is not None else "",
                    pr.converged if pr and pr.converged is not None else "",
                ])
        return path

    # ------------------------------------------------------------------ 履歴・要約
    def _fill_history(self) -> None:
        b = self.bundle
        r = b.run
        nd_to_Mdot, scale = r.mdot_conversion()
        refs = []
        row = next((x for x in self.table1 if paper_delta(r.delta) == x.delta_rp), None)
        if row is not None:
            refs.append((f"論文 GSIS-PBC {row.gsis_pbc_M:g}", row.gsis_pbc_M))
            if r.kind == "kinetic":
                refs.append((f"論文 CIS {row.cis_M:g}", row.cis_M))
        tol = r.summary.get("tolerance") if isinstance(r.summary.get("tolerance"), (int, float)) else None
        self.history_plot.set_context(nd_to_Mdot=nd_to_Mdot, mass_flow_scale=scale, patch_types=r.patch_types(),
                                      tolerance=tol, reference_mdot=refs)
        self.history_plot.set_history(b.history)

    def _fill_summary(self) -> None:
        self.summary_tree.clear()
        s = self.bundle.run.summary

        def add(parent, key, value):
            if isinstance(value, dict):
                it = QTreeWidgetItem([str(key), ""])
                for k, v in value.items():
                    add(it, k, v)
            elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                it = QTreeWidgetItem([str(key), f"{len(value)} 件"])
                for i, v in enumerate(value):
                    add(it, v.get("name", i) if isinstance(v, dict) else i, v)
            else:
                it = QTreeWidgetItem([str(key), json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value])
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(it)
            else:
                parent.addChild(it)

        for k, v in s.items():
            add(self.summary_tree, k, v)
        self.summary_tree.expandToDepth(0)

    # ------------------------------------------------------------------ 共通
    def _save_figure(self, panel: PlotPanel, stem: str) -> None:
        name = self.bundle.run.name if self.bundle else "figure"
        start = str((self.bundle.run.directory if self.bundle else self.ctx.root) / f"{name}_{stem}.png")
        p, _ = QFileDialog.getSaveFileName(self, "図を保存", start, FIG_FILTER)
        if p:
            panel.save(p, dpi=170)
            self.status.emit(f"図を保存しました: {p}")

    def _restyle(self, scheme: str) -> None:
        self.cl_plot.restyle(scheme)
        self.history_plot.restyle(scheme)
        self.view3d.restyle(scheme)
        self.profile.restyle(scheme)
        self.update_centerline()
        if self.bundle is not None:
            self.update_table1()

    def shutdown(self) -> None:
        self.runner.cancel_pending()
        self.view3d.shutdown()
