"""幾何量(pmesh §4)の検証: 箱の体積・重心、非平面の面、円柱の体積の収束。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import perturbed_box
from gsis_tools.geometry.box import box_mesh
from gsis_tools.geometry.tube import ogrid_cylinder
from gsis_tools.pmesh import check_mesh, face_geometry


def test_box_volumes_and_centroids():
    x = np.array([0.0, 0.1, 0.4, 1.0])
    y = np.array([0.0, 0.5, 2.0])
    z = np.array([-1.0, 0.0, 0.25, 3.0])
    m = box_mesh(x, y, z, renumber="rcm")
    g = m.geometry()
    # セル中心からどの区間のセルかを決め、体積と重心を解析値と比べる
    xc = g.cell_centres
    i = np.searchsorted(x, xc[:, 0]) - 1
    j = np.searchsorted(y, xc[:, 1]) - 1
    k = np.searchsorted(z, xc[:, 2]) - 1
    vol = np.diff(x)[i] * np.diff(y)[j] * np.diff(z)[k]
    np.testing.assert_allclose(g.cell_volumes, vol, rtol=1e-13)
    cen = np.stack([(x[i] + x[i + 1]) / 2, (y[j] + y[j + 1]) / 2, (z[k] + z[k + 1]) / 2], axis=1)
    np.testing.assert_allclose(xc, cen, rtol=0, atol=1e-14)
    assert g.cell_volumes.sum() == pytest.approx(1.0 * 2.0 * 4.0, rel=1e-14)
    # 面積: 側面
    areas = {p.name: g.face_areas[p.faces].sum() for p in m.patches}
    assert areas["xmin"] == pytest.approx(2.0 * 4.0, rel=1e-14)
    assert areas["zmax"] == pytest.approx(1.0 * 2.0, rel=1e-14)


def test_nonplanar_faces_closed_and_conservative():
    """内部の節点をずらして面を非平面にしても、セルは閉じていて体積の和は保たれる。

    体積は面の寄与が隣のセルと打ち消し合うので和は厳密に 1。重心(1 次モーメント)は
    非平面の面では角錐分割による近似なので、和は近い値になるだけ。
    """
    m = perturbed_box(6, amp=0.3)
    g = m.geometry()
    r = check_mesh(m)
    assert r.ok, r.errors
    assert g.cell_closure.max() < 1e-13
    assert g.cell_volumes.sum() == pytest.approx(1.0, rel=1e-13)
    first_moment = (g.cell_volumes[:, None] * g.cell_centres).sum(axis=0)
    np.testing.assert_allclose(first_moment, [0.5, 0.5, 0.5], rtol=1e-4)


def test_face_geometry_triangle_and_polygons():
    pts = np.array(
        [
            [0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1.0, 0.0],  # 三角形
            [0.0, 0.0, 1.0], [3.0, 0.0, 1.0], [3.0, 2.0, 1.0], [0.0, 2.0, 1.0],  # 長方形
            [0.0, 0.0, 2.0], [2.0, 0.0, 2.0], [2.0, 1.0, 2.0], [1.0, 3.0, 2.0], [0.0, 1.0, 2.0],  # 五角形
        ]
    )
    fo = np.array([0, 3, 7, 12])
    fn = np.arange(12)
    S, xf = face_geometry(pts, fo, fn)
    np.testing.assert_allclose(S[0], [0, 0, 1.0])
    np.testing.assert_allclose(xf[0], [2 / 3, 1 / 3, 0])
    np.testing.assert_allclose(S[1], [0, 0, 6.0])
    np.testing.assert_allclose(xf[1], [1.5, 1.0, 1.0])
    # 五角形 = 長方形 2×1 + 三角形 (0,1)-(2,1)-(1,3)
    area = 2.0 + 2.0
    cx = (2.0 * 1.0 + 2.0 * 1.0) / area
    cy = (2.0 * 0.5 + 2.0 * (1.0 + 2.0 / 3.0)) / area
    np.testing.assert_allclose(S[2], [0, 0, area])
    np.testing.assert_allclose(xf[2], [cx, cy, 2.0])


def test_nonplanar_quad_follows_definition():
    """非平面の四角形は節点平均を中心とした扇形分割(pmesh §4)で計算する。"""
    p = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.2], [1.0, 1.0, 0.0], [0.0, 1.0, 0.2]])
    S, xf = face_geometry(p, np.array([0, 4]), np.arange(4))
    xbar = p.mean(axis=0)
    nk = [np.cross(p[k] - xbar, p[(k + 1) % 4] - xbar) for k in range(4)]
    Sref = 0.5 * np.sum(nk, axis=0)
    nhat = Sref / np.linalg.norm(Sref)
    w = np.array([n @ nhat for n in nk])
    ck = np.array([(p[k] + p[(k + 1) % 4] + xbar) / 3 for k in range(4)])
    np.testing.assert_allclose(S[0], Sref, rtol=1e-14)
    np.testing.assert_allclose(xf[0], (w[:, None] * ck).sum(axis=0) / w.sum(), rtol=1e-14)


def test_cylinder_volume_converges_hex():
    """O 型格子の円柱: 体積は 4n 角柱の体積と一致し、n を倍にすると誤差が約 1/4 になる。"""
    R, L = 1.5, 2.0
    errs = []
    for n in (4, 8, 16, 32):
        m = ogrid_cylinder(R, L, n_theta=n, nx=3)
        assert check_mesh(m).ok
        V = m.geometry().cell_volumes.sum()
        poly = 2 * n * math.sin(math.pi / (2 * n)) * R**2 * L
        assert V == pytest.approx(poly, rel=1e-13)
        errs.append(abs(V - math.pi * R**2 * L) / (math.pi * R**2 * L))
    ratios = [a / b for a, b in zip(errs[:-1], errs[1:])]
    assert all(3.9 < r < 4.1 for r in ratios), ratios
    assert errs[-1] < 5e-4


def test_cylinder_volume_converges_tet():
    """gmsh の四面体の円柱: メッシュを細かくすると体積の誤差が減る。"""
    gmsh = pytest.importorskip("gmsh")
    from gsis_tools.gmsh_convert import gmsh_session, polymesh_from_gmsh

    errs = []
    for h in (0.5, 0.25, 0.125):
        with gmsh_session("cyl", verbosity=0):
            gmsh.model.occ.addCylinder(0, 0, 0, 2.0, 0, 0, 1.0)
            gmsh.model.occ.synchronize()
            gmsh.model.addPhysicalGroup(2, [t for _, t in gmsh.model.getEntities(2)], name="wall")
            gmsh.option.setNumber("Mesh.MeshSizeMax", h)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.model.mesh.generate(3)
            m = polymesh_from_gmsh()
        assert check_mesh(m).ok
        V = m.geometry().cell_volumes.sum()
        errs.append(abs(V - 2.0 * math.pi) / (2.0 * math.pi))
    assert errs[0] > errs[1] > errs[2]
    assert errs[2] < 2e-3
