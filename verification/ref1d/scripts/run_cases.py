"""主要ケース(熱伝導 2 種 × クエット流 2 種 × Kn)を計算し、分布と要約の CSV を書く。

実行(リポジトリのルートから):
    uv run --project verification/ref1d python verification/ref1d/scripts/run_cases.py

各ケースを本番の格子(既定: N = 800 セル、速度格子 v3、3 次補間)と、誤差の目安用の粗い格子
(N = 400、v2)で解き、両者の差を「誤差の目安(上限側)」として要約に書く。

3 次元ソルバーと同じ速度格子(ξx 方向が区間中点の一様格子 30 点、範囲 ±10 √(RT0))での参照値:
    uv run --project verification/ref1d python verification/ref1d/scripts/run_cases.py \\
        --vgrid midpoint:30:10 --vgrid-check same --tag _vmid30
(要約は results/summary_vmid30.csv、分布は results/profiles_vmid30/)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from ref1d.cases import CASE_TYPES, KN_LIST, kn_tag, make_problem, summarize, write_profile, write_rows
from ref1d.mesh import make_mesh
from ref1d.solver import Solver
from ref1d.velocity import gauss_legendre, gl_level, midpoint

ROOT = Path(__file__).resolve().parents[1]


def parse_vgrid(spec: str):
    """速度格子の指定: "v3" などの水準名、または "midpoint:30:10"(点数:範囲)。"""
    if spec.startswith("midpoint:"):
        _, n, xm = spec.split(":")
        return midpoint(int(n), float(xm))
    if spec.startswith("gl:"):
        # gl:xi_max:panel:n_gl:n_geo:ratio
        _, xm, pw, ng, nge, ra = spec.split(":")
        return gauss_legendre(float(xm), float(pw), int(ng), int(nge), float(ra))
    return gl_level(spec)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--N", type=int, default=800, help="本番の格子のセル数")
    ap.add_argument("--vgrid", default="v3", help="本番の速度格子")
    ap.add_argument("--N-check", type=int, default=400, help="誤差の目安用の格子のセル数(0 で省略)")
    ap.add_argument("--vgrid-check", default="v2", help="誤差の目安用の速度格子(same なら本番と同じ速度格子)")
    ap.add_argument("--cases", nargs="*", default=list(CASE_TYPES), help="計算するケース")
    ap.add_argument("--kn", nargs="*", type=float, default=KN_LIST, help="Kn のリスト")
    ap.add_argument("--out", default=str(ROOT / "results"), help="出力先")
    ap.add_argument("--tag", default="", help="出力ファイル名に付ける接尾辞(格子を変えた計算用)")
    ap.add_argument("--no-profiles", action="store_true", help="分布の CSV を書かない")
    args = ap.parse_args()

    out = Path(args.out)
    vg = parse_vgrid(args.vgrid)
    vgc = vg if args.vgrid_check == "same" else parse_vgrid(args.vgrid_check)
    mesh = make_mesh(args.N)
    meshc = make_mesh(args.N_check) if args.N_check > 0 else None
    print(f"格子: {mesh.describe()} / {vg.describe()}")
    if meshc is not None:
        print(f"確認用: {meshc.describe()} / {vgc.describe()}")

    rows = []
    t_all = time.perf_counter()
    for case in args.cases:
        prev = prevc = None
        for Kn in sorted(args.kn, reverse=True):  # 大きい Kn から順に解き、前の解を初期値にする
            pr = make_problem(case, Kn)
            s = Solver(pr, mesh, vg)
            r = s.solve(prim0=None if prev is None else s.interpolate_from(prev))
            prev = r
            d = summarize(r)
            if meshc is not None:
                sc = Solver(pr, meshc, vgc)
                rc = sc.solve(prim0=None if prevc is None else sc.interpolate_from(prevc))
                prevc = rc
                dc = summarize(rc)
                # 相対差。クエット流の q_x の平均と全エネルギー流束は(対称性で)ほぼ 0 なので、壁での値と P_xy で見る。
                # 熱伝導の P_xy は 0
                keys = ("qx_mean", "qx_wallL", "qwall_L", "Qtot_mean") if case.startswith("heat") else ("qx_wallL", "qwall_L", "Pxy_mean")
                for key in ("qx_mean", "qx_wallL", "qwall_L", "Pxy_mean", "Qtot_mean"):
                    d[f"{key}_diff_check_rel"] = abs(d[key] - dc[key]) / abs(d[key]) if key in keys else float("nan")
                for key in ("T_jump_L", "T_jump_R", "slip_L", "slip_R", "T_center"):
                    d[f"{key}_diff_check_abs"] = abs(d[key] - dc[key])
                d["check_grid"] = f"N={args.N_check},{vgc.describe()}"
            rows.append(d)
            if not args.no_profiles:
                # 格子を変えた計算(--tag)は profiles{tag}/ に分けて置く
                write_profile(r, out / f"profiles{args.tag}" / f"{case}_{kn_tag(Kn)}.csv")
            print(
                f"{case:12s} Kn={Kn:7.3f} it={r.iterations:5d} conv={r.converged} "
                f"qx={d['qx_mean']:+.10e} Pxy={d['Pxy_mean']:+.10e} "
                f"dq={d.get('qx_wallL_diff_check_rel', np.nan):.1e} dP={d.get('Pxy_mean_diff_check_rel', np.nan):.1e} "
                f"t={r.wall_time:.1f}s",
                flush=True,
            )
    write_rows(rows, out / f"summary{args.tag}.csv")
    print(f"合計 {time.perf_counter() - t_all:.1f} s")


if __name__ == "__main__":
    main()
