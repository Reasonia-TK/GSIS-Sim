"""AI モデリングの設定の画面: 形式・接続先・モデル・API キー・最大トークン・ツール呼び出しの上限・画像・作業フォルダなど。

API キーの欄は伏せ字(入力の内容は表示しない)。前に入れたキーや保存したキーは欄に戻さず、どこから読むか
(この画面で入力・資格情報マネージャー・環境変数)だけを示す。キーは OK のときに、このセッションの間だけ
メモリに持つ。「OS の資格情報マネージャーに保存する」を選んだときだけ保存する。設定ファイルには書かない。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..ai.secrets import KeyResolver, KeyStore, Secret, redact
from ..ai.settings import (
    BASE_URL_EXAMPLES,
    EFFORTS,
    ENV_KEY,
    FORMAT_ANTHROPIC,
    FORMAT_LABELS,
    FORMAT_OPENAI,
    FORMATS,
    OFFICIAL_BASE_URL,
    OPENAI_API_LABELS,
    OPENAI_APIS,
    THINKING_DISPLAYS,
    THINKING_LABELS,
    TOKEN_PARAM_LABELS,
    TOKEN_PARAMS,
    AiSettings,
    normalize_base_url,
)
from ..widgets.common import PathField, StatusLabel


class _Bridge(QObject):
    models = Signal(object, object)  # (一覧, エラーの説明)


class _FitHeight:
    """折り返すラベルの高さを、今の幅で決める(QFormLayout は狭い幅で計算した高さを使うので、上下に空白ができる)。"""

    _fit_h = -1

    def sizeHint(self):  # noqa: N802
        sh = super().sizeHint()
        w = self.width()
        if self.wordWrap() and w > 40 and self.text():
            return QSize(sh.width(), self.heightForWidth(w))
        return sh

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.wordWrap():
            h = self.heightForWidth(self.width())
            if h != self._fit_h:
                self._fit_h = h
                self.updateGeometry()


class _Note(_FitHeight, QLabel):
    """薄い色の注記。"""


class _Status(_FitHeight, StatusLabel):
    """状態を色で示す表示。"""


def _note(text: str) -> QLabel:
    lab = _Note(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(True)
    return lab


def _form_page() -> tuple[QWidget, QFormLayout]:
    """タブの 1 枚: 上にフォーム、下に伸びしろ。"""
    w = QWidget()
    outer = QVBoxLayout(w)
    f = QFormLayout()
    outer.addLayout(f)
    outer.addStretch(1)
    return w, f


class AiSettingsDialog(QDialog):
    """設定の画面。OK で :attr:`result_settings`・:attr:`entered_key`・:attr:`save_key` を読む。"""

    def __init__(self, settings: AiSettings, resolver: KeyResolver, *, workspace_default: str = "",
                 fetch_models: Callable[[AiSettings, Secret | None], Future] | None = None,
                 conversation_active: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("AI の設定")
        self.resize(760, 640)
        self.s = settings.copy()
        self.resolver = resolver
        self.store: KeyStore = resolver.store
        self.fetch_models = fetch_models
        self.workspace_default = workspace_default
        self.conversation_active = conversation_active
        self.entered_keys: dict[tuple[str, str], Secret] = {}
        self.save_key_requested: set[tuple[str, str]] = set()
        self.deleted_keys: set[tuple[str, str]] = set()
        self._fmt = self.s.format
        self._bridge = _Bridge()
        self._bridge.models.connect(self._on_models)
        self._build()
        self._load_format(self._fmt)

    # ------------------------------------------------------------------ 画面
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        self.tabs = tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # 接続
        w, f = _form_page()
        self.format_combo = QComboBox()
        for fmt in FORMATS:
            self.format_combo.addItem(FORMAT_LABELS[fmt], fmt)
        self.format_combo.setCurrentIndex(FORMATS.index(self._fmt))
        self.format_combo.currentIndexChanged.connect(self._on_format)
        f.addRow("形式", self.format_combo)
        self.api_combo = QComboBox()
        for k in OPENAI_APIS:
            self.api_combo.addItem(OPENAI_API_LABELS[k], k)
        self.api_label = QLabel("API の種類(OpenAI)")
        f.addRow(self.api_label, self.api_combo)
        self.api_status = _Status("")
        f.addRow("", self.api_status)
        f.addRow("", _note("※ 形式と API の種類は、会話の途中では変えられない(履歴の形が違うため。変えると会話のリセットが要る)"))
        row = QHBoxLayout()
        self.base_combo = QComboBox()
        self.base_combo.setEditable(True)
        self.base_combo.setMinimumWidth(380)
        self.base_combo.lineEdit().setPlaceholderText("空なら公式の接続先")
        self.official_btn = QPushButton("公式に戻す")
        self.official_btn.clicked.connect(lambda: self.base_combo.setEditText(OFFICIAL_BASE_URL[self._fmt]))
        row.addWidget(self.base_combo, 1)
        row.addWidget(self.official_btn)
        f.addRow("接続先(base URL)", row)
        row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(300)
        self.model_combo.lineEdit().setPlaceholderText("一覧から選ぶか入力する")
        self.fetch_btn = QPushButton("一覧を取得")
        self.fetch_btn.setToolTip("接続先の /v1/models からモデルの一覧を読む(キーが要ります)")
        self.fetch_btn.clicked.connect(self._fetch)
        self.forget_btn = QPushButton("一覧から消す")
        self.forget_btn.clicked.connect(self._forget_model)
        row.addWidget(self.model_combo, 1)
        row.addWidget(self.fetch_btn)
        row.addWidget(self.forget_btn)
        f.addRow("モデル", row)
        self.models_status = _Status("")
        f.addRow("", self.models_status)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setMinimumWidth(380)
        self.key_edit.setInputMethodHints(Qt.InputMethodHint.ImhHiddenText | Qt.InputMethodHint.ImhNoPredictiveText
                                          | Qt.InputMethodHint.ImhSensitiveData)
        f.addRow("API キー", self.key_edit)
        self.key_source = _Status("")
        f.addRow("", self.key_source)
        row = QHBoxLayout()
        self.save_key_check = QCheckBox("OS の資格情報マネージャーに保存する")
        self.delete_key_btn = QPushButton("保存したキーを消す")
        self.delete_key_btn.clicked.connect(self._delete_key)
        self.forget_session_btn = QPushButton("入力したキーを忘れる")
        self.forget_session_btn.clicked.connect(self._forget_session_key)
        row.addWidget(self.save_key_check)
        row.addWidget(self.delete_key_btn)
        row.addWidget(self.forget_session_btn)
        row.addStretch(1)
        f.addRow("", row)
        self.no_key_check = QCheckBox("キーなしで送る(OpenAI 互換のローカルのサーバー。Authorization を送らない)")
        f.addRow("", self.no_key_check)
        self.use_env_check = QCheckBox("環境変数のキーを、公式以外のこの接続先にも使う")
        f.addRow("", self.use_env_check)
        f.addRow("", _note("キーは画面・ログ・会話の記録・設定ファイル・作業フォルダに出しません。読み込む順: "
                           "この画面で入力 → OS の資格情報マネージャー → 環境変数(公式の接続先だけ)"))
        tabs.addTab(w, "接続")

        # 応答
        w, f = _form_page()
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 1_000_000)
        self.max_tokens.setSingleStep(1024)
        self.max_tokens.setGroupSeparatorShown(True)
        f.addRow("最大トークン(1 回の応答の出力の上限)", self.max_tokens)
        self.tool_limit = QSpinBox()
        self.tool_limit.setRange(1, 1000)
        self.tool_limit.setValue(self.s.tool_limit)
        f.addRow("ツール呼び出しの上限(1 回の指示あたり)", self.tool_limit)
        f.addRow("", _note("※ 上限そのものは次の指示から効く。AI への指示の文に書く上限の数は、会話のリセットの後に効く"))
        self.stream_check = QCheckBox("応答を逐次表示する(ストリーミング)")
        self.stream_check.setChecked(self.s.stream)
        f.addRow("", self.stream_check)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(5, 7200)
        self.timeout.setDecimals(0)
        self.timeout.setValue(self.s.timeout_s)
        f.addRow("タイムアウト [s]", self.timeout)
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        self.retries.setValue(self.s.max_retries)
        f.addRow("再試行の回数(429・5xx・通信の失敗)", self.retries)
        self.anth_box = QWidget()
        af = QFormLayout(self.anth_box)
        af.setContentsMargins(0, 0, 0, 0)
        self.cache_check = QCheckBox("プロンプトキャッシュ(システム・ツールの定義・会話)")
        self.cache_check.setChecked(self.s.prompt_cache)
        af.addRow("Anthropic", self.cache_check)
        self.thinking_combo = QComboBox()
        for k in THINKING_DISPLAYS:
            self.thinking_combo.addItem(THINKING_LABELS[k], k)
        self.thinking_combo.setCurrentIndex(THINKING_DISPLAYS.index(self.s.thinking_display))
        af.addRow("考えの表示", self.thinking_combo)
        self.effort_combo = QComboBox()
        for k in EFFORTS:
            self.effort_combo.addItem(k or "指定しない(モデルの既定)", k)
        self.effort_combo.setCurrentIndex(EFFORTS.index(self.s.effort))
        af.addRow("effort(考える量)", self.effort_combo)
        self.fallback_check = QCheckBox('安全の分類で断られたら、別のモデルで続ける(fallbacks: "default"、公式の接続先だけ)')
        self.fallback_check.setChecked(self.s.fallbacks)
        af.addRow("", self.fallback_check)
        f.addRow(self.anth_box)
        self.oa_box = QWidget()
        of = QFormLayout(self.oa_box)
        of.setContentsMargins(0, 0, 0, 0)
        self.token_param_combo = QComboBox()
        for k in TOKEN_PARAMS:
            self.token_param_combo.addItem(TOKEN_PARAM_LABELS[k], k)
        of.addRow("OpenAI(Chat Completions): 出力の上限の引数", self.token_param_combo)
        self.store_check = QCheckBox("応答を OpenAI 側に保存する(store: true。既定は保存しない)")
        self.store_check.setToolTip("保存しないとき(store: false)は、推論の項目を暗号化された内容ごと毎回送り返す。"
                                    "保存すると OpenAI 側に 30 日以上残る")
        of.addRow("OpenAI(Responses)", self.store_check)
        of.addRow("", _note("Responses の出力の上限は max_output_tokens(上の「最大トークン」)。ツールの定義は strict: false で送る"))
        f.addRow(self.oa_box)
        self.extra_edit = QPlainTextEdit()
        self.extra_edit.setPlaceholderText('例(Anthropic): {"output_config": {"effort": "high"}}\n例(OpenAI): {"reasoning_effort": "low"}')
        self.extra_edit.setMaximumHeight(90)
        f.addRow("追加の引数(JSON。本文に足す)", self.extra_edit)
        self.extra_status = _Status("")
        f.addRow("", self.extra_status)
        tabs.addTab(w, "応答")

        # 作業
        w, f = _form_page()
        self.images_check = QCheckBox("render の画像を AI に送る(画像はトークンを多く使う)")
        self.images_check.setChecked(self.s.send_images)
        f.addRow("画像", self.images_check)
        f.addRow("", _note("※ 会話のリセットの後に効く(render を AI のツールの一覧に入れるかは、会話の始めに決まるため)。"
                           "送らない設定にしたときは、すぐに送らなくなる"))
        self.confirm_check = QCheckBox("ツールを実行する前に、毎回確かめる")
        self.confirm_check.setChecked(self.s.confirm_tools)
        f.addRow("確認", self.confirm_check)
        self.ws_field = PathField(mode="dir", caption="作業フォルダ")
        self.ws_field.edit.setPlaceholderText(self.workspace_default or "(既定)")
        self.ws_field.setText(self.s.workspace)
        f.addRow("作業フォルダ", self.ws_field)
        f.addRow("", _note("作業フォルダは MCP サーバーが書き出してよい場所(既定はリポジトリの mcp_workspace/gui。"
                           "git の対象外)。※ 変えると、すぐに MCP サーバーを起動し直し、会話をリセットする"))
        self.reset_note = _Status("")
        if self.conversation_active:
            self.reset_note.set_status("warning", "会話の途中です。形式・OpenAI の API の種類を変えると会話のリセットが要ります。"
                                                  "画像を送るか・上限の文は会話のリセットの後に効き、"
                                                  "作業フォルダを変えると会話はリセットされます")
        f.addRow("", self.reset_note)
        tabs.addTab(w, "作業")

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.base_combo.editTextChanged.connect(lambda _t: self._update_key_source())
        self.base_combo.editTextChanged.connect(lambda _t: self._update_api_status())
        self.key_edit.textChanged.connect(lambda _t: self._update_key_source())
        self.api_combo.currentIndexChanged.connect(lambda _i: self._update_api_status())
        self.no_key_check.toggled.connect(lambda _on: self._update_key_source())
        self.use_env_check.toggled.connect(lambda _on: self._update_key_source())

    # ------------------------------------------------------------------ 形式ごとの欄
    def _load_format(self, fmt: str) -> None:
        fs = self.s.per_format[fmt]
        self.base_combo.blockSignals(True)
        self.base_combo.clear()
        self.base_combo.addItems(BASE_URL_EXAMPLES[fmt])
        self.base_combo.setEditText(fs.base_url or OFFICIAL_BASE_URL[fmt])
        self.base_combo.blockSignals(False)
        self.model_combo.clear()
        self.model_combo.addItems(fs.models)
        self.model_combo.setEditText(fs.model)
        self.max_tokens.setValue(int(fs.max_tokens))
        self.no_key_check.setChecked(fs.no_key)
        self.no_key_check.setVisible(fmt == FORMAT_OPENAI)
        self.use_env_check.setChecked(fs.use_env_key)
        self.token_param_combo.setCurrentIndex(max(0, TOKEN_PARAMS.index(fs.token_param)
                                                   if fs.token_param in TOKEN_PARAMS else 0))
        self.extra_edit.setPlainText(fs.extra_body)
        self.api_combo.setCurrentIndex(max(0, self.api_combo.findData(fs.api)))
        self.api_combo.setVisible(fmt == FORMAT_OPENAI)
        self.api_label.setVisible(fmt == FORMAT_OPENAI)
        self.store_check.setChecked(bool(fs.store))
        self.anth_box.setVisible(fmt == FORMAT_ANTHROPIC)
        self.oa_box.setVisible(fmt == FORMAT_OPENAI)
        self.key_edit.clear()
        self.save_key_check.setChecked(False)
        self.models_status.set_status(None, "")
        self._update_key_source()
        self._update_api_status()

    def _update_api_status(self) -> None:
        """OpenAI 形式で、いまの接続先と API の種類の選び方で、どの API を使うか。"""
        if self._fmt != FORMAT_OPENAI:
            self.api_status.set_status(None, "")
            self.api_status.setVisible(False)
            return
        s = self._current_settings()
        api = s.effective_openai_api()
        name = OPENAI_API_LABELS[api]
        why = ("" if s.current.api != "auto" else
               ("(公式の接続先なので)" if s.is_official() else "(公式以外の接続先なので)"))
        self.api_status.setVisible(True)
        self.api_status.set_status("info", f"使う API: {name}{why}")

    def _store_format(self, fmt: str) -> None:
        fs = self.s.per_format[fmt]
        fs.base_url = normalize_base_url(self.base_combo.currentText(), fmt)
        fs.model = self.model_combo.currentText().strip()
        models = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
        if fs.model and fs.model not in models:
            models.insert(0, fs.model)
        fs.models = models
        fs.max_tokens = int(self.max_tokens.value())
        fs.no_key = self.no_key_check.isChecked() and fmt == FORMAT_OPENAI
        fs.use_env_key = self.use_env_check.isChecked()
        fs.token_param = self.token_param_combo.currentData() or "auto"
        fs.extra_body = self.extra_edit.toPlainText().strip()
        if fmt == FORMAT_OPENAI:
            fs.api = self.api_combo.currentData() or "auto"
            fs.store = self.store_check.isChecked()
        typed = self.key_edit.text().strip()
        if typed:
            self.entered_keys[(fmt, fs.base_url)] = Secret(typed)
            if self.save_key_check.isChecked():
                self.save_key_requested.add((fmt, fs.base_url))
        self.key_edit.clear()

    def _on_format(self, _i: int) -> None:
        self._store_format(self._fmt)
        self._fmt = self.format_combo.currentData()
        self._load_format(self._fmt)

    def _current_settings(self) -> AiSettings:
        s = self.s.copy()
        s.format = self._fmt
        fs = s.per_format[self._fmt]
        fs.base_url = normalize_base_url(self.base_combo.currentText(), self._fmt)
        fs.model = self.model_combo.currentText().strip()
        fs.no_key = self.no_key_check.isChecked() and self._fmt == FORMAT_OPENAI
        fs.use_env_key = self.use_env_check.isChecked()
        if self._fmt == FORMAT_OPENAI:
            fs.api = self.api_combo.currentData() or "auto"
        return s

    def _typed_key(self) -> Secret | None:
        t = self.key_edit.text().strip()
        if t:
            return Secret(t)
        s = self._current_settings()
        return self.entered_keys.get((self._fmt, s.base_url))

    def _update_key_source(self) -> None:
        s = self._current_settings()
        if self._typed_key() is not None and self.key_edit.text().strip():
            self.key_source.set_status("info", "入力したキーを使います(OK を押すと、このセッションの間だけ覚えます)")
            return
        r = self.resolver.resolve(s)
        pending = self.entered_keys.get((self._fmt, s.base_url))
        if pending is not None:
            self.key_source.set_status("info", "入力したキーを使います(このセッションの間だけ)")
        elif r.source == "no_key":
            self.key_source.set_status("info", "キーなしで送ります")
        elif r.secret is not None:
            self.key_source.set_status("ok", f"いまのキー: {r.label}")
        else:
            env = ENV_KEY[self._fmt]
            self.key_source.set_status("warning", f"キーがありません({r.label})。入力するか、環境変数 {env} を設定してください")
        ok, name = self.store.available()
        self.save_key_check.setEnabled(ok)
        self.save_key_check.setToolTip("" if ok else f"資格情報マネージャーが使えません({name})")

    def _delete_key(self) -> None:
        s = self._current_settings()
        self.deleted_keys.add((self._fmt, s.base_url))
        ok = self.store.delete(self._fmt, s.base_url)
        self.models_status.set_status("ok" if ok else "warning",
                                      "保存したキーを消しました" if ok else "保存したキーはありませんでした")
        self._update_key_source()

    def _forget_session_key(self) -> None:
        s = self._current_settings()
        self.entered_keys.pop((self._fmt, s.base_url), None)
        self.resolver.set_session(self._fmt, s.base_url, None)
        self.key_edit.clear()
        self._update_key_source()

    # ------------------------------------------------------------------ モデルの一覧
    def _fetch(self) -> None:
        if self.fetch_models is None:
            return
        s = self._current_settings()
        self.fetch_btn.setEnabled(False)
        self.models_status.set_status("info", "モデルの一覧を読んでいます…")
        try:
            fut = self.fetch_models(s, self._typed_key())
        except Exception as exc:
            self._on_models(None, redact(exc))
            return

        def done(f: Future) -> None:
            try:
                res = f.result()
                err = None
            except Exception as exc:
                res = None
                d = getattr(exc, "describe", None)
                err = redact(d() if callable(d) else exc)
            try:
                self._bridge.models.emit(res, err)
            except RuntimeError:  # 画面が閉じられた後
                pass

        fut.add_done_callback(done)

    def _on_models(self, models: list[str] | None, err: str | None) -> None:
        self.fetch_btn.setEnabled(True)
        if err:
            self.models_status.set_status("error", f"一覧を読めませんでした: {err}")
            return
        cur = self.model_combo.currentText()
        old = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
        merged = list(dict.fromkeys([*models, *old]))
        self.model_combo.clear()
        self.model_combo.addItems(merged)
        self.model_combo.setEditText(cur or (models[0] if models else ""))
        self.models_status.set_status("ok", f"{len(models)} 個のモデルを読みました")

    def _forget_model(self) -> None:
        i = self.model_combo.findText(self.model_combo.currentText())
        if i >= 0:
            self.model_combo.removeItem(i)

    # ------------------------------------------------------------------ OK
    def _accept(self) -> None:
        self._store_format(self._fmt)
        self.s.format = self._fmt
        self.s.tool_limit = int(self.tool_limit.value())
        self.s.stream = self.stream_check.isChecked()
        self.s.timeout_s = float(self.timeout.value())
        self.s.max_retries = int(self.retries.value())
        self.s.prompt_cache = self.cache_check.isChecked()
        self.s.thinking_display = self.thinking_combo.currentData()
        self.s.effort = self.effort_combo.currentData() or ""
        self.s.fallbacks = self.fallback_check.isChecked()
        self.s.send_images = self.images_check.isChecked()
        self.s.confirm_tools = self.confirm_check.isChecked()
        self.s.workspace = self.ws_field.text().strip()
        try:
            self.s.extra_body()
        except ValueError as exc:
            self.extra_status.set_status("error", str(exc))
            return
        self.accept()

    def apply_keys(self) -> list[str]:
        """入力したキーをこのセッションに覚え、選ばれたものを資格情報マネージャーに保存する。説明の一覧を返す。"""
        msgs = []
        for (fmt, url), sec in self.entered_keys.items():
            self.resolver.set_session(fmt, url, sec)
            if (fmt, url) in self.save_key_requested:
                try:
                    self.store.set(fmt, url, sec)
                    msgs.append(f"キーを OS の資格情報マネージャーに保存しました({fmt}・{url})")
                except RuntimeError as exc:
                    msgs.append(redact(exc))
        return msgs

    @property
    def result_settings(self) -> AiSettings:
        return self.s.copy()

    def snapshot_texts(self) -> list[str]:
        """画面の欄に表示されている文字(試験でキーが出ていないことを確かめる)。"""
        out = []
        for w in self.findChildren(QLineEdit):
            out.append(w.displayText())
        for w in self.findChildren(QLabel):
            out.append(w.text())
        for w in self.findChildren(QPlainTextEdit):
            out.append(w.toPlainText())
        for w in self.findChildren(QComboBox):
            out.append(w.currentText())
        return out


__all__ = ["AiSettingsDialog", "Any"]
