"""平行平板間の 1 次元定常 Shakhov 方程式の参照ソルバー。

問題(無次元、R = 1。基準量は README を参照)
---------------------------------------------
  ξx ∂f/∂x = ν (f^S - f),   0 < x < 1
  f^S = f^M [1 + (1-Pr)(c·q)/(5 p T) (c²/T - 5)]、ν = p/μ = sqrt(π/2)/Kn · ρ T^{1-ω}
  x = 0: 壁(温度 T_L、y 方向の速度 u_L)、x = 1: 壁(T_R、u_R)。完全拡散反射。
  平板間の平均密度を 1 にする(全質量の保存)。

縮約分布関数
------------
f を ξy, ξz で積分した 5 本の関数 G_k(x, ξx)(kernels.py の冒頭)は、
  ξx ∂G_k/∂x = ν (G_k^S - G_k)
を満たし、G_k^S は (ρ, u_x, u_y, T, q_x, q_y) で閉じた式になる。壁の Maxwell 分布の縮約も解析的。
したがって ξy, ξz 方向の離散化誤差はなく、ξx 方向だけを離散化すればよい(厳密な縮約)。

離散化
------
- ξx: 速度格子(velocity.py)。
- x: 節点格子(mesh.py)。衝突時間座標 s(x) = ∫_0^x ν dx' で輸送方程式は ξ dG/ds = G^S - G となる。
  Δs は ν の 3 次補間を積分して求め、セル内の G^S を s の p 次多項式で補間して、各速度点で厳密に積分する
  (kernels.sweep)。
- 壁の流入密度 n_L, n_R: 流入をゼロとした掃引 G_A と、壁から放出された分子の減衰 exp(-s/|ξ|) を使い、
  離散速度空間で壁の正味質量流束がゼロになる 2 元連立 1 次方程式を、各掃引の中で解く(付録 A.1 と同じ条件)。

反復
----
巨視量 X = (ρ, u_x, u_y, T, q_x, q_y)(節点ごと)の不動点問題 X = Φ(X) を解く。
Φ: X → (ν, G^S) → 掃引 → 壁の流入密度 → モーメント → 全質量で規格化。
Anderson 加速(既定)または単純反復(Picard)。
"""

from __future__ import annotations

import time
import warnings
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from . import kernels as K
from .mesh import Mesh, cell_integration_weights, integrate_nodal, nodal_quadrature_weights, stencils
from .velocity import VelocityGrid


@dataclass(frozen=True)
class Problem:
    """問題の定義(すべて無次元。温度は T0 = (T_L + T_R)/2、速度は sqrt(R T0) が単位)。"""

    Kn: float
    T_L: float = 1.0
    T_R: float = 1.0
    u_L: float = 0.0  # 左の壁(x = 0)の y 方向速度
    u_R: float = 0.0  # 右の壁(x = 1)の y 方向速度
    omega: float = 0.66
    Pr: float = 2.0 / 3.0  # Pr = 1 で BGK モデル
    name: str = ""
    accommodation: float = 1.0  # 両壁に共通のMaxwell適応係数

    def __post_init__(self):
        if not 0.0 < self.accommodation <= 1.0:
            raise ValueError("accommodation は 0 より大きく 1 以下にすること")
        if abs(0.5 * (self.T_L + self.T_R) - 1.0) > 1e-12:
            raise ValueError("T0 = (T_L + T_R)/2 = 1 となるように無次元化すること")

    @property
    def delta(self) -> float:
        """希薄度パラメータ δ = p0 L /(μ0 sqrt(2 R T0)) = sqrt(π)/(2 Kn)。"""
        return np.sqrt(np.pi) / (2.0 * self.Kn) if np.isfinite(self.Kn) else 0.0

    @property
    def perturbation(self) -> float:
        """収束判定に使う代表的な摂動の大きさ。"""
        return max(abs(self.T_R - self.T_L), abs(self.u_R - self.u_L), 1e-300)


def wall_reduced_maxwellian(xi: np.ndarray, T: float, u: float) -> np.ndarray:
    """単位密度・静止(x 方向)・y 方向速度 u の壁 Maxwell 分布の縮約モーメント H[j, k]。"""
    M1 = np.exp(-0.5 * xi**2 / T) / np.sqrt(2.0 * np.pi * T)
    H = np.empty((xi.size, K.NK))
    H[:, 0] = M1
    H[:, 1] = u * M1
    H[:, 2] = (2.0 * T + u * u) * M1
    H[:, 3] = (T + u * u) * M1
    H[:, 4] = (4.0 * u * T + u**3) * M1
    return H


