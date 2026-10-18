"""検証ケースの定義、要約量の計算、CSV の書き出し。"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import analytic
from .solver import Problem, Result

# 3 次元ソルバーの検証に使うケース(無次元。温度は T0、速度は sqrt(R T0) が単位)
CASE_TYPES: dict[str, dict] = {
    # 熱伝導(静止壁)
    "heat_lin": dict(T_L=0.95, T_R=1.05),
    "heat_nl": dict(T_L=0.5, T_R=1.5),
    # クエット流(等温壁、左の壁が -u_w、右の壁が +u_w で y 方向に動く)
    "couette_lin": dict(u_L=-0.1, u_R=0.1),
    "couette_nl": dict(u_L=-1.0, u_R=1.0),
}

KN_LIST = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]


def make_problem(case: str, Kn: float, omega: float = 0.66, Pr: float = 2.0 / 3.0) -> Problem:
    return Problem(Kn=Kn, omega=omega, Pr=Pr, name=case, **CASE_TYPES[case])


def kn_tag(Kn: float) -> str:
    """ファイル名用の Kn の表記(例: 0.05 → Kn0p05、10 → Kn10)。"""
    if not math.isfinite(Kn):
        return "Kninf"
    s = f"{Kn:g}".replace(".", "p").replace("-", "m").replace("+", "")
    return f"Kn{s}"


@dataclass
class Summary:
    values: dict

    def __getitem__(self, k):
        return self.values[k]


def summarize(r: Result) -> dict:
    """1 ケースの要約量。"""
    pr = r.problem
    x = r.x
    rho, uy, T = r.col("rho"), r.col("uy"), r.col("T")
    qx, Pxy, Qtot = r.col("qx"), r.col("Pxy"), r.col("Qtot")
    Pxx, J = r.col("Pxx"), r.col("Jmass")
    # 区分的 3 次補間の積分による平均(区間 [0,1] なので積分 = 平均)
    from .mesh import nodal_quadrature_weights

    wq = nodal_quadrature_weights(x, 3)
    fm = analytic.free_molecular(pr)
    d = dict(
        case=pr.name,
        Kn=pr.Kn,
        delta=pr.delta,
        T_L=pr.T_L,
        T_R=pr.T_R,
        u_L=pr.u_L,
        u_R=pr.u_R,
        omega=pr.omega,
        Pr=pr.Pr,
        # 熱流束(x 方向)。厳密解では熱伝導ケースで一様
        qx_mean=float(wq @ qx),
        qx_wallL=float(qx[0]),
        qx_wallR=float(qx[-1]),
        qx_spread=float(np.ptp(qx)),
        # 壁の座標系での熱流束(= 全エネルギー流束 - 壁速度 × P_xy、+x 向き)
        qwall_L=float(wq @ Qtot - pr.u_L * (wq @ Pxy)),
        qwall_R=float(wq @ Qtot - pr.u_R * (wq @ Pxy)),
        # せん断応力(一様)
        Pxy_mean=float(wq @ Pxy),
        Pxy_spread=float(np.ptp(Pxy)),
        # 全エネルギー流束 q_x + u_y P_xy(一様)
        Qtot_mean=float(wq @ Qtot),
        Qtot_spread=float(np.ptp(Qtot)),
        Pxx_mean=float(wq @ Pxx),
        Pxx_spread=float(np.ptp(Pxx)),
        Jmass_maxabs=float(np.max(np.abs(J))),
        # 壁での気体の値(壁の節点で、流入する壁 Maxwell 分布と流出する分布の両方から計算)
        T_gas_L=float(T[0]),
        T_gas_R=float(T[-1]),
        T_jump_L=float(T[0] - pr.T_L),
        T_jump_R=float(T[-1] - pr.T_R),
        uy_gas_L=float(uy[0]),
        uy_gas_R=float(uy[-1]),
        slip_L=float(uy[0] - pr.u_L),
        slip_R=float(uy[-1] - pr.u_R),
        rho_gas_L=float(rho[0]),
        rho_gas_R=float(rho[-1]),
        p_gas_L=float(rho[0] * T[0]),
        p_gas_R=float(rho[-1] * T[-1]),
        T_center=float(np.interp(0.5, x, T)),
        rho_center=float(np.interp(0.5, x, rho)),
        p_center=float(np.interp(0.5, x, rho * T)),
        n_wall_L=r.nL,
        n_wall_R=r.nR,
        qx_fm=fm.qx,
        Pxy_fm=fm.Pxy,
        qx_over_fm=float(wq @ qx) / fm.qx if fm.qx != 0.0 else float("nan"),
        Pxy_over_fm=float(wq @ Pxy) / fm.Pxy if fm.Pxy != 0.0 else float("nan"),
        N_cells=r.mesh.n_cells,
        Nv=r.vgrid.nv,
        vgrid=r.vgrid.describe(),
        p_interp=r.p,
        iterations=r.iterations,
        residual=r.residual,
        converged=r.converged,
        wall_time_s=r.wall_time,
    )
    return d


PROFILE_COLUMNS = ("x", "rho", "ux", "uy", "T", "p", "qx", "qy", "Pxx", "Pxy", "Pyy", "Pzz")


def write_profile(r: Result, path: Path, extra_header: list[str] | None = None) -> None:
    """分布の CSV(先頭の # 行に条件を書く)。"""
    pr = r.problem
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        r.x,
        r.col("rho"),
        r.col("ux"),
        r.col("uy"),
        r.col("T"),
        r.col("rho") * r.col("T"),
        r.col("qx"),
        r.col("qy"),
        r.col("Pxx"),
        r.col("Pxy"),
        r.col("Pyy"),
        r.col("Pzz"),
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# case={pr.name} Kn={pr.Kn:g} delta={pr.delta:.10g} T_L={pr.T_L:g} T_R={pr.T_R:g} u_L={pr.u_L:g} u_R={pr.u_R:g} omega={pr.omega:g} Pr={pr.Pr:.10g}\n")
        fh.write(f"# grid: {r.mesh.describe()} / {r.vgrid.describe()} / p={r.p}\n")
        # データファイルはどの環境(既定の文字コードが CP932 など)でも読めるように ASCII だけで書く
        fh.write("# nondimensional: x/L, rho/rho0, u/sqrt(R T0), T/T0, p/p0 (p0 = rho0 R T0, p = rho T), q/(p0 sqrt(R T0)), P_ij/p0\n")
        for line in extra_header or []:
            fh.write(f"# {line}\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(PROFILE_COLUMNS)
        for row in zip(*cols):
            w.writerow([f"{v:.12e}" for v in row])


def read_profile(path: Path) -> dict[str, np.ndarray]:
    """write_profile で書いた分布の CSV を読む(# で始まる行は飛ばす)。列名 → 配列 の辞書を返す。"""
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    names = [c.strip() for c in lines[0].split(",")]
    data = np.loadtxt(lines[1:], delimiter=",", ndmin=2)
    return {n: data[:, i] for i, n in enumerate(names)}


def write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.15e}" if isinstance(v, float) else v) for k, v in r.items()})


def read_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            try:
                d[k] = float(v)
            except (TypeError, ValueError):
                d[k] = v
        out.append(d)
    return out
