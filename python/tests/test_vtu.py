"""VTU 出力: VTK で読み戻してセルの種類と体積が一致すること。"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import perturbed_box
from gsis_tools.builder import ELEMENT_FACES, BoundaryGroup, polymesh_from_elements
from gsis_tools.geometry.box import box_mesh
from gsis_tools.vtu import (
    VTK_HEXAHEDRON,
    VTK_POLYHEDRON,
    VTK_PYRAMID,
    VTK_QUAD,
    VTK_TETRA,
    VTK_WEDGE,
    write_boundary_vtu,
    write_vtu,
)


def _hybrid_mesh():
    """六面体 1 + その上のピラミッド 1 + 横のプリズム 1 + 四面体 2 の小さな混在メッシュ。"""
    pts = np.array(
        [
            [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
            [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
            [0.5, 0.5, 1.6],  # 8: ピラミッドの頂点
            [2, 0, 0], [2, 0, 1],  # 9, 10: プリズム(x = 1 の面の横)
            [0.5, -1.0, 0.5],  # 11: 四面体
        ],
        dtype=float,
    )
    hexa = np.array([[0, 1, 2, 3, 4, 5, 6, 7]])
    pyr = np.array([[4, 5, 6, 7, 8]])
    prism = np.array([[1, 9, 2, 5, 10, 6]])
    tets = np.array([[0, 1, 4, 11], [1, 5, 4, 11]])
    elements = [("hex", hexa), ("pyramid", pyr), ("prism", prism), ("tet", tets)]
    # 境界面: 全要素の面のうち 1 回しか現れないもの
    faces = []
    for kind, conn in elements:
        for c in conn:
            for f in ELEMENT_FACES[kind]:
                faces.append(tuple(c[list(f)]))
    keys = [tuple(sorted(f)) for f in faces]
    bnd = [f for f, k in zip(faces, keys) if keys.count(k) == 1]
    tri = np.array([f for f in bnd if len(f) == 3])
    quad = np.array([f for f in bnd if len(f) == 4])
    return polymesh_from_elements(pts, elements, [BoundaryGroup("wall", "wall", [tri, quad])], renumber=None)


def _read(path):
    vtk = pytest.importorskip("vtk")
    from vtk.util.numpy_support import vtk_to_numpy

    r = vtk.vtkXMLUnstructuredGridReader()
    r.SetFileName(str(path))
    r.Update()
    ug = r.GetOutput()
    f = vtk.vtkCellSizeFilter()
    f.SetInputData(ug)
    f.ComputeVolumeOn()
    f.ComputeAreaOn()
    f.Update()
    out = f.GetOutput()
    types = np.array([ug.GetCellType(i) for i in range(ug.GetNumberOfCells())])
    return ug, out, types, vtk_to_numpy


def test_vtu_native_cell_types(tmp_path):
    m = _hybrid_mesh()
    assert m.n_cells == 5
    path = write_vtu(tmp_path / "hybrid.vtu", m, {"value": np.arange(5.0), "vec": np.ones((5, 3))})
    ug, out, types, to_np = _read(path)
    assert ug.GetNumberOfPoints() == m.n_points
    assert sorted(types.tolist()) == sorted([VTK_HEXAHEDRON, VTK_PYRAMID, VTK_WEDGE, VTK_TETRA, VTK_TETRA])
    vol = to_np(out.GetCellData().GetArray("Volume"))
    np.testing.assert_allclose(vol, m.geometry().cell_volumes, rtol=1e-12)
    np.testing.assert_allclose(to_np(ug.GetCellData().GetArray("cell_volume")), m.geometry().cell_volumes)
    assert to_np(ug.GetCellData().GetArray("vec")).shape == (5, 3)


def test_vtu_perturbed_hex_and_polyhedra(tmp_path):
    m = perturbed_box(4, amp=0.2)
    _, out, types, to_np = _read(write_vtu(tmp_path / "hex.vtu", m))
    assert set(types.tolist()) == {VTK_HEXAHEDRON}
    assert np.all(to_np(out.GetCellData().GetArray("Volume")) > 0)
    # 面が平面なら、多面体として書いても体積は一致する
    b = box_mesh(np.linspace(0, 1, 4), np.linspace(0, 2, 3), np.linspace(0, 1, 3))
    _, out, types, to_np = _read(write_vtu(tmp_path / "poly.vtu", b, all_polyhedra=True))
    assert set(types.tolist()) == {VTK_POLYHEDRON}
    np.testing.assert_allclose(to_np(out.GetCellData().GetArray("Volume")), b.geometry().cell_volumes, rtol=1e-12)


def test_boundary_vtu(tmp_path):
    b = box_mesh(np.linspace(0, 1, 4), np.linspace(0, 2, 3), np.linspace(0, 1, 3))
    path = write_boundary_vtu(tmp_path / "bnd.vtu", b)
    ug, out, types, to_np = _read(path)
    assert ug.GetNumberOfCells() == b.n_boundary_faces
    assert set(types.tolist()) == {VTK_QUAD}
    pid = to_np(ug.GetCellData().GetArray("patch_id"))
    assert np.array_equal(pid, b.boundary_patch_ids())
    area = to_np(out.GetCellData().GetArray("Area"))
    np.testing.assert_allclose(area, b.geometry().face_areas[b.n_internal_faces :], rtol=1e-12)
    assert "patch_id 0=xmin" in path.read_bytes()[:400].decode("utf-8", "ignore")
