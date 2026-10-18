"""gmsh からの変換(四面体・六面体・プリズム・ピラミッド)と builder。"""

from __future__ import annotations

import numpy as np
import pytest

gmsh = pytest.importorskip("gmsh")

from gsis_tools.builder import ELEMENT_FACES, BoundaryGroup, element_volumes, polymesh_from_elements  # noqa: E402
from gsis_tools.gmsh_convert import (  # noqa: E402
    gmsh_session,
    polymesh_from_gmsh,
    read_msh,
    write_msh_from_elements,
)
from gsis_tools.pmesh import PMeshError, check_mesh  # noqa: E402
from gsis_tools.quality import compute_quality  # noqa: E402


def _box_groups(names=("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"), skip=()):
    """箱の 6 面に、位置から決めた名前の Physical Group を付ける。"""
    for _, tag in gmsh.model.getEntities(2):
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(2, tag)
        c = np.array([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
        d = np.array([x1 - x0, y1 - y0, z1 - z0])
        axis = int(np.argmin(d))
        side = 0 if c[axis] < 0.5 * (1.0, 2.0, 3.0)[axis] else 1
        name = names[2 * axis + side]
        if name in skip:
            continue
        gmsh.model.addPhysicalGroup(2, [tag], name=name)


def _check_box(m, ncells=None):
    r = check_mesh(m)
    assert r.ok, r.errors
    assert r.warnings == []
    assert m.geometry().cell_volumes.sum() == pytest.approx(6.0, rel=1e-12)
    areas = {p.name: m.geometry().face_areas[p.faces].sum() for p in m.patches}
    assert areas["xmin"] == pytest.approx(6.0) and areas["ymax"] == pytest.approx(3.0)
    assert areas["zmin"] == pytest.approx(2.0)
    if ncells is not None:
        assert m.n_cells == ncells


def test_reference_faces_point_outward():
    """面の定義が gmsh の参照要素で外向き・閉じていること。"""
    with gmsh_session("ref", verbosity=0):
        for t, kind in ((4, "tet"), (5, "hex"), (6, "prism"), (7, "pyramid")):
            loc = np.asarray(gmsh.model.mesh.getElementProperties(t)[4]).reshape(-1, 3)
            conn = np.arange(loc.shape[0])[None, :]
            assert element_volumes(loc, kind, conn)[0] > 0
            c = loc.mean(axis=0)
            total = np.zeros(3)
            for f in ELEMENT_FACES[kind]:
                P = loc[list(f)]
                S = 0.5 * sum(np.cross(P[k] - P.mean(0), P[(k + 1) % len(f)] - P.mean(0)) for k in range(len(f)))
                assert S @ (P.mean(0) - c) > 0
                total += S
            np.testing.assert_allclose(total, 0, atol=1e-14)


def test_tet_box():
    with gmsh_session("tet", verbosity=0):
        gmsh.model.occ.addBox(0, 0, 0, 1, 2, 3)
        gmsh.model.occ.synchronize()
        _box_groups()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.4)
        gmsh.model.mesh.generate(3)
        n = len(gmsh.model.mesh.getElementsByType(4)[0])
        m = polymesh_from_gmsh(patch_order=["xmin", "xmax"])
    _check_box(m, n)
    assert [p.name for p in m.patches][:2] == ["xmin", "xmax"]
    assert compute_quality(m).cell_type_counts() == {"tetrahedron": n}


def test_hex_box_transfinite():
    with gmsh_session("hex", verbosity=0):
        gmsh.model.occ.addBox(0, 0, 0, 1, 2, 3)
        gmsh.model.occ.synchronize()
        _box_groups()
        gmsh.model.mesh.setTransfiniteAutomatic()
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5)
        gmsh.model.mesh.generate(3)
        n = len(gmsh.model.mesh.getElementsByType(5)[0])
        m = polymesh_from_gmsh()
    assert n > 0
    _check_box(m, n)
    q = compute_quality(m)
    assert q.cell_type_counts() == {"hexahedron": n}
    assert q.non_orthogonality.max() < 1e-6


def test_prism_extrusion():
    with gmsh_session("prism", verbosity=0):
        s = gmsh.model.occ.addRectangle(0, 0, 0, 1, 2)
        gmsh.model.occ.extrude([(2, s)], 0, 0, 3, numElements=[4], recombine=True)
        gmsh.model.occ.synchronize()
        _box_groups()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.4)
        gmsh.model.mesh.generate(3)
        n = len(gmsh.model.mesh.getElementsByType(6)[0])
        m = polymesh_from_gmsh()
    _check_box(m, n)
    assert compute_quality(m).cell_type_counts() == {"prism": n}


def test_pyramids_via_msh(tmp_path):
    """立方体を中心で 6 個のピラミッドに分けたものを .msh に書いて読み戻す。"""
    pts = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1], [0.5, 0.5, 0.5]],
        dtype=float,
    )
    hexfaces = ELEMENT_FACES["hex"]  # 外向き
    pyr = np.array([[f[0], f[3], f[2], f[1], 8] for f in hexfaces])  # 底面の法線が頂点を向く
    groups = [BoundaryGroup("wall", "wall", [np.array(hexfaces)])]
    path = write_msh_from_elements(tmp_path / "pyr.msh", pts, [("pyramid", pyr)], groups)
    m = read_msh(path)
    assert check_mesh(m).ok
    assert m.n_cells == 6
    assert m.geometry().cell_volumes.sum() == pytest.approx(1.0, rel=1e-14)
    np.testing.assert_allclose(m.geometry().cell_volumes, 1 / 6, rtol=1e-14)
    assert compute_quality(m).cell_type_counts() == {"pyramid": 6}
    assert m.patches[0].name == "wall" and m.patches[0].count == 6


