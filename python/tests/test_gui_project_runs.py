"""G3a: プロジェクト実行、独立したロック、例題と汎用の境界結果。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import tomlkit

from gsis_tools.gui.examples import create_tube_project
from gsis_tools.gui.general_settings import GeneralSettings
from gsis_tools.gui.history import mass_flow_series, read_history
from gsis_tools.gui.patch_results import MISSING, export_patch_csv, patch_rows
from gsis_tools.gui.paths import default_solver, repo_root
from gsis_tools.gui.project import Project, load_project, save_project
from gsis_tools.gui.project_runs import GpuLock, next_run_directory, prepare_run, solver_repository
from gsis_tools.gui.results import load_run

ROOT = repo_root()


def wait(app, condition, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    assert condition()


def smoke_project(directory, mode="cis"):
    from gsis_tools.pmesh import read_header
    source = tomlkit.parse((ROOT / "cases/smoke/smoke.toml").read_text(encoding="utf-8")).unwrap()
    srcmesh = (ROOT / "cases/smoke" / source["case"]["mesh"]).resolve()
    directory.mkdir(parents=True)
    (directory / "mesh").mkdir()
    shutil.copy2(srcmesh, directory / "mesh/fluid.pmesh")
    s = GeneralSettings(mode=mode, length_auto=False, velocity_auto=False, **source["gas"],
        L_ref=source["conditions"]["L_ref"], T0=source["conditions"]["T0"], p_ref=100,
        vel_n=[4, 4, 4], vel_range=[[-4, 4]] * 3,
        kinetic={"backend": "cpu"}, macro={"init_iterations": 0}, max_pbc=1, n_cis=1)
    p = Project(name="general_smoke", directory=directory, settings=s, boundaries=[dict(b, faces=[]) for b in source["boundary"]],
                mesh=dict(path="mesh/fluid.pmesh", cells=read_header(srcmesh)["n_cells"], validated=True))
    for b in p.boundaries:
        if b["type"] == "pressure_inlet":
            b["pressure"] = s.p_ref
    for key in ("geometry", "settings", "boundaries", "mesh"):
        p.complete(key)
    save_project(p)
    return p


def test_lock_ownership_and_wait(tmp_path):
    path = tmp_path / "runs/.gpu_lock"
    first, second = GpuLock(path, "G 試験"), GpuLock(path, "次の計算")
    assert first.try_acquire()
    assert not second.try_acquire() and not second.release()
    first.set_solver_pid(123)
    owner = json.loads(first.owner_text())
    assert owner["solver_pid"] == 123 and owner["gui_pid"] == os.getpid()
    assert first.release()
    assert second.try_acquire() and second.release()


def test_lock_does_not_reclaim_unknown_or_changed_owner(tmp_path):
    lock = GpuLock(tmp_path / ".gpu_lock", "自分")
    assert lock.try_acquire()
    (lock.path / "owner.txt").write_text("他の担当", encoding="utf-8")
    assert not lock.release() and lock.path.exists()
    assert not GpuLock(lock.path, "再起動した GUI").try_acquire()


def test_lock_owner_write_failure_is_released(tmp_path, monkeypatch):
    lock = GpuLock(tmp_path / ".gpu_lock", "所有者を書けない場合")
    def fail():
        raise OSError("試験用の書き込み失敗")
    monkeypatch.setattr(lock, "_write_owner", fail)
    with pytest.raises(OSError):
        lock.try_acquire()
    assert not lock.path.exists() and not lock.acquired


def test_solver_repository_outside_project(tmp_path):
    assert solver_repository(ROOT / "build/release/src/gsis-solver.exe", tmp_path) == ROOT


@pytest.mark.parametrize("delta", [1, 10, 100, 1000])
def test_example_check_case(tmp_path, delta):
    p = create_tube_project(ROOT, tmp_path / "example", delta)
    assert not p.blocked_reason("run") and p.geometry["source_kind"] == "existing_mesh"
    assert p.mesh["cells"] == 135104
    before = (ROOT / f"cases/tube_vacuum/gsis/tube_delta{delta}_gsis.toml").read_bytes()
    doc = tomlkit.parse((p.directory / "case.toml").read_text(encoding="utf-8")).unwrap()
    assert doc["gsis"]["max_iterations"] == 200
    from gsis_tools.gui.casefile import derive, load_case
    assert derive(load_case(p.directory / "case.toml").model).delta == pytest.approx(delta)
    loaded = load_project(p.directory)
    assert loaded.geometry["example"] == "tube_vacuum" and not loaded.blocked_reason("run")
    if default_solver().is_file():
        from gsis_tools.gui.solver import run_capture
        result = run_capture([str(default_solver()), "check-case", str(p.directory / "case.toml")], cwd=ROOT)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (ROOT / f"cases/tube_vacuum/gsis/tube_delta{delta}_gsis.toml").read_bytes() == before


def test_run_snapshot_and_unique_directory(tmp_path):
    p = smoke_project(tmp_path / "project")
    out = next_run_directory(p)
    case = prepare_run(p, out)
    doc = tomlkit.parse(case.read_text(encoding="utf-8")).unwrap()
    assert Path(doc["case"]["mesh"]).is_absolute()
    assert next_run_directory(p) != out
    p.name = "変更後"
    assert doc["case"]["name"] == "general_smoke"
    assert not load_run(out, ROOT).is_tube_example


def test_patch_totals_si_and_energy_is_not_heat(tmp_path):
    import pyvista as pv
    from gsis_tools.gui.results import RunResult, Scales
    boundary = pv.Plane(i_size=2, j_size=3, i_resolution=1, j_resolution=1).cast_to_unstructured_grid()
    boundary.cell_data["patch_id"] = np.array([0])
    boundary.cell_data["mass_flux"] = np.array([2.0])
    boundary.cell_data["momentum_flux"] = np.array([[1., -2., 3.]])
    boundary.cell_data["energy_flux"] = np.array([100.])
    r = RunResult(tmp_path, summary={"patches": [{"name": "wall", "type": "diffuse_wall"}]})
    rows = patch_rows(r, boundary)
    assert rows[0]["mass_flow_kg_s"] == pytest.approx(12)
    assert rows[0]["force_y_N"] == pytest.approx(-12)
    assert rows[0]["heat_flow_W"] is None
    boundary.cell_data["heat_flux_normal"] = np.array([5.])
    assert patch_rows(r, boundary)[0]["heat_flow_W"] == pytest.approx(30)
    path = export_patch_csv(tmp_path / "patches.csv", rows)
    assert MISSING in path.read_text(encoding="utf-8-sig")
    r.summary["patches"][0].update(mdot_kg_s=7, momentum_flux_nd=[1, 2, 3])
    r.scales = Scales(0.01, 300, 10, 1, 1, 1, 1)
    row = patch_rows(r, boundary)[0]
    assert row["mass_flow_kg_s"] == 7 and row["force_z_N"] == pytest.approx(.003)
    r.summary["patches"][0]["type"] = "vacuum"
    assert patch_rows(r, boundary)[0]["force_x_N"] is None


def test_mass_flow_inlet_history(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("iteration,mdot_feed_kg_s,mdot_exit_kg_s,mdot_wall_kg_s\n1,-2,2,0\n", encoding="utf-8")
    series, _ = mass_flow_series(read_history(path), patch_types={"feed": "mass_flow_inlet", "exit": "vacuum", "wall": "diffuse_wall"})
    assert [s.patch for s in series] == ["feed", "exit"]
    assert [s.y[-1] for s in series] == [2, 2]


@pytest.fixture(scope="module")
def gui(tmp_path_factory):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["GSIS_GUI_3D"] = "static"
    os.environ["GSIS_GUI_SETTINGS"] = str(tmp_path_factory.mktemp("g3_settings") / "gui.ini")
    from gsis_tools.gui.app import create_app
    from gsis_tools.gui.main_window import MainWindow
    app, Theme, Context = create_app()
    ctx = Context(Theme(app, mode="light"))
    ctx.theme.apply()
    win = MainWindow(ctx, view_backend="static")
    yield app, ctx, win
    win.project.dirty = False
    win.shutdown()
    win.close()


def test_float_display_keeps_precision(gui):
    from gsis_tools.gui.widgets.common import FloatField
    value = 5.3333339999999996e-05
    field = FloatField(value)
    assert field.text() == "5.33333e-05" and field.value() == value
    field.setText("0.012345678901")
    assert field.value() == 0.012345678901


@pytest.mark.parametrize("mode,sub", [("cis", "cis-run"), ("gsis", "run")])
def test_project_cpu_run_and_general_results(gui, tmp_path, mode, sub):
    app, ctx, win = gui
    if not ctx.solver_ok():
        pytest.skip("ソルバーがありません")
    p = smoke_project(tmp_path / "project", mode)
    win._install_project(p)
    assert win.show_page("run")
    rp = win.run_page
    assert rp.sub_combo.currentData() == sub
    rp.maxit_check.setChecked(True)
    rp.maxit_spin.setValue(3)
    rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
    assert rp.start()
    wait(app, lambda: not rp.busy)
    assert "終了コード 0" in rp.state.text(), rp.log.text_all()
    out = rp._out_dir
    assert out.parent == p.directory / "runs" and (out / "summary.json").is_file()
    metadata = json.loads((out / "gui_run.json").read_text(encoding="utf-8"))
    assert metadata["command"][1] == sub and "cpu" in metadata["command"]
    assert p.stages["run"] == "done" and win.show_page("results")
    rs = win.results_page
    assert rs.load(out)
    wait(app, lambda: not rs._busy and not rs.runner.busy())
    assert not rs.bundle.errors, rs.bundle.errors
    assert p.stages["results"] == "done"
    assert not rs.tabs.isTabVisible(2) and not rs.tabs.isTabVisible(3)
    assert not rs.history_plot.reference_mdot
    assert rs.boundary_tab.table.rowCount() >= 2
    assert np.allclose(rs.view3d.seed_center.value(), np.asarray(rs.bundle.bounds).reshape(3, 2).mean(axis=1) / rs.bundle.length)
    assert not rs.view3d.stream_check.isChecked()
    rs.boundary_tab.export_csv(tmp_path / "flows.csv")
    assert (tmp_path / "flows.csv").is_file()
    rs.profile.export_csv(tmp_path / "line.csv")
    assert "p/p_ref" in (tmp_path / "line.csv").read_text(encoding="utf-8").splitlines()[0]
    assert not rs.view3d.focus_btn.isVisible()


def test_nsf_wait_and_gpu_queue_cancel_resume(gui, tmp_path, monkeypatch):
    app, ctx, win = gui
    p = smoke_project(tmp_path / "project", "nsf")
    win._install_project(p)
    rp = win.run_page
    assert not rp.start() and "ソルバーの対応待ち" in rp.state.text()
    p.settings.mode = "cis"
    p.settings.kinetic["backend"] = "cuda"
    save_project(p)
    rp.bind_project(p)
    # GPU を起動せずに待機と自動再開を検査する。
    monkeypatch.setattr("gsis_tools.gui.pages.run_page.mesh_memory", lambda *a: {"total": 3_000_000_000, "budget": 4_000_000_000})
    rp.backend_combo.setCurrentIndex(0)
    rp.gpu_lock_path = tmp_path / "fake_repo/runs/.gpu_lock"
    other = GpuLock(rp.gpu_lock_path, "S: 試験用の所有者")
    assert other.try_acquire()
    assert rp.start() and rp._pending_launch is not None
    assert "順番待ち" in rp.state.text() and "試験用の所有者" in rp.lock_info.toPlainText()
    assert not win.save() and not win.show_page("settings")
    rp.stop()
    assert not rp.busy and other.path.is_dir()
    assert rp.start()
    assert other.release()
    launches = []
    monkeypatch.setattr(rp, "_launch", lambda *args: launches.append(args) or True)
    wait(app, lambda: bool(launches), 5)
    assert rp._gpu_lock.acquired
    rp._cleanup()
    assert not other.path.exists()
    rp.gpu_lock_path = None


def test_example_window_and_existing_results(gui, tmp_path):
    app, ctx, win = gui
    win.project.dirty = False
    assert win.open_example(tmp_path / "tube", 1)
    assert "既存メッシュから" in win.geometry_page.note.text()
    assert win.show_page("run")
    assert win.run_page.sub_combo.currentData() == "run"
    out = ROOT / "runs/kinetic/tube_delta1_cis"
    if not (out / "fields.vtu").is_file():
        pytest.skip("既存の管の出力がありません")
    assert win.results_page.run_combo.findData(str(out.resolve())) >= 0
    win._open_results(str(out))
    rs = win.results_page
    wait(app, lambda: not rs._busy and not rs.runner.busy(), 180)
    assert rs.bundle.run.is_tube_example and rs.bundle.centerline is not None
    assert rs.tabs.isTabVisible(2) and rs.tabs.isTabVisible(3)
    assert rs.boundary_tab.table.rowCount() == 3
    assert rs.boundary_tab.view.scene().items
    for mode in ("dark", "light"):
        ctx.theme.set_mode(mode)
        app.processEvents()
        assert rs.boundary_tab.view.scene().items


@pytest.mark.parametrize("outcome", ["success", "failure", "stop", "shutdown", "failed_start"])
def test_launch_releases_only_owned_lock(gui, tmp_path, outcome):
    """GPU の代わりに CPU の Python 子プロセスを起動し、全ての終了経路を通す。"""
    import sys
    app, ctx, win = gui
    rp = win.run_page
    rp.project = None
    rp._gpu_lock = GpuLock(tmp_path / "fake_repo/.gpu_lock", "終了経路の試験")
    if outcome in ("stop", "shutdown"):
        script = "import time; time.sleep(60)"
    else:
        script = "raise SystemExit(1)" if outcome == "failure" else "pass"
    cmd = [str(tmp_path / "missing.exe")] if outcome == "failed_start" else [sys.executable, "-c", script]
    rp._pending_launch = (cmd, "cis-run", tmp_path / "out")
    path = rp._gpu_lock.path
    rp._try_waiting()
    if outcome in ("stop", "shutdown"):
        wait(app, lambda: rp.proc is not None and rp.proc.processId() > 0)
        assert path.is_dir()
        (rp.stop if outcome == "stop" else rp.shutdown)()
    wait(app, lambda: not rp.busy)
    assert not path.exists()


def test_example_edit_settings_reuse_mesh_and_save_as(gui, tmp_path):
    app, ctx, win = gui
    win.project.dirty = False
    assert win.open_example(tmp_path / "original", 10)
    win.settings_page.inputs["tolerance"].setText("0.00002")
    win.settings_page.accept_btn.click()
    assert win.project.stages["settings"] == "done", win.settings_page.note.text() + " / " + win.statusBar().currentMessage()
    win.boundaries_page.patches.setCurrentIndex(win.boundaries_page.patches.findData("inlet"))
    win.boundaries_page.inputs["pressure"].setText("204")
    assert win.boundaries_page.apply_patch()
    win.boundaries_page.accept()
    assert win.mesh_page.generate()
    assert not win.project.blocked_reason("run")
    assert win.save(tmp_path / "copy")
    assert win.run_page.project.directory == tmp_path / "copy"
    win.project.dirty = False
    assert win.open_project(tmp_path / "copy")
    assert not win.project.blocked_reason("run")
    assert win.project.settings.tolerance == 0.00002


def test_patch_ids_follow_mesh_order_without_summary(tmp_path):
    from gsis_tools.gui.casefile import load_case
    from gsis_tools.gui.results import RunResult
    from gsis_tools.pmesh import read_header
    path = ROOT / "cases/smoke/smoke.toml"
    model = load_case(path).model
    model.boundaries.reverse()
    result = RunResult(tmp_path, case_path=path, case=model, boundary_vtu=tmp_path / "boundary.vtu")
    mesh = (path.parent / model.mesh).resolve()
    expected = [p["name"] for p in read_header(mesh)["patches"]]
    assert result.patch_names() == expected


def test_previous_results_remain_accessible_when_settings_change(gui, tmp_path):
    app, ctx, win = gui
    p = smoke_project(tmp_path / "project")
    out = p.directory / "runs/previous"
    out.mkdir(parents=True)
    (out / "summary.json").write_text("{}", encoding="utf-8")
    win._install_project(p)
    p.change("settings")
    win.refresh_workflow()
    assert p.blocked_reason("results")
    assert win.show_page("results")
    assert win.results_page.run_combo.findData(str(out.resolve())) >= 0
