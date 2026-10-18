"""メインウィンドウ: 左の一覧で 4 つの画面(ケース設定・メッシュ・実行・結果)を切り替える。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QWidget,
)

from .context import AppContext
from .pages.case_page import CasePage
from .pages.mesh_page import MeshPage
from .pages.results_page import ResultsPage
from .pages.run_page import RunPage
from .paths import display_path, solver_exe_name
from .theme import MODE_LABELS, MODES

PAGES = (("case", "ケース設定"), ("mesh", "メッシュ"), ("run", "実行"), ("results", "結果"))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, view_backend: str | None = None):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("GSIS-PBC — gsis-gui(試作 v0.2)")
        self.resize(1600, 980)

        # ツールバー: ソルバーと表示モード
        tb = QToolBar("設定")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        title = QLabel("GSIS-PBC")
        title.setObjectName("Heading")
        tb.addWidget(title)
        sub = QLabel("希薄気体の定常解法(arXiv:2610.07658)— 試作 v0.2")
        sub.setObjectName("Muted")
        tb.addWidget(sub)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.solver_label = QLabel("")
        self.solver_label.setObjectName("Muted")
        tb.addWidget(self.solver_label)
        self.solver_btn = QPushButton("ソルバーを選ぶ…")
        tb.addWidget(self.solver_btn)
        tb.addSeparator()
        tb.addWidget(QLabel("表示"))
        self.mode_combo = QComboBox()
        for m in MODES:
            self.mode_combo.addItem(MODE_LABELS[m], m)
        self.mode_combo.setCurrentIndex(MODES.index(ctx.theme.mode))
        tb.addWidget(self.mode_combo)

        # 左の一覧と画面
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(170)
        self.stack = QStackedWidget()
        self.case_page = CasePage(ctx)
        self.mesh_page = MeshPage(ctx, view_backend=view_backend)
        self.run_page = RunPage(ctx)
        self.results_page = ResultsPage(ctx, view_backend=view_backend)
        self.pages = {"case": self.case_page, "mesh": self.mesh_page, "run": self.run_page, "results": self.results_page}
        for i, (key, label) in enumerate(PAGES):
            item = QListWidgetItem(f"{i + 1}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(150, 38))
            self.nav.addItem(item)
            self.stack.addWidget(self.pages[key])
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # 画面どうしのつながり
        self.run_page.editor_case = lambda: (self.case_page.current_path(), self.case_page.is_dirty())
        self.case_page.case_saved.connect(self.run_page.set_case)
        self.case_page.case_loaded.connect(self.run_page.set_case)
        self.mesh_page.mesh_selected.connect(self._use_mesh)
        self.run_page.open_results.connect(self._open_results)
        self.run_page.run_finished.connect(lambda _d, _c: self.results_page.refresh_run_list())
        for p in self.pages.values():
            p.status.connect(self.show_status)
        self.ctx.status.connect(self.show_status)
        self.solver_btn.clicked.connect(self._choose_solver)
        self.mode_combo.currentIndexChanged.connect(lambda _i: self.ctx.theme.set_mode(self.mode_combo.currentData()))
        self.ctx.solver_changed.connect(self._update_solver_label)
        self._update_solver_label()
        self.statusBar().showMessage(f"リポジトリ: {ctx.root}")

    # ------------------------------------------------------------------
    def show_page(self, key: str) -> None:
        idx = [k for k, _ in PAGES].index(key)
        self.nav.setCurrentRow(idx)

    def show_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000)

    def _update_solver_label(self) -> None:
        p = self.ctx.solver_path()
        if self.ctx.solver_ok():
            subs = self.ctx.subcommands()
            names = "・".join(n for n in ("run", "cis-run", "pbc-run") if n in subs) or "実行のサブコマンドなし"
            self.solver_label.setText(f"ソルバー: {display_path(p, self.ctx.root)}({names})")
            self.solver_label.setProperty("status", "")
        else:
            self.solver_label.setText(f"ソルバーが見つかりません: {display_path(p, self.ctx.root)}")

    def _choose_solver(self) -> None:
        start = str(self.ctx.solver_path().parent if self.ctx.solver_path().parent.exists() else self.ctx.root)
        p, _ = QFileDialog.getOpenFileName(self, "gsis-solver を選ぶ", start, f"gsis-solver ({solver_exe_name()});;すべてのファイル (*)")
        if p:
            self.ctx.set_solver_path(p)

    def _use_mesh(self, path: str) -> None:
        self.case_page.set_mesh(path)
        self.show_page("case")
        self.show_status(f"ケースのメッシュを {display_path(path, self.ctx.root)} にしました(保存すると TOML に書く)")

    def _open_results(self, out_dir: str) -> None:
        self.show_page("results")
        self.results_page.load(Path(out_dir))

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.run_page.proc is not None:
            r = QMessageBox.question(self, "計算を止めますか", "実行中の計算があります。止めて終了しますか?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
        if self.case_page.is_dirty():
            r = QMessageBox.question(self, "変更を捨てますか", "ケース設定に保存していない変更があります。捨てて終了しますか?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
        self.shutdown()
        super().closeEvent(ev)

    def shutdown(self) -> None:
        self.run_page.shutdown()
        self.mesh_page.shutdown()
        self.results_page.shutdown()
