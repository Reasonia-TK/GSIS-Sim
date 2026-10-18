"""x 方向の速度格子(ξx だけを離散化する)。

ref1d は ξy, ξz 方向を解析的に積分した縮約分布関数 G_k(x, ξx) を解くので、
離散化するのは平板の法線方向の速度 ξx だけである(solver.py の冒頭を参照)。

格子の並びは「前半が負の速度(昇順)、後半が正の速度(昇順)」で、ξx = 0 は含まない。
正負は対称: xi[nh + j] = -xi[nh - 1 - j](nh = Nv / 2)。

用意している格子:
- ``gauss_legendre``: 半区間 (0, ξmax] ごとの複合 Gauss–Legendre 求積(既定)。
  ξx = 0 で分布関数が不連続になる(壁)ことと、0 付近の |ξx| ln|ξx| 型の特異性に対応するため、
  区間 [0, panel] を 0 に向かって幾何級数的に細かく分ける。
- ``midpoint``: 一様格子の区間中点配置(3 次元ソルバーの速度格子と同じ点配置で比べるため)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class VelocityGrid:
    """ξx 方向の速度格子。"""

    xi: np.ndarray  # (Nv,) 速度点
    w: np.ndarray  # (Nv,) 求積の重み
    kind: str
    params: dict = field(default_factory=dict)

    @property
    def nv(self) -> int:
        return int(self.xi.size)

    @property
    def nh(self) -> int:
        """負(または正)の速度点の数。"""
        return int(self.xi.size // 2)

    def describe(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.kind}(Nv={self.nv}; {p})"


def _mirror(xp: np.ndarray, wp: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """正の半区間の点と重みを負側に鏡映して全体の格子にする。"""
    order = np.argsort(xp)
    xp = xp[order]
    wp = wp[order]
    xi = np.concatenate([-xp[::-1], xp])
    w = np.concatenate([wp[::-1], wp])
    return xi, w


def gauss_legendre(
    xi_max: float = 10.0,
    panel: float = 0.5,
    n_gl: int = 8,
    n_geo: int = 4,
    ratio: float = 0.25,
) -> VelocityGrid:
    """半区間ごとの複合 Gauss–Legendre 格子。

    正の半区間 (0, xi_max] を次のパネルに分け、各パネルに n_gl 点の Gauss–Legendre 求積を置く。
      [0, panel*ratio**n_geo], [panel*ratio**n_geo, panel*ratio**(n_geo-1)], ..., [panel*ratio, panel],
      [panel, 2*panel], ..., [.., xi_max]
    負の半区間は鏡映。点の総数は 2 * n_gl * (n_geo + 1 + 一様パネル数)。
    """
    if xi_max <= panel:
        raise ValueError("xi_max は panel より大きくすること")
    edges = [0.0]
    edges += [panel * ratio**k for k in range(n_geo, 0, -1)]
    n_uni = int(np.ceil((xi_max - panel) / panel - 1e-12))
    edges += [panel + panel * k for k in range(0, n_uni)]
    edges.append(xi_max)
    edges = np.unique(np.asarray(edges, dtype=float))
    t, wt = np.polynomial.legendre.leggauss(n_gl)
    xs, ws = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        xs.append(0.5 * (b - a) * t + 0.5 * (b + a))
        ws.append(0.5 * (b - a) * wt)
    xi, w = _mirror(np.concatenate(xs), np.concatenate(ws))
    params = dict(xi_max=xi_max, panel=panel, n_gl=n_gl, n_geo=n_geo, ratio=ratio)
    return VelocityGrid(xi=xi, w=w, kind="gauss_legendre", params=params)


def midpoint(n: int, xi_max: float) -> VelocityGrid:
    """[-xi_max, xi_max] を n 等分した区間中点配置(n は偶数。ξx = 0 が区間の境界になる)。"""
    if n % 2 != 0:
        raise ValueError("n は偶数にすること(ξx = 0 を区間の境界にするため)")
    h = 2.0 * xi_max / n
    xi = -xi_max + h * (np.arange(n) + 0.5)
    w = np.full(n, h)
    return VelocityGrid(xi=xi, w=w, kind="midpoint", params=dict(n=n, xi_max=xi_max))


# 収束調査で使う速度格子の水準(粗い → 細かい)
GL_LEVELS: dict[str, dict] = {
    "v0": dict(xi_max=10.0, panel=1.0, n_gl=4, n_geo=2, ratio=0.25),
    "v1": dict(xi_max=10.0, panel=1.0, n_gl=6, n_geo=3, ratio=0.25),
    "v2": dict(xi_max=10.0, panel=0.5, n_gl=6, n_geo=4, ratio=0.25),
    "v3": dict(xi_max=10.0, panel=0.5, n_gl=8, n_geo=5, ratio=0.25),
    "v4": dict(xi_max=12.0, panel=0.25, n_gl=8, n_geo=6, ratio=0.25),
}


def gl_level(name: str) -> VelocityGrid:
    return gauss_legendre(**GL_LEVELS[name])
