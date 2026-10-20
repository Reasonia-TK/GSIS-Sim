"""G3c: S3 固定版の新境界の入力・CPU実行・履歴・結果の接続。"""

from __future__ import annotations

import copy
import csv
import json
import os
from pathlib import Path

import numpy as np
import pytest

from gsis_tools.gui.boundaries import boundary_notes, validate_boundary
from gsis_tools.gui.casefile import load_case, model_from_dict, update_document, validate
from gsis_tools.gui.check_case import grid_warnings
from gsis_tools.gui.general_settings import velocity_grid
from gsis_tools.gui.history import History, mass_balance, mass_flow_series, read_history
from gsis_tools.gui.patch_results import COLUMNS, export_patch_csv, mass_flow_inlet_rows, patch_rows
from gsis_tools.gui.paths import repo_root
from gsis_tools.gui.project import case_document, load_project, save_project
from gsis_tools.gui.project_runs import prepare_run
from gsis_tools.gui.results import load_run, summary_warnings
from gsis_tools.gui.screenshots_g3c import demo_project
from gsis_tools.gui.solver import run_capture
from test_gui_project_runs import wait

ROOT = repo_root()
SOLVER = Path(os.environ.get("GSIS_SOLVER", ROOT / "runs/phase8/s3/gsis-solver-s3-final.exe"))


@pytest.fixture(scope="module")
def gui(tmp_path_factory):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["GSIS_GUI_3D"] = "static"
    os.environ["GSIS_GUI_SETTINGS"] = str(tmp_path_factory.mktemp("s3_settings") / "gui.ini")
    from gsis_tools.gui.app import create_app
    from gsis_tools.gui.main_window import MainWindow
    app, Theme, Context = create_app()
    ctx = Context(Theme(app, mode="light"))
    ctx.set_solver_path(SOLVER, save=False)
    win = MainWindow(ctx, view_backend="static")
    yield app, ctx, win
    win.project.dirty = False
    win.shutdown()
    win.close()


@pytest.mark.parametrize("key,value", [("accommodation", 0), ("accommodation", -1), ("accommodation", 1.01),
    ("accommodation", float("nan")), ("mass_flow", 0), ("mass_flow", -1), ("mass_flow", float("inf")),
    ("temperature", 0), ("tangential_velocity", [1, 0, 0])])
def test_reject_invalid_boundary(tmp_path, key, value):
    p = demo_project(tmp_path / "project")
    b = copy.deepcopy(p.boundaries[2 if key == "accommodation" else 0])
    b[key] = value
    with pytest.raises(ValueError):
        validate_boundary(b, p.geometry["faces"])
    doc = case_document(p).unwrap()
    doc["boundary"][2 if key == "accommodation" else 0][key] = value
    assert validate(model_from_dict(doc))


def test_case_and_project_roundtrip(tmp_path, gui):
    app, ctx, win = gui
    p = demo_project(tmp_path / "project")
    p.boundaries[2]["velocity"] = [12, 0, 0]
    save_project(p)
    assert load_project(p.directory / "project.toml").boundaries == p.boundaries
    original = load_case(p.directory / "case.toml")
    assert not validate(original.model)
    assert original.model.boundaries[0].mass_flow == 1e-8
    assert original.model.boundaries[0].tangential_velocity == "zero"
    assert original.model.boundaries[2].copy().accommodation == .5
    from gsis_tools.gui.pages.case_page import BoundaryTable
    editor = BoundaryTable()
    try:
        editor.set_boundaries(original.model.boundaries)
        model = original.model.copy()
        model.boundaries = editor.boundaries()
        assert not validate(model)
        result = model_from_dict(update_document(original.doc, model).unwrap())
        assert result.boundaries == original.model.boundaries
    finally:
        editor.close()


@pytest.mark.parametrize("motion", [None, [1200, 0, 0]])
def test_new_boundaries_auto_grid_and_check_case(tmp_path, motion):
    p = demo_project(tmp_path / "project")
    p.settings.velocity_auto = True
    p.settings.p_ref = 321
    p.boundaries[0]["temperature"] = 100
    p.boundaries[2]["temperature"] = 700
    if motion:
        p.boundaries[2]["velocity"] = motion
    p.settings.update_boundary_defaults(p.boundaries)
    assert p.settings.p_ref == 321
    save_project(p)
    cp = run_capture([str(SOLVER), "check-case", str(p.directory / "case.toml"), "--json",
                      "--free-vram-bytes", str(8 * 2**30)])
    assert cp.returncode == 0, cp.stdout + cp.stderr
    result = json.loads(cp.stdout)
    assert result["ok"] and result["mesh_loaded"]
    n, ranges = velocity_grid(p.settings)
    assert n == result["velocity_grid"]["n"]
    np.testing.assert_allclose(ranges, result["velocity_grid"]["range"], rtol=2e-13)
    assert not grid_warnings(p.settings, result)
    assert result["velocity_grid"]["T_min_K"] == 100 and result["velocity_grid"]["T_max_K"] == 700
    assert result["velocity_grid"]["u_max_m_s"] == pytest.approx(np.linalg.norm(motion) if motion else 0)
    assert result["patches"][0]["pressure_Pa"] == 321
    assert result["patches"][0]["target_mass_flow_kg_s"] == 1e-8
    assert result["patches"][0]["tangential_velocity"] == "zero"
    assert result["patches"][2]["accommodation"] == .5
    assert load_case(p.directory / "case.toml").model.vel_n == n


