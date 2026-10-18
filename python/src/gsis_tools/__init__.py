"""GSIS-PBC の前処理・後処理・検証ツール(gsis_tools)。

メッシュ関係の主なモジュール:

- :mod:`gsis_tools.pmesh` … 内部メッシュ形式 ``.pmesh`` の読み書き・幾何量・検査
- :mod:`gsis_tools.builder` … 要素(四面体・六面体・プリズム・ピラミッド)から面ベースのメッシュを組み立てる
- :mod:`gsis_tools.gmsh_convert` … gmsh のモデル / ``.msh`` からの変換
- :mod:`gsis_tools.vtu` … ParaView 用の VTU 書き出し
- :mod:`gsis_tools.quality` … メッシュ品質
- :mod:`gsis_tools.geometry` … 管ケース・平板のメッシュ生成
"""

from __future__ import annotations

try:  # パッケージとして導入されていれば、その版を使う
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("gsis-tools")
except Exception:  # pragma: no cover - 導入されていない場合
    __version__ = "0.1.0"


def main(argv: list[str] | None = None) -> int:
    """CLI の入口(``gsis-tools``)。実体は :func:`gsis_tools.cli.main`。"""
    from .cli import main as _main

    return _main(argv)
