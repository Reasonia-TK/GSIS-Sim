"""テストの共通部品。"""

from __future__ import annotations

import numpy as np
import pytest

from gsis_tools.geometry.box import box_mesh


@pytest.fixture
def box3():
    """3×2×2 の不等間隔の箱(6 側面が別のパッチ)。"""
    return box_mesh([0.0, 0.2, 0.5, 1.0], [0.0, 0.7, 2.0], [0.0, 1.0, 3.0])


def perturbed_box(n: int = 6, amp: float = 0.03, seed: int = 0):
    """単位立方体の内部の節点をずらした箱(面は平面でなくなる)。"""
    m = box_mesh(np.linspace(0, 1, n + 1), np.linspace(0, 1, n + 1), np.linspace(0, 1, n + 1), renumber="rcm")
    rng = np.random.default_rng(seed)
    P = m.points.copy()
    inner = np.all((P > 1e-12) & (P < 1 - 1e-12), axis=1)
    P[inner] += rng.uniform(-amp, amp, size=(int(inner.sum()), 3)) / n
    m.points = P
    m.geometry(recompute=True)
    return m
