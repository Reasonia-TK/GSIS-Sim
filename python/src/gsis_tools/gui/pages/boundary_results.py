"""パッチの集計表と boundary.vtu の壁面分布。"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QPushButton,
                               QTableWidget, QVBoxLayout, QWidget)

from ..fields import SHOW, component_values, components, list_fields, scale_for
from ..patch_results import COLUMNS, HEADERS, MISSING, export_patch_csv
from ..widgets.common import StatusLabel, muted, readonly_item
from ..widgets.vtk_view import Scene, SceneItem, VtkView


class BoundaryResultsTab(QWidget):
    def __init__(self, ctx, view_backend=None):
        super().__init__()
        self.ctx, self.bundle = ctx, None
        layout = QVBoxLayout(self)
        layout.addWidget(muted("質量・熱は領域から外向きを正とします。力は壁が受ける力です。"
            "開口の運動量輸送と全エネルギー流束は、壁の力・熱流量に置き換えません。"))
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(210)
        layout.addWidget(self.table)
        bar = QHBoxLayout()
        self.csv_btn = QPushButton("パッチ集計を CSV に書き出す…")
        self.patch, self.field, self.comp = QComboBox(), QComboBox(), QComboBox()
        for w in (self.csv_btn, self.patch, self.field, self.comp):
            bar.addWidget(w)
        bar.addStretch()
        layout.addLayout(bar)
        self.view = VtkView(backend=view_backend)
        layout.addWidget(self.view, 1)
        self.note = StatusLabel("boundary.vtu の量を SI で色付けします")
        layout.addWidget(self.note)
        self.field.currentIndexChanged.connect(self._field_changed)
        self.comp.currentIndexChanged.connect(self.draw)
        self.patch.currentIndexChanged.connect(self.draw)
        self.csv_btn.clicked.connect(self._export)
        ctx.theme.changed.connect(self.draw)

    def set_bundle(self, bundle):
        self.bundle = bundle
        rows = bundle.patch_totals if bundle else []
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(COLUMNS):
                value = row.get(key)
                text = MISSING if value is None else f"{value:.6g}" if isinstance(value, float) else str(value)
                self.table.setItem(r, c, readonly_item(text))
        with QSignalBlocker(self.patch), QSignalBlocker(self.field):
            self.patch.clear()
            self.field.clear()
            self.patch.addItem("壁の全パッチ", "walls")
            self.patch.addItem("境界の全パッチ", "all")
            if bundle and bundle.boundary is not None:
                names = bundle.run.patch_names()
                ids = np.asarray(bundle.boundary.cell_data.get("patch_id", []), dtype=int)
                for i in sorted(set(ids.tolist())):
                    self.patch.addItem(names[i] if 0 <= i < len(names) else f"patch_{i}", int(i))
                for spec in list_fields(bundle.boundary):
                    if spec.name != "patch_id":
                        self.field.addItem(spec.label, spec)
                index = next((i for i in range(self.field.count())
                              if self.field.itemData(i).name == "normal_stress"), 0)
                self.field.setCurrentIndex(index)
        self.csv_btn.setEnabled(bool(rows))
        self._field_changed()

    def _field_changed(self, *_):
        with QSignalBlocker(self.comp):
            self.comp.clear()
            spec = self.field.currentData()
            if spec:
                for key, label in components(spec):
                    self.comp.addItem(label, key)
        self.draw()

    def draw(self, *_):
        b, spec = self.bundle, self.field.currentData()
        if b is None or b.boundary is None or spec is None:
            self.view.clear()
            self.note.set_status("info", "boundary.vtu はソルバーの出力にない、または表示できる量がありません")
            return
        boundary = b.boundary
        selection = self.patch.currentData()
        if selection != "all":
            if "patch_id" not in boundary.cell_data:
                self.view.clear()
                self.note.set_status("warning", "patch_id がないため壁だけを選べません。境界の全パッチを選んでください")
                return
            ids = np.asarray(boundary.cell_data["patch_id"])
            wall_ids = [i for i, name in enumerate(b.run.patch_names())
                        if b.run.patch_types().get(name) in ("diffuse_wall", "maxwell_wall")]
            mask = np.isin(ids, wall_ids) if selection == "walls" else ids == selection
            boundary = boundary.extract_cells(mask)
        if boundary.n_cells == 0:
            self.view.clear()
            self.note.set_status("info", "選んだパッチに表示できる境界面がありません")
            return
        shown = boundary.copy(deep=True)
        comp = self.comp.currentData() or "value"
        values = component_values(shown, spec.name, comp)
        shown.cell_data[SHOW] = values
        _, title = scale_for(spec.name, b.run.scales, False, spec, comp)
        finite = values[np.isfinite(values)]
        if not finite.size:
            self.view.clear()
            self.note.set_status("warning", "有限の値がありません")
            return
        self.view.restyle(self.ctx.theme.scheme())
        self.view.set_scene(Scene(items=[SceneItem(shown, scalars=SHOW, cmap="cividis", scalar_bar_title=title)],
                                  caption="boundary.vtu / SI", view="iso"), keep_camera=True)
        self.note.set_status("ok", f"{spec.label}: {finite.min():.6g} ～ {finite.max():.6g} / {shown.n_cells:,} 面")

    def export_csv(self, path):
        return export_patch_csv(path, self.bundle.patch_totals)

    def _export(self):
        if self.bundle:
            path, _ = QFileDialog.getSaveFileName(self, "パッチ集計", str(self.bundle.run.directory / "patch_totals.csv"), "CSV (*.csv)")
            if path:
                self.export_csv(path)

    def shutdown(self):
        self.view.close()
