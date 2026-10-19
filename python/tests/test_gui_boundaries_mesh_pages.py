"""G2 の offscreen 画面操作と生成・保存・停止・VRAM ゲート。"""

import subprocess
from pathlib import Path

import pytest

from test_gui_general_pages import window, wait
from test_gui_boundaries_mesh import make_project


def prepare(window, tmp_path, shape="box"):
    app, win = window
    p, _ = make_project(tmp_path / "source", shape)
    assert win.new_project(tmp_path / "gui_project")
    win.geometry_page.unit.setCurrentText("mm")
    assert win.geometry_page.load_cad(p.asset(p.geometry["source"]))
    assert wait(app, lambda: not win.geometry_page.busy)
    assert win.project.stages["geometry"] == "done", win.geometry_page.note.text()
    win.settings_page.accept_btn.click()
    assert win.project.stages["settings"] == "done"
    return app, win


def put_patch(win, name, kind, ids, **values):
    bp = win.boundaries_page
    bp.new_patch()
    bp.name.setText(name)
    bp.kind.setCurrentIndex(bp.kind.findData(kind))
    bp.faces.clearSelection()
    for tag in ids:
        bp.pick_face(tag, add=True)
    for key, value in values.items():
        bp.inputs[key].setText(str(value))
    assert bp.apply_patch(), bp.note.text()


@pytest.mark.parametrize("shape", ["box", "orifice"])
def test_gui_to_solver_and_reopen(window, tmp_path, shape):
    app, win = prepare(window, tmp_path, shape)
    bp = win.boundaries_page
    assert all(win.project.stages[k] == "pending" for k in ("boundaries", "mesh", "run", "results"))
    face = next(f for f in win.project.geometry["faces"] if f["type"] == "plane")
    put_patch(win, "inlet", "pressure_inlet", [face["id"]], temperature=400, pressure=200)
    assert win.project.settings.T0 == 400
    assert win.project.settings.T_min == 273.15
    assert win.settings_page.inputs["p_ref"].value() == 200
    # 予算などの設定を確定し直しても、基準温度の自動更新で既定壁が加熱されない。
    win.settings_page.accept_btn.click()
    default = next(b for b in win.project.boundaries if b.get("automatic"))
    assert default["temperature"] == 273.15
    bp.accept_btn.click()
    assert win.project.stages["boundaries"] == "done"
    assert win.stack.currentWidget() is win.mesh_page
    mp = win.mesh_page
    mp.patch_inputs["inlet"].setText(str(mp.size_max.value() / 2))
    assert mp.generate()
    assert not win.show_page("geometry")
    assert not win.save()
    assert wait(app, lambda: not mp.busy, timeout=60), mp.note.text()
    assert win.project.stages["mesh"] == "done", mp.note.text()
    assert win.project.mesh["validated"]
    assert "生成と検査が済みました" in mp.note.text()
    assert win.project.settings.T_min == 273.15
    assert mp.view.scene().items
    assert mp.areas.rowCount() == 2
    assert "VRAM" in mp.memory.text()
    assert "セル数" in mp.quality.text()
    assert win.show_page("run")
    solver = Path(__file__).resolve().parents[2] / "build/release/src/gsis-solver.exe"
    cp = subprocess.run([str(solver), "check-case", str(win.project.asset("case.toml"))], capture_output=True, timeout=30)
    assert cp.returncode == 0, (cp.stdout + cp.stderr).decode("utf-8", errors="replace")
    assert win.save(tmp_path / "copy")
    assert win.open_project(tmp_path / "copy")
    assert win.project.stages["mesh"] == "done"
    assert win.mesh_page.result["cells"] == win.project.mesh["cells"]
    assert win.mesh_page.view.scene().items
    assert win.boundaries_page.faces.rowCount() == len(win.project.geometry["faces"])
    win.ctx.theme.set_mode("dark")
    assert win.mesh_page.view.scene().items
    # 元と同じ CAD を読み直しても境界は幾何の指紋で引き継がれる。
    before = win.project.boundaries
    source = win.project.asset(win.project.geometry["source"])
    assert win.geometry_page.load_cad(source)
    assert wait(app, lambda: not win.geometry_page.busy)
    assert win.project.boundaries[0]["patch"] == before[0]["patch"]
    assert "引き継げなかった" not in win.statusBar().currentMessage()
    assert win.project.stages["mesh"] == "redo"
    assert not win.project.mesh.get("validated")


