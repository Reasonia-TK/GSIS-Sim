"""汎用アプリの 6 段階とプロジェクトの読み書き。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy,
                               QStackedWidget, QToolBar, QVBoxLayout, QWidget)

from .context import AppContext
from .geometry import prepare_geometry
from .boundaries import remap_boundaries, with_default
from .general_mesh import mesh_memory
from .examples import create_tube_project, mesh_geometry
from .pages.boundaries_page import BoundariesPage
from .pages.mesh_page import MeshPage
from .pages.geometry_page import GeometryPage
from .pages.results_page import ResultsPage
from .pages.run_page import RunPage
from .pages.settings_page import SettingsPage
from .paths import display_path, solver_exe_name
from .project import LABELS, STAGES, STATE_LABELS, Project, load_project, save_project
from .theme import MODE_LABELS, MODES
from .widgets.common import heading, muted

PAGES = tuple(zip(STAGES, LABELS))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, view_backend=None):
        super().__init__()
        self.ctx = ctx
        self._staging = tempfile.TemporaryDirectory(prefix="gsis_project_")
        self.project = Project(directory=Path(self._staging.name))
        self.ctx.project = self.project
        self._saved = False
        self._closed = False
        self.resize(1600, 980)
        self._build_menu()
        tb = QToolBar("プロジェクトと表示")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(heading("GSIS-PBC"))
        tb.addWidget(QLabel("  計算名 "))
        self.name_field = QLineEdit(self.project.name)
        self.name_field.setMaximumWidth(260)
        tb.addWidget(self.name_field)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.solver_label = muted("")
        tb.addWidget(self.solver_label)
        self.solver_btn = QPushButton("ソルバーを選ぶ…")
        tb.addWidget(self.solver_btn)
        tb.addWidget(QLabel("表示"))
        self.mode_combo = QComboBox()
        for m in MODES:
            self.mode_combo.addItem(MODE_LABELS[m], m)
        self.mode_combo.setCurrentIndex(MODES.index(ctx.theme.mode))
        tb.addWidget(self.mode_combo)
        central = QWidget()
        lay = QHBoxLayout(central)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(240)
        self.stack = QStackedWidget()
        self.geometry_page = GeometryPage(ctx, lambda: self.project.directory, view_backend=view_backend)
        self.ai_page = self.geometry_page.ai_page
        self.settings_page = SettingsPage(ctx)
        self.boundaries_page = BoundariesPage(ctx, view_backend=view_backend)
        self.mesh_page = MeshPage(ctx, view_backend=view_backend)
        self.run_page = RunPage(ctx)
        self.results_page = ResultsPage(ctx, view_backend=view_backend)
        self.pages = dict(zip(STAGES, (self.geometry_page, self.settings_page, self.boundaries_page,
                                      self.mesh_page, self.run_page, self.results_page)))
        for key, label in PAGES:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(220, 55))
            self.nav.addItem(item)
            self.stack.addWidget(self.pages[key])
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self._navigate)
        self.name_field.textChanged.connect(self._rename)
        self.geometry_page.changed.connect(lambda: self._change("geometry"))
        self.geometry_page.ready.connect(self._geometry_ready)
        self.settings_page.changed.connect(self._settings_changed)
        self.settings_page.accepted.connect(self._accept_settings)
        self.boundaries_page.changed.connect(self._boundaries_changed)
        self.boundaries_page.accepted.connect(self._accept_boundaries)
        self.mesh_page.changed.connect(lambda: self._change("mesh"))
        self.mesh_page.ready.connect(self._mesh_ready)
        self.mesh_page.busy_changed.connect(lambda busy: self.nav.setEnabled(not busy))
        self.run_page.editor_case = lambda: (self.project.directory / "case.toml" if self._saved else None,
                                             self.project.dirty)
        self.run_page.open_results.connect(self._open_results)
        self.run_page.run_finished.connect(self._run_finished)
        self.run_page.busy_changed.connect(self._run_busy)
        self.results_page.loaded.connect(self._results_loaded)
        self.ai_page.open_mesh.connect(lambda _path: self.show_status("採用した AI 形状の境界条件を確定し、メッシュ画面で生成してください"))
        for page in (self.geometry_page, self.settings_page, self.boundaries_page, self.mesh_page, self.run_page, self.results_page):
            page.status.connect(self.show_status)
        ctx.status.connect(self.show_status)
        self.solver_btn.clicked.connect(self._choose_solver)
        self.mode_combo.currentIndexChanged.connect(lambda _: ctx.theme.set_mode(self.mode_combo.currentData()))
        ctx.solver_changed.connect(self._update_solver_label)
        self._update_solver_label()
        self.results_page.set_project(self.project)
        self.refresh_workflow()
        self.nav.setCurrentRow(0)

    def _build_menu(self):
        menu = self.menuBar().addMenu("プロジェクト")
        commands = (("新規…", QKeySequence.StandardKey.New, self._new_dialog),
                    ("開く…", QKeySequence.StandardKey.Open, self._open_dialog),
                    ("保存", QKeySequence.StandardKey.Save, self._save_dialog),
                    ("名前を付けて保存…", QKeySequence.StandardKey.SaveAs, lambda: self._save_dialog(True)))
        for label, shortcut, fn in commands:
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(fn)
            menu.addAction(action)
        self.recent_menu = menu.addMenu("最近使ったプロジェクト")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        menu.addAction("例題を開く…", self._example_dialog)

    def open_example(self, directory, delta):
        if not self._can_replace():
            return False
        try:
            p = create_tube_project(self.ctx.root, Path(directory), delta)
            self._install_project(p, mesh_geometry(p))
            return True
        except (OSError, ValueError, KeyError) as exc:
            self.show_status(f"例題を開けません: {exc}")
            return False

    def _example_dialog(self):
        delta, ok = QInputDialog.getItem(self, "例題の管", "δ_rp", ["1", "10", "100", "1000"], 0, False)
        if ok:
            directory = QFileDialog.getExistingDirectory(self, "例題プロジェクトの保存先(空のフォルダ)")
            if directory:
                self.open_example(Path(directory), int(delta))

    def _run_busy(self, busy):
        for widget in (self.nav, self.name_field, self.settings_page, self.boundaries_page, self.geometry_page,
                       self.mesh_page, self.solver_btn):
            widget.setEnabled(not busy)
        if busy:
            self.project.change("run")
        self.refresh_workflow()

    def _run_finished(self, directory, code):
        if directory and (Path(directory) / "summary.json").is_file() and not self.project.blocked_reason("run"):
            self.project.complete("run")
        self.results_page.refresh_run_list()
        self.refresh_workflow()

    def _fill_recent(self):
        self.recent_menu.clear()
        for path in self.ctx.recent("projects"):
            action = self.recent_menu.addAction(path)
            action.triggered.connect(lambda _checked=False, p=path: self.open_project(Path(p)))
        if not self.recent_menu.actions():
            self.recent_menu.addAction("まだありません").setEnabled(False)

    def refresh_workflow(self):
        for i, key in enumerate(STAGES):
            item = self.nav.item(i)
            reason = self.project.blocked_reason(key)
            if key == "results" and self.results_page.has_project_runs():
                reason = ""
            item.setText(f"{i + 1}  {LABELS[i]}\n    {STATE_LABELS[self.project.stages[key]]}")
            item.setToolTip(reason or "この段階に戻れます")
            item.setFlags(Qt.ItemFlag.ItemIsSelectable | (Qt.ItemFlag.ItemIsEnabled if not reason else Qt.ItemFlag.NoItemFlags))
        suffix = " *" if self.project.dirty else ""
        self.setWindowTitle(f"{self.project.name}{suffix} — GSIS-PBC" + (" / 未保存" if not self._saved else ""))

    def show_page(self, key):
        if self.run_page.busy and key != "run":
            self.show_status("実行または順番待ちを停止してから画面を切り替えてください")
            return False
        if self.mesh_page.busy and key != "mesh":
            self.show_status("メッシュ生成を停止してから画面を切り替えてください")
            return False
        if key == "ai":
            self.geometry_page.tabs.setCurrentIndex(1)
            key = "geometry"
        if key == "case":
            key = "settings"
        reason = self.project.blocked_reason(key)
        if key == "results" and self.results_page.has_project_runs():
            reason = ""
        if reason:
            self.show_status(reason)
            return False
        self.nav.setCurrentRow(STAGES.index(key))
        self.stack.setCurrentWidget(self.pages[key])
        if key == "run":
            self.run_page.bind_project(self.project)
        elif key == "results":
            self.results_page.refresh_run_list()
        return True

    def _navigate(self, row):
        if row >= 0 and self.stack.currentWidget() is not self.pages[STAGES[row]]:
            self.show_page(STAGES[row])

    def _change(self, stage):
        self.project.change(stage)
        if stage == "geometry":
            self.project.geometry["accepted"] = False
            gp = self.geometry_page
            self.project.geometry["pending_input"] = dict(unit=gp.unit.currentText(), flow=gp.flow.currentData(),
                                                          box_factor=gp.factor.value())
        self.refresh_workflow()

    def _geometry_ready(self, result):
        boundaries, lost = remap_boundaries(self.project.boundaries, result.metadata["faces"])
        self.project.geometry = dict(result.metadata, accepted=True)
        self.project.boundaries = with_default(boundaries, result.metadata["faces"], self.project.settings.wall_temperature)
        self.boundaries_page.load_geometry(result)
        self.mesh_page.reload()
        self.project.complete("geometry")
        if self.project.settings.length_auto:
            self.project.settings.L_ref = result.metadata["length_ref"]
        self.settings_page.load_settings(self.project.settings)
        self.refresh_workflow()
        message = "形状の検査が通りました。次に設定へ進めます"
        if lost:
            message += " / 引き継げなかった面: " + "、".join(lost)
            self.geometry_page.note.set_status("warning", message)
        self.show_status(message)

    def _settings_changed(self):
        self._change("settings")
        try:
            self.project.settings = self.settings_page.collect()
            if self.project.settings.length_auto and self.project.geometry.get("length_ref"):
                self.project.settings.L_ref = self.project.geometry["length_ref"]
                self.settings_page.inputs["L_ref"].setValue(self.project.settings.L_ref)
                self.settings_page.refresh()
        except ValueError:
            pass  # 編集途中の不正な入力はフォームに保ち、保存時に報告する。

    def _accept_settings(self):
        try:
            self.project.settings = self.settings_page.collect()
            self.project.boundaries = with_default(self.project.boundaries, self.project.geometry.get("faces", []),
                                                   self.project.settings.wall_temperature)
            self.boundaries_page.reload()
            self.project.complete("settings")
            self.refresh_workflow()
            self.show_page("boundaries")
        except ValueError as exc:
            self.show_status(str(exc))

    def _boundaries_changed(self):
        self._change("boundaries")
        self.project.settings.update_boundary_defaults(self.project.boundaries)
        self.settings_page.load_settings(self.project.settings)

    def _accept_boundaries(self):
        self.project.settings.update_boundary_defaults(self.project.boundaries)
        self.settings_page.load_settings(self.project.settings)
        self.project.complete("boundaries")
        self.mesh_page.reload()
        self.refresh_workflow()
        self.show_page("mesh")

    def _mesh_ready(self, result):
        self.settings_page.load_settings(self.project.settings)
        memory = mesh_memory(self.project, result["cells"])
        if memory["fits"]:
            self.project.mesh["validated"] = True
            self.project.complete("mesh")
        else:
            self.project.mesh.pop("validated", None)
        # 未保存プロジェクトでも生成結果と case.toml を作業領域に保持する。
        error = None
        try:
            if not self.project.name.strip():
                raise ValueError("計算名を入力してください")
            save_project(self.project)
            if not self._saved:
                # 一時領域の自動出力は、ユーザーが保存先を選んだことにはしない。
                self.project.dirty = True
            if memory["fits"]:
                self.run_page.bind_project(self.project)
        except (ValueError, OSError, TypeError) as exc:
            self.project.change("mesh")
            error = f"ケースを保存できません: {exc}"
        self.mesh_page.present(result)
        if error:
            self.mesh_page.note.set_status("error", error)
            self.show_status(error)
        self.refresh_workflow()

    def _rename(self, text):
        self.project.name = text
        self.project.dirty = True
        self.refresh_workflow()

    def _can_replace(self):
        if self.geometry_page.busy or self.mesh_page.busy or self.ai_page.busy or self.run_page.busy or self.results_page._busy:
            self.show_status("実行中の処理が終わるか、停止してからプロジェクトを切り替えてください")
            return False
        return self._check_unsaved()

    def _check_unsaved(self):
        if not self.project.dirty:
            return True
        answer = QMessageBox.question(self, "変更を保存しますか", "プロジェクトに保存していない変更があります。",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self._save_dialog()
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self, directory: Path, name="新しい計算"):
        if not self._can_replace():
            return False
        try:
            directory = Path(directory).resolve()
            if directory.exists() and any(directory.iterdir()):
                raise ValueError("新規プロジェクトは空のフォルダに作ってください")
            p = Project(name=name, directory=directory)
            save_project(p)
            self._install_project(p)
            return True
        except (ValueError, OSError) as exc:
            self.show_status(str(exc))
            return False

    def open_project(self, path: Path):
        if not self._can_replace():
            return False
        try:
            p = load_project(path)
            result = None
            if p.geometry.get("source"):
                m = p.geometry
                result = prepare_geometry(p.asset(m["source"]), p.directory, unit=m["unit"], flow=m["flow"],
                                          box_factor=m.get("box_factor", 3))
                result.metadata = m
            elif p.geometry.get("source_kind") == "existing_mesh":
                result = mesh_geometry(p)
            self._install_project(p, result)
            return True
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.show_status(f"プロジェクトを開けません: {exc}")
            return False

    def _install_project(self, p, result=None):
        self.project = self.ctx.project = p
        self._saved = True
        self.name_field.blockSignals(True)
        self.name_field.setText(p.name)
        self.name_field.blockSignals(False)
        gp = self.geometry_page
        gp.result = None
        gp.view.clear()
        gp.faces.setRowCount(0)
        gp.source.clear()
        gp.note.set_status(None, "閉じた立体を 1 つ読み込んでください")
        if result is not None:
            gp.present(result)
            if p.geometry.get("source"):
                gp.source.setText(str(p.asset(p.geometry["source"])))
            else:
                gp.source.setText("既存メッシュから: " + p.mesh["path"])
                gp.note.set_status("ok", "既存メッシュから形状の段階を完了しました。CAD はない例題です")
            if p.geometry.get("accepted") is False:
                gp.note.set_status("warning", "形状の入力が未確定です。領域を再検査してください。")
                pending = p.geometry.get("pending_input", {})
                gp._loading = True
                gp.unit.setCurrentText(pending.get("unit", p.geometry["unit"]))
                gp.flow.setCurrentIndex(gp.flow.findData(pending.get("flow", p.geometry["flow"])))
                gp.factor.setValue(pending.get("box_factor", p.geometry["box_factor"]))
                gp._loading = False
        self.settings_page.load_settings(p.settings)
        self.boundaries_page.load_geometry(result)
        self.mesh_page.reload()
        self.run_page.project = None
        self.run_page.set_case("")
        self.results_page.set_project(p)
        self.ctx.add_recent("projects", p.directory / "project.toml")
        self.refresh_workflow()
        self.show_page("geometry")
        if p.stages["mesh"] == "done":
            self.run_page.bind_project(p)

    def save(self, directory=None):
        if self.run_page.busy:
            self.show_status("実行または順番待ちを停止してから保存してください")
            return False
        if self.geometry_page.busy or self.mesh_page.busy:
            self.show_status("形状・メッシュの処理が終わってから保存してください")
            return False
        try:
            if self.boundaries_page.has_draft:
                raise ValueError("編集中の境界パッチを割り当て・更新してから保存してください")
            if not self.project.name.strip():
                raise ValueError("計算名を入力してください")
            self.project.settings = self.settings_page.collect()
            path = save_project(self.project, Path(directory) if directory else None)
            self._saved = True
            self.run_page.bind_project(self.project)
            self.results_page.set_project(self.project)
            self.ctx.add_recent("projects", path)
            self.refresh_workflow()
            self.show_status(f"保存しました: {path}")
            return True
        except (ValueError, OSError, TypeError) as exc:
            self.show_status(f"保存できません: {exc}")
            return False

    def _new_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "新規プロジェクトの空のフォルダ")
        if path:
            self.new_project(Path(path), Path(path).name)

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "プロジェクト (project.toml)")
        if path:
            self.open_project(Path(path))

    def _save_dialog(self, save_as=False):
        if save_as or not self._saved:
            path = QFileDialog.getExistingDirectory(self, "プロジェクトの保存先(空のフォルダ)")
            return self.save(Path(path)) if path else False
        return self.save()

    def show_status(self, text):
        self.statusBar().showMessage(text, 15000)

    def _update_solver_label(self):
        p = self.ctx.solver_path()
        self.solver_label.setText(f"ソルバー: {display_path(p, self.ctx.root)}" if self.ctx.solver_ok() else "ソルバーが見つかりません")

    def _choose_solver(self):
        p, _ = QFileDialog.getOpenFileName(self, "gsis-solver を選ぶ", str(self.ctx.root), f"ソルバー ({solver_exe_name()})")
        if p:
            self.ctx.set_solver_path(p)

    def _open_results(self, out_dir):
        self.show_page("results")
        self.stack.setCurrentWidget(self.results_page)
        self.results_page.load(Path(out_dir))

    def _results_loaded(self, directory):
        if (Path(directory).resolve().is_relative_to(self.project.asset("runs")) and
                not self.project.blocked_reason("results")):
            self.project.complete("results")
            self.refresh_workflow()

    def closeEvent(self, ev):  # noqa: N802
        if self.mesh_page.busy:
            self.mesh_page.stop()
            self.show_status("メッシュの停止が終わってから終了してください")
            ev.ignore()
            return
        if self.geometry_page.busy:
            self.show_status("形状の読み込みが終わってから終了してください")
            ev.ignore()
            return
        if self.run_page.busy or self.ai_page.busy:
            answer = QMessageBox.question(self, "処理を止めますか", "実行中の処理を止めて終了しますか?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.run_page.stop()
            if self.run_page.proc is not None:
                self.run_page.proc.waitForFinished(3000)
            if self.run_page.busy:
                self.show_status("ソルバーの停止が終わってから終了してください")
                ev.ignore()
                return
        if not self._closed and not self._check_unsaved():
            ev.ignore()
            return
        self.shutdown()
        super().closeEvent(ev)

    def shutdown(self):
        if self._closed:
            return
        self.run_page.shutdown()
        self.geometry_page.shutdown()
        self.boundaries_page.shutdown()
        self.mesh_page.shutdown()
        self.results_page.shutdown()
        self._staging.cleanup()
        self._closed = True
