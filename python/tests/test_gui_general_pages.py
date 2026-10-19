"""G1 の 6 段階、プロジェクト、形状、設定を offscreen で通す。"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def wait(app, condition, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(tmp_path, monkeypatch):
    from gsis_tools.gui.app import create_app
    from gsis_tools.gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv("GSIS_GUI_SETTINGS", str(tmp_path / "settings.ini"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    app, Theme, Context = create_app()
    ctx = Context(Theme(app, mode="light"))
    ctx.theme.apply()
    win = MainWindow(ctx, view_backend="none")
    ctx.theme.apply()
    win.show()
    app.processEvents()
    yield app, win
    win.project.dirty = False
    win.close()
    app.processEvents()


def make_box(path):
    from gsis_tools.mcp.engine import ModelEngine
    from gsis_tools.mcp.workspace import Workspace
    e = ModelEngine(Workspace(path.parent))
    try:
        e.call("model_new", {"unit": "mm"})
        e.call("add_box", dict(corner=[0, 0, 0], size=[2, 3, 4]))
        e.call("export", dict(path=path.name))
    finally:
        e.close()


def test_workflow_cad_settings_save_reopen(window, tmp_path):
    from PySide6.QtCore import Qt
    from gsis_tools.gui.project import load_project
    app, win = window
    assert win.nav.count() == win.stack.count() == 6
    assert not win.show_page("settings")
    assert "形状" in win.statusBar().currentMessage()
    assert not win.nav.item(1).flags() & Qt.ItemFlag.ItemIsEnabled
    assert "形状" in win.nav.item(1).toolTip()
    assert win.new_project(tmp_path / "project", "箱の流れ")
    cad = tmp_path / "box.step"
    make_box(cad)
    gp = win.geometry_page
    assert gp.load_cad(cad)
    assert wait(app, lambda: not gp.busy)
    assert win.project.stages["geometry"] == "done", gp.note.text()
    assert gp.faces.rowCount() == 6 and gp.view.scene().items
    assert win.project.settings.L_ref == pytest.approx(0.002, abs=1e-8)
    assert win.show_page("settings")
    sp = win.settings_page
    sp.gas.setCurrentText("Ar")
    assert sp.collect().gas == "Ar"
    sp.inputs["T_min"].setText("0")
    assert not sp.accept_btn.isEnabled()
    assert not win.save()
    sp.inputs["T_min"].setText("273.15")
    sp.accept_btn.click()
    assert win.project.stages["settings"] == "done"
    assert win.stack.currentWidget() is win.boundaries_page
    assert not win.show_page("mesh")
    assert win.save()
    assert win.save(tmp_path / "copy")
    assert load_project(tmp_path / "copy").settings.gas == "Ar"
    assert win.open_project(tmp_path / "project")
    assert win.geometry_page.faces.rowCount() == 6
    assert win.project.stages["settings"] == "done"
    # 形状を変えると設定以降が無効になる。新しい入力も保存する。
    gp.flow.setCurrentIndex(1)
    assert all(win.project.stages[k] == "redo" for k in ("geometry", "settings"))
    assert all(win.project.stages[k] == "pending" for k in ("boundaries", "mesh", "run", "results"))
    assert not win.show_page("settings")
    assert win.save()
    assert win.open_project(tmp_path / "project")
    assert gp.flow.currentData() == "external"
    assert win.project.stages["geometry"] == "redo"
    assert gp.load_cad(cad)
    assert wait(app, lambda: not gp.busy)
    assert len(win.project.geometry["faces"]) == 12
    assert win.project.stages["geometry"] == "done"
    win.ctx.theme.set_mode("dark")
    assert win.ctx.theme.scheme() == "dark"
    win.ctx.theme.set_mode("light")
    win._fill_recent()
    assert len(win.recent_menu.actions()) == 2


def test_settings_manual_grid_and_boundary_update(window):
    from gsis_tools.gui.general_settings import derived
    app, win = window
    sp = win.settings_page
    sp.velocity_auto.setChecked(False)
    sp.vel_n[0].setValue(30)
    sp.vel_lo[0].setText("10")
    sp.vel_hi[0].setText("-10")
    assert not sp.accept_btn.isEnabled()
    sp.vel_lo[0].setText("-10")
    sp.vel_hi[0].setText("10")
    assert sp.collect().vel_n[0] == 30
    sp.advanced_toggle.setChecked(True)
    sp.inputs["estimated_cells"].setValue(100000000)
    assert "VRAM" in sp.note.text() and "減らして" in sp.note.text()
    sp.mode.setCurrentIndex(sp.mode.findData("nsf"))
    assert derived(sp.collect())["memory"]["distribution"] == 0
    sp.gas.setCurrentText("任意")
    sp.inputs["molecular_mass"].setText("2e-26")
    assert sp.collect().molecular_mass == 2e-26
    s = sp.collect()
    s.update_boundary_defaults([dict(temperature=600, pressure=2000, velocity=[100, 0, 0])])
    sp.load_settings(s)
    assert sp.inputs["T0"].value() == 600


def test_cancel_preserves_project_and_unsaved_save(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app, win = window
    win.name_field.setText("変更中")
    original = win.project
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not win.new_project(tmp_path / "cancelled")
    assert win.project is original and not (tmp_path / "cancelled").exists()
    assert win.save(tmp_path / "saved")
    assert win.project.directory == (tmp_path / "saved").resolve()
    assert not win.project.dirty
