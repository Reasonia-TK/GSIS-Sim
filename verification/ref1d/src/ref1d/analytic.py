"""検証用の解析解・準解析解(無次元、R = 1、基準量は solver.py と同じ)。

- free_molecular: 自由分子流(Kn → ∞)の厳密解
- nsf_slip: Navier–Stokes–Fourier 方程式 + 1 次の速度すべり・温度跳躍条件の解(scipy.solve_bvp)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_bvp

from .solver import Problem

# 線形化 BGK / S モデルの完全拡散壁での係数(平均自由行程 ℓ = μ sqrt(2RT)/p 基準)。
# 出典: Siewert & Sharipov, Phys. Fluids 14, 4123 (2002) Table I(すべり係数)、
#       Garcia & Siewert, Eur. J. Mech. B/Fluids 29, 181 (2010) Table 6, 10(温度跳躍係数・すべり係数)。
#       README の「文献値」も参照。
SIGMA_P_BGK = 1.016191  # 速度すべり係数(BGK)
SIGMA_P_S = 1.018372  # 速度すべり係数(S モデル)。BGK とは異なる
ZETA_T_BGK = 1.302716  # 温度跳躍係数(BGK、Pr = 1)
ZETA_T_S = 1.954073  # 温度跳躍係数(S モデル、Pr = 2/3)。ZETA_T_BGK / Pr に等しい


@dataclass
class FreeMolecular:
    nL: float
    nR: float
    rho: float
    uy: float
    T: float
    Pxy: float
    qx: float
    Qtot: float


def free_molecular(pr: Problem) -> FreeMolecular:
    """自由分子流の厳密解(壁から放出された半 Maxwell 分布が、衝突なしに反対側の壁へ届く)。

    零質量流束: n_L sqrt(T_L) = n_R sqrt(T_R) = C、平均密度 1: (n_L + n_R)/2 = 1。
    巨視量は x によらない。
      ρ u_y = (n_L u_L + n_R u_R)/2
      3ρT + ρu_y² = [n_L(3T_L + u_L²) + n_R(3T_R + u_R²)]/2
      P_xy = C (u_L - u_R)/sqrt(2π)
      全エネルギー流束 = C [(2T_L + u_L²/2) - (2T_R + u_R²/2)]/sqrt(2π)、q_x = 全エネルギー流束 - u_y P_xy
    """
    C = 2.0 / (pr.T_L**-0.5 + pr.T_R**-0.5)
    nL = C / np.sqrt(pr.T_L)
    nR = C / np.sqrt(pr.T_R)
    rho = 0.5 * (nL + nR)
    uy = 0.5 * (nL * pr.u_L + nR * pr.u_R) / rho
    e2 = 0.5 * (nL * (3 * pr.T_L + pr.u_L**2) + nR * (3 * pr.T_R + pr.u_R**2)) / rho
    T = (e2 - uy**2) / 3.0
    s2p = np.sqrt(2.0 * np.pi)
    Pxy = C * (pr.u_L - pr.u_R) / s2p
    Qtot = C * ((2 * pr.T_L + 0.5 * pr.u_L**2) - (2 * pr.T_R + 0.5 * pr.u_R**2)) / s2p
    if pr.accommodation != 1.0:
        factor = pr.accommodation / (2.0 - pr.accommodation)
        Pxy *= factor
        Qtot *= factor
    qx = Qtot - uy * Pxy
    return FreeMolecular(nL=nL, nR=nR, rho=rho, uy=uy, T=T, Pxy=Pxy, qx=qx, Qtot=Qtot)


@dataclass
class NSFSolution:
    x: np.ndarray
    u: np.ndarray
    T: np.ndarray
    rho: np.ndarray
    p: float
    Pxy: float  # 一様なせん断応力
    E: float  # 一様な全エネルギー流束 q_x + u_y P_xy
    qx: np.ndarray
    sigma_P: float
    zeta_T: float
    success: bool


def nsf_slip(
    pr: Problem,
    sigma_P: float | None = None,
    zeta_T: float | None = None,
    n: int = 2001,
) -> NSFSolution:
    """NSF 方程式 + 1 次の速度すべり・温度跳躍条件の解。

    方程式(u_x = 0、p は一様):
      du/dx = -P/μ(T),  dT/dx = (u P - E)/κ(T),  μ = Kn sqrt(2/π) T^ω,  κ = (5/2) μ / Pr
      ∫_0^1 ρ dx = 1(ρ = p/T)
    境界条件(n は気体側を向く法線、ℓ = μ sqrt(2T)/p は壁での局所値):
      u - u_w = σ_P ℓ ∂u/∂n,  T - T_w = ζ_T ℓ ∂T/∂n
    σ_P, ζ_T を省略すると、Pr = 1 なら BGK、Pr = 2/3 なら S モデルの文献値を使う
    (sigma_P = zeta_T = 0 なら跳躍なし)。
    """
    if pr.accommodation != 1.0 and (sigma_P is None or zeta_T is None):
        raise ValueError("部分適応壁のNSF比較には対応するsigma_Pとzeta_Tを明示してください")
    is_bgk = abs(pr.Pr - 1.0) < 1e-12
    if sigma_P is None:
        sigma_P = SIGMA_P_BGK if is_bgk else SIGMA_P_S
    if zeta_T is None:
        zeta_T = ZETA_T_BGK if is_bgk else ZETA_T_S
    mu0 = pr.Kn * np.sqrt(2.0 / np.pi)
    om = pr.omega
    ck = 2.5 / pr.Pr  # κ = ck μ
    mk = 1.0 / ck  # μ/κ

    def fun(x, y, prm):
        P, E, p = prm
        u, T, _ = y
        mu = mu0 * T**om
        return np.vstack([-P / mu, (u * P - E) / (ck * mu), p / T])

    def bc(ya, yb, prm):
        P, E, p = prm
        ua, Ta, _ = ya
        ub, Tb, _ = yb
        la = np.sqrt(2.0 * Ta) / p  # ℓ/μ
        lb = np.sqrt(2.0 * Tb) / p
        return np.array(
            [
                ua - pr.u_L + sigma_P * la * P,  # u'(0) = -P/μ
                Ta - pr.T_L - zeta_T * la * mk * (ua * P - E),  # T'(0) = (uP - E)/κ
                ub - pr.u_R - sigma_P * lb * P,
                Tb - pr.T_R + zeta_T * lb * mk * (ub * P - E),
                ya[2],
                yb[2] - 1.0,
            ]
        )

    x = np.linspace(0.0, 1.0, n)
    u0 = pr.u_L + (pr.u_R - pr.u_L) * x
    T0 = pr.T_L + (pr.T_R - pr.T_L) * x
    y0 = np.vstack([u0, T0, x])
    P0 = -mu0 * (pr.u_R - pr.u_L)
    E0 = -ck * mu0 * (pr.T_R - pr.T_L)
    sol = solve_bvp(fun, bc, x, y0, p=np.array([P0, E0, 1.0]), tol=1e-10, max_nodes=200000)
    P, E, p = sol.p
    xs = np.linspace(0.0, 1.0, n)
    u, T, _ = sol.sol(xs)
    return NSFSolution(
        x=xs,
        u=u,
        T=T,
        rho=p / T,
        p=p,
        Pxy=P,
        E=E,
        qx=E - u * P,
        sigma_P=sigma_P,
        zeta_T=zeta_T,
        success=bool(sol.success),
    )


def linear_slip_heat_ratio(delta: float, zeta_T: float, Pr: float = 2.0 / 3.0) -> float:
    """線形化した熱伝導の q/q_fm(NSF + 温度跳躍)= (5 sqrt(π)/(4 Pr)) / (δ + 2 ζ_T)。

    Pr = 2/3 なら (15 sqrt(π)/8) / (δ + 2 ζ_T)。q_fm = -sqrt(2/π) ΔT(線形化した自由分子値)。
    """
    return (5.0 * np.sqrt(np.pi) / (4.0 * Pr)) / (delta + 2.0 * zeta_T)


def linear_slip_couette_ratio(delta: float, sigma_P: float) -> float:
    """線形化したクエット流の P_xy/P_xy,fm(NSF + 速度すべり)= sqrt(π) / (δ + 2 σ_P)。"""
    return np.sqrt(np.pi) / (delta + 2.0 * sigma_P)
