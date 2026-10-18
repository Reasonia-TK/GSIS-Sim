"""warm clay テーマを Qt(PySide6)に当てる: パレット・スタイルシート・表示モードの切替と保存。

- モードは ``system``(OS の設定に追従、既定)/ ``light`` / ``dark``。選んだモードは設定ファイルに保存する
- ``app.setStyle("Fusion")`` を先に呼び、パレットとトークンから作ったスタイルシートを当てる
- matplotlib の rcParams も同じトークンで更新し、:attr:`ThemeManager.changed` で各画面に描き直しを知らせる
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from . import theme
from .theme import MODES, rgba

UI_FONT_FAMILIES = ["Segoe UI", "Yu Gothic UI", "Meiryo UI"]
MONO_FONT_FAMILIES = ["Cascadia Mono", "Consolas", "BIZ UDGothic", "MS Gothic"]


def qcolor(hex_color: str) -> QColor:
    r, g, b, a = rgba(hex_color)
    return QColor(r, g, b, a)


def css_rgba(hex_color: str) -> str:
    r, g, b, a = rgba(hex_color)
    return f"rgba({r}, {g}, {b}, {a})"


def ui_font(point_size: float = 9.0) -> QFont:
    f = QFont()
    f.setFamilies(UI_FONT_FAMILIES)
    f.setPointSizeF(point_size)
    return f


def mono_font(point_size: float = 9.0) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FONT_FAMILIES)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(point_size)
    return f


def build_palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    R = QPalette.ColorRole
    for role, key in [
        (R.Window, "bg"), (R.Base, "surface_2"), (R.AlternateBase, "surface"), (R.WindowText, "text"),
        (R.Text, "text"), (R.Button, "surface"), (R.ButtonText, "text"), (R.Highlight, "accent"),
        (R.HighlightedText, "accent_fg"), (R.ToolTipBase, "surface"), (R.ToolTipText, "text"),
        (R.PlaceholderText, "text_muted"), (R.Link, "accent_text"), (R.BrightText, "error"),
        (R.Light, "surface_2"), (R.Midlight, "surface_2"), (R.Mid, "border"), (R.Dark, "border"), (R.Shadow, "border"),
    ]:
        p.setColor(role, qcolor(t[key]))
    D = QPalette.ColorGroup.Disabled
    for role in (R.Text, R.ButtonText, R.WindowText):
        p.setColor(D, role, qcolor(t["text_disabled"]))
    p.setColor(D, R.Base, qcolor(t["surface"]))
    return p


def icon_files(t: dict[str, str], scheme: str) -> dict[str, str]:
    """チェックボックス・矢印の小さな SVG をテーマの色で作り、パス(``/`` 区切り)を返す。

    スタイルシートの ``image: url(...)`` はファイルを指すので、一時フォルダに書く(内容が同じなら書き直さない)。
    """
    d = Path(tempfile.gettempdir()) / "gsis_gui_icons" / scheme
    d.mkdir(parents=True, exist_ok=True)

    def chevron(points: str, color: str) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                f'<path d="{points}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
                f'stroke-linejoin="round"/></svg>')

    svgs = {
        "check": (f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                  f'<path d="M3.2 7.3 L5.9 10 L10.9 4.2" fill="none" stroke="{t["accent_fg"]}" stroke-width="2" '
                  f'stroke-linecap="round" stroke-linejoin="round"/></svg>'),
        "radio": (f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                  f'<circle cx="7" cy="7" r="3.4" fill="{t["accent"]}"/></svg>'),
        "down": chevron("M2 3.6 L5 6.6 L8 3.6", t["text_muted"]),
        "up": chevron("M2 6.4 L5 3.4 L8 6.4", t["text_muted"]),
        "down_disabled": chevron("M2 3.6 L5 6.6 L8 3.6", t["text_disabled"]),
        "up_disabled": chevron("M2 6.4 L5 3.4 L8 6.4", t["text_disabled"]),
    }
    out = {}
    for name, text in svgs.items():
        f = d / f"{name}.svg"
        try:
            if not f.is_file() or f.read_text(encoding="utf-8") != text:
                f.write_text(text, encoding="utf-8")
        except OSError:
            pass
        out[name] = f.as_posix()
    return out


def build_stylesheet(t: dict[str, str], scheme: str = "light") -> str:
    sel = css_rgba(t["selection"])
    ic = icon_files(t, scheme)
    return f"""
