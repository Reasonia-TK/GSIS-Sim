"""作業フォルダ(workspace)の到達範囲の制限。

MCP のツールが書き出すファイルは、すべて作業フォルダの中に限る。読み込み(CAD の取り込み、
状態の読み込み)は、作業フォルダと、起動時に ``--read-dir`` で足したフォルダの中に限る。

拒否するもの(Windows で別名になり得る書き方を含む):

- 空のパス、NUL 文字、UNC(``\\\\server\\share``)とデバイス名前空間(``\\\\?\\``、``\\\\.\\``)
- ``..`` を含むパス(解決後に中に収まっても、意図が読めないので拒否する)
- ドライブ相対(``C:foo``)、代替データストリーム(``a.txt:stream``)など、名前の中の ``:``
- 予約されたデバイス名(``CON``、``NUL``、``COM1`` など。拡張子付きも)
- 末尾が ``.`` か空白の名前、8.3 形式の短い名前(``PROGRA~1`` など)、``<>"|?*``
- 解決後(シンボリックリンク・ジャンクションをたどった後)に作業フォルダの外になるパス
- 内部用のフォルダ ``.gsis_mcp`` への書き出し
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePath, PureWindowsPath

from .errors import ToolFailure

INTERNAL_DIR = ".gsis_mcp"

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_RESERVED |= {f"COM{i}" for i in range(10)} | {f"LPT{i}" for i in range(10)}
_RESERVED |= {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
_SHORT_NAME = re.compile(r"~\d")
_BAD_CHARS = set('<>"|?*')


class PathRejected(ToolFailure):
    """作業フォルダの制限に反するパス。"""


def _check_component(part: str, original: str) -> None:
    if part in ("", "."):
        return
    if part == "..":
        raise PathRejected(f"'..' を含むパスは使えません: {original}")
    if ":" in part:
        raise PathRejected(f"名前に ':' を含むパス(ドライブ相対・代替データストリーム)は使えません: {original}")
    if any(c in _BAD_CHARS or ord(c) < 32 for c in part):
        raise PathRejected(f"使えない文字を含むパスです: {original}")
    if part[-1] in ". ":
        raise PathRejected(f"末尾が '.' か空白の名前は使えません: {original}")
    stem = part.split(".")[0].rstrip(" ").upper()
    if stem in _RESERVED:
        raise PathRejected(f"予約されたデバイス名は使えません: {original}")
    if _SHORT_NAME.search(part):
        raise PathRejected(f"8.3 形式の短い名前(~ と数字)は使えません: {original}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:  # pragma: no cover - ドライブが違う
        return False


class Workspace:
    """作業フォルダと、読み込みを許すフォルダ。"""

    def __init__(self, root: str | os.PathLike, read_dirs: list[str | os.PathLike] | None = None):
        r = Path(root).expanduser()
        r.mkdir(parents=True, exist_ok=True)
        self.root = r.resolve(strict=True)
        self.read_dirs = [Path(d).expanduser().resolve(strict=True) for d in (read_dirs or [])]
        for d in self.read_dirs:
            if not d.is_dir():
                raise PathRejected(f"読み込みを許すフォルダがありません: {d}")

    # ------------------------------------------------------------------

    @property
    def internal(self) -> Path:
        """内部用のフォルダ(取り込んだ CAD の写しなど)。"""
        p = self.root / INTERNAL_DIR
        p.mkdir(exist_ok=True)
        return p

    def relative(self, path: Path) -> str:
        """作業フォルダからの相対パス(区切りは /)。外なら絶対パス。"""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _parse(self, p: str) -> PurePath:
        if not isinstance(p, str) or not p.strip():
            raise PathRejected("パスが空です")
        if "\x00" in p:
            raise PathRejected("パスに NUL 文字があります")
        if p.startswith(("\\\\", "//")):
            raise PathRejected(f"UNC パスとデバイスのパスは使えません: {p}")
        pp = PureWindowsPath(p) if os.name == "nt" else PurePath(p)
        parts = list(pp.parts)
        if pp.anchor:
            parts = parts[1:]
        for part in parts:
            _check_component(part, p)
        return pp

    def _resolve(self, p: str) -> Path:
        pp = self._parse(p)
        if os.name == "nt" and pp.drive and not pp.root:
            raise PathRejected(f"ドライブ相対のパス(例 C:foo)は使えません: {p}")
        path = Path(pp) if pp.is_absolute() else self.root / pp
        return path.resolve(strict=False)

    @staticmethod
    def _check_suffix(path: Path, suffixes: tuple[str, ...] | None, original: str) -> None:
        if suffixes and path.suffix.lower() not in suffixes:
            raise PathRejected(f"拡張子は {', '.join(suffixes)} のいずれかにしてください: {original}")

    def out_path(self, p: str, suffixes: tuple[str, ...] | None = None) -> Path:
        """書き出し先のパス(作業フォルダの中に限る)。親フォルダは作る。"""
        path = self._resolve(p)
        if not _is_within(path, self.root) or path == self.root:
            raise PathRejected(f"作業フォルダ({self.root})の外には書き出せません: {p}")
        rel = path.relative_to(self.root)
        if rel.parts and rel.parts[0].lower() == INTERNAL_DIR:
            raise PathRejected(f"内部用のフォルダ {INTERNAL_DIR} には書き出せません: {p}")
        self._check_suffix(path, suffixes, p)
        if path.exists() and path.is_dir():
            raise PathRejected(f"フォルダには書き出せません: {p}")
        path.parent.mkdir(parents=True, exist_ok=True)
        # フォルダを作った後にもう一度確かめる(途中にリンクがあった場合)
        if not _is_within(path.parent.resolve(strict=True), self.root):
            raise PathRejected(f"作業フォルダの外には書き出せません: {p}")
        return path

    def in_path(self, p: str, suffixes: tuple[str, ...] | None = None) -> Path:
        """読み込むファイルのパス(作業フォルダか、読み込みを許すフォルダの中に限る)。"""
        path = self._resolve(p)
        allowed = [self.root, *self.read_dirs]
        if not any(_is_within(path, d) for d in allowed):
            where = "、".join(str(d) for d in allowed)
            raise PathRejected(f"読み込めるのは作業フォルダなど({where})の中のファイルだけです: {p}")
        self._check_suffix(path, suffixes, p)
        if not path.is_file():
            raise PathRejected(f"ファイルがありません: {p}")
        return path