def test_boundary_form_and_notes(gui, tmp_path):
    app, ctx, win = gui
    p = demo_project(tmp_path / "project", "nsf")
    win._install_project(p)
    bp = win.boundaries_page
    bp.patches.setCurrentIndex(bp.patches.findData("wall"))
    assert bp.collect()["accommodation"] == .5
    assert "NSF" in bp.boundary_note.text() and "すべり係数" in bp.boundary_note.text()
    bp.inputs["accommodation"].setText("1.01")
    assert not bp.apply_btn.isEnabled()
    bp.inputs["accommodation"].setText("1")
    assert bp.apply_btn.isEnabled()
    bp.wall_motion.setChecked(True)
    bp.velocity[0].setText("10")
    assert bp.collect()["velocity"] == [10, 0, 0]
    bp.patches.setCurrentIndex(bp.patches.findData("feed_A-1"))
    assert "初期" in bp.boundary_note.text() and "圧力を指定する境界がない" in bp.boundary_note.text()
    assert not bp.inputs["pressure"].isVisibleTo(bp)
    for invalid in ("0", "-1", "nan", ""):
        bp.inputs["mass_flow"].setText(invalid)
        assert not bp.apply_btn.isEnabled()
    bp.inputs["mass_flow"].setText("2e-8")
    bp.inputs["tangential_velocity"].setCurrentIndex(bp.inputs["tangential_velocity"].findData("extrapolate"))
    assert bp.collect()["mass_flow"] == 2e-8
    assert bp.collect()["tangential_velocity"] == "extrapolate"
    assert bp.apply_patch()
    save_project(p)
    win._check_inputs()
    wait(app, lambda: win.check_job.proc is None)
    panel = win.settings_page.check_panel.toPlainText()
    assert "目標流量: 2e-08" in panel and "初期 p_b = p_ref: 100" in panel and "適応係数 α: 0.5" in panel
    assert "NSF" in win.settings_page.boundary_note.text()
    assert any("NSF" in n for n in boundary_notes(p.settings, p.boundaries))
    p.settings.mode = "cis"
    assert not any("NSF" in n for n in boundary_notes(p.settings, p.boundaries))
    inlet = next(b for b in p.boundaries if b["type"] == "mass_flow_inlet")
    del inlet["tangential_velocity"]
    validate_boundary(inlet, p.geometry["faces"])


@pytest.mark.parametrize("mode", ["cis", "gsis", "nsf"])
def test_cpu_run_history_results_and_inlet_diagnostics(gui, tmp_path, mode):
    app, ctx, win = gui
    p = demo_project(tmp_path / mode, mode)
    win._install_project(p)
    rp = win.run_page
    rp.gpu_lock_path = tmp_path / "fake_repo/runs/.gpu_lock"
    rp.maxit_check.setChecked(True)
    rp.maxit_spin.setValue(3)
    rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
    rp.precision_combo.setCurrentIndex(rp.precision_combo.findText("fp64"))
    assert rp.start(), rp.state.text()
    wait(app, lambda: not rp.busy)
    assert not rp.gpu_lock_path.exists()
    out = rp._out_dir
    if mode != "nsf":
        command = json.loads((out / "gui_run.json").read_text(encoding="utf-8"))["command"]
        assert command[command.index("--precision") + 1] == "fp64"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["iterations"] == 3
    inlet = mass_flow_inlet_rows(summary)[0]
    assert inlet["target_mass_flow_kg_s"] == pytest.approx(1e-8, rel=1e-13, abs=0)
    assert inlet["pressure_Pa"] > 0
    assert inlet["mass_flow_relative_error"] == pytest.approx(abs(inlet["mass_flow_kg_s"] - 1e-8) / 1e-8)
    hist = read_history(out / "history.csv")
    assert hist.n in (3, 4) and hist.last("iteration") == 3
    assert hist.inlet_pressure_columns() == {"feed_A-1": "p_b_feed_A-1_Pa"}
    assert hist.last("p_b_feed_A-1_Pa") == pytest.approx(inlet["pressure_Pa"])
    assert len(rp.plot.panel.figure.axes) == 3
    assert "p_b [Pa]" in rp.plot.panel.figure.axes[2].get_ylabel()
    if mode == "gsis":
        diagnostic = summary["conservation_diagnostics"]
        assert diagnostic["inlet_kg_s"] == pytest.approx(inlet["mass_flow_kg_s"])
    types = {p["name"]: p["type"] for p in summary["patches"]}
    series, _ = mass_flow_series(hist, patch_types=types)
    feed = next(s for s in series if s.patch == "feed_A-1" and s.source == "main")
    assert feed.role == "in" and np.isfinite(mass_balance(series))
    assert summary_warnings(summary) and "0.1%" in rp.state.text()
    win._open_results(str(out))
    rs = win.results_page
    wait(app, lambda: not rs._busy and not rs.runner.busy())
    assert not rs.bundle.errors
    table = rs.boundary_tab.inlet_table
    assert table.rowCount() == 1 and table.item(0, 0).text() == "feed_A-1"
    assert float(table.item(0, 1).text()) == pytest.approx(inlet["pressure_Pa"], rel=1e-5)
    assert float(table.item(0, 4).text()) == pytest.approx(inlet["mass_flow_relative_error"] * 100, rel=1e-5)
    assert "0.1%" in rs.info.text() and "超え" in rs.boundary_tab.inlet_note.text()
    assert len(rs.history_plot.panel.figure.axes) == 3
    rows = patch_rows(load_run(out))
    wall = next(r for r in rows if r["type"] == "maxwell_wall")
    assert wall["accommodation"] == .5
    csv_path = export_patch_csv(tmp_path / "patches.csv", rows)
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        exported = list(csv.DictReader(fh))
    assert exported[0]["pressure_Pa"] and float(exported[0]["target_mass_flow_kg_s"]) == pytest.approx(1e-8, rel=1e-13, abs=0)
    assert rs.boundary_tab.table.item(2, COLUMNS.index("accommodation")).text() == "0.5"
    rp.gpu_lock_path = None


