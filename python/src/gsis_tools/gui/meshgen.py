"""管メッシュの生成(``gsis_tools.geometry.tube``)と、品質の集計・3D 表示用のデータ。

生成は ``gsis-tools mesh tube`` と同じ関数を呼ぶ。:func:`cli_command` で同じ設定のコマンドラインも示す
(画面で作ったメッシュを CLI で作り直せるように)。
"""

from __future__ import annotations

import math
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..geometry.tube import TubeDims, TubeHexOptions, TubeTetOptions, generate_tube_hex, generate_tube_tet
from ..pmesh import PolyMesh, check_mesh, read_pmesh
from ..quality import MeshQuality, compute_quality, stats


@dataclass
class TubeMeshSpec:
    """画面の設定(長さは管半径 R 単位、``radius`` だけ m)。"""

    kind: str = "hex"
    target_cells: int | None = 135_000
    radius: float = 1e-3
    # 六面体
    n_theta: int = 16
    square: float = 0.4
    ds_wall: float = 0.03
    ds_mouth: float = 0.03
    h_tube: float = 0.25
    h_max: float = 0.5
    growth: float = 1.2
    refine: float | None = None
    # 四面体
    tet_h_max: float = 0.5
    tet_h_tube: float = 0.08
    gradient: float = 0.15
    algorithm: str = "delaunay"
    threads: int = 8

    def hex_options(self) -> TubeHexOptions:
        opt = TubeHexOptions(n_theta=int(self.n_theta), square=self.square, ds_wall=self.ds_wall,
                             ds_mouth=self.ds_mouth, h_tube=self.h_tube, h_max=self.h_max, growth=self.growth)
        return opt.refined(self.refine) if self.refine else opt

    def tet_options(self) -> TubeTetOptions:
        return TubeTetOptions(h_max=self.tet_h_max, h_tube=self.tet_h_tube, gradient=self.gradient,
                              algorithm=self.algorithm, threads=int(self.threads))


def cli_command(spec: TubeMeshSpec, out: str = "<出力>.pmesh") -> str:
    """同じメッシュを作る ``gsis-tools`` のコマンドライン。"""
    a = ["uv run --project python gsis-tools mesh tube", f"--kind {spec.kind}"]
    if spec.target_cells:
        a.append(f"--target-cells {int(spec.target_cells)}")
    if spec.radius != 1e-3:
        a.append(f"--radius {spec.radius:g}")
    d_hex, d_tet = TubeHexOptions(), TubeTetOptions()
    if spec.kind == "hex":
        for flag, v, dv in (("--n-theta", spec.n_theta, d_hex.n_theta), ("--square", spec.square, d_hex.square),
                            ("--ds-wall", spec.ds_wall, d_hex.ds_wall), ("--ds-mouth", spec.ds_mouth, d_hex.ds_mouth),
                            ("--h-tube", spec.h_tube, d_hex.h_tube), ("--max-size", spec.h_max, d_hex.h_max),
                            ("--growth", spec.growth, d_hex.growth)):
            if v != dv:
                a.append(f"{flag} {v:g}")
        if spec.refine:
            a.append(f"--refine {spec.refine:g}")
    else:
        for flag, v, dv in (("--max-size", spec.tet_h_max, d_tet.h_max), ("--h-tube", spec.tet_h_tube, d_tet.h_tube),
                            ("--gradient", spec.gradient, d_tet.gradient)):
            if v != dv:
                a.append(f"{flag} {v:g}")
        if spec.algorithm != d_tet.algorithm:
            a.append(f"--algorithm {spec.algorithm}")
        if spec.threads != 8:
            a.append(f"--threads {spec.threads}")
    a.append(f"--out {out}")
    return " ".join(a)