QWidget {{ color: {t['text']}; }}
QMainWindow, QDialog, QWidget#Page, QStackedWidget {{ background: {t['bg']}; }}
QWidget#Panel {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QGroupBox {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px;
            margin-top: 16px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px;
                   color: {t['text_muted']}; font-weight: 600; }}
QLabel {{ background: transparent; }}
QLabel#Muted {{ color: {t['text_muted']}; }}
QLabel#Heading {{ font-size: 13pt; font-weight: 600; }}
QLabel#Subheading {{ font-weight: 600; }}
QLabel[status="ok"] {{ color: {t['success']}; }}
QLabel[status="warning"] {{ color: {t['warning']}; }}
QLabel[status="error"] {{ color: {t['error']}; }}
QLabel[status="info"] {{ color: {t['info']}; }}
QLabel#Swatch {{ border: 1px solid {t['border']}; border-radius: 3px; }}

QListWidget#Nav {{ background: {t['surface']}; border: none; border-right: 1px solid {t['border']};
                  padding: 8px 6px; outline: 0; }}
QListWidget#Nav::item {{ padding: 9px 10px; border-radius: 4px; margin: 1px 0; color: {t['text']};
                        border-left: 3px solid transparent; }}
QListWidget#Nav::item:hover {{ background: {t['surface_2']}; }}
QListWidget#Nav::item:selected {{ background: {sel}; color: {t['text']}; border-left: 3px solid {t['accent']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {t['surface_2']}; border: 1px solid {t['border']}; border-radius: 4px; padding: 3px 6px;
    selection-background-color: {t['accent']}; selection-color: {t['accent_fg']}; }}
QPlainTextEdit, QTextEdit {{ padding: 4px; }}
QPlainTextEdit#Log, QTextEdit#Log {{ background: {t['surface']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {t['focus']}; padding: 2px 5px; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {t['text_disabled']}; background: {t['surface']}; }}
QLineEdit:read-only {{ background: {t['surface']}; }}
QLineEdit[invalid="true"] {{ border: 2px solid {t['error']}; padding: 2px 5px; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']};
    selection-background-color: {sel}; selection-color: {t['text']}; outline: 0; }}

QPushButton, QToolButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 4px;
    padding: 4px 12px; }}
QToolButton {{ padding: 3px 8px; }}
QPushButton:hover, QToolButton:hover {{ background: {t['surface_2']}; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {t['border']}; }}
QPushButton:focus, QToolButton:focus {{ border: 2px solid {t['focus']}; padding: 3px 11px; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['text_disabled']}; background: {t['surface']}; }}
QPushButton[primary="true"] {{ background: {t['accent_strong']}; color: {t['accent_fg']}; border: none;
    padding: 5px 14px; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t['accent_hover']}; }}
QPushButton[primary="true"]:focus {{ border: 2px solid {t['text']}; padding: 3px 12px; }}
QPushButton[primary="true"]:disabled {{ background: {t['surface_2']}; color: {t['text_disabled']}; }}

QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 8px; background: {t['bg']}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {t['text_muted']}; padding: 6px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; }}
QTabBar::tab:hover {{ color: {t['text']}; background: {t['surface_2']}; }}

QHeaderView::section {{ background: {t['surface_2']}; color: {t['text']}; border: none;
    border-right: 1px solid {t['border']}; border-bottom: 1px solid {t['border']}; padding: 4px 6px; }}
QTableView, QTableWidget, QTreeView, QListView, QListWidget {{ background: {t['surface']};
    alternate-background-color: {t['bg']}; gridline-color: {t['border']}; border: 1px solid {t['border']};
    border-radius: 4px; selection-background-color: {sel}; selection-color: {t['text']}; outline: 0; }}
QTableCornerButton::section {{ background: {t['surface_2']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['border']}; min-height: 24px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['border']}; min-width: 24px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {t['text_disabled']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 4px; }}
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['text_muted']}; }}
QStatusBar QLabel {{ color: {t['text_muted']}; }}
QToolBar {{ background: {t['surface']}; border: none; border-bottom: 1px solid {t['border']}; spacing: 8px;
    padding: 4px 8px; }}
