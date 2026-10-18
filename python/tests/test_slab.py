"""平板メッシュ。"""

from __future__ import annotations

import numpy as np
import pytest

from gsis_tools.geometry.slab import generate_slab, slab_nodes
from gsis_tools.pmesh import check_mesh


def test_uniform_slab():
    L, nx = 2e-3, 40
    m = generate_slab(L, nx)
    r = check_mesh(m)
    assert r.ok, r.errors
    assert m.n_cells == nx
    assert [(p.name, p.kind, p.count) for p in m.patches] == [
        ("wall_left", "wall", 1),
        ("wall_right", "wall", 1),
        ("sym_y", "symmetry", 2 * nx),
        ("sym_z", "symmetry", 2 * nx),
    ]
    g = m.geometry()
    T = L / nx
    np.testing.assert_allclose(g.cell_volumes, T**3, rtol=1e-12)
    # セル番号は x の小さい順
    assert np.all(np.diff(g.cell_centres[:, 0]) > 0)
    wl, wr = m.patch("wall_left"), m.patch("wall_right")
    assert g.face_centres[wl.start, 0] == 0.0 and g.face_normals[wl.start, 0] == pytest.approx(-1.0)
    assert g.face_centres[wr.start, 0] == pytest.approx(L) and g.face_normals[wr.start, 0] == pytest.approx(1.0)
    assert m.owner[wl.start] == 0 and m.owner[wr.start] == nx - 1
    sy = m.patch("sym_y")
    assert np.all(np.abs(np.abs(g.face_normals[sy.faces, 1]) - 1.0) < 1e-14)
    # 内部面は隣り合うセルの間
    assert np.array_equal(m.neighbour - m.owner[: m.n_internal_faces], np.ones(nx - 1, dtype=np.int32))


def test_wall_clustered_slab():
    L, nx, d1 = 1e-3, 64, 1e-6
    x = slab_nodes(L, nx, first_cell=d1)
    dx = np.diff(x)
    assert dx[0] == pytest.approx(d1, rel=1e-9) and dx[-1] == pytest.approx(d1, rel=1e-9)
    np.testing.assert_allclose(dx, dx[::-1], rtol=1e-9)
    assert x[0] == 0.0 and x[-1] == L
    m = generate_slab(L, nx, first_cell=d1, thickness=5e-6)
    assert check_mesh(m).ok
    g = m.geometry()
    assert g.cell_volumes.sum() == pytest.approx(L * 5e-6**2, rel=1e-12)
    assert m.metadata["nx"] == str(nx)


def test_cos_slab_matches_ref1d_formula():
    """余弦分布は verification/ref1d の kind="cos" と同じ式。"""
    L, nx, st = 2e-3, 30, 0.8
    x = slab_nodes(L, nx, cos_stretch=st)
    t = np.linspace(0, 1, nx + 1)
    np.testing.assert_allclose(x / L, (1 - st) * t + st * 0.5 * (1 - np.cos(np.pi * t)), atol=1e-15)
    m = generate_slab(L, nx, cos_stretch=st)
    assert check_mesh(m).ok
    with pytest.raises(ValueError):
        slab_nodes(L, nx, first_cell=1e-6, cos_stretch=0.5)
