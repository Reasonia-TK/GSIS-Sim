"""G3c の新境界・入口圧力・結果を小さな CPU 問題で撮影する。"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path


def demo_project(directory, mode="cis"):
    """試験・撮影専用の12セル箱。S3と同じSI入力で、精度検証の例題ではない。"""
    import numpy as np
    from ..geometry.box import box_mesh
    from ..pmesh import write_pmesh
    from .examples import mesh_geometry
    from .general_settings import GeneralSettings
    from .project import Project, save_project

    directory = Path(directory)
    (directory / "mesh").mkdir(parents=True)
    mesh = box_mesh(np.linspace(0, .003, 4), np.linspace(0, .001, 3), np.linspace(0, .001, 3),
                    patches={"feed_A-1": ("inlet", ["xmin"]), "exit": ("outlet", ["xmax"]),
                             "wall": ("wall", ["ymin", "ymax", "zmin", "zmax"])})
    write_pmesh(directory / "mesh/fluid.pmesh", mesh)
    settings = GeneralSettings(mode=mode, length_auto=False, velocity_auto=False, L_ref=.001,
        p_ref=100, vel_n=[8] * 3, vel_range=[[-5, 5]] * 3, n_cis=1, max_pbc=2, cfl_pbc=1,
        kinetic=dict(backend="cpu", precision="fp64", pseudo_time="velocity", pseudo_cfl=1),
        macro=dict(init_iterations=0, relaxation=.5))
    boundaries = [dict(patch="feed_A-1", type="mass_flow_inlet", mass_flow=1e-8, temperature=273.15,
                       tangential_velocity="zero", faces=[1]),
                  dict(patch="exit", type="vacuum", faces=[2]),
                  dict(patch="wall", type="maxwell_wall", temperature=273.15, accommodation=.5, faces=[3])]
    p = Project(name="S3 接続確認 / CPU 12 セル・未収束", directory=directory, settings=settings,
                boundaries=boundaries, geometry=dict(source_kind="existing_mesh"),
                mesh=dict(path="mesh/fluid.pmesh", cells=12, validated=True))
    p.geometry = mesh_geometry(p).metadata
    settings.estimated_cells = 12
    for key in ("geometry", "settings", "boundaries", "mesh"):
        p.complete(key)
    save_project(p)
    return p


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/gui_screenshots")
    parser.add_argument("--solver", default="runs/phase8/s3/gsis-solver-s3-final.exe")
    args = parser.parse_args(argv)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["GSIS_GUI_3D"] = "static"
    work = Path(tempfile.mkdtemp(prefix="gsis_g3c_shots_"))
    os.environ["GSIS_GUI_SETTINGS"] = str(work / "settings.ini")
    from .app import create_app
    from .examples import mesh_geometry
    from .main_window import MainWindow

    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app, Theme, Context = create_app()
    ctx = Context(Theme(app, mode="light"))
    ctx.set_solver_path(Path(args.solver).resolve(), save=False)
    win = MainWindow(ctx, view_backend="static")
    ctx.theme.apply()
    win.resize(1800, 1120)
    win.show()

    def wait(condition):
        end = time.monotonic() + 45
        while not condition() and time.monotonic() < end:
            app.processEvents()
            time.sleep(.01)
        if not condition():
            raise RuntimeError("画面の処理が時間内に終わりませんでした")

    def pair(number, name):
        for offset, mode in enumerate(("light", "dark")):
            win.mode_combo.setCurrentIndex(win.mode_combo.findData(mode))
            wait(lambda: not win.results_page.runner.busy())
            for _ in range(3):
                app.processEvents()
            path = output / f"{number + offset:02d}_{'dark_' if offset else ''}{name}.png"
            if not win.grab().save(str(path)):
                raise RuntimeError(str(path))
            print(path.name, flush=True)
        win.mode_combo.setCurrentIndex(win.mode_combo.findData("light"))

    try:
        p = demo_project(work / "project")
        win._install_project(p)
        win.boundaries_page.load_geometry(mesh_geometry(p))
        win._check_inputs()
        wait(lambda: win.check_job.proc is None)
        win.show_page("boundaries")
        bp = win.boundaries_page
        bp.patches.setCurrentIndex(bp.patches.findData("wall"))
        pair(75, "s3_maxwell_wall")
        bp.patches.setCurrentIndex(bp.patches.findData("feed_A-1"))
        pair(77, "s3_mass_flow_inlet")
        win.show_page("settings")
        # 検査パネルの追加行を先頭側へ表示する。
        win.settings_page.check_panel.setMinimumHeight(440)
        win.settings_page.check_panel.verticalScrollBar().setValue(win.settings_page.check_panel.verticalScrollBar().maximum())
        pair(79, "s3_check_case")
        win.show_page("run")
        rp = win.run_page
        rp.gpu_lock_path = work / "fake_repository/runs/.gpu_lock"
        rp.maxit_check.setChecked(True)
        rp.maxit_spin.setValue(5)
        rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
        rp.precision_combo.setCurrentIndex(rp.precision_combo.findText("fp64"))
        if not rp.start():
            raise RuntimeError(rp.state.text())
        wait(lambda: not rp.busy)
        pair(81, "s3_inlet_pressure_history")
        win._open_results(str(rp._out_dir))
        rs = win.results_page
        wait(lambda: not rs._busy and not rs.runner.busy())
        rs.tabs.setCurrentWidget(rs.boundary_tab)
        pair(83, "s3_inlet_results")
        # NSFの注意は入力画面を切り替えて撮る。追加計算は行わない。
        p.settings.mode = "nsf"
        win.settings_page.load_settings(p.settings)
        win.show_page("boundaries")
        bp.patches.setCurrentIndex(bp.patches.findData("wall"))
        pair(85, "s3_nsf_maxwell_warning")
        print(f"撮影用の入力・出力: {work}", flush=True)
    finally:
        win.project.dirty = False
        win.shutdown()
        win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
