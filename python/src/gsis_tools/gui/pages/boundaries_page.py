"""一般形状の境界条件。面の複数選択、パッチの編集と即時検査。"""

from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import Qt, QItemSelectionModel, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLineEdit, QPushButton, QScrollArea,
                               QSplitter, QTableWidget, QVBoxLayout, QWidget)

from .. import theme
from ..ai.surface import build_polydata, face_label_points
from ..boundaries import (DEFAULT_PATCH, FIELDS, KINDS, assign_boundary, validate_boundary,
                          validate_boundaries, with_default)
from ..widgets.common import FloatField, StatusLabel, heading, muted, primary_button, readonly_item
from ..widgets.vtk_view import Scene, SceneItem, VtkView
from .ai_page import pick_face_at


class BoundariesPage(QWidget):
    changed = Signal()
    accepted = Signal()
    status = Signal(str)

    def __init__(self, ctx, parent=None, view_backend=None):
        super().__init__(parent)
        self.ctx = ctx
        self.surface = None
        self.editing = None
        self._loading = False
        self.has_draft = False
        root = QVBoxLayout(self)
        root.addWidget(heading("境界条件"))
        root.addWidget(muted("面を一覧または 3D で選び、パッチへ割り当てます。Ctrl で複数選択。未割り当て面は既定の拡散壁です。"))
        split = QSplitter()
        root.addWidget(split, 1)
        left = QWidget()
        lay = QVBoxLayout(left)
        self.view = VtkView(backend=view_backend)
        lay.addWidget(self.view, 2)
        self.faces = QTableWidget(0, 4)
        self.faces.setHorizontalHeaderLabels(["面", "パッチ", "面積 [m²]", "法線"])
        self.faces.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.faces.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.faces.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.faces.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.faces, 1)
        split.addWidget(left)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        right = QVBoxLayout(body)
        self.patches = QComboBox()
        right.addWidget(self.patches)
        row = QHBoxLayout()
        self.new_btn = QPushButton("新しいパッチ")
        self.delete_btn = QPushButton("削除して既定の壁へ")
        row.addWidget(self.new_btn)
        row.addWidget(self.delete_btn)
        right.addLayout(row)
        self.form = QFormLayout()
        right.addLayout(self.form)
        self.name = QLineEdit("inlet")
        self.kind = QComboBox()
        for key, label in KINDS.items():
            self.kind.addItem(f"{label} ({key})", key)
        self.form.addRow("パッチ名", self.name)
        self.form.addRow("種類", self.kind)
        self.inputs = {}
        for key, label, value in (("temperature", "温度 [K]", 273.15), ("pressure", "圧力 [Pa]", 100),
                                  ("accommodation", "適応係数 α (0, 1]", 1), ("mass_flow", "流入質量流量 [kg/s]", 1e-9)):
            w = FloatField(value, positive=key != "pressure", non_negative=key == "pressure")
            self.inputs[key] = w
            self.form.addRow(label, w)
            w.textChanged.connect(self._draft_changed)
        self.wall_motion = QCheckBox("壁速度を指定する")
        self.form.addRow(self.wall_motion)
        vector = QWidget()
        vlay = QHBoxLayout(vector)
        vlay.setContentsMargins(0, 0, 0, 0)
        self.velocity = [FloatField(0) for _ in range(3)]
        for axis, w in zip("xyz", self.velocity):
            w.setPlaceholderText(axis)
            w.textChanged.connect(self._draft_changed)
            vlay.addWidget(w)
        self.inputs["velocity"] = vector
        self.form.addRow("壁速度 x, y, z [m/s]", vector)
        tv = QComboBox()
        tv.addItem("内部から外挿 (extrapolate)", "extrapolate")
        tv.addItem("ゼロ (zero)", "zero")
        self.inputs["tangential_velocity"] = tv
        self.form.addRow("接線速度", tv)
        tv.currentIndexChanged.connect(self._draft_changed)
        self.note = StatusLabel()
        right.addWidget(self.note)
        self.apply_btn = primary_button("選択した面を割り当て・更新")
        right.addWidget(self.apply_btn)
        right.addStretch()
        self.accept_btn = primary_button("境界条件を確定してメッシュへ")
        right.addWidget(self.accept_btn)
        scroll.setWidget(body)
        split.addWidget(scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.faces.itemSelectionChanged.connect(self.refresh)
        self.patches.currentIndexChanged.connect(self._edit_patch)
        self.new_btn.clicked.connect(self.new_patch)
        self.delete_btn.clicked.connect(self.delete_patch)
        self.kind.currentIndexChanged.connect(self._draft_changed)
        self.wall_motion.toggled.connect(self._draft_changed)
        self.name.textChanged.connect(self._draft_changed)
        self.apply_btn.clicked.connect(self.apply_patch)
        self.accept_btn.clicked.connect(self.accept)
        ctx.theme.changed.connect(lambda *_: self.draw())
        self._connect_picker()
        self.refresh()

    @property
    def project(self):
        return self.ctx.project

    def selected_faces(self):
        return [int(self.faces.item(i.row(), 0).text()) for i in self.faces.selectionModel().selectedRows()]

    def pick_face(self, tag, add=False):
        if not add:
            self.faces.clearSelection()
        for r in range(self.faces.rowCount()):
            if int(self.faces.item(r, 0).text()) == tag:
                self.faces.selectionModel().select(self.faces.model().index(r, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def _connect_picker(self):
        if self.view.backend != "interactive" or self.view.plotter is None:
            return
        iren = self.view.plotter.iren
        press = []

        def down(*_):
            press[:] = iren.interactor.GetEventPosition()

        def up(*_):
            pos = iren.interactor.GetEventPosition()
            if len(press) == 2 and sum(abs(a - b) for a, b in zip(pos, press)) <= 4:
                tag = pick_face_at(self.view.plotter, *pos)
                if tag is not None:
                    self.pick_face(tag, bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier))
            press.clear()

        self._observers = [iren.add_observer("LeftButtonPressEvent", down), iren.add_observer("LeftButtonReleaseEvent", up)]

    def load_geometry(self, result=None):
        self.surface = copy.copy(result.surface) if result else None
        self.reload()

    def reload(self):
        p = self.project
        faces = p.geometry.get("faces", [])
        p.boundaries = with_default(p.boundaries, faces, p.settings.wall_temperature)
        self._loading = True
        self.patches.clear()
        self.patches.addItem("パッチを選んで編集…", None)
        for b in p.boundaries:
            self.patches.addItem(f"{b['patch']} / {KINDS.get(b['type'], b['type'])} / {len(b['faces'])} 面", b["patch"])
        self.faces.blockSignals(True)
        self.faces.setRowCount(len(faces))
        pof = {f: b["patch"] for b in p.boundaries for f in b.get("faces", [])}
        for r, f in enumerate(faces):
            for c, text in enumerate((str(f["id"]), pof.get(f["id"], DEFAULT_PATCH), f"{f['area']:.6g}",
                                      "不明" if f.get("normal") is None else ", ".join(f"{v:.3g}" for v in f["normal"]))):
                self.faces.setItem(r, c, readonly_item(text))
        self.faces.blockSignals(False)
        self.editing = None
        self.has_draft = False
        self._loading = False
        self.inputs["temperature"].setValue(p.settings.T0)
        self.refresh()

    def new_patch(self):
        self.editing = None
        self.patches.setCurrentIndex(0)
        self.name.setText("patch")
        self.name.setEnabled(True)
        self.apply_btn.setEnabled(True)
        self.refresh()

    def _edit_patch(self):
        if self._loading:
            return
        name = self.patches.currentData()
        self.editing = name
        b = next((b for b in self.project.boundaries if b["patch"] == name), None)
        if not b:
            return
        self._loading = True
        self.name.setText(name)
        self.kind.setCurrentIndex(self.kind.findData(b["type"]))
        for key in ("temperature", "pressure", "mass_flow", "accommodation"):
            if key in b:
                self.inputs[key].setValue(b[key])
        self.inputs["tangential_velocity"].setCurrentIndex(
            self.inputs["tangential_velocity"].findData(b.get("tangential_velocity", "extrapolate")))
        self.wall_motion.setChecked(b.get("velocity") is not None)
        for w, v in zip(self.velocity, b.get("velocity") or [0, 0, 0]):
            w.setValue(v)
        self.faces.clearSelection()
        for tag in b["faces"]:
            self.pick_face(tag, add=True)
        self._loading = False
        self.refresh()

    def collect(self):
        b = dict(patch=self.name.text().strip(), type=self.kind.currentData(), faces=self.selected_faces())
        if self.project.geometry.get("source_kind") == "existing_mesh":
            old = next((p for p in self.project.boundaries if p["patch"] == self.editing), None)
            if old is None or b["patch"] != old["patch"]:
                raise ValueError("既存メッシュではパッチを選んで値を編集してください。名前と割り当ては固定です")
            b["faces"] = list(old["faces"])
        for key in FIELDS[b["type"]]:
            if key == "velocity":
                if self.wall_motion.isChecked():
                    b[key] = [w.value() for w in self.velocity]
            elif key == "tangential_velocity":
                b[key] = self.inputs[key].currentData()
            else:
                b[key] = self.inputs[key].value()
        validate_boundary(b, self.project.geometry.get("faces", []))
        return b

    def _draft_changed(self, *_):
        if not self._loading:
            # 編集途中も済の印を外す。保存されるのは割り当てボタンで確定した値。
            self.changed.emit()
            self.has_draft = True
            self.refresh()

    def refresh(self, *_):
        if self._loading:
            return
        keys = FIELDS[self.kind.currentData()]
        for key, w in self.inputs.items():
            self.form.setRowVisible(w, key in keys and (key != "velocity" or self.wall_motion.isChecked()))
        self.form.setRowVisible(self.wall_motion, "velocity" in keys)
        automatic = self.editing == DEFAULT_PATCH
        self.delete_btn.setEnabled(bool(self.editing) and not automatic)
        self.name.setEnabled(not automatic)
        self.kind.setEnabled(not automatic)
        for w in self.inputs.values():
            w.setEnabled(not automatic)
        self.wall_motion.setEnabled(not automatic)
        existing = self.project.geometry.get("source_kind") == "existing_mesh"
        self.new_btn.setEnabled(not existing)
        if existing:
            self.name.setEnabled(False)
            self.delete_btn.setEnabled(False)
        try:
            self.collect()
            if automatic:
                raise ValueError("既定の壁の温度は設定画面で指定します。変更する面は新しいパッチへ割り当ててください")
            self.note.set_status("ok", f"選択: {len(self.selected_faces())} 面。入力は有効です")
            self.apply_btn.setEnabled(True)
        except (ValueError, TypeError) as exc:
            self.note.set_status("info" if not self.selected_faces() else "error", str(exc))
            self.apply_btn.setEnabled(False)
        try:
            validate_boundaries(self.project.boundaries, self.project.geometry.get("faces", []))
            self.accept_btn.setEnabled(bool(self.project.geometry.get("faces")) and not self.has_draft)
        except ValueError:
            self.accept_btn.setEnabled(False)
        self.draw()

    def apply_patch(self):
        try:
            b = self.collect()
            p = self.project
            p.boundaries = assign_boundary(p.boundaries, b, p.geometry["faces"], p.settings.wall_temperature, replace=self.editing)
            self.changed.emit()
            self.reload()
            self.status.emit(f"{b['patch']} に {len(b['faces'])} 面を割り当てました")
            return True
        except ValueError as exc:
            self.note.set_status("error", str(exc))
            return False

    def delete_patch(self):
        p = self.project
        if p.geometry.get("source_kind") == "existing_mesh":
            return
        p.boundaries = with_default([b for b in p.boundaries if b["patch"] != self.editing], p.geometry["faces"], p.settings.wall_temperature)
        self.changed.emit()
        self.reload()

    def accept(self):
        try:
            if self.has_draft:
                raise ValueError("編集中のパッチを割り当て・更新してください")
            validate_boundaries(self.project.boundaries, self.project.geometry.get("faces", []))
            self.accepted.emit()
        except ValueError as exc:
            self.note.set_status("error", str(exc))

    def draw(self):
        if self.surface is None:
            self.view.clear()
            return
        scheme, t = self.ctx.theme.scheme(), self.ctx.theme.tokens()
        bs = self.project.boundaries
        self.surface.patches = [dict(name=b["patch"], kind=b["type"], faces=b["faces"]) for b in bs]
        data = build_polydata(self.surface, {b["patch"]: i for i, b in enumerate(bs)})
        colors = theme.series(scheme)
        items, legend = [], []
        mesh = data["boundary"]
        if mesh is not None:
            for i, b in enumerate(bs):
                color = t["text_muted"] if b.get("automatic") else colors[i % len(colors)]
                part = mesh.extract_cells(mesh.cell_data["patch_id"] == i)
                if part.n_cells:
                    items.append(SceneItem(part, color=color))
                    legend.append((b["patch"], color))
            selected = mesh.extract_cells(np.isin(mesh.cell_data["face_id"], self.selected_faces()))
            if selected.n_cells:
                items.append(SceneItem(selected.extract_surface(algorithm="dataset_surface").extract_all_edges(), color=t["accent"],
                                       line_width=4, lighting=False, pickable=False))
        if data["lines"] is not None:
            items.append(SceneItem(data["lines"], color=t["text"], line_width=1.5, pickable=False))
        pts, labels = face_label_points(self.surface)
        self.view.restyle(scheme)
        self.view.set_scene(Scene(items=items, legend=legend, caption="境界パッチ [m]",
                                  labels=list(zip(map(tuple, pts), labels))), keep_camera=True)

    def shutdown(self):
        self.view.close()
