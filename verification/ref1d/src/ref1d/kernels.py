"""numba で書いた計算核(掃引、目標分布、モーメント)。

配列の並び:
- 縮約分布関数 G[j, i, k]: j = 速度点(ξx)、i = 節点、k = 縮約の番号(0..4)
- 原始量 prim[i, :] = (ρ, u_x, u_y, T, q_x, q_y)
- 巨視量 mom[i, :] は下の列番号(RHO, UX, ...)

縮約分布関数(ξy, ξz で積分したもの。無次元、R = 1):
  G0 = ∫∫ f dξy dξz
  G1 = ∫∫ ξy f
  G2 = ∫∫ (ξy² + ξz²) f
  G3 = ∫∫ ξy² f
  G4 = ∫∫ ξy (ξy² + ξz²) f
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

NK = 5  # 縮約分布関数の本数

# 巨視量の列番号
RHO, UX, UY, TH, QX, QY, PXX, PXY, PYY, PZZ, JM, MXX, MXY, QTOT = range(14)
NF = 14
FIELD_NAMES = ("rho", "ux", "uy", "T", "qx", "qy", "Pxx", "Pxy", "Pyy", "Pzz", "Jmass", "Mxx", "Mxy", "Qtot")


@njit(cache=True)
def emom(tau, p, out):
    """E_m(τ) = ∫_0^τ (t/τ)^m e^{-t} dt(m = 0..p)を out に入れる。

    τ < 1 は級数 E_m = τ Σ_j (-τ)^j / (j! (m+j+1))、τ ≥ 1 は漸化式 E_m = (m/τ) E_{m-1} - e^{-τ}。
    """
    if tau < 1.0:
        for m in range(p + 1):
            c = tau
            s = c / (m + 1)
            for j in range(1, 60):
                c *= -tau / j
                add = c / (m + j + 1)
                s += add
                if abs(add) <= 1e-17 * abs(s):
                    break
            out[m] = s
    else:
        e = np.exp(-tau)
        out[0] = 1.0 - e
        for m in range(1, p + 1):
            out[m] = (m / tau) * out[m - 1] - e


@njit(parallel=True, cache=True)
def sweep(xi, ds, GS, st_pos, st_neg, Vp, Vn, p, GA):
    """壁からの流入をゼロとして、各速度点で 1 次元の輸送を厳密に積分する(短特性線法)。

    衝突時間座標 s(ds[i] = セル i の Δs = ∫ν dx)で、ξ dG/ds = G^S - G。
    セル内の G^S を s の p 次多項式(節点 st..st+p の Lagrange 補間)で表すと
      G_out = G_in e^{-τ} + Σ_a W_a G^S_a、τ = Δs/|ξ|、W_a = Σ_m Vinv[m, a] E_m(τ)
    となる(σ = 流出端から測った s の正規化座標で、Vinv は σ のべきの Vandermonde 行列の逆)。
    """
    nv, nn, nk = GS.shape
    N = nn - 1
    for j in prange(nv):
        E = np.empty(p + 1)
        W = np.empty(p + 1)
        g = np.zeros(nk)
        x = xi[j]
        if x > 0.0:
            for k in range(nk):
                GA[j, 0, k] = 0.0
            for i in range(N):
                tau = ds[i] / x
                emom(tau, p, E)
                for a in range(p + 1):
                    acc = 0.0
                    for m in range(p + 1):
                        acc += Vp[i, m, a] * E[m]
                    W[a] = acc
                e = np.exp(-tau)
                st = st_pos[i]
                for k in range(nk):
                    val = g[k] * e
                    for a in range(p + 1):
                        val += W[a] * GS[j, st + a, k]
                    g[k] = val
                    GA[j, i + 1, k] = val
        else:
            for k in range(nk):
                GA[j, N, k] = 0.0
            for i in range(N - 1, -1, -1):
                tau = ds[i] / (-x)
                emom(tau, p, E)
                for a in range(p + 1):
                    acc = 0.0
                    for m in range(p + 1):
                        acc += Vn[i, m, a] * E[m]
                    W[a] = acc
                e = np.exp(-tau)
                st = st_neg[i]
                for k in range(nk):
                    val = g[k] * e
                    for a in range(p + 1):
                        val += W[a] * GS[j, st + a, k]
                    g[k] = val
                    GA[j, i, k] = val


@njit(parallel=True, cache=True)
def add_wall(G, s, xi, HL, HR, nL, nR):
    """壁から放出された分子(流入密度 nL, nR の壁 Maxwell 分布)の減衰した寄与を G に足す。

    ξ > 0: nL HL(ξ) exp(-s_i/ξ)、ξ < 0: nR HR(ξ) exp(-(s_N - s_i)/|ξ|)。
    """
    nv, nn, nk = G.shape
    sN = s[nn - 1]
    for j in prange(nv):
        x = xi[j]
        if x > 0.0:
            for i in range(nn):
                a = nL * np.exp(-s[i] / x)
                for k in range(nk):
                    G[j, i, k] += a * HL[j, k]
        else:
            for i in range(nn):
                a = nR * np.exp(-(sN - s[i]) / (-x))
                for k in range(nk):
                    G[j, i, k] += a * HR[j, k]


@njit(parallel=True, cache=True)
def target(xi, prim, a_coef, GS):
    """Shakhov の目標分布 f^S を ξy, ξz で積分した縮約モーメント G^S_k(ξx) を計算する。

    f^S = f^M [1 + A (c·q)(c²/T - 5)]、A = (1-Pr)/(5 p T)、a_coef = (1-Pr)/5。
    a = ξx - u_x、M1 = (2πT)^{-1/2} exp(-a²/2T)、B0 = a²/T - 3、d = a² - T とすると
      G0^S = ρ M1 [1 + A q_x a B0]
      G1^S = ρ M1 [u_y (1 + A q_x a B0) + A q_y d]
      G2^S = ρ M1 [(2T + u_y²) + A q_x a (2d + u_y² B0) + 2 A q_y u_y d]
      G3^S = ρ M1 [(T + u_y²) + A q_x a (d + u_y² B0) + 2 A q_y u_y d]
      G4^S = ρ M1 [(4 u_y T + u_y³) + A q_x a (4 u_y d + u_y³ B0) + A q_y (4T(a² + T) + 3 u_y² d)]
    (u_z = q_z = 0 を仮定。導出は README を参照)
    """
    nv, nn, nk = GS.shape
    for i in prange(nn):
        rho = prim[i, 0]
        ux = prim[i, 1]
        uy = prim[i, 2]
        th = prim[i, 3]
        qx = prim[i, 4]
        qy = prim[i, 5]
        A = a_coef / (rho * th * th)
        c0 = rho / np.sqrt(2.0 * np.pi * th)
        uy2 = uy * uy
        uy3 = uy2 * uy
        for j in range(nv):
            a = xi[j] - ux
            a2 = a * a
            M = c0 * np.exp(-0.5 * a2 / th)
            qa = A * a * qx
            qyA = A * qy
            B0 = a2 / th - 3.0
            d = a2 - th
            base = 1.0 + qa * B0
            GS[j, i, 0] = M * base
            GS[j, i, 1] = M * (uy * base + qyA * d)
            GS[j, i, 2] = M * ((2.0 * th + uy2) + qa * (2.0 * d + uy2 * B0) + 2.0 * qyA * uy * d)
            GS[j, i, 3] = M * ((th + uy2) + qa * (d + uy2 * B0) + 2.0 * qyA * uy * d)
            GS[j, i, 4] = M * (
                (4.0 * uy * th + uy3) + qa * (4.0 * uy * d + uy3 * B0) + qyA * (4.0 * th * (a2 + th) + 3.0 * uy2 * d)
            )


@njit(parallel=True, cache=True)
def moments(xi, w, G, out):
    """節点ごとに巨視量を計算する(out[i, :] の列は RHO, UX, ... の順)。

    総和は速度点の番号順に行うので、同じ入力なら同じ結果になる。
    """
    nv, nn, nk = G.shape
    for i in prange(nn):
        R0 = 0.0
        R1 = 0.0
        R2 = 0.0
        R3 = 0.0
        Y0 = 0.0
        Y1 = 0.0
        Y2 = 0.0
        S0 = 0.0
        S1 = 0.0
        Q0 = 0.0
        F0 = 0.0
        for j in range(nv):
            wj = w[j]
            x = xi[j]
            g0 = G[j, i, 0]
            g1 = G[j, i, 1]
            g2 = G[j, i, 2]
            g3 = G[j, i, 3]
            g4 = G[j, i, 4]
            wx = wj * x
            wx2 = wx * x
            wx3 = wx2 * x
            R0 += wj * g0
            R1 += wx * g0
            R2 += wx2 * g0
            R3 += wx3 * g0
            Y0 += wj * g1
            Y1 += wx * g1
            Y2 += wx2 * g1
            S0 += wj * g2
            S1 += wx * g2
            Q0 += wj * g3
            F0 += wj * g4
        rho = R0
        ux = R1 / R0
        uy = Y0 / R0
        th = ((R2 + S0) / R0 - ux * ux - uy * uy) / 3.0
        pxx = R2 - 2.0 * ux * R1 + ux * ux * R0
        a1y = Y1 - ux * Y0  # Σ w a G1
        a1 = R1 - ux * R0  # Σ w a G0(= 0)
        pxy = a1y - uy * a1
        pyy = Q0 - 2.0 * uy * Y0 + uy * uy * R0
        pzz = S0 - Q0
        a3 = R3 - 3.0 * ux * R2 + 3.0 * ux * ux * R1 - ux * ux * ux * R0
        aS = S1 - ux * S0
        qx = 0.5 * (a3 + aS - 2.0 * uy * a1y + uy * uy * a1)
        a2y = Y2 - 2.0 * ux * Y1 + ux * ux * Y0
        qy = 0.5 * (a2y - uy * pxx + F0 - uy * (2.0 * Q0 + S0) + 3.0 * uy * uy * Y0 - uy * uy * uy * R0)
        out[i, 0] = rho
        out[i, 1] = ux
        out[i, 2] = uy
        out[i, 3] = th
        out[i, 4] = qx
        out[i, 5] = qy
        out[i, 6] = pxx
        out[i, 7] = pxy
        out[i, 8] = pyy
        out[i, 9] = pzz
        out[i, 10] = R1  # 質量流束
        out[i, 11] = R2  # x 運動量の x 方向流束
        out[i, 12] = Y1  # y 運動量の x 方向流束
        out[i, 13] = 0.5 * (R3 + S1)  # 全エネルギーの x 方向流束
