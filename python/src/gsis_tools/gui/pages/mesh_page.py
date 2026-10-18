"""メッシュの画面: 管メッシュ(六面体・四面体)を gsis_tools で作り、品質と境界パッチを確かめる。

- 生成は別スレッド(:mod:`gsis_tools.gui.workers`)。四面体は gmsh をメインスレッドで先に初期化しておく
- 3D 表示: 境界パッチを色分けした表面。「断面」を選ぶと z ≤ 0 の側のセルを見せ、断面のセルの形が分かる
- 保存: ``.pmesh`` と品質レポート(Markdown)。必要なら ParaView 用の VTU も書く
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..context import AppContext
from ..meshgen import MeshPreview, TubeMeshSpec, cli_command, ensure_gmsh_main_thread, generate_preview, load_preview
from ..paths import display_path, tube_mesh_dir
from ..widgets.common import FloatField, LogView, StatusLabel, heading, muted, primary_button, readonly_item
from ..widgets.vtk_view import Scene, SceneItem, VtkView
from ..workers import QtLogHandler, start_task


def _save_mesh_files(preview: MeshPreview, path: Path, write_vtu: bool) -> list[Path]:
    """.pmesh・品質レポート・(任意で)VTU を書く(別スレッドで呼ぶ)。"""
    from ...geometry.tube import TubeDims
    from ...pmesh import write_pmesh
    from ...quality import compute_quality, format_report
    from ...vtu import write_boundary_vtu, write_vtu as _write_vtu

    mesh = preview.mesh
    out = [write_pmesh(path, mesh) or path]
    R = preview.report.radius
    q = compute_quality(mesh)
    dims = TubeDims(radius=R)
    tube_like = all(n in mesh.patch_names() for n in ("inlet", "outlet", "wall"))
    notes = []
    spec = preview.info.get("spec")
    if spec:
        notes.append("生成: gsis-gui(同じ設定のコマンド: `" + cli_command(TubeMeshSpec(**spec), path.name) + "`)")
    text = format_report(
        mesh, q, title=f"メッシュの品質: {path.name}", length_scale=R, length_label="R",
        regions=dims.regions(mesh.geometry().cell_centres) if tube_like else None,
        exact_volume=dims.volume() if tube_like else None, exact_areas=dims.patch_areas() if tube_like else None,
        notes=notes,
    )
    rep = path.with_name(path.stem + "_quality.md")
    rep.write_text(text, encoding="utf-8", newline="\n")
    out.append(rep)
    if write_vtu:
        out.append(_write_vtu(path.with_suffix(".vtu"), mesh, q.cell_data()))
        out.append(write_boundary_vtu(path.with_name(path.stem + "_boundary.vtu"), mesh))
    return out


class MeshPage(QWidget):
    """メッシュの画面。"""

    mesh_selected = Signal(str)  # 「ケースに使う」
    status = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, view_backend: str | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self.preview: MeshPreview | None = None
        self._busy = False
        self._log_handler = QtLogHandler(logging.INFO)
        self._log_handler.message.connect(lambda s: self.log.append_lines(s, stream="gui"))
        self._build(view_backend)
        self.ctx.theme.changed.connect(self._restyle)
        self._update_kind()
        self._update_cli()

    # ------------------------------------------------------------------ 画面
    def _build(self, view_backend: str | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(heading("メッシュ"))
        top.addSpacing(12)
        self.gen_btn = primary_button("生成")
        self.open_btn = QPushButton(".pmesh を開く…")
        self.save_btn = QPushButton("保存…")
        self.use_btn = QPushButton("ケースに使う")
        self.use_btn.setToolTip("保存したメッシュを、ケース設定の画面のメッシュにする")
        for w in (self.gen_btn, self.open_btn, self.save_btn, self.use_btn):
            top.addWidget(w)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(160)
        top.addWidget(self.progress)
        top.addSpacing(16)
        self.state_label = StatusLabel("")
        self.state_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.state_label, 1)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # 左: 設定
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lv = QVBoxLayout(host)
        lv.setContentsMargins(0, 0, 8, 0)
        scroll.setWidget(host)
        scroll.setMinimumWidth(340)
        split.addWidget(scroll)

        g = QGroupBox("形状と方式(論文 §4.1 の管。長さは管半径 R 単位)")
        f = QFormLayout(g)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("六面体(構造格子、O 型断面)", "hex")
        self.kind_combo.addItem("四面体(gmsh、非構造)", "tet")
        self.radius_field = FloatField(1e-3, positive=True)
        self.target_check = QCheckBox("目標セル数に合わせる")
        self.target_check.setChecked(True)
        self.target_spin = QSpinBox()
        self.target_spin.setRange(1000, 50_000_000)
        self.target_spin.setSingleStep(5000)
        self.target_spin.setValue(135_000)
        self.target_spin.setGroupSeparatorShown(True)
        f.addRow("方式", self.kind_combo)
        f.addRow("管半径 R [m]", self.radius_field)
        f.addRow(self.target_check, self.target_spin)
        f.addRow("", muted("上流槽 半径 8・長さ 8、管 半径 1・長さ 10、下流槽 半径 4・長さ 4。"
                           "パッチは inlet(x = 0)・outlet(x = 22)・wall"))
        lv.addWidget(g)

        d = TubeMeshSpec()
        self.hex_box = QGroupBox("六面体の設定")
        f = QFormLayout(self.hex_box)
        self.n_theta = QSpinBox()
        self.n_theta.setRange(2, 512)
        self.n_theta.setValue(d.n_theta)
        self.square = FloatField(d.square, positive=True)
        self.ds_wall = FloatField(d.ds_wall, positive=True)
        self.ds_mouth = FloatField(d.ds_mouth, positive=True)
        self.h_tube = FloatField(d.h_tube, positive=True)
        self.h_max = FloatField(d.h_max, positive=True)
        self.growth = FloatField(d.growth, positive=True)
        self.refine = FloatField(None, allow_empty=True, positive=True, placeholder="なし")
        f.addRow("周方向の分割(1/4 周)n_theta", self.n_theta)
        f.addRow("中心の正方形の半幅 square", self.square)
        f.addRow("壁の最初のセル幅 ds_wall", self.ds_wall)
        f.addRow("出入口の最初のセル幅 ds_mouth", self.ds_mouth)
        f.addRow("管の中の軸方向の間隔 h_tube", self.h_tube)
        f.addRow("槽の中の間隔の上限 max_size", self.h_max)
        f.addRow("間隔の比 growth", self.growth)
        f.addRow("一様に細かくする倍率 refine", self.refine)
        f.addRow("", muted("目標セル数に合わせるときは ds_wall・ds_mouth・h_tube を同じ倍率で変える"))
        lv.addWidget(self.hex_box)

        self.tet_box = QGroupBox("四面体の設定")
        f = QFormLayout(self.tet_box)
        self.tet_h_max = FloatField(d.tet_h_max, positive=True)
        self.tet_h_tube = FloatField(d.tet_h_tube, positive=True)
        self.gradient = FloatField(d.gradient, positive=True)
        self.algorithm = QComboBox()
        self.algorithm.addItems(["delaunay", "hxt"])
        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(d.threads)
        f.addRow("槽の中のセル寸法の上限 max_size", self.tet_h_max)
        f.addRow("管の中のセル寸法 h_tube", self.tet_h_tube)
        f.addRow("寸法の増加率 gradient", self.gradient)
        f.addRow("gmsh の 3 次元アルゴリズム", self.algorithm)
        f.addRow("スレッド数", self.threads)
        f.addRow("", muted("13 万セルで 15〜30 秒。目標セル数に合わせるときは h_tube を調整して最大 3 回作る"))
        lv.addWidget(self.tet_box)

        g = QGroupBox("保存と再現")
        f = QFormLayout(g)
        self.vtu_check = QCheckBox("ParaView 用の VTU(セル・境界面)も書く")
        self.cli_edit = QLineEdit()
        self.cli_edit.setReadOnly(True)
        self.cli_edit.setToolTip("同じメッシュを CLI で作るコマンド")
        f.addRow(self.vtu_check)
        f.addRow("同じ設定の CLI", self.cli_edit)
        lv.addWidget(g)
        lv.addStretch(1)

        # 右: 3D と品質
        right = QSplitter(Qt.Orientation.Vertical)
        view_host = QWidget()
        vv = QVBoxLayout(view_host)
        vv.setContentsMargins(8, 0, 0, 0)
        vbar = QHBoxLayout()
        vbar.addWidget(QLabel("表示"))
        self.show_mode = QButtonGroup(self)
        self.rb_boundary = QRadioButton("境界パッチ(全体)")
        self.rb_section = QRadioButton("断面(z ≤ 0 の側のセル)")
        self.rb_section.setChecked(True)
        for i, rb in enumerate((self.rb_section, self.rb_boundary)):
            self.show_mode.addButton(rb, i)
            vbar.addWidget(rb)
        self.edges_check = QCheckBox("辺")
        self.edges_check.setChecked(True)
        vbar.addWidget(self.edges_check)
        vbar.addStretch(1)
        vbar.addWidget(QLabel("視点"))
        self.view_buttons = {}
        for key, label in (("iso", "斜め"), ("xy", "上から"), ("focus", "管の付近")):
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, k=key: self.view.set_view(k))
            self.view_buttons[key] = b
            vbar.addWidget(b)
        vv.addLayout(vbar)
        self.view = VtkView(backend=view_backend)
        vv.addWidget(self.view, 1)
        right.addWidget(view_host)

        tabs = QTabWidget()
        self.quality_table = QTableWidget(0, 2)
        self.quality_table.setHorizontalHeaderLabels(["項目", "値"])
        self.patch_table = QTableWidget(0, 5)
        self.patch_table.setHorizontalHeaderLabels(["パッチ", "kind", "面の数", "面積 [R²]", "円の面積との差"])
        for t in (self.quality_table, self.patch_table):
            t.verticalHeader().setVisible(False)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            t.horizontalHeader().setStretchLastSection(True)
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.log = LogView(max_lines=5000)
        tabs.addTab(self.quality_table, "品質")
        tabs.addTab(self.patch_table, "パッチ")
        tabs.addTab(self.log, "ログ")
        self.tabs = tabs
        right.addWidget(tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 1100])

        self.kind_combo.currentIndexChanged.connect(self._update_kind)
        self.target_check.toggled.connect(self.target_spin.setEnabled)
        for w in (self.radius_field, self.square, self.ds_wall, self.ds_mouth, self.h_tube, self.h_max, self.growth,
                  self.refine, self.tet_h_max, self.tet_h_tube, self.gradient):
            w.valueChanged.connect(self._update_cli)
        for w in (self.n_theta, self.threads, self.target_spin):
            w.valueChanged.connect(self._update_cli)
        for w in (self.kind_combo, self.algorithm):
            w.currentIndexChanged.connect(self._update_cli)
        self.target_check.toggled.connect(self._update_cli)
        self.show_mode.idToggled.connect(lambda _i, on: on and self._show_scene())
        self.edges_check.toggled.connect(lambda _on: self._show_scene())
        self.gen_btn.clicked.connect(self.generate)
        self.open_btn.clicked.connect(self._open_dialog)
        self.save_btn.clicked.connect(self._save_dialog)
        self.use_btn.clicked.connect(self._use_in_case)
        self._update_buttons()

    # ------------------------------------------------------------------ 設定
    def _update_kind(self) -> None:
        hexa = self.kind_combo.currentData() == "hex"
        self.hex_box.setVisible(hexa)
        self.tet_box.setVisible(not hexa)
        self._update_cli()

    def spec(self) -> TubeMeshSpec:
        def v(f: FloatField, name: str) -> float:
            if not f.is_valid() or f.value() is None:
                raise ValueError(f"{name} の値が正しくありません: {f.text()!r}")
            return float(f.value())

        s = TubeMeshSpec(kind=self.kind_combo.currentData())
        s.target_cells = self.target_spin.value() if self.target_check.isChecked() else None
        s.radius = v(self.radius_field, "管半径 R")
        s.n_theta = self.n_theta.value()
        s.square = v(self.square, "square")
        s.ds_wall = v(self.ds_wall, "ds_wall")
        s.ds_mouth = v(self.ds_mouth, "ds_mouth")
        s.h_tube = v(self.h_tube, "h_tube")
        s.h_max = v(self.h_max, "max_size")
        s.growth = v(self.growth, "growth")
        s.refine = self.refine.value() if self.refine.is_valid() else None
        s.tet_h_max = v(self.tet_h_max, "max_size")
        s.tet_h_tube = v(self.tet_h_tube, "h_tube")
        s.gradient = v(self.gradient, "gradient")
        s.algorithm = self.algorithm.currentText()
        s.threads = self.threads.value()
        return s

    def set_spec(self, s: TubeMeshSpec) -> None:
        widgets = (self.kind_combo, self.n_theta, self.threads, self.target_spin, self.target_check, self.algorithm)
        blockers = [QSignalBlocker(w) for w in widgets]
        self.kind_combo.setCurrentIndex(max(self.kind_combo.findData(s.kind), 0))
        self.target_check.setChecked(s.target_cells is not None)
        self.target_spin.setEnabled(s.target_cells is not None)
        if s.target_cells:
            self.target_spin.setValue(int(s.target_cells))
        self.radius_field.setValue(s.radius)
        self.n_theta.setValue(int(s.n_theta))
        for f, val in ((self.square, s.square), (self.ds_wall, s.ds_wall), (self.ds_mouth, s.ds_mouth),
                       (self.h_tube, s.h_tube), (self.h_max, s.h_max), (self.growth, s.growth),
                       (self.refine, s.refine), (self.tet_h_max, s.tet_h_max), (self.tet_h_tube, s.tet_h_tube),
                       (self.gradient, s.gradient)):
            f.setValue(val)
        self.algorithm.setCurrentText(s.algorithm)
        self.threads.setValue(int(s.threads))
        del blockers
        self._update_kind()

    def _update_cli(self, *_a) -> None:
        try:
            self.cli_edit.setText(cli_command(self.spec()))
        except ValueError as exc:
            self.cli_edit.setText(f"(設定に誤り: {exc})")

    def _update_buttons(self) -> None:
        self.gen_btn.setEnabled(not self._busy)
        self.open_btn.setEnabled(not self._busy)
        self.save_btn.setEnabled(not self._busy and self.preview is not None)
        self.use_btn.setEnabled(not self._busy and self.preview is not None and self.preview.path is not None)
        self.progress.setVisible(self._busy)

    # ------------------------------------------------------------------ 生成・読み込み
    def _attach_log(self) -> None:
        lg = logging.getLogger("gsis_tools")
        if self._log_handler not in lg.handlers:
            lg.addHandler(self._log_handler)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)

    def _detach_log(self) -> None:
        logging.getLogger("gsis_tools").removeHandler(self._log_handler)

    def generate(self) -> bool:
        if self._busy:
            return False
        try:
            spec = self.spec()
        except ValueError as exc:
            QMessageBox.warning(self, "設定の誤り", str(exc))
            return False
        if spec.kind == "tet":
            ensure_gmsh_main_thread()
        self._busy = True
        self._update_buttons()
        self.state_label.set_status("info", "生成しています…")
        self.log.append_lines(f"生成を始めました: {cli_command(spec)}", stream="gui")
        self.tabs.setCurrentWidget(self.log)
        self._attach_log()
        start_task(self, generate_preview, spec, on_done=self._on_ready, on_error=self._on_failed)
        return True

    def load_mesh(self, path: str | Path) -> bool:
        if self._busy:
            return False
        path = Path(path)
        self._busy = True
        self._update_buttons()
        self.state_label.set_status("info", f"読み込んでいます: {path.name}")
        self.log.append_lines(f"読み込み: {path}", stream="gui")
        self._attach_log()
        start_task(self, load_preview, path, on_done=self._on_ready, on_error=self._on_failed)
        return True

    def _open_dialog(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "メッシュを開く", str(tube_mesh_dir(self.ctx.root)),
                                           "メッシュ (*.pmesh);;すべてのファイル (*)")
        if p:
            self.load_mesh(p)

    def _on_ready(self, preview: MeshPreview) -> None:
        self._detach_log()
        self._busy = False
        self.preview = preview
        rep = preview.report
        el = preview.info.get("elapsed")
        what = f"{display_path(preview.path, self.ctx.root)} を読み込みました" if preview.path else "生成しました"
        self.state_label.set_status("ok" if rep.check_ok else "error",
                                    f"{what}: {rep.n_cells:,} セル" + (f"({el:.1f} s)" if el else ""))
        self.log.append_lines(f"{what}: {rep.n_cells:,} セル" + (f"、{el:.1f} s" if el else ""), stream="gui")
        if preview.path is not None:
            from ..meshgen import spec_from_metadata

            s = spec_from_metadata(preview.mesh)
            if s is not None:
                self.set_spec(s)
        self._fill_tables()
        self._show_scene()
        self.tabs.setCurrentWidget(self.quality_table)
        self._update_buttons()
        self.status.emit(f"メッシュ: {rep.n_cells:,} セル")

    def _on_failed(self, msg: str, tb: str) -> None:
        self._detach_log()
        self._busy = False
        self.state_label.set_status("error", f"失敗しました: {msg}")
        self.log.append_lines(tb, kind="error")
        self._update_buttons()

    # ------------------------------------------------------------------ 表示
    def _fill_tables(self) -> None:
        rep = self.preview.report
        rows = rep.rows()
        self.quality_table.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            self.quality_table.setItem(r, 0, readonly_item(k))
            self.quality_table.setItem(r, 1, readonly_item(v))
        self.patch_table.setRowCount(len(rep.patches))
        for r, p in enumerate(rep.patches):
            self.patch_table.setItem(r, 0, readonly_item(p["name"]))
            self.patch_table.setItem(r, 1, readonly_item(p["kind"]))
            self.patch_table.setItem(r, 2, readonly_item(f"{p['faces']:,}", True))
            self.patch_table.setItem(r, 3, readonly_item(f"{p['area']:.6g}", True))
            ex = p.get("exact_area")
            diff = "—" if not ex else f"{(p['area'] - ex) / ex:+.3e}(円 {ex:.6g})"
            self.patch_table.setItem(r, 4, readonly_item(diff))

    def patch_colors(self) -> list[tuple[str, str]]:
        scheme = self.ctx.theme.scheme()
        if self.preview is None:
            return []
        return [(p.name, theme.patch_color(scheme, p.name, p.kind, i)) for i, p in enumerate(self.preview.mesh.patches)]

    def _show_scene(self) -> None:
        if self.preview is None:
            self.view.clear()
            return
        t = self.ctx.theme.tokens()
        colors = [c for _, c in self.patch_colors()]
        n = len(colors)
        edges = self.edges_check.isChecked()
        R = self.preview.report.radius
        items = []
        section = self.rb_section.isChecked() and self.preview.section is not None
        if section:
            cmap = [t["surface_2"]] + colors  # -1 は断面
            items.append(SceneItem(self.preview.section, scalars="patch_id", cmap=cmap, clim=(-1.5, n - 0.5),
                                   n_colors=n + 1, show_edges=edges, edge_color=t["text_muted"]))
            caption = "z ≤ 0 の側のセル(断面は灰色)。色は境界パッチ"
        else:
            items.append(SceneItem(self.preview.boundary, scalars="patch_id", cmap=colors or [t["text_muted"]],
                                   clim=(-0.5, n - 0.5), n_colors=max(n, 1), show_edges=edges,
                                   edge_color=t["text_muted"]))
            caption = "境界面(色は境界パッチ)"
        legend = self.patch_colors() + ([("断面", t["surface_2"])] if section else [])
        scene = Scene(items=items, view=self.view.scene().view if self.view.scene() else "iso",
                      focus_bounds=(6 * R, 20 * R, -2 * R, 2 * R, -2 * R, 2 * R), legend=legend,
                      caption=caption + f"。{self.preview.report.n_cells:,} セル")
        self.view.set_scene(scene)

    def _restyle(self, scheme: str) -> None:
        self.log.restyle(self.ctx.theme.tokens())
        self.view.restyle(scheme)
        if self.preview is not None:
            self._show_scene()

    # ------------------------------------------------------------------ 保存
    def _save_dialog(self) -> None:
        if self.preview is None:
            return
        kind = self.preview.info.get("kind") or self.kind_combo.currentData()
        default = tube_mesh_dir(self.ctx.root) / f"tube_{kind}_{self.preview.report.n_cells}.pmesh"
        p, _ = QFileDialog.getSaveFileName(self, "メッシュを保存", str(self.preview.path or default),
                                           "メッシュ (*.pmesh)")
        if p:
            self.save_mesh(Path(p))

    def save_mesh(self, path: Path) -> bool:
        if self.preview is None or self._busy:
            return False
        self._busy = True
        self._update_buttons()
        self.state_label.set_status("info", f"保存しています: {path.name}")
        start_task(self, _save_mesh_files, self.preview, path, self.vtu_check.isChecked(),
                   on_done=lambda files, p=path: self._on_saved(p, files), on_error=self._on_failed)
        return True

    def _on_saved(self, path: Path, files: list[Path]) -> None:
        self._busy = False
        self.preview.path = path
        self.state_label.set_status("ok", f"保存しました: {display_path(path, self.ctx.root)}")
        for f in files:
            self.log.append_lines(f"書き出し: {f}", stream="gui")
        self._update_buttons()
        self.status.emit(f"メッシュを保存しました: {display_path(path, self.ctx.root)}")

    def _use_in_case(self) -> None:
        if self.preview is not None and self.preview.path is not None:
            self.mesh_selected.emit(str(self.preview.path))

    def shutdown(self) -> None:
        self.view.close_view()


__all__ = ["MeshPage", "asdict"]
