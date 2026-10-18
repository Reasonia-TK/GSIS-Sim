"""会話の欄(モデリング(AI)の画面): ユーザーの文章、AI の文章(Markdown)、考え、ツールの呼び出しの記録
(折りたためる)、説明(情報・注意・エラー)、書き出したメッシュへのリンク。

応答の逐次表示: :meth:`ChatView.append_delta` で文字を足し、:meth:`ChatView.finish_assistant` で確定する。
色はテーマのトークンだけから使う(:meth:`ChatView.restyle`)。
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..qtheme import css_rgba, mono_font

# ツールの呼び出しの見出しに出す主な引数
_SUMMARY_KEYS = ("name", "path", "unit", "op", "select", "size_max", "target", "steps", "entities", "objects",
                 "section", "max_cells")


def args_summary(name: str, args: Any, limit: int = 90) -> str:
    """ツールの引数の短い要約(見出し用)。"""
    if not isinstance(args, dict):
        s = str(args or "")
        return s[:limit] + ("…" if len(s) > limit else "")
    if name == "batch" and isinstance(args.get("steps"), list):
        tools = [str(st.get("tool")) for st in args["steps"] if isinstance(st, dict)]
        s = f"{len(tools)} 手順: " + "・".join(tools)
        return s[:limit] + ("…" if len(s) > limit else "")
    parts = []
    for k in _SUMMARY_KEYS:
        if k in args:
            v = json.dumps(args[k], ensure_ascii=False, separators=(",", ":"))
            parts.append(f"{k}={v}")
    if not parts and args:
        k = next(iter(args))
        parts.append(f"{k}={json.dumps(args[k], ensure_ascii=False, separators=(',', ':'))}")
    s = ", ".join(parts)
    return s[:limit] + ("…" if len(s) > limit else "")


def result_summary(text: str, ok: bool, limit: int = 110) -> str:
    """ツールの結果の短い要約(エラーなら理由、成功なら主な値)。"""
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        d = None
    if isinstance(d, dict):
        if not ok and d.get("error"):
            s = str(d["error"])
        else:
            keys = ("volume", "total_volume", "rel_error", "patch", "faces", "unassigned", "complete", "tetrahedra",
                    "cells", "check", "path", "count", "steps", "ok", "model")
            parts = []
            for k in keys:
                if k in d and not isinstance(d[k], (dict, list)):
                    parts.append(f"{k}={d[k]}")
                elif k in d and isinstance(d[k], list) and len(d[k]) <= 6 and all(not isinstance(x, (dict, list)) for x in d[k]):
                    parts.append(f"{k}={d[k]}")
            s = ", ".join(parts) or "OK"
    else:
        s = str(text or "")
    s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def pretty_json(v: Any, limit: int = 20000) -> str:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return v[:limit]
    s = json.dumps(v, ensure_ascii=False, indent=1)
    return s[:limit] + ("\n…(長いので省略)" if len(s) > limit else "")


class _Bubble(QFrame):
    """会話の 1 つの塊(枠と中身)。"""

    def __init__(self, role: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.setObjectName(f"Chat_{role}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(10, 6, 10, 6)
        self.lay.setSpacing(3)


class WrapLabel(QLabel):
    """折り返す文字のラベル。最小の大きさを 1 行分にして、高さは幅から決めさせる(スクロールの欄の中で、
    折り返しの最小の幅で計算した高さが残って、下に空白ができるのを防ぐ)。"""

    def minimumSizeHint(self):  # noqa: N802
        sh = super().minimumSizeHint()
        return QSize(min(sh.width(), 60), self.fontMetrics().height())


def soft_wrap(text: str) -> str:
    """パスなどの長い語を折り返せるように、区切りの後に幅 0 の空白を入れる。"""
    out = []
    for ch in text:
        out.append(ch)
        if ch in "\\/_.,=":
            out.append("​")
    return "".join(out)


def safe_markdown(text: str) -> str:
    """AI の文章(Markdown)を HTML にする。文章の中の生の HTML は使わず、文字として出す(AI の出力で
    画面に勝手な要素を作らせない)。"""
    doc = QTextDocument()
    f = QTextDocument.MarkdownFeature
    doc.setMarkdown(text, f.MarkdownDialectGitHub | f.MarkdownNoHTML)
    return doc.toHtml()


def _label(text: str = "", *, md: bool = False, muted: bool = False, mono: bool = False) -> QLabel:
    lab = WrapLabel()
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lab.setTextFormat(Qt.TextFormat.RichText if md else Qt.TextFormat.PlainText)
    lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    if muted:
        lab.setObjectName("Muted")
    if mono:
        lab.setFont(mono_font(8.5))
    lab.setText(safe_markdown(text) if md and text else text)
    return lab


class ToolCard(_Bubble):
    """ツールの呼び出し 1 つ。見出し(状態・名前・引数の要約・時間)を押すと、引数と結果を開く。"""

    def __init__(self, call_id: str, name: str, args: Any, parent: QWidget | None = None):
        super().__init__("tool", parent)
        self.call_id = call_id
        self.name = name
        self.args = args
        self.ok: bool | None = None
        head = QHBoxLayout()
        head.setSpacing(6)
        self.toggle = QToolButton()
        self.toggle.setObjectName("ChatToggle")
        self.toggle.setCheckable(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setAutoRaise(True)
        self.toggle.toggled.connect(self.set_open)
        self.state = QLabel("…")
        self.state.setObjectName("ChatState")
        self.title = QLabel(f"<b>{html.escape(name)}</b>")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.summary = _label(args_summary(name, args), muted=True)
        self.summary.setWordWrap(False)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.time = QLabel("")
        self.time.setObjectName("Muted")
        head.addWidget(self.toggle)
        head.addWidget(self.state)
        head.addWidget(self.title)
        head.addWidget(self.summary, 1)
        head.addWidget(self.time)
        self.lay.addLayout(head)
        self.result_line = _label("実行しています…", muted=True)
        self.lay.addWidget(self.result_line)
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(22, 2, 0, 2)
        bl.setSpacing(2)
        bl.addWidget(_label("引数", muted=True))
        self.args_view = QPlainTextEdit(pretty_json(args))
        self.args_view.setReadOnly(True)
        self.args_view.setFont(mono_font(8.5))
        self.args_view.setMaximumHeight(160)
        bl.addWidget(self.args_view)
        bl.addWidget(_label("結果", muted=True))
        self.result_view = QPlainTextEdit("")
        self.result_view.setReadOnly(True)
        self.result_view.setFont(mono_font(8.5))
        self.result_view.setMaximumHeight(220)
        bl.addWidget(self.result_view)
        self.image_label = QLabel()
        self.image_label.setVisible(False)
        bl.addWidget(self.image_label)
        self.body.setVisible(False)
        self.lay.addWidget(self.body)

    def set_open(self, on: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.body.setVisible(on)
        if self.toggle.isChecked() != on:
            self.toggle.setChecked(on)

    def is_open(self) -> bool:
        return self.body.isVisible()

    def finish(self, ok: bool, text: str, seconds: float, *, skipped: bool = False, image: bytes | None = None) -> None:
        self.ok = ok
        self.skipped = skipped
        self.state.setText("✓" if ok else ("—" if skipped else "✗"))
        self.state.setProperty("status", "ok" if ok else ("warning" if skipped else "error"))
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self.time.setText(f"{seconds:.2f} s" if seconds >= 0.005 else "")
        self.result_line.setText(soft_wrap(result_summary(text, ok)))
        self.result_view.setPlainText(pretty_json(text))
        if image:
            img = QImage.fromData(image, "PNG")
            if not img.isNull():
                self.image_label.setPixmap(QPixmap.fromImage(img).scaledToWidth(
                    min(480, img.width()), Qt.TransformationMode.SmoothTransformation))
                self.image_label.setVisible(True)


class _Host(QWidget):
    """会話の欄の中身。最小の大きさを 0 にして、高さは幅から決めさせる(QScrollArea は最小の大きさと
    heightForWidth の大きい方を使うので、折り返しの最小の幅で計算した高さが残ると、下に空白ができる)。"""

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, 0)


class ChatView(QScrollArea):
    """会話の欄。"""

    open_mesh = Signal(str)  # 書き出したメッシュを開く
    resend_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setObjectName("ChatView")
        self._host = _Host()
        self._host.setObjectName("ChatHost")
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(6, 6, 10, 6)
        self._lay.setSpacing(6)
        self._lay.addStretch(1)
        self.setWidget(self._host)
        self._cards: dict[str, ToolCard] = {}
        self._draft: _Bubble | None = None
        self._draft_text: QLabel | None = None
        self._draft_think: QLabel | None = None
        self._text_buf = ""
        self._think_buf = ""
        self._items: list[QWidget] = []
        self._tokens = theme.tokens("light")
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(30)
        self._scroll_timer.timeout.connect(self._to_bottom)
        self._stick = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ------------------------------------------------------------------ 中身
    def _add(self, w: QWidget) -> QWidget:
        self._lay.insertWidget(self._lay.count() - 1, w)
        self._items.append(w)
        self._scroll_soon()
        return w

    def items(self) -> list[QWidget]:
        return list(self._items)

    def tool_cards(self) -> list[ToolCard]:
        return [w for w in self._items if isinstance(w, ToolCard)]

    def clear(self) -> None:
        for w in self._items:
            w.setParent(None)
            w.deleteLater()
        self._items.clear()
        self._cards.clear()
        self._draft = None
        self._draft_text = self._draft_think = None
        self._text_buf = self._think_buf = ""

    def add_user(self, text: str, notes: list[str] | None = None) -> None:
        b = _Bubble("user")
        if notes:
            b.lay.addWidget(_label("[GUI での操作] " + " / ".join(notes), muted=True))
        b.lay.addWidget(_label(text))
        self._add(b)

    def _ensure_draft(self) -> None:
        if self._draft is None:
            self._draft = _Bubble("assistant")
            self._draft_think = _label("", muted=True)
            self._draft_think.setVisible(False)
            self._draft_text = _label("", md=True)
            self._draft.lay.addWidget(self._draft_think)
            self._draft.lay.addWidget(self._draft_text)
            self._add(self._draft)

    def append_delta(self, kind: str, text: str) -> None:
        """応答の途中の文字(``kind`` は text / thinking)。"""
        if kind not in ("text", "thinking") or not text:
            return
        self._ensure_draft()
        if kind == "text":
            self._text_buf += text
            self._draft_text.setText(safe_markdown(self._text_buf))
        else:
            self._think_buf += text
            self._draft_think.setText("考え: " + self._think_buf)
            self._draft_think.setVisible(True)
        self._scroll_soon()

    def finish_assistant(self, text: str, thinking: list[str] | None = None, model: str | None = None,
                         refused: bool = False) -> None:
        """応答 1 回を確定する(逐次表示の下書きを置き換える)。文章も考えもなければ何も出さない。"""
        thinking = [t for t in (thinking or []) if t and t.strip()]
        if not text.strip() and not thinking:
            if self._draft is not None:
                self._items.remove(self._draft)
                self._draft.setParent(None)
                self._draft.deleteLater()
        else:
            self._ensure_draft()
            self._draft_think.setText("考え: " + " / ".join(thinking) if thinking else "")
            self._draft_think.setVisible(bool(thinking))
            self._draft_text.setText(safe_markdown(text if not refused else f"(断られた応答)\n\n{text}"))
            self._draft_text.setVisible(bool(text.strip()))
            if model:
                self._draft.setToolTip(f"モデル: {model}")
        self._draft = None
        self._draft_text = self._draft_think = None
        self._text_buf = self._think_buf = ""
        self._scroll_soon()

    def start_tool(self, call_id: str, name: str, args: Any) -> ToolCard:
        if self._draft is not None and not self._text_buf and not self._think_buf:
            pass
        card = ToolCard(str(call_id), name, args)
        self._cards[str(call_id)] = card
        self._add(card)
        return card

    def finish_tool(self, call_id: str, name: str, ok: bool, text: str, seconds: float, *, skipped: bool = False,
                    image: bytes | None = None, args: Any = None) -> ToolCard:
        card = self._cards.get(str(call_id))
        if card is None:
            card = self.start_tool(str(call_id), name, args)
        card.finish(ok, text, seconds, skipped=skipped, image=image)
        self._scroll_soon()
        return card

    def add_note(self, kind: str, text: str, *, retry: bool = False) -> QWidget:
        b = _Bubble("note")
        b.setProperty("kind", kind)
        lab = _label(text)
        lab.setProperty("status", {"error": "error", "warning": "warning", "info": "info"}.get(kind, ""))
        b.lay.addWidget(lab)
        if retry:
            row = QHBoxLayout()
            btn = QPushButton("再送")
            btn.setToolTip("同じ会話をもう一度 API に送る")
            btn.clicked.connect(self.resend_requested)
            row.addWidget(btn)
            row.addStretch(1)
            b.lay.addLayout(row)
            b.retry_button = btn
        self._add(b)
        return b

    def add_pmesh(self, path: str, display: str | None = None) -> QWidget:
        b = _Bubble("pmesh")
        row = QHBoxLayout()
        row.addWidget(_label(soft_wrap(f"書き出したメッシュ: {display or path}")), 1)
        btn = QPushButton("メッシュの画面で開く")
        btn.clicked.connect(lambda _c=False, p=path: self.open_mesh.emit(p))
        row.addWidget(btn)
        b.lay.addLayout(row)
        b.open_button = btn
        self._add(b)
        return b

    def add_manual(self, name: str, args: Any, ok: bool, text: str) -> ToolCard:
        card = ToolCard(f"manual-{len(self._items)}", name, args)
        card.title.setText(f"<b>{html.escape(name)}</b>(手で実行)")
        self._add(card)
        card.finish(ok, text, 0.0)
        return card

    def load_records(self, records: list[dict[str, Any]], resolve_display: Callable[[str], str] | None = None) -> None:
        """保存した会話の記録から作り直す。"""
        self.clear()
        for r in records:
            role = r.get("role")
            if role == "user":
                self.add_user(str(r.get("text", "")), r.get("notes") or [])
            elif role == "assistant":
                self.finish_assistant(str(r.get("text", "")), r.get("thinking") or [], r.get("model"),
                                      bool(r.get("refused")))
            elif role == "tool":
                card = self.start_tool(str(r.get("id")), str(r.get("name")), r.get("args"))
                card.finish(bool(r.get("ok")), str(r.get("text", "")), float(r.get("seconds") or 0.0),
                            skipped=bool(r.get("skipped")))
            elif role == "manual":
                self.add_manual(str(r.get("name")), r.get("args"), bool(r.get("ok")), str(r.get("text", "")))
            elif role == "note":
                self.add_note(str(r.get("kind", "info")), str(r.get("text", "")))
            elif role == "pmesh":
                p = str(r.get("path", ""))
                self.add_pmesh(p, resolve_display(p) if resolve_display else None)

    # ------------------------------------------------------------------ 見た目
    def restyle(self, tokens: dict[str, str]) -> None:
        t = self._tokens = tokens
        sel = css_rgba(t["selection"])
        self.setStyleSheet(f"""
QWidget#ChatHost {{ background: {t['bg']}; }}
QFrame#Chat_user {{ background: {t['surface_2']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QFrame#Chat_assistant {{ background: {t['bg']}; border: none; border-left: 3px solid {t['accent']}; }}
QFrame#Chat_tool {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; }}
QFrame#Chat_note {{ background: transparent; border: none; }}
QFrame#Chat_pmesh {{ background: {sel}; border: 1px solid {t['accent']}; border-radius: 6px; }}
QLabel#ChatState[status="ok"] {{ color: {t['success']}; font-weight: 600; }}
QLabel#ChatState[status="error"] {{ color: {t['error']}; font-weight: 600; }}
QLabel#ChatState[status="warning"] {{ color: {t['warning']}; font-weight: 600; }}
QToolButton#ChatToggle {{ border: none; padding: 0; }}
""")

    # ------------------------------------------------------------------ 送り
    def _on_scroll(self, v: int) -> None:
        sb = self.verticalScrollBar()
        self._stick = v >= sb.maximum() - 30

    def _scroll_soon(self) -> None:
        if self._stick:
            self._scroll_timer.start()

    def _to_bottom(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
