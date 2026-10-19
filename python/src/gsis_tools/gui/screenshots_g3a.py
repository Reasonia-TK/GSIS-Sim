"""G3a の実行・順番待ち・結果を、実 GPU 計算と実ロックを使わず撮影する。"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import time
from pathlib import Path

import tomlkit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/gui_screenshots")
    args = parser.parse_args(argv)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["GSIS_GUI_3D"] = "static"
    from .app import create_app
    from .general_settings import GeneralSettings
    from .main_window import MainWindow
    from .project import Project, save_project
    from .project_runs import GpuLock
    from ..pmesh import read_header

    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gsis_g3a_shots_") as td:
        work = Path(td)
        os.environ["GSIS_GUI_SETTINGS"] = str(work / "settings.ini")
        app, Theme, Context = create_app()
        ctx = Context(Theme(app, mode="light"))
        ctx.theme.apply()
        win = MainWindow(ctx, view_backend="static")
        ctx.theme.apply()
        win.resize(1600, 980)
        win.show()

        def pump(seconds=.3):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(.01)

        def wait(condition):
            end = time.monotonic() + 90
            while not condition() and time.monotonic() < end:
                pump(.03)
            if not condition():
                raise RuntimeError("画面の処理が時間内に終わりませんでした")

        def pair(light, dark):
            for mode, name in (("light", light), ("dark", dark)):
                win.mode_combo.setCurrentIndex(win.mode_combo.findData(mode))
                wait(lambda: not win.results_page.runner.busy())
                pump(.4)
                if not win.grab().save(str(output / f"{name}.png")):
                    raise RuntimeError(name)
                print(name, flush=True)
            win.mode_combo.setCurrentIndex(win.mode_combo.findData("light"))

        lock = None
        try:
            if not win.open_example(work / "tube", 1):
                raise RuntimeError(win.statusBar().currentMessage())
            win.show_page("run")
            pair("51_project_run", "52_dark_project_run")
            rp = win.run_page
            rp.gpu_lock_path = work / "fake_repository/runs/.gpu_lock"
            lock = GpuLock(rp.gpu_lock_path, "S: 別の計算が実行中 (撮影用の模擬ロック)")
            if not lock.try_acquire() or not rp.start() or rp.proc is not None:
                raise RuntimeError("模擬ロックでの待機に失敗しました")
            pair("53_gpu_wait", "54_dark_gpu_wait")
            rp.stop()
            lock.release()
            rp.gpu_lock_path = None
            run = ctx.root / "runs/kinetic/tube_delta1_cis"
            win._open_results(str(run))
            rs = win.results_page
            wait(lambda: not rs._busy and not rs.runner.busy())
            if rs.bundle is None or rs.bundle.errors:
                raise RuntimeError(rs.info.text())
            rs.tabs.setCurrentIndex(0)
            pair("55_project_results", "56_dark_project_results")
            rs.tabs.setCurrentWidget(rs.boundary_tab)
            pair("57_patch_wall_results", "58_dark_patch_wall_results")

            # 汎用画面の写真には CPU の小さな実計算を使う。
            doc = tomlkit.parse((ctx.root / "cases/smoke/smoke.toml").read_text(encoding="utf-8")).unwrap()
            folder = work / "general"
            (folder / "mesh").mkdir(parents=True)
            mesh = ctx.root / "cases/smoke" / doc["case"]["mesh"]
            shutil.copy2(mesh, folder / "mesh/fluid.pmesh")
            settings = GeneralSettings(mode="cis", velocity_auto=False, length_auto=False, **doc["gas"],
                L_ref=doc["conditions"]["L_ref"], T0=doc["conditions"]["T0"], p_ref=100,
                vel_n=[4, 4, 4], vel_range=[[-4, 4]]*3, kinetic=dict(backend="cpu"))
            boundaries = [dict(b, faces=[]) for b in doc["boundary"]]
            for b in boundaries:
                if b["type"] == "pressure_inlet":
                    b["pressure"] = 100
            project = Project(name="CPU の小さな箱", directory=folder, settings=settings,
                boundaries=boundaries, mesh=dict(path="mesh/fluid.pmesh", cells=read_header(mesh)["n_cells"], validated=True))
            for stage in ("geometry", "settings", "boundaries", "mesh"):
                project.complete(stage)
            save_project(project)
            win._install_project(project)
            win.show_page("run")
            rp.maxit_check.setChecked(True)
            rp.maxit_spin.setValue(5)
            rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
            if not rp.start():
                raise RuntimeError(rp.state.text())
            wait(lambda: not rp.busy)
            if not (rp._out_dir / "summary.json").is_file():
                raise RuntimeError(rp.log.text_all())
            pair("59_general_cpu_run", "60_dark_general_cpu_run")
            win._open_results(str(rp._out_dir))
            wait(lambda: not rs._busy and not rs.runner.busy())
            rs.tabs.setCurrentWidget(rs.boundary_tab)
            pair("61_general_results", "62_dark_general_results")
        finally:
            rp = win.run_page
            rp.stop()
            if lock is not None:
                lock.release()
            win.project.dirty = False
            win.shutdown()
            win.close()
            pump(.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
