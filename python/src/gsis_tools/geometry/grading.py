"""1 次元の節点分布(壁や管の出入口に向かって細かくする)。"""

from __future__ import annotations

import math

import numpy as np


def first_cell_to_wall_spacing(first: float, growth: float) -> float:
    """最初のセル幅が ``first`` になるような、端点での間隔の目安 w。

    h(d) = w + g d のとき最初のセル(∫dd/h = 1)の幅は w (e^g - 1) / g なので、その逆。
    """
    g = growth - 1.0
    return first * g / math.expm1(g)


def spacing_function(
    s: np.ndarray,
    length: float,
    h_max: float,
    h_start: float | None = None,
    h_end: float | None = None,
    growth: float = 1.15,
) -> np.ndarray:
    """間隔の目安 h(s) = min(h_max, w_start + (growth-1)s, w_end + (growth-1)(length-s))。

    ``h_start`` / ``h_end`` は端の最初のセル幅で、w はそれから :func:`first_cell_to_wall_spacing`
    で決める。間隔が距離に比例して増えるので、隣り合うセルの間隔の比はおよそ ``growth`` になる。
    """
    g = growth - 1.0
    h = np.full_like(np.asarray(s, dtype=np.float64), h_max)
    if h_start is not None:
        h = np.minimum(h, first_cell_to_wall_spacing(h_start, growth) + g * s)
    if h_end is not None:
        h = np.minimum(h, first_cell_to_wall_spacing(h_end, growth) + g * (length - s))
    return h


def graded_nodes(
    length: float,
    h_max: float,
    h_start: float | None = None,
    h_end: float | None = None,
    growth: float = 1.15,
    n: int | None = None,
    samples: int = 20001,
) -> np.ndarray:
    """[0, length] の節点座標。セル数は ``n``(省略時は ceil(∫ds/h))。

    s(φ) を ∫ds/h の逆関数で決めるので、各セルの間隔は h(s) 以下になる。
    """
    if length <= 0:
        raise ValueError("length は正にしてください")
    s = np.linspace(0.0, length, samples)
    inv = 1.0 / spacing_function(s, length, h_max, h_start, h_end, growth)
    phi = np.concatenate([[0.0], np.cumsum(0.5 * (inv[1:] + inv[:-1]) * np.diff(s))])
    if n is None:
        n = max(1, int(math.ceil(phi[-1] - 1e-9)))
    x = np.interp(np.linspace(0.0, phi[-1], n + 1), phi, s)
    x[0], x[-1] = 0.0, length
    return x


def _phi_wall(L: np.ndarray, w: float, hc: np.ndarray, g: float) -> np.ndarray:
    """h(d) = min(hc, w + g d) のときの ∫_0^L dd/h。"""
    d_star = (hc - w) / g
    return np.where(
        L <= d_star,
        np.log((w + g * L) / w) / g,
        np.log(hc / w) / g + (L - d_star) / hc,
    )


def wall_graded_rays(lengths: np.ndarray, n: int, h_wall: float, growth: float) -> np.ndarray:
    """長さの違う複数の線分を同じセル数 ``n`` で分け、壁側(端点 d = 0)の最初のセル幅を揃える。

    各線分で h(d) = min(hc, w + (growth-1) d)(d は壁からの距離、w は最初のセル幅が
    ``h_wall`` になる値)とし、
    ∫_0^L dd/h = n となる hc を二分法で決める。戻り値は壁からの距離 (n+1, 線分の数)。
    線分が短くて壁側の間隔 ``h_wall`` を保てない(L < n·h_wall)場合は、その線分を一様に分ける。
    """
    L = np.asarray(lengths, dtype=np.float64)
    g = growth - 1.0
    h_wall = first_cell_to_wall_spacing(h_wall, growth)
    lo = np.full_like(L, h_wall)
    hi = np.maximum(L, h_wall) * 4.0
    feasible = _phi_wall(L, h_wall, lo, g) >= n  # hc = h_wall でもセルが足りるか
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        too_many = _phi_wall(L, h_wall, mid, g) > n
        lo = np.where(too_many, mid, lo)
        hi = np.where(too_many, hi, mid)
    hc = 0.5 * (lo + hi)
    k = np.arange(n + 1)[:, None] / n
    phi_tot = _phi_wall(L, h_wall, hc, g)
    phi = k * phi_tot[None, :]
    phi_star = np.log(hc / h_wall) / g
    d = np.where(
        phi <= phi_star[None, :],
        h_wall * (np.exp(g * phi) - 1.0) / g,
        (hc - h_wall)[None, :] / g + (phi - phi_star[None, :]) * hc[None, :],
    )
    # 壁側の間隔を保てない線分は一様に縮める
    if not np.all(feasible):
        bad = ~feasible
        d[:, bad] = (k * L[None, bad]) * np.ones((1, int(bad.sum())))
    d[0, :] = 0.0
    d[-1, :] = L
    return d