def ensure_gmsh_main_thread() -> None:
    """gmsh を(メインスレッドで)初期化しておく。

    gmsh の ``initialize`` は既定で SIGINT の処理を入れるのでメインスレッドでしか呼べない。先に初期化しておけば、
    ``gmsh_session`` は初期化済みの gmsh を使い、別スレッドから四面体メッシュを作れる。
    """
    import gmsh

    if not gmsh.isInitialized():
        gmsh.initialize(["-nopopup"], readConfigFiles=False, interruptible=False)
        gmsh.option.setNumber("General.Terminal", 0)


def generate(spec: TubeMeshSpec) -> tuple[PolyMesh, dict]:
    """メッシュを作る(別スレッドから呼んでよい。四面体は先に :func:`ensure_gmsh_main_thread`)。"""
    dims = TubeDims(radius=spec.radius)
    t0 = time.perf_counter()
    if spec.kind == "hex":
        mesh, info = generate_tube_hex(dims, spec.hex_options(), target_cells=spec.target_cells)
    elif spec.kind == "tet":
        mesh, info = generate_tube_tet(dims, spec.tet_options(), target_cells=spec.target_cells, verbosity=0)
    else:
        raise ValueError(f"kind は hex か tet です: {spec.kind}")
    info["elapsed"] = time.perf_counter() - t0
    info["spec"] = asdict(spec)
    return mesh, info


@dataclass
class MeshReport:
    """画面に出す品質の要約(長さは R 単位)。"""

    n_cells: int
    n_faces: int
    n_internal_faces: int
    n_points: int
    cell_types: dict[str, int]
    h_v: dict[str, float]
    max_edge: dict[str, float]
    edge_ratio: dict[str, float]
    non_orthogonality: dict[str, float]
    skewness: dict[str, float]
    volume_ratio: dict[str, float]
    volume: float
    exact_volume: float | None
    patches: list[dict] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    check_ok: bool = True
    check_messages: list[str] = field(default_factory=list)
    radius: float = 1e-3

    def rows(self) -> list[tuple[str, str]]:
        """「項目, 値」の表。"""
        def f(v: float, d: int = 4) -> str:
            return "—" if not math.isfinite(v) else f"{v:.{d}g}"

        r = [
            ("セル数", f"{self.n_cells:,}"),
            ("面(内部 / 全体)・節点", f"{self.n_internal_faces:,} / {self.n_faces:,}・{self.n_points:,}"),
            ("要素の内訳", "、".join(f"{k} {v:,}" for k, v in self.cell_types.items())),
            ("セル寸法 h_V = V^(1/3) [R]", f"最小 {f(self.h_v['min'])}、平均 {f(self.h_v['mean'])}、最大 {f(self.h_v['max'])}"),
            ("最大辺 [R]", f"平均 {f(self.max_edge['mean'])}、最大 {f(self.max_edge['max'])}"),
            ("最大辺 / 最小辺", f"平均 {f(self.edge_ratio['mean'])}、最大 {f(self.edge_ratio['max'])}"),
            ("非直交角 [deg]", f"平均 {f(self.non_orthogonality['mean'], 3)}、99% {f(self.non_orthogonality['p99'], 3)}、"
                              f"最大 {f(self.non_orthogonality['max'], 3)}"),
            ("歪み(内部面)", f"平均 {f(self.skewness['mean'], 3)}、最大 {f(self.skewness['max'], 3)}"),
            ("隣接セルの体積比", f"最大 {f(self.volume_ratio['max'], 3)}"),
        ]
        vol = f"{self.volume:.6g} R³"
        if self.exact_volume:
            vol += f"(円柱の値 {self.exact_volume:.6g}、相対差 {(self.volume - self.exact_volume) / self.exact_volume:+.2e})"
        r.append(("体積", vol))
        for reg in self.regions:
            r.append((f"領域: {reg['name']}", f"{reg['cells']:,} セル、h_V 平均 {f(reg['h_mean'])}、最大 {f(reg['h_max'])}"))
        r.append(("検査(pmesh §4)", "OK" if self.check_ok else "エラー: " + " / ".join(self.check_messages)))
        return r