QMenuBar {{ background: {t['surface']}; }}
QMenuBar::item:selected {{ background: {t['surface_2']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; }}
QMenu::item {{ padding: 4px 18px; }}
QMenu::item:selected {{ background: {sel}; }}
QSplitter::handle {{ background: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QProgressBar {{ border: 1px solid {t['border']}; border-radius: 4px; background: {t['surface_2']};
    text-align: center; max-height: 14px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {t['text_muted']}; border-radius: 3px;
    background: {t['surface_2']}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({ic['check']}); }}
QCheckBox::indicator:disabled {{ border-color: {t['text_disabled']}; background: {t['surface']}; }}
QCheckBox::indicator:checked:disabled {{ background: {t['text_disabled']}; border-color: {t['text_disabled']}; }}
QRadioButton::indicator {{ width: 13px; height: 13px; border: 1px solid {t['text_muted']}; border-radius: 7px;
    background: {t['surface_2']}; }}
QRadioButton::indicator:checked {{ border-color: {t['accent']}; image: url({ic['radio']}); }}
QAbstractSpinBox {{ padding-right: 18px; }}
QAbstractSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 16px;
    border: none; border-left: 1px solid {t['border']}; border-top-right-radius: 4px; background: transparent; }}
QAbstractSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 16px;
    border: none; border-left: 1px solid {t['border']}; border-bottom-right-radius: 4px; background: transparent; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {t['border']}; }}
QAbstractSpinBox::up-arrow {{ image: url({ic['up']}); width: 8px; height: 8px; }}
QAbstractSpinBox::down-arrow {{ image: url({ic['down']}); width: 8px; height: 8px; }}
QAbstractSpinBox::up-arrow:disabled {{ image: url({ic['up_disabled']}); }}
QAbstractSpinBox::down-arrow:disabled {{ image: url({ic['down_disabled']}); }}
QComboBox::down-arrow {{ image: url({ic['down']}); width: 9px; height: 9px; }}
QComboBox::down-arrow:disabled {{ image: url({ic['down_disabled']}); }}
QToolButton::menu-indicator {{ image: url({ic['down']}); width: 9px; height: 9px;
    subcontrol-origin: padding; subcontrol-position: center right; }}
QToolButton[popupMode="2"] {{ padding-right: 16px; }}
"""


def settings() -> QSettings:
    """GUI の設定(``GSIS_GUI_SETTINGS`` があればその ini ファイル。テストで使う)。"""
    path = os.environ.get("GSIS_GUI_SETTINGS")
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return QSettings(path, QSettings.Format.IniFormat)
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "GSIS-PBC", "gsis-gui")


class ThemeManager(QObject):
    """表示モードの管理。:attr:`changed` は実際の配色(``"light"`` / ``"dark"``)を送る。"""

    changed = Signal(str)

    def __init__(self, app: QApplication, mode: str | None = None):
        super().__init__(app)
        self._app = app
        self._settings = settings()
        saved = str(self._settings.value("theme/mode", "system"))
        self._mode = mode if mode in MODES else (saved if saved in MODES else "system")
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_changed)

    @property
    def mode(self) -> str:
        return self._mode

    def scheme(self) -> str:
        if self._mode in ("light", "dark"):
            return self._mode
        hints = QGuiApplication.styleHints()
        try:
            return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"
        except AttributeError:  # pragma: no cover - Qt 6.5 より前
            return "light"

    def tokens(self) -> dict[str, str]:
        return theme.tokens(self.scheme())

    def series(self) -> list[str]:
        return theme.series(self.scheme())

    def set_mode(self, mode: str, save: bool = True) -> None:
        if mode not in MODES:
            raise ValueError(f"モードは {MODES} のどれかです: {mode}")
        self._mode = mode
        if save:
            self._settings.setValue("theme/mode", mode)
            self._settings.sync()
        self.apply()

    def apply(self) -> None:
        scheme = self.scheme()
        t = theme.tokens(scheme)
        self._app.setStyle("Fusion")
        self._app.setPalette(build_palette(t))
        self._app.setFont(ui_font())
        self._app.setStyleSheet(build_stylesheet(t, scheme))
        theme.apply_mpl(scheme)
        self.changed.emit(scheme)

    def _on_system_changed(self, *_args) -> None:
        if self._mode == "system":
            self.apply()
