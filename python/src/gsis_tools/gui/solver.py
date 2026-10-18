"""``gsis-solver`` の呼び出し: サブコマンドの一覧(``--help`` の解析)とコマンドラインの組み立て。

ソルバーとはサブプロセスでだけやり取りする。サブコマンドの使える引数は ``--help`` の使い方の行から
読むので、フェーズ 4 の ``run`` が追加されたら、そのまま使える(使えない引数は省いて知らせる)。

Qt には依存しない(画面では QProcess で同じコマンドラインを起動する)。
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

RUN_SUBCOMMANDS = ("run", "cis-run", "pbc-run")
SUBCOMMAND_LABELS = {
    "run": "run(GSIS-PBC。フェーズ 4)",
    "cis-run": "cis-run(運動論ソルバー CIS だけ)",
    "pbc-run": "pbc-run(PBC マクロソルバーだけ、NSF のみ)",
}
# ソルバーの既定の出力先(--out を渡さないとき。カレントディレクトリからの相対パス)
DEFAULT_OUT_BASE = {"cis-run": "runs/kinetic", "pbc-run": "runs/macro", "run": "runs/gsis"}

# 画面の項目 → ソルバーの引数の候補(使い方の行にあるものを使う)
FLAG_CANDIDATES = {
    "out": ("--out",),
    "max_iter": ("--max-iter", "--iterations", "--max-iterations"),
    "tolerance": ("--tol", "--tolerance"),
    "backend": ("--backend",),
    "precision": ("--precision",),
    "print_every": ("--print-every",),
    "no_vtu": ("--no-vtu",),
    "cells_csv": ("--cells-csv",),
}
# --help が読めないときに仮定する引数
FALLBACK_FLAGS = {
    "cis-run": {"--out", "--max-iter", "--tol", "--backend", "--precision", "--print-every", "--no-vtu", "--cells-csv"},
    "pbc-run": {"--out", "--iterations", "--tolerance", "--print-every", "--no-vtu", "--cells-csv"},
    "run": {"--out"},
}

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


@dataclass
class SubcommandInfo:
    name: str
    summary: str = ""
    usage: str = ""
    flags: set[str] = field(default_factory=set)


def parse_help(text: str) -> dict[str, SubcommandInfo]:
    """``gsis-solver --help`` の出力からサブコマンドの一覧を作る。

    形式(``src/app/main.cpp`` の ``print_usage``): 「サブコマンド:」の後に、2 文字下げた行に
    「名前  説明」、さらに下げた行に使い方が続く。空行か下げのない行で終わる。
    """
    out: dict[str, SubcommandInfo] = {}
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("サブコマンド"))
    except StopIteration:
        return out
    current: SubcommandInfo | None = None
    for line in lines[start + 1 :]:
        if not line.strip():
            if current is not None and out:
                break
            continue
        if not line.startswith(" "):
            break
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if indent <= 2:
            parts = body.split(None, 1)
            current = SubcommandInfo(parts[0], parts[1].strip() if len(parts) > 1 else "")
            out[current.name] = current
        elif current is not None:
            current.usage = (current.usage + "\n" + body).strip()
    for info in out.values():
        info.flags = set(re.findall(r"--[a-z][a-z0-9-]*", info.usage))
    return out


def run_capture(args: list[str], cwd: Path | None = None, timeout: float = 60.0) -> subprocess.CompletedProcess:
    """ソルバーを同期で実行して出力を受ける(UTF-8)。画面からは使わない(テスト・スクリプト用)。"""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=flags,
    )


def query_subcommands(exe: Path, cwd: Path | None = None, timeout: float = 20.0) -> dict[str, SubcommandInfo]:
    """``--help`` を実行してサブコマンドの一覧を得る。実行できなければ空の辞書。"""
    try:
        cp = run_capture([str(exe), "--help"], cwd=cwd, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_help(cp.stdout)


def split_extra_args(text: str) -> list[str]:
    """「追加の引数」の文字列を分ける(引用符で囲めば空白を含められる。Windows のパスの \\ はそのまま)。"""
    text = text.strip()
    if not text:
        return []
    parts = shlex.split(text, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


@dataclass
class RunOptions:
    """実行の画面の項目(None・False は渡さない)。"""

    out_dir: str | None = None
    max_iter: int | None = None
    tolerance: float | None = None
    backend: str | None = None
    precision: str | None = None
    print_every: int | None = None
    no_vtu: bool = False
    cells_csv: bool = False
    extra: str = ""


def pick_flag(item: str, flags: set[str]) -> str | None:
    for f in FLAG_CANDIDATES[item]:
        if f in flags:
            return f
    return None


def build_run_args(
    subcommand: str,
    case_path: str | Path,
    opts: RunOptions,
    flags: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """サブコマンド以降の引数と、使えずに省いた項目の説明の一覧を返す。

    ``flags`` はそのサブコマンドの使い方にある引数(:func:`parse_help`)。None なら
    :data:`FALLBACK_FLAGS` を仮定する。
    """
    flags = set(flags) if flags is not None else set(FALLBACK_FLAGS.get(subcommand, set()))
    args = [subcommand, str(case_path)]
    skipped: list[str] = []

    def add(item: str, value: str | None = None, label: str = "") -> None:
        f = pick_flag(item, flags)
        if f is None:
            skipped.append(f"{label or item}({subcommand} の使い方にない)")
            return
        args.append(f)
        if value is not None:
            args.append(value)

    if opts.out_dir:
        add("out", str(opts.out_dir), "出力先 --out")
    if opts.max_iter is not None:
        add("max_iter", str(int(opts.max_iter)), "反復の上限")
    if opts.tolerance is not None:
        add("tolerance", repr(float(opts.tolerance)), "収束判定")
    if opts.backend:
        add("backend", opts.backend, "実行先 --backend")
    if opts.precision:
        add("precision", opts.precision, "精度 --precision")
    if opts.print_every is not None:
        add("print_every", str(int(opts.print_every)), "表示の間隔 --print-every")
    if opts.no_vtu:
        add("no_vtu", None, "VTU を書かない --no-vtu")
    if opts.cells_csv:
        add("cells_csv", None, "cells.csv を書く --cells-csv")
    args += split_extra_args(opts.extra)
    return args, skipped


def default_out_dir(subcommand: str, case_name: str, root: Path) -> Path:
    """ソルバーの既定の出力先(``runs/kinetic/<ケース名>`` など)。"""
    return root / DEFAULT_OUT_BASE.get(subcommand, "runs/gsis") / case_name


def explain_failure(exit_code: int, output: str, subcommand: str | None = None) -> str:
    """終了コードと出力から、利用者向けの説明を作る。"""
    if exit_code == EXIT_OK:
        return "正常に終了しました"
    if "知らないサブコマンド" in output:
        name = subcommand or "指定した"
        extra = "(run はフェーズ 4 で追加済みなので、それより前に作ったソルバーです)" if name == "run" else ""
        return f"このソルバーには {name} サブコマンドがありません{extra}。ソルバーを作り直すか、別のサブコマンドを選んでください"
    if exit_code == EXIT_USAGE:
        return "引数の誤りで止まりました(ログの「引数の誤り」の行を見てください)"
    if exit_code == EXIT_ERROR:
        return "エラーで止まりました(ログの「エラー」の行を見てください)"
    return f"終了コード {exit_code} で止まりました(強制終了を含む)"
