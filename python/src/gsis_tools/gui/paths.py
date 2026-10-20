"""GUI が使うパス(リポジトリのルート、ソルバーの実行ファイル、論文データ、既定の出力先)。"""

from __future__ import annotations

import os
from pathlib import Path


def _looks_like_root(p: Path) -> bool:
    return (p / "python" / "pyproject.toml").is_file() and (p / "CMakeLists.txt").is_file()


def repo_root() -> Path:
    """リポジトリのルート。環境変数 ``GSIS_ROOT`` があればそれを使う。

    なければ、このファイルの位置(``python/src/gsis_tools/gui``)から上にたどり、
    見つからなければ現在のディレクトリから上にたどる。どちらもなければ現在のディレクトリ。
    """
    env = os.environ.get("GSIS_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for p in here.parents:
        if _looks_like_root(p):
            return p
    cwd = Path.cwd().resolve()
    for p in (cwd, *cwd.parents):
        if _looks_like_root(p):
            return p
    return cwd


def solver_exe_name() -> str:
    return "gsis-solver.exe" if os.name == "nt" else "gsis-solver"


def default_solver(root: Path | None = None) -> Path:
    """既定のソルバー(``build/release/src/gsis-solver.exe``)。なければ CUDA なしの版を探す。"""
    if os.environ.get("GSIS_SOLVER"):
        return Path(os.environ["GSIS_SOLVER"]).resolve()
    root = root or repo_root()
    exe = solver_exe_name()
    candidates = [root / "build" / "release" / "src" / exe, root / "build" / "release-cpu" / "src" / exe]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def reference_dir(root: Path | None = None) -> Path:
    """論文の比較用データ(Table 1 の CSV、Fig.3 のデジタイズ値)。"""
    return (root or repo_root()) / "reference" / "paper_2610.07658"


def cases_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "cases"


def runs_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "runs"


def tube_mesh_dir(root: Path | None = None) -> Path:
    return cases_dir(root) / "tube_vacuum" / "mesh"


def to_posix_relative(path: Path, start: Path) -> str:
    """``start`` からの相対パス(区切りは ``/``)。ドライブが違うなどで作れなければ絶対パス。"""
    path = Path(path)
    try:
        rel = os.path.relpath(path, start)
    except ValueError:  # Windows でドライブが違う
        return path.as_posix()
    return Path(rel).as_posix()


def display_path(path: Path | str, root: Path | None = None) -> str:
    """画面に出すパス。リポジトリの中ならルートからの相対パスにする。"""
    p = Path(path)
    root = root or repo_root()
    try:
        return str(p.resolve().relative_to(root))
    except (ValueError, OSError):
        return str(p)
