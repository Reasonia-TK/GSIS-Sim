"""格子の細分化による収束の調査。

実行(リポジトリのルートから):
    uv run --project verification/ref1d python verification/ref1d/scripts/convergence.py

出力(results/convergence/):
- spatial.csv : 空間格子 N = 50..1600(速度格子 v3、3 次補間)
- velocity.csv: 速度格子 v0..v4(N = 800)
- order.csv   : 補間次数 p = 1, 2, 3 ごとの空間収束(速度格子 v2)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from ref1d.cases import CASE_TYPES, make_problem, summarize, write_rows
from ref1d.mesh import make_mesh
from ref1d.solver import Solver
from ref1d.velocity import GL_LEVELS, gl_level

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "convergence"
KN = [0.01, 0.1, 1.0, 10.0]
KEYS = ("qx_mean", "qx_wallL", "qwall_L", "Pxy_mean", "Qtot_mean", "T_jump_L", "slip_L", "T_center", "rho_gas_L",
        "qx_spread", "Pxy_spread", "Qtot_spread", "Jmass_maxabs", "iterations", "converged", "wall_time_s")


def row(study, case, Kn, N, vlevel, p, r):
    d = summarize(r)
    out = dict(study=study, case=case, Kn=Kn, N=N, vgrid=vlevel, Nv=r.vgrid.nv, p=p)
    out.update({k: d[k] for k in KEYS})
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.perf_counter()
    # 1) 空間格子
    rows = []
    vg = gl_level("v3")
    for case in CASE_TYPES:
        for Kn in KN:
            prev = None
            for N in [50, 100, 200, 400, 800, 1600]:
                s = Solver(make_problem(case, Kn), make_mesh(N), vg, p=3)
                r = s.solve(prim0=None if prev is None else s.interpolate_from(prev))
                prev = r
                rows.append(row("spatial", case, Kn, N, "v3", 3, r))
                print(f"spatial {case} Kn={Kn} N={N} it={r.iterations} t={r.wall_time:.1f}s", flush=True)
    write_rows(rows, OUT / "spatial.csv")
    # 2) 速度格子
    rows = []
    mesh = make_mesh(800)
    for case in CASE_TYPES:
        for Kn in KN:
            prev = None
            for lv in GL_LEVELS:
                s = Solver(make_problem(case, Kn), mesh, gl_level(lv), p=3)
                r = s.solve(prim0=None if prev is None else s.interpolate_from(prev))
                prev = r
                rows.append(row("velocity", case, Kn, 800, lv, 3, r))
                print(f"velocity {case} Kn={Kn} {lv} it={r.iterations} t={r.wall_time:.1f}s", flush=True)
    write_rows(rows, OUT / "velocity.csv")
    # 3) 補間次数
    rows = []
    vg = gl_level("v2")
    for case in ("heat_nl", "couette_nl"):
        for Kn in (0.01, 0.1, 1.0):
            for p in (1, 2, 3):
                prev = None
                for N in [50, 100, 200, 400, 800, 1600]:
                    s = Solver(make_problem(case, Kn), make_mesh(N), vg, p=p)
                    r = s.solve(prim0=None if prev is None else s.interpolate_from(prev))
                    prev = r
                    rows.append(row("order", case, Kn, N, "v2", p, r))
                    print(f"order {case} Kn={Kn} p={p} N={N} it={r.iterations} t={r.wall_time:.1f}s", flush=True)
    write_rows(rows, OUT / "order.csv")
    print(f"合計 {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
