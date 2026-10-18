"""GUI のソルバー呼び出し(gsis_tools.gui.solver)。"""

from __future__ import annotations

import pytest

from gsis_tools.gui.paths import default_solver, repo_root
from gsis_tools.gui.solver import (
    RunOptions,
    build_run_args,
    explain_failure,
    parse_help,
    query_subcommands,
    split_extra_args,
)

HELP = """gsis-solver 0.1.0 (MSVC 19.51.36260.0, Release, CUDA 13.4.92, arch 120)

使い方: gsis-solver [共通オプション] <サブコマンド> [引数...]

サブコマンド:
  mesh-info   メッシュ(.pmesh)を読み、検査結果と統計を表示する
                mesh-info <file.pmesh> [--vtu <out.vtu>] [--boundary-vtu <out.vtu>] [--ascii]
  check-case  ケース定義(TOML)とメッシュを読み、導出量とメモリ量を表示する
                check-case <case.toml>
  cis-run     運動論ソルバー(CIS)だけで定常解を求める
                cis-run <case.toml> [--out <dir>] [--max-iter N] [--min-iter N] [--tol X]
                [--backend cpu|cuda] [--precision fp32|fp64] [--set key=value]...
                [--print-every N] [--vtu-every N] [--no-vtu] [--cells-csv] [--init-from <cells.csv>]
  pbc-run     PBC マクロソルバーだけ(NSF のみの単独モード)で、一様な状態から定常解を求める
                pbc-run <case.toml> [--out <dir>] [--iterations N] [--cfl X] [--cfl-start X] [--cfl-growth G]
                [--tolerance X] [--advection upwind1|upwind2] [--threads N] [--print-every N]
                [--no-vtu] [--cells-csv] [--restart <cells.csv>] [--continuity-pseudo-time]

共通オプション:
  -v, --verbose   詳細なログを出す
"""


def test_parse_help():
    subs = parse_help(HELP)
    assert list(subs) == ["mesh-info", "check-case", "cis-run", "pbc-run"]
    assert subs["cis-run"].summary.startswith("運動論ソルバー")
    assert {"--out", "--max-iter", "--tol", "--backend", "--cells-csv"} <= subs["cis-run"].flags
    assert {"--iterations", "--tolerance", "--threads"} <= subs["pbc-run"].flags
    assert "--max-iter" not in subs["pbc-run"].flags
    assert parse_help("なにもない") == {}


def test_build_run_args_maps_options():
    subs = parse_help(HELP)
    opts = RunOptions(out_dir="out dir", max_iter=5, tolerance=1e-6, backend="cpu", precision="fp64",
                      print_every=2, no_vtu=True, cells_csv=True, extra='--set limiter_k=5 "--init-from" a.csv')
    args, skipped = build_run_args("cis-run", "c.toml", opts, subs["cis-run"].flags)
    assert args == ["cis-run", "c.toml", "--out", "out dir", "--max-iter", "5", "--tol", "1e-06", "--backend", "cpu",
                    "--precision", "fp64", "--print-every", "2", "--no-vtu", "--cells-csv", "--set", "limiter_k=5",
                    "--init-from", "a.csv"]
    assert skipped == []
    args, skipped = build_run_args("pbc-run", "c.toml", opts, subs["pbc-run"].flags)
    assert args[:8] == ["pbc-run", "c.toml", "--out", "out dir", "--iterations", "5", "--tolerance", "1e-06"]
    assert "--backend" not in args and "--precision" not in args
    assert len(skipped) == 2 and all("pbc-run の使い方にない" in s for s in skipped)


def test_build_run_args_for_unknown_run_subcommand():
    """--help に run がない(フェーズ 4 の前)ときは --out だけを仮定する。"""
    args, skipped = build_run_args("run", "c.toml", RunOptions(out_dir="o", max_iter=3), None)
    assert args == ["run", "c.toml", "--out", "o"]
    assert skipped and "反復の上限" in skipped[0]


def test_split_extra_args_keeps_windows_paths():
    assert split_extra_args(r'--init-from C:\runs\a\cells.csv --set "x=1 2"') == [
        "--init-from", r"C:\runs\a\cells.csv", "--set", "x=1 2"]
    assert split_extra_args("  ") == []


def test_explain_failure():
    assert "run サブコマンドがありません" in explain_failure(2, "引数の誤り: 知らないサブコマンド run です", "run")
    assert "フェーズ 4" in explain_failure(2, "知らないサブコマンド run です", "run")
    assert "引数の誤り" in explain_failure(2, "", "cis-run")
    assert "エラー" in explain_failure(1, "")
    assert explain_failure(0, "") == "正常に終了しました"


@pytest.mark.skipif(not default_solver().is_file(), reason="gsis-solver がビルドされていない")
def test_query_real_solver():
    subs = query_subcommands(default_solver(), cwd=repo_root())
    assert "cis-run" in subs and "pbc-run" in subs and "check-case" in subs
    assert "--out" in subs["cis-run"].flags and "--out" in subs["pbc-run"].flags
