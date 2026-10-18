"""gmsh(OpenCASCADE)の上の AI モデリング用 MCP サーバー(フェーズ 7)。

エージェントが MCP のツールだけで計算領域の形状を作り、境界パッチを付けて、ソルバー用の
``.pmesh`` まで書き出せるようにする薄いツール層。使い方は ``docs/mcp.md``。

- :mod:`.engine` … gmsh のモデルと手順の履歴(元に戻す・保存・読み込みは履歴のやり直し)
- :mod:`.tools` … ツールの引数(pydantic)と説明
- :mod:`.server` … MCP サーバー(stdio、公式 Python SDK)
- :mod:`.workspace` … 書き出し先・読み込み元の制限
- :mod:`.render` … 画面を出さずに描く画像
- :mod:`.stdio_guard` … gmsh・OpenCASCADE の標準出力への書き込みから通信路を守る

起動: ``uv run --project python --extra mcp gsis-mcp [--workspace DIR]``
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """``gsis-mcp`` の入口。gmsh を読み込む前に標準入出力を通信路から切り離す。"""
    from .config import parse_args

    args = parse_args(argv)
    from .stdio_guard import guard_stdio

    fds = guard_stdio()
    from .server import serve

    return serve(args, fds)
