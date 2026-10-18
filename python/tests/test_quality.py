"""品質の指標。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from gsis_tools.geometry.box import box_mesh
from gsis_tools.quality import compute_quality, format_report, summary


def test_orthogonal_box():
    m = box_mesh(np.linspace(0, 1, 5), [0.0, 0.5, 2.0], np.linspace(0, 3, 4))
    q = compute_quality(m)
    assert q.non_orthogonality.max() < 1e-6
    assert q.boundary_non_orthogonality.max() < 1e-6
    assert q.skewness.max() < 1e-12
    assert q.boundary_skewness.max() < 1e-12
    # y 方向の間隔は 0.5 と 1.5 → 隣り合うセルの体積比は 3
    assert q.volume_ratio.max() == pytest.approx(3.0)
    assert q.cell_max_edge.max() == pytest.approx(1.5)
    assert q.cell_min_edge.min() == pytest.approx(0.25)
    assert q.cell_type_counts() == {"hexahedron": m.n_cells}


def test_sheared_box_non_orthogonality():
    """x' = x + y tan(α) のせん断: x 方向に隣り合うセルの面の非直交角は α。"""
    alpha = math.radians(25.0)
    m = box_mesh(np.linspace(0, 1, 4), [0.0, 1.0], [0.0, 1.0])
    P = m.points.copy()
    P[:, 0] += P[:, 1] * math.tan(alpha)
    m.points = P
    m.geometry(recompute=True)
    q = compute_quality(m)
    np.testing.assert_allclose(q.non_orthogonality, 25.0, rtol=1e-10)
    assert q.skewness.max() < 1e-12  # 中心を結ぶ線は面の中心を通る


def test_report_text():
    m = box_mesh(np.linspace(0, 1, 3), np.linspace(0, 1, 3), np.linspace(0, 1, 3))
    text = format_report(m, title="箱", exact_volume=1.0, exact_areas={"xmin": 1.0}, regions={"全体": np.ones(m.n_cells, bool)})
    assert "# 箱" in text and "セル数 | 8" in text and "非直交角の分布" in text
    s = summary(m)
    assert s["n_cells"] == 8 and s["cell_types"] == {"hexahedron": 8}
    assert s["total_volume"] == pytest.approx(1.0)
