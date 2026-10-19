"""結果の画面の「3D 表示」: 場の断面(任意の原点と法線)・等値線・境界面、流線。

計算(断面・等値線・流線)は :class:`~gsis_tools.gui.workers.JobRunner` で別スレッドに出し、
この画面は出来上がったものを描くだけにする(13.5 万セルの結果でも操作が固まらない)。

- 場と表示: 場・成分・無次元、表示の形(3D・断面・断面と等値線・境界面・なし)、色の範囲、領域の輪郭
- 断面: プリセット(z = 0・y = 0・x 一定)と自由な原点・法線、法線の向きに動かすスライダー、
  平面ウィジェット(対話型の 3D 表示のときだけ。マウスで動かして離すと断面を作り直す)
- 流線: 種(円盤・線分・点の群・断面上の 2 次元)、本数、積分の向き、長さの上限。速さで色を付ける
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..context import AppContext
from ..fields import (
    DIRECTIONS,
    DISPLAY_MODES,
    PLANE_PRESETS,
    RANGE_MODES,
    SEED_KINDS,
    SHOW,
    FieldSpec,
    PlaneSpec,
    ResultBundle,
    StreamSpec,
    VisRequest,
    VisResult,
    build_vis,
    component_suffix,
    components,
    list_fields,
    plane_offset_range,
    unit,
)
from ..widgets.common import FloatField, StatusLabel, muted, primary_button
from ..widgets.vtk_view import Scene, SceneItem, VtkView
from ..workers import JobRunner

TUBE_FOCUS = (6.0, 20.0, -2.0, 2.0, -2.0, 2.0)  # 管の付近(L_ref 単位)


class Vec3Field(QWidget):
    """3 つの数値の入力欄(x, y, z)。"""

    edited = Signal()

    def __init__(self, value=(0.0, 0.0, 0.0), parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.fields = [FloatField(v) for v in value]
        for f, ph in zip(self.fields, ("x", "y", "z")):
            f.setPlaceholderText(ph)
            f.setMinimumWidth(60)
            f.editingFinished.connect(self.edited)
            lay.addWidget(f)

    def value(self) -> tuple[float, float, float]:
        out = []
        for f in self.fields:
            if not f.is_valid() or f.value() is None:
                raise ValueError(f"数値ではありません: {f.text()!r}")
            out.append(float(f.value()))
        return tuple(out)

    def setValue(self, v) -> None:
        for f, x in zip(self.fields, v):
            f.setValue(float(x))


def _other_cmap(cmap: str) -> str:
    return "viridis" if cmap == "cividis" else "cividis"


class View3DTab(QWidget):
    """「3D 表示」のタブ。"""

    status = Signal(str)

    def __init__(self, ctx: AppContext, runner: JobRunner, view_backend: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self.bundle: ResultBundle | None = None
        self.line_provider: Callable[[], tuple | None] | None = None
        self._applied_stream: StreamSpec | None = None
        self._last: VisResult | None = None
        self._scene_key = None
        self._building = False
        self._build(view_backend)
        runner.busy_changed.connect(self._on_busy)

    # ------------------------------------------------------------------ 画面
    def _build(self, view_backend: str | None) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.ctl = QTabWidget()
        self.ctl.setMaximumWidth(380)
        self.ctl.setMinimumWidth(330)
        self.ctl.addTab(self._page_field(), "場と表示")
        self.ctl.addTab(self._page_plane(), "断面")
        self.ctl.addTab(self._page_stream(), "流線")
        lay.addWidget(self.ctl)
        right = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(QLabel("視点"))
        for key, label in (("iso", "斜め"), ("xy", "上から"), ("plane", "断面に垂直"), ("focus", "管の付近")):
            b = QPushButton(label)
            if key == "focus":
                self.focus_btn = b
            b.clicked.connect(lambda _c=False, k=key: self.view.set_view(k))
            top.addWidget(b)
        top.addStretch(1)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setVisible(False)
        self.busy_label = muted("", wrap=False)
        top.addWidget(self.busy_label)
        top.addWidget(self.busy)
        self.save_img_btn = QPushButton("画像を保存…")
        top.addWidget(self.save_img_btn)
        right.addLayout(top)
        self.view = VtkView(backend=view_backend)
        right.addWidget(self.view, 1)
        self.state = StatusLabel("")
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        right.addWidget(self.state)
        lay.addLayout(right, 1)
        self.save_img_btn.clicked.connect(self._save_image)

    def _scroll(self, inner: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        return sa

    def _page_field(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.field_combo = QComboBox()
        self.comp_combo = QComboBox()
        self.nondim_check = QCheckBox("無次元で表示(p/p_in、u/√(2RT0)、T/T0 など)")
        self.nondim_check.setChecked(True)
        self.mode_combo = QComboBox()
        for k, v in DISPLAY_MODES.items():
            self.mode_combo.addItem(v, k)
        self.ncont_spin = QSpinBox()
        self.ncont_spin.setRange(2, 60)
        self.ncont_spin.setValue(12)
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(list(theme.COLORMAPS))
        self.range_combo = QComboBox()
        for k, v in RANGE_MODES.items():
            self.range_combo.addItem(v, k)
        self.vmin = FloatField(None, allow_empty=True, placeholder="最小")
        self.vmax = FloatField(None, allow_empty=True, placeholder="最大")
        rng = QHBoxLayout()
        rng.addWidget(self.vmin)
        rng.addWidget(self.vmax)
        self.outline_check = QCheckBox("領域の輪郭を薄く表示")
        f.addRow("場", self.field_combo)
        f.addRow("成分", self.comp_combo)
        f.addRow(self.nondim_check)
        f.addRow("表示", self.mode_combo)
        f.addRow("等値線の本数", self.ncont_spin)
        f.addRow("カラーマップ", self.cmap_combo)
        f.addRow("色の範囲", self.range_combo)
        f.addRow("最小・最大", rng)
        f.addRow(self.outline_check)
        self.field_stats = muted("")
        f.addRow(self.field_stats)
        f.addRow(muted("断面の位置と向きは「断面」、流線は「流線」のページで決める。計算は別のスレッドで行い、"
                       "終わったら描き直す。"))
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        for wdg in (self.comp_combo, self.mode_combo, self.cmap_combo, self.range_combo):
            wdg.currentIndexChanged.connect(lambda _i: self.request_update())
        self.nondim_check.toggled.connect(lambda _b: self.request_update())
        self.outline_check.toggled.connect(lambda _b: self.request_update())
        self.ncont_spin.valueChanged.connect(lambda _v: self.request_update())
        self.vmin.editingFinished.connect(self.request_update)
        self.vmax.editingFinished.connect(self.request_update)
        self.range_combo.currentIndexChanged.connect(self._update_range_enabled)
        self._update_range_enabled()
        return self._scroll(w)

    def _page_plane(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.plane_preset = QComboBox()
        for k, (label, _o, _n) in PLANE_PRESETS.items():
            self.plane_preset.addItem(label, k)
        self.origin_field = Vec3Field((0.0, 0.0, 0.0))
        self.normal_field = Vec3Field((0.0, 0.0, 1.0))
        self.offset_slider = QSlider(Qt.Orientation.Horizontal)
        self.offset_slider.setRange(-1000, 1000)
        self.offset_slider.setValue(0)
        self.offset_label = muted("", wrap=False)
        self.widget_check = QCheckBox("平面ウィジェットで動かす(マウスで動かして離すと作り直す)")
        self.plane_label = muted("")
        f.addRow("プリセット", self.plane_preset)
        f.addRow("原点 [L_ref]", self.origin_field)
        f.addRow("法線", self.normal_field)
        f.addRow("法線の向きに動かす", self.offset_slider)
        f.addRow("", self.offset_label)
        f.addRow(self.widget_check)
        f.addRow(self.plane_label)
        f.addRow(muted("座標は L_ref(管半径)単位。管の軸は x で、入口 x = 8・出口 x = 18(X = x − 8)。"
                       "「3D」の表示では、既定の斜めの視点から見て断面の奥の側の壁を描く。"))
        self.plane_preset.currentIndexChanged.connect(self._on_plane_preset)
        self.origin_field.edited.connect(self._on_plane_edited)
        self.normal_field.edited.connect(self._on_plane_edited)
        self.offset_slider.valueChanged.connect(self._on_slider_moved)
        self.offset_slider.sliderReleased.connect(self._on_slider_released)
        self.offset_slider.valueChanged.connect(lambda _v: (not self.offset_slider.isSliderDown()) and
                                                self._on_slider_released())
        self.widget_check.toggled.connect(self._on_widget_toggled)
        self._slider_base: PlaneSpec | None = None
        self._slider_range = (-1.0, 1.0)
        return self._scroll(w)

    def _page_stream(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.f_stream = f
        d = StreamSpec()
        self.stream_check = QCheckBox("流線を描く")
        self.seed_combo = QComboBox()
        for k, v in SEED_KINDS.items():
            self.seed_combo.addItem(v, k)
        self.seed_center = Vec3Field(d.center)
        self.seed_normal = Vec3Field(d.normal)
        self.seed_radius = FloatField(d.radius, positive=True)
        self.seed_a = Vec3Field(d.a)
        self.seed_b = Vec3Field(d.b)
        self.seed_n = QSpinBox()
        self.seed_n.setRange(1, 3000)
        self.seed_n.setValue(d.n)
        self.dir_combo = QComboBox()
        for k, v in DIRECTIONS.items():
            self.dir_combo.addItem(v, k)
        self.max_len = FloatField(d.max_length, positive=True)
        self.tube_check = QCheckBox("管(チューブ)のように描く")
        self.tube_check.setChecked(True)
        self.stream_btn = primary_button("流線を計算")
        self.stream_info = muted("")
        f.addRow(self.stream_check)
        f.addRow("種の置き方", self.seed_combo)
        f.addRow("中心 [L_ref]", self.seed_center)
        f.addRow("円盤の法線", self.seed_normal)
        f.addRow("半径 [L_ref]", self.seed_radius)
        f.addRow("点 A [L_ref]", self.seed_a)
        f.addRow("点 B [L_ref]", self.seed_b)
        f.addRow("本数(種の数)", self.seed_n)
        f.addRow("積分の向き", self.dir_combo)
        f.addRow("長さの上限 [L_ref]", self.max_len)
        f.addRow(self.tube_check)
        f.addRow(self.stream_btn)
        f.addRow(self.stream_info)
        f.addRow(muted("セル値の速度を点へ補間して積分する(RK45)。色は速さ。「断面上の 2 次元の流線」は、"
                       "「断面」のページの断面に投影した速度の流線で、色は面内の速さ。"))
        self.seed_combo.currentIndexChanged.connect(self._update_seed_rows)
        self.stream_btn.clicked.connect(self.apply_stream)
        self.stream_check.toggled.connect(self._on_stream_toggled)
        self.tube_check.toggled.connect(lambda _b: self._render(self._last) if self._last else None)
        self._update_seed_rows()
        return self._scroll(w)

    # ------------------------------------------------------------------ 状態
    def set_bundle(self, b: ResultBundle | None) -> None:
        self.bundle = b
        tube = b is not None and b.run.is_tube_example
        self.focus_btn.setVisible(tube)
        self.nondim_check.setText("無次元で表示(p/p_in、u/√(2RT0)、T/T0 など)" if tube else
                                 "基準量で無次元化(p/p_ref、u/√(2RT0)、T/T0 など)")
        if b is not None:
            self.view.clear()
            self._applied_stream = None
            with QSignalBlocker(self.stream_check):
                self.stream_check.setChecked(False)
        if b is not None and not tube and b.bounds is not None:
            centre = [(b.bounds[2 * i] + b.bounds[2 * i + 1]) / (2 * b.length) for i in range(3)]
            self.set_plane(centre, (0, 0, 1), preset="free", update=False)
            size = [(b.bounds[2*i+1] - b.bounds[2*i]) / b.length for i in range(3)]
            axis = int(np.argmax(size))
            normal = [float(i == axis) for i in range(3)]
            radius = max(min(v for v in size if v > 0) * .1, 1e-12)
            self.seed_center.setValue(centre)
            self.seed_normal.setValue(normal)
            self.seed_radius.setValue(radius)
            a, end = list(centre), list(centre)
            a[(axis + 1) % 3] -= radius
            end[(axis + 1) % 3] += radius
            self.seed_a.setValue(a)
            self.seed_b.setValue(end)
        elif tube:
            self.set_plane((0, 0, 0), (0, 0, 1), preset="z0", update=False)
            defaults = StreamSpec()
            self.seed_center.setValue(defaults.center)
            self.seed_normal.setValue(defaults.normal)
            self.seed_radius.setValue(defaults.radius)
            self.seed_a.setValue(defaults.a)
            self.seed_b.setValue(defaults.b)
        for i, (key, (label, _, _)) in enumerate(PLANE_PRESETS.items()):
            self.plane_preset.setItemText(i, label if tube else {"z0": "z = 0 (xy 面)", "y0": "y = 0 (xz 面)",
                "x": "領域中央の x 一定面", "free": "自由(原点と法線を入力)"}[key])
        self._last = None
        self._scene_key = None
        with QSignalBlocker(self.field_combo):
            self.field_combo.clear()
            if b is not None and b.grid is not None:
                for spec in list_fields(b.grid):
                    self.field_combo.addItem(f"{spec.label}({spec.name})", spec)
                idx = next((i for i in range(self.field_combo.count()) if self.field_combo.itemData(i).name == "p"), 0)
                self.field_combo.setCurrentIndex(idx)
        self._fill_components()
        self._update_slider_range()
        if self.widget_check.isChecked():
            self._on_widget_toggled(True)
        if b is None or b.grid is None:
            self.view.clear()
            self.field_stats.setText("VTU がありません" if b is not None else "")
            self.state.set_status(None, "VTU がないので 3D 表示はない" if b is not None else "")
            return
        self.request_update()

    def _fill_components(self) -> None:
        spec: FieldSpec | None = self.field_combo.currentData()
        with QSignalBlocker(self.comp_combo):
            self.comp_combo.clear()
            if spec is None:
                return
            for key, label in components(spec):
                self.comp_combo.addItem(label, key)
            if spec.n_comp == 3:  # 速度は x 成分(軸方向)を既定にする
                self.comp_combo.setCurrentIndex(1)
        self.comp_combo.setEnabled(spec.n_comp > 1)

    def _on_field_changed(self, *_a) -> None:
        self._fill_components()
        self.request_update()

    def _update_range_enabled(self) -> None:
        manual = self.range_combo.currentData() == "manual"
        self.vmin.setEnabled(manual)
        self.vmax.setEnabled(manual)

    # ------------------------------------------------------------------ 断面
    def plane(self) -> PlaneSpec:
        o = self.origin_field.value()
        n = tuple(float(v) for v in unit(self.normal_field.value()))
        return PlaneSpec(o, n)

    def set_plane(self, origin, normal, preset: str | None = None, update: bool = True) -> None:
        self.origin_field.setValue(origin)
        self.normal_field.setValue(normal)
        if preset is not None:
            with QSignalBlocker(self.plane_preset):
                self.plane_preset.setCurrentIndex(max(self.plane_preset.findData(preset), 0))
        self._update_slider_range()
        self._sync_widget()
        if update:
            self.request_update()

    def _on_plane_preset(self, *_a) -> None:
        key = self.plane_preset.currentData()
        _label, o, n = PLANE_PRESETS[key]
        if o is None:
            return  # 自由: 入力欄の値のまま
        if key == "x" and self.bundle and not self.bundle.run.is_tube_example and self.bundle.bounds:
            o = tuple((self.bundle.bounds[2*i] + self.bundle.bounds[2*i+1]) / (2*self.bundle.length) for i in range(3))
        self.set_plane(o, n, None)

    def _on_plane_edited(self) -> None:
        try:
            p = self.plane()
        except ValueError as exc:
            self.state.set_status("error", f"断面の入力の誤り: {exc}")
            return
        # 入力がプリセットと違えば「自由」にする
        key = self.plane_preset.currentData()
        _l, o, n = PLANE_PRESETS.get(key, ("", None, None))
        if n is not None and not np.allclose(unit(n), p.normal):
            with QSignalBlocker(self.plane_preset):
                self.plane_preset.setCurrentIndex(self.plane_preset.findData("free"))
        self._update_slider_range()
        self._sync_widget()
        self.request_update()

    def _update_slider_range(self) -> None:
        b = self.bundle
        try:
            base = self.plane()
        except ValueError:
            return
        self._slider_base = base
        if b is None or b.grid is None:
            self._slider_range = (-1.0, 1.0)
        else:
            self._slider_range = plane_offset_range(b.grid.bounds, base, b.length)
        with QSignalBlocker(self.offset_slider):
            lo, hi = self._slider_range
            self.offset_slider.setValue(int(round(-1000 + 2000 * (0.0 - lo) / (hi - lo))) if hi > lo else 0)
        self._show_offset(0.0)

    def _slider_offset(self) -> float:
        lo, hi = self._slider_range
        return lo + (hi - lo) * (self.offset_slider.value() + 1000) / 2000.0

    def _show_offset(self, d: float) -> None:
        lo, hi = self._slider_range
        self.offset_label.setText(f"移動 {d:+.3f}(動かせる範囲 {lo:.2f}〜{hi:.2f})")
        try:
            p = self.plane()
            self.plane_label.setText("断面: ({:.4g}, {:.4g}, {:.4g}) を通り、法線 ({:.3f}, {:.3f}, {:.3f})".format(
                *p.origin, *p.normal))
        except ValueError:
            pass

    def _on_slider_moved(self, _v: int) -> None:
        self._show_offset(self._slider_offset())

    def _on_slider_released(self) -> None:
        if self._slider_base is None:
            return
        d = self._slider_offset()
        base = self._slider_base
        o = np.asarray(base.origin) + d * np.asarray(base.normal)  # 法線の向きは変えない(プリセットのまま)
        self.origin_field.setValue(tuple(o))
        self._show_offset(d)
        self._sync_widget()
        self.request_update()

    # --- 平面ウィジェット ---
    def _on_widget_toggled(self, on: bool) -> None:
        if not self.view.supports_plane_widget:
            if on:
                self.state.set_status("warning", "平面ウィジェットは対話型の 3D 表示でだけ使えます(いまは静止画の方式)")
                with QSignalBlocker(self.widget_check):
                    self.widget_check.setChecked(False)
            return
        self._sync_widget()

    def _sync_widget(self) -> None:
        if not self.view.supports_plane_widget:
            return
        b = self.bundle
        if not self.widget_check.isChecked() or b is None or b.grid is None:
            self.view.set_plane_widget(False)
            return
        try:
            p = self.plane()
        except ValueError:
            return
        L = b.length
        self.view.set_plane_widget(True, origin=tuple(np.asarray(p.origin) * L), normal=p.normal,
                                   bounds=b.grid.bounds, callback=self._on_widget_moved)

    def _on_widget_moved(self, normal, origin_m) -> None:
        if self.bundle is None:
            return
        L = self.bundle.length
        self.origin_field.setValue(tuple(np.asarray(origin_m) / L))
        self.normal_field.setValue(tuple(unit(normal)))
        with QSignalBlocker(self.plane_preset):
            self.plane_preset.setCurrentIndex(self.plane_preset.findData("free"))
        self._update_slider_range()
        self.request_update()

    # ------------------------------------------------------------------ 流線
    def _update_seed_rows(self) -> None:
        kind = self.seed_combo.currentData()
        rows = {
            self.seed_center: kind in ("disk", "cloud"),
            self.seed_normal: kind == "disk",
            self.seed_radius: kind in ("disk", "cloud"),
            self.seed_a: kind == "line",
            self.seed_b: kind == "line",
        }
        for wdg, visible in rows.items():
            self.f_stream.setRowVisible(wdg, visible)

    def stream_spec(self) -> StreamSpec:
        kind = self.seed_combo.currentData()
        r = self.seed_radius.value()
        if kind in ("disk", "cloud") and not (r and r > 0):
            raise ValueError("半径は正の数にしてください")
        ml = self.max_len.value()
        if not (ml and ml > 0):
            raise ValueError("長さの上限は正の数にしてください")
        normal = tuple(float(v) for v in unit(self.seed_normal.value())) if kind == "disk" else StreamSpec().normal
        return StreamSpec(kind=kind, center=self.seed_center.value(), normal=normal, radius=float(r or 1.0),
                          a=self.seed_a.value(), b=self.seed_b.value(), n=self.seed_n.value(),
                          direction=self.dir_combo.currentData(), max_length=float(ml))

    def apply_stream(self) -> bool:
        """入力欄の設定で流線を計算する(「流線を描く」も入れる)。"""
        try:
            spec = self.stream_spec()
        except ValueError as exc:
            self.stream_info.setText(f"入力の誤り: {exc}")
            return False
        self._applied_stream = spec
        with QSignalBlocker(self.stream_check):
            self.stream_check.setChecked(True)
        self.stream_info.setText("流線を計算しています…")
        self.request_update()
        return True

    def _on_stream_toggled(self, on: bool) -> None:
        if on and self._applied_stream is None:
            self.apply_stream()
        else:
            self.request_update()

    # ------------------------------------------------------------------ 計算と描画
    def request(self) -> VisRequest | None:
        spec: FieldSpec | None = self.field_combo.currentData()
        if spec is None:
            return None
        plane = self.plane()
        clim = None
        if self.range_combo.currentData() == "manual":
            lo, hi = self.vmin.value(), self.vmax.value()
            if lo is not None and hi is not None and self.vmin.is_valid() and self.vmax.is_valid() and lo < hi:
                clim = (float(lo), float(hi))
        line = self.line_provider() if self.line_provider is not None else None
        return VisRequest(
            field=spec.name, comp=self.comp_combo.currentData() or "value", nondim=self.nondim_check.isChecked(),
            mode=self.mode_combo.currentData(), n_contours=self.ncont_spin.value(),
            range_mode=self.range_combo.currentData() if (self.range_combo.currentData() != "manual" or clim) else "all",
            clim=clim, plane=plane,
            stream=self._applied_stream if self.stream_check.isChecked() else None,
            outline=self.outline_check.isChecked(), line=line,
        )

    def request_update(self) -> None:
        b = self.bundle
        if b is None or b.grid is None:
            return
        try:
            req = self.request()
        except ValueError as exc:
            self.state.set_status("error", f"入力の誤り: {exc}")
            return
        if req is None:
            return
        self.runner.submit("vis", build_vis, b, req, on_done=self._on_vis, on_error=self._on_vis_error)

    def _on_busy(self, busy: bool) -> None:
        self.busy.setVisible(busy)
        self.busy_label.setText("計算しています…" if busy else "")

    def _on_vis_error(self, msg: str, tb: str) -> None:
        self.state.set_status("error", f"3D 表示を作れませんでした: {msg}")
        if self._applied_stream is not None:
            self.stream_info.setText(f"流線を作れませんでした: {msg}")

    def _on_vis(self, res: VisResult) -> None:
        if self.bundle is None or res.bundle_id != id(self.bundle):
            return  # 前に読んだ結果の計算
        self._last = res
        self._render(res)

    def _render(self, res: VisResult) -> None:
        t = self.ctx.theme.tokens()
        req = res.request
        cmap = self.cmap_combo.currentText()
        items: list[SceneItem] = []
        field_bar = False
        if res.outline is not None:
            items.append(SceneItem(res.outline, color=t["text_muted"], opacity=0.12, lighting=False))
        for ds, bar in ((res.slice, True), (res.surface, True), (res.half, False)):
            if ds is not None:
                items.append(SceneItem(ds, scalars=SHOW, cmap=cmap, clim=res.clim,
                                       scalar_bar_title=res.label if bar and not field_bar else None))
                field_bar = field_bar or bar
        if res.contours is not None:
            items.append(SceneItem(res.contours, color=t["text"], line_width=1.6, lighting=False))
        if res.streams is not None:
            items.append(SceneItem(res.streams, scalars="__speed_show__", cmap=_other_cmap(cmap),
                                   clim=res.stream_clim, scalar_bar_title=res.stream_label,
                                   scalar_bar_pos="left" if field_bar else "right",
                                   line_width=3.0 if self.tube_check.isChecked() else 1.6,
                                   render_lines_as_tubes=self.tube_check.isChecked()))
        if res.line is not None:
            items.append(SceneItem(res.line, color=t["accent"], line_width=4.0, lighting=False))
        L = self.bundle.length if self.bundle else 1.0
        parallel = req.mode in ("slice", "contour")
        focus = tuple(v * L for v in TUBE_FOCUS) if self.bundle.run.is_tube_example else self.bundle.bounds
        if parallel and res.slice is not None:
            # 断面の範囲と管の付近の箱の重なり(管を横切る断面なら管の断面に合わせて拡大する)
            sb = res.slice.bounds
            lo = [max(sb[2 * i], focus[2 * i]) for i in range(3)]
            hi = [min(sb[2 * i + 1], focus[2 * i + 1]) for i in range(3)]
            if all(h >= l for l, h in zip(lo, hi)):
                focus = (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
        view = self.view.scene().view if self.view.scene() else ("focus" if self.bundle.run.is_tube_example else "iso")
        key = (res.bundle_id, req.mode, req.plane.normal if parallel else None)
        spec: FieldSpec | None = self.field_combo.currentData()
        if spec is not None and spec.name != req.field:  # 場を変える前の要求の結果(説明は要求の場で書く)
            spec = next((d for d in (self.field_combo.itemData(i) for i in range(self.field_combo.count()))
                         if getattr(d, "name", None) == req.field), None)
        cap = DISPLAY_MODES[req.mode]
        if spec is not None and req.mode != "none":
            known = req.comp == "mag" or str(req.comp).isdigit()  # 成分の欄を作り直している間の要求は "value"
            cap = f"{spec.label}{component_suffix(spec, req.comp) if spec.n_comp > 1 and known else ''}。" + cap
        if res.streams is not None:
            cap += "。" + res.stream_info
        scene = Scene(items=items, view=view, focus_bounds=focus, caption=cap, parallel=parallel,
                      plane_normal=req.plane.normal)
        self.view.set_scene(scene, keep_camera=key == self._scene_key)
        self._scene_key = key
        lo, hi = res.stats
        self.field_stats.setText(f"範囲(領域全体){lo:.5g} 〜 {hi:.5g}({res.label})。色の範囲 {res.clim[0]:.4g} 〜 "
                                 f"{res.clim[1]:.4g}")
        if req.stream is not None:
            self.stream_info.setText(res.stream_info)
        notes = "。".join(res.notes)
        self.state.set_status("warning" if res.notes else None,
                              f"描きました({res.elapsed:.2f} 秒)" + (f"。{notes}" if notes else ""))

    def restyle(self, scheme: str) -> None:
        self.view.restyle(scheme)
        if self._last is not None:
            self._render(self._last)

    def _save_image(self) -> None:
        if self.view.scene() is None:
            return
        name = self.bundle.run.name if self.bundle else "view"
        start = str((self.bundle.run.directory if self.bundle else self.ctx.root) / f"{name}_3d.png")
        p, _ = QFileDialog.getSaveFileName(self, "画像を保存", start, "PNG 画像 (*.png)")
        if p:
            self.view.screenshot(p)
            self.status.emit(f"画像を保存しました: {p}")

    def shutdown(self) -> None:
        self.view.close_view()


__all__ = ["View3DTab", "Vec3Field", "math"]