def mesh_report(mesh: PolyMesh, radius: float | None = None, q: MeshQuality | None = None) -> MeshReport:
    """品質の要約。``radius``(管半径 [m])を省くとメッシュの metadata から読み、なければ 1e-3。"""
    if radius is None:
        try:
            radius = float(mesh.metadata.get("radius", "1e-3"))
        except ValueError:
            radius = 1e-3
    R = float(radius)
    q = q or compute_quality(mesh)
    g = mesh.geometry()
    dims = TubeDims(radius=R)
    tube_like = all(n in mesh.patch_names() for n in ("inlet", "outlet", "wall"))
    exact_areas = dims.patch_areas() if tube_like else {}
    patches = []
    for p in mesh.patches:
        a = float(g.face_areas[p.faces].sum()) / R**2
        ex = exact_areas.get(p.name)
        patches.append({"name": p.name, "kind": p.kind, "faces": p.count, "area": a,
                        "exact_area": None if ex is None else ex / R**2})
    regions = []
    if tube_like:
        for name, mask in dims.regions(g.cell_centres).items():
            mask = np.asarray(mask, dtype=bool)
            if mask.any():
                hv = q.cell_size[mask] / R
                regions.append({"name": name, "cells": int(mask.sum()), "h_mean": float(hv.mean()),
                                "h_max": float(hv.max())})
    res = check_mesh(mesh)
    return MeshReport(
        n_cells=mesh.n_cells, n_faces=mesh.n_faces, n_internal_faces=mesh.n_internal_faces, n_points=mesh.n_points,
        cell_types=q.cell_type_counts(), h_v=stats(q.cell_size / R), max_edge=stats(q.cell_max_edge / R),
        edge_ratio=stats(q.edge_ratio), non_orthogonality=stats(q.non_orthogonality), skewness=stats(q.skewness),
        volume_ratio=stats(q.volume_ratio), volume=float(g.cell_volumes.sum()) / R**3,
        exact_volume=dims.volume() / R**3 if tube_like else None, patches=patches, regions=regions,
        check_ok=res.ok, check_messages=list(res.errors)[:5], radius=R,
    )


# ---------------------------------------------------------------------------
# 3D 表示のデータ(pyvista)
# ---------------------------------------------------------------------------


def boundary_polydata(mesh: PolyMesh) -> Any:
    """境界面の PolyData(セルデータ ``patch_id``。座標は m)。"""
    import pyvista as pv

    ni, nf = mesh.n_internal_faces, mesh.n_faces
    fo = mesh.face_offsets
    nodes = mesh.face_nodes[fo[ni] : fo[nf]].astype(np.int64)
    sizes = np.diff(fo[ni:]).astype(np.int64)
    used, inv = np.unique(nodes, return_inverse=True)
    faces = np.empty(sizes.size + nodes.size, dtype=np.int64)
    starts = np.cumsum(sizes + 1) - (sizes + 1)
    faces[starts] = sizes
    mask = np.ones(faces.size, dtype=bool)
    mask[starts] = False
    faces[mask] = inv
    pd = pv.PolyData(mesh.points[used], faces=faces)
    pd.cell_data["patch_id"] = mesh.boundary_patch_ids()
    return pd


def volume_grid(mesh: PolyMesh, cell_data: dict[str, np.ndarray] | None = None) -> Any:
    """セルの UnstructuredGrid(gsis_tools.vtu の書き出しを通して作る。座標は m)。"""
    import pyvista as pv

    from ..vtu import write_vtu

    with tempfile.TemporaryDirectory(prefix="gsis_gui_mesh_") as d:
        path = Path(d) / "cells.vtu"
        write_vtu(path, mesh, cell_data)
        grid = pv.read(path)
        grid = grid.copy(deep=True)
    return grid


