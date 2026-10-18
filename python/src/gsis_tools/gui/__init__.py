"""GSIS-PBC の GUI(試作 v0.2、``gsis-gui``)。

ソルバー ``gsis-solver`` とは、ファイル(ケース TOML・メッシュ・結果)とサブプロセスだけで
やり取りする(docs/spec.md §4.1、docs/gui.md)。

Qt に依存しない部分(テストで直接使う):

- :mod:`gsis_tools.gui.paths` … リポジトリのルート、ソルバー、参照データの場所
- :mod:`gsis_tools.gui.casefile` … ケース TOML の読み書き(tomlkit、コメントを保つ)・プリセット・導出量
- :mod:`gsis_tools.gui.history` … ``history.csv`` の解析と追いかけ読み
- :mod:`gsis_tools.gui.centerline` … 中心線の分布の取り出し
- :mod:`gsis_tools.gui.reference` … 論文の Table 1 と Fig.3 のデジタイズ値、比較の計算
- :mod:`gsis_tools.gui.solver` … ``gsis-solver`` のサブコマンドの一覧とコマンドラインの組み立て
- :mod:`gsis_tools.gui.results` … 出力フォルダ(summary.json・VTU)の読み込み

画面(PySide6・pyvista・pyvistaqt。extra ``gui`` で入る)は :mod:`gsis_tools.gui.app` から起動する。
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """``gsis-gui`` の入口。Qt はここで初めて読み込む(Qt なしでも他のモジュールは使える)。"""
    from .app import main as _main

    return _main(argv)
