"""GUI の画面(PySide6、QT_QPA_PLATFORM=offscreen)。

画面を組み立て、ケース設定の読み書き、メッシュ生成、ソルバーの短い実行(smoke ケース)、結果の読み込みを通す。
3D 表示はオフスクリーンの静止画の方式(GSIS_GUI_3D=static)で描く。
"""

from __future__ import annotations

import json
import math
import os
import time  # noqa: F401  (待ちの間の時刻)
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GSIS_GUI_3D"] = "static"

pytest.importorskip("PySide6")
pytest.importorskip("pyvista")
pytest.importorskip("tomlkit")

from gsis_tools.gui import casefile  # noqa: E402
from gsis_tools.gui.paths import default_solver, repo_root  # noqa: E402

ROOT = repo_root()
HAS_SOLVER = default_solver().is_file()


def wait_until(app, cond, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(cond())


@pytest.fixture(scope="module")
def gui(tmp_path_factory):
    """QApplication・テーマ・メインウィンドウ(このモジュールで 1 つ)。"""
    os.environ["GSIS_GUI_SETTINGS"] = str(tmp_path_factory.mktemp("settings") / "gsis-gui.ini")
    from PySide6.QtWidgets import QMessageBox

    from gsis_tools.gui.app import create_app
    from gsis_tools.gui.main_window import MainWindow

    app, ThemeManager, AppContext = create_app()
    tm = ThemeManager(app, mode="light")
    ctx = AppContext(tm)
    tm.apply()
    win = MainWindow(ctx)
    tm.apply()
    win.resize(1500, 950)
    win.show()
    app.processEvents()
    # テスト中に確認の窓で止まらないようにする
    saved = (QMessageBox.question, QMessageBox.warning, QMessageBox.information)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    yield app, ctx, win
    win.project.dirty = False
    win.close()  # 確認の窓は差し替えたまま閉じる(offscreen で窓が出ると止まる)
    app.processEvents()
    QMessageBox.question, QMessageBox.warning, QMessageBox.information = saved


def test_theme_tokens_in_stylesheet(gui):
    from gsis_tools.gui import theme
    from gsis_tools.gui.qtheme import build_palette, build_stylesheet

    app, ctx, win = gui
    light = build_stylesheet(theme.LIGHT, "light")
    dark = build_stylesheet(theme.DARK, "dark")
    assert "#FAF9F5" in light and "#B4532F" in light and "#262624" not in light
    assert "#262624" in dark and "#D97757" in dark
    pal = build_palette(theme.DARK)
    from PySide6.QtGui import QPalette

    assert pal.color(QPalette.ColorRole.Window).name().upper() == "#262624"
    # モードの切り替えと保存
    ctx.theme.set_mode("dark")
    assert ctx.theme.scheme() == "dark" and "#262624" in app.styleSheet()
    import matplotlib as mpl

    assert mpl.rcParams["axes.facecolor"].upper() == "#262624"
    ctx.theme.set_mode("light")
    assert "#FAF9F5" in app.styleSheet()
    text = Path(os.environ["GSIS_GUI_SETTINGS"]).read_text(encoding="utf-8")
    assert "mode=light" in text










def test_run_page_command_and_missing_subcommand(gui, tmp_path):
    app, ctx, win = gui
    rp = win.run_page
    rp.set_case(ROOT / "cases/smoke/smoke.toml")
    app.processEvents()
    assert rp.case_model is not None and rp.case_model.name == "smoke_box"
    rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("cis-run"))
    rp.out_field.setText(str(tmp_path / "out"))
    rp.maxit_check.setChecked(True)
    rp.maxit_spin.setValue(4)
    cmd, skipped = rp.command()
    assert cmd[1:3] == ["cis-run", str(ROOT / "cases/smoke/smoke.toml")]
    assert "--out" in cmd and "--max-iter" in cmd and skipped == []
    if HAS_SOLVER and "run" not in ctx.subcommands():
        rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("run"))
        app.processEvents()
        assert "run サブコマンドがありません" in rp.sub_note.text()
        assert "このソルバーにはない" in rp.sub_combo.currentText()
        assert rp.start() is False  # 起動しない(窓で知らせる)
        assert rp.proc is None


