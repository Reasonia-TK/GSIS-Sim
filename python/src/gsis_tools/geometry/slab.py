"""平板メッシュ(平行平板間の 1 次元問題を 3 次元ソルバーで解くための薄い板)。

x 方向に nx セル(壁の近くを細かくすることもできる: tanh 分布か余弦分布)、y・z 方向は 1 セル。

パッチ(この順に並ぶ):

- ``wall_left``(x = 0、kind = wall)
- ``wall_right``(x = L、kind = wall)
- ``sym_y``(y = 0 と y = T の 2 面、kind = symmetry)
- ``sym_z``(z = 0 と z = T の 2 面、kind = symmetry)

セル番号は x の小さい順(付け直しはしない)。
"""

from __future__ import annotations

import math

import numpy as np

from ..pmesh import PolyMesh
from .box import box_mesh

SLAB_PATCHES = {
    "wall_left": ("wall", ("xmin",)),
    "wall_right": ("wall", ("xmax",)),
    "sym_y": ("symmetry", ("ymin", "ymax")),
    "sym_z": ("symmetry", ("zmin", "zmax")),
}


def slab_nodes(
    length: float, nx: int, first_cell: float | None = None, cos_stretch: float | None = None
) -> np.ndarray:
    """[0, length] を nx セルに分ける節点(左右対称)。

    - ``first_cell`` を与えると tanh 分布で両側の壁に向かって細かくする。壁に接するセルの幅が
      ``first_cell`` になるように分布の強さを決める(``first_cell >= length / nx`` なら一様)
    - ``cos_stretch``(0〜1)を与えると余弦分布 x/L = (1-s)t + s(1-cos πt)/2(t は等間隔)。
      s = 1 で Chebyshev–Lobatto 点(verification/ref1d の ``kind="cos"`` と同じ式)
    - どちらもなければ一様
    """
    if nx < 1:
        raise ValueError("nx は 1 以上にしてください")
    if first_cell is not None and cos_stretch is not None:
        raise ValueError("first_cell と cos_stretch は同時に指定できません")
    xi = np.arange(nx + 1) / nx
    if cos_stretch is not None:
        if not 0.0 <= cos_stretch <= 1.0:
            raise ValueError("cos_stretch は 0〜1 にしてください")
        x = length * ((1.0 - cos_stretch) * xi + cos_stretch * 0.5 * (1.0 - np.cos(np.pi * xi)))
        x[0], x[-1] = 0.0, length
        return x
    if first_cell is None or first_cell >= length / nx:
        return length * xi
    if first_cell <= 0:
        raise ValueError("first_cell は正にしてください")

    def first(beta: float) -> float:
        return 0.5 * length * (1.0 + math.tanh(beta * (2.0 / nx - 1.0)) / math.tanh(beta))

    lo, hi = 1e-8, 1.0
    while first(hi) > first_cell:
        hi *= 2.0
        if hi > 1e4:
            raise ValueError("first_cell が小さすぎます")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if first(mid) > first_cell:
            lo = mid
        else:
            hi = mid
    beta = 0.5 * (lo + hi)
    x = 0.5 * length * (1.0 + np.tanh(beta * (2.0 * xi - 1.0)) / math.tanh(beta))
    x[0], x[-1] = 0.0, length
    return x


def generate_slab(
    length: float = 1e-3,
    nx: int = 100,
    *,
    first_cell: float | None = None,
    cos_stretch: float | None = None,
    thickness: float | None = None,
    metadata: dict[str, str] | None = None,
) -> PolyMesh:
    """平板メッシュ。節点の分布は :func:`slab_nodes`。``thickness``(y・z 方向の厚さ)の既定値は length / nx。"""
    x = slab_nodes(length, nx, first_cell, cos_stretch)
    t = float(thickness) if thickness is not None else length / nx
    if t <= 0:
        raise ValueError("thickness は正にしてください")
    md = {
        "source": "gsis_tools.geometry.slab.generate_slab",
        "kind": "slab",
        "length": repr(float(length)),
        "nx": str(int(nx)),
        "first_cell": repr(first_cell),
        "cos_stretch": repr(cos_stretch),
        "thickness": repr(t),
    }
    md.update(metadata or {})
    return box_mesh(x, [0.0, t], [0.0, t], SLAB_PATCHES, renumber=None, metadata=md)