def section_surface(grid: Any, boundary: Any | None = None, z0: float = 0.0) -> Any:
    """z ≤ z0 の側のセル(断面にかかるセルを含む)の表面。断面のセルの形が見える。

    ``boundary``(:func:`boundary_polydata`)を渡すと、表面の各面に境界パッチの番号 ``patch_id`` を付ける
    (面の中心が境界面の中心と一致する面。断面の面は -1)。同じ場所に 2 つの面を重ねて描かないで済む。
    """
    clipped = grid.clip(normal=(0.0, 0.0, 1.0), origin=(0.0, 0.0, z0), invert=True, crinkle=True)
    surf = clipped.extract_surface(algorithm="dataset_surface")
    if boundary is not None and surf.n_cells:
        from scipy.spatial import cKDTree

        bc = np.asarray(boundary.cell_centers().points)
        sc = np.asarray(surf.cell_centers().points)
        dist, idx = cKDTree(bc).query(sc, k=1)
        scale = float(np.ptp(bc, axis=0).max()) if bc.size else 1.0
        pid = np.asarray(boundary.cell_data["patch_id"])[idx].astype(np.int32)
        pid[dist > 1e-6 * scale] = -1
        surf.cell_data["patch_id"] = pid
    return surf


@dataclass
class MeshPreview:
    """メッシュの画面に出すもの(別スレッドで作る)。"""

    mesh: PolyMesh
    info: dict
    report: MeshReport
    boundary: Any
    section: Any | None
    path: Path | None = None


def prepare_preview(mesh: PolyMesh, info: dict | None = None, *, with_section: bool = True,
                    path: Path | None = None) -> MeshPreview:
    radius = None
    if info and "spec" in info:
        radius = info["spec"].get("radius")
    rep = mesh_report(mesh, radius)
    bnd = boundary_polydata(mesh)
    sec = None
    if with_section:
        sec = section_surface(volume_grid(mesh), bnd, 0.0)
    return MeshPreview(mesh, info or {}, rep, bnd, sec, path)


def generate_preview(spec: TubeMeshSpec, with_section: bool = True) -> MeshPreview:
    mesh, info = generate(spec)
    return prepare_preview(mesh, info, with_section=with_section)


def load_preview(path: str | Path, with_section: bool = True) -> MeshPreview:
    path = Path(path)
    mesh = read_pmesh(path)
    info = {"kind": mesh.metadata.get("kind", ""), "source": str(path)}
    pv_ = prepare_preview(mesh, info, with_section=with_section, path=path)
    return pv_


def spec_from_metadata(mesh: PolyMesh) -> TubeMeshSpec | None:
    """メッシュの metadata(``options``)から設定を読み戻す(読めなければ None)。"""
    import ast

    md = mesh.metadata
    try:
        opts = ast.literal_eval(md.get("options", "{}"))
        radius = float(md.get("radius", "1e-3"))
    except (ValueError, SyntaxError):
        return None
    kind = md.get("kind", "")
    if kind == "hex":
        s = TubeMeshSpec(kind="hex", target_cells=None, radius=radius)
        for k in ("n_theta", "square", "ds_wall", "ds_mouth", "h_tube", "h_max", "growth"):
            if k in opts:
                setattr(s, k, opts[k])
        return s
    if kind == "tet":
        s = TubeMeshSpec(kind="tet", target_cells=None, radius=radius)
        s.tet_h_max = opts.get("h_max", s.tet_h_max)
        s.tet_h_tube = opts.get("h_tube", s.tet_h_tube)
        s.gradient = opts.get("gradient", s.gradient)
        s.algorithm = opts.get("algorithm", s.algorithm)
        return s
    return None


__all__ = [
    "TubeMeshSpec", "cli_command", "generate", "mesh_report", "MeshReport", "boundary_polydata", "volume_grid",
    "section_surface", "MeshPreview", "prepare_preview", "generate_preview", "load_preview", "spec_from_metadata",
    "ensure_gmsh_main_thread", "replace",
]