@pytest.mark.skipif(not HAS_SOLVER, reason="gsis-solver がビルドされていない")
def test_run_page_runs_pbc_smoke(gui, tmp_path):
    """pbc-run(CPU)を smoke ケースで数反復だけ実行し、ログと history.csv を追う。"""
    app, ctx, win = gui
    rp = win.run_page
    rp.set_case(ROOT / "cases/smoke/smoke.toml")
    rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("pbc-run"))
    out = tmp_path / "pbc"
    rp.out_field.setText(str(out))
    rp.maxit_check.setChecked(True)
    rp.maxit_spin.setValue(5)
    assert rp.start()
    assert wait_until(app, lambda: rp.proc is None, 180)
    assert "終了コード 0" in rp.state.text()
    assert (out / "history.csv").is_file() and (out / "summary.json").is_file()
    h = rp.tail.history()
    assert 1 <= h.n <= 5 and h.kind == "macro"
    assert "PBC マクロソルバー" in rp.log.text_all()
    assert rp.lab_iter.text() == str(h.n)
    assert "inlet" in rp.lab_mdot.text()
    assert rp.results_btn.isEnabled()


@pytest.mark.skipif(not HAS_SOLVER, reason="gsis-solver がビルドされていない")
def test_run_page_stop(gui, tmp_path):
    """長い実行(smoke ケースの cis-run、CPU)を始め、履歴が出始めたら「停止」で止める。"""
    app, ctx, win = gui
    rp = win.run_page
    rp.set_case(ROOT / "cases/smoke/smoke.toml")
    rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("cis-run"))
    rp.out_field.setText(str(tmp_path / "stop"))
    rp.maxit_check.setChecked(True)
    rp.maxit_spin.setValue(100000)
    rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
    assert rp.start()
    assert wait_until(app, lambda: rp.tail is not None and rp.tail.n_rows >= 3, 120)
    assert rp.stop_btn.isEnabled() and not rp.run_btn.isEnabled()
    rp.stop()
    assert wait_until(app, lambda: rp.proc is None, 60)
    assert "停止しました" in rp.state.text()
    assert rp.run_btn.isEnabled() and not rp.stop_btn.isEnabled()
    rp.backend_combo.setCurrentIndex(0)






