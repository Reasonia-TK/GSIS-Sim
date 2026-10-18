"""実行の画面: ``gsis-solver`` のサブコマンド(``run``・``cis-run``・``pbc-run``)を起動・停止し、
標準出力のログと ``history.csv``(収束の履歴と質量流量)をリアルタイムに表示する。

- サブコマンドと使える引数は ``gsis-solver --help`` から読む。ないサブコマンド(フェーズ 4 の ``run`` など)は
  一覧に「(このソルバーにはない)」と出し、実行の前に知らせる
- 出力先は必ず ``--out`` で渡し(使えるとき)、そのフォルダの ``history.csv`` を 1 秒ごとに読む
- 停止は QProcess の kill(Windows のコンソールのプログラムは terminate では止まらない)。出力は途中まで残る
- GPU のメモリ(GPU 全体の使用量)は nvidia-smi があれば 5 秒ごとに読む
"""

from __future__ import annotations

import codecs
import math
import shutil
import time
from pathlib import Path

import numpy as np

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import casefile
from ..casefile import CaseFileError, CaseModel, Derived
from ..context import AppContext
from ..history import HistoryTail, mass_balance, mass_flow_series
from ..paths import display_path
from ..reference import load_table1, table1_row
from ..solver import (
    RUN_SUBCOMMANDS,
    SUBCOMMAND_LABELS,
    RunOptions,
    build_run_args,
    default_out_dir,
    explain_failure,
    pick_flag,
)
from ..widgets.common import FloatField, LogView, PathField, StatusLabel, heading, muted, primary_button
from ..widgets.history_plot import HistoryPlot


