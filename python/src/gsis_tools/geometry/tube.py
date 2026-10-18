"""管ケース(論文 §4.1「円管 → 真空」)の形状とメッシュ。

座標は x 軸が管の軸(長さはすべて管半径 R を単位として指定し、最後に R [m] を掛ける)。

- 上流槽: 半径 8R、x ∈ [0, 8R](x = 0 の端面が ``inlet``)
- 管: 半径 R、x ∈ [8R, 18R]
- 下流槽: 半径 4R、x ∈ [18R, 22R](x = 22R の端面が ``outlet``)
- それ以外の境界(槽の側面・槽の段差の面・管の内面)はすべて ``wall``

生成方式:

- :func:`generate_tube_hex` … マルチブロックの構造六面体。断面は O 型格子(中心の正方形 +
  管壁までのリング + 槽の円環)で、断面の格子を x 方向に掃引する(numpy で直接作る)
- :func:`generate_tube_tet` … 非構造の四面体(gmsh の OpenCASCADE で形状を作り、管からの距離で
  セル寸法を変える)

どちらも ``target_cells`` を与えると、細かい部分の寸法を調整してセル数を目標に近づける。
「セル寸法」は h_V = V^(1/3)(体積の 3 乗根)で表す。
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass, field, replace

import numpy as np

from ..builder import BoundaryGroup, polymesh_from_elements
from ..pmesh import PolyMesh
from .grading import _phi_wall, first_cell_to_wall_spacing, graded_nodes, wall_graded_rays

logger = logging.getLogger(__name__)

PATCH_ORDER = ("inlet", "outlet", "wall")
PATCH_KINDS = {"inlet": "inlet", "outlet": "outlet", "wall": "wall"}


@dataclass(frozen=True)
class TubeDims:
    """管ケースの寸法。``radius`` は R [m]、他は R を単位とした値。"""

    radius: float = 1e-3
    r_up: float = 8.0
    l_up: float = 8.0
    l_tube: float = 10.0
    r_down: float = 4.0
    l_down: float = 4.0

    @property
    def x_tube_in(self) -> float:
        return self.l_up

    @property
    def x_tube_out(self) -> float:
        return self.l_up + self.l_tube

    @property
    def length(self) -> float:
        return self.l_up + self.l_tube + self.l_down

    def volume(self) -> float:
        """領域の体積 [m^3](円柱の厳密な値)。"""
        return math.pi * self.radius**3 * (self.r_up**2 * self.l_up + self.l_tube + self.r_down**2 * self.l_down)

    def patch_areas(self) -> dict[str, float]:
        """パッチの面積 [m^2](円の厳密な値)。"""
        R2 = self.radius**2
        wall = (
            2 * math.pi * self.r_up * self.l_up
            + math.pi * (self.r_up**2 - 1.0)
            + 2 * math.pi * self.l_tube
            + math.pi * (self.r_down**2 - 1.0)
            + 2 * math.pi * self.r_down * self.l_down
        )
        return {
            "inlet": math.pi * self.r_up**2 * R2,
            "outlet": math.pi * self.r_down**2 * R2,
            "wall": wall * R2,
        }

    def regions(self, centres: np.ndarray) -> dict[str, np.ndarray]:
        """セル中心から領域(上流槽・管・下流槽、上流槽の遠方)を判定する。"""
        x = centres[:, 0] / self.radius
        r = np.hypot(centres[:, 1], centres[:, 2]) / self.radius
        up = x < self.x_tube_in
        down = x > self.x_tube_out
        tube = ~up & ~down
        dist = np.hypot(np.maximum(r - 1.0, 0.0), np.maximum(self.x_tube_in - x, 0.0))
        return {
            "上流槽": up,
            "上流槽(管の入口から 3R 以上)": up & (dist >= 3.0),
            "管": tube,
            "下流槽": down,
        }


# ---------------------------------------------------------------------------
# (b) マルチブロックの構造六面体(O 型断面)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TubeHexOptions:
    """六面体メッシュの設定(長さは R 単位)。

    - ``n_theta`` … 1/4 周あたりの周方向のセル数(全周で 4 n_theta)
    - ``square`` … 中心の正方形ブロックの半幅
    - ``ds_wall`` … 管壁(r = R)に接するセルの半径方向の幅。槽の円環も r = R でこの幅から始める
    - ``ds_mouth`` … 管の出入口(x = 8R, 18R)に接するセルの軸方向の幅
    - ``h_tube`` … 管の中央部の軸方向の間隔の上限
    - ``h_max`` … 槽の中の半径方向・軸方向の間隔の上限(周方向は 2πr / (4 n_theta))
    - ``growth`` … 隣り合うセルの間隔の比の目安
    - ``h_core`` … リングの中心側の半径方向の間隔(省略時は正方形のセル寸法の √2 倍)
    """

    n_theta: int = 16
    square: float = 0.4
    ds_wall: float = 0.03
    ds_mouth: float = 0.03
    h_tube: float = 0.25
    h_max: float = 0.5
    growth: float = 1.2
    h_core: float | None = None

    def scaled(self, s: float) -> "TubeHexOptions":
        """壁・出入口の最小間隔と管の中の軸方向の間隔を s 倍にした設定(周方向の分割と h_max は変えない)。

        ``target_cells`` に合わせるときに使う。
        """
        return replace(
            self,
            ds_wall=self.ds_wall * s,
            ds_mouth=self.ds_mouth * s,
            h_tube=min(self.h_tube * s, self.h_max),
            h_core=None if self.h_core is None else self.h_core * s,
        )

    def refined(self, f: float) -> "TubeHexOptions":
        """全体を一様に f 倍細かくした設定(周方向の分割を f 倍、全ての間隔を 1/f 倍)。メッシュ依存性の確認用。"""
        return replace(
            self,
            n_theta=max(4, int(round(self.n_theta * f))),
            ds_wall=self.ds_wall / f,
            ds_mouth=self.ds_mouth / f,
            h_tube=self.h_tube / f,
            h_max=self.h_max / f,
            h_core=None if self.h_core is None else self.h_core / f,
        )


@dataclass
class _HexLayout:
    n_theta: int
    theta: np.ndarray  # (4n,)
    ring_r: np.ndarray  # (n_ring+1, 4n)
    ann_up: np.ndarray  # 1 から r_up までの半径
    ann_down: np.ndarray
    x_up: np.ndarray
    x_tube: np.ndarray
    x_down: np.ndarray
    square_u: np.ndarray

    @property
    def n_ring(self) -> int:
        return self.ring_r.shape[0] - 1

    def section_cells(self) -> tuple[int, int, int]:
        n = self.n_theta
        inner = n * n + 4 * n * self.n_ring
        return inner + 4 * n * (self.ann_up.size - 1), inner, inner + 4 * n * (self.ann_down.size - 1)

    def n_cells(self) -> int:
        up, inner, down = self.section_cells()
        return up * (self.x_up.size - 1) + inner * (self.x_tube.size - 1) + down * (self.x_down.size - 1)


def _hex_layout(dims: TubeDims, opt: TubeHexOptions) -> _HexLayout:
    n = int(opt.n_theta)
    a = float(opt.square)
    if not 0.1 <= a <= 0.65:
        raise ValueError("square(中心の正方形の半幅 / R)は 0.1〜0.65 にしてください")
    theta = -0.25 * math.pi + np.arange(4 * n) * (0.5 * math.pi / n)
    rho = a / np.maximum(np.abs(np.cos(theta)), np.abs(np.sin(theta)))
    square_u = a * np.tan(0.25 * math.pi * np.linspace(-1.0, 1.0, n + 1))
    g = opt.growth - 1.0
    h_core = opt.h_core if opt.h_core is not None else a * (0.5 * math.pi / n) * math.sqrt(2.0)
    h_core = max(h_core, opt.ds_wall)
    L = 1.0 - rho
    w = first_cell_to_wall_spacing(opt.ds_wall, opt.growth)
    n_ring = max(1, int(math.ceil(float(_phi_wall(np.array([L.max()]), w, np.array([h_core]), g)[0]) - 1e-9)))
    d = wall_graded_rays(L, n_ring, opt.ds_wall, opt.growth)  # 壁からの距離 (n_ring+1, 4n)
    ring_r = 1.0 - d[::-1, :]
    ring_r[0, :] = rho
    ring_r[-1, :] = 1.0
    ann_up = 1.0 + graded_nodes(dims.r_up - 1.0, opt.h_max, h_start=opt.ds_wall, growth=opt.growth)
    ann_down = 1.0 + graded_nodes(dims.r_down - 1.0, opt.h_max, h_start=opt.ds_wall, growth=opt.growth)
    x_up = graded_nodes(dims.l_up, opt.h_max, h_end=opt.ds_mouth, growth=opt.growth)
    x_tube = dims.l_up + graded_nodes(
        dims.l_tube, min(opt.h_tube, opt.h_max), h_start=opt.ds_mouth, h_end=opt.ds_mouth, growth=opt.growth
    )
    x_down = dims.x_tube_out + graded_nodes(dims.l_down, opt.h_max, h_start=opt.ds_mouth, growth=opt.growth)
    return _HexLayout(n, theta, ring_r, ann_up, ann_down, x_up, x_tube, x_down, square_u)


def _ogrid_section(lay: _HexLayout, ann_r: np.ndarray) -> dict[str, np.ndarray]:
    """O 型格子の断面(2 次元)。節点番号は 正方形 → リング → 円環 の順。

    戻り値: ``yz``(節点座標)、``quads``(反時計回り = 法線 +x)、``annulus_quads``(円環の四角形)、
    ``wall_edges``(r = 1 の辺)、``outer_edges``(外周の辺。円環がなければ r = 1)、``n_inner``。
    """
    n = lay.n_theta
    m4 = 4 * n
    u = lay.square_u
    n_sq = (n + 1) * (n + 1)
    I, J = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
    sq_yz = np.stack([u[I.ravel()], u[J.ravel()]], axis=1)

    def sq(i, j):
        return i * (n + 1) + j

    m = np.arange(n)
    boundary = np.concatenate([sq(n, m), sq(n - m, n), sq(0, n - m), sq(m, 0)])
    cos_t, sin_t = np.cos(lay.theta), np.sin(lay.theta)

    n_ring = lay.n_ring
    ring_yz = np.stack([(lay.ring_r[1:] * cos_t).ravel(), (lay.ring_r[1:] * sin_t).ravel()], axis=1)
    n_inner = n_sq + n_ring * m4
    n_ann = ann_r.size - 1
    ann_yz = np.stack([(ann_r[1:, None] * cos_t).ravel(), (ann_r[1:, None] * sin_t).ravel()], axis=1)
    yz = np.concatenate([sq_yz, ring_yz, ann_yz.reshape(-1, 2)])

    def level(k):
        """リング・円環の第 k 層の節点番号(k = 0 は正方形の境界、k = n_ring は r = 1)。"""
        if k == 0:
            return boundary
        if k <= n_ring:
            return n_sq + (k - 1) * m4 + np.arange(m4)
        return n_inner + (k - n_ring - 1) * m4 + np.arange(m4)

    i, j = (t.ravel() for t in np.meshgrid(np.arange(n), np.arange(n), indexing="ij"))
    q_sq = np.stack([sq(i, j), sq(i + 1, j), sq(i + 1, j + 1), sq(i, j + 1)], axis=1)
    ring_q = []
    for k in range(n_ring + n_ann):
        lo, hi = level(k), level(k + 1)
        ring_q.append(np.stack([lo, hi, np.roll(hi, -1), np.roll(lo, -1)], axis=1))
    q_ring = np.concatenate(ring_q[:n_ring])
    q_ann = np.concatenate(ring_q[n_ring:]) if n_ann else np.zeros((0, 4), dtype=np.int64)
    wall = level(n_ring)
    outer = level(n_ring + n_ann)
    return {
        "yz": yz,
        "quads": np.concatenate([q_sq, q_ring, q_ann]).astype(np.int64),
        "annulus_quads": q_ann.astype(np.int64),
        "wall_edges": np.stack([wall, np.roll(wall, -1)], axis=1).astype(np.int64),
        "outer_edges": np.stack([outer, np.roll(outer, -1)], axis=1).astype(np.int64),
        "n_inner": np.int64(n_inner),
    }


def tube_hex_elements(
    dims: TubeDims = TubeDims(), opt: TubeHexOptions = TubeHexOptions()
) -> tuple[np.ndarray, list[tuple[str, np.ndarray]], list[BoundaryGroup], _HexLayout]:
    """管の構造六面体メッシュの節点・要素・境界グループ(座標は m)。"""
    lay = _hex_layout(dims, opt)
    s_up = _ogrid_section(lay, lay.ann_up)
    s_tb = _ogrid_section(lay, np.array([1.0]))
    s_dn = _ogrid_section(lay, lay.ann_down)
    N_in = int(s_tb["n_inner"])
    m4 = 4 * lay.n_theta
    A_up = (lay.ann_up.size - 1) * m4
    A_dn = (lay.ann_down.size - 1) * m4

    x_all = np.concatenate([lay.x_up, lay.x_tube[1:], lay.x_down[1:]])
    P = x_all.size
    p_t0 = lay.x_up.size - 1  # x = 8R の面
    p_d0 = p_t0 + lay.x_tube.size - 1  # x = 18R の面
    base_up = P * N_in
    base_dn = base_up + (p_t0 + 1) * A_up
    n_nodes = base_dn + (P - p_d0) * A_dn

    pts = np.empty((n_nodes, 3))
    yz_in = s_tb["yz"][:N_in]
    pts[:base_up, 0] = np.repeat(x_all, N_in)
    pts[:base_up, 1:] = np.tile(yz_in, (P, 1))
    yz_up = s_up["yz"][N_in:]
    pts[base_up:base_dn, 0] = np.repeat(x_all[: p_t0 + 1], A_up)
    pts[base_up:base_dn, 1:] = np.tile(yz_up, (p_t0 + 1, 1))
    yz_dn = s_dn["yz"][N_in:]
    pts[base_dn:, 0] = np.repeat(x_all[p_d0:], A_dn)
    pts[base_dn:, 1:] = np.tile(yz_dn, (P - p_d0, 1))
    pts *= dims.radius

    def gid_up(p, l):
        return np.where(l < N_in, p * N_in + l, base_up + p * A_up + (l - N_in))

    def gid_dn(p, l):
        return np.where(l < N_in, p * N_in + l, base_dn + (p - p_d0) * A_dn + (l - N_in))

    def gid_in(p, l):
        return p * N_in + l

    def hexes(Q, planes, gid):
        p = np.asarray(planes)[:, None, None]
        return np.concatenate([gid(p, Q[None]), gid(p + 1, Q[None])], axis=2).reshape(-1, 8)

    def side_quads(E, planes, gid):
        p = np.asarray(planes)[:, None]
        e0, e1 = E[None, :, 0], E[None, :, 1]
        return np.stack([gid(p, e0), gid(p, e1), gid(p + 1, e1), gid(p + 1, e0)], axis=2).reshape(-1, 4)

    conn = np.concatenate(
        [
            hexes(s_up["quads"], np.arange(0, p_t0), gid_up),
            hexes(s_tb["quads"], np.arange(p_t0, p_d0), gid_in),
            hexes(s_dn["quads"], np.arange(p_d0, P - 1), gid_dn),
        ]
    )
    inlet = gid_up(0, s_up["quads"])
    outlet = gid_dn(P - 1, s_dn["quads"])
    wall = [
        side_quads(s_up["outer_edges"], np.arange(0, p_t0), gid_up),
        gid_up(p_t0, s_up["annulus_quads"]),
        side_quads(s_tb["wall_edges"], np.arange(p_t0, p_d0), gid_in),
        gid_dn(p_d0, s_dn["annulus_quads"]),
        side_quads(s_dn["outer_edges"], np.arange(p_d0, P - 1), gid_dn),
    ]
    groups = [
        BoundaryGroup("inlet", "inlet", [inlet]),
        BoundaryGroup("outlet", "outlet", [outlet]),
        BoundaryGroup("wall", "wall", wall),
    ]
    return pts, [("hex", conn)], groups, lay


def ogrid_cylinder(
    radius: float = 1.0,
    length: float = 1.0,
    *,
    n_theta: int = 8,
    n_ring: int | None = None,
    nx: int = 4,
    square: float = 0.4,
    renumber: str | None = None,
) -> PolyMesh:
    """x 軸方向の円柱(半径 ``radius``、x ∈ [0, length])の O 型断面の六面体メッシュ(試験・確認用)。

    リングの半径方向は一様。パッチは ``xmin``・``xmax``(端面)と ``wall``(側面)。
    外周は 4 n_theta 角形で近似されるので、体積は (2 n_theta) sin(π / (2 n_theta)) R² × length。
    """
    n = int(n_theta)
    n_ring = int(n_ring or max(1, n // 2))
    theta = -0.25 * math.pi + np.arange(4 * n) * (0.5 * math.pi / n)
    rho = square / np.maximum(np.abs(np.cos(theta)), np.abs(np.sin(theta)))
    ring_r = rho[None, :] + (1.0 - rho[None, :]) * np.linspace(0.0, 1.0, n_ring + 1)[:, None]
    one = np.array([0.0, 1.0])
    lay = _HexLayout(n, theta, ring_r, np.array([1.0]), np.array([1.0]), one, one, one,
                     square * np.tan(0.25 * math.pi * np.linspace(-1.0, 1.0, n + 1)))
    sec = _ogrid_section(lay, np.array([1.0]))
    N = int(sec["n_inner"])
    x = np.linspace(0.0, length, nx + 1)
    pts = np.empty(((nx + 1) * N, 3))
    pts[:, 0] = np.repeat(x, N)
    pts[:, 1:] = np.tile(sec["yz"][:N] * radius, (nx + 1, 1))
    p = np.arange(nx)[:, None, None]
    Q = sec["quads"][None]
    conn = np.concatenate([p * N + Q, (p + 1) * N + Q], axis=2).reshape(-1, 8)
    E = sec["wall_edges"]
    pe = np.arange(nx)[:, None]
    side = np.stack([pe * N + E[None, :, 0], pe * N + E[None, :, 1], (pe + 1) * N + E[None, :, 1], (pe + 1) * N + E[None, :, 0]], axis=2)
    groups = [
        BoundaryGroup("xmin", "patch", [sec["quads"]]),
        BoundaryGroup("xmax", "patch", [nx * N + sec["quads"]]),
        BoundaryGroup("wall", "wall", [side.reshape(-1, 4)]),
    ]
    return polymesh_from_elements(pts, [("hex", conn)], groups, renumber=renumber)


def _match_hex_target(dims: TubeDims, opt: TubeHexOptions, target: int) -> tuple[TubeHexOptions, float]:
    """セル数が目標に最も近くなる倍率 s(:meth:`TubeHexOptions.scaled`)を探す。

    セル数は節点の配置だけから計算できるので、メッシュを作らずに探せる。
    """

    def count(s: float) -> int:
        return _hex_layout(dims, opt.scaled(s)).n_cells()

    lo, hi = 0.05, 20.0
    if count(hi) > target:
        logger.warning("細かい部分をいくら粗くしてもセル数が目標を超えます(n_theta か h_max を見直してください)")
        return opt.scaled(hi), hi
    if count(lo) < target:
        logger.warning("細かい部分をいくら細かくしてもセル数が目標に届きません(n_theta か h_max を見直してください)")
        return opt.scaled(lo), lo
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if count(mid) > target:
            lo = mid
        else:
            hi = mid
    # セル数は s について階段状なので、近くの候補から最も近いものを選ぶ
    cands = np.geomspace(lo / 1.1, hi * 1.1, 41)
    best = min(cands, key=lambda s: abs(count(s) - target))
    return opt.scaled(float(best)), float(best)


def generate_tube_hex(
    dims: TubeDims = TubeDims(),
    opt: TubeHexOptions = TubeHexOptions(),
    *,
    target_cells: int | None = None,
    renumber: str | None = "rcm",
    msh_path: str | None = None,
) -> tuple[PolyMesh, dict]:
    """管の構造六面体メッシュ。``target_cells`` を与えると、壁・出入口の最小間隔と管の中の
    軸方向の間隔を同じ倍率で変えてセル数を合わせる(周方向の分割 ``n_theta`` と ``h_max`` は変えない)。

    戻り値は (メッシュ, 情報)。情報には使った設定・セル数の内訳・所要時間を入れる。
    """
    t0 = time.perf_counter()
    scale = 1.0
    if target_cells:
        opt, scale = _match_hex_target(dims, opt, int(target_cells))
    pts, elements, groups, lay = tube_hex_elements(dims, opt)
    t1 = time.perf_counter()
    if msh_path:
        from ..gmsh_convert import write_msh_from_elements

        write_msh_from_elements(msh_path, pts, elements, groups)
    md = {
        "source": "gsis_tools.geometry.tube.generate_tube_hex",
        "kind": "hex",
        "radius": repr(dims.radius),
        "options": repr(asdict(opt)),
    }
    mesh = polymesh_from_elements(pts, elements, groups, renumber=renumber, metadata=md)
    t2 = time.perf_counter()
    up, inner, down = lay.section_cells()
    info = {
        "kind": "hex",
        "options": asdict(opt),
        "scale": scale,
        "n_cells": mesh.n_cells,
        "n_ring": lay.n_ring,
        "section_cells": {"上流槽": up, "管": inner, "下流槽": down},
        "layers": {"上流槽": lay.x_up.size - 1, "管": lay.x_tube.size - 1, "下流槽": lay.x_down.size - 1},
        "radial_annulus": {"上流槽": lay.ann_up.size - 1, "下流槽": lay.ann_down.size - 1},
        "time_generate": t1 - t0,
        "time_convert": t2 - t1,
    }
    return mesh, info


# ---------------------------------------------------------------------------
# (a) 非構造の四面体
# ---------------------------------------------------------------------------

# 四面体の h_V = V^(1/3) と gmsh のメッシュサイズの比(gmsh のサイズ ≈ TET_SIZE_FACTOR × h_V)。
# 正四面体なら辺 = 2.04 h_V だが、gmsh の四面体は平均で h_V ≈ 0.6 × サイズだった(実測)
TET_SIZE_FACTOR = 1.7
# セル数の見積り N ≈ C ∫ dV / s^3(s は gmsh のサイズ)の C。管で 13 万セルのときの実測値
# (Delaunay / HXT、いずれも Netgen の最適化後)。生成後に補正する
TET_COUNT_COEFF = {"delaunay": 4.2, "hxt": 3.6}
_ALG3D = {"delaunay": 1, "hxt": 10}


@dataclass(frozen=True)
class TubeTetOptions:
    """四面体メッシュの設定(長さは R 単位、寸法は h_V = V^(1/3) で指定)。

    - ``h_max`` … 槽の中のセル寸法の上限
    - ``h_tube`` … 管の中と出入口付近のセル寸法(``target_cells`` を与えると調整する)
    - ``gradient`` … 管(r ≤ R, 8R ≤ x ≤ 18R)からの距離に対するセル寸法の増加率
    - ``algorithm`` … gmsh の 3 次元アルゴリズム(``"delaunay"`` / ``"hxt"``)
    - ``optimize`` … 生成後の最適化(``"Netgen"`` など。None なら gmsh の標準の最適化のみ)
    """

    h_max: float = 0.5
    h_tube: float = 0.08
    gradient: float = 0.15
    algorithm: str = "delaunay"
    optimize: str | None = "Netgen"
    size_factor: float = TET_SIZE_FACTOR
    threads: int = 8


def _tet_h(dims: TubeDims, opt: TubeTetOptions, x: np.ndarray, r: np.ndarray, h_tube: float | None = None) -> np.ndarray:
    """位置 (x, r)(R 単位)での目標セル寸法 h_V(R 単位)。"""
    ht = opt.h_tube if h_tube is None else h_tube
    dx = np.maximum(np.maximum(dims.x_tube_in - x, 0.0), x - dims.x_tube_out)
    dr = np.maximum(r - 1.0, 0.0)
    return np.minimum(opt.h_max, ht + opt.gradient * np.hypot(dx, dr))


def _size_integral(dims: TubeDims, opt: TubeTetOptions, h_tube: float, step: float = 0.02) -> float:
    """∫ dV / s^3(s は gmsh のサイズ、R 単位)。軸対称なので (x, r) の中点則で計算する。"""
    total = 0.0
    for x0, x1, rmax in (
        (0.0, dims.x_tube_in, dims.r_up),
        (dims.x_tube_in, dims.x_tube_out, 1.0),
        (dims.x_tube_out, dims.length, dims.r_down),
    ):
        nx = max(4, int(math.ceil((x1 - x0) / step)))
        nr = max(4, int(math.ceil(rmax / step)))
        x = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
        r = (np.arange(nr) + 0.5) * rmax / nr
        X, Rr = np.meshgrid(x, r, indexing="ij")
        s = opt.size_factor * _tet_h(dims, opt, X, Rr, h_tube)
        total += float(np.sum(2 * math.pi * Rr / s**3)) * (x1 - x0) / nx * rmax / nr
    return total


def _solve_h_tube(dims: TubeDims, opt: TubeTetOptions, target: int, coeff: float) -> float:
    lo, hi = 0.005, opt.h_max
    if coeff * _size_integral(dims, opt, hi) > target:
        logger.warning("h_tube = h_max でもセル数が目標を超えます(h_max を大きくしてください)")
        return hi
    for _ in range(50):
        mid = math.sqrt(lo * hi)
        if coeff * _size_integral(dims, opt, mid) > target:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def build_tube_occ(dims: TubeDims = TubeDims(), scale: float = 1.0) -> dict[str, list[int]]:
    """現在の gmsh モデルに管の形状(OpenCASCADE、円柱 3 つの和)を作り、Physical Group を付ける。

    座標は R 単位 × ``scale``。2 次元の Physical Group ``inlet``・``outlet``・``wall`` と、
    3 次元の ``fluid`` を作る。戻り値はグループ名 → 曲面(体積)のタグ。
    """
    import gmsh

    occ = gmsh.model.occ
    s = scale
    c1 = occ.addCylinder(0, 0, 0, dims.l_up * s, 0, 0, dims.r_up * s)
    c2 = occ.addCylinder(dims.x_tube_in * s, 0, 0, dims.l_tube * s, 0, 0, 1.0 * s)
    c3 = occ.addCylinder(dims.x_tube_out * s, 0, 0, dims.l_down * s, 0, 0, dims.r_down * s)
    out, _ = occ.fuse([(3, c1)], [(3, c2), (3, c3)])
    occ.synchronize()
    vols = [t for d, t in out if d == 3]
    if len(vols) != 1:
        raise RuntimeError(f"円柱の和が 1 つの体積になりません: {out}")
    tol = 1e-6 * s * dims.length
    groups: dict[str, list[int]] = {"inlet": [], "outlet": [], "wall": []}
    for _, tag in gmsh.model.getBoundary([(3, vols[0])], oriented=False):
        xmin, _, _, xmax, _, _ = gmsh.model.getBoundingBox(2, tag)
        if xmax < tol:
            groups["inlet"].append(tag)
        elif xmin > dims.length * s - tol:
            groups["outlet"].append(tag)
        else:
            groups["wall"].append(tag)
    for name in PATCH_ORDER:
        if not groups[name]:
            raise RuntimeError(f"{name} の曲面が見つかりません")
        gmsh.model.addPhysicalGroup(2, groups[name], name=name)
    gmsh.model.addPhysicalGroup(3, vols, name="fluid")
    groups["fluid"] = vols
    return groups


def _mesh_tet_once(dims: TubeDims, opt: TubeTetOptions, h_tube: float) -> int:
    """現在のモデルに四面体メッシュを作り、要素数を返す(座標は R 単位)。"""
    import gmsh

    alg = _ALG3D[opt.algorithm]
    hmax, grad, f = opt.h_max, opt.gradient, opt.size_factor
    x_in, x_out = dims.x_tube_in, dims.x_tube_out

    def size(dim, tag, x, y, z, lc):  # gmsh から呼ばれる(R 単位)
        dx = x_in - x if x < x_in else (x - x_out if x > x_out else 0.0)
        r = math.sqrt(y * y + z * z)
        dr = r - 1.0 if r > 1.0 else 0.0
        h = h_tube + grad * math.sqrt(dx * dx + dr * dr)
        return f * (hmax if h > hmax else h)

    gmsh.model.mesh.clear()
    gmsh.model.mesh.setSizeCallback(size)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.Algorithm3D", alg)
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("General.NumThreads", int(opt.threads))
    gmsh.model.mesh.generate(3)
    if opt.optimize:
        gmsh.model.mesh.optimize(opt.optimize)
    gmsh.model.mesh.removeSizeCallback()
    return len(gmsh.model.mesh.getElementsByType(4)[0])


def generate_tube_tet(
    dims: TubeDims = TubeDims(),
    opt: TubeTetOptions = TubeTetOptions(),
    *,
    target_cells: int | None = None,
    tolerance: float = 0.05,
    max_passes: int = 3,
    renumber: str | None = "rcm",
    msh_path: str | None = None,
    verbosity: int = 1,
) -> tuple[PolyMesh, dict]:
    """管の四面体メッシュ。``target_cells`` を与えると ``h_tube`` を調整してセル数を合わせる。

    セル数は N ≈ C ∫ dV / s^3 で見積もって ``h_tube`` を決め、生成した結果で C を補正して
    相対差が ``tolerance`` 以下になるまで(最大 ``max_passes`` 回)作り直す。
    """
    from ..gmsh_convert import gmsh_session, polymesh_from_gmsh

    if opt.algorithm not in _ALG3D:
        raise ValueError(f"algorithm は {list(_ALG3D)} のいずれか: {opt.algorithm!r}")
    t0 = time.perf_counter()
    coeff = TET_COUNT_COEFF[opt.algorithm]
    h_tube = opt.h_tube
    passes = []
    with gmsh_session("tube_tet", verbosity=verbosity, threads=opt.threads) as gmsh:
        build_tube_occ(dims)
        for k in range(max_passes if target_cells else 1):
            if target_cells:
                h_tube = _solve_h_tube(dims, opt, int(target_cells), coeff)
            tp = time.perf_counter()
            n = _mesh_tet_once(dims, opt, h_tube)
            passes.append({"h_tube": h_tube, "n_cells": n, "coeff": coeff, "time": time.perf_counter() - tp})
            logger.info("四面体メッシュ: h_tube = %.4f R → %d セル(%.1f s)", h_tube, n, passes[-1]["time"])
            if not target_cells or abs(n / target_cells - 1.0) <= tolerance:
                break
            coeff = n / _size_integral(dims, opt, h_tube)
        t1 = time.perf_counter()
        R = dims.radius
        gmsh.model.mesh.affineTransform([R, 0, 0, 0, 0, R, 0, 0, 0, 0, R, 0])
        if msh_path:
            gmsh.option.setNumber("Mesh.Binary", 1)
            gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
            gmsh.write(str(msh_path))
        md = {
            "source": "gsis_tools.geometry.tube.generate_tube_tet",
            "kind": "tet",
            "radius": repr(dims.radius),
            "options": repr(asdict(replace(opt, h_tube=h_tube))),
        }
        mesh = polymesh_from_gmsh(patch_order=PATCH_ORDER, patch_kinds=PATCH_KINDS, renumber=renumber, metadata=md)
    t2 = time.perf_counter()
    info = {
        "kind": "tet",
        "options": asdict(replace(opt, h_tube=h_tube)),
        "n_cells": mesh.n_cells,
        "passes": passes,
        "time_generate": t1 - t0,
        "time_convert": t2 - t1,
    }
    return mesh, info
