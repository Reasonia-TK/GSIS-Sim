"""モデリング(AI)の画面: 自然言語の指示で、AI が gmsh の MCP ツールを使って計算領域を作り、境界パッチを付け、
``.pmesh`` まで書き出す。その様子は 3D 表示にその場で出る。

- 左: 会話の欄(AI の文章、ツールの呼び出しの記録は折りたためる)、指示の入力、停止・再送、トークン数
- 右: 3D 表示(ツールで形状・パッチ・メッシュが変わるたびに更新。面はパッチごとに色分け、付いていない面は灰色)、
  パッチの一覧、面の一覧(面を選んで会話に差し込む・手でパッチを付ける)、ツールを手で実行する欄、ログ
- 上: 元に戻す、状態の保存・読み込み、会話の保存・読み込み・リセット、設定

裏側(MCP サーバー・AI の反復)は :class:`~gsis_tools.gui.ai.backend.AiBackend`(別スレッドの asyncio)。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..ai.backend import AiBackend
from ..ai.secrets import KeyResolver, KeyStore, Secret, install_log_redaction, redact
from ..ai.settings import KIND_LABELS, AiSettings
from ..ai.surface import SurfaceData, build_polydata, face_label_points
from ..context import AppContext
from ..paths import display_path
from ..widgets.chat_view import ChatView, pretty_json
from ..widgets.common import LogView, StatusLabel, heading, muted, primary_button, readonly_item, swatch
from ..widgets.vtk_view import Scene, SceneItem, VtkView

logger = logging.getLogger("gsis_tools.gui.ai")

PATCH_KINDS = ("", "wall", "inlet", "outlet", "symmetry", "patch")
SECTIONS = {"none": "全体", "y": "断面(手前 y < 中心 を除く)", "z": "断面(上 z > 中心 を除く)",
            "x": "断面(x < 中心 を除く)"}
# 断面で残す側(clip の法線の向きの側を残す)。斜めの視点のカメラは −x・−y・+z の側にあるので、その側を除く
SECTION_KEEP = {"y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, -1.0), "x": (1.0, 0.0, 0.0)}


def pick_face_at(plotter: Any, x: float, y: float) -> int | None:
    """3D 表示の画面の位置 (x, y)(VTK の表示座標、左下が原点)にある面の番号(セルの値 face_id)。なければ None。
    線・文字などの選べない描画(pickable でないもの)は選ばない。"""
    import vtk

    picker = vtk.vtkCellPicker()
    picker.SetTolerance(0.0005)
    if not picker.Pick(float(x), float(y), 0.0, plotter.renderer):
        return None
    cid = picker.GetCellId()
    ds = picker.GetDataSet()
    if cid < 0 or ds is None:
        return None
    arr = ds.GetCellData().GetArray("face_id")
    if arr is None:
        return None
    return int(arr.GetValue(cid))


class _Bridge(QObject):
    event = Signal(str, object)


class AiPage(QWidget):
    """モデリング(AI)の画面。"""

    status = Signal(str)
    open_mesh = Signal(str)  # 書き出した .pmesh をメッシュの画面で開く
    geometry_exported = Signal(object)  # G1 の共通の形状検査へ渡す

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, view_backend: str | None = None,
                 resolver: KeyResolver | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self.settings = AiSettings.load(ctx.settings)
        self.resolver = resolver or KeyResolver(KeyStore())
        self.http_client_factory: Any = None  # 試験・模擬の AI で差し替える(httpx2 の AsyncClient を返す関数)
        self.connection_factory: Any = None  # 試験で MCP サーバーの起動を差し替える
        self.backend: AiBackend | None = None
        self.surface: SurfaceData | None = None
        self.selected_faces: list[int] = []
        self.last_pmesh: str | None = None
        self.busy = False
        self.server_state = "stopped"
        self.key_label = ""
        self.key_missing = False
        self.last_status = ""
        self.done_count = 0  # AI の指示の処理が終わった回数(試験で待つのに使う)
        self._delta: list[tuple[str, str]] = []
        self._log_filter = install_log_redaction()
        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_event)
        self._delta_timer = QTimer(self)
        self._delta_timer.setInterval(60)
        self._delta_timer.timeout.connect(self._flush_delta)
        self._build(view_backend)
        self.ctx.theme.changed.connect(self._restyle)
        self._restyle(self.ctx.theme.scheme())
        self._update_header()
        self._update_buttons()

    # ------------------------------------------------------------------ 画面
    def _build(self, view_backend: str | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(heading("モデリング(AI)"))
        top.addSpacing(10)
        self.header = StatusLabel("")
        self.header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.header, 1)
        self.undo_btn = QPushButton("元に戻す")
        self.undo_btn.setToolTip("最後の手順を取り消す(MCP の undo。batch はまとめて 1 手順)")
        self.save_state_btn = QPushButton("状態の保存…")
        self.load_state_btn = QPushButton("状態の読み込み…")
        self.settings_btn = QPushButton("設定…")
        for b in (self.undo_btn, self.save_state_btn, self.load_state_btn, self.settings_btn):
            top.addWidget(b)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # 左: 会話
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 8, 0)
        lv.setSpacing(6)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("会話"))
        crow.addStretch(1)
        self.save_conv_btn = QPushButton("会話の保存…")
        self.load_conv_btn = QPushButton("会話を開く…")
        self.reset_btn = QPushButton("会話のリセット")
        self.reset_btn.setToolTip("会話を消して新しく始める(モデルの状態はそのまま)")
        for b in (self.save_conv_btn, self.load_conv_btn, self.reset_btn):
            crow.addWidget(b)
        lv.addLayout(crow)
        self.chat = ChatView()
        lv.addWidget(self.chat, 1)
        self.state_label = StatusLabel("")
        lv.addWidget(self.state_label)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("AI への指示(例: 半径 1 mm・長さ 10 mm の管の両側に槽を付けた計算領域を作って、"
                                      "入口・出口・壁のパッチを付け、.pmesh に書き出して)。Ctrl+Enter で送る")
        self.input.setMaximumHeight(110)
        lv.addWidget(self.input)
        brow = QHBoxLayout()
        self.send_btn = primary_button("送る")
        self.send_btn.setToolTip("Ctrl+Enter")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setToolTip("AI の応答待ちはすぐ止める。ツールの実行中は 2 秒待ち、終わらなければ MCP サーバーを"
                                 "止めて直前の状態(自動保存)に戻す")
        self.resend_btn = QPushButton("再送")
        self.resend_btn.setToolTip("API のエラーの後に、同じ会話をもう一度送る")
        self.insert_faces_btn = QPushButton("選んだ面を差し込む")
        self.insert_faces_btn.setToolTip("右の 3D 表示か面の一覧で選んだ面の番号を、指示の欄に差し込む")
        for b in (self.send_btn, self.stop_btn, self.resend_btn, self.insert_faces_btn):
            brow.addWidget(b)
        brow.addStretch(1)
        lv.addLayout(brow)
        self.tokens_label = muted("")
        lv.addWidget(self.tokens_label)
        split.addWidget(left)

        # 右: 3D とパッチなど
        right = QSplitter(Qt.Orientation.Vertical)
        vh = QWidget()
        vv = QVBoxLayout(vh)
        vv.setContentsMargins(8, 0, 0, 0)
        vbar = QHBoxLayout()
        vbar.addWidget(QLabel("表示"))
        self.section_combo = QComboBox()
        for k, label in SECTIONS.items():
            self.section_combo.addItem(label, k)
        vbar.addWidget(self.section_combo)
        self.edges_check = QCheckBox("メッシュの辺")
        self.edges_check.setChecked(True)
        self.labels_check = QCheckBox("面の番号")
        self.pick_check = QCheckBox("面を選ぶ(クリック)")
        self.pick_check.setToolTip("3D 表示で面をクリックして選ぶ(対話型の 3D 表示のときだけ)。Ctrl を押すと足す")
        for w in (self.edges_check, self.labels_check, self.pick_check):
            vbar.addWidget(w)
        vbar.addStretch(1)
        vbar.addWidget(QLabel("視点"))
        for key, label in (("focus", "斜め"), ("xy", "上から"), ("xz", "横から")):  # focus: 斜めから形状に合わせて拡大
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, k=key: self.view.set_view(k))
            vbar.addWidget(b)
        vv.addLayout(vbar)
        self.view = VtkView(backend=view_backend)
        vv.addWidget(self.view, 1)
        right.addWidget(vh)

        self.tabs = QTabWidget()
        # パッチ
        self.patch_table = QTableWidget(0, 5)
        self.patch_table.setHorizontalHeaderLabels(["", "パッチ", "kind", "面の数・番号", "面積"])
        # 面
        fw = QWidget()
        fl = QVBoxLayout(fw)
        fl.setContentsMargins(4, 4, 4, 4)
        self.faces_table = QTableWidget(0, 6)
        self.faces_table.setHorizontalHeaderLabels(["面", "種類", "パッチ", "面積", "重心", "境界"])
        self.faces_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        fl.addWidget(self.faces_table, 1)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("選んだ面"))
        self.faces_edit = QLineEdit()
        self.faces_edit.setPlaceholderText("面の番号(カンマ区切り)")
        frow.addWidget(self.faces_edit, 1)
        frow.addWidget(QLabel("パッチ"))
        self.patch_name = QComboBox()
        self.patch_name.setEditable(True)
        self.patch_name.setMinimumWidth(110)
        self.patch_name.lineEdit().setPlaceholderText("名前")
        frow.addWidget(self.patch_name)
        self.patch_kind = QComboBox()
        for k in PATCH_KINDS:
            self.patch_kind.addItem(k or "(名前から推定)", k)
        frow.addWidget(self.patch_kind)
        self.assign_btn = QPushButton("パッチを付ける")
        self.assign_btn.setToolTip("選んだ面にパッチを付ける(別のパッチに入っていれば移す)")
        frow.addWidget(self.assign_btn)
        fl.addLayout(frow)
        # ツールを手で実行
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(4, 4, 4, 4)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("ツール"))
        self.tool_combo = QComboBox()
        self.tool_combo.setMinimumWidth(200)
        trow.addWidget(self.tool_combo)
        self.tool_run_btn = QPushButton("実行")
        trow.addWidget(self.tool_run_btn)
        trow.addStretch(1)
        tl.addLayout(trow)
        self.tool_desc = muted("")
        tl.addWidget(self.tool_desc)
        self.tool_args = QPlainTextEdit("{}")
        self.tool_args.setMaximumHeight(110)
        tl.addWidget(self.tool_args)
        self.tool_result = QPlainTextEdit()
        self.tool_result.setReadOnly(True)
        tl.addWidget(self.tool_result, 1)
        # ログ
        self.log = LogView(max_lines=5000)
        self.tabs.addTab(self.patch_table, "パッチ")
        self.tabs.addTab(fw, "面")
        self.tabs.addTab(tw, "ツールを手で実行")
        self.tabs.addTab(self.log, "ログ")
        for t in (self.patch_table, self.faces_table):
            t.verticalHeader().setVisible(False)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            t.horizontalHeader().setStretchLastSection(True)
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        right.addWidget(self.tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        split.addWidget(right)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        split.setSizes([640, 860])
        self.open_pmesh_btn = QPushButton("書き出したメッシュを開く")
        self.open_pmesh_btn.setToolTip("最後に書き出した .pmesh をメッシュの画面で開く")
        vbar.insertWidget(vbar.count(), self.open_pmesh_btn)

        # つなぐ
        self.send_btn.clicked.connect(self.send_input)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):  # 指示の欄にいるときだけ
            QShortcut(QKeySequence(seq), self.input, activated=self.send_input,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.stop_btn.clicked.connect(self.stop)
        self.resend_btn.clicked.connect(self.resend)
        self.chat.resend_requested.connect(self.resend)
        self.chat.open_mesh.connect(self.open_mesh)
        self.open_pmesh_btn.clicked.connect(lambda: self.last_pmesh and self.open_mesh.emit(self.last_pmesh))
        self.insert_faces_btn.clicked.connect(self.insert_selected_faces)
        self.undo_btn.clicked.connect(self.undo)
        self.save_state_btn.clicked.connect(self._save_state_dialog)
        self.load_state_btn.clicked.connect(self._load_state_dialog)
        self.save_conv_btn.clicked.connect(self._save_conversation_dialog)
        self.load_conv_btn.clicked.connect(self._load_conversation_dialog)
        self.reset_btn.clicked.connect(self.reset_conversation)
        self.settings_btn.clicked.connect(self.open_settings)
        self.section_combo.currentIndexChanged.connect(lambda _i: self._show_scene(keep_camera=True))
        self.edges_check.toggled.connect(lambda _on: self._show_scene(keep_camera=True))
        self.labels_check.toggled.connect(lambda _on: self._show_scene(keep_camera=True))
        self.pick_check.toggled.connect(self._set_pick_mode)
        self.faces_table.itemSelectionChanged.connect(self._on_faces_selected)
        self.patch_table.itemSelectionChanged.connect(self._on_patch_selected)
        self.faces_edit.editingFinished.connect(self._on_faces_edited)
        self.assign_btn.clicked.connect(self.assign_patch_to_selection)
        self.tool_combo.currentIndexChanged.connect(self._on_tool_chosen)
        self.tool_run_btn.clicked.connect(self.run_manual_tool)

    # ------------------------------------------------------------------ 裏側
    def ensure_backend(self) -> AiBackend:
        if self.backend is None:
            self.backend = AiBackend(settings=self.settings, resolver=self.resolver, emit=self._bridge.event.emit,
                                     root=self.ctx.root, http_client_factory=self.http_client_factory,
                                     connection_factory=self.connection_factory)
        else:
            self.backend.http_client_factory = self.http_client_factory
        return self.backend

    def start_server(self) -> bool:
        return self.ensure_backend().start()

    def set_resolver(self, resolver: KeyResolver) -> None:
        """キーの読み込み元を差し替える(試験用)。"""
        self.resolver = resolver
        if self.backend is not None:
            self.backend.resolver = resolver

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        if self.backend is None or self.server_state in ("stopped", "error"):
            QTimer.singleShot(0, self.start_server)

    @property
    def workspace(self) -> Path:
        return self.settings.workspace_path(self.ctx.root)

    # ------------------------------------------------------------------ 操作
    def send_input(self) -> bool:
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return False
        b = self.ensure_backend()
        b.http_client_factory = self.http_client_factory
        if b.send(text):
            self.input.clear()
            return True
        return False

    def send_text(self, text: str) -> bool:
        self.input.setPlainText(text)
        return self.send_input()

    def stop(self) -> None:
        if self.backend is not None:
            self.backend.stop()

    def resend(self) -> bool:
        if self.backend is None or self.busy:
            return False
        return self.backend.resend()

    def undo(self) -> bool:
        return self.ensure_backend().undo()

    def reset_conversation(self) -> bool:
        if self.backend is None:
            self.chat.clear()
            return True
        return self.backend.reset_conversation()

    def _save_state_dialog(self) -> None:
        ws = self.workspace
        p, _ = QFileDialog.getSaveFileName(self, "状態の保存(作業フォルダの中)", str(ws / "state.json"),
                                           "状態 (*.json)")
        if p:
            self.ensure_backend().save_state(p)

    def _load_state_dialog(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "状態の読み込み", str(self.workspace), "状態 (*.json)")
        if p:
            self.ensure_backend().load_state(p)

    def _save_conversation_dialog(self) -> None:
        if self.backend is None or self.backend.conv.empty:
            self.status.emit("保存する会話がありません")
            return
        d = self.workspace / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        p, _ = QFileDialog.getSaveFileName(self, "会話の保存", str(d / "conversation.json"), "会話 (*.json)")
        if p:
            self.save_conversation(Path(p))

    def save_conversation(self, path: Path) -> Path | None:
        if self.backend is None:
            return None
        try:
            out = self.backend.save_conversation(path)
        except OSError as exc:
            QMessageBox.warning(self, "保存できません", redact(exc))
            return None
        self.status.emit(f"会話を保存しました: {display_path(out, self.ctx.root)}")
        return out

    def _load_conversation_dialog(self) -> None:
        d = self.workspace / "conversations"
        p, _ = QFileDialog.getOpenFileName(self, "会話を開く", str(d if d.is_dir() else self.workspace),
                                           "会話 (*.json)")
        if p:
            self.ensure_backend().load_conversation(p)

    def open_settings(self) -> bool:
        if self.busy:
            return False
        dlg = self.make_settings_dialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        return self.apply_settings_dialog(dlg)

    def make_settings_dialog(self):
        from .ai_settings_dialog import AiSettingsDialog

        default_ws = str(AiSettings().workspace_path(self.ctx.root))
        conv_active = self.backend is not None and not self.backend.conv.empty
        return AiSettingsDialog(self.settings, self.resolver, workspace_default=default_ws,
                                fetch_models=self._fetch_models, conversation_active=conv_active, parent=self)

    def apply_settings_dialog(self, dlg) -> bool:
        new = dlg.result_settings
        for m in dlg.apply_keys():
            self.chat.add_note("info", m)
        if (self.backend is not None and not self.backend.conv.empty
                and new.provider_kind() != self.backend.conv.format):
            r = QMessageBox.question(self, "会話をリセットしますか",
                                     "形式(OpenAI の API の種類を含む)が変わったので、いまの会話は続けられません。"
                                     "会話をリセットしますか?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.Yes)
            if r == QMessageBox.StandardButton.Yes:
                self.backend.reset_conversation()
        self.settings = new
        self.settings.save(self.ctx.settings)
        if self.backend is not None:
            self.backend.update_settings(new)
        self._update_header()
        self.status.emit("AI の設定を保存しました(API キーは設定ファイルに書きません)")
        return True

    def _fetch_models(self, settings: AiSettings, typed: Secret | None):
        b = self.ensure_backend()
        b.http_client_factory = self.http_client_factory
        resolver = self.resolver
        if typed is not None:
            resolver = KeyResolver(self.resolver.store, self.resolver._environ)
            resolver._session = dict(self.resolver._session)
            resolver.set_session(settings.format, settings.base_url, typed)
        return b.list_models(settings, resolver=resolver)

    # ------------------------------------------------------------------ 面とパッチ
    def insert_selected_faces(self) -> None:
        if not self.selected_faces:
            return
        txt = "面 " + ", ".join(str(f) for f in self.selected_faces)
        cur = self.input.toPlainText()
        self.input.setPlainText((cur + (" " if cur and not cur.endswith((" ", "\n")) else "") + txt) if cur else txt)
        self.input.setFocus()

    def select_faces(self, faces: list[int], *, add: bool = False) -> None:
        faces = [int(f) for f in faces]
        if add:
            faces = list(dict.fromkeys([*self.selected_faces, *faces]))
        self.selected_faces = faces
        self.faces_edit.setText(", ".join(str(f) for f in faces))
        self.faces_table.blockSignals(True)
        self.faces_table.clearSelection()
        for r in range(self.faces_table.rowCount()):
            it = self.faces_table.item(r, 0)
            if it is not None and int(it.text()) in faces:
                for c in range(self.faces_table.columnCount()):
                    item = self.faces_table.item(r, c)
                    if item is not None:
                        item.setSelected(True)
        self.faces_table.blockSignals(False)
        self._show_scene(keep_camera=True)
        self._update_buttons()

    def pick_face(self, face_id: int, add: bool = False) -> None:
        """3D 表示で面を選んだ(クリック。試験からも呼ぶ)。"""
        self.select_faces([face_id], add=add)
        f = self.surface.faces.get(int(face_id), {}) if self.surface else {}
        patch = (self.surface.patch_of().get(int(face_id)) if self.surface else None) or "(付いていない)"
        self.status.emit(f"面 {face_id}: {f.get('type', '?')}、面積 {f.get('area', 0):.6g}、パッチ {patch}")

    def _on_faces_selected(self) -> None:
        rows = sorted({i.row() for i in self.faces_table.selectedIndexes()})
        faces = []
        for r in rows:
            it = self.faces_table.item(r, 0)
            if it is not None:
                faces.append(int(it.text()))
        self.selected_faces = faces
        self.faces_edit.setText(", ".join(str(f) for f in faces))
        self._show_scene(keep_camera=True)
        self._update_buttons()

    def _on_patch_selected(self) -> None:
        rows = sorted({i.row() for i in self.patch_table.selectedIndexes()})
        if not rows or self.surface is None:
            return
        name_item = self.patch_table.item(rows[0], 1)
        if name_item is None:
            return
        name = name_item.text()
        faces = self.surface.unassigned_faces() if name.startswith("(") else next(
            (p["faces"] for p in self.surface.patches if p["name"] == name), [])
        if not name.startswith("("):
            self.patch_name.setEditText(name)
        self.select_faces(list(faces))

    def _on_faces_edited(self) -> None:
        txt = self.faces_edit.text().replace("、", ",")
        faces = []
        for part in txt.split(","):
            part = part.strip()
            if part.isdigit():
                faces.append(int(part))
        if faces != self.selected_faces:
            self.select_faces(faces)

    def assign_patch_to_selection(self) -> bool:
        self._on_faces_edited()
        name = self.patch_name.currentText().strip()
        if not self.selected_faces or not name:
            self.status.emit("面とパッチ名を選んでください")
            return False
        kind = self.patch_kind.currentData() or None
        return self.ensure_backend().assign_patch(self.selected_faces, name, kind)

    def _set_pick_mode(self, on: bool) -> None:
        if on and not self.view.supports_plane_widget:
            self.pick_check.blockSignals(True)
            self.pick_check.setChecked(False)
            self.pick_check.blockSignals(False)
            self.status.emit("面のクリックでの選択は、対話型の 3D 表示のときだけ使えます(面の一覧で選んでください)")
            return
        self._apply_picking()

    def _apply_picking(self) -> None:
        """「面を選ぶ」: 左のクリック(押して離すまでに 4 px 以内しか動かさない)で面を選ぶ。ドラッグ(回転)では選ばない。"""
        p = self.view.plotter
        if p is None or not self.view.supports_plane_widget:
            return
        iren = getattr(p, "iren", None)
        if iren is None:  # pragma: no cover
            return
        for oid in getattr(self, "_pick_observers", []):
            try:
                iren.remove_observer(oid)
            except Exception:
                pass
        self._pick_observers = []
        if not self.pick_check.isChecked():
            return
        state: dict[str, Any] = {"press": None}

        def on_press(*_a) -> None:
            state["press"] = tuple(iren.interactor.GetEventPosition())

        def on_release(*_a) -> None:
            press, state["press"] = state["press"], None
            pos = tuple(iren.interactor.GetEventPosition())
            if press is None or abs(pos[0] - press[0]) + abs(pos[1] - press[1]) > 4:
                return
            fid = pick_face_at(p, pos[0], pos[1])
            if fid is not None:
                from PySide6.QtWidgets import QApplication

                add = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
                self.pick_face(fid, add=add)

        try:
            self._pick_observers = [iren.add_observer("LeftButtonPressEvent", on_press),
                                    iren.add_observer("LeftButtonReleaseEvent", on_release)]
        except Exception as exc:  # pragma: no cover - VTK の版による
            self.status.emit(f"面の選択を始められませんでした: {exc}")

    # ------------------------------------------------------------------ ツールを手で実行
    def _fill_tool_combo(self, tools: list[dict[str, Any]]) -> None:
        """「ツールを手で実行」の一覧(GUI 専用のツールは除いたもの)。"""
        cur = self.tool_combo.currentText()
        self.tool_combo.blockSignals(True)
        self.tool_combo.clear()
        self._tool_info = {t["name"]: t for t in tools}
        for t in tools:
            self.tool_combo.addItem(t["name"])
        if cur:
            self.tool_combo.setCurrentText(cur)
        self.tool_combo.blockSignals(False)
        self._on_tool_chosen()

    def _on_tool_chosen(self, *_a) -> None:
        t = getattr(self, "_tool_info", {}).get(self.tool_combo.currentText())
        if t is None:
            self.tool_desc.setText("")
            return
        self.tool_desc.setText(t.get("description") or "")
        schema = t.get("input_schema") or {}
        req = schema.get("required") or []
        props = schema.get("properties") or {}
        skel = {}
        for k in req:
            typ = (props.get(k) or {}).get("type")
            skel[k] = [] if typ == "array" else ({} if typ == "object" else (0 if typ in ("number", "integer") else ""))
        self.tool_args.setPlainText(json.dumps(skel, ensure_ascii=False))

    def run_manual_tool(self) -> bool:
        name = self.tool_combo.currentText()
        try:
            args = json.loads(self.tool_args.toPlainText() or "{}")
        except ValueError as exc:
            self.tool_result.setPlainText(f"引数が JSON として読めません: {exc}")
            return False
        if not isinstance(args, dict):
            self.tool_result.setPlainText("引数は JSON のオブジェクト({...})にしてください")
            return False
        return bool(name) and self.ensure_backend().run_tool(name, args)

    # ------------------------------------------------------------------ 知らせ
    def _on_event(self, kind: str, p: dict[str, Any]) -> None:
        try:
            self._handle(kind, p)
        except Exception:  # pragma: no cover
            logger.exception("画面の更新に失敗しました(%s)", kind)

    def _handle(self, kind: str, p: dict[str, Any]) -> None:
        if kind == "delta":
            self._delta.append((p.get("kind", "text"), p.get("text", "")))
            if not self._delta_timer.isActive():
                self._delta_timer.start()
            if p.get("kind") == "tool":
                self.state_label.set_status("info", f"AI がツール {p.get('text')} を呼ぼうとしています…")
            return
        self._flush_delta()
        if kind == "busy":
            self.busy = bool(p.get("busy"))
            if not self.busy:
                self.state_label.set_status(None, "")
            self._update_buttons()
        elif kind == "server":
            self.server_state = p.get("state", "")
            st = self.server_state
            if st == "starting":
                self.state_label.set_status("info", "MCP サーバー(gmsh)を起動しています…")
            elif st == "restarting":
                self.state_label.set_status("warning", "MCP サーバーを起動し直しています…" + (
                    f"({p.get('message')})" if p.get("message") else ""))
            elif st == "ready":
                self.state_label.set_status(None, "")
                self._fill_tool_combo(p.get("tool_list") or [])
            elif st == "error":
                self.state_label.set_status("error", f"MCP サーバーを起動できませんでした: {p.get('message', '')}")
            self.log.append_lines(f"MCP サーバー: {st} {redact(p.get('message', '') or '')}", stream="gui")
            self._update_header()
        elif kind == "status":
            self.state_label.set_status("info", p.get("text", ""))
        elif kind == "user":
            self.chat.add_user(p.get("text", ""), p.get("notes") or [])
        elif kind == "assistant":
            self.chat.finish_assistant(p.get("text", ""), p.get("thinking") or [], p.get("model"),
                                       bool(p.get("refused")))
            if p.get("model"):
                self._update_header(model=p.get("model"))
        elif kind == "tool_start":
            self.chat.start_tool(p.get("id"), p.get("name"), p.get("args"))
            self.state_label.set_status("info", f"ツール {p.get('name')} を実行しています…")
        elif kind == "tool_end":
            self.chat.finish_tool(p.get("id"), p.get("name"), bool(p.get("ok")), str(p.get("text") or ""),
                                  float(p.get("seconds") or 0.0), skipped=bool(p.get("skipped")),
                                  image=p.get("image"))
            self.log.append_lines(f"{p.get('name')}: {'OK' if p.get('ok') else 'エラー'}"
                                  f"({float(p.get('seconds') or 0):.2f} s)", stream="gui",
                                  kind=None if p.get("ok") else "warning")
        elif kind == "surface":
            self.surface = p.get("surface")
            self._fill_tables()
            self._show_scene(keep_camera=self.view.scene() is not None)
            self._update_header()
        elif kind == "project_geometry":
            self.geometry_exported.emit(p)
        elif kind == "usage":
            self._show_usage(p.get("last") or {}, p.get("total") or {})
        elif kind == "note":
            self.chat.add_note(p.get("kind", "info"), p.get("text", ""))
            self.log.append_lines(p.get("text", ""), stream="gui",
                                  kind={"error": "error", "warning": "warning"}.get(p.get("kind"), "info"))
        elif kind == "api_error":
            self.chat.add_note("error", p.get("text", ""), retry=bool(p.get("retryable")))
            self.log.append_lines(p.get("text", ""), stream="gui", kind="error")
        elif kind == "pmesh":
            path = str(p.get("path"))
            self.last_pmesh = path
            self.chat.add_pmesh(path, display_path(path, self.ctx.root))
            self._update_buttons()
        elif kind == "manual":
            self.chat.add_manual(p.get("name", ""), p.get("args"), bool(p.get("ok")), str(p.get("text") or ""))
            self.tool_result.setPlainText(pretty_json(p.get("text") or ""))
        elif kind == "confirm":
            self._ask_confirm(p)
        elif kind == "conversation":
            self.chat.load_records(p.get("records") or [], lambda s: display_path(s, self.ctx.root))
            self._show_usage({}, p.get("usage") or {})
            for r in p.get("records") or []:
                if r.get("role") == "pmesh":
                    self.last_pmesh = r.get("path")
            self._update_buttons()
        elif kind == "key":
            self.key_label = str(p.get("label") or "")
            self.key_missing = not bool(p.get("ok"))
            self._update_header()
        elif kind == "settings":
            tp = p.get("token_param")
            if tp:
                self.settings.current.token_param = tp
                self.settings.save(self.ctx.settings)
        elif kind == "done":
            st = p.get("status")
            self.last_status = str(st)
            self.done_count += 1
            msg = {"done": "AI の作業が終わりました", "stopped": "止めました", "limit": "ツール呼び出しの上限で止めました",
                   "error": "API のエラーで止まりました", "refusal": "AI が応答を断りました",
                   "config": "設定を直してください"}.get(st, str(st))
            self.status.emit(f"AI: {msg}")

    def _flush_delta(self) -> None:
        if not self._delta:
            self._delta_timer.stop()
            return
        items, self._delta = self._delta, []
        merged: list[tuple[str, str]] = []
        for k, t in items:
            if k not in ("text", "thinking"):
                continue
            if merged and merged[-1][0] == k:
                merged[-1] = (k, merged[-1][1] + t)
            else:
                merged.append((k, t))
        for k, t in merged:
            self.chat.append_delta(k, t)

    def _ask_confirm(self, p: dict[str, Any]) -> None:
        text = f"AI がツール {p.get('name')} を実行しようとしています。実行しますか?\n\n引数:\n{pretty_json(p.get('args'), 3000)}"
        r = QMessageBox.question(self, "ツールの実行の確認", text,
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.Yes)
        if self.backend is not None:
            self.backend.answer_confirm(int(p.get("id")), r == QMessageBox.StandardButton.Yes)

    def _show_usage(self, last: dict[str, Any], total: dict[str, Any]) -> None:
        def f(d: dict[str, Any], k: str) -> str:
            return f"{int(d.get(k, 0) or 0):,}"

        txt = (f"トークン(この会話): 入力 {f(total, 'input_tokens')}(キャッシュの読み {f(total, 'cache_read_tokens')}・"
               f"書き {f(total, 'cache_write_tokens')})、出力 {f(total, 'output_tokens')}、"
               f"{int(total.get('requests', 0) or 0)} 回のリクエスト")
        if last:
            txt += f"。直前: 入力 {f(last, 'input_tokens')}・出力 {f(last, 'output_tokens')}"
        txt += "。料金は各社の料金表で確かめてください"
        self.tokens_label.setText(txt)

    # ------------------------------------------------------------------ 表示
    def _update_header(self, model: str | None = None) -> None:
        s = self.settings
        st = {"ready": "MCP: 準備できました", "starting": "MCP: 起動しています", "restarting": "MCP: 起動し直しています",
              "error": "MCP: 起動できません", "stopped": "MCP: 止まっています"}.get(self.server_state, self.server_state)
        # キーはここでは読まない(送るときと設定の画面で読む)。前に送ったときの読み込み元だけを出す
        parts = [KIND_LABELS[s.provider_kind()], f"モデル {model or s.model or '(未設定)'}",
                 f"接続先 {s.base_url}", f"キー: {self.key_label or '送るときに読み込みます'}", st,
                 f"作業フォルダ {display_path(self.workspace, self.ctx.root)}"]
        if self.surface is not None:
            parts.append(f"モデル {self.surface.model}[{self.surface.unit}] 手順 {self.surface.steps}")
        self.header.set_status("warning" if self.key_missing else None, "  ·  ".join(parts))

    def _update_buttons(self) -> None:
        idle = not self.busy
        self.send_btn.setEnabled(idle)
        self.stop_btn.setEnabled(self.busy)
        self.resend_btn.setEnabled(idle and self.backend is not None and not self.backend.conv.empty)
        for b in (self.undo_btn, self.save_state_btn, self.load_state_btn, self.settings_btn, self.save_conv_btn,
                  self.load_conv_btn, self.reset_btn, self.tool_run_btn):
            b.setEnabled(idle)
        self.assign_btn.setEnabled(idle and bool(self.selected_faces))
        self.insert_faces_btn.setEnabled(bool(self.selected_faces))
        self.open_pmesh_btn.setEnabled(bool(self.last_pmesh))

    def patch_colors(self) -> list[tuple[str, str]]:
        scheme = self.ctx.theme.scheme()
        if self.surface is None:
            return []
        return [(p["name"], theme.patch_color(scheme, p["name"], p.get("kind"), i))
                for i, p in enumerate(self.surface.patches)]

    def _fill_tables(self) -> None:
        s = self.surface
        if s is None:
            return
        colors = dict(self.patch_colors())
        rows = s.patch_rows()
        un = s.unassigned_faces()
        self.patch_table.setRowCount(len(rows) + 1)
        unit2 = f"{s.unit}²"
        for r, row in enumerate(rows):
            sw = swatch(colors.get(row["name"], self.ctx.theme.tokens()["text_muted"]))
            self.patch_table.setCellWidget(r, 0, sw)
            self.patch_table.setItem(r, 1, readonly_item(row["name"]))
            self.patch_table.setItem(r, 2, readonly_item(row["kind"]))
            fs = row["faces"]
            self.patch_table.setItem(r, 3, readonly_item(f"{len(fs)}: " + ", ".join(str(f) for f in fs[:30])
                                                         + (" …" if len(fs) > 30 else "")))
            self.patch_table.setItem(r, 4, readonly_item(f"{row['area']:.6g} {unit2}", True))
        r = len(rows)
        self.patch_table.setCellWidget(r, 0, swatch(self.ctx.theme.tokens()["surface_2"]))
        self.patch_table.setItem(r, 1, readonly_item("(付いていない面)"))
        self.patch_table.setItem(r, 2, readonly_item(""))
        self.patch_table.setItem(r, 3, readonly_item(f"{len(un)}: " + ", ".join(str(f) for f in un[:30])
                                                     + (" …" if len(un) > 30 else "")))
        area = sum(float(s.faces[f].get("area", 0.0)) for f in un)
        self.patch_table.setItem(r, 4, readonly_item(f"{area:.6g} {unit2}" if un else "—", True))
        pof = s.patch_of()
        self.faces_table.blockSignals(True)
        self.faces_table.setRowCount(len(s.faces))
        for r, (t, f) in enumerate(sorted(s.faces.items())):
            self.faces_table.setItem(r, 0, readonly_item(str(t), True))
            self.faces_table.setItem(r, 1, readonly_item(str(f.get("type", ""))))
            self.faces_table.setItem(r, 2, readonly_item(pof.get(t, "—" if f.get("boundary") else "(内部)")))
            self.faces_table.setItem(r, 3, readonly_item(f"{float(f.get('area', 0)):.6g}", True))
            c = f.get("c") or [0, 0, 0]
            self.faces_table.setItem(r, 4, readonly_item("(" + ", ".join(f"{float(x):.4g}" for x in c) + ")"))
            self.faces_table.setItem(r, 5, readonly_item("はい" if f.get("boundary") else "いいえ"))
        self.faces_table.blockSignals(False)
        names = [p["name"] for p in s.patches]
        cur = self.patch_name.currentText()
        self.patch_name.blockSignals(True)
        self.patch_name.clear()
        self.patch_name.addItems(names or ["inlet", "outlet", "wall"])
        self.patch_name.setEditText(cur)
        self.patch_name.blockSignals(False)
        self.selected_faces = [f for f in self.selected_faces if f in s.faces]

    def _show_scene(self, keep_camera: bool = False) -> None:
        s = self.surface
        if s is None or s.empty:
            self.view.clear()
            if s is not None:
                self.view.caption.setText(f"モデル {s.model}: 面がまだありません")
                self.view.caption.setVisible(True)
            return
        t = self.ctx.theme.tokens()
        pc = self.patch_colors()
        index = {name: i for i, (name, _c) in enumerate(pc)}
        polys = build_polydata(s, index)
        sec = self.section_combo.currentData() or "none"
        if sec != "none":
            b = polys["boundary"] if polys["boundary"] is not None else polys["internal"]
            centre = np.array(b.center if b is not None else (0, 0, 0))
            normal = SECTION_KEEP[sec]
            for k in ("boundary", "internal", "lines"):
                if polys[k] is not None:
                    try:
                        polys[k] = polys[k].clip(normal=normal, origin=tuple(centre), invert=False)
                        if polys[k].n_cells == 0:
                            polys[k] = None
                    except Exception:
                        pass
        colors = [t["surface_2"]] + [c for _n, c in pc]  # -1 = 付いていない面
        n = len(pc)
        items = []
        mesh_edges = s.source == "mesh" and self.edges_check.isChecked()
        if polys["boundary"] is not None:
            items.append(SceneItem(polys["boundary"], scalars="patch_id", cmap=colors, clim=(-1.5, n - 0.5),
                                   n_colors=n + 1, show_edges=mesh_edges, edge_color=t["text_muted"]))
        if polys["internal"] is not None:
            items.append(SceneItem(polys["internal"], color=t["info"], opacity=0.25, pickable=False))
        if polys["lines"] is not None:
            items.append(SceneItem(polys["lines"], color=t["text"], line_width=1.6, lighting=False, pickable=False))
        if self.selected_faces and polys["boundary"] is not None:
            try:
                b = polys["boundary"]
                ids = np.flatnonzero(np.isin(np.asarray(b.cell_data["face_id"]), self.selected_faces))
                if ids.size:
                    sel = b.extract_cells(ids).extract_surface(algorithm="dataset_surface")
                    items.append(SceneItem(sel, color=t["accent"], opacity=0.55, pickable=False))
                    items.append(SceneItem(sel.extract_feature_edges(boundary_edges=True, feature_edges=False,
                                                                     manifold_edges=False, non_manifold_edges=False),
                                           color=t["accent"], line_width=3.5, lighting=False, pickable=False))
            except Exception:
                pass
        labels = []
        if self.labels_check.isChecked():
            pts, labs = face_label_points(s)
            labels = [(tuple(p), lab) for p, lab in zip(pts, labs)]
        legend = pc + ([("付いていない面", t["surface_2"])] if s.unassigned_faces() else [])
        if self.selected_faces:
            legend.append((f"選んだ面 {', '.join(str(f) for f in self.selected_faces[:8])}", t["accent"]))
        what = "メッシュの表面" if s.source == "mesh" else "形状(粗い面のメッシュ)"
        mesh = s.mesh or {}
        cap = (f"{what}。モデル {s.model}[{s.unit}]、体積 {s.volumes} 個、面 {len(s.faces)} 枚、手順 {s.steps}"
               + (f"、四面体 {mesh.get('tetrahedra'):,}" if mesh.get("tetrahedra") else ""))
        prev = self.view.scene()
        bounds = None
        if polys["boundary"] is not None or polys["internal"] is not None:
            b0 = polys["boundary"] if polys["boundary"] is not None else polys["internal"]
            bb = np.array(b0.bounds, dtype=float).reshape(3, 2)
            pad = 0.06 * float(np.linalg.norm(bb[:, 1] - bb[:, 0]))  # 拡大しすぎて端が切れないように少し広げる
            bounds = tuple(float(v) for v in (bb + np.array([-pad, pad])).ravel())
        scene = Scene(items=items, view=prev.view if prev else "focus", focus_bounds=bounds, legend=legend,
                      caption=cap, labels=labels)
        self.view.set_scene(scene, keep_camera=keep_camera and prev is not None)
        self._apply_picking()

    def _restyle(self, scheme: str) -> None:
        tok = self.ctx.theme.tokens()
        self.chat.restyle(tok)
        self.log.restyle(tok)
        self.view.restyle(scheme)
        if self.surface is not None:
            self._fill_tables()
            self._show_scene(keep_camera=True)

    # ------------------------------------------------------------------ 終わり
    def shutdown(self) -> None:
        if self.backend is not None:
            self.backend.shutdown()
            self.backend = None
        self.view.close_view()
