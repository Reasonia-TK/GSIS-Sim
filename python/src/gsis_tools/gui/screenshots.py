"""画面の確認用のスクリーンショットを作る(docs/gui_screenshots/)。

    uv run --project python --extra gui python -m gsis_tools.gui.screenshots [--out docs/gui_screenshots]
                                                                            [--interactive] [--size 1600x980]

- 既定は Qt の offscreen プラットフォームで、画面を出さずに撮る(3D は静止画の方式)
- ``--interactive`` は通常の表示(Windows)で窓を出して撮る(3D は pyvistaqt の QtInteractor)
- データは ``runs/`` の既存の出力を読むだけ。短い実行(smoke ケース、CPU)の出力は一時フォルダに書く
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="gsis-gui のスクリーンショットを作る")
    ap.add_argument("--out", default=None, help="書き出すフォルダ(既定 docs/gui_screenshots)")
    ap.add_argument("--interactive", action="store_true", help="通常の表示で窓を出して撮る")
    ap.add_argument("--size", default="1600x980", help="窓の大きさ(既定 1600x980)")
    ap.add_argument("--only", default="", help="撮る画像の名前の一部(カンマ区切り。確認用)")
    args = ap.parse_args(argv)
    if not args.interactive:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["GSIS_GUI_3D"] = "static"
    tmp = Path(tempfile.mkdtemp(prefix="gsis_gui_shots_"))
    os.environ["GSIS_GUI_SETTINGS"] = str(tmp / "settings.ini")  # 利用者の設定を変えない
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    from .app import create_app

    app, ThemeManager, AppContext = create_app([sys.argv[0]])
    from PySide6.QtWidgets import QMessageBox

    from .main_window import MainWindow
    from .paths import repo_root

    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    root = repo_root()
    out = Path(args.out) if args.out else root / "docs" / "gui_screenshots"
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(v) for v in args.size.lower().split("x"))
    only = [s for s in args.only.split(",") if s]

    tm = ThemeManager(app, mode="light")
    ctx = AppContext(tm)
    tm.apply()
    win = MainWindow(ctx)
    tm.apply()
    win.resize(w, h)
    win.move(40, 40)
    win.show()

    def pump(sec: float = 0.3) -> None:
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.01)

    def wait(cond, timeout: float = 180.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.03)
        return False

    shots: list[str] = []

    def shot(name: str) -> None:
        if only and not any(s in name for s in only):
            return
        pump(0.6)
        path = out / f"{name}.png"
        win.grab().save(str(path))
        shots.append(path.name)
        print(f"書き出し: {path}")

    def set_theme(mode: str) -> None:
        win.mode_combo.setCurrentIndex(win.mode_combo.findData(mode))
        pump(0.5)

    run_kin1 = root / "runs/kinetic/tube_delta1_cis"
    run_kin10 = root / "runs/kinetic/tube_delta10_cis"
    run_mac100 = root / "runs/macro/tube_hex_d100_nsf"
    run_mac1000 = root / "runs/macro/tube_hex_d1000_nsf"

    # 1. ケース設定(δ_rp = 1 の CIS のケースと check-case)
    cp = win.case_page
    win.show_page("case")
    cp.load_file(root / "cases/tube_vacuum/kinetic/tube_delta1_cis.toml")
    if cp.run_check():
        wait(lambda: cp._proc is None, 120)
    shot("01_case")

    # 2. メッシュ(既存の六面体メッシュ 135,104 セル)
    mp = win.mesh_page
    win.show_page("mesh")
    mp.load_mesh(root / "cases/tube_vacuum/mesh/tube_hex.pmesh")
    wait(lambda: not mp._busy, 180)
    mp.view.set_view("focus")
    shot("02_mesh_section")
    mp.rb_boundary.setChecked(True)
    mp.view.set_view("iso")
    mp.tabs.setCurrentWidget(mp.patch_table)
    shot("03_mesh_patches")
    mp.rb_section.setChecked(True)
    mp.tabs.setCurrentWidget(mp.quality_table)

    # 3. 実行: 既存の出力(δ_rp = 1 の CIS、1,573 反復)を追う
    rp = win.run_page
    win.show_page("run")
    rp.set_case(root / "cases/tube_vacuum/kinetic/tube_delta1_cis.toml")
    rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("cis-run"))
    rp.out_field.setText(str(run_kin1))
    rp.attach_dir(run_kin1)
    rp.timer.stop()
    shot("04_run_monitor")

    # 4. 実行: smoke ケースを CPU で短く実行(本当にソルバーを起動する)
    if ctx.solver_ok():
        rp.set_case(root / "cases/smoke/smoke.toml")
        rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("cis-run"))
        rp.out_field.setText(str(tmp / "smoke_cis"))
        rp.maxit_check.setChecked(True)
        rp.maxit_spin.setValue(40)
        rp.backend_combo.setCurrentIndex(rp.backend_combo.findData("cpu"))
        rp.print_check.setChecked(True)
        rp.print_spin.setValue(5)
        if rp.start():
            wait(lambda: rp.proc is None, 300)
        shot("05_run_smoke_cpu")
        # フェーズ 4 の run がないときの表示
        if "run" not in ctx.subcommands():
            rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("run"))
            shot("06_run_missing_subcommand")
            rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("cis-run"))

    # 5. 結果: 3D・流線・任意断面・線上の分布・中心線・Table 1・履歴
    rs = win.results_page
    v = rs.view3d
    win.show_page("results")

    def idle(timeout: float = 300.0) -> None:
        wait(lambda: not rs._busy and not rs.runner.busy(), timeout)

    def field(name: str) -> None:
        v.field_combo.setCurrentIndex(next(k for k in range(v.field_combo.count())
                                           if v.field_combo.itemData(k).name == name))

    def mode(key: str) -> None:
        v.mode_combo.setCurrentIndex(v.mode_combo.findData(key))

    for run in (run_kin10, run_mac100, run_mac1000):  # 比べる計算(Table 1 用)
        if run.is_dir() and rs.load(run):
            idle()
            rs.pin_current()
    if rs.load(run_kin1):
        idle()
    rs.tabs.setCurrentWidget(v)
    v.ctl.setCurrentIndex(0)
    mode("contour")
    idle()
    v.view.set_view("focus")
    shot("07_results_3d_contour")
    mode("3d")
    field("u")
    idle()
    v.view.set_view("focus")
    shot("08_results_3d_velocity")
    # 流線(円盤の種、管の入口の手前)と 3D の断面
    field("p")
    v.ctl.setCurrentIndex(2)
    v.apply_stream()
    idle()
    v.view.set_view("focus")
    shot("17_results_streamlines")
    # 流線だけ(領域の輪郭を薄く)
    v.ctl.setCurrentIndex(0)
    mode("none")
    v.outline_check.setChecked(True)
    idle()
    v.view.set_view("focus")
    shot("18_results_streamlines_outline")
    v.outline_check.setChecked(False)
    v.stream_check.setChecked(False)
    # 任意断面: x 一定(X = 5)、u_x の等値線、色の範囲は断面の中
    v.ctl.setCurrentIndex(1)
    v.plane_preset.setCurrentIndex(v.plane_preset.findData("x"))
    v.ctl.setCurrentIndex(0)
    mode("contour")
    field("u")
    v.range_combo.setCurrentIndex(v.range_combo.findData("view"))
    idle()
    v.view.set_view("focus")
    v.ctl.setCurrentIndex(1)
    shot("19_results_slice_x")
    # 任意断面: 自由な法線(下流槽の噴流を斜めに切る)、u_x の等値線、断面に垂直に見る
    v.origin_field.setValue((19.0, 0.0, 0.0))
    v.normal_field.setValue((1.0, 0.0, 0.4))
    v._on_plane_edited()
    idle()
    v.view.set_view("plane")
    shot("20_results_slice_free")
    v.range_combo.setCurrentIndex(v.range_combo.findData("all"))
    # 断面上の 2 次元の流線(z = 0 の断面、面内の速さで色付け)
    v.plane_preset.setCurrentIndex(v.plane_preset.findData("z0"))
    v.ctl.setCurrentIndex(2)
    v.seed_combo.setCurrentIndex(v.seed_combo.findData("plane2d"))
    v.seed_n.setValue(200)
    v.apply_stream()
    v.ctl.setCurrentIndex(0)
    mode("slice")
    field("p")
    idle()
    v.view.set_view("focus")
    v.ctl.setCurrentIndex(2)
    shot("21_results_streamlines_2d")
    v.stream_check.setChecked(False)
    v.seed_combo.setCurrentIndex(v.seed_combo.findData("disk"))
    v.seed_n.setValue(60)
    v.ctl.setCurrentIndex(0)
    mode("contour")
    idle()
    # 線上の分布: 管の直径(X = 5)
    lp = rs.profile
    rs.tabs.setCurrentWidget(lp)
    lp.preset.setCurrentIndex(lp.preset.findData("diam_mid"))
    idle()
    shot("22_results_line_profile")
    lp.preset.setCurrentIndex(lp.preset.findData("axis_tube"))
    idle()
    rs.show_pinned.setChecked(False)
    rs.tabs.setCurrentIndex(rs.tabs.indexOf(lp) + 1)
    shot("09_results_centerline")
    rs.tabs.setCurrentIndex(rs.tabs.indexOf(lp) + 2)
    shot("10_results_table1")
    rs.tabs.setCurrentIndex(rs.tabs.indexOf(lp) + 3)
    shot("11_results_history")

    # 6. ダークモード
    set_theme("dark")
    rs.tabs.setCurrentIndex(rs.tabs.indexOf(lp) + 1)
    shot("12_dark_results_centerline")
    rs.tabs.setCurrentWidget(v)
    v.ctl.setCurrentIndex(1)
    v.plane_preset.setCurrentIndex(v.plane_preset.findData("z0"))
    v.ctl.setCurrentIndex(0)
    mode("contour")
    v.range_combo.setCurrentIndex(v.range_combo.findData("all"))
    idle()
    v.view.set_view("focus")
    shot("13_dark_results_3d")
    mode("3d")
    v.ctl.setCurrentIndex(2)
    v.apply_stream()
    idle()
    v.view.set_view("focus")
    shot("23_dark_results_streamlines")
    win.show_page("run")
    rp.set_case(root / "cases/tube_vacuum/kinetic/tube_delta1_cis.toml")
    rp.sub_combo.setCurrentIndex(rp.sub_combo.findData("cis-run"))
    rp.out_field.setText(str(run_kin1))
    rp.attach_dir(run_kin1)
    rp.timer.stop()
    shot("14_dark_run_monitor")
    win.show_page("case")
    shot("15_dark_case")
    win.show_page("mesh")
    shot("16_dark_mesh")
    set_theme("light")

    win.close()
    app.processEvents()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(shots)} 枚: {', '.join(shots)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