class RunPage(QWidget):
    """実行の画面。"""

    run_finished = Signal(str, int)  # 出力先、終了コード
    open_results = Signal(str)
    status = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self.editor_case = None  # () -> (Path | None, bool 変更あり) を main window が入れる
        self.proc: QProcess | None = None
        self.tail: HistoryTail | None = None
        self.case_model: CaseModel | None = None
        self.derived: Derived | None = None
        self._t_start = 0.0
        self._out_dir: Path | None = None
        self._current_sub: str | None = None
        self._decoders = {}
        self._stop_requested = False
        self._last_draw = 0.0
        self._pending_draw = False
        self._gpu_proc: QProcess | None = None
        self._nvsmi = shutil.which("nvidia-smi")
        self._table1 = None
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._poll)
        self.gpu_timer = QTimer(self)
        self.gpu_timer.setInterval(5000)
        self.gpu_timer.timeout.connect(self._poll_gpu)
        self.ctx.theme.changed.connect(self._restyle)
        self.ctx.solver_changed.connect(self.refresh_subcommands)
        self.refresh_subcommands()

    # ------------------------------------------------------------------ 画面
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(heading("実行"))
        top.addSpacing(12)
        self.run_btn = primary_button("実行")
        self.stop_btn = QPushButton("停止")
        self.attach_btn = QPushButton("出力フォルダを追う")
        self.attach_btn.setToolTip("実行せずに、指定した出力フォルダの history.csv を読んで表示する(別に起動した計算を見る)")
        self.results_btn = QPushButton("結果を開く")
        for w in (self.run_btn, self.stop_btn, self.attach_btn, self.results_btn):
            top.addWidget(w)
        top.addSpacing(16)
        self.state = StatusLabel("待機中")
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.state, 1)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # 左: 設定
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 8, 0)
        g = QGroupBox("計算")
        f = QFormLayout(g)
        self.case_field = PathField("open", "ケース定義 (*.toml);;すべてのファイル (*)", "ケースを選ぶ",
                                    start_dir=lambda: str(self.ctx.root / "cases"))
        self.from_editor_btn = QPushButton("ケース設定の画面のケースを使う")
        self.case_info = muted("")
        self.sub_combo = QComboBox()
        self.sub_note = StatusLabel("")
        self.refresh_btn = QPushButton("サブコマンドを読み直す")
        self.refresh_btn.setToolTip("gsis-solver --help をもう一度読む(ソルバーを作り直したとき)")
        self.out_field = PathField("dir", caption="出力先のフォルダ")
        self.out_default_btn = QPushButton("既定の出力先")
        f.addRow("ケース", self.case_field)
        f.addRow("", self.from_editor_btn)
        f.addRow("", self.case_info)
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.sub_combo, 1)
        sub_row.addWidget(self.refresh_btn)
        f.addRow("サブコマンド", sub_row)
        f.addRow("", self.sub_note)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out_field, 1)
        out_row.addWidget(self.out_default_btn)
        f.addRow("出力先", out_row)
        lv.addWidget(g)

        g = QGroupBox("ソルバーの引数(使えないものは灰色)")
        grid = QGridLayout(g)
        self.maxit_check = QCheckBox("反復の上限")
        self.maxit_spin = QSpinBox()
        self.maxit_spin.setRange(1, 10_000_000)
        self.maxit_spin.setValue(20)
        self.tol_check = QCheckBox("収束判定")
        self.tol_field = FloatField(1e-5, positive=True)
        self.backend_label = QLabel("実行先")
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("ケースのとおり", "")
        self.backend_combo.addItem("cuda(GPU)", "cuda")
        self.backend_combo.addItem("cpu(小さいメッシュ用)", "cpu")
        self.precision_label = QLabel("精度")
        self.precision_combo = QComboBox()
        self.precision_combo.addItem("ケースのとおり", "")
        self.precision_combo.addItems(["fp32", "fp64"])
        self.print_check = QCheckBox("表示の間隔")
        self.print_spin = QSpinBox()
        self.print_spin.setRange(1, 100000)
        self.print_spin.setValue(1)
        self.novtu_check = QCheckBox("VTU を書かない(--no-vtu)")
        self.cellscsv_check = QCheckBox("cells.csv も書く(--cells-csv)")
        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("例: --set limiter_k=5  --threads 8")
        grid.addWidget(self.maxit_check, 0, 0)
        grid.addWidget(self.maxit_spin, 0, 1)
        grid.addWidget(self.tol_check, 1, 0)
        grid.addWidget(self.tol_field, 1, 1)
        grid.addWidget(self.backend_label, 2, 0)
        grid.addWidget(self.backend_combo, 2, 1)
        grid.addWidget(self.precision_label, 3, 0)
        grid.addWidget(self.precision_combo, 3, 1)
        grid.addWidget(self.print_check, 4, 0)
        grid.addWidget(self.print_spin, 4, 1)
        grid.addWidget(self.novtu_check, 5, 0, 1, 2)
        grid.addWidget(self.cellscsv_check, 6, 0, 1, 2)
        grid.addWidget(QLabel("追加の引数"), 7, 0)
        grid.addWidget(self.extra_edit, 7, 1)
        self.maxit_check.setChecked(True)
        lv.addWidget(g)

        g = QGroupBox("コマンド")
        v = QVBoxLayout(g)
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setReadOnly(True)
        self.cmd_note = muted("")
        v.addWidget(self.cmd_edit)
        v.addWidget(self.cmd_note)
        lv.addWidget(g)

        g = QGroupBox("状況")
        f = QFormLayout(g)
        self.lab_iter = QLabel("—")
        self.lab_eps = QLabel("—")
        self.lab_mdot = QLabel("—")
        self.lab_time = QLabel("—")
        self.lab_gpu = QLabel("—" if self._nvsmi else "nvidia-smi がないので出さない")
        for lab in (self.lab_iter, self.lab_eps, self.lab_mdot, self.lab_time, self.lab_gpu):
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setWordWrap(True)
        f.addRow("反復", self.lab_iter)
        f.addRow("収束", self.lab_eps)
        f.addRow("質量流量", self.lab_mdot)
        f.addRow("時間", self.lab_time)
        f.addRow("GPU のメモリ(全体)", self.lab_gpu)
        lv.addWidget(g)
        lv.addStretch(1)
        split.addWidget(left)

        # 右: グラフとログ
        right = QSplitter(Qt.Orientation.Vertical)
        self.plot = HistoryPlot()
        right.addWidget(self.plot)
        self.log = LogView()
        right.addWidget(self.log)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([430, 1050])

        self.case_field.changed.connect(self._on_case_changed)
        self.from_editor_btn.clicked.connect(self.use_editor_case)
        self.sub_combo.currentIndexChanged.connect(self._on_sub_changed)
        self.refresh_btn.clicked.connect(lambda: self.refresh_subcommands(refresh=True))
        self.out_default_btn.clicked.connect(self._set_default_out)
        self.out_field.changed.connect(self._update_command)
        for w in (self.maxit_check, self.tol_check, self.print_check, self.novtu_check, self.cellscsv_check):
            w.toggled.connect(self._update_command)
        self.maxit_spin.valueChanged.connect(self._update_command)
        self.print_spin.valueChanged.connect(self._update_command)
        self.tol_field.valueChanged.connect(self._update_command)
        self.backend_combo.currentIndexChanged.connect(self._update_command)
        self.precision_combo.currentIndexChanged.connect(self._update_command)
        self.extra_edit.textChanged.connect(self._update_command)
        self.run_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.attach_btn.clicked.connect(self.attach)
        self.results_btn.clicked.connect(lambda: self._out_dir and self.open_results.emit(str(self._out_dir)))
        self._update_buttons()

    # ------------------------------------------------------------------ サブコマンド
    def refresh_subcommands(self, refresh: bool = False) -> None:
        subs = self.ctx.subcommands(refresh=refresh)
        current = self.sub_combo.currentData()
        self.sub_combo.blockSignals(True)
        self.sub_combo.clear()
        for name in RUN_SUBCOMMANDS:
            label = SUBCOMMAND_LABELS.get(name, name)
            if name not in subs:
                label += "  — このソルバーにはない"
            self.sub_combo.addItem(label, name)
        # 既定: 使えるもののうち run、なければ cis-run
        pick = current if current else next((n for n in RUN_SUBCOMMANDS if n in subs), "cis-run")
        self.sub_combo.setCurrentIndex(max(self.sub_combo.findData(pick), 0))
        self.sub_combo.blockSignals(False)
        self._on_sub_changed()

    def available(self, name: str) -> bool:
        return name in self.ctx.subcommands()

    def _flags(self, name: str) -> set[str] | None:
        info = self.ctx.subcommands().get(name)
        return None if info is None else info.flags

    def _on_sub_changed(self, *_a) -> None:
        name = self.sub_combo.currentData()
        if not self.ctx.solver_ok():
            self.sub_note.set_status("error", f"ソルバーが見つかりません: {self.ctx.solver_path()}(上のツールバーで選ぶ)")
        elif not self.available(name):
            hint = "(フェーズ 4 で追加される予定)" if name == "run" else ""
            self.sub_note.set_status("warning", f"このソルバーには {name} サブコマンドがありません{hint}。"
                                                 "ソルバーを作り直したら「サブコマンドを読み直す」を押してください")
        else:
            self.sub_note.set_status("ok", self.ctx.subcommands()[name].summary)
        flags = self._flags(name) or set()
        has = lambda item: pick_flag(item, flags) is not None  # noqa: E731
        self.maxit_check.setEnabled(has("max_iter"))
        self.maxit_spin.setEnabled(has("max_iter"))
        self.tol_check.setEnabled(has("tolerance"))
        self.tol_field.setEnabled(has("tolerance"))
        for w in (self.backend_combo, self.backend_label):
            w.setEnabled(has("backend"))
        for w in (self.precision_combo, self.precision_label):
            w.setEnabled(has("precision"))
        self.print_check.setEnabled(has("print_every"))
        self.print_spin.setEnabled(has("print_every"))
        self.novtu_check.setEnabled(has("no_vtu"))
        self.cellscsv_check.setEnabled(has("cells_csv"))
        if not self.out_field.text() or self._out_is_default:
            self._set_default_out()
        self._update_command()
        self._update_buttons()

    # ------------------------------------------------------------------ ケース
    _out_is_default = True

    def use_editor_case(self) -> None:
        if self.editor_case is None:
            return
        path, dirty = self.editor_case()
        if path is None:
            QMessageBox.information(self, "ケース", "ケース設定の画面のケースはまだ保存されていません。先に保存してください。")
            return
        if dirty:
            QMessageBox.information(self, "ケース", "ケース設定の画面に保存していない変更があります。実行の前に保存してください。")
        self.set_case(path)

    def set_case(self, path: str | Path) -> None:
        self.case_field.setText(str(path))

    def _on_case_changed(self, *_a) -> None:
        p = Path(self.case_field.text()) if self.case_field.text() else None
        self.case_model = None
        self.derived = None
        if p is None:
            self.case_info.setText("ケース定義(TOML)を選んでください")
        elif not p.is_file():
            self.case_info.setText(f"見つかりません: {p}")
        else:
            try:
                self.case_model = casefile.load_case(p).model
                self.derived = casefile.derive(self.case_model)
                m = self.case_model
                ref = f"δ_rp = {m.ref_value:g}" if m.ref_kind == "delta_rp" else f"{m.ref_kind} = {m.ref_value:g}"
                self.case_info.setText(f"名前 {m.name}、{ref}、mode = {m.mode}、メッシュ {m.mesh}")
            except (OSError, CaseFileError, ValueError, ZeroDivisionError) as exc:
                self.case_info.setText(f"読めません: {exc}")
        if self._out_is_default:
            self._set_default_out()
        self._update_command()
        self._update_buttons()

    def _set_default_out(self) -> None:
        name = self.case_model.name if self.case_model else "case"
        sub = self.sub_combo.currentData() or "cis-run"
        self.out_field.blockSignals(True)
        self.out_field.setText(str(default_out_dir(sub, name, self.ctx.root)))
        self.out_field.blockSignals(False)
        self._out_is_default = True
        self._update_command()

    # ------------------------------------------------------------------ コマンド
    def options(self) -> RunOptions:
        return RunOptions(
            out_dir=self.out_field.text() or None,
            max_iter=self.maxit_spin.value() if self.maxit_check.isChecked() and self.maxit_check.isEnabled() else None,
            tolerance=self.tol_field.value() if self.tol_check.isChecked() and self.tol_check.isEnabled() else None,
            backend=(self.backend_combo.currentData() or None) if self.backend_combo.isEnabled() else None,
            precision=(self.precision_combo.currentText() if self.precision_combo.currentIndex() > 0 else None)
            if self.precision_combo.isEnabled() else None,
            print_every=self.print_spin.value() if self.print_check.isChecked() and self.print_check.isEnabled() else None,
            no_vtu=self.novtu_check.isChecked() and self.novtu_check.isEnabled(),
            cells_csv=self.cellscsv_check.isChecked() and self.cellscsv_check.isEnabled(),
            extra=self.extra_edit.text(),
        )

    def command(self) -> tuple[list[str], list[str]]:
        sub = self.sub_combo.currentData()
        args, skipped = build_run_args(sub, self.case_field.text() or "<case.toml>", self.options(), self._flags(sub))
        return [str(self.ctx.solver_path()), *args], skipped

    def _update_command(self, *_a) -> None:
        if self.sender() is self.out_field:
            self._out_is_default = False
        cmd, skipped = self.command()
        self.cmd_edit.setText(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        notes = []
        if skipped:
            notes.append("使えないので渡さない: " + "、".join(skipped))
        if "--out" not in cmd:
            notes.append("--out を渡せないので、出力先はソルバーの既定(runs/…/<ケース名>)とみなす")
        self.cmd_note.setText(" / ".join(notes) or f"作業ディレクトリ: {self.ctx.root}")

    def _update_buttons(self) -> None:
        running = self.proc is not None
        sub = self.sub_combo.currentData()
        self.run_btn.setEnabled(not running and self.ctx.solver_ok() and self.case_model is not None)
        self.stop_btn.setEnabled(running)
        self.attach_btn.setEnabled(not running)
        self.results_btn.setEnabled(self._out_dir is not None and (self._out_dir / "summary.json").is_file())
        for w in (self.case_field, self.from_editor_btn, self.sub_combo, self.refresh_btn, self.out_field,
                  self.out_default_btn):
            w.setEnabled(not running)
        if running:
            self.run_btn.setToolTip("実行中")
        elif sub and not self.available(sub):
            self.run_btn.setToolTip(f"このソルバーには {sub} がありません")
        else:
            self.run_btn.setToolTip("")

    # ------------------------------------------------------------------ 実行
    def _plot_context(self) -> None:
        d = self.derived
        types = {b.patch: b.type for b in self.case_model.boundaries} if self.case_model else None
        refs = []
        case_dir = Path(self.case_field.text()).parent if self.case_field.text() else None
        if d is not None and d.has_p_in and casefile.is_tube_case(self.case_model, case_dir):
            if self._table1 is None:
                try:
                    self._table1 = load_table1()
                except OSError:
                    self._table1 = []
            row = table1_row(self._table1, d.delta)
            if row is not None:
                sub = self._current_sub or self.sub_combo.currentData()
                if sub == "cis-run":
                    refs.append((f"論文 CIS {row.cis_M:g}", row.cis_M))
                refs.append((f"論文 GSIS-PBC {row.gsis_pbc_M:g}", row.gsis_pbc_M))
        tol = None
        if self.proc is None and getattr(self, "_attached_tolerance", None):
            tol = self._attached_tolerance  # 出力フォルダを追うときは、その計算の収束判定
        elif self.tol_check.isChecked() and self.tol_check.isEnabled():
            tol = self.tol_field.value()
        elif self.case_model is not None:
            tol = float(self.case_model.gsis_value("tolerance")) if (self._current_sub or "") != "pbc-run" else None
        self.plot.set_context(
            nd_to_Mdot=d.nd_to_Mdot if d is not None and d.has_p_in else None,
            mass_flow_scale=d.mass_flow_scale if d is not None and d.has_p_in else None,
            patch_types=types, tolerance=tol, reference_mdot=refs,
        )

    def start(self) -> bool:
        if self.proc is not None:
            return False
        sub = self.sub_combo.currentData()
        if not self.ctx.solver_ok():
            QMessageBox.warning(self, "ソルバーがありません", f"ソルバーが見つかりません:\n{self.ctx.solver_path()}")
            return False
        if not self.available(sub):
            hint = "\n\n(run は GSIS-PBC の結合(フェーズ 4)で追加される予定です。それまでは cis-run か pbc-run を使ってください)" if sub == "run" else ""
            QMessageBox.warning(self, "サブコマンドがありません",
                                f"このソルバー({self.ctx.solver_path().name})には {sub} サブコマンドがありません。{hint}")
            self.state.set_status("warning", f"{sub} はこのソルバーにありません")
            return False
        if self.case_model is None:
            QMessageBox.warning(self, "ケース", "ケース定義(TOML)を選んでください。")
            return False
        cmd, skipped = self.command()
        out = self._expected_out_dir(cmd)
        if out is not None and ((out / "history.csv").exists() or (out / "summary.json").exists()):
            r = QMessageBox.question(self, "上書きしますか", f"出力先に前の結果があります:\n{out}\n\n上書きしますか?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return False
        return self._launch(cmd, sub, out)

    def _expected_out_dir(self, cmd: list[str]) -> Path | None:
        if "--out" in cmd:
            p = Path(cmd[cmd.index("--out") + 1])
            return p if p.is_absolute() else self.ctx.root / p
        name = self.case_model.name if self.case_model else "case"
        return default_out_dir(self.sub_combo.currentData(), name, self.ctx.root)

    def _launch(self, cmd: list[str], sub: str, out: Path | None) -> bool:
        self._current_sub = sub
        self._attached_tolerance = None
        self._out_dir = out
        self._t_start = time.time()
        self._stop_requested = False
        self.log.clear_log()
        self.log.append_lines("$ " + " ".join(cmd), stream="gui")
        self.tail = HistoryTail(out / "history.csv", min_mtime=self._t_start - 2.0) if out else None
        self._plot_context()
        self.plot.set_history(None)
        self._decoders = {k: codecs.getincrementaldecoder("utf-8")(errors="replace") for k in ("out", "err")}
        proc = QProcess(self)
        proc.setWorkingDirectory(str(self.ctx.root))
        env = QProcessEnvironment.systemEnvironment()
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(lambda: self._read(proc, "out"))
        proc.readyReadStandardError.connect(lambda: self._read(proc, "err"))
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._error)
        self.proc = proc
        proc.start(cmd[0], cmd[1:])
        self.state.set_status("info", f"{sub} を実行しています…")
        self.timer.start()
        if self._nvsmi:
            self.gpu_timer.start()
            self._poll_gpu()
        self._update_buttons()
        self.status.emit(f"{sub} を起動しました(出力先 {display_path(out, self.ctx.root) if out else '既定'})")
        return True

    def _read(self, proc: QProcess, stream: str) -> None:
        data = bytes(proc.readAllStandardOutput() if stream == "out" else proc.readAllStandardError())
        text = self._decoders[stream].decode(data)
        if text:
            self.log.append_lines(text.rstrip("\n"), stream=stream)

    def _error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart and self.proc is not None:
            self.log.append_lines(f"起動できません: {self.proc.errorString()}", kind="error")
            self.state.set_status("error", "ソルバーを起動できませんでした")
            self._cleanup()

    def _finished(self, code: int, _status) -> None:
        if self.proc is not None:
            self._read(self.proc, "out")
            self._read(self.proc, "err")
        self._poll(force=True)
        text = self.log.text_all()
        if self._stop_requested:
            self.state.set_status("warning", f"停止しました(終了コード {code})。出力は途中まで")
        elif code == 0:
            conv = self._summary_state()
            self.state.set_status("ok", f"終了しました(終了コード 0){conv}")
        else:
            self.state.set_status("error", f"{explain_failure(code, text, self._current_sub)}(終了コード {code})")
        self.log.append_lines(f"— 終了コード {code}({time.time() - self._t_start:.1f} 秒)", stream="gui")
        out = self._out_dir
        self._cleanup()
        self.run_finished.emit(str(out) if out else "", int(code))

    def _summary_state(self) -> str:
        if self._out_dir is None or not (self._out_dir / "summary.json").is_file():
            return ""
        try:
            from ..results import read_json

            s = read_json(self._out_dir / "summary.json")
        except (OSError, ValueError):
            return ""
        conv = s.get("converged")
        it = s.get("iterations")
        return f"。{it} 反復で" + ("収束" if conv else "上限に達した(収束していない)") if conv is not None else ""

    def _cleanup(self) -> None:
        self.timer.stop()
        self.gpu_timer.stop()
        self.proc = None
        self._update_buttons()

    def stop(self) -> None:
        if self.proc is None:
            return
        self._stop_requested = True
        self.log.append_lines("停止を要求しました(プロセスを終了させます)", stream="gui")
        self.proc.kill()

    def attach(self) -> None:
        """実行せずに出力フォルダの history.csv を読む。"""
        out = Path(self.out_field.text()) if self.out_field.text() else None
        if out is None or not out.is_dir():
            QMessageBox.information(self, "出力フォルダ", "出力先に既存のフォルダを指定してください。")
            return
        self.attach_dir(out)

    def attach_dir(self, out: Path) -> None:
        self._out_dir = Path(out)
        self._current_sub = None
        self._attached_tolerance = None
        # 出力の summary.json からケースとサブコマンドを知る(実行中で summary.json がなければ今のケースのまま)
        try:
            from ..results import load_run

            run = load_run(out, self.ctx.root)
            if run.case_path is not None and Path(self.case_field.text() or ".").resolve() != run.case_path:
                self.case_field.setText(str(run.case_path))
            self._current_sub = {"kinetic": "cis-run", "macro": "pbc-run", "gsis": "run"}.get(run.kind)
            tol = run.summary.get("tolerance")
            if isinstance(tol, (int, float)) and run.kind != "macro":
                self._attached_tolerance = float(tol)
        except (OSError, ValueError):
            pass
        self.tail = HistoryTail(self._out_dir / "history.csv")
        self._plot_context()
        self.plot.set_history(None)
        self._poll(force=True)
        if self.proc is None:
            self.log.clear_log()
            self.lab_gpu.setText("—(この画面から実行しているときだけ読む)" if self._nvsmi else self.lab_gpu.text())
        self.log.append_lines(f"出力フォルダを追っています: {self._out_dir}(history.csv {self.tail.n_rows} 行。"
                              "ソルバーの標準出力はこの画面から起動したときだけ出る)", stream="gui")
        if self.proc is None:
            self.timer.start()  # 別に起動した計算なら、増えていく履歴を追う
        self.state.set_status("info", f"出力フォルダを追っています: {display_path(self._out_dir, self.ctx.root)}")
        self._update_buttons()

    # ------------------------------------------------------------------ 履歴
    def _poll(self, force: bool = False) -> None:
        if self.tail is None:
            return
        new = self.tail.poll()
        now = time.time()
        self._pending_draw = self._pending_draw or bool(new)
        if not (self._pending_draw or force):
            if self.proc is not None:
                self.lab_time.setText(f"経過 {now - self._t_start:.0f} 秒")
            return
        if not force and now - self._last_draw < 0.9:
            return  # 描くのは 1 秒に 1 回まで(読んだ行は次の回に描く)
        self._last_draw = now
        self._pending_draw = False
        h = self.tail.history()
        self.plot.set_history(h)
        if h.n == 0:
            return
        it = h.last("iteration")
        self.lab_iter.setText(f"{int(it)}" if math.isfinite(it) else "—")
        eps = h.last("epsilon")
        if math.isfinite(eps):
            self.lab_eps.setText(f"ε = {eps:.3e}")
        else:
            ups = [h.last(c) for c in ("update_p", "update_u", "update_T")]
            ups = [u for u in ups if math.isfinite(u)]
            self.lab_eps.setText(f"更新量 max = {max(ups):.3e}" if ups else "—")
        d = self.derived
        series, _ = mass_flow_series(h, nd_to_Mdot=d.nd_to_Mdot if d and d.has_p_in else None,
                                     mass_flow_scale=d.mass_flow_scale if d and d.has_p_in else None,
                                     patch_types={b.patch: b.type for b in self.case_model.boundaries}
                                     if self.case_model else None)
        parts = [f"{s.label} {s.y[-1]:.5g}" for s in series
                 if s.source == "main" and s.y.size and math.isfinite(s.y[-1])]
        mb = mass_balance(series)
        if math.isfinite(mb):
            parts.append(f"差 {mb:.1e}")
        self.lab_mdot.setText(("Ṁ: " if d and d.has_p_in else "") + "、".join(parts) if parts else "—")
        el = h.elapsed_seconds()
        per = None
        for c in ("time_iter_s", "seconds"):
            if h.has(c):
                v = h.column(c)
                v = v[np.isfinite(v) & (v > 0)]
                if v.size:
                    per = float(np.median(v[-20:]))
                break
        t_txt = f"経過 {el[-1]:.1f} 秒" if el is not None and el.size and math.isfinite(el[-1]) else ""
        if per:
            t_txt += f"、1 反復 {per:.3g} 秒"
        self.lab_time.setText(t_txt or "—")

    def _poll_gpu(self) -> None:
        if not self._nvsmi or self._gpu_proc is not None:
            return
        p = QProcess(self)
        p.finished.connect(lambda _c, _s, proc=p: self._gpu_done(proc))
        self._gpu_proc = p
        p.start(self._nvsmi, ["--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])

    def _gpu_done(self, proc: QProcess) -> None:
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        self._gpu_proc = None
        try:
            used, total = (float(x) for x in text.splitlines()[0].split(","))
            self.lab_gpu.setText(f"{used:,.0f} / {total:,.0f} MiB(GPU 全体。ほかのプログラムの分を含む)")
        except (ValueError, IndexError):
            self.lab_gpu.setText("読めません")

    def _restyle(self, scheme: str) -> None:
        self.log.restyle(self.ctx.theme.tokens())
        self.plot.restyle(scheme)

    def shutdown(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.waitForFinished(3000)
