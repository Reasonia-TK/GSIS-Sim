"""G3b の S2 検査・停止・SI パッチ・保存性警告を CPU の小さな箱で撮影する。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import tomlkit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/gui_screenshots")
    parser.add_argument("--solver", default="runs/phase8/s2/gsis-solver-s2-final.exe")
    args = parser.parse_args(argv)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["GSIS_GUI_3D"] = "static"
    from .app import create_app
    from .general_settings import GeneralSettings
    from .main_window import MainWindow
    from .paths import repo_root
    from .project import Project, save_project
    from ..pmesh import read_header

    root = repo_root()
    work = Path(tempfile.mkdtemp(prefix="gsis_g3b_shots_"))
    os.environ["GSIS_GUI_SETTINGS"] = str(work / "settings.ini")
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app, Theme, Context = create_app()
    ctx = Context(Theme(app, mode="light"))
    ctx.set_solver_path(Path(args.solver).resolve(), save=False)
    ctx.theme.apply()
    win = MainWindow(ctx, view_backend="static")
    ctx.theme.apply()
    win.resize(1700, 1060)
    win.show()

    def pump():
        app.processEvents()
        time.sleep(.01)

    def wait(condition):
        end = time.monotonic() + 45
        while not condition() and time.monotonic() < end:
            pump()
        if not condition():
            raise RuntimeError("画面の処理が時間内に終わりませんでした")

    def pair(number, name, *, pending_stop=False):
        for offset, mode in enumerate(("light", "dark")):
            win.mode_combo.setCurrentIndex(win.mode_combo.findData(mode))
            # 実ソルバーの STOP 保存は進むが、撮影中は Qt の終了通知を処理しない。
            if not pending_stop:
                wait(lambda: not win.results_page.runner.busy())
                pump()
            path = output / f"{number + offset:02d}_{'dark_' if offset else ''}{name}.png"
            if not win.grab().save(str(path)):
                raise RuntimeError(str(path))
            print(path.name, flush=True)
        win.mode_combo.setCurrentIndex(win.mode_combo.findData("light"))

    try:
        source = tomlkit.parse((root / "cases/smoke/smoke.toml").read_text(encoding="utf-8")).unwrap()
        folder = work / "project"
        (folder / "mesh").mkdir(parents=True)
        mesh = (root / "cases/smoke" / source["case"]["mesh"]).resolve()
        shutil.copy2(mesh, folder / "mesh/fluid.pmesh")
        settings = GeneralSettings(mode="gsis", velocity_auto=False, length_auto=False, **source["gas"],
            L_ref=source["conditions"]["L_ref"], T0=source["conditions"]["T0"], p_ref=100,
            vel_n=[4, 4, 4], vel_range=[[-4, 4]] * 3, kinetic=dict(backend="cpu"),
            macro=dict(init_iterations=0), n_cis=1, max_pbc=1)
        boundaries = [dict(b, faces=[]) for b in source["boundary"]]
        for b in boundaries:
            if b["type"] == "pressure_inlet":
                b["pressure"] = 100
        p = Project(name="S2 接続確認 / CPU の小さな箱", directory=folder, settings=settings,
            geometry=dict(source_kind="existing_mesh"), boundaries=boundaries,
            mesh=dict(path="mesh/fluid.pmesh", cells=read_header(mesh)["n_cells"], validated=True))
        for stage in ("geometry", "settings", "boundaries", "mesh"):
            p.complete(stage)
        save_project(p)
        win._install_project(p)
        p.stages["mesh"] = "pending"
        win._check_inputs()
        wait(lambda: win.check_job.proc is None)
        win.show_page("settings")
        pair(63, "s2_check_settings")
        p.stages["mesh"] = "done"
        win._check_inputs()
        wait(lambda: win.check_job.proc is None)
        win.show_page("mesh")
        pair(65, "s2_check_mesh")

        win.show_page("run")
        rp = win.run_page
        rp.gpu_lock_path = work / "fake_repository/runs/.gpu_lock"
        rp.maxit_check.setChecked(True)
        rp.maxit_spin.setValue(1000000)
        rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
        if not rp.start():
            raise RuntimeError(rp.state.text())
        wait(lambda: rp.proc is not None and rp.proc.processId() > 0)
        rp.stop()
        pair(67, "s2_stop_request", pending_stop=True)
        wait(lambda: not rp.busy)
        pair(69, "s2_stopped")
        win._open_results(str(rp._out_dir))
        rs = win.results_page
        wait(lambda: not rs._busy and not rs.runner.busy())
        rs.tabs.setCurrentWidget(rs.boundary_tab)
        pair(71, "s2_patch_si")
        # 保存性の警告は診断値だけを模擬する。写真と文書に表示例であることを明記する。
        diagnostic_out = work / "diagnostic_example"
        shutil.copytree(rp._out_dir, diagnostic_out)
        path = diagnostic_out / "summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary.update(converged=True, stopped_by_user=False, conservation_diagnostics=dict(
            warning=True, inlet_kg_s=1e-6, outlet_kg_s=.9e-6, relative_imbalance=-.1,
            last_pbc_correction=dict(epsilon=.01, rho=.01, u=.001, T=.002)))
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        rs.load(diagnostic_out)
        wait(lambda: not rs._busy and not rs.runner.busy())
        rs.bundle.run.notes.append("保存性警告の表示例: 診断値は模擬")
        rs._on_loaded(rs.bundle)
        wait(lambda: not rs.runner.busy())
        pair(73, "s2_conservation_example")
        print(f"撮影用の入力・出力: {work}", flush=True)
    finally:
        win.project.dirty = False
        win.shutdown()
        win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