def _synthetic_run(tmp_path: Path) -> Path:
    """O 型断面の円柱(半径 1 mm、長さ 22 mm)に解析的な場を入れた出力フォルダ。"""
    from gsis_tools.geometry.tube import ogrid_cylinder
    from gsis_tools.vtu import write_vtu

    L = 1e-3
    m = casefile.tube_preset(10.0)
    d = casefile.derive(m)
    mesh = ogrid_cylinder(radius=L, length=22 * L, n_theta=6, nx=44)
    c = mesh.geometry().cell_centres
    X = c[:, 0] / L - 8.0
    r2 = (c[:, 1] ** 2 + c[:, 2] ** 2) / L**2
    p = d.p_ref * (1.0 - 0.04 * X - 0.1 * r2)
    ux = math.sqrt(2.0) * d.v_ref * (0.2 + 0.01 * X + 0.2 * r2)
    T = d.T0 * np.ones(mesh.n_cells)
    u = np.stack([ux, np.zeros_like(ux), np.zeros_like(ux)], axis=1)
    run = tmp_path / "synthetic_run"
    run.mkdir()
    write_vtu(run / "fields.vtu", mesh, {"rho": p / (d.R * T), "u": u, "T": T, "p": p,
                                        "Ma": np.abs(ux) / np.sqrt(5 / 3 * d.R * T)}, include_volume=False)
    summary = {
        "case": "synthetic_d10", "iterations": 12, "converged": True, "delta": 10.0, "tolerance": 1e-5,
        "reference": {"length_m": L, "temperature_K": d.T0, "pressure_Pa": d.p_ref, "density_kg_m3": d.rho_ref,
                      "velocity_m_s": d.v_ref, "heat_flux_W_m2": d.heat_flux_ref, "mass_flux_kg_m2_s": d.mass_flux_ref},
        "patches": [{"name": "xmin", "type": "pressure_inlet"}, {"name": "xmax", "type": "vacuum"},
                    {"name": "wall", "type": "diffuse_wall"}],
        "mass_flow": {"scale_kg_s": d.mass_flow_scale, "M_dot_inlet": 0.329, "M_dot_outlet": 0.331, "M_dot": 0.330},
    }
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run / "gui_run.json").write_text(json.dumps({"example": "tube_vacuum"}), encoding="utf-8")
    hdr = "iteration,epsilon,eps_rho,eps_u,eps_T,residual,residual_rel,mdot_xmin_nd,mdot_xmax_nd,mdot_wall_nd,time_iter_s,time_total_s"
    rows = [f"{i},{10.0 ** -i},0,0,0,0,0,{-0.41},{0.415},0,0.1,{0.1 * i}" for i in range(1, 13)]
    (run / "history.csv").write_text(hdr + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return run


def test_results_page_synthetic_run(gui, tmp_path):
    app, ctx, win = gui
    rs = win.results_page
    run = _synthetic_run(tmp_path)
    assert rs.load(run)
    assert wait_until(app, lambda: not rs._busy, 120)
    assert wait_until(app, lambda: not rs.runner.busy(), 120)
    b = rs.bundle
    assert b is not None and b.run.name == "synthetic_d10" and not b.errors, rs.info.text()
    v = rs.view3d
    names = [v.field_combo.itemData(i).name for i in range(v.field_combo.count())]
    assert names[:2] == ["p", "rho"] and "u" in names
    for key in ("3d", "slice", "contour", "surface"):  # 4 つの表示がどれも描ける(計算は別スレッド)
        v.mode_combo.setCurrentIndex(v.mode_combo.findData(key))
        assert wait_until(app, lambda: not rs.runner.busy(), 60)
        assert v.view.scene() is not None and v.view.scene().items, key
    v.mode_combo.setCurrentIndex(v.mode_combo.findData("contour"))
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    assert len(v.view.scene().items) == 2  # 断面と等値線
    assert "p / p_in" in v.field_stats.text()
    # 中心線: 軸上の解析解 p/p_in = 1 − 0.04 X、u_x/√(2RT0) = 0.2 + 0.01 X
    cl = b.centerline
    assert cl is not None and cl.method == "layers"
    assert np.max(np.abs(cl.values["p"] - (1.0 - 0.04 * cl.X))) < 1e-10
    assert np.max(np.abs(cl.values["ux"] - (0.2 + 0.01 * cl.X))) < 1e-10
    assert rs.cl_table.rowCount() >= 4  # δ = 10 の GSIS-PBC・CIS と p・u
    # Table 1
    rows = rs.table1_rows()
    mine = [r for r in rows if r["run"] is not None]
    assert len(mine) == 1 and mine[0]["paper"].delta_rp == 10.0
    t1 = {rs.t1_table.item(r, 5).text(): rs.t1_table.item(r, 8).text() for r in range(rs.t1_table.rowCount())}
    assert t1["synthetic_d10"] == "+1.23%"
    # 書き出し
    files = rs.export_centerline(tmp_path / "cl.csv")
    assert all(f.is_file() for f in files) and len(files) == 2
    head = (tmp_path / "cl.csv").read_text(encoding="utf-8").splitlines()[0]
    assert head == "X,p_over_pin,ux_over_sqrt2RT0,T_over_T0,p_paper_gsis,ux_paper_gsis"
    rs.export_table1(tmp_path / "t1.csv")
    assert "synthetic_d10" in (tmp_path / "t1.csv").read_text(encoding="utf-8")
    rs.cl_plot.save(tmp_path / "cl.png")
    assert (tmp_path / "cl.png").stat().st_size > 1000
    # 履歴のグラフ
    assert rs.history_plot.hist is not None and rs.history_plot.hist.n == 12
    assert "流入と流出の差" in rs.history_plot.info.text()
    # 比べる計算に加えて外す
    rs.pin_current()
    assert len(rs.pinned) == 1
    rs.t1_table.selectRow(next(r for r in range(rs.t1_table.rowCount()) if rs.t1_table.item(r, 5).text()))
    rs._unpin_selected()
    assert rs.pinned == []


def test_results_3d_plane_streamlines_and_profile(gui, tmp_path):
    """任意の断面(プリセット・自由な法線・スライダー)、流線(線分・断面上の 2 次元)、線上の分布と CSV。"""
    app, ctx, win = gui
    rs = win.results_page
    assert rs.load(_synthetic_run(tmp_path))
    assert wait_until(app, lambda: not rs._busy and not rs.runner.busy(), 120)
    v = rs.view3d
    L = rs.bundle.length
    # 断面: x 一定(X = 5)
    v.plane_preset.setCurrentIndex(v.plane_preset.findData("x"))
    v.mode_combo.setCurrentIndex(v.mode_combo.findData("slice"))
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    sl = v._last.slice
    assert sl is not None and np.allclose(np.asarray(sl.points)[:, 0], 13.0 * L, atol=1e-12)
    assert v.view.scene().plane_normal == (1.0, 0.0, 0.0) and v.view.scene().parallel
    # 自由な法線(入力欄)→ プリセットは「自由」
    v.normal_field.setValue((1.0, 1.0, 0.0))
    v._on_plane_edited()
    assert v.plane_preset.currentData() == "free"
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    n = np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0)
    pts = np.asarray(v._last.slice.points)
    assert np.allclose((pts - np.array([13.0, 0.0, 0.0]) * L) @ n, 0.0, atol=1e-12)
    # スライダーで法線の向きに動かす(放したときに作り直す)
    lo, hi = v._slider_range
    v.offset_slider.setValue(int(round(-1000 + 2000 * (2.0 - lo) / (hi - lo))))
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    o = np.asarray(v.plane().origin)
    assert abs(float((o - np.array([13.0, 0.0, 0.0])) @ n) - 2.0) < 0.02
    # 流線: 線分の種(軸方向の流れなので、流線は軸に平行)
    v.plane_preset.setCurrentIndex(v.plane_preset.findData("z0"))
    v.seed_combo.setCurrentIndex(v.seed_combo.findData("line"))
    v.seed_a.setValue((3.0, -0.5, 0.0))
    v.seed_b.setValue((3.0, 0.5, 0.0))
    v.seed_n.setValue(5)
    assert v.apply_stream() and v.stream_check.isChecked()
    assert wait_until(app, lambda: not rs.runner.busy(), 120)
    st = v._last.streams
    assert st is not None and st.n_cells >= 5
    assert np.allclose(np.asarray(st.points)[:, 2], 0.0, atol=1e-9)
    assert any(it.scalars == "__speed_show__" for it in v.view.scene().items)
    assert "流線" in v.stream_info.text()
    # 断面上の 2 次元の流線(z = 0 の断面の上)
    v.seed_combo.setCurrentIndex(v.seed_combo.findData("plane2d"))
    v.seed_n.setValue(30)
    assert v.apply_stream()
    assert wait_until(app, lambda: not rs.runner.busy(), 120)
    st = v._last.streams
    assert st is not None and np.allclose(np.asarray(st.points)[:, 2], 0.0, atol=1e-9)
    assert v._last.stream_label == "|u_t| / √(2RT0)"
    v.stream_check.setChecked(False)
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    assert v._last.streams is None
    # 平面ウィジェットは静止画の方式では使えない(知らせて外す)
    v.widget_check.setChecked(True)
    assert not v.widget_check.isChecked() and "対話型" in v.state.text()
    # 線上の分布: 管の直径(X = 5)。p/p_in = 1 − 0.04 X − 0.1 r²(セル値を点へ補間した値)
    lp = rs.profile
    lp.preset.setCurrentIndex(lp.preset.findData("diam_mid"))
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    prof = lp.profile
    assert prof is not None and prof.n_valid >= prof.s.size - 2  # 両端は多角形の境界の上(外と判定されうる)
    r = prof.points[:, 1]
    p = prof.values["p"] / rs.bundle.run.scales.pressure
    inner = (np.abs(r) < 0.8) & prof.valid
    assert np.max(np.abs(p[inner] - (1.0 - 0.04 * 5.0 - 0.1 * r[inner] ** 2))) < 0.01
    assert len(lp.plot.figure.axes) == len(lp.selected_keys()) >= 2
    out = lp.export_csv(tmp_path / "line.csv")
    head = out.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("s,x,y,z,p/p_in,rho/rho_in,u_x/sqrt(2RT0)")
    # 3D 表示に線を出す
    lp.show3d_check.setChecked(True)
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    assert v._last.line is not None
    lp.show3d_check.setChecked(False)
    assert wait_until(app, lambda: not rs.runner.busy(), 60)


