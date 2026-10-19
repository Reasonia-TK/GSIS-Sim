"""G1・G2 の画面写真を作る。CAD は一時フォルダで作り、API・ソルバーは起動しない。

    python -m gsis_tools.gui.screenshots [--out docs/gui_screenshots] [--size 1600x980]

01–31・39 は旧画面の記録として保持し、32–43 と G2 の 44–50 を生成する。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description="G1・G2 の形状・設定・境界・メッシュの画面写真")
    ap.add_argument("--out", default="docs/gui_screenshots")
    ap.add_argument("--size", default="1600x980")
    ap.add_argument("--only", default="")
    ap.add_argument("--interactive", action="store_true")
    args = ap.parse_args(argv)
    if not args.interactive:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["GSIS_GUI_3D"] = "static"
    from .app import create_app
    from .main_window import MainWindow
    from ..mcp.engine import ModelEngine
    from ..mcp.workspace import Workspace

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gsis_g1_shots_") as td:
        folder = Path(td)
        os.environ["GSIS_GUI_SETTINGS"] = str(folder / "settings.ini")
        app, Theme, Context = create_app()
        ctx = Context(Theme(app, mode="light"))
        ctx.theme.apply()
        win = MainWindow(ctx)
        ctx.theme.apply()
        win.resize(*(int(x) for x in args.size.split("x")))
        win.show()

        def pump(seconds=0.2):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.01)

        def wait_idle():
            end = time.monotonic() + 90
            while win.geometry_page.busy and time.monotonic() < end:
                pump(0.03)
            if win.geometry_page.busy or win.project.stages["geometry"] != "done":
                raise RuntimeError(win.geometry_page.note.text())
            pump()

        def shot(name):
            if args.only and not any(part in name for part in args.only.split(",")):
                return
            pump(0.4)
            path = out / (name + ".png")
            if not win.grab().save(str(path)):
                raise RuntimeError(f"画面写真を保存できません: {path}")
            print(path.name, flush=True)

        def theme(mode):
            win.mode_combo.setCurrentIndex(win.mode_combo.findData(mode))
            pump()

        try:
            win.new_project(folder / "project", "オリフィスの形状確認")
            engine = ModelEngine(Workspace(folder / "cad"))
            try:
                engine.call("model_new", {"unit": "mm"})
                engine.call("add_box", dict(corner=[-5, -4, -0.8], size=[10, 8, 1.6], name="plate"))
                engine.call("add_cylinder", dict(base=[0, 0, -2], axis=[0, 0, 4], radius=1.3, name="hole"))
                engine.call("boolean", dict(op="cut", objects=["plate"], tools=["hole"], name="orifice"))
                engine.call("export", {"path": "orifice.step"})
            finally:
                engine.close()
            gp, sp = win.geometry_page, win.settings_page
            gp.load_cad(folder / "cad/orifice.step")
            wait_idle()
            gp.faces.selectRow(0)
            shot("32_geometry_cad")
            theme("dark")
            shot("33_dark_geometry_cad")
            theme("light")
            win.show_page("settings")
            shot("34_settings_auto")
            theme("dark")
            shot("35_dark_settings_auto")
            theme("light")
            sp.velocity_auto.setChecked(False)
            sp.vel_n[0].setValue(30)
            sp.advanced_toggle.setChecked(True)
            # 折りたたみの下の入力欄を画面内に入れる。
            from PySide6.QtWidgets import QScrollArea
            scroll = sp.findChild(QScrollArea)
            pump()
            scroll.ensureWidgetVisible(sp.manual)
            shot("36_settings_advanced_manual")
            theme("dark")
            shot("37_dark_settings_advanced_manual")
            sp.inputs["estimated_cells"].setValue(100_000_000)
            shot("38_settings_vram_warning")
            theme("light")
            sp.inputs["estimated_cells"].setValue(100000)
            sp.accept_btn.click()
            bp, mp = win.boundaries_page, win.mesh_page

            def assign(name, kind, direction, **values):
                bp.new_patch()
                bp.name.setText(name)
                bp.kind.setCurrentIndex(bp.kind.findData(kind))
                bp.faces.clearSelection()
                import numpy as np
                for f in win.project.geometry["faces"]:
                    if f["type"] == "plane" and np.allclose(f["normal"], direction):
                        bp.pick_face(f["id"], add=True)
                for key, value in values.items():
                    bp.inputs[key].setText(str(value))
                if not bp.apply_patch():
                    raise RuntimeError(bp.note.text())

            assign("inlet", "pressure_inlet", [0, 0, -1], temperature=420, pressure=200)
            assign("outlet", "vacuum", [0, 0, 1])
            bp.patches.setCurrentIndex(bp.patches.findData("inlet"))
            shot("44_boundaries")
            theme("dark")
            shot("45_dark_boundaries")
            theme("light")
            bp.new_patch()
            bp.name.setText("hole_wall")
            bp.kind.setCurrentIndex(bp.kind.findData("maxwell_wall"))
            bp.faces.clearSelection()
            for f in win.project.geometry["faces"]:
                if f["type"] == "cylinder":
                    bp.pick_face(f["id"], add=True)
            bp.inputs["accommodation"].setText("1.2")
            shot("46_boundary_validation")
            theme("dark")
            shot("47_dark_boundary_validation")
            theme("light")
            bp.inputs["accommodation"].setText("0.7")
            bp.apply_patch()
            bp.accept_btn.click()
            mp.patch_inputs["hole_wall"].setText(str(mp.size_max.value() / 2))

            def mesh():
                if not mp.generate():
                    raise RuntimeError(mp.note.text())
                end = time.monotonic() + 90
                while mp.busy and time.monotonic() < end:
                    pump(0.03)
                if mp.busy or not mp.result:
                    raise RuntimeError(mp.note.text())
                pump()

            mesh()
            shot("48_mesh")
            theme("dark")
            shot("49_dark_mesh")
            theme("light")
            sp.inputs["free_vram_gib"].setText("0.01")
            sp.accept_btn.click()
            bp.accept_btn.click()
            mesh()
            shot("50_mesh_vram_warning")
            win.show_page("geometry")
            gp.flow.setCurrentIndex(1)
            gp.load_cad(folder / "cad/orifice.step")
            wait_idle()
            gp.view.set_view("xz")
            shot("40_geometry_external")
            theme("dark")
            shot("41_dark_geometry_external")
            theme("light")
            win.show_page("ai")
            shot("42_geometry_ai_tab")
            theme("dark")
            shot("43_dark_geometry_ai_tab")
            win.save()
        finally:
            win.project.dirty = False
            win.close()
            app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
