"""参照ソルバー自体の検証。

実行(リポジトリのルートから):
    uv run --project verification/ref1d python verification/ref1d/scripts/verify.py [fm] [nsf] [coef] [lit]

出力(results/verification/):
- free_molecular.csv : 大きい Kn での自由分子解析解との差
- nsf.csv            : 小さい Kn での NSF + すべり・温度跳躍の解との比較
- slip_jump.csv      : 線形化した問題の解から逆算した速度すべり係数・温度跳躍係数(BGK と S モデル)
- literature.csv     : 文献の表との比較(線形化した BGK / S モデル)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from ref1d import analytic as A
from ref1d.cases import CASE_TYPES, make_problem, summarize, write_rows
from ref1d.mesh import cell_integration_weights, make_mesh
from ref1d.solver import Problem, Solver
from ref1d.velocity import gauss_legendre, gl_level

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "verification"
N_DEFAULT = 800
V_DEFAULT = "v3"


def solve(pr: Problem, N=N_DEFAULT, vlevel=V_DEFAULT, prev=None, **kw):
    s = Solver(pr, make_mesh(N), gl_level(vlevel), p=3)
    return s.solve(prim0=None if prev is None else s.interpolate_from(prev), **kw)


# ----------------------------------------------------------------------------
def study_free_molecular() -> None:
    """自由分子極限: Kn = ∞ と大きい Kn で解析解と比べる。

    非線形ケースでは、壁付近の密度の (ln Kn)/Kn 型の補正が全質量の規格化を通して q_x, P_xy にも現れる。
    その対数特異性は |ξx| ≈ νL(Kn = 1e6 で約 1e-6)にあるので、ξx = 0 付近を 12 段まで細かくした速度格子を使う。
    """
    rows = []
    vg = gauss_legendre(10.0, 0.5, 8, 12, 0.25)
    for case in CASE_TYPES:
        for Kn in [np.inf, 1e6, 1e5, 1e4, 1e3, 1e2, 50.0, 20.0, 10.0]:
            pr = make_problem(case, Kn)
            r = Solver(pr, make_mesh(200), vg, p=3).solve()
            d = summarize(r)
            fm = A.free_molecular(pr)
            rows.append(
                dict(
                    case=case,
                    Kn=Kn,
                    delta=pr.delta,
                    qx_mean=d["qx_mean"],
                    qx_fm=fm.qx,
                    qx_rel_dev=(d["qx_mean"] - fm.qx) / fm.qx if fm.qx != 0 else float("nan"),
                    Qtot_mean=d["Qtot_mean"],
                    Qtot_fm=fm.Qtot,
                    Qtot_rel_dev=(d["Qtot_mean"] - fm.Qtot) / fm.Qtot if fm.Qtot != 0 else float("nan"),
                    Pxy_mean=d["Pxy_mean"],
                    Pxy_fm=fm.Pxy,
                    Pxy_rel_dev=(d["Pxy_mean"] - fm.Pxy) / fm.Pxy if fm.Pxy != 0 else float("nan"),
                    T_center=d["T_center"],
                    T_fm=fm.T,
                    T_dev=d["T_center"] - fm.T,
                    rho_center=d["rho_center"],
                    rho_dev=d["rho_center"] - fm.rho,
                    n_wall_L=d["n_wall_L"],
                    nL_fm=fm.nL,
                    n_wall_R=d["n_wall_R"],
                    nR_fm=fm.nR,
                    iterations=r.iterations,
                )
            )
            x = rows[-1]
            print(f"fm {case} Kn={Kn:g}: dq={x['qx_rel_dev']:+.2e} dP={x['Pxy_rel_dev']:+.2e} dT={x['T_dev']:+.2e}", flush=True)
    write_rows(rows, OUT / "free_molecular.csv")


# ----------------------------------------------------------------------------
def study_nsf() -> None:
    """連続体に近い極限: NSF + 1 次のすべり・温度跳躍(S モデルの係数)の解と比べる。"""
    rows = []
    kns = [0.1, 0.05, 0.02, 0.01, 0.005, 0.0025]
    for case in CASE_TYPES:
        prev = None
        for Kn in kns:
            pr = make_problem(case, Kn)
            N = 800 if Kn >= 0.005 else 1600
            r = solve(pr, N=N, prev=prev)
            prev = r
            d = summarize(r)
            ns = A.nsf_slip(pr)  # S モデルの係数(σ_P = 1.016191, ζ_T = 1.95408)
            ns0 = A.nsf_slip(pr, sigma_P=0.0, zeta_T=0.0)  # すべり・跳躍なし
            kin_q = d["qx_mean"] if case.startswith("heat") else d["qx_wallL"]
            nsf_q = float(np.mean(ns.qx)) if case.startswith("heat") else float(ns.qx[0])
            nsf0_q = float(np.mean(ns0.qx)) if case.startswith("heat") else float(ns0.qx[0])
            row = dict(
                case=case,
                Kn=Kn,
                delta=pr.delta,
                N=N,
                kin_qx=kin_q,
                nsf_qx=nsf_q,
                nsf_noslip_qx=nsf0_q,
                qx_rel_diff=(kin_q - nsf_q) / nsf_q,
                kin_Pxy=d["Pxy_mean"],
                nsf_Pxy=ns.Pxy,
                nsf_noslip_Pxy=ns0.Pxy,
                Pxy_rel_diff=(d["Pxy_mean"] - ns.Pxy) / ns.Pxy if ns.Pxy != 0 else float("nan"),
                kin_T_center=d["T_center"],
                nsf_T_center=float(np.interp(0.5, ns.x, ns.T)),
                kin_p_center=d["p_center"],
                nsf_p=ns.p,
                kin_T_gas_L=d["T_gas_L"],
                nsf_T_L=float(ns.T[0]),
                kin_uy_gas_L=d["uy_gas_L"],
                nsf_u_L=float(ns.u[0]),
                iterations=r.iterations,
                nsf_success=ns.success,
            )
            rows.append(row)
            print(f"nsf {case} Kn={Kn}: dq={row['qx_rel_diff']:+.2e} dP={row['Pxy_rel_diff']:+.2e} it={r.iterations} t={r.wall_time:.1f}s", flush=True)
    write_rows(rows, OUT / "nsf.csv")


# ----------------------------------------------------------------------------
def study_coefficients(eps: float = 1e-4) -> None:
    """線形化した熱伝導・クエット流の解から、温度跳躍係数 ζ_T と速度すべり係数 σ_P を逆算する。

    線形の大きい δ では、q/q_fm = (5 sqrt(π)/(4 Pr))/(δ + 2ζ_T)、P/P_fm = sqrt(π)/(δ + 2σ_P) が
    クヌーセン層どうしの干渉(δ について代数的より速く小さくなる)を除いて成り立つ。
    """
    rows = []
    for Pr, model in [(1.0, "BGK"), (2.0 / 3.0, "S")]:
        prev_h = prev_c = None
        for Kn in [0.05, 0.02, 0.01, 0.005]:
            d = np.sqrt(np.pi) / (2 * Kn)
            N = 800 if Kn >= 0.01 else 1600
            rh = solve(Problem(Kn=Kn, T_L=1 - eps, T_R=1 + eps, Pr=Pr), N=N, prev=prev_h)
            rc = solve(Problem(Kn=Kn, u_L=-eps, u_R=eps, Pr=Pr), N=N, prev=prev_c)
            prev_h, prev_c = rh, rc
            sh, sc = summarize(rh), summarize(rc)
            q_ratio = sh["qx_mean"] / (-np.sqrt(2 / np.pi) * 2 * eps)
            P_ratio = sc["Pxy_mean"] / (-eps * np.sqrt(2 / np.pi))
            zeta = 0.5 * ((5 * np.sqrt(np.pi) / (4 * Pr)) / q_ratio - d)
            sigma = 0.5 * (np.sqrt(np.pi) / P_ratio - d)
            rows.append(dict(model=model, Pr=Pr, Kn=Kn, delta=d, N=N, eps=eps, q_over_qfm=q_ratio, P_over_Pfm=P_ratio,
                             zeta_T=zeta, sigma_P=sigma, iterations_heat=rh.iterations, iterations_couette=rc.iterations))
            print(f"coef {model} Kn={Kn}: zeta_T={zeta:.6f} sigma_P={sigma:.6f}", flush=True)
    write_rows(rows, OUT / "slip_jump.csv")


# ----------------------------------------------------------------------------
# 文献の表との比較
LIT_EPS = 1e-4  # 線形化した問題の代わりに解く摂動の大きさ(非線形の補正は相対 O(ε²) = 1e-8)
LIT_VGRID = dict(xi_max=10.0, panel=0.5, n_gl=8, n_geo=12, ratio=0.25)  # 小さい δ の対数特異性まで解像する


def _lit_mesh_N(delta: float) -> int:
    return 800 if delta <= 20.0 else 1600


class _LinearRuns:
    """(モデル, 問題, δ) ごとに線形化に近い小さな摂動の解を 1 回だけ計算して使い回す。"""

    def __init__(self):
        self.cache: dict = {}
        self.vg = gauss_legendre(**LIT_VGRID)

    def get(self, model: str, problem: str, delta: float):
        key = (model, problem, delta)
        if key not in self.cache:
            Pr = 1.0 if model == "BGK" else 2.0 / 3.0
            Kn = np.sqrt(np.pi) / (2.0 * delta)
            e = LIT_EPS
            if problem == "heat":
                pr = Problem(Kn=Kn, T_L=1 - e, T_R=1 + e, Pr=Pr)
            else:
                pr = Problem(Kn=Kn, u_L=-e, u_R=e, Pr=Pr)
            r = Solver(pr, make_mesh(_lit_mesh_N(delta)), self.vg, p=3).solve()
            self.cache[key] = r
            print(f"  lit run {model} {problem} delta={delta:g}: it={r.iterations} t={r.wall_time:.1f}s", flush=True)
        return self.cache[key]


def _half_mean(x: np.ndarray, f: np.ndarray) -> float:
    """x ∈ [0.5, 1] での平均(区分 3 次補間の積分)。x = 0.5 が節点であること。"""
    st, W = cell_integration_weights(x, 3)
    i0 = int(np.argmin(np.abs(x - 0.5)))
    assert abs(x[i0] - 0.5) < 1e-13
    tot = 0.0
    for c in range(i0, x.size - 1):
        tot += float(W[c] @ f[st[c] : st[c] + 4])
    return tot / 0.5


def _lit_value(tab: dict, r, delta: float, param):
    """文献の量に合わせて本ソルバーの解を換算する(定義は literature_data.py の冒頭)。"""
    from scipy.interpolate import CubicSpline

    e = LIT_EPS
    x = r.x
    q = tab["quantity"]
    if q == "P_xy/P_xy,fm":
        return summarize(r)["Pxy_mean"] / (-e * np.sqrt(2.0 / np.pi))
    if q == "q_x/q_x,fm":
        return summarize(r)["qx_mean"] / (-np.sqrt(2.0 / np.pi) * 2.0 * e)
    if q.startswith("u_C(τ/a)"):
        return float(-CubicSpline(x, r.col("uy"))(0.5 * (1.0 + param)) / e)
    if q.startswith("q_C(τ/a)"):
        return float(-CubicSpline(x, r.col("qy"))(0.5 * (1.0 + param)) / e)
    if q == "U_C":
        return -_half_mean(x, r.col("uy")) / e / delta
    if q == "Q_C":
        return -_half_mean(x, r.col("qy")) / e / delta
    if q.startswith("(T-T0)/ΔT") or q.startswith("(n-n0)/n0"):
        # 分布は ε の奇関数ではないので、(f - 1)/ε には O(ε) の対称な非線形成分が残る。
        # 壁温を入れ替えた解は鏡映 f(x; -ε) = f(1-x; ε) なので、反対称成分 [f(x) - f(1-x)]/(2ε) が線形の部分になる
        f = CubicSpline(x, r.col("T" if q.startswith("(T") else "rho"))
        xo = 0.5 - param
        return float((f(xo) - f(1.0 - xo)) / (2.0 * e))
    if q.startswith("Q_x (δ1=8, δ2=1)"):
        return summarize(r)["qx_mean"] / (-np.sqrt(2.0 / np.pi) * 2.0 * e) * 7.0 / np.sqrt(np.pi)
    raise ValueError(q)


def _unit(ref: float, tab: dict) -> float:
    """文献値の最後の桁の 1 単位。"""
    kind, n = tab.get("precision", ("sig", 6))[:2]
    if kind == "dec":
        return 10.0 ** (-n)
    if ref == 0.0:
        return 0.0
    return 10.0 ** (np.floor(np.log10(abs(ref))) - (n - 1))


def _agrees(val: float, ref: float, tab: dict) -> bool:
    """文献の桁数の範囲で一致するか(四捨五入: |差| ≤ 0.5 単位、切り捨て: 0 ≤ |val| - |ref| < 1 単位)。"""
    u = _unit(ref, tab)
    prec = tab.get("precision", ("sig", 6))
    if len(prec) > 2 and prec[2] == "trunc":
        return (np.sign(val) == np.sign(ref)) and (0.0 <= abs(val) - abs(ref) < u * (1.0 + 1e-9))
    return abs(val - ref) <= 0.5 * u * (1.0 + 1e-9)


def study_literature() -> None:
    """文献の表(literature_data.py)との比較。"""
    from literature_data import LINEAR, NONLINEAR, SOURCES  # scripts/ にある

    runs = _LinearRuns()
    rows = []
    for tab in LINEAR:
        for delta, param, ref in tab["rows"]:
            r = runs.get(tab["model"], tab["problem"], delta)
            val = _lit_value(tab, r, delta, param)
            u = _unit(ref, tab)
            ok = _agrees(val, ref, tab)
            prec = tab.get("precision", ("sig", 6))
            rows.append(
                dict(
                    source=tab["source"],
                    table=tab["table"],
                    model=tab["model"],
                    quantity=tab["quantity"],
                    delta=delta,
                    param="" if param is None else param,
                    reference=ref,
                    ref1d=val,
                    abs_diff=val - ref,
                    rel_diff=(val - ref) / ref if ref != 0 else float("nan"),
                    diff_in_last_digit=(val - ref) / u if u > 0 else float("nan"),
                    ref_rounding="切り捨て" if len(prec) > 2 and prec[2] == "trunc" else "四捨五入",
                    agrees_to_published_digits=ok,
                )
            )
            print(f"lit {tab['key']} delta={delta:g} {param if param is not None else ''}: ref={ref:.8g} ref1d={val:.8g} "
                  f"diff={(val - ref) / ref:+.2e} ({(val - ref) / u:+.2f} unit) {'OK' if ok else 'NG'}", flush=True)
    # 非線形(Shakhov、剛体球 ω = 0.5)
    tab = NONLINEAR
    vg = gauss_legendre(**LIT_VGRID)
    prev = {}
    for beta, U0, d0, ref in sorted(tab["rows"], key=lambda t: (t[0], t[1], t[2])):
        uw = U0 * np.sqrt(2.0)  # v0 = sqrt(2 R T0) 単位 → sqrt(R T0) 単位
        pr = Problem(Kn=np.sqrt(np.pi) / (2.0 * d0), T_L=1.0 + beta, T_R=1.0 - beta, u_L=-uw, u_R=uw,
                     omega=tab["omega"], Pr=2.0 / 3.0)
        s = Solver(pr, make_mesh(_lit_mesh_N(d0)), vg, p=3)
        key = (beta, U0)
        r = s.solve(prim0=s.interpolate_from(prev[key]) if key in prev else None)
        prev[key] = r
        val = summarize(r)["Pxy_mean"] / 2.0
        ok = abs(val - ref) <= 0.5e-4 * (1.0 + 1e-9)
        rows.append(dict(source=tab["source"], table=tab["table"], model="S (nonlinear, ω=0.5)",
                         quantity=f"{tab['quantity']} (β={beta:g}, U0={U0:g})", delta=d0, param="",
                         reference=ref, ref1d=val, abs_diff=val - ref, rel_diff=(val - ref) / ref,
                         diff_in_last_digit=(val - ref) / 1e-4, ref_rounding="四捨五入", agrees_to_published_digits=ok))
        print(f"lit nonlinear beta={beta} U0={U0} delta0={d0}: ref={ref} ref1d={val:.6f} {'OK' if ok else 'NG'} "
              f"it={r.iterations} t={r.wall_time:.1f}s", flush=True)
    for beta, U0, ref in tab["fm_rows"]:
        uw = U0 * np.sqrt(2.0)
        pr = Problem(Kn=np.inf, T_L=1.0 + beta, T_R=1.0 - beta, u_L=-uw, u_R=uw, omega=tab["omega"])
        val = A.free_molecular(pr).Pxy / 2.0
        rows.append(dict(source=tab["source"], table=tab["table"], model="自由分子(解析解)",
                         quantity=f"{tab['quantity']} (β={beta:g}, U0={U0:g})", delta=0.0, param="",
                         reference=ref, ref1d=val, abs_diff=val - ref, rel_diff=(val - ref) / ref,
                         diff_in_last_digit=(val - ref) / 1e-4, ref_rounding="四捨五入",
                         agrees_to_published_digits=abs(val - ref) <= 0.5e-4 * (1.0 + 1e-9)))
    write_rows(rows, OUT / "literature.csv")
    with open(OUT / "literature_sources.txt", "w", encoding="utf-8") as fh:
        for k, v in SOURCES.items():
            fh.write(f"{k}: {v}\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    which = set(sys.argv[1:]) or {"fm", "nsf", "coef", "lit"}
    t0 = time.perf_counter()
    if "fm" in which:
        study_free_molecular()
    if "coef" in which:
        study_coefficients()
    if "nsf" in which:
        study_nsf()
    if "lit" in which:
        study_literature()
    print(f"合計 {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