RUN = ROOT / "runs/kinetic/tube_delta1_cis"


@pytest.mark.skipif(not (RUN / "fields.vtu").is_file(), reason="既存の出力がない")
def test_results_streamlines_do_not_freeze(gui):
    """13.5 万セルの管の結果で流線を計算している間も、画面のタイマーが止まらない(操作が固まらない)。"""
    from PySide6.QtCore import QTimer

    app, ctx, win = gui
    rs = win.results_page
    assert rs.load(RUN)
    assert wait_until(app, lambda: not rs._busy and not rs.runner.busy(), 300)
    v = rs.view3d
    v.mode_combo.setCurrentIndex(v.mode_combo.findData("none"))
    v.seed_combo.setCurrentIndex(v.seed_combo.findData("disk"))
    v.seed_center.setValue((7.0, 0.0, 0.0))
    v.seed_n.setValue(60)
    assert wait_until(app, lambda: not rs.runner.busy(), 60)
    ticks: list[float] = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    t0 = time.perf_counter()
    assert v.apply_stream()
    while rs.runner.busy() and time.perf_counter() - t0 < 300:
        app.processEvents()
        time.sleep(0.005)
    timer.stop()
    st = v._last.streams
    assert st is not None and st.n_cells >= 60
    gaps = np.diff(ticks)
    assert gaps.size > 5 and float(gaps.max()) < 0.5, f"最大の間隔 {gaps.max():.3f} 秒"
    # 流線は上流槽(x < 8)から管を通って下流槽(x > 18)まで
    xs = np.asarray(st.points)[:, 0] / rs.bundle.length
    assert xs.min() < 2.0 and xs.max() > 20.0