@pytest.mark.parametrize("error,warn", [(0, False), (.001, False), (.00101, True), (.1, True), (None, True)])
def test_target_warning_threshold_and_missing(error, warn):
    summary = dict(stopped_by_user=True, patches=[dict(name="feed", type="mass_flow_inlet",
        pressure_Pa=100, target_mass_flow_kg_s=1e-8, mass_flow_relative_error=error)])
    assert bool(summary_warnings(summary)) == warn


def test_pressure_series_optional_and_multiple(gui):
    app, ctx, win = gui
    hist = History(["iteration", "epsilon", "p_b_feed_A-1_Pa", "p_b_second_Pa"], [[1, 1e-3, 100, 200]])
    assert hist.inlet_pressure_columns() == {"feed_A-1": "p_b_feed_A-1_Pa", "second": "p_b_second_Pa"}
    win.run_page.plot.set_history(hist)
    ax = win.run_page.plot.panel.figure.axes[2]
    assert len(ax.lines) == 2
    assert [line.get_ydata()[0] for line in ax.lines] == [100, 200]
    old = History(["iteration", "epsilon"], [[1, .01]])
    assert not old.inlet_pressure_columns()
    win.run_page.plot.set_history(old)
    assert len(win.run_page.plot.panel.figure.axes) == 2


@pytest.mark.parametrize("mode", ["cis", "gsis", "nsf"])
def test_stopped_inlet_keeps_pressure_and_warning(tmp_path, mode):
    p = demo_project(tmp_path / "project", mode)
    out = p.directory / "runs/stopped"
    case = prepare_run(p, out)
    (out / "STOP").touch()
    cmd = [str(SOLVER), "run", str(case), "--out", str(out), "--max-iter", "3", "--threads", "2"]
    if mode != "nsf":
        cmd.extend(["--backend", "cpu", "--precision", "fp64"])
    cp = run_capture(cmd)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["stopped_by_user"] and summary["iterations"] <= 3
    row = mass_flow_inlet_rows(summary)[0]
    assert row["pressure_Pa"] > 0 and row["mass_flow_relative_error"] > .001
    assert any("0.1%" in warning for warning in summary_warnings(summary))
    history = read_history(out / "history.csv")
    if summary["iterations"] == 0:
        # GSISは開始前のSTOPで外側反復に入らず、履歴には評価行を作らない。
        assert history.n == 0 and row["pressure_Pa"] == p.settings.p_ref
    else:
        assert history.last("p_b_feed_A-1_Pa") == pytest.approx(row["pressure_Pa"])


def test_flow_target_and_conservation_warnings_are_separate():
    summary = dict(converged=True, patches=[dict(name="feed", type="mass_flow_inlet",
        target_mass_flow_kg_s=10, mass_flow_kg_s=9)], conservation_diagnostics=dict(warning=True,
        inlet_kg_s=9, outlet_kg_s=8, relative_imbalance=-1/9))
    messages = summary_warnings(summary)
    assert any("0.1%" in m for m in messages) and any("保存" in m for m in messages)
    assert mass_flow_inlet_rows(summary)[0]["mass_flow_relative_error"] == .1
