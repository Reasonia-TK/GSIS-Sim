"""起動時の引数(``gsis-mcp``)。重いモジュールを読み込まないように分けてある。"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_WORKSPACE = "mcp_workspace"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gsis-mcp",
        description="gmsh(OpenCASCADE)で計算領域を作り、境界パッチ付きの .pmesh を書き出す MCP サーバー(stdio)",
    )
    ap.add_argument("--workspace", default=None,
                    help=f"作業フォルダ(既定はカレントの下の {DEFAULT_WORKSPACE}/)。ファイルはこの中にだけ書く")
    ap.add_argument("--read-dir", action="append", default=[],
                    help="読み込み(import_cad・model_load)を許すフォルダを足す(繰り返し可)")
    ap.add_argument("--allow-python", action="store_true",
                    help="run_python(gmsh の Python スクリプトの実行)を有効にする。任意のコードが動くので注意")
    ap.add_argument("--gui-tools", action="store_true",
                    help="GUI 専用のツール(gui_surface)も一覧に出す(GUI が子プロセスとして起動するとき)")
    ap.add_argument("--threads", type=int, default=None, help="gmsh のスレッド数(既定は min(8, CPU 数))")
    ap.add_argument("--log-level", default="WARNING", choices=("DEBUG", "INFO", "WARNING", "ERROR"),
                    help="標準エラーに出すログの水準")
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    args.workspace = Path(args.workspace) if args.workspace else Path.cwd() / DEFAULT_WORKSPACE
    return args
