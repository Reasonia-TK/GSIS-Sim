"""GUI の中心線の取り出し(gsis_tools.gui.centerline / results)。"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("tomlkit")  # gsis_tools.gui.results がケース TOML を読む(extra gui)

from gsis_tools.gui.centerline import TUBE_X_OFFSET, detect_method, extract_centerline
from gsis_tools.gui.paths import repo_root
from gsis_tools.gui.results import centerline_from_cells_csv, load_run, write_centerline_csv

ROOT = repo_root()
RUN = ROOT / "runs/kinetic/tube_delta1_cis"


def f_axis(x):
    return 1.0 - 0.05 * x + 0.01 * np.sin(x)


def test_layers_exact_for_quadratic_profile():
    """x の層ごとに a + b r² が厳密に当たる値なら、軸の値 a がそのまま出る。"""
    rng = np.random.default_rng(1)
    xs = np.linspace(0.05, 21.95, 120)
    x = np.repeat(xs, 25)
    r = rng.uniform(0.0, 0.5, x.size)
    th = rng.uniform(0, 2 * np.pi, x.size)
    y, z = r * np.cos(th), r * np.sin(th)
    p = f_axis(x) - 0.3 * r**2
    ux = 0.2 + 0.01 * x + 0.5 * r**2
    cl = extract_centerline(x, y, z, {"p": p, "ux": ux}, r_max=0.3, x_offset=TUBE_X_OFFSET)
    assert cl.method == "layers"
    assert np.allclose(cl.X, xs - TUBE_X_OFFSET)
    assert np.max(np.abs(cl.values["p"] - f_axis(cl.X + TUBE_X_OFFSET))) < 1e-12
    assert np.max(np.abs(cl.values["ux"] - (0.2 + 0.01 * (cl.X + TUBE_X_OFFSET)))) < 1e-12
    assert cl.interp("p", 0.0) == pytest.approx(f_axis(8.0), abs=1e-3)


def test_bins_for_unstructured_cells():
    """層がない(四面体のような)セルの並びでは区間ごとに当て、線形 + r² の場を厳密に取り出す。"""
    rng = np.random.default_rng(2)
    n = 20000
    x = rng.uniform(0.0, 22.0, n)
    r = 0.6 * np.sqrt(rng.uniform(0, 1, n))
    th = rng.uniform(0, 2 * np.pi, n)
    y, z = r * np.cos(th), r * np.sin(th)
    p = 2.0 - 0.07 * x + 0.4 * r**2
    assert detect_method(x[r < 0.3]) == "bins"
    cl = extract_centerline(x, y, z, {"p": p}, r_max=0.3, x_offset=0.0)
    assert cl.method == "bins" and len(cl) > 20
    assert np.max(np.abs(cl.values["p"] - (2.0 - 0.07 * cl.X))) < 1e-10


def test_flat_layer_falls_back_to_mean():
    x = np.array([1.0, 1.0, 1.0, 2.0, 2.0, 2.0])
    y = np.array([0.1, 0.1, 0.1, 0.0, 0.1, 0.2])
    z = np.zeros(6)
    v = np.array([1.0, 2.0, 3.0, 5.0, 5.01, 5.04])
    cl = extract_centerline(x, y, z, {"v": v}, r_max=0.3)
    assert cl.values["v"][0] == pytest.approx(2.0)  # r が 1 種類だけの層は平均
    assert cl.values["v"][1] == pytest.approx(5.0)


def test_write_centerline_csv(tmp_path):
    x = np.repeat(np.linspace(8.0, 18.0, 11), 4)
    y = np.tile([0.0, 0.1, 0.2, 0.1], 11)
    z = np.tile([0.0, 0.0, 0.0, 0.1], 11)
    cl = extract_centerline(x, y, z, {"p": 1 - 0.1 * (x - 8), "ux": 0.1 + 0 * x, "T": 1 + 0 * x}, x_offset=8.0)
    p = write_centerline_csv(tmp_path / "cl.csv", cl, {"p_paper_gsis": np.full(cl.X.size, np.nan)})
    d = np.genfromtxt(p, delimiter=",", names=True)
    assert list(d.dtype.names) == ["X", "p_over_pin", "ux_over_sqrt2RT0", "T_over_T0", "p_paper_gsis"]
    assert d["X"][0] == pytest.approx(0.0) and d["p_over_pin"][-1] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.skipif(not (RUN / "cells.csv").is_file(), reason="既存の出力がない")
def test_real_run_matches_existing_centerline():
    """cases/tube_vacuum/kinetic/centerline.py が作った centerline.csv(有効数字 6 桁)と一致する。"""
    cl = centerline_from_cells_csv(RUN / "cells.csv")
    ref = np.genfromtxt(RUN / "centerline.csv", delimiter=",", names=True)
    assert cl.X.size == ref["X"].size
    assert np.max(np.abs(cl.X - ref["X"])) < 1e-4
    assert np.max(np.abs(cl.values["p"] - ref["p_over_pin"])) < 1e-5
    assert np.max(np.abs(cl.values["ux"] - ref["ux_over_sqrt2RT0"])) < 1e-5


@pytest.mark.skipif(not (RUN / "fields.vtu").is_file(), reason="既存の出力がない")
def test_real_run_vtu_matches_cells_csv():
    """VTU(SI のセル値)から作った中心線が、cells.csv(無次元)から作ったものと一致する。"""
    pv = pytest.importorskip("pyvista")
    from gsis_tools.gui.results import centerline_from_arrays, centerline_inputs_from_grid

    run = load_run(RUN)
    grid = pv.read(run.cell_vtu)
    c, v = centerline_inputs_from_grid(grid, run.scales)
    cl_vtu = centerline_from_arrays(c, v)
    cl_csv = centerline_from_cells_csv(run.cells_csv)
    assert cl_vtu.X.size == cl_csv.X.size
    for k in ("p", "ux", "T"):
        assert np.max(np.abs(cl_vtu.values[k] - cl_csv.values[k])) < 1e-8
