"""管→真空ケース(arXiv:2610.07658 §4.1)の流体領域を、閉じた三角形メッシュ(STL)で作る。

形状(管半径 R を 1 とした無次元座標。管軸は x 軸):
    上流槽  x ∈ [-8, 0],   r ≤ 8
    管      x ∈ [0, 10],   r ≤ 1
    下流槽  x ∈ [10, 14],  r ≤ 4
入口端面(x = -8)と出口端面(x = 14)は、DSMC の計算領域の面で流入・流出を与えるため、
STL の蓋を領域の外へ ext だけ延ばす(DSMC 側の真空モデル A と同じ構成)。

円は正 N 角形(N は 4 の倍数、頂点は θ = 2πk/N)で近似する。頂点を θ = 0, π/2, ... に
置くので、多角形は y = 0 面と z = 0 面に対して鏡映対称になり、1/4 領域の計算に使える。
radius_mode:
    equal-area  外接半径を R·sqrt(2π/(N sin(2π/N))) にし、断面積を円と一致させる(既定)
    inscribed   頂点を円周上に置く(断面積は円より (2π/N)²/6 だけ小さい)
側面の四角形はどれも平面なので、三角形への分割で形状は変わらない。

STL は R 単位のバイナリ形式で書く。DSMC の入力では surface0.scale = R [m] で SI 単位にする。

使い方(リポジトリのルートから):
    uv run --project python python validation/dsmc/tools/tube_geometry.py \
        --out validation/dsmc/tube_vacuum/geometry/tube_vacuum_n128.stl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

import numpy as np

# 論文 Fig.2 の寸法(R 単位)
X_INLET = -8.0
X_TUBE_IN = 0.0
X_TUBE_OUT = 10.0
X_OUTLET = 14.0
R_UP = 8.0
R_TUBE = 1.0
R_DOWN = 4.0


def polygon_scale(n_theta: int, mode: str) -> float:
    """円の半径に対する多角形の外接半径の比。"""
    if mode == "inscribed":
        return 1.0
    if mode == "equal-area":
        return math.sqrt(2.0 * math.pi / (n_theta * math.sin(2.0 * math.pi / n_theta)))
    raise ValueError(f"未知の radius_mode: {mode}")


def polygon_area(r: float, n_theta: int, scale: float) -> float:
    """外接半径 r*scale の正 N 角形の面積。"""
    rc = r * scale
    return 0.5 * n_theta * rc * rc * math.sin(2.0 * math.pi / n_theta)


def build_profile(ext: float, n_theta: int, aspect: float, cap_step: float):
    """子午面の折れ線 (x, r) を、入口の軸上から出口の軸上まで順に返す。

    側面(r 一定)の軸方向の刻みは aspect × (周方向の辺長 2πr/N)、
    環状面(x 一定)の半径方向の刻みは等比(比 1 + aspect·2π/N)、
    蓋(領域外)の半径方向の刻みは cap_step で一様。
    """
    x_in = X_INLET - ext
    x_out = X_OUTLET + ext
    arc = 2.0 * math.pi / n_theta

    def axial(x0, x1, r):
        h = aspect * arc * r
        n = max(1, math.ceil(abs(x1 - x0) / h))
        return [(x0 + (x1 - x0) * i / n, r) for i in range(1, n + 1)]

    def radial_geometric(x, r0, r1):
        ratio = 1.0 + aspect * arc
        n = max(1, math.ceil(abs(math.log(r1 / r0)) / math.log(ratio)))
        return [(x, r0 * (r1 / r0) ** (i / n)) for i in range(1, n + 1)]

    def radial_uniform(x, r0, r1):
        n = max(1, math.ceil(abs(r1 - r0) / cap_step))
        return [(x, r0 + (r1 - r0) * i / n) for i in range(1, n + 1)]

    pts = [(x_in, 0.0)]
    pts += radial_uniform(x_in, 0.0, R_UP)                # 入口の蓋(領域外)
    pts += axial(x_in, X_TUBE_IN, R_UP)                   # 上流槽の側面
    pts += radial_geometric(X_TUBE_IN, R_UP, R_TUBE)      # 上流槽の下流側の壁(環状)
    pts += axial(X_TUBE_IN, X_TUBE_OUT, R_TUBE)           # 管の側面
    pts += radial_geometric(X_TUBE_OUT, R_TUBE, R_DOWN)   # 下流槽の上流側の壁(環状)
    pts += axial(X_TUBE_OUT, x_out, R_DOWN)               # 下流槽の側面
    pts += radial_uniform(x_out, R_DOWN, 0.0)             # 出口の蓋(領域外)
    # 丸め誤差で 0 にならない点を厳密に 0 へ
    return [(x, 0.0 if abs(r) < 1e-12 else r) for x, r in pts]


def build_mesh(profile, n_theta: int, scale: float):
    """子午面の折れ線を回転させて、頂点と三角形(頂点番号)を作る。"""
    theta = 2.0 * math.pi * np.arange(n_theta) / n_theta
    cos_t = np.cos(theta)
    sin_t = np.sin(theta)
    # θ = kπ/2 の頂点は厳密に軸上の値にして、鏡映対称を丸め誤差なしで保つ
    q = n_theta // 4
    for k in range(n_theta):
        if k % q == 0:
            cos_t[k] = round(math.cos(theta[k]))
            sin_t[k] = round(math.sin(theta[k]))

    vertices = []
    rings = []  # 各節点の頂点番号の配列(軸上の点は長さ 1)
    for x, r in profile:
        if r == 0.0:
            rings.append(np.array([len(vertices)]))
            vertices.append((x, 0.0, 0.0))
        else:
            rc = r * scale
            idx = np.arange(len(vertices), len(vertices) + n_theta)
            for k in range(n_theta):
                vertices.append((x, rc * cos_t[k], rc * sin_t[k]))
            rings.append(idx)

    tris = []
    for a, b in zip(rings[:-1], rings[1:]):
        if len(a) == 1 and len(b) == 1:
            raise ValueError("軸上の点が連続しています")
        for k in range(n_theta):
            k1 = (k + 1) % n_theta
            if len(a) == 1:
                tris.append((a[0], b[k1], b[k]))
            elif len(b) == 1:
                tris.append((a[k], a[k1], b[0]))
            else:
                tris.append((a[k], a[k1], b[k1]))
                tris.append((a[k], b[k1], b[k]))
    return np.array(vertices, dtype=np.float64), np.array(tris, dtype=np.int64)


def check_mesh(vertices: np.ndarray, tris: np.ndarray) -> dict:
    """閉鎖性・向き・体積・鏡映対称性を独立に検査する。"""
    p = vertices[tris]
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    twice_area = np.linalg.norm(cross, axis=1)
    if np.any(twice_area <= 0.0):
        raise ValueError("面積 0 の三角形があります")
    directed = tris[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    keys = np.sort(directed, axis=1)
    uniq, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    if np.any(counts != 2):
        raise ValueError(f"閉じた多様体ではありません({int(np.sum(counts != 2))} 本の辺)")
    sign = np.where(directed[:, 0] < directed[:, 1], 1, -1)
    if np.any(np.bincount(inverse.ravel(), weights=sign) != 0):
        raise ValueError("共有辺の向きがそろっていません")
    volume = float(np.sum(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2]))) / 6.0)
    # 鏡映対称性: 頂点集合が y → -y, z → -z で不変
    def canon(v):
        return np.unique(np.round(v, 12), axis=0)
    base = canon(vertices)
    sym_y = np.array_equal(base, canon(vertices * [1.0, -1.0, 1.0]))
    sym_z = np.array_equal(base, canon(vertices * [1.0, 1.0, -1.0]))
    return {
        "triangles": int(len(tris)),
        "vertices": int(len(vertices)),
        "edges": int(len(uniq)),
        "closed_manifold": True,
        "consistent_orientation": True,
        "signed_volume": volume,
        "min_triangle_area": float(0.5 * twice_area.min()),
        "mirror_symmetric_y": bool(sym_y),
        "mirror_symmetric_z": bool(sym_z),
    }


def write_binary_stl(path: Path, vertices: np.ndarray, tris: np.ndarray) -> None:
    p = vertices[tris]
    normal = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    record = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    record["n"] = normal
    record["v"] = p
    header = b"GSIS-PBC tube_vacuum fluid region (units: R)".ljust(80, b" ")
    with open(path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(tris)))
        f.write(record.tobytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="出力 STL(R 単位)")
    ap.add_argument("--n-theta", type=int, default=128, help="周方向の分割数(4 の倍数)")
    ap.add_argument("--aspect", type=float, default=4.0, help="側面の三角形の縦横比の目安")
    ap.add_argument("--cap-step", type=float, default=1.0, help="領域外の蓋の半径方向の刻み [R]")
    ap.add_argument("--ext", type=float, default=0.1, help="入口・出口の蓋を領域外へ延ばす長さ [R]")
    ap.add_argument("--radius-mode", choices=["equal-area", "inscribed"], default="equal-area")
    args = ap.parse_args(argv)
    if args.n_theta % 4 != 0 or args.n_theta < 8:
        ap.error("--n-theta は 8 以上の 4 の倍数にしてください")
    if args.ext <= 0.0:
        ap.error("--ext は正にしてください(蓋は計算領域の外に置く)")

    scale = polygon_scale(args.n_theta, args.radius_mode)
    profile = build_profile(args.ext, args.n_theta, args.aspect, args.cap_step)
    vertices, tris = build_mesh(profile, args.n_theta, scale)
    checks = check_mesh(vertices, tris)
    if checks["signed_volume"] < 0.0:  # 外向き法線にそろえる
        tris = tris[:, [0, 2, 1]]
        checks = check_mesh(vertices, tris)

    # 多角形近似での解析的な体積と比べる
    a_up = polygon_area(R_UP, args.n_theta, scale)
    a_tube = polygon_area(R_TUBE, args.n_theta, scale)
    a_down = polygon_area(R_DOWN, args.n_theta, scale)
    v_poly = (a_up * (X_TUBE_IN - X_INLET + args.ext) + a_tube * (X_TUBE_OUT - X_TUBE_IN)
              + a_down * (X_OUTLET - X_TUBE_OUT + args.ext))
    rel = abs(checks["signed_volume"] - v_poly) / v_poly
    if rel > 1e-12:
        raise SystemExit(f"体積が解析値と一致しません(相対差 {rel:.3e})")
    if not (checks["mirror_symmetric_y"] and checks["mirror_symmetric_z"]):
        raise SystemExit("y=0 / z=0 面に対して鏡映対称になっていません")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_binary_stl(args.out, vertices, tris)
    sha = hashlib.sha256(args.out.read_bytes()).hexdigest()
    # 計算領域内(x ∈ [-8, 14])の流体体積(円との比較用)
    v_domain_poly = a_up * 8.0 + a_tube * 10.0 + a_down * 4.0
    v_domain_circle = math.pi * (R_UP**2 * 8.0 + R_TUBE**2 * 10.0 + R_DOWN**2 * 4.0)
    meta = {
        "description": "管→真空ケースの流体領域(内部が流体、fluid_inside = 1)。座標は R 単位",
        "stl": args.out.name,
        "stl_sha256": sha,
        "units": "R (管半径)。DSMC 入力で surface0.scale = R[m] とする",
        "n_theta": args.n_theta,
        "radius_mode": args.radius_mode,
        "polygon_circumradius_over_radius": scale,
        "aspect": args.aspect,
        "cap_step": args.cap_step,
        "cap_extension_outside_domain": args.ext,
        "dimensions": {
            "upstream": {"x": [X_INLET, X_TUBE_IN], "r": R_UP},
            "tube": {"x": [X_TUBE_IN, X_TUBE_OUT], "r": R_TUBE},
            "downstream": {"x": [X_TUBE_OUT, X_OUTLET], "r": R_DOWN},
            "stl_x_extent": [X_INLET - args.ext, X_OUTLET + args.ext],
        },
        "cross_section_area_over_circle": {
            "upstream": a_up / (math.pi * R_UP**2),
            "tube": a_tube / (math.pi * R_TUBE**2),
            "downstream": a_down / (math.pi * R_DOWN**2),
        },
        "tube_perimeter_over_circle": args.n_theta * 2.0 * scale * math.sin(math.pi / args.n_theta) / (2.0 * math.pi),
        "fluid_volume_in_domain_full": v_domain_poly,
        "fluid_volume_in_domain_full_circle": v_domain_circle,
        "fluid_volume_in_domain_quarter": v_domain_poly / 4.0,
        "stl_volume": checks["signed_volume"],
        "stl_volume_relative_error_vs_polygon": rel,
        "checks": checks,
    }
    json_path = args.out.with_suffix(".json")
    json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("stl", "n_theta", "radius_mode", "stl_volume",
                                           "fluid_volume_in_domain_quarter")}, ensure_ascii=False))
    print(f"triangles = {checks['triangles']}, vertices = {checks['vertices']}, sha256 = {sha[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
