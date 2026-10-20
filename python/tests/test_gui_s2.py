"""G3b: S2 固定版との CPU 結合、停止・保存性・FLTK の回帰。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
import tomlkit

from gsis_tools.gui.casefile import load_case
from gsis_tools.gui.general_settings import GASES, derived, velocity_grid
from gsis_tools.gui.paths import repo_root
from gsis_tools.gui.project import case_document, load_project, save_project
from gsis_tools.gui.solver import run_capture, nsf_args
from test_gui_project_runs import smoke_project, wait

ROOT = repo_root()
SOLVER = Path(os.environ.get("GSIS_SOLVER", ROOT / "runs/phase8/s3/gsis-solver-s3-final.exe"))


@pytest.fixture(scope="module")
def gui(tmp_path_factory):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["GSIS_GUI_3D"] = "static"
    os.environ["GSIS_GUI_SETTINGS"] = str(tmp_path_factory.mktemp("s2_settings") / "gui.ini")
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


@pytest.mark.parametrize("gas,low,high,speed", [("He", 273.15, 273.15, 0), ("Ne", 100, 600, 300),
    ("Ar", 200, 400, 1200), ("Kr", 80, 900, 50), ("Xe", 300, 1200, 700)])
def test_auto_grid_and_derived_match_cpp(tmp_path, gas, low, high, speed):
    p = smoke_project(tmp_path / "project")
    s = p.settings
    for k, value in GASES[gas].items():
        setattr(s, k, value)
    s.velocity_auto = True
    p.boundaries = [dict(patch="inlet", type="pressure_inlet", temperature=low, pressure=100),
                    dict(patch="outlet", type="vacuum"),
                    dict(patch="wall", type="diffuse_wall", temperature=high, velocity=[speed, 0, 0])]
    s.update_boundary_defaults(p.boundaries)
    save_project(p)
    doc = case_document(p)
    assert doc["velocity_grid"].unwrap() == {"auto": True}
    model = load_case(p.directory / "case.toml").model
    assert model.velocity_auto and model.vel_n == velocity_grid(s)[0]
    cp = run_capture([str(SOLVER), "check-case", str(p.directory / "case.toml"), "--json", "--no-mesh"])
    result = json.loads(cp.stdout)
    assert cp.returncode == 0 and result["ok"] and not result["mesh_loaded"]
    d = derived(s)
    assert result["velocity_grid"]["n"] == d["n"]
    assert np.allclose(result["velocity_grid"]["range"], d["ranges"], rtol=2e-13)
    for key, pykey in (("R_J_kg_K", "R"), ("mu_Pa_s", "mu"), ("mean_free_path_m", "mean_free_path"), ("Kn", "Kn")):
        assert result["derived"][key] == pytest.approx(d[pykey], rel=2e-13)
    assert result["memory"] is None and all(x["area_m2"] is None for x in result["patches"])


def test_async_check_panels_and_authoritative_memory(gui, tmp_path):
    app, ctx, win = gui
    p = smoke_project(tmp_path / "project")
    p.stages["mesh"] = "pending"
    win._install_project(p)
    win._check_inputs()
    assert "--no-mesh" in win.check_job.command
    wait(app, lambda: win.check_job.proc is None)
    assert not p._solver_check["mesh_loaded"]
    assert "パッチ inlet" in win.settings_page.check_panel.toPlainText()
    p.stages["mesh"] = "done"
    win._check_inputs()
    assert "--no-mesh" not in win.check_job.command
    wait(app, lambda: win.check_job.proc is None)
    assert p._solver_check["mesh_loaded"]
    from gsis_tools.gui.general_mesh import mesh_memory
    assert mesh_memory(p, p.mesh["cells"]) == p._solver_check["memory"]
    assert "VRAM 確定見積もり(C++)" in win.mesh_page.check_panel.toPlainText()
    p.boundaries[0]["patch"] = "missing_patch"
    win._check_inputs()
    wait(app, lambda: win.check_job.proc is None)
    assert not p._solver_check["ok"] and p._solver_check["errors"]
    assert "エラー:" in win.mesh_page.check_panel.toPlainText()


def test_grid_mismatch_and_cancel_stale_job(gui, tmp_path):
    app, ctx, win = gui
    from gsis_tools.gui.check_case import grid_warnings
    p = smoke_project(tmp_path / "project")
    assert grid_warnings(p.settings, dict(velocity_grid=dict(n=[2, 2, 2], range=[[-1, 1]] * 3)))
    job = win.check_job
    job.start(SOLVER, p)
    old = job.work.name
    p.name = "latest_input"
    job.start(SOLVER, p, no_mesh=True)
    assert not Path(old).exists()
    received = []
    job.completed.connect(received.append)
    wait(app, lambda: job.proc is None)
    job.completed.disconnect(received.append)
    assert len(received) == 1 and received[0]["input"]["name"] == "latest_input"


@pytest.mark.parametrize("mode", ["cis", "gsis", "nsf"])
def test_gui_stop_saves_all_outputs_and_removes_own_stop(gui, tmp_path, mode):
    app, ctx, win = gui
    p = smoke_project(tmp_path / "project", mode)
    win._install_project(p)
    rp = win.run_page
    rp.gpu_lock_path = tmp_path / "fake_repo/.gpu_lock"
    rp.extra_edit.setText("--min-iter 5 --vtu-every=4 --dump-every 3 --save-init --sections 0.5" if mode == "nsf" else "")
    rp.maxit_check.setChecked(True)
    rp.maxit_spin.setValue(1000000)
    rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
    assert rp.start()
    wait(app, lambda: rp.proc is not None and rp.proc.processId() > 0)
    rp.stop()
    out = rp._out_dir
    assert (out / "STOP").is_file() and "停止要求済み" in rp.state.text()
    assert not rp._force_allowed
    wait(app, lambda: not rp.busy)
    assert not (out / "STOP").exists() and not rp.gpu_lock_path.exists()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["stopped_by_user"] and "利用者が停止" in rp.state.text()
    for name in ("fields.vtu", "boundary.vtu", "cells.csv"):
        assert (out / name).is_file()
    values = np.genfromtxt(out / "cells.csv", delimiter=",", names=True)
    assert all(np.all(np.isfinite(values[k])) for k in values.dtype.names)
    cmd = json.loads((out / "gui_run.json").read_text(encoding="utf-8"))["command"]
    assert cmd[1] == "run"
    if mode == "nsf":
        assert not any(a.split("=")[0] in {"--min-iter", "--vtu-every", "--dump-every", "--save-init", "--sections", "--backend", "--precision"} for a in cmd)
    win._open_results(str(out))
    wait(app, lambda: not win.results_page._busy and not win.results_page.runner.busy())
    assert "利用者が停止" in win.results_page.info.text()
    assert not win.results_page.bundle.errors
    rp.extra_edit.clear()
    rp.gpu_lock_path = None


def test_nsf_option_filter():
    args, skipped = nsf_args(["run", "case.toml", "--min-iter=5", "--save-init", "--sections", "1,2", "--threads", "2", "--tol", "1e-5"])
    assert args == ["run", "case.toml", "--threads", "2", "--tol", "1e-5"] and len(skipped) == 3


def test_nsf_then_cis_keeps_solver_flags(gui, tmp_path):
    app, ctx, win = gui
    for mode in ("nsf", "cis"):
        p = smoke_project(tmp_path / mode, mode)
        win._install_project(p)
        assert win.run_page.backend_combo.isEnabled() == (mode == "cis")
        assert win.run_page.precision_combo.isEnabled() == (mode == "cis")
        assert "--backend" in ctx.subcommands()["run"].flags


def test_manual_auto_roundtrip_and_no_temperature_fallback(tmp_path):
    from gsis_tools.gui.casefile import update_document, model_from_dict, validate
    p = smoke_project(tmp_path / "project", "nsf")
    s = p.settings
    s.velocity_auto = True
    s.T0 = s.T_min = s.T_max = 500
    p.boundaries = [dict(patch="outlet", type="vacuum")]
    doc = case_document(p)
    model = model_from_dict(doc.unwrap())
    assert not validate(model)
    assert model.vel_range == velocity_grid(s)[1]
    model.velocity_auto = False
    update_document(doc, model)
    assert "auto" not in doc["velocity_grid"] and "n" in doc["velocity_grid"]
    model.velocity_auto = True
    update_document(doc, model)
    assert doc["velocity_grid"].unwrap() == {"auto": True}
    doc["velocity_grid"]["n"] = [4, 4, 4]
    with pytest.raises(ValueError, match="併用"):
        model_from_dict(doc.unwrap())


@pytest.mark.parametrize("mode,precision", [("cis", "fp32"), ("gsis", "fp64"), ("nsf", "fp32")])
def test_cpp_memory_and_python_estimate_are_separate(tmp_path, mode, precision):
    from gsis_tools.gui.general_settings import derived
    p = smoke_project(tmp_path / "project", mode)
    p.settings.kinetic["precision"] = precision
    save_project(p)
    cp = run_capture([str(SOLVER), "check-case", str(p.directory / "case.toml"), "--json",
                      "--free-vram-bytes", str(2**30 + 12345)])
    m = json.loads(cp.stdout)["memory"]
    assert cp.returncode == 0 and m["reserve"] == 2**30 and m["budget"] == 12345
    if mode == "nsf":
        assert all(m[k] == 0 for k in ("distribution", "workspace", "overhead", "total", "chunk")) and m["fits"]
        assert derived(p.settings)["memory"]["total"] == 0
    else:
        assert m["distribution"] == p.mesh["cells"] * 64 * (8 if precision == "fp64" else 4)
        assert m["overhead"] != derived(p.settings)["memory"]["overhead"]


def test_check_job_failed_start_and_timeout(gui, tmp_path):
    from gsis_tools.gui.check_case import CheckCaseJob
    app, ctx, win = gui
    p = smoke_project(tmp_path / "project")
    win.check_timer.stop()
    win.check_job.cancel()
    job = CheckCaseJob(win)
    results = []
    job.completed.connect(results.append)
    job.start(tmp_path / "missing.exe", p, no_mesh=True)
    wait(app, lambda: bool(results))
    assert not results[-1]["ok"] and results[-1]["errors"] and job.work is None
    job.start(SOLVER, p)
    job._timed_out()
    assert "30秒" in results[-1]["errors"][0] and job.proc is None and job.work is None


@pytest.mark.parametrize("existing", [False, True])
def test_stop_timeout_confirmation_and_ownership(gui, tmp_path, monkeypatch, existing):
    import sys
    from PySide6.QtWidgets import QMessageBox
    app, ctx, win = gui
    rp = win.run_page
    rp.project = None
    out = tmp_path / "out"
    out.mkdir()
    stop = out / "STOP"
    if existing:
        stop.write_text("他者の停止要求", encoding="utf-8")
    try:
        assert rp._launch([sys.executable, "-c", "import time; time.sleep(60)"], "run", out)
        wait(app, lambda: rp.proc is not None and rp.proc.processId() > 0)
        rp.stop()
        assert rp.stop_timer.interval() == 60000 and not rp.stop_btn.isEnabled()
        rp._allow_force_stop()
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
        rp.stop()
        assert rp.busy
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
        rp.stop()
        wait(app, lambda: not rp.busy)
        assert stop.exists() == existing
        if existing:
            assert stop.read_text(encoding="utf-8") == "他者の停止要求"
    finally:
        rp.shutdown()


def test_si_patches_keep_heat_separate_and_export_sign(tmp_path):
    from gsis_tools.gui.patch_results import patch_rows, export_patch_csv
    from gsis_tools.gui.results import RunResult
    p = dict(name="inlet", type="pressure_inlet", mass_flow_kg_s=3., mdot_kg_s=-3., heat_flow_W=5.,
             energy_flow_W=20., force_N=[1., 2., 3.], area_m2=4.)
    r = RunResult(tmp_path, summary=dict(patches=[p]))
    row = patch_rows(r)[0]
    assert row["mass_flow_kg_s"] == 3 and row["force_z_N"] == 3 and row["area_m2"] == 4
    assert row["heat_flow_W"] == 5 and row["energy_flow_W"] == 20 and row["flow_sign"] == "流入"
    path = export_patch_csv(tmp_path / "patches.csv", [row])
    assert "energy_flow_W" in path.read_text(encoding="utf-8-sig") and "流入" in path.read_text(encoding="utf-8-sig")


def test_conservation_warning_and_old_mass_balance(gui, tmp_path):
    from gsis_tools.gui.results import summary_warnings
    summary = dict(converged=True, conservation_diagnostics=dict(warning=True, inlet_kg_s=1e-6,
        outlet_kg_s=0.9e-6, relative_imbalance=-0.1, last_pbc_correction=dict(epsilon=0.01, rho=0.01, u=0.001, T=0.002)),
        mass_balance=dict(tolerance=0.002, kinetic=dict(checked=True, imbalance=0, section_spread=.01)))
    warnings = summary_warnings(summary)
    assert "残差では収束扱いだが" in warnings[0] and "差 -10 %" in warnings[0]
    assert any("mass_balance" in x for x in warnings) and any("last_pbc_correction" in x for x in warnings)
    summary["conservation_diagnostics"]["warning"] = False
    summary["mass_balance"] = {}
    assert not summary_warnings(summary)
    summary["conservation_diagnostics"]["warning"] = True
    app, ctx, win = gui
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    win.run_page._out_dir = out
    assert "補正後の質量" in win.run_page._summary_state()


def test_tetrahedron_detection_defaults_and_explicit_gsis(gui, tmp_path):
    from gsis_tools.builder import BoundaryGroup, polymesh_from_elements, ELEMENT_FACES
    from gsis_tools.pmesh import write_pmesh
    from gsis_tools.gui.general_mesh import apply_tetrahedron_defaults
    from gsis_tools.gui.check_case import TET_WARNING
    p = smoke_project(tmp_path / "project", "gsis")
    mesh = polymesh_from_elements(np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]),
        [("tet", np.array([[0, 1, 2, 3]]))], [BoundaryGroup("wall", "wall", [np.array(ELEMENT_FACES["tet"])])])
    write_pmesh(p.asset(p.mesh["path"]), mesh)
    assert apply_tetrahedron_defaults(p)
    assert p.settings.mode == "cis" and p.settings.kinetic["positivity_limiter"] is True
    assert p.settings.kinetic["pseudo_time"] == "velocity" and p.settings.kinetic["pseudo_cfl"] == 5
    save_project(p)
    q = load_project(p.directory)
    q.settings.mode = "gsis"
    q.mesh.pop("contains_tetrahedra")  # 再生成で再判定しても明示したモードは保持する。
    assert not apply_tetrahedron_defaults(q) and q.settings.mode == "gsis"
    app, ctx, win = gui
    win._install_project(q)
    assert TET_WARNING in win.settings_page.note.text()
    assert TET_WARNING in win.run_page.state.text()
    doc = case_document(q)
    assert doc["kinetic"]["pseudo_cfl"] == 5


@pytest.mark.parametrize("entry", ["session", "engine", "gui"])
def test_gmsh_no_popup_and_no_new_repository_root_files(tmp_path, entry):
    import gmsh
    if gmsh.isInitialized():
        gmsh.finalize()
    before = {x.name for x in ROOT.iterdir()}
    if entry == "session":
        from gsis_tools.gmsh_convert import gmsh_session
        with gmsh_session(verbosity=0) as g:
            assert g.option.getNumber("General.NoPopup") == 1
            with pytest.raises(Exception):
                g.open(str(tmp_path / "missing.brep"))
    elif entry == "engine":
        from gsis_tools.mcp.engine import ModelEngine
        from gsis_tools.mcp.workspace import Workspace
        engine = ModelEngine(Workspace(tmp_path / "work"))
        try:
            assert engine.gmsh.option.getNumber("General.NoPopup") == 1
            engine.call("model_new", {"unit": "mm"})
            engine.call("add_box", dict(corner=[0, 0, 0], size=[1, 1, 1], name="box"))
            engine.call("mesh_settings", dict(size_max=.5, size_min=.2, threads=1))
            engine.call("mesh_generate", dict(dim=3))
        finally:
            engine.close()
    else:
        from gsis_tools.gui.meshgen import ensure_gmsh_main_thread
        ensure_gmsh_main_thread()
        assert gmsh.option.getNumber("General.NoPopup") == 1
        gmsh.finalize()
    assert {x.name for x in ROOT.iterdir()} == before
