"""管の中心線(軸 y = z = 0)の分布をセル値から取り出す。

セル中心は軸の上にないので、軸の近く(r < r_max)のセルに a + b r² を最小二乗で当て、r = 0 の値 a を取る
(``cases/tube_vacuum/kinetic/centerline.py`` と同じ考え方)。

- ``"layers"``: 構造六面体のように、同じ x の層にセルが並ぶメッシュ。層ごとに当てる
- ``"bins"``: 四面体などの層がないメッシュ。x を区間に分け、区間ごとに a + b (x − x_c) + c r² を当てる
- ``"auto"``: 軸の近くのセルの 9 割以上が 3 個以上の層に入っていれば ``"layers"``、それ以外は ``"bins"``

座標と値は呼び出し側で無次元にして渡す(座標は L_ref 単位、論文の X = x/L_ref − x_offset)。
Qt には依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# 論文 Fig.3 の横軸: 管の入口 x = 8R が X = 0、出口 x = 18R が X = 10(gsis_tools.geometry.tube.TubeDims)
TUBE_X_OFFSET = 8.0
TUBE_LENGTH = 10.0


@dataclass
class Centerline:
    """中心線の分布。``X`` は昇順。``values`` は名前 → X ごとの値(r = 0 に外挿した値)。"""

    X: np.ndarray
    values: dict[str, np.ndarray] = field(default_factory=dict)
    method: str = ""
    n_cells: int = 0
    r_max: float = 0.0

    def __len__(self) -> int:
        return int(self.X.size)

    def get(self, name: str) -> np.ndarray:
        return self.values[name]

    def interp(self, name: str, X: np.ndarray | float) -> np.ndarray:
        return np.interp(X, self.X, self.values[name])

    def clipped(self, x_min: float, x_max: float) -> "Centerline":
        m = (self.X >= x_min) & (self.X <= x_max)
        return Centerline(self.X[m], {k: v[m] for k, v in self.values.items()}, self.method, self.n_cells, self.r_max)


def _group_lstsq(group: np.ndarray, n_groups: int, A: np.ndarray, values: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """グループごとの最小二乗 A c ≈ v の係数(グループ数 × 列数)。正規方程式を bincount で作り、擬似逆行列で解く。"""
    k = A.shape[1]
    M = np.empty((n_groups, k, k))
    for i in range(k):
        for j in range(i, k):
            s = np.bincount(group, weights=A[:, i] * A[:, j], minlength=n_groups)
            M[:, i, j] = s
            M[:, j, i] = s
    Minv = np.linalg.pinv(M, rcond=1e-10)
    out = {}
    for name, v in values.items():
        rhs = np.stack([np.bincount(group, weights=A[:, i] * v, minlength=n_groups) for i in range(k)], axis=1)
        out[name] = np.einsum("gij,gj->gi", Minv, rhs)
    return out


def _layers(x: np.ndarray, decimals: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xr = np.round(x, decimals)
    uniq, inv, counts = np.unique(xr, return_inverse=True, return_counts=True)
    return uniq, inv, counts


def detect_method(x: np.ndarray, decimals: int = 6) -> str:
    """軸の近くのセルの x から、層があるメッシュかどうかを判定する。"""
    if x.size == 0:
        return "bins"
    _, inv, counts = _layers(x, decimals)
    in_layers = counts[inv] >= 3
    return "layers" if np.mean(in_layers) >= 0.9 else "bins"


def extract_centerline(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    values: dict[str, np.ndarray],
    *,
    r_max: float = 0.3,
    x_offset: float = 0.0,
    method: str = "auto",
    bin_width: float | None = None,
    min_cells: int = 3,
    decimals: int = 6,
) -> Centerline:
    """セル中心 (x, y, z)(L_ref 単位)と値から中心線の分布を作る。"""
    x = np.asarray(x, dtype=float)
    r = np.hypot(np.asarray(y, dtype=float), np.asarray(z, dtype=float))
    sel = (r < r_max) & np.isfinite(x)
    for v in values.values():
        sel &= np.isfinite(np.asarray(v, dtype=float))
    xs, rs = x[sel], r[sel]
    vs = {k: np.asarray(v, dtype=float)[sel] for k, v in values.items()}
    if method == "auto":
        method = detect_method(xs, decimals)
    if xs.size == 0:
        return Centerline(np.zeros(0), {k: np.zeros(0) for k in values}, method, 0, r_max)

    if method == "layers":
        uniq, inv, counts = _layers(xs, decimals)
        A = np.stack([np.ones_like(rs), rs**2], axis=1)
        coef = _group_lstsq(inv, uniq.size, A, vs)
        # r の広がりがない層(1 列だけ)では a + b r² が決まらないので平均にする
        r2 = rs**2
        r2_min = np.full(uniq.size, np.inf)
        r2_max = np.full(uniq.size, -np.inf)
        np.minimum.at(r2_min, inv, r2)
        np.maximum.at(r2_max, inv, r2)
        flat = (r2_max - r2_min) <= 1e-12 * max(r_max**2, 1e-300)
        keep = counts >= min_cells
        X = np.bincount(inv, weights=xs, minlength=uniq.size) / np.maximum(counts, 1)  # 層の x(丸める前の平均)
        out = {}
        for k, c in coef.items():
            a = c[:, 0].copy()
            if np.any(flat):
                mean = np.bincount(inv, weights=vs[k], minlength=uniq.size) / np.maximum(counts, 1)
                a[flat] = mean[flat]
            out[k] = a[keep]
        return Centerline(X[keep] - x_offset, out, "layers", int(xs.size), r_max)

    # 区間ごとに a + b (x − x_c) + c r²
    lo, hi = float(xs.min()), float(xs.max())
    if bin_width is None:
        bin_width = float(np.clip((hi - lo) * 30.0 / max(xs.size, 1), 0.02, 0.5))
    nb = max(1, int(np.ceil((hi - lo) / bin_width)))
    edges = lo + bin_width * np.arange(nb + 1)
    idx = np.clip(np.searchsorted(edges, xs, side="right") - 1, 0, nb - 1)
    counts = np.bincount(idx, minlength=nb)
    xc = np.bincount(idx, weights=xs, minlength=nb) / np.maximum(counts, 1)
    A = np.stack([np.ones_like(rs), xs - xc[idx], rs**2], axis=1)
    coef = _group_lstsq(idx, nb, A, vs)
    keep = counts >= max(min_cells, 6)
    out = {k: c[keep, 0] for k, c in coef.items()}
    return Centerline(xc[keep] - x_offset, out, "bins", int(xs.size), r_max)
