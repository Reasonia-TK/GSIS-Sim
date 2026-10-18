"""x 方向の節点格子と、セル上の補間・積分の係数。

節点 x_0 = 0 < x_1 < ... < x_N = 1(壁の上にも節点を置く)。分布関数と巨視量は節点で持つ。
既定は余弦分布(Chebyshev–Gauss–Lobatto 点)で、壁付近の間隔は約 π²/(4N²)、中央は約 π/(2N)。
クヌーセン層の x ln x 型の特異性を、壁に向かって 2 乗で細かくなる格子で解像する。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Mesh:
    x: np.ndarray  # (N+1,) 節点
    kind: str
    stretch: float

    @property
    def n_cells(self) -> int:
        return int(self.x.size - 1)

    def describe(self) -> str:
        dx = np.diff(self.x)
        return f"{self.kind}(N={self.n_cells}, stretch={self.stretch}, dx_min={dx.min():.3e}, dx_max={dx.max():.3e})"


def make_mesh(n_cells: int, kind: str = "cos", stretch: float = 1.0) -> Mesh:
    """節点格子を作る。

    kind="uniform": 等間隔。
    kind="cos": x = (1-stretch)*t + stretch*(1-cos(πt))/2(t は等間隔)。stretch=1 で Chebyshev–Lobatto 点。
    """
    t = np.linspace(0.0, 1.0, n_cells + 1)
    if kind == "uniform":
        x = t.copy()
    elif kind == "cos":
        x = (1.0 - stretch) * t + stretch * 0.5 * (1.0 - np.cos(np.pi * t))
    else:
        raise ValueError(f"未知の格子: {kind}")
    x[0] = 0.0
    x[-1] = 1.0
    return Mesh(x=x, kind=kind, stretch=stretch)


def stencils(n_cells: int, p: int) -> tuple[np.ndarray, np.ndarray]:
    """セル i(区間 [x_i, x_{i+1}])で目標分布を補間する節点のステンシルの先頭番号。

    戻り値 (st_pos, st_neg): ξx > 0 / ξx < 0 の掃引で使うステンシル {st, st+1, ..., st+p}。
    - p = 1: {i, i+1}
    - p = 2: 流出側の節点が中央に来るように選ぶ(ξx>0: {i, i+1, i+2}、ξx<0: {i-1, i, i+1})。
             衝突が支配的な極限で、流出節点での微分が中心差分になる。
    - p = 3: セルを中央に挟む {i-1, i, i+1, i+2}(向きによらない)。
    壁の近くではステンシルを内側にずらす。
    """
    i = np.arange(n_cells)
    if p == 1:
        sp = i.copy()
        sn = i.copy()
    elif p == 2:
        sp = i.copy()
        sn = i - 1
    elif p == 3:
        sp = i - 1
        sn = i - 1
    else:
        raise ValueError("p は 1, 2, 3 のどれか")
    lo, hi = 0, n_cells - p
    if hi < 0:
        raise ValueError("セル数が補間次数に対して少なすぎる")
    return np.clip(sp, lo, hi).astype(np.int64), np.clip(sn, lo, hi).astype(np.int64)


def cell_integration_weights(x: np.ndarray, p: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """各セル上の積分 ∫_{x_i}^{x_{i+1}} φ dx を、節点値の Lagrange 補間(次数 p、中央寄せのステンシル)で近似する重み。

    戻り値 (st, W): ∫_{x_i}^{x_{i+1}} φ dx ≈ Σ_a W[i, a] φ[st[i] + a]
    """
    n = x.size - 1
    i = np.arange(n)
    st = np.clip(i - (p - 1) // 2 if p > 1 else i, 0, n - p).astype(np.int64)
    tq, wq = np.polynomial.legendre.leggauss(8)
    W = np.zeros((n, p + 1))
    for c in range(n):
        a, b = x[c], x[c + 1]
        xq = 0.5 * (b - a) * tq + 0.5 * (b + a)
        wqc = 0.5 * (b - a) * wq
        nodes = x[st[c] : st[c] + p + 1]
        for k in range(p + 1):
            lk = np.ones_like(xq)
            for m in range(p + 1):
                if m != k:
                    lk *= (xq - nodes[m]) / (nodes[k] - nodes[m])
            W[c, k] = np.sum(wqc * lk)
    return st, W


def integrate_nodal(st: np.ndarray, W: np.ndarray, f: np.ndarray) -> np.ndarray:
    """cell_integration_weights の重みでセルごとの積分を返す(f は (N+1,) または (N+1, ...))。"""
    p1 = W.shape[1]
    out = np.zeros((W.shape[0],) + f.shape[1:])
    for a in range(p1):
        out += (W[:, a].reshape((-1,) + (1,) * (f.ndim - 1))) * f[st + a]
    return out


def nodal_quadrature_weights(x: np.ndarray, p: int = 3) -> np.ndarray:
    """∫_0^1 φ dx ≈ Σ_i q_i φ_i となる節点ごとの重み q(区分的な Lagrange 補間の積分)。"""
    st, W = cell_integration_weights(x, p)
    q = np.zeros(x.size)
    for a in range(W.shape[1]):
        np.add.at(q, st + a, W[:, a])
    return q
