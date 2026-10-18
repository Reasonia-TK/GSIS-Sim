"""直方体の構造六面体メッシュ(平板メッシュと試験で使う)。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from ..builder import BoundaryGroup, polymesh_from_elements
from ..pmesh import PolyMesh

SIDES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")


def box_hex_elements(
    x: Sequence[float], y: Sequence[float], z: Sequence[float]
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """軸ごとの節点座標から、節点・六面体の接続(gmsh の節点順)・6 側面の四角形を作る。

    節点番号は ``(i * (ny + 1) + j) * (nz + 1) + k``。
    """
    x, y, z = (np.asarray(a, dtype=np.float64) for a in (x, y, z))
    for name, a in (("x", x), ("y", y), ("z", z)):
        if a.ndim != 1 or a.size < 2 or np.any(np.diff(a) <= 0):
            raise ValueError(f"{name} の節点座標は 2 点以上の狭義単調増加にしてください")
    nx, ny, nz = x.size - 1, y.size - 1, z.size - 1
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
    points = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)

    def nid(i, j, k):
        return (i * (ny + 1) + j) * (nz + 1) + k

    i, j, k = (a.ravel() for a in np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij"))
    conn = np.stack(
        [
            nid(i, j, k),
            nid(i + 1, j, k),
            nid(i + 1, j + 1, k),
            nid(i, j + 1, k),
            nid(i, j, k + 1),
            nid(i + 1, j, k + 1),
            nid(i + 1, j + 1, k + 1),
            nid(i, j + 1, k + 1),
        ],
        axis=1,
    )

    def quads(a_n, b_n, f):
        a, b = (t.ravel() for t in np.meshgrid(np.arange(a_n), np.arange(b_n), indexing="ij"))
        return np.stack([f(a, b), f(a + 1, b), f(a + 1, b + 1), f(a, b + 1)], axis=1)

    sides = {
        "xmin": quads(ny, nz, lambda a, b: nid(0, a, b)),
        "xmax": quads(ny, nz, lambda a, b: nid(nx, a, b)),
        "ymin": quads(nx, nz, lambda a, b: nid(a, 0, b)),
        "ymax": quads(nx, nz, lambda a, b: nid(a, ny, b)),
        "zmin": quads(nx, ny, lambda a, b: nid(a, b, 0)),
        "zmax": quads(nx, ny, lambda a, b: nid(a, b, nz)),
    }
    return points, conn, sides


def box_mesh(
    x: Sequence[float],
    y: Sequence[float],
    z: Sequence[float],
    patches: Mapping[str, Sequence[str] | tuple[str, Sequence[str]]] | None = None,
    *,
    renumber: str | None = None,
    metadata: dict[str, str] | None = None,
) -> PolyMesh:
    """直方体の構造六面体メッシュ。

    ``patches`` はパッチ名 → 側面名の並び(``"xmin"`` など)、または
    パッチ名 → ``(kind, 側面名の並び)``。省略すると 6 側面をそれぞれ別のパッチ
    (名前は側面名、kind は ``patch``)にする。
    """
    points, conn, sides = box_hex_elements(x, y, z)
    if patches is None:
        patches = {s: ("patch", (s,)) for s in SIDES}
    groups = []
    used: list[str] = []
    for name, spec in patches.items():
        if isinstance(spec, tuple) and len(spec) == 2 and isinstance(spec[0], str) and not isinstance(spec[1], str):
            kind, side_names = spec
        else:
            kind, side_names = None, spec
        side_names = [side_names] if isinstance(side_names, str) else list(side_names)
        for s in side_names:
            if s not in sides:
                raise ValueError(f"未知の側面名です: {s!r}(使えるのは {SIDES})")
        used += side_names
        groups.append(BoundaryGroup(name, kind, [sides[s] for s in side_names]))
    return polymesh_from_elements(points, [("hex", conn)], groups, renumber=renumber, metadata=metadata)
