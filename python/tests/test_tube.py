"""管ケースのメッシュ: パッチ面積・体積・検査・セル数の合わせ込み。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from gsis_tools.geometry.tube import (
    TubeDims,
    TubeHexOptions,
    TubeTetOptions,
    _hex_layout,
    generate_tube_hex,
    generate_tube_tet,
)
from gsis_tools.pmesh import check_mesh, read_pmesh, write_pmesh
from gsis_tools.quality import compute_quality

R = 1e-3


def _polygon_rel_error(n_sides: int) -> float:
    """円を正 n 角形(頂点が円上)で近似したときの面積の相対誤差。"""
    return 1.0 - n_sides / (2 * math.pi) * math.sin(2 * math.pi / n_sides)


def _patch_areas(m):
    g = m.geometry()
    return {p.name: float(g.face_areas[p.faces].sum()) for p in m.patches}


@pytest.fixture(scope="module")
def hex_coarse():
    dims = TubeDims(radius=R)
    opt = TubeHexOptions(n_theta=8, ds_wall=0.08, ds_mouth=0.08, h_tube=0.5, h_max=1.0)
    m, info = generate_tube_hex(dims, opt)
    return dims, opt, m, info


@pytest.fixture(scope="module")
def tet_coarse():
    dims = TubeDims(radius=R)
    m, info = generate_tube_tet(dims, TubeTetOptions(h_max=0.8, h_tube=0.2, threads=4), verbosity=0)
    return dims, m, info


def test_hex_patches_and_volume(hex_coarse):
    dims, opt, m, info = hex_coarse
    r = check_mesh(m)
    assert r.ok, r.errors
    assert r.warnings == []
    assert [p.name for p in m.patches] == ["inlet", "outlet", "wall"]
    assert [p.kind for p in m.patches] == ["inlet", "outlet", "wall"]
    exact = dims.patch_areas()
    areas = _patch_areas(m)
    eps = _polygon_rel_error(4 * opt.n_theta)
    # 端面は外周が円に内接する 4n 角形なので、面積は正 4n 角形の面積と一致する
    assert areas["inlet"] == pytest.approx(math.pi * (8 * R) ** 2 * (1 - eps), rel=1e-12)
    assert areas["outlet"] == pytest.approx(math.pi * (4 * R) ** 2 * (1 - eps), rel=1e-12)
    assert areas["inlet"] == pytest.approx(exact["inlet"], rel=1.01 * eps)
    assert areas["outlet"] == pytest.approx(exact["outlet"], rel=1.01 * eps)
    assert areas["wall"] == pytest.approx(exact["wall"], rel=1.01 * eps)
    V = m.geometry().cell_volumes.sum()
    assert V == pytest.approx(dims.volume() * (1 - eps), rel=1e-12)
    assert m.n_cells == _hex_layout(dims, opt).n_cells()


def test_hex_inlet_outlet_planes(hex_coarse):
    dims, _, m, _ = hex_coarse
    g = m.geometry()
    for name, x in (("inlet", 0.0), ("outlet", dims.length * R)):
        p = m.patch(name)
        np.testing.assert_allclose(g.face_centres[p.faces, 0], x, atol=1e-15)
        n = g.face_normals[p.faces]
        np.testing.assert_allclose(n[:, 0], -1.0 if name == "inlet" else 1.0, atol=1e-12)


def test_hex_quality_reasonable(hex_coarse):
    _, _, m, _ = hex_coarse
    q = compute_quality(m)
    assert q.cell_type_counts() == {"hexahedron": m.n_cells}
    assert q.non_orthogonality.max() < 45.0
    assert q.skewness.max() < 0.6


def test_hex_target_cells():
    dims = TubeDims(radius=R)
    m, info = generate_tube_hex(dims, TubeHexOptions(n_theta=8), target_cells=30000)
    assert abs(m.n_cells / 30000 - 1) < 0.05
    assert info["options"]["n_theta"] == 8
    assert check_mesh(m).ok


def _disc_polygon(m, name, radius):
    """端面の外周の節点(円上)を角度順に結んだ多角形の面積と頂点数。"""
    p = m.patch(name)
    nodes = np.unique(m.face_nodes[m.face_offsets[p.start] : m.face_offsets[p.stop]])
    P = m.points[nodes]
    ring = P[np.hypot(P[:, 1], P[:, 2]) > radius * (1 - 1e-9)]
    ang = np.sort(np.arctan2(ring[:, 2], ring[:, 1]))
    dth = np.diff(np.concatenate([ang, [ang[0] + 2 * math.pi]]))
    return 0.5 * radius**2 * float(np.sum(np.sin(dth))), ang.size


def test_tet_patches_and_volume(tet_coarse):
    dims, m, info = tet_coarse
    r = check_mesh(m)
    assert r.ok, r.errors
    assert [p.name for p in m.patches] == ["inlet", "outlet", "wall"]
    exact = dims.patch_areas()
    areas = _patch_areas(m)
    # 端面の三角形分割の面積は、外周の節点を結んだ多角形の面積と一致し、円との差は多角形近似の誤差
    for name, rad in (("inlet", dims.r_up * R), ("outlet", dims.r_down * R)):
        poly, n = _disc_polygon(m, name, rad)
        assert areas[name] == pytest.approx(poly, rel=1e-12), name
        assert n >= 12
        assert exact[name] * (1 - 1.5 * _polygon_rel_error(n)) < areas[name] < exact[name]
    # 側面と体積は円柱面の多角形近似の分だけ小さい(粗いメッシュなので 3% 以内)
    assert exact["wall"] * 0.97 < areas["wall"] < exact["wall"]
    assert dims.volume() * 0.97 < m.geometry().cell_volumes.sum() < dims.volume()
    q = compute_quality(m)
    assert q.cell_type_counts() == {"tetrahedron": m.n_cells}
    reg = dims.regions(m.geometry().cell_centres)
    hv = q.cell_size / R
    # 管の中は細かく、槽の遠方は粗い
    assert hv[reg["管"]].mean() < 0.5 * hv[reg["上流槽(管の入口から 3R 以上)"]].mean()


def test_tet_roundtrip_file(tmp_path, tet_coarse):
    _, m, _ = tet_coarse
    path = write_pmesh(tmp_path / "tube_tet.pmesh", m)
    r = read_pmesh(path)
    assert r.n_cells == m.n_cells
    assert r.metadata["kind"] == "tet"


@pytest.mark.slow
def test_tet_target_cells():
    dims = TubeDims(radius=R)
    m, info = generate_tube_tet(dims, TubeTetOptions(threads=4), target_cells=20000, verbosity=0)
    assert abs(m.n_cells / 20000 - 1) <= 0.05 or len(info["passes"]) == 3
    assert check_mesh(m).ok