def test_gui_kind_fields_validation_edit_delete(window, tmp_path):
    app, win = prepare(window, tmp_path)
    bp = win.boundaries_page
    bp.kind.setCurrentIndex(bp.kind.findData("maxwell_wall"))
    ids = [f["id"] for f in win.project.geometry["faces"][:2]]
    bp.pick_face(ids[0])
    bp.pick_face(ids[1], add=True)
    bp.inputs["accommodation"].setText("1.1")
    assert not bp.apply_btn.isEnabled()
    assert not bp.accept_btn.isEnabled()
    assert not win.save()
    bp.inputs["accommodation"].setText("0.5")
    bp.name.setText("moving_wall")
    bp.wall_motion.setChecked(True)
    for w, v in zip(bp.velocity, [3, 4, 0]):
        w.setText(str(v))
    assert bp.apply_patch()
    assert win.project.settings.u_max == 5
    assert len(win.project.boundaries[0]["faces"]) == 2
    bp.patches.setCurrentIndex(bp.patches.findData("moving_wall"))
    bp.inputs["temperature"].setText("600")
    assert bp.apply_patch()
    assert win.project.settings.T_max == 600
    bp.patches.setCurrentIndex(bp.patches.findData("moving_wall"))
    bp.delete_btn.click()
    assert len(win.project.boundaries) == 1
    assert win.project.boundaries[0]["automatic"]
    bp.kind.setCurrentIndex(bp.kind.findData("symmetry"))
    # 複数の軸の対称面はひとつのパッチにまとめない。
    bp.faces.selectAll()
    assert not bp.apply_btn.isEnabled()
    assert "軸が異なる" in bp.note.text()
    bp.kind.setCurrentIndex(bp.kind.findData("vacuum"))
    assert not bp.inputs["temperature"].isVisible()
    assert not bp.inputs["pressure"].isVisible()


def test_gui_vram_blocks_execution_and_can_stop(window, tmp_path):
    app, win = prepare(window, tmp_path)
    # 意図的に小さい予算で、実セル数での判定を確認する。
    win.settings_page.inputs["free_vram_gib"].setText("0.01")
    win.settings_page.accept_btn.click()
    win.boundaries_page.accept_btn.click()
    mp = win.mesh_page
    assert mp.generate()
    assert wait(app, lambda: not mp.busy, timeout=60)
    assert mp.result and not win.project.mesh.get("validated")
    assert win.project.stages["mesh"] == "pending"
    assert not win.show_page("run")
    assert "予算超過" in mp.memory.text()
    original = win.project.asset(win.project.mesh["path"]).read_bytes()
    assert mp.generate()
    mp.stop()
    assert wait(app, lambda: not mp.busy)
    assert "停止" in mp.note.text()
    assert win.project.asset(win.project.mesh["path"]).read_bytes() == original


def test_geometry_reload_reports_lost_faces(window, tmp_path):
    app, win = prepare(window, tmp_path)
    ids = [f["id"] for f in win.project.geometry["faces"]]
    put_patch(win, "wall", "diffuse_wall", ids, temperature=500)
    # 同じ番号でも、外部流の内側の面は向きが逆なので引き継がない。
    win.geometry_page.flow.setCurrentIndex(win.geometry_page.flow.findData("external"))
    assert win.geometry_page.load_cad(win.project.asset(win.project.geometry["source"]))
    assert wait(app, lambda: not win.geometry_page.busy)
    assert "引き継げなかった面" in win.geometry_page.note.text()
    assert len(win.project.boundaries) == 2
    assert not win.boundaries_page.accept_btn.isEnabled()  # 空になった明示パッチを再割り当てする。


def test_unsaved_project_stays_dirty_after_mesh(window, tmp_path):
    app, win = window
    p, _ = make_project(tmp_path / "source")
    assert not win._saved
    assert win.geometry_page.load_cad(p.asset(p.geometry["source"]))
    assert wait(app, lambda: not win.geometry_page.busy)
    win.settings_page.accept_btn.click()
    win.boundaries_page.accept_btn.click()
    assert win.mesh_page.generate()
    assert wait(app, lambda: not win.mesh_page.busy, timeout=60)
    assert win.project.stages["mesh"] == "done", win.mesh_page.note.text()
    assert not win._saved and win.project.dirty
    assert win.project.asset("case.toml").is_file()
    assert not win.ctx.recent("projects")