@dataclass
class Result:
    problem: Problem
    mesh: Mesh
    vgrid: VelocityGrid
    p: int
    x: np.ndarray
    mom: np.ndarray  # (N+1, NF) 巨視量(列は kernels の RHO, UX, ...)
    nL: float
    nR: float
    iterations: int
    residual: float
    converged: bool
    wall_time: float
    history: list = field(default_factory=list)
    G: np.ndarray | None = None  # 縮約分布関数(必要なときだけ保持)

    def col(self, name: str) -> np.ndarray:
        return self.mom[:, K.FIELD_NAMES.index(name)]

    @property
    def pressure(self) -> np.ndarray:
        return self.col("rho") * self.col("T")


class Solver:
    def __init__(self, problem: Problem, mesh: Mesh, vgrid: VelocityGrid, p: int = 3, quad_p: int = 3):
        self.problem = problem
        self.mesh = mesh
        self.vg = vgrid
        self.p = p
        x = mesh.x
        self.N = mesh.n_cells
        self.st_pos, self.st_neg = stencils(self.N, p)
        self.qst, self.qW = cell_integration_weights(x, quad_p)
        self.wq = nodal_quadrature_weights(x, quad_p)
        xi, w = vgrid.xi, vgrid.w
        self.xi = np.ascontiguousarray(xi)
        self.w = np.ascontiguousarray(w)
        self.nh = vgrid.nh
        pos = self.xi > 0
        neg = ~pos
        self.pos, self.neg = pos, neg
        HL = wall_reduced_maxwellian(self.xi, problem.T_L, problem.u_L)
        HR = wall_reduced_maxwellian(self.xi, problem.T_R, problem.u_R)
        HL[neg] = 0.0
        HR[pos] = 0.0
        self.HL, self.HR = np.ascontiguousarray(HL), np.ascontiguousarray(HR)
        # 単位密度の壁 Maxwell 分布が気体へ流入する質量流束(離散速度空間で計算)
        self.jL = float(np.sum(self.w[pos] * self.xi[pos] * HL[pos, 0]))
        self.jR = float(np.sum(self.w[neg] * (-self.xi[neg]) * HR[neg, 0]))
        nv, nn = self.xi.size, self.N + 1
        self.GS = np.zeros((nv, nn, K.NK))
        self.GA = np.zeros((nv, nn, K.NK))
        self.G = np.zeros((nv, nn, K.NK))
        self.mom = np.zeros((nn, K.NF))
        self.a_coef = (1.0 - problem.Pr) / 5.0
        self.free_molecular = not np.isfinite(problem.Kn)
        self.nL = 1.0
        self.nR = 1.0
        self.n_phi = 0

    # ------------------------------------------------------------------
    def nu(self, prim: np.ndarray) -> np.ndarray:
        """衝突頻度 ν = p/μ = sqrt(π/2)/Kn · ρ T^{1-ω}(μ = Kn sqrt(2/π) T^ω)。"""
        pr = self.problem
        return np.sqrt(np.pi / 2.0) / pr.Kn * prim[:, 0] * prim[:, 3] ** (1.0 - pr.omega)

    def _vinv(self, s: np.ndarray, ds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """各セルの補間の Vandermonde 行列の逆(σ = 流出端から測った正規化座標)。"""
        p = self.p
        ar = np.arange(p + 1)
        idx = self.st_pos[:, None] + ar[None, :]
        sig = (s[1:, None] - s[idx]) / ds[:, None]
        Vp = np.linalg.inv(sig[:, :, None] ** ar[None, None, :])
        idx = self.st_neg[:, None] + ar[None, :]
        sig = (s[idx] - s[:-1, None]) / ds[:, None]
        Vn = np.linalg.inv(sig[:, :, None] ** ar[None, None, :])
        return np.ascontiguousarray(Vp), np.ascontiguousarray(Vn)

    def _wall_densities(self, s: np.ndarray) -> tuple[float, float]:
        """壁の正味質量流束をゼロにする流入密度 n_L, n_R(離散速度空間)。

        左の壁:  n_L j_L + a_L + n_R b_L = 0
        右の壁:  a_R + n_L b_R - n_R j_R = 0
        a: 流入ゼロの掃引 G_A が壁へ運ぶ質量流束、b: 反対側の壁から放出され減衰して届く分の流束。
        """
        xi, w, pos, neg, N = self.xi, self.w, self.pos, self.neg, self.N
        sN = s[-1]
        GA = self.GA
        aL = float(np.sum(w[neg] * xi[neg] * GA[neg, 0, 0]))
        bL = float(np.sum(w[neg] * xi[neg] * np.exp(-sN / (-xi[neg])) * self.HR[neg, 0]))
        aR = float(np.sum(w[pos] * xi[pos] * GA[pos, N, 0]))
        bR = float(np.sum(w[pos] * xi[pos] * np.exp(-sN / xi[pos]) * self.HL[pos, 0]))
        jL, jR = self.jL, self.jR
        det = -jL * jR - bL * bR
        if abs(det) <= 1e-14 * jL * jR:
            # 自由分子流(ν = 0): 比だけが決まる。大きさは全質量の規格化で決める
            return 1.0 / jL, 1.0 / jR
        nL = (-aL * (-jR) - bL * (-aR)) / det
        nR = (jL * (-aR) - bR * (-aL)) / det
        return nL, nR

    def _maxwell_walls(self, s: np.ndarray) -> tuple[float, float]:
        """壁間の全ての鏡面往復を解析的に足し、零質量流束の2元連立系を解く。"""
        alpha = self.problem.accommodation
        r = 1.0 - alpha
        x = self.xi[self.pos]
        w = self.w[self.pos] * x
        e = np.exp(-s[-1] / x)[:, None]
        den = 1.0 - r * r * e * e
        A = self.GA[self.pos, self.N, :]
        B = self.GA[self.neg, 0, :][::-1]
        HL = self.HL[self.pos]
        HR = self.HR[self.neg][::-1]
        L0, R0 = (r * B + r*r*e*A) / den, (r * A + r*r*e*B) / den
        LL, LR = alpha*HL/den, r*e*alpha*HR/den
        RL, RR = r*e*alpha*HL/den, alpha*HR/den

        def flux(a):
            return float(w @ a[:, 0])

        matrix = np.array([[flux(LL-e*RL), flux(LR-e*RR)],
                           [flux(e*LL-RL), flux(e*LR-RR)]])
        rhs = -np.array([flux(L0-B-e*R0), flux(A+e*L0-R0)])
        if self.free_molecular:
            nL, nR = 1.0 / self.jL, 1.0 / self.jR
        else:
            nL, nR = np.linalg.solve(matrix, rhs)
        left, right = np.zeros_like(self.HL), np.zeros_like(self.HR)
        left[self.pos] = L0 + nL*LL + nR*LR
        right[self.neg] = (R0 + nL*RL + nR*RR)[::-1]
        K.add_wall(self.G, s, self.xi, left, right, 1.0, 1.0)
        return float(nL), float(nR)

    def phi(self, prim: np.ndarray) -> np.ndarray:
        """不動点写像 Φ。prim (N+1, 6) → 新しい prim。self.G, self.mom も更新する。"""
        self.n_phi += 1
        nn = self.N + 1
        if self.free_molecular:
            s = np.zeros(nn)
            self.GA[:] = 0.0
        else:
            nu = self.nu(prim)
            ds = integrate_nodal(self.qst, self.qW, nu)
            if np.any(ds <= 0.0):
                raise FloatingPointError("Δs が正でない(ν の補間が負)")
            s = np.concatenate([[0.0], np.cumsum(ds)])
            Vp, Vn = self._vinv(s, ds)
            K.target(self.xi, np.ascontiguousarray(prim), self.a_coef, self.GS)
            K.sweep(self.xi, ds, self.GS, self.st_pos, self.st_neg, Vp, Vn, self.p, self.GA)
        self.G[:] = self.GA
        if self.problem.accommodation == 1.0:
            nL, nR = self._wall_densities(s)
            K.add_wall(self.G, s, self.xi, self.HL, self.HR, nL, nR)
        else:
            nL, nR = self._maxwell_walls(s)
        K.moments(self.xi, self.w, self.G, self.mom)
        mass = float(self.wq @ self.mom[:, K.RHO])
        c = 1.0 / mass
        # 全質量で規格化する(示量的な量だけを c 倍)
        self.G *= c
        for col in (K.RHO, K.QX, K.QY, K.PXX, K.PXY, K.PYY, K.PZZ, K.JM, K.MXX, K.MXY, K.QTOT):
            self.mom[:, col] *= c
        self.nL, self.nR = nL * c, nR * c
        self.s = s
        return self.mom[:, :6].copy()

    # ------------------------------------------------------------------
    def initial_guess(self) -> np.ndarray:
        pr = self.problem
        x = self.mesh.x
        T = pr.T_L + (pr.T_R - pr.T_L) * x
        rho = 1.0 / T
        rho /= self.wq @ rho
        uy = pr.u_L + (pr.u_R - pr.u_L) * x
        prim = np.zeros((x.size, 6))
        prim[:, 0] = rho
        prim[:, 2] = uy
        prim[:, 3] = T
        return prim

    def interpolate_from(self, other: Result) -> np.ndarray:
        """別の格子の解を初期値として補間する。"""
        prim = np.zeros((self.N + 1, 6))
        for c in range(6):
            prim[:, c] = np.interp(self.mesh.x, other.x, other.mom[:, c])
        prim[:, 0] /= self.wq @ prim[:, 0]
        return prim

    def solve(
        self,
        prim0: np.ndarray | None = None,
        tol: float = 1e-12,
        atol: float = 1e-14,
        maxit: int = 20000,
        method: str = "anderson",
        m_aa: int = 100,
        rcond: float = 1e-12,
        n_picard_first: int = 2,
        verbose: bool = False,
        keep_G: bool = False,
    ) -> Result:
        """不動点反復を解く。

        収束判定: d = max_f ||Φ(X)_f - X_f||_∞(f は 6 つの巨視量)として、
          d / pert < tol(pert は壁温差・壁速度差の大きい方)または d < atol(丸め誤差の下限)。
        ρ, T は O(1) の量なので、d は 1e-15 程度より小さくならない。摂動が小さい線形ケースでは atol で止まる。
        """
        t0 = time.perf_counter()
        pert = self.problem.perturbation
        X = (self.initial_guess() if prim0 is None else prim0.copy()).ravel()
        shape = (self.N + 1, 6)
        dG: deque = deque(maxlen=m_aa)
        dF: deque = deque(maxlen=m_aa)
        g_prev = f_prev = None
        hist = []
        converged = False
        best = np.inf
        it = 0
        res = np.inf
        for it in range(1, maxit + 1):
            g = self.phi(X.reshape(shape)).ravel()
            f = g - X
            dmax = float(np.max(np.abs(f)))
            res = dmax / pert
            hist.append(res)
            if verbose and (it <= 5 or it % 50 == 0):
                print(f"  it={it:6d} res={res:.3e}")
            if not np.isfinite(res):
                raise FloatingPointError("反復が発散した")
            if res < tol or dmax < atol:
                converged = True
                X = g
                break
            use_aa = method == "anderson" and it > n_picard_first
            Xn = g
            if use_aa:
                if g_prev is not None:
                    dG.append(g - g_prev)
                    dF.append(f - f_prev)
                if len(dF) > 0:
                    Fm = np.column_stack(dF)
                    Gm = np.column_stack(dG)
                    gamma, *_ = np.linalg.lstsq(Fm, f, rcond=rcond)
                    Xn = g - Gm @ gamma
                    P = Xn.reshape(shape)
                    if np.any(P[:, 0] <= 0.0) or np.any(P[:, 3] <= 0.0) or not np.all(np.isfinite(Xn)):
                        Xn = g
                        dG.clear()
                        dF.clear()
                # 残差が最良値から大きく悪化したら履歴を捨てる
                if res > 1e3 * best:
                    dG.clear()
                    dF.clear()
                    Xn = g
            best = min(best, res)
            g_prev, f_prev = g, f
            X = Xn
        if not converged:
            warnings.warn(
                f"反復が収束しなかった({self.problem.name or self.problem}, it={it}, res={res:.2e})", RuntimeWarning, stacklevel=2
            )
        # 収束した X で最後にもう一度 Φ を評価して、G と mom を X に対応させる
        self.phi(X.reshape(shape))
        wall = time.perf_counter() - t0
        return Result(
            problem=self.problem,
            mesh=self.mesh,
            vgrid=self.vg,
            p=self.p,
            x=self.mesh.x.copy(),
            mom=self.mom.copy(),
            nL=self.nL,
            nR=self.nR,
            iterations=it,
            residual=res,
            converged=converged,
            wall_time=wall,
            history=hist,
            G=self.G.copy() if keep_G else None,
        )


def solve_case(
    problem: Problem,
    mesh: Mesh,
    vgrid: VelocityGrid,
    p: int = 3,
    prim0: np.ndarray | None = None,
    init_from: Result | None = None,
    **kw,
) -> Result:
    s = Solver(problem, mesh, vgrid, p=p)
    if init_from is not None and prim0 is None:
        prim0 = s.interpolate_from(init_from)
    return s.solve(prim0=prim0, **kw)
