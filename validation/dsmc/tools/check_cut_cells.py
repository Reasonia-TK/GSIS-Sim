"""円筒壁で切られたセルのうち、DSMC コードの体積率の中点積分で「流体 0」と判定されるのに、
実際には流体部分を持つセル(未解像セル)を数える。

DSMC コード(SurfaceGeometry / BuildGasFraction)は、形状と交わるセルの体積率を各軸 N 点
(surfaces.volume_samples)の中点則で求め、体積率 0 のセルに粒子が入ると計算を停止する。
円筒の側面(管軸 = x 軸)で切られるセルは x 方向に一様なので、y-z 断面の 2 次元で判定できる。
x = 0, 10R の環状の壁はセル境界に一致させているので、微小な切れ端を作らない。

判定: 正 N_theta 角形(頂点 θ_k = 2πk/N、外接半径 r·scale)とセル(Δy × Δz)の
    - 厳密な交差面積(多角形のクリッピング)
    - 中点積分の流体点の数(N × N 点)
を比べ、面積 > 0 なのに流体点 0 のセルを「未解像」とする。

使い方:
    uv run --project python python validation/dsmc/tools/check_cut_cells.py --dy 0.25 --samples 16
    uv run --project python python validation/dsmc/tools/check_cut_cells.py --dy 0.1 --samples 16 --search 0.02
"""

from __future__ import annotations

import argparse
import math

import numpy as np

RADII = (1.0, 4.0, 8.0)


def polygon_vertices(r: float, n_theta: int, scale: float) -> np.ndarray:
    th = 2.0 * math.pi * np.arange(n_theta) / n_theta
    c, s = np.cos(th), np.sin(th)
    q = n_theta // 4
    c[::q] = np.round(c[::q])
    s[::q] = np.round(s[::q])
    return np.stack([r * scale * c, r * scale * s], axis=1)


def clip_area(poly: np.ndarray, y0: float, y1: float, z0: float, z1: float) -> float:
    """凸多角形と長方形の交差面積(Sutherland–Hodgman)。"""
    pts = [tuple(p) for p in poly]

    def clip(points, inside, intersect):
        out = []
        n = len(points)
        for i in range(n):
            cur, prev = points[i], points[i - 1]
            ci, pi = inside(cur), inside(prev)
            if ci:
                if not pi:
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif pi:
                out.append(intersect(prev, cur))
        return out

    def ix(axis, value):
        def f(a, b):
            t = (value - a[axis]) / (b[axis] - a[axis])
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        return f

    for axis, value, keep_ge in ((0, y0, True), (0, y1, False), (1, z0, True), (1, z1, False)):
        if not pts:
            return 0.0
        pts = clip(pts, (lambda p, a=axis, v=value, g=keep_ge: p[a] >= v if g else p[a] <= v), ix(axis, value))
    if len(pts) < 3:
        return 0.0
    a = np.array(pts)
    return 0.5 * abs(np.dot(a[:, 0], np.roll(a[:, 1], -1)) - np.dot(a[:, 1], np.roll(a[:, 0], -1)))


def inside_regular_polygon(y, z, r, n_theta, scale):
    """正 N 角形(頂点 θ_k = 2πk/N)の内部判定(ベクトル化)。境界上は内部扱い。"""
    dth = 2.0 * math.pi / n_theta
    phi = np.mod(np.arctan2(z, y), 2.0 * math.pi)
    k = np.floor(phi / dth)
    rr = np.hypot(y, z)
    return rr * np.cos(phi - (k + 0.5) * dth) <= r * scale * math.cos(0.5 * dth)


def count_unresolved(dy: float, dz: float, samples: int, n_theta: int, scale: float,
                     radii=RADII, return_cells=False):
    """1/4 断面(y, z ≥ 0。軸はセルの角)で未解像セルを数える。"""
    result = {}
    cells = []
    for r in radii:
        rc = r * scale
        ny = int(math.ceil(rc / dy)) + 1
        nz = int(math.ceil(rc / dz)) + 1
        poly = polygon_vertices(r, n_theta, scale)
        rin = rc * math.cos(math.pi / n_theta)   # 内接円の半径
        n_cut = n_unres = 0
        min_area_resolved = math.inf
        for j in range(ny):
            for k in range(nz):
                y0, y1, z0, z1 = j * dy, (j + 1) * dy, k * dz, (k + 1) * dz
                rmin = math.hypot(y0, z0)
                rmax = math.hypot(y1, z1)
                if rmax <= rin or rmin >= rc:
                    continue           # 完全に流体 / 完全に固体
                area = clip_area(poly, y0, y1, z0, z1) / (dy * dz)
                if area <= 0.0 or area >= 1.0:
                    continue
                n_cut += 1
                sy = y0 + (np.arange(samples) + 0.5) * dy / samples
                sz = z0 + (np.arange(samples) + 0.5) * dz / samples
                Y, Z = np.meshgrid(sy, sz, indexing="ij")
                n_fluid = int(np.count_nonzero(inside_regular_polygon(Y, Z, r, n_theta, scale)))
                if n_fluid == 0:
                    n_unres += 1
                    cells.append((r, j, k, area))
                else:
                    min_area_resolved = min(min_area_resolved, area)
        result[r] = {"cut_cells": n_cut, "unresolved": n_unres}
    if return_cells:
        return result, cells
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dy", type=float, required=True, help="y, z 方向のセル幅 [R]")
    ap.add_argument("--samples", type=int, default=16, help="surfaces.volume_samples")
    ap.add_argument("--n-theta", type=int, default=128)
    ap.add_argument("--radius-mode", choices=["equal-area", "inscribed"], default="equal-area")
    ap.add_argument("--search", type=float, default=0.0,
                    help="正なら、セル幅を ±search の相対範囲で走査し、未解像セルが 0 の値を探す")
    ap.add_argument("--extent", type=float, default=8.0 + 0.5,
                    help="走査時: y, z の領域幅をセル幅の整数倍にするための最小幅 [R]")
    args = ap.parse_args(argv)
    if args.radius_mode == "equal-area":
        scale = math.sqrt(2.0 * math.pi / (args.n_theta * math.sin(2.0 * math.pi / args.n_theta)))
    else:
        scale = 1.0

    res, cells = count_unresolved(args.dy, args.dy, args.samples, args.n_theta, scale, return_cells=True)
    print(f"dy = {args.dy:.6f} R, samples = {args.samples}: " +
          ", ".join(f"r={r:g}: cut={v['cut_cells']} unresolved={v['unresolved']}" for r, v in res.items()))
    for r, j, k, a in cells[:20]:
        print(f"  未解像: r={r:g}, cell (j,k)=({j},{k}), 流体面積率 {a:.3e}")
    if args.search > 0.0:
        print("走査(未解像セルが 0 の候補):")
        n0 = round(args.extent / args.dy)
        found = 0
        for n in range(int(n0 * (1 - args.search)), int(n0 * (1 + args.search)) + 2):
            # 領域幅 L = n·dy が extent 以上になる dy を、元の dy の近くで探す
            dy = args.extent / n
            if abs(dy / args.dy - 1.0) > args.search:
                continue
            r2 = count_unresolved(dy, dy, args.samples, args.n_theta, scale)
            tot = sum(v["unresolved"] for v in r2.values())
            if tot == 0:
                found += 1
                print(f"  dy = {dy:.8f} R(n = {n}、幅 {n * dy:.4f} R)")
        if not found:
            print("  見つからない")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