def test_mixed_hex_prism_and_msh_roundtrip(tmp_path):
    """三角形と四角形が混ざった面を押し出し(六面体 + プリズム)、モデルからと .msh からの変換が一致する。"""
    with gmsh_session("mixed", verbosity=0):
        r1 = gmsh.model.occ.addRectangle(0, 0, 0, 0.5, 2)
        r2 = gmsh.model.occ.addRectangle(0.5, 0, 0, 0.5, 2)
        out, _ = gmsh.model.occ.fragment([(2, r1)], [(2, r2)])
        gmsh.model.occ.synchronize()
        surfs = [t for d, t in out if d == 2]
        left = min(surfs, key=lambda t: gmsh.model.getBoundingBox(2, t)[0])
        gmsh.model.occ.extrude([(2, t) for t in surfs], 0, 0, 3, numElements=[3], recombine=True)
        gmsh.model.occ.synchronize()
        # 左の面は構造四角形(→ 六面体)、右の面は三角形(→ プリズム)
        gmsh.model.mesh.setRecombine(2, left)
        gmsh.model.mesh.setTransfiniteSurface(left)
        for _, c in gmsh.model.getBoundary([(2, left)], oriented=False):
            gmsh.model.mesh.setTransfiniteCurve(c, 4)
        # 外側の曲面だけに Physical Group(2 つの体積の境の面は内部面)
        vols = [t for _, t in gmsh.model.getEntities(3)]
        outer = [t for _, t in gmsh.model.getBoundary([(3, v) for v in vols], combined=True, oriented=False)]
        gmsh.model.addPhysicalGroup(2, outer, name="wall")
        gmsh.model.addPhysicalGroup(3, vols, name="fluid")
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.4)
        gmsh.model.mesh.generate(3)
        m1 = polymesh_from_gmsh()
        gmsh.write(str(tmp_path / "mixed.msh"))
    m2 = read_msh(tmp_path / "mixed.msh")
    for m in (m1, m2):
        r = check_mesh(m)
        assert r.ok, r.errors
        assert m.geometry().cell_volumes.sum() == pytest.approx(1.0 * 2.0 * 3.0, rel=1e-12)
        types = compute_quality(m).cell_type_counts()
        assert types.get("hexahedron", 0) > 0 and types.get("prism", 0) > 0
    assert m1.n_cells == m2.n_cells
    assert m1.patches[0].count == m2.patches[0].count


def test_unassigned_boundary_face_is_error():
    with gmsh_session("missing", verbosity=0):
        gmsh.model.occ.addBox(0, 0, 0, 1, 2, 3)
        gmsh.model.occ.synchronize()
        _box_groups(skip=("zmax",))
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5)
        gmsh.model.mesh.generate(3)
        with pytest.raises(PMeshError, match="どの境界グループにも属さない"):
            polymesh_from_gmsh()


def test_face_in_two_groups_is_error():
    with gmsh_session("dup", verbosity=0):
        gmsh.model.occ.addBox(0, 0, 0, 1, 2, 3)
        gmsh.model.occ.synchronize()
        _box_groups()
        gmsh.model.addPhysicalGroup(2, [gmsh.model.getEntities(2)[0][1]], name="extra")
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5)
        gmsh.model.mesh.generate(3)
        with pytest.raises(PMeshError, match="複数の境界グループ"):
            polymesh_from_gmsh()


def test_high_order_is_error():
    with gmsh_session("ho", verbosity=0):
        gmsh.model.occ.addBox(0, 0, 0, 1, 2, 3)
        gmsh.model.occ.synchronize()
        _box_groups()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 1.0)
        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.setOrder(2)
        with pytest.raises(PMeshError, match="未対応の 3 次元要素"):
            polymesh_from_gmsh()


def test_builder_fixes_inverted_elements_and_renumbers():
    """負の向きの要素を直し、RCM で番号を付け直しても体積と規約が保たれる。"""
    from gsis_tools.geometry.box import box_hex_elements

    pts, conn, sides = box_hex_elements(np.linspace(0, 1, 5), np.linspace(0, 2, 4), np.linspace(0, 3, 3))
    conn = conn.copy()
    conn[::3] = conn[::3][:, [4, 5, 6, 7, 0, 1, 2, 3]]  # 上下を入れ替えて向きを反転
    groups = [BoundaryGroup("all", None, list(sides.values()))]
    m0, map0 = polymesh_from_elements(pts, [("hex", conn)], groups, renumber=None, return_cell_map=True)
    m1, map1 = polymesh_from_elements(pts, [("hex", conn)], groups, renumber="rcm", return_cell_map=True)
    for m in (m0, m1):
        r = check_mesh(m)
        assert r.ok, r.errors
        np.testing.assert_allclose(m.geometry().cell_volumes, 0.25 * (2 / 3) * 1.5, rtol=1e-13)
    assert np.array_equal(map0, np.arange(m0.n_cells))
    # 番号を付け直したメッシュのセル中心は、元の要素の中心と対応する
    np.testing.assert_allclose(m1.geometry().cell_centres, m0.geometry().cell_centres[map1], atol=1e-14)
    assert m1.patches[0].kind == "patch"
