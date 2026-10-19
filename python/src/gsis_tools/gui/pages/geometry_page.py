"""CAD と AI のタブを持つ形状画面。受理した流体領域は両方とも同じ検査を通す。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSplitter, QTableWidget, QTabWidget, QVBoxLayout, QWidget)

from ..ai.surface import build_polydata, face_label_points
from ..geometry import GeometryResult, prepare_geometry
from ..widgets.common import StatusLabel, heading, muted, primary_button, readonly_item
from ..widgets.vtk_view import Scene, SceneItem, VtkView
from ..workers import start_task
from .ai_page import AiPage


class GeometryPage(QWidget):
    status = Signal(str)
    changed = Signal()
    ready = Signal(object)

    def __init__(self, ctx, directory, parent=None, view_backend=None):
        super().__init__(parent)
        self.ctx = ctx
        self.directory = directory
        self.result = None
        self.busy = False
        self._loading = False
        root = QVBoxLayout(self)
        root.addWidget(heading("形状"))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        cad = QWidget()
        lay = QVBoxLayout(cad)
        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText("STEP・IGES・BREP を読み込んで流体領域を決めます")
        browse = QPushButton("CAD を選ぶ…")
        row.addWidget(self.source, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("座標の単位"))
        self.unit = QComboBox()
        self.unit.addItems(["m", "cm", "mm", "um"])
        self.unit.setCurrentText("mm")
        row.addWidget(self.unit)
        self.flow = QComboBox()
        self.flow.addItem("内部流: 立体を流体領域にする", "internal")
        self.flow.addItem("外部流: 箱から立体を引く", "external")
        row.addWidget(self.flow)
        row.addWidget(QLabel("箱 / 外接箱"))
        self.factor = QDoubleSpinBox()
        self.factor.setRange(1.01, 1000)
        self.factor.setValue(3)
        row.addWidget(self.factor)
        self.apply_btn = primary_button("読み込み・領域を検査")
        row.addWidget(self.apply_btn)
        lay.addLayout(row)
        lay.addWidget(muted("STEP・IGES はファイル内の単位を OCC が変換します。BREP は選んだ単位で解釈します。表示と一覧は SI です。"))
        self.note = StatusLabel("閉じた立体を 1 つ読み込んでください")
        lay.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Vertical)
        self.view = VtkView(backend=view_backend)
        split.addWidget(self.view)
        self.faces = QTableWidget(0, 4)
        self.faces.setHorizontalHeaderLabels(["面の番号", "面積 [m²]", "重心 [m]", "外向き法線(面上の代表点)"])
        self.faces.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.faces.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        split.addWidget(self.faces)
        split.setSizes([500, 240])
        lay.addWidget(split, 1)
        self.tabs.addTab(cad, "CAD / 流体領域")
        ai_tab = QWidget()
        ai_lay = QVBoxLayout(ai_tab)
        row = QHBoxLayout()
        row.addWidget(muted("AI で作った立体も、流体領域として取り込んで次の設定へ進めます。"), 1)
        self.adopt_btn = primary_button("AI の形状を流体領域に使う")
        row.addWidget(self.adopt_btn)
        ai_lay.addLayout(row)
        self.ai_page = AiPage(ctx, view_backend=view_backend)
        ai_lay.addWidget(self.ai_page)
        self.tabs.addTab(ai_tab, "モデリング(AI)")
        browse.clicked.connect(self._browse)
        self.apply_btn.clicked.connect(lambda: self.load_cad(Path(self.source.text())))
        self.adopt_btn.clicked.connect(self._adopt_ai)
        self.ai_page.geometry_exported.connect(self._adopt_export)
        self.ai_page.status.connect(self.status)
        self.unit.currentTextChanged.connect(self._edited)
        self.flow.currentIndexChanged.connect(self._edited)
        self.factor.valueChanged.connect(self._edited)
        self.faces.itemSelectionChanged.connect(self._draw)
        ctx.theme.changed.connect(lambda _: self._draw())

    def _edited(self, *_):
        self.factor.setEnabled(self.flow.currentData() == "external")
        if not self._loading and self.result is not None:
            self.changed.emit()
            self.note.set_status("warning", "領域の入力を変更しました。「読み込み・領域を検査」で確定してください。")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "CAD を読み込む", "", "CAD (*.step *.stp *.iges *.igs *.brep *.brp)")
        if path:
            self.load_cad(Path(path))

    def load_cad(self, path: Path, *, source_kind="cad", ai_files=None, ai_imports=None) -> bool:
        if self.busy:
            return False
        if not path.is_file():
            self.note.set_status("error", "CAD のファイルを選んでください")
            return False
        self.source.setText(str(path))
        self.changed.emit()
        self.busy = True
        self.apply_btn.setEnabled(False)
        self.note.set_status("info", "CAD を読み込み、流体領域を検査しています…")

        def done(result):
            self.busy = False
            self.apply_btn.setEnabled(True)
            result.metadata["source_kind"] = source_kind
            if ai_files:
                import shutil
                for key, src in ai_files.items():
                    dst = self.directory() / "geometry" / Path(src).name
                    shutil.copy2(src, dst)
                    result.metadata[key] = dst.relative_to(self.directory()).as_posix()
                for rel, src in (ai_imports or {}).items():
                    dst = (self.directory() / rel).resolve()
                    if not dst.is_relative_to(self.directory().resolve()):
                        raise ValueError("AI の CAD の保存先がプロジェクトの外です")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            self.present(result)
            self.ready.emit(result)

        def failed(message, _trace):
            self.busy = False
            self.apply_btn.setEnabled(True)
            self.note.set_status("error", message)
            self.status.emit(message)

        self.task = start_task(self, prepare_geometry, path, self.directory(), unit=self.unit.currentText(),
                               flow=self.flow.currentData(), box_factor=self.factor.value(), on_done=done, on_error=failed)
        return True

    def present(self, result: GeometryResult):
        self.result = result
        m = result.metadata
        self._loading = True
        self.unit.setCurrentText(m["unit"])
        self.flow.setCurrentIndex(self.flow.findData(m["flow"]))
        self.factor.setValue(m["box_factor"])
        self._loading = False
        self.factor.setEnabled(m["flow"] == "external")
        self.faces.setRowCount(len(m["faces"]))
        self.faces.blockSignals(True)
        for r, f in enumerate(m["faces"]):
            values = [str(f["id"]), f"{f['area']:.7g}", ", ".join(f"{v:.6g}" for v in f["centroid"]),
                      "判定できません" if f.get("normal") is None else ", ".join(f"{v:.5g}" for v in f["normal"])]
            for c, value in enumerate(values):
                self.faces.setItem(r, c, readonly_item(value))
        self.faces.blockSignals(False)
        warnings = " / ".join(m["warnings"])
        self.note.set_status("warning" if warnings else "ok",
                             f"閉じた立体: 1 / 体積: {m['volume']:.7g} m³ / 外接箱の最小辺: {m['length_ref']:.7g} m"
                             + (" / " + warnings if warnings else ""))
        self._draw()

    def _draw(self):
        if self.result is None:
            return
        t = self.ctx.theme.tokens()
        s = self.result.surface
        data = build_polydata(s, {})
        face_color = t["surface_2"] if self.ctx.theme.scheme() == "light" else t["text_muted"]
        items = [SceneItem(m, color=face_color, show_edges=False) for k in ("boundary", "internal")
                 if (m := data[k]) is not None]
        if data["lines"] is not None:
            items.append(SceneItem(data["lines"], color=t["text"], line_width=1.5))
        selected = [int(self.faces.item(i.row(), 0).text()) for i in self.faces.selectionModel().selectedRows()]
        if selected and data["boundary"] is not None:
            import numpy as np
            b = data["boundary"]
            picked = b.extract_cells(np.isin(b.cell_data["face_id"], selected))
            if picked.n_cells:
                items.append(SceneItem(picked, color=t["accent"], lighting=False))
        pts, labels = face_label_points(s)
        self.view.restyle(self.ctx.theme.scheme())
        self.view.set_scene(Scene(items=items, labels=list(zip(map(tuple, pts), labels)), caption="流体領域 [m]"))

    def _adopt_ai(self):
        if not self.busy and not self.ai_page.busy:
            self.ai_page.ensure_backend().export_project_geometry()

    def _adopt_export(self, payload):
        self._loading = True
        self.unit.setCurrentText(payload["unit"])
        self._loading = False
        self.tabs.setCurrentIndex(0)
        self.load_cad(Path(payload["path"]), source_kind="ai",
                      ai_files={"ai_state": payload["state"], "ai_conversation": payload["conversation"]},
                      ai_imports=payload.get("imports"))

    def shutdown(self):
        if self.busy:
            self.task.wait()
        self.ai_page.shutdown()
        self.view.close()
