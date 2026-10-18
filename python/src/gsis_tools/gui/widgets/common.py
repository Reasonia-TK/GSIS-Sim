"""小さな共通部品: 数値の入力欄、パスの入力欄、ログ、状態の表示、[kinetic] などのキーと値の表。"""

from __future__ import annotations

import html
import math
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..casefile import parse_scalar, scalar_to_text, scalar_type
from ..qtheme import mono_font


def repolish(w: QWidget) -> None:
    """動的プロパティを変えたあとにスタイルシートを当て直す。"""
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def primary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setProperty("primary", True)
    return b


def heading(text: str, name: str = "Heading") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    return lab


def muted(text: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(wrap)
    return lab


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setObjectName("Rule")
    return f


class StatusLabel(QLabel):
    """状態(ok / warning / error / info / 無印)を色で示すラベル。"""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def set_status(self, kind: str | None, text: str) -> None:
        self.setProperty("status", kind or "")
        self.setText(text)
        repolish(self)


class FloatField(QLineEdit):
    """浮動小数点の入力欄(指数表記可)。不正な値は枠を赤くする。

    ``allow_empty`` なら空欄を None として受け付ける。``positive`` / ``non_negative`` で範囲を検査する。
    """

    valueChanged = Signal()

    def __init__(self, value: float | None = None, *, allow_empty: bool = False, positive: bool = False,
                 non_negative: bool = False, placeholder: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.allow_empty = allow_empty
        self.positive = positive
        self.non_negative = non_negative
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.setValue(value)
        self.textChanged.connect(self._on_text)
        self.setMinimumWidth(90)

    def setValue(self, v: float | None) -> None:
        with QSignalBlocker(self):
            self.setText("" if v is None else repr(float(v)))
        self._validate()

    def value(self) -> float | None:
        s = self.text().strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def is_valid(self) -> bool:
        s = self.text().strip()
        if not s:
            return self.allow_empty
        try:
            v = float(s)
        except ValueError:
            return False
        if not math.isfinite(v):
            return False
        if self.positive and not v > 0:
            return False
        if self.non_negative and v < 0:
            return False
        return True

    def _validate(self) -> None:
        bad = not self.is_valid()
        if bool(self.property("invalid")) != bad:
            self.setProperty("invalid", bad)
            repolish(self)

    def _on_text(self, _t: str) -> None:
        self._validate()
        self.valueChanged.emit()


class PathField(QWidget):
    """パスの入力欄と「参照…」ボタン。``mode`` は ``"open"`` / ``"save"`` / ``"dir"``。"""

    changed = Signal(str)

    def __init__(self, mode: str = "open", file_filter: str = "", caption: str = "", parent: QWidget | None = None,
                 start_dir: Callable[[], str] | None = None):
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        self.caption = caption
        self.start_dir = start_dir
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        self.button = QToolButton()
        self.button.setText("参照…")
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        self.button.clicked.connect(self._browse)
        self.edit.textChanged.connect(self.changed)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, s: str) -> None:
        self.edit.setText(s)

    def _browse(self) -> None:
        start = self.text() or (self.start_dir() if self.start_dir else "")
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, self.caption or "フォルダを選ぶ", start)
        elif self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, self.caption or "保存先", start, self.file_filter)
        else:
            p, _ = QFileDialog.getOpenFileName(self, self.caption or "ファイルを開く", start, self.file_filter)
        if p:
            self.setText(str(Path(p)))


