"""``gsis-gui`` の起動。

    uv run --project python --extra gui gsis-gui [--theme system|light|dark] [--case FILE.toml]
                                                 [--results RUN_DIR] [--page case|mesh|run|results]
                                                 [--solver EXE] [--offscreen] [--quit-after 秒]

``--offscreen`` は画面を出さずに動かす(Qt の offscreen プラットフォーム、3D は静止画の方式)。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gsis-gui", description="GSIS-PBC の GUI(試作 v0.2)")
    ap.add_argument("--theme", choices=("system", "light", "dark"), default=None,
                    help="表示モード(既定は前回の選択、初回は system)")
    ap.add_argument("--case", help="起動時に開くケース定義(TOML)")
    ap.add_argument("--results", help="起動時に読み込む出力フォルダ")
    ap.add_argument("--page", choices=("case", "mesh", "run", "results"), default=None, help="最初に出す画面")
    ap.add_argument("--solver", help="gsis-solver の実行ファイル(既定は build/release/src/gsis-solver.exe)")
    ap.add_argument("--offscreen", action="store_true", help="画面を出さずに動かす(確認用)")
    ap.add_argument("--quit-after", type=float, default=None, metavar="秒", help="指定した秒数で終わる(起動の確認用)")
    return ap


def prepare_environment() -> None:
    """QApplication を作る前の環境変数(Qt の束縛、offscreen でのフォントの場所)。"""
    os.environ.setdefault("QT_API", "pyside6")
    if os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen") and "QT_QPA_FONTDIR" not in os.environ:
        # offscreen のプラットフォームは OS のフォントを探さないので、Windows のフォントの場所を教える
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if windir and (Path(windir) / "Fonts").is_dir():
            os.environ["QT_QPA_FONTDIR"] = str(Path(windir) / "Fonts")


def create_app(argv: list[str] | None = None):
    """QApplication・テーマ・共有の状態を作る(テストとスクリーンショットからも使う)。"""
    prepare_environment()
    from PySide6.QtWidgets import QApplication

    from .context import AppContext
    from .qtheme import ThemeManager

    app = QApplication.instance() or QApplication(list(argv or [sys.argv[0]]))
    app.setApplicationName("gsis-gui")
    app.setOrganizationName("GSIS-PBC")
    return app, ThemeManager, AppContext


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ.setdefault("GSIS_GUI_3D", "static")
    app, ThemeManager, AppContext = create_app([sys.argv[0]])
    from .main_window import MainWindow

    tm = ThemeManager(app, mode=args.theme)
    ctx = AppContext(tm)
    if args.solver:
        ctx.set_solver_path(Path(args.solver), save=False)
    tm.apply()
    win = MainWindow(ctx)
    tm.apply()  # 画面を作った後にもう一度(各画面がテーマの通知を受ける)
    if args.case:
        win.case_page.load_file(args.case)
    if args.results:
        win.results_page.load(Path(args.results))
        win.show_page("results")
    if args.page:
        win.show_page(args.page)
    win.show()
    if args.quit_after is not None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(args.quit_after * 1000), win.close)
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
