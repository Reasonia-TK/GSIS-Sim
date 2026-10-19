"""一般形状の四面体メッシュ、品質と実セル数による VRAM の検査。"""

from __future__ import annotations

import copy
import json
import threading

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
                               QTableWidget, QVBoxLayout, QWidget)

from .. import theme
from ..general_mesh import generate_mesh, mesh_memory, validate_options
from ..widgets.common import FloatField, StatusLabel, heading, muted, primary_button, readonly_item
from ..widgets.vtk_view import Scene, SceneItem, VtkView
from ..workers import start_task


class MeshPage(QWidget):
    status = Signal(str)
    changed = Signal()
    ready = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, ctx, parent=None, view_backend=None):
        super().__init__(parent)
        self.ctx = ctx
        self.busy = False
        self._loading = False
        self.result = None
        self._mesh = None
        root = QVBoxLayout(self)
        root.addWidget(heading("メッシュ"))
        root.addWidget(muted("gmsh の四面体メッシュを作ります。寸法は SI (m)。成長率はパッチから離れるときの寸法の増え方です。"))
        row = QHBoxLayout()
        root.addLayout(row, 1)
        left = QVBoxLayout()
        row.addLayout(left, 2)
        self.options_body = QWidget()
        form = QFormLayout(self.options_body)
        self.name = QLineEdit("fluid")
        self.size_max = FloatField(0.001, positive=True)
        self.size_min = FloatField(0.0001, positive=True)
        self.growth = FloatField(1.3, positive=True)
        for label, w in (("メッシュ名", self.name), ("最大寸法 [m]", self.size_max),
                         ("最小寸法 [m]", self.size_min), ("成長率 (1, 3]", self.growth)):
            form.addRow(label, w)
            w.textChanged.connect(self._edited)
        left.addWidget(self.options_body)
        self.patch_sizes = QTableWidget(0, 2)
        self.patch_sizes.setHorizontalHeaderLabels(["パッチ", "寸法 [m] (空欄: 全体)"])
        self.patch_sizes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        left.addWidget(self.patch_sizes)
        self.note = StatusLabel()
        left.addWidget(self.note)
        buttons = QHBoxLayout()
        self.generate_btn = primary_button("メッシュを生成")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        buttons.addWidget(self.generate_btn)
        buttons.addWidget(self.stop_btn)
        left.addLayout(buttons)
        self.view = VtkView(backend=view_backend)
        row.addWidget(self.view, 3)
        self.quality = StatusLabel("生成後にセル数・非直交角・歪み・体積比を表示します")
        root.addWidget(self.quality)
        self.areas = QTableWidget(0, 5)
        self.areas.setHorizontalHeaderLabels(["パッチ", "メッシュ面積 [m²]", "形状面積 [m²]", "差 [m²]", "相対差"])
        self.areas.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.areas.setMaximumHeight(180)
        root.addWidget(self.areas)
        self.memory = StatusLabel()
        root.addWidget(self.memory)
        self.generate_btn.clicked.connect(self.generate)
        self.stop_btn.clicked.connect(self.stop)
        ctx.theme.changed.connect(lambda *_: self.draw())
        self.reload()

    @property
    def project(self):
        return self.ctx.project

    def reload(self):
        self._loading = True
        p = self.project
        options = p.mesh.get("options", {})
        length = p.geometry.get("length_ref", 0.01)
        self.name.setText(options.get("name", "fluid"))
        self.size_max.setValue(options.get("size_max", length / 3))
        self.size_min.setValue(options.get("size_min", length / 30))
        self.growth.setValue(options.get("growth", 1.3))
        self.patch_sizes.clearContents()
        self.patch_sizes.setRowCount(len(p.boundaries))
        self.patch_inputs = {}
        for r, b in enumerate(p.boundaries):
            name = b["patch"]
            self.patch_sizes.setItem(r, 0, readonly_item(name))
            w = FloatField(options.get("patch_sizes", {}).get(name), allow_empty=True, positive=True)
            w.textChanged.connect(self._edited)
            self.patch_sizes.setCellWidget(r, 1, w)
            self.patch_inputs[name] = w
        self.result = None
        self._mesh = None
        report = p.mesh.get("quality_path")
        if report and p.asset(report).is_file():
            try:
                self.result = json.loads(p.asset(report).read_text(encoding="utf-8"))
                self.present(self.result)
            except (ValueError, OSError, KeyError):
                self.result = None
        if self.result is None:
            self.view.clear()
            self.areas.setRowCount(0)
            self.quality.setText("生成後にセル数・非直交角・歪み・体積比を表示します")
            self.memory.clear()
            if p.geometry.get("source_kind") == "existing_mesh":
                self.present(dict(cells=p.mesh["cells"]))
        self._loading = False
        self.refresh()

    def collect(self):
        options = dict(name=self.name.text().strip(), size_max=self.size_max.value(),
                       size_min=self.size_min.value(), growth=self.growth.value(),
                       patch_sizes={k: w.value() for k, w in self.patch_inputs.items() if w.text().strip()})
        validate_options(options, self.project.boundaries)
        return options

    def _edited(self, *_):
        if not self._loading:
            self.changed.emit()
            self.project.mesh.pop("validated", None)
            try:
                self.project.mesh["options"] = self.collect()
            except ValueError:
                pass
            self.refresh()

    def refresh(self):
        if self.project.geometry.get("source_kind") == "existing_mesh":
            self.options_body.setEnabled(False)
            self.patch_sizes.setEnabled(False)
            self.generate_btn.setText("既存メッシュを確認して使う")
            self.generate_btn.setEnabled(not self.project.blocked_reason("mesh"))
            self.note.set_status("info", "既存メッシュから。CAD がないため再生成しません。パッチの名前と割り当ては固定です")
            return
        self.options_body.setEnabled(not self.busy)
        self.patch_sizes.setEnabled(not self.busy)
        self.generate_btn.setText("メッシュを生成")
        try:
            self.collect()
            reason = self.project.blocked_reason("mesh")
            if reason:
                raise ValueError(reason)
            self.generate_btn.setEnabled(not self.busy)
            self.note.set_status("info", "生成中…停止できます" if self.busy else "寸法を確認して生成してください")
        except (ValueError, TypeError) as exc:
            self.generate_btn.setEnabled(False)
            self.note.set_status("error", str(exc))

    def generate(self):
        if self.busy:
            return False
        if self.project.geometry.get("source_kind") == "existing_mesh":
            if self.project.blocked_reason("mesh"):
                return False
            try:
                from ...pmesh import read_header
                cells = read_header(self.project.asset(self.project.mesh["path"]))["n_cells"]
                memory = mesh_memory(self.project, cells)
                if not memory["fits"]:
                    raise ValueError("VRAM の予算を超えています")
                result = dict(cells=cells)
                self.project.mesh.update(result)
                self.ready.emit(result)
                self.note.set_status("ok", f"既存メッシュの確認が済みました: {cells:,} セル")
                return True
            except (OSError, ValueError) as exc:
                self.note.set_status("error", str(exc))
                return False
        try:
            options = self.collect()
            if self.project.blocked_reason("mesh"):
                raise ValueError(self.project.blocked_reason("mesh"))
        except ValueError as exc:
            self.note.set_status("error", str(exc))
            return False
        self.project.mesh["options"] = options
        self.changed.emit()
        self.project.mesh.pop("validated", None)
        self.busy = True
        self.busy_changed.emit(True)
        self.cancel = threading.Event()
        self.options_body.setEnabled(False)
        self.patch_sizes.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.refresh()

        def done(result):
            self._idle()
            self.project.mesh.update(result)
            self.project.settings.estimated_cells = result["cells"]
            self.present(result)
            self.ready.emit(result)

        def failed(message, _trace):
            self._idle()
            self.note.set_status("warning" if self.cancel.is_set() else "error", message)
            self.status.emit(message)

        self.task = start_task(self, generate_mesh, copy.deepcopy(self.project), options, self.cancel,
                               on_done=done, on_error=failed)
        return True

    def _idle(self):
        self.busy = False
        self.busy_changed.emit(False)
        self.options_body.setEnabled(True)
        self.patch_sizes.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.refresh()

    def stop(self):
        if self.busy:
            self.cancel.set()
            self.stop_btn.setEnabled(False)
            self.note.set_status("warning", "停止しています…")

    def present(self, result):
        self.result = result
        if self.project.geometry.get("source_kind") == "existing_mesh":
            self.quality.set_status("ok", f"既存の六面体メッシュ: {result['cells']:,} セル")
            memory = mesh_memory(self.project, result["cells"])
            self.memory.set_status("ok" if memory["fits"] else "warning",
                f"VRAM 概算: {memory['total'] / 2**30:.3f} GiB / 予算 {memory['budget'] / 2**30:.3f} GiB")
            self.draw()
            return
        self.quality.set_status("ok", f"セル数: {result['cells']:,} / 非直交角 最大: {result['non_orthogonality']['max']:.4g}°"
            f" / 歪み 最大: {result['skewness']['max']:.4g} / 隣接体積比 最大: {result['volume_ratio']['max']:.4g}")
        self.areas.setRowCount(len(result["patches"]))
        for r, (name, p) in enumerate(result["patches"].items()):
            for c, value in enumerate((name, f"{p['area']:.7g}", f"{p['geometry_area']:.7g}",
                                        f"{p['area_difference']:+.4g}", f"{p['relative_difference']:+.3%}")):
                self.areas.setItem(r, c, readonly_item(value))
        memory = mesh_memory(self.project, result["cells"])
        self.memory.set_status("ok" if memory["fits"] else "warning",
            f"VRAM 概算(実セル数): {memory['total'] / 2**30:.3f} GiB / 予算 {memory['budget'] / 2**30:.3f} GiB"
            f" / 速度チャンク {memory['chunk']}。" + ("実行へ進めます。" if memory["fits"] else
            "予算超過のため実行へ進めません。セル数または速度点を減らしてください。"))
        if not memory["fits"]:
            self.note.set_status("warning", "品質の検査は済みましたが、VRAM の予算を超えています")
        elif self.project.mesh.get("validated") is not True:
            self.note.set_status("warning", "入力が変更されています。実行前に再生成してください")
        else:
            self.note.set_status("ok", "メッシュの生成と検査が済みました。case.toml を保存しました")
        self.draw()

    def draw(self):
        if not self.result:
            return
        path = self.project.asset(self.project.mesh.get("path", "mesh/fluid.pmesh"))
        if not path.is_file():
            return
        if self._mesh is None or self._mesh[0] != path or self._mesh[1] != path.stat().st_mtime_ns:
            import pyvista as pv
            from ...pmesh import read_pmesh
            pm = read_pmesh(path)
            polys = []
            for patch in pm.patches:
                ids = range(patch.start, patch.stop)
                cells = np.concatenate([np.r_[pm.face_offsets[f + 1] - pm.face_offsets[f],
                    pm.face_nodes[pm.face_offsets[f]:pm.face_offsets[f + 1]]] for f in ids])
                polys.append((patch.name, pv.PolyData(pm.points, cells)))
            self._mesh = path, path.stat().st_mtime_ns, polys
        colors = theme.series(self.ctx.theme.scheme())
        items, legend = [], []
        for i, (name, mesh) in enumerate(self._mesh[2]):
            color = self.ctx.theme.tokens()["text_muted"] if name == "default_wall" else colors[i % len(colors)]
            items.append(SceneItem(mesh, color=color, show_edges=True, edge_color=self.ctx.theme.tokens()["border"]))
            legend.append((name, color))
        self.view.restyle(self.ctx.theme.scheme())
        self.view.set_scene(Scene(items=items, legend=legend, caption="四面体メッシュの境界 [m]"))

    def shutdown(self):
        if self.busy:
            self.cancel.set()
            self.task.wait()
        self.view.close()
