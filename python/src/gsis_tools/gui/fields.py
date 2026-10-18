"""VTU の場(pyvista)の読み込みと、表示に使うものを作る処理。

- 表示用の値(成分・無次元化)、任意の断面(原点と法線)、等値線
- 流線: セル値の速度を点へ補間し(``cell_data_to_point_data``)、種(円盤・線分・点の群)から積分する。
  断面上の 2 次元の流線は、断面に投影した速度を断面の上で積分する(``surface_streamlines``)
- 線上の分布: 任意の 2 点を結ぶ線の上で、点へ補間した値をサンプリングする

重い処理(:func:`build_vis`・:func:`sample_line`・:func:`load_bundle`)は別スレッドから呼ぶ。同じ結果の
データに対する処理は 1 つずつ順に行う前提(画面の JobRunner が順に流す)。Qt には依存しない。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .centerline import Centerline
from .history import History, read_history
from .results import RunResult, Scales, centerline_from_arrays, centerline_from_cells_csv, centerline_inputs_from_grid, load_run

# 名前 → (画面の名前, SI の単位, 無次元の書き方, 基準量の種類)
FIELD_INFO: dict[str, tuple[str, str, str, str | None]] = {
    "p": ("圧力 p", "Pa", "p / p_in", "pressure"),
    "rho": ("密度 ρ", "kg/m³", "ρ / ρ_in", "density"),
    "u": ("速度 u", "m/s", "u / √(2RT0)", "velocity2"),
    "U": ("速度 u", "m/s", "u / √(2RT0)", "velocity2"),
    "T": ("温度 T", "K", "T / T0", "temperature"),
    "Ma": ("マッハ数 Ma", "", "Ma", None),
    "Kn_local": ("局所 Kn", "", "Kn_local", None),
    "q": ("熱流束 q", "W/m²", "q / (p_in √(RT0))", "heat_flux"),
    "sigma": ("応力 σ", "Pa", "σ / p_in", "pressure"),
    "residual": ("残差", "", "residual", None),
    "residual_mass_nondim": ("質量の残差(無次元)", "", "residual_mass", None),
}
# 無次元の書き方・単位は 3D 表示の色の帯(VTK の文字)にも使うので、日本語を入れない(VTK の字体に字がない)
FIELD_ORDER = ("p", "rho", "u", "U", "T", "Ma", "Kn_local", "q", "sigma", "residual", "residual_mass_nondim")
HIDDEN = {"cell_id", "vtkOriginalCellIds", "vtkOriginalPointIds", "__show__", "vtkValidPointMask", "Distance",
          "Texture Coordinates"}
SIGMA_COMPONENTS = ("xx", "yy", "zz", "xy", "yz", "xz")
SHOW = "__show__"
# 既定の斜めの視点の向き(注視点からカメラへ)。管の軸(x)を横に、少し上(+z)と手前(−y)から見る
OBLIQUE_VIEW = (-0.6, -1.0, 0.85)
SPEED = "__speed__"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    n_comp: int
    label: str


def list_fields(ds: Any, assoc: str = "cell") -> list[FieldSpec]:
    data = ds.cell_data if assoc == "cell" else ds.point_data
    names = [n for n in data.keys() if n not in HIDDEN]
    order = {n: i for i, n in enumerate(FIELD_ORDER)}
    names.sort(key=lambda n: (order.get(n, 100), n))
    out = []
    for n in names:
        a = np.asarray(data[n])
        nc = 1 if a.ndim == 1 else int(a.shape[1])
        out.append(FieldSpec(n, nc, FIELD_INFO.get(n, (n, "", n, None))[0]))
    return out


def components(spec: FieldSpec) -> list[tuple[str, str]]:
    """成分の選択肢 (キー, 名前)。"""
    if spec.n_comp == 1:
        return [("value", "値")]
    if spec.n_comp == 3:
        return [("mag", "大きさ"), ("0", "x 成分"), ("1", "y 成分"), ("2", "z 成分")]
    if spec.n_comp == 6 and spec.name == "sigma":
        return [(str(i), f"{c} 成分") for i, c in enumerate(SIGMA_COMPONENTS)]
    return [(str(i), f"成分 {i}") for i in range(spec.n_comp)]


def _symbol_with_component(symbol: str, spec: "FieldSpec | None", comp: str) -> str:
    """記号に成分を付ける(u → u_x、|u|、σ → σ_xx)。"""
    if spec is None or spec.n_comp == 1:
        return symbol
    if comp == "mag":
        return f"|{symbol}|"
    if spec.n_comp == 6 and spec.name == "sigma":
        return f"{symbol}_{SIGMA_COMPONENTS[int(comp)]}"
    if spec.n_comp == 3:
        return f"{symbol}_{'xyz'[int(comp)]}"
    return f"{symbol}[{comp}]"


def scale_for(name: str, scales: Scales | None, nondim: bool, spec: "FieldSpec | None" = None,
              comp: str = "value") -> tuple[float, str]:
    """表示の倍率と色の帯の名前(成分を含む)。無次元にできなければ SI のまま。"""
    label, unit, nd_label, kind = FIELD_INFO.get(name, (name, "", name, None))
    head, _, rest = nd_label.partition(" / ")
    symbol = _symbol_with_component(head, spec, comp)
    if nondim and scales is not None and kind is not None:
        ref = {
            "pressure": scales.pressure, "density": scales.density, "velocity2": math.sqrt(2.0) * scales.velocity,
            "temperature": scales.temperature, "heat_flux": scales.heat_flux,
        }[kind]
        return 1.0 / ref, f"{symbol} / {rest}" if rest else symbol
    if kind is None:
        return 1.0, symbol
    return 1.0, f"{symbol} [{unit}]" if unit else symbol


def component_values(ds: Any, name: str, comp: str, assoc: str = "cell") -> np.ndarray:
    data = ds.cell_data if assoc == "cell" else ds.point_data
    a = np.asarray(data[name], dtype=float)
    if a.ndim == 1:
        return a
    if comp == "mag":
        return np.linalg.norm(a, axis=1)
    return a[:, int(comp)]


def component_suffix(spec: FieldSpec, comp: str) -> str:
    if spec.n_comp == 1:
        return ""
    if comp == "mag":
        return " の大きさ"
    if spec.n_comp == 6 and spec.name == "sigma":
        return f"_{SIGMA_COMPONENTS[int(comp)]}"
    return "_" + "xyz"[int(comp)] if spec.n_comp == 3 else f"[{comp}]"


def set_show(ds: Any, name: str, comp: str, factor: float) -> np.ndarray:
    """表示する値を ``__show__`` のセルデータにする。"""
    v = component_values(ds, name, comp) * factor
    ds.cell_data[SHOW] = v
    return v


def velocity_name(ds: Any, assoc: str = "cell") -> str | None:
    data = ds.cell_data if assoc == "cell" else ds.point_data
    for n in ("u", "U"):
        if n in data.keys():
            return n
    return None


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------


@dataclass
class ResultBundle:
    """結果の画面で使うもの一式(別スレッドで読む)。"""

    run: RunResult
    grid: Any = None  # セルの UnstructuredGrid(セル値、SI)
    surface: Any = None  # 領域の表面(セル値を持つ)
    boundary: Any = None  # boundary.vtu
    centerline: Centerline | None = None
    centerline_source: str = ""
    history: History | None = None
    load_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    # 別スレッドの処理で作って覚えておくもの
    point_grid: Any = None  # セル値を点へ補間した格子(流線・線上の分布)
    stream_cache: dict = field(default_factory=dict)

    @property
    def bounds(self) -> tuple[float, ...] | None:
        return None if self.grid is None else tuple(self.grid.bounds)

    @property
    def length(self) -> float:
        """L_ref [m](座標を L_ref 単位にする)。"""
        return self.run.scales.length if self.run.scales is not None else 1.0


def compute_centerline(bundle: ResultBundle, *, r_max: float = 0.3, x_offset: float = 8.0) -> None:
    run = bundle.run
    if bundle.grid is not None and run.scales is not None:
        c, v = centerline_inputs_from_grid(bundle.grid, run.scales)
        bundle.centerline = centerline_from_arrays(c, v, r_max=r_max, x_offset=x_offset)
        bundle.centerline_source = f"{run.cell_vtu.name} のセル値"
    elif run.cells_csv is not None:
        bundle.centerline = centerline_from_cells_csv(run.cells_csv, r_max=r_max, x_offset=x_offset)
        bundle.centerline_source = "cells.csv"
    else:
        bundle.centerline = None
        bundle.centerline_source = "(VTU も cells.csv もないので作れない)"


def load_bundle(directory: str | Path, root: Path | None = None, *, with_grid: bool = True,
                r_max: float = 0.3, x_offset: float = 8.0) -> ResultBundle:
    """出力フォルダを読む(VTU・中心線・履歴)。別スレッドから呼んでよい。"""
    import pyvista as pv

    t0 = time.perf_counter()
    run = load_run(directory, root)
    b = ResultBundle(run=run)
    if with_grid and run.cell_vtu is not None:
        try:
            g = pv.read(run.cell_vtu)
            b.grid = g
            b.surface = g.extract_surface(algorithm="dataset_surface")
        except Exception as exc:
            b.errors.append(f"{run.cell_vtu.name} を読めません: {exc}")
    if with_grid and run.boundary_vtu is not None:
        try:
            b.boundary = pv.read(run.boundary_vtu)
        except Exception as exc:
            b.errors.append(f"boundary.vtu を読めません: {exc}")
    try:
        compute_centerline(b, r_max=r_max, x_offset=x_offset)
    except Exception as exc:
        b.errors.append(f"中心線を作れません: {exc}")
    if run.history_path is not None:
        try:
            b.history = read_history(run.history_path)
        except Exception as exc:
            b.errors.append(f"history.csv を読めません: {exc}")
    b.load_time = time.perf_counter() - t0
    return b


def point_grid(bundle: ResultBundle) -> Any:
    """セル値を点へ補間した格子(初回だけ作って覚える。13.5 万セルの多面体で 0.1 秒前後)。"""
    if bundle.point_grid is None:
        import pyvista as pv

        g = bundle.grid
        small = pv.UnstructuredGrid()
        small.copy_structure(g)
        for name in g.cell_data.keys():
            if name not in HIDDEN:
                small.cell_data[name] = g.cell_data[name]
        bundle.point_grid = small.cell_data_to_point_data()
    return bundle.point_grid


# ---------------------------------------------------------------------------
# 断面
# ---------------------------------------------------------------------------

# 断面のプリセット: キー → (名前, 原点 [L_ref], 法線)。管の軸は x、管は x = 8〜18(X = x − 8)
PLANE_PRESETS: dict[str, tuple[str, tuple[float, float, float] | None, tuple[float, float, float] | None]] = {
    "z0": ("z = 0(xy 面、管の軸を含む)", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "y0": ("y = 0(xz 面、管の軸を含む)", (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "x": ("x 一定(yz 面、管を横切る。既定 X = 5)", (13.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    "free": ("自由(原点と法線を入力)", None, None),
}


@dataclass(frozen=True)
class PlaneSpec:
    """断面(原点は L_ref 単位)。"""

    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)

    def unit_normal(self) -> np.ndarray:
        return unit(self.normal)


def unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=float).reshape(3)
    n = float(np.linalg.norm(a))
    if not n > 0 or not math.isfinite(n):
        raise ValueError(f"向きのベクトルが 0 です: {tuple(a)}")
    return a / n


def view_up_for(normal) -> tuple[float, float, float]:
    """法線の向きから見るときの上向き(z 軸に近い法線なら y、それ以外は z)。"""
    n = unit(normal)
    return (0.0, 1.0, 0.0) if abs(n[2]) > 0.9 else (0.0, 0.0, 1.0)


def plane_basis(normal) -> tuple[np.ndarray, np.ndarray]:
    """法線に垂直な正規直交の 2 本(円盤の点を並べるのに使う)。"""
    n = unit(normal)
    helper = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(n, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    return e1, e2


def plane_offset_range(bounds, plane: PlaneSpec, L: float) -> tuple[float, float]:
    """領域(bounds、m)の範囲で、原点から法線の向きに動かせる距離の範囲(L_ref 単位)。"""
    n = plane.unit_normal()
    o = np.asarray(plane.origin, dtype=float) * L
    xs = (bounds[0], bounds[1])
    ys = (bounds[2], bounds[3])
    zs = (bounds[4], bounds[5])
    d = [float(np.dot(np.array([x, y, z]) - o, n)) / L for x in xs for y in ys for z in zs]
    return min(d), max(d)


# ---------------------------------------------------------------------------
# 流線
# ---------------------------------------------------------------------------

SEED_KINDS = {
    "disk": "円盤(既定: 管の入口の手前)",
    "line": "線分",
    "cloud": "点の群(球の中)",
    "plane2d": "断面上の 2 次元の流線",
}
DIRECTIONS = {"both": "両方", "forward": "流れの向き(前へ)", "backward": "逆向き(後ろへ)"}


@dataclass(frozen=True)
class StreamSpec:
    """流線の設定(長さは L_ref 単位)。"""

    kind: str = "disk"
    center: tuple[float, float, float] = (7.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (1.0, 0.0, 0.0)
    radius: float = 1.5
    a: tuple[float, float, float] = (7.0, -1.5, 0.0)
    b: tuple[float, float, float] = (7.0, 1.5, 0.0)
    n: int = 60
    direction: str = "both"
    max_length: float = 80.0


def disk_points(center, normal, radius: float, n: int) -> np.ndarray:
    """円盤の上に n 点をほぼ一様に並べる(ひまわりの種の並び)。"""
    n = max(int(n), 1)
    e1, e2 = plane_basis(normal)
    i = np.arange(n)
    r = radius * np.sqrt((i + 0.5) / n)
    th = i * math.pi * (3.0 - math.sqrt(5.0))
    return np.asarray(center, dtype=float) + np.outer(r * np.cos(th), e1) + np.outer(r * np.sin(th), e2)


def line_points(a, b, n: int) -> np.ndarray:
    n = max(int(n), 1)
    t = np.linspace(0.0, 1.0, n) if n > 1 else np.array([0.5])
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return a + np.outer(t, b - a)


def cloud_points(center, radius: float, n: int, seed: int = 0) -> np.ndarray:
    """球の中に n 点を乱数で置く(同じ設定なら毎回同じ点)。"""
    n = max(int(n), 1)
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1)[:, None]
    r = radius * rng.random(n) ** (1.0 / 3.0)
    return np.asarray(center, dtype=float) + v * r[:, None]


def seed_points(spec: StreamSpec) -> np.ndarray:
    """種の点(L_ref 単位)。``plane2d`` は断面から決めるのでここでは作らない。"""
    if spec.kind == "disk":
        return disk_points(spec.center, spec.normal, spec.radius, spec.n)
    if spec.kind == "line":
        return line_points(spec.a, spec.b, spec.n)
    if spec.kind == "cloud":
        return cloud_points(spec.center, spec.radius, spec.n)
    raise ValueError(f"種の置き方 {spec.kind!r} は点を作れません")


def _tracer_kwargs(direction: str, max_length_m: float) -> dict:
    if direction not in DIRECTIONS:
        raise ValueError(f"積分の向きは {list(DIRECTIONS)} のどれかです: {direction}")
    return dict(integration_direction=direction, max_steps=6000, initial_step_length=0.5, step_unit="cl",
                min_step_length=0.01, max_step_length=1.0, max_length=max_length_m, terminal_speed=1e-12)


def compute_streamlines(pg: Any, seeds_m: np.ndarray, direction: str = "both", max_length_m: float = 1.0,
                        vectors: str | None = None) -> Any:
    """点へ補間した格子 ``pg`` の上で、種(m 単位の点)から流線を積分する。点データ ``__speed__`` に速さ。"""
    import pyvista as pv

    vec = vectors or velocity_name(pg, "point")
    if vec is None:
        raise ValueError("速度の場(u / U)がありません")
    lines = pg.streamlines_from_source(pv.PolyData(np.asarray(seeds_m, dtype=float)), vectors=vec,
                                       **_tracer_kwargs(direction, max_length_m))
    if lines.n_points:
        lines.point_data[SPEED] = np.linalg.norm(np.asarray(lines.point_data[vec]), axis=1)
    return lines


def compute_plane_streamlines(pg: Any, plane: PlaneSpec, L: float, n: int, direction: str = "both",
                              max_length_m: float = 1.0) -> tuple[Any, Any]:
    """断面上の 2 次元の流線(断面に投影した速度を断面の上で積分する)。戻り値は (流線, 断面)。

    種は断面のセルの中心から n 個を選ぶ(同じ断面なら毎回同じ)。点データ ``__speed__`` は面内の速さ。
    """
    import pyvista as pv

    vec = velocity_name(pg, "point")
    if vec is None:
        raise ValueError("速度の場(u / U)がありません")
    nrm = plane.unit_normal()
    sl = pg.slice(normal=tuple(nrm), origin=tuple(np.asarray(plane.origin) * L))
    if sl.n_cells == 0:
        return pv.PolyData(), sl
    u = np.asarray(sl.point_data[vec], dtype=float)
    up = u - np.outer(u @ nrm, nrm)
    sl.point_data["__u_plane__"] = up
    cc = np.asarray(sl.cell_centers().points)
    rng = np.random.default_rng(0)
    pick = np.sort(rng.choice(cc.shape[0], size=min(max(int(n), 1), cc.shape[0]), replace=False))
    lines = sl.streamlines_from_source(pv.PolyData(cc[pick]), vectors="__u_plane__", surface_streamlines=True,
                                       **_tracer_kwargs(direction, max_length_m))
    if lines.n_points:
        lines.point_data[SPEED] = np.linalg.norm(np.asarray(lines.point_data["__u_plane__"]), axis=1)
    return lines, sl


# ---------------------------------------------------------------------------
# 線上の分布
# ---------------------------------------------------------------------------

LINE_PRESETS: dict[str, tuple[str, tuple[float, float, float] | None, tuple[float, float, float] | None]] = {
    "axis_tube": ("中心線(管の中、X = 0〜10)", (8.0, 0.0, 0.0), (18.0, 0.0, 0.0)),
    "axis_all": ("中心線(全体、x = 0〜22)", (0.0, 0.0, 0.0), (22.0, 0.0, 0.0)),
    "diam_mid": ("管の直径(X = 5、y 方向)", (13.0, -1.0, 0.0), (13.0, 1.0, 0.0)),
    "diam_exit": ("管の出口の直径(X = 9.9、y 方向)", (17.9, -1.0, 0.0), (17.9, 1.0, 0.0)),
    "jet": ("下流槽の断面(x = 20、y 方向)", (20.0, -4.0, 0.0), (20.0, 4.0, 0.0)),
    "free": ("任意(2 点を入力)", None, None),
}


@dataclass
class LineProfile:
    """線上の分布。座標と距離は L_ref 単位、値は SI(``values`` は名前 → 配列。ベクトルは成分に分ける)。"""

    a: tuple[float, float, float]
    b: tuple[float, float, float]
    s: np.ndarray
    points: np.ndarray
    valid: np.ndarray
    values: dict[str, np.ndarray] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def n_valid(self) -> int:
        return int(np.count_nonzero(self.valid))


def profile_columns(pg: Any) -> list[tuple[str, str, str]]:
    """線上の分布で使える量: (キー, 場の名前, 成分)。ベクトルは x・y・z 成分と大きさに分ける。"""
    out = []
    for spec in list_fields(pg, "point"):
        if spec.n_comp == 1:
            out.append((spec.name, spec.name, "value"))
        elif spec.n_comp == 3:
            for c, s in (("0", "x"), ("1", "y"), ("2", "z")):
                out.append((f"{spec.name}_{s}", spec.name, c))
            out.append((f"|{spec.name}|", spec.name, "mag"))
        elif spec.n_comp == 6 and spec.name == "sigma":
            for i, c in enumerate(SIGMA_COMPONENTS):
                out.append((f"sigma_{c}", spec.name, str(i)))
    return out


def sample_line(bundle: ResultBundle, a, b, n: int = 300) -> LineProfile:
    """2 点 a・b(L_ref 単位)を結ぶ線の上の n 点で、点へ補間した値を取る(領域の外の点は nan)。"""
    t0 = time.perf_counter()
    if bundle.grid is None:
        raise ValueError("VTU がないので線上の分布を作れません")
    L = bundle.length
    pg = point_grid(bundle)
    a = tuple(float(v) for v in a)
    b = tuple(float(v) for v in b)
    if np.allclose(a, b):
        raise ValueError("2 点が同じです")
    n = max(int(n), 2)
    out = pg.sample_over_line(np.asarray(a) * L, np.asarray(b) * L, resolution=n - 1)
    valid = np.asarray(out.point_data["vtkValidPointMask"]).astype(bool)
    pts = np.asarray(out.points, dtype=float) / L
    s = np.linalg.norm(pts - np.asarray(a), axis=1)
    prof = LineProfile(a=a, b=b, s=s, points=pts, valid=valid)
    for key, name, comp in profile_columns(pg):
        v = component_values(out, name, comp, "point").astype(float)
        v[~valid] = np.nan
        prof.values[key] = v
    prof.elapsed = time.perf_counter() - t0
    return prof


def split_profile_key(key: str) -> tuple[str, str]:
    """線上の分布の列のキーを (場の名前, 成分) に分ける(``u_x`` → (u, 0)、``|u|`` → (u, mag)、``p`` → (p, value))。"""
    if key.startswith("|") and key.endswith("|"):
        return key[1:-1], "mag"
    head, sep, suffix = key.partition("_")
    if sep and head in FIELD_INFO and key not in FIELD_INFO:
        if suffix in ("x", "y", "z"):
            return head, str("xyz".index(suffix))
        if suffix in SIGMA_COMPONENTS:
            return head, str(SIGMA_COMPONENTS.index(suffix))
    return key, "value"


def profile_scale(key: str, scales: Scales | None, nondim: bool) -> tuple[float, str]:
    """線上の分布の列の倍率と軸の名前(``u_x`` → u_x / √(2RT0) など)。"""
    name, comp = split_profile_key(key)
    n_comp = 1 if comp == "value" else (6 if name == "sigma" else 3)
    return scale_for(name, scales, nondim, FieldSpec(name, n_comp, name), comp)


_ASCII_REF = {"pressure": "p_in", "density": "rho_in", "velocity2": "sqrt(2RT0)", "temperature": "T0",
              "heat_flux": "(p_in*sqrt(RT0))"}
_ASCII_UNIT = {"Pa": "Pa", "kg/m³": "kg/m3", "m/s": "m/s", "K": "K", "W/m²": "W/m2"}


def csv_column_name(key: str, scales: Scales | None, nondim: bool) -> str:
    """CSV の列名(ASCII)。例: ``p/p_in``、``u_x/sqrt(2RT0)``、``|u|/sqrt(2RT0)``、``T[K]``、``Ma``。"""
    name, _comp = split_profile_key(key)
    _label, unit_si, _nd, kind = FIELD_INFO.get(name, (name, "", name, None))
    if kind is None:
        return key
    if nondim and scales is not None:
        return f"{key}/{_ASCII_REF[kind]}"
    return f"{key}[{_ASCII_UNIT.get(unit_si, unit_si)}]"


def write_profile_csv(path: str | Path, prof: LineProfile, scales: Scales | None, nondim: bool) -> Path:
    """線上の分布を CSV に書く(列: s は点 A からの距離、x, y, z は座標。どれも L_ref 単位。値は無次元か SI。
    領域の外の点は空欄)。"""
    path = Path(path)
    keys = list(prof.values)
    heads = ["s", "x", "y", "z"]
    cols = [prof.s, prof.points[:, 0], prof.points[:, 1], prof.points[:, 2]]
    for k in keys:
        f, _label = profile_scale(k, scales, nondim)
        heads.append(csv_column_name(k, scales, nondim))
        cols.append(prof.values[k] * f)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(heads) + "\n")
        for i in range(prof.s.size):
            vals = []
            for c in cols:
                v = c[i]
                vals.append("" if not np.isfinite(v) else f"{v:.8g}")
            fh.write(",".join(vals) + "\n")
    return path


# ---------------------------------------------------------------------------
# 3D 表示の場面(別スレッドで作る)
# ---------------------------------------------------------------------------

DISPLAY_MODES = {
    "3d": "3D(断面と奥の壁)",
    "slice": "断面(断面に垂直に見る)",
    "contour": "断面と等値線",
    "surface": "境界面(全体)",
    "none": "なし(流線だけ)",
}
RANGE_MODES = {"all": "領域全体", "view": "表示しているもの", "manual": "指定"}


@dataclass(frozen=True)
class VisRequest:
    """3D 表示の要求(画面の設定をまとめたもの)。"""

    field: str
    comp: str = "value"
    nondim: bool = True
    mode: str = "3d"
    n_contours: int = 12
    range_mode: str = "all"
    clim: tuple[float, float] | None = None
    plane: PlaneSpec = PlaneSpec()
    stream: StreamSpec | None = None
    outline: bool = False
    line: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None


@dataclass
class VisResult:
    """3D 表示に描くもの(SI の値は ``__show__``・``__speed__`` に倍率を掛けて入れてある)。"""

    bundle_id: int
    request: VisRequest
    label: str = ""
    clim: tuple[float, float] = (0.0, 1.0)
    stats: tuple[float, float] = (math.nan, math.nan)
    slice: Any = None
    half: Any = None
    surface: Any = None
    contours: Any = None
    outline: Any = None
    streams: Any = None
    stream_label: str = ""
    stream_clim: tuple[float, float] | None = None
    stream_info: str = ""
    line: Any = None
    elapsed: float = 0.0
    notes: list[str] = field(default_factory=list)


def _nice_clim(lo: float, hi: float) -> tuple[float, float]:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return (0.0, 1.0)
    if lo == hi:
        return (lo - 0.5, hi + 0.5) if lo == 0 else (lo - 0.001 * abs(lo), hi + 0.001 * abs(hi))
    return (lo, hi)


def build_vis(bundle: ResultBundle, req: VisRequest) -> VisResult:
    """3D 表示に描くものを作る(別スレッドで呼ぶ。元の格子は変えない)。"""
    import pyvista as pv

    t0 = time.perf_counter()
    res = VisResult(bundle_id=id(bundle), request=req)
    g = bundle.grid
    if g is None:
        raise ValueError("VTU がありません")
    L = bundle.length
    scales = bundle.run.scales
    spec = next((s for s in list_fields(g) if s.name == req.field), None)
    if spec is None:
        raise ValueError(f"場 {req.field} がありません")
    factor, res.label = scale_for(spec.name, scales, req.nondim, spec, req.comp)
    allv = component_values(g, spec.name, req.comp) * factor
    res.stats = (float(np.nanmin(allv)), float(np.nanmax(allv)))
    shown: list[np.ndarray] = []
    nrm = req.plane.unit_normal()
    org = np.asarray(req.plane.origin, dtype=float) * L

    def with_show(ds: Any) -> Any:
        v = component_values(ds, spec.name, req.comp) * factor
        ds.cell_data[SHOW] = v
        shown.append(v)
        return ds

    if req.mode in ("3d", "slice", "contour"):
        sl = g.slice(normal=tuple(nrm), origin=tuple(org))
        if sl.n_cells:
            res.slice = with_show(sl)
        else:
            res.notes.append("断面が領域にかかっていません")
        if req.mode == "3d" and bundle.surface is not None:
            # 既定の斜めの視点から見て断面の奥の側の壁を残す(手前の壁で断面が隠れないように)
            away = nrm if float(np.dot(nrm, OBLIQUE_VIEW)) >= 0.0 else -nrm
            half = bundle.surface.clip(normal=tuple(away), origin=tuple(org), invert=True)
            if half.n_cells:
                res.half = with_show(half)
    elif req.mode == "surface" and bundle.surface is not None:
        res.surface = with_show(bundle.surface.copy(deep=False))
    if req.outline and bundle.surface is not None:
        res.outline = bundle.surface.copy(deep=False)

    # 色の範囲
    if req.range_mode == "manual" and req.clim is not None:
        clim = req.clim
    elif req.range_mode == "view" and shown:
        v = np.concatenate([x[np.isfinite(x)] for x in shown]) if shown else allv
        clim = (float(v.min()), float(v.max())) if v.size else res.stats
    else:
        clim = res.stats
    res.clim = _nice_clim(*clim)

    if req.mode == "contour" and res.slice is not None:
        pts = res.slice.cell_data_to_point_data()
        levels = np.linspace(res.clim[0], res.clim[1], max(int(req.n_contours), 1) + 2)[1:-1]
        try:
            cont = pts.contour(isosurfaces=levels, scalars=SHOW)
            if cont.n_points:
                res.contours = cont
        except Exception as exc:  # 等値線が作れなくても断面は描く
            res.notes.append(f"等値線を作れません: {exc}")

    # 流線(同じ設定なら覚えておいた結果を使う)
    if req.stream is not None:
        st = req.stream
        vf = math.sqrt(2.0) * scales.velocity if (req.nondim and scales is not None) else 1.0
        key = (st, req.plane if st.kind == "plane2d" else None)
        cached = bundle.stream_cache.get(key)
        ts = time.perf_counter()
        if cached is None:
            pg = point_grid(bundle)
            if st.kind == "plane2d":
                lines, _ = compute_plane_streamlines(pg, req.plane, L, st.n, st.direction, st.max_length * L)
            else:
                lines = compute_streamlines(pg, seed_points(st) * L, st.direction, st.max_length * L)
            bundle.stream_cache[key] = lines
            cached = lines
            took = time.perf_counter() - ts
        else:
            took = 0.0
        lines = cached.copy(deep=False)
        if lines.n_points and lines.n_cells:
            sp = np.asarray(lines.point_data[SPEED]) / vf
            lines.point_data["__speed_show__"] = sp
            res.streams = lines
            res.stream_clim = _nice_clim(float(np.nanmin(sp)), float(np.nanmax(sp)))
            sym = "|u_t|" if st.kind == "plane2d" else "|u|"  # u_t は断面に投影した速度(面内の速さ)
            res.stream_label = f"{sym} / √(2RT0)" if vf != 1.0 else f"{sym} [m/s]"
            res.stream_info = (f"流線 {lines.n_cells:,} 本、{lines.n_points:,} 点"
                               + (f"(計算 {took:.1f} 秒)" if took else "(前の計算を使った)"))
        else:
            res.stream_info = "流線ができませんでした(種が領域の外にないか確かめてください)"

    if req.line is not None:
        a, b = req.line
        res.line = pv.Line(np.asarray(a) * L, np.asarray(b) * L)
    res.elapsed = time.perf_counter() - t0
    return res