class LogView(QPlainTextEdit):
    """ソルバーなどの出力のログ。エラー・警告の行は色を変える(テーマを変えたら色を付け直す)。"""

    def __init__(self, parent: QWidget | None = None, max_lines: int = 20000):
        super().__init__(parent)
        self.setObjectName("Log")
        self.setReadOnly(True)
        self.setFont(mono_font(9))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(max_lines)
        self._lines: deque[tuple[str, str]] = deque(maxlen=max_lines)
        self._tokens = theme.tokens("light")

    @staticmethod
    def classify(line: str, stream: str = "out") -> str:
        if "エラー" in line or "引数の誤り" in line or "error" in line.lower():
            return "error"
        if "警告" in line or "warning" in line.lower():
            return "warning"
        if stream == "err":
            return "warning"
        if stream == "gui":
            return "info"
        return "normal"

    def _color(self, kind: str) -> str:
        return {"error": self._tokens["error"], "warning": self._tokens["warning"], "info": self._tokens["info"],
                "muted": self._tokens["text_muted"]}.get(kind, self._tokens["text"])

    def _append_one(self, line: str, kind: str) -> None:
        # 色は行ごとに必ず付ける(付けないと前の行の色を引き継ぐ)
        c = self._color(kind)
        self.appendHtml(f'<span style="color:{c}; white-space:pre">{html.escape(line) or "&nbsp;"}</span>')

    def append_lines(self, text: str, stream: str = "out", kind: str | None = None) -> None:
        at_end = self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() - 4
        for line in text.splitlines():
            k = kind or self.classify(line, stream)
            self._lines.append((line, k))
            self._append_one(line, k)
        if at_end:
            self.moveCursor(QTextCursor.MoveOperation.End)
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        self._scroll_left()

    def clear_log(self) -> None:
        self._lines.clear()
        self.clear()

    def restyle(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        lines = list(self._lines)
        self.clear()
        for line, k in lines:
            self._append_one(line, k)
        self._scroll_left()

    def _scroll_left(self) -> None:
        """横は左端に戻す。カーソルを最後の行の先頭に置く(表示のときにカーソルが見える位置まで横に
        動くので、長い行の末尾に置いたままだと右にずれる)。配置が決まった後にも、もう一度戻す。"""
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.movePosition(QTextCursor.MoveOperation.StartOfLine)
        self.setTextCursor(c)
        self.horizontalScrollBar().setValue(0)

        def later() -> None:
            try:
                self.horizontalScrollBar().setValue(0)
            except RuntimeError:  # 部品が先に消えた
                pass

        QTimer.singleShot(0, later)

    def text_all(self) -> str:
        return "\n".join(line for line, _ in self._lines)


class KeyValueTable(QWidget):
    """[kinetic]・[macro]・[output] のキーと値の表。既知のキーは型と説明を出す。"""

    changed = Signal()

    def __init__(self, known: dict[str, tuple[str, Any, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.known = known
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["キー", "値", "説明"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(90)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.key_combo = QComboBox()
        self.key_combo.setEditable(True)
        self.key_combo.addItems(sorted(known))
        self.key_combo.setCurrentText("")
        self.key_combo.lineEdit().setPlaceholderText("キーを選ぶか入力")
        add = QPushButton("追加")
        rem = QPushButton("選んだ行を消す")
        row.addWidget(self.key_combo, 1)
        row.addWidget(add)
        row.addWidget(rem)
        lay.addLayout(row)
        add.clicked.connect(self._add_clicked)
        rem.clicked.connect(self._remove_selected)
        self._tokens = theme.tokens("light")

    def restyle(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        self._validate_all()

    def set_values(self, values: dict[str, Any]) -> None:
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
            for k, v in values.items():
                self._append_row(k, scalar_to_text(v))
        self._validate_all()

    def _append_row(self, key: str, value_text: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(key))
        self.table.setItem(r, 1, QTableWidgetItem(value_text))
        info = self.known.get(key)
        desc = QTableWidgetItem(f"{info[2]}(既定 {scalar_to_text(info[1])})" if info else "知らないキー(ソルバーがエラーにする)")
        desc.setFlags(desc.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 2, desc)

    def _add_clicked(self) -> None:
        key = self.key_combo.currentText().strip()
        if not key:
            return
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == key:
                self.table.setCurrentCell(r, 1)
                return
        default = self.known.get(key, ("str", "", ""))[1]
        with QSignalBlocker(self.table):
            self._append_row(key, scalar_to_text(default))
        self._validate_all()
        self.changed.emit()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        if rows:
            self.changed.emit()

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        self._validate_all()
        self.changed.emit()

    def _parse_row(self, r: int) -> tuple[str, Any]:
        key = self.table.item(r, 0).text().strip()
        text = self.table.item(r, 1).text()
        info = self.known.get(key)
        return key, parse_scalar(text, info[0] if info else None)

    def _validate_all(self) -> None:
        with QSignalBlocker(self.table):
            for r in range(self.table.rowCount()):
                ok = True
                try:
                    key, _ = self._parse_row(r)
                    ok = bool(key) and key in self.known
                    err = not bool(key)
                except ValueError:
                    ok, err = False, True
                item = self.table.item(r, 1)
                if err:
                    item.setForeground(QColor(self._tokens["error"]))
                elif not ok:
                    item.setForeground(QColor(self._tokens["warning"]))
                else:
                    item.setForeground(QColor(self._tokens["text"]))

    def values(self) -> dict[str, Any]:
        """表の値(型が合わない値は ValueError)。"""
        out: dict[str, Any] = {}
        for r in range(self.table.rowCount()):
            key, v = self._parse_row(r)
            if key:
                out[key] = v
        return out

    def problems(self) -> list[str]:
        msgs = []
        for r in range(self.table.rowCount()):
            key = self.table.item(r, 0).text().strip()
            try:
                self._parse_row(r)
            except ValueError as exc:
                msgs.append(f"{key}: {exc}")
                continue
            if key and key not in self.known:
                msgs.append(f"{key}: 知らないキー")
        return msgs


def swatch(color: str, size: int = 12) -> QLabel:
    lab = QLabel()
    lab.setObjectName("Swatch")
    lab.setFixedSize(size, size)
    lab.setStyleSheet(f"background:{color};")
    return lab


class Legend(QWidget):
    """色見本と名前を横に並べた凡例(3D 表示の下に置く)。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 2, 4, 2)
        self._lay.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_entries(self, entries: list[tuple[str, str]]) -> None:
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for label, color in entries:
            self._lay.addWidget(swatch(color))
            lab = QLabel(label)
            self._lay.addWidget(lab)
        self._lay.addStretch(1)


def fmt(v: Any, digits: int = 4) -> str:
    """表に出す数値の表記。"""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "—"
        if v == 0:
            return "0"
        if abs(v) >= 1e5 or abs(v) < 1e-3:
            return f"{v:.{digits - 1}e}"
        return f"{v:.{digits}g}"
    return str(v)


def readonly_item(text: str, align_right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


__all__ = [
    "FloatField", "PathField", "LogView", "KeyValueTable", "StatusLabel", "Legend", "primary_button", "heading",
    "muted", "hline", "repolish", "fmt", "readonly_item", "swatch", "scalar_type",
]
