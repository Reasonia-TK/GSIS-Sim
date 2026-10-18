"""GUI の論文データとの比較(gsis_tools.gui.reference)。"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("tomlkit")  # gsis_tools.gui.results がケース TOML を読む(extra gui)

from gsis_tools.gui.paths import reference_dir, repo_root
from gsis_tools.gui.reference import (
    compare_series,
    load_fig3,
    load_table1,
    paper_delta,
    relative_error,
    table1_row,
)
from gsis_tools.gui.results import centerline_from_cells_csv

ROOT = repo_root()

pytestmark = pytest.mark.skipif(not (reference_dir() / "table1_tube.csv").is_file(), reason="論文データがない")


def test_table1_values():
    rows = load_table1()
    assert [r.delta_rp for r in rows] == [1.0, 10.0, 100.0, 1000.0]
    assert [r.gsis_pbc_M for r in rows] == [0.198, 0.326, 0.885, 1.275]
    assert [r.ref_method for r in rows] == ["DSMC", "DSMC", "DSMC", "NSF-FLUENT"]
    assert rows[3].cis_iter == 7847 and rows[0].gsis_pbc_iter == 17
    assert table1_row(rows, 100.00000000000001).M_ref == 0.898
    assert table1_row(rows, 50.0) is None


def test_paper_delta_and_relative_error():
    assert paper_delta(1.0000000000000002) == 1.0
    assert paper_delta(999.5) == 1000.0
    assert paper_delta(2.0) is None and paper_delta(None) is None
    assert relative_error(0.19943, 0.198) == pytest.approx(0.0072222, rel=1e-4)
    assert math.isnan(relative_error(math.nan, 0.198))


def test_fig3_files():
    X, P = load_fig3("pressure", "gsis", 1.0)
    assert X.size == 691 and np.all(np.diff(X) >= 0)
    assert load_fig3("velocity", "dsmc", 100.0)[0].size == 20
    assert load_fig3("pressure", "dsmc", 1.0) is None
    assert load_fig3("pressure", "gsis", 1000.0) is None


def test_compare_series_basic():
    Xr = np.array([0.05, 0.1, 1.0, 2.0, 5.0, 9.0, 10.0, 10.5])
    Vr = 1.0 - 0.1 * Xr
    Xm = np.linspace(0.0, 10.0, 201)
    same = compare_series(Xm, 1.0 - 0.1 * Xm, Xr, Vr)
    assert same.n == 5  # X < 0.15 と X > 10 と計算の範囲外は使わない
    assert same.rms == pytest.approx(0.0, abs=1e-12)
    shifted = compare_series(Xm, 1.01 - 0.1 * Xm, Xr, Vr)
    assert shifted.rms == pytest.approx(0.01) and shifted.mean == pytest.approx(0.01)
    bump = 1.0 - 0.1 * Xm + np.where(np.abs(Xm - 5.0) < 1e-9, 0.05, 0.0)
    d = compare_series(Xm, bump, Xr, Vr)
    assert d.max_abs == pytest.approx(0.05) and d.x_at_max == pytest.approx(5.0)
    assert compare_series(Xm[:1], Xm[:1], Xr, Vr) is None


RUN = ROOT / "runs/kinetic/tube_delta1_cis"


@pytest.mark.skipif(not (RUN / "cells.csv").is_file(), reason="既存の出力がない")
def test_real_run_differences_match_phase2_report():
    """docs/phase2_report.md の値(論文 CIS と p の差の RMS 0.0054・最大 0.0087、u の RMS 0.023・最大 0.050 @ X=9.94)。"""
    cl = centerline_from_cells_csv(RUN / "cells.csv")
    dp = compare_series(cl.X, cl.values["p"], *load_fig3("pressure", "cis", 1.0))
    du = compare_series(cl.X, cl.values["ux"], *load_fig3("velocity", "cis", 1.0))
    assert dp.rms == pytest.approx(0.0054, abs=5e-5) and dp.max_abs == pytest.approx(0.0087, abs=5e-5)
    assert du.rms == pytest.approx(0.023, abs=5e-4) and du.max_abs == pytest.approx(0.050, abs=5e-4)
    assert du.x_at_max == pytest.approx(9.94, abs=0.01)
    dg = compare_series(cl.X, cl.values["p"], *load_fig3("pressure", "gsis", 1.0))
    assert dg.rms == pytest.approx(0.0096, abs=5e-5)