@pytest.mark.skipif(not (RUN / "fields.vtu").is_file(), reason="既存の出力がない")
def test_results_page_real_run(gui):
    app, ctx, win = gui
    rs = win.results_page
    assert rs.load(RUN)
    assert wait_until(app, lambda: not rs._busy, 180)
    b = rs.bundle
    assert b.run.name == "tube_delta1_cis" and b.centerline is not None and len(b.centerline) == 82
    t1 = {rs.t1_table.item(r, 5).text(): rs.t1_table.item(r, 8).text() for r in range(rs.t1_table.rowCount())}
    assert t1["tube_delta1_cis"] == "+0.72%"
    who = [rs.cl_table.item(r, 1).text() for r in range(rs.cl_table.rowCount())]
    assert who.count("論文 GSIS-PBC(δ=1)") == 2 and who.count("論文 CIS(δ=1)") == 2
    # 線上の分布は読み込んだときに今のプリセットで作る
    assert wait_until(app, lambda: not rs.runner.busy(), 120)
    prof = rs.profile.profile
    assert prof is not None and prof.n_valid == prof.s.size
    # 当てはめの半径を変えると、中心線を別スレッドで作り直す
    rs.rmax_field.setText("0.25")
    rs._recompute_centerline()
    assert wait_until(app, lambda: not rs.runner.busy(), 120)
    assert rs.bundle.centerline.r_max == pytest.approx(0.25)


def test_vtk_view_static_render(gui):
    import pyvista as pv

    from gsis_tools.gui.widgets.vtk_view import Scene, SceneItem, VtkView

    app, ctx, win = gui
    v = VtkView(backend="static")
    v.resize(400, 300)
    v.show()
    sphere = pv.Sphere()
    sphere.cell_data["s"] = np.arange(sphere.n_cells, dtype=float)
    v.set_scene(Scene(items=[SceneItem(sphere, scalars="s", scalar_bar_title="s")], legend=[("球", "#C96442")]))
    img = v.screenshot(size=(320, 240))
    assert img.shape == (240, 320, 3) and img.std() > 5
    v.restyle("dark")
    v.close()


def test_vtk_safe_text_and_profile_key_carry_over():
    from gsis_tools.gui.pages.line_profile import _canon
    from gsis_tools.gui.widgets.vtk_view import vtk_safe_text

    # VTK の字体で描けない「|」・ρ・σ・[ ] を置き換える
    assert vtk_safe_text("|u| / √(2RT0)") == "u_mag / √(2RT0)"
    assert vtk_safe_text("|u_t| / √(2RT0)") == "u_t_mag / √(2RT0)"
    assert vtk_safe_text("ρ / ρ_in") == "rho / rho_in"
    assert vtk_safe_text("σ_xx [Pa]") == "sigma_xx (Pa)"
    assert vtk_safe_text("u_x / √(2RT0)") == "u_x / √(2RT0)"
    # 線上の分布の選択は、cis-run の u と pbc-run の U を同じものとして引き継ぐ
    assert _canon("U_x") == "u_x" and _canon("|U|") == "|u|" and _canon("u_y") == "u_y"
    assert _canon("p") == "p" and _canon("T") == "T"
