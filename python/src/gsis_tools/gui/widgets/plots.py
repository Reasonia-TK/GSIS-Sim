"""matplotlib の図(テーマに合わせて描き直す)。"""

from __future__ import annotations

import warnings
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from .. import theme

# 隠れている画面の図は大きさが 0 になり、描くたびに警告が出るので抑える
warnings.filterwarnings("ignore", message="constrained_layout not applied")


class PlotPanel(QWidget):
    """matplotlib の図とツールバー(拡大・移動・保存)。

    テーマを変えたら :meth:`restyle` を呼び、持ち主が図を描き直す(rcParams は作るときにしか効かないため)。
    """

    def __init__(self, parent: QWidget | None = None, toolbar: bool = True, figsize=(6.0, 4.0)):
        super().__init__(parent)
        self.figure = Figure(figsize=figsize, layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._with_toolbar = toolbar
        self.toolbar: NavigationToolbar2QT | None = None
        self._make_toolbar()
        self._lay.addWidget(self.canvas, 1)

    def _make_toolbar(self) -> None:
        if not self._with_toolbar:
            return
        if self.toolbar is not None:
            self._lay.removeWidget(self.toolbar)
            self.toolbar.deleteLater()
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        self._lay.insertWidget(0, self.toolbar)

    def restyle(self, scheme: str) -> None:
        t = theme.tokens(scheme)
        self.figure.set_facecolor(t["bg"])
        self._make_toolbar()  # アイコンの色(明暗)は作るときに決まる

    def clear(self) -> None:
        self.figure.clear()

    def draw(self) -> None:
        self.canvas.draw_idle()

    def save(self, path: str | Path, dpi: int = 150) -> Path:
        path = Path(path)
        self.figure.savefig(path, dpi=dpi)
        return path
