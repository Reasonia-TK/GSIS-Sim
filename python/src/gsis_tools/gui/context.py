"""画面どうしで共有する状態: リポジトリのルート、ソルバー、テーマ、設定。"""

from __future__ import annotations

from pathlib import Path
import os

from PySide6.QtCore import QObject, Signal

from .paths import default_solver, repo_root
from .qtheme import ThemeManager, settings
from .solver import SubcommandInfo, query_subcommands


class AppContext(QObject):
    """アプリ全体の状態。ソルバーの場所を変えたら :attr:`solver_changed` を送る。"""

    solver_changed = Signal()
    status = Signal(str)

    def __init__(self, theme_manager: ThemeManager, root: Path | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.root = Path(root or repo_root())
        self.theme = theme_manager
        self.settings = settings()
        saved = self.settings.value("solver/path", "")
        self._solver = (default_solver(self.root) if os.environ.get("GSIS_SOLVER") else
                        Path(saved) if saved and Path(str(saved)).is_file() else default_solver(self.root))
        self._subcommands: dict[str, SubcommandInfo] | None = None

    # --- ソルバー ---
    def solver_path(self) -> Path:
        return self._solver

    def solver_ok(self) -> bool:
        return self._solver.is_file()

    def set_solver_path(self, path: str | Path, save: bool = True) -> None:
        self._solver = Path(path)
        self._subcommands = None
        if save:
            self.settings.setValue("solver/path", str(self._solver))
            self.settings.sync()
        self.solver_changed.emit()

    def subcommands(self, refresh: bool = False) -> dict[str, SubcommandInfo]:
        """``--help`` から読んだサブコマンドの一覧(結果は覚えておく)。"""
        if self._subcommands is None or refresh:
            self._subcommands = query_subcommands(self._solver, cwd=self.root) if self.solver_ok() else {}
        return self._subcommands

    # --- 最近使ったファイル ---
    def recent(self, key: str) -> list[str]:
        v = self.settings.value(f"recent/{key}", [])
        if isinstance(v, str):
            v = [v]
        return [str(x) for x in (v or [])]

    def add_recent(self, key: str, path: str | Path, limit: int = 10) -> None:
        items = [str(path)] + [p for p in self.recent(key) if p != str(path)]
        self.settings.setValue(f"recent/{key}", items[:limit])
        self.settings.sync()
