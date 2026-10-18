"""ケース設定の画面: 管ケースの TOML を読み書きする。

- 節ごと(case・gas・conditions・boundary・velocity_grid・gsis・kinetic・macro・output)の入力欄
- プリセット(He、δ_rp = 1, 10, 100, 1000)
- 保存の前に ``gsis-solver check-case`` を実行して結果を出す(エラーなら保存するか尋ねる)
- 元のファイルのコメントと並びは保つ(:mod:`gsis_tools.gui.casefile`)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import casefile
from ..casefile import (
    BOUNDARY_OPTIONAL,
    BOUNDARY_REQUIRED,
    BOUNDARY_TYPE_LABELS,
    BOUNDARY_TYPES,
    GSIS_DEFAULTS,
    HELIUM,
    KNOWN_MODULE_KEYS,
    PRESET_DELTAS,
    Boundary,
    CaseFileError,
    CaseModel,
)
from ..context import AppContext
from ..paths import display_path, to_posix_relative, tube_mesh_dir
from ..solver import explain_failure
from ..widgets.common import (
    FloatField,
    KeyValueTable,
    LogView,
    PathField,
    StatusLabel,
    heading,
    muted,
    primary_button,
    readonly_item,
)

TOML_FILTER = "ケース定義 (*.toml);;すべてのファイル (*)"


def _bytes_text(n: float) -> str:
    for unit, s in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= s:
            return f"{n / s:.3g} {unit}"
    return f"{n:.0f} B"


class BoundaryTable(QWidget):
    """[[boundary]] の表。種類に要らない欄は灰色にし、保存するときは書かない。"""

    changed = Signal()

    COLS = ("パッチ", "種類", "温度 [K]", "圧力 [Pa]", "壁速度 [m/s](x, y, z)")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(130)
        self.table.itemChanged.connect(lambda _i: self.changed.emit())
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.add_btn = QPushButton("行を追加")
        self.del_btn = QPushButton("選んだ行を消す")
        self.from_mesh_btn = QPushButton("メッシュのパッチから作る")
        self.from_mesh_btn.setToolTip("メッシュのパッチ名から境界条件の行を作り直す(名前で種類を推定する)")
        row.addWidget(self.add_btn)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        row.addWidget(self.from_mesh_btn)
        lay.addLayout(row)
        self.add_btn.clicked.connect(lambda: (self._append(Boundary("", "diffuse_wall", 273.15)), self.changed.emit()))
        self.del_btn.clicked.connect(self._remove_selected)
        self.hint = muted("温度・圧力は空欄なら書かない(圧力入口の圧力は省略すると p_in)。灰色の欄はその種類では使わない。")
        lay.addWidget(self.hint)

    def _append(self, b: Boundary) -> None:
        with QSignalBlocker(self.table):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(b.patch))
            combo = QComboBox()
            for t in BOUNDARY_TYPES:
                combo.addItem(f"{t}({BOUNDARY_TYPE_LABELS[t]})", t)
            idx = combo.findData(b.type)
            combo.setCurrentIndex(max(idx, 0))
            combo.currentIndexChanged.connect(lambda _i, c=combo: self._on_type_changed(c))
            self.table.setCellWidget(r, 1, combo)
            self.table.setItem(r, 2, QTableWidgetItem("" if b.temperature is None else repr(b.temperature)))
            self.table.setItem(r, 3, QTableWidgetItem("" if b.pressure is None else repr(b.pressure)))
            vel = "" if b.velocity is None else ", ".join(repr(float(v)) for v in b.velocity)
            self.table.setItem(r, 4, QTableWidgetItem(vel))
        self._update_enabled(r)

    def _row_of(self, combo: QComboBox) -> int:
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 1) is combo:
                return r
        return -1

    def _on_type_changed(self, combo: QComboBox) -> None:
        r = self._row_of(combo)
        if r >= 0:
            self._update_enabled(r)
        self.changed.emit()

    def _update_enabled(self, r: int) -> None:
        t = self.table.cellWidget(r, 1).currentData()
        allowed = set(BOUNDARY_REQUIRED.get(t, ())) | set(BOUNDARY_OPTIONAL.get(t, ()))
        with QSignalBlocker(self.table):
            for col, key in ((2, "temperature"), (3, "pressure"), (4, "velocity")):
                it = self.table.item(r, col)
                flags = it.flags()
                if key in allowed:
                    it.setFlags(flags | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
                    tip = "必須" if key in BOUNDARY_REQUIRED.get(t, ()) else "任意(空欄なら書かない)"
                else:
                    it.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsEditable)
                    tip = "この種類では使わない(保存しない)"
                it.setToolTip(tip)

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        if rows:
            self.changed.emit()

    def set_boundaries(self, items: list[Boundary]) -> None:
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
        for b in items:
            self._append(b)

    def boundaries(self) -> list[Boundary]:
        """表の値(数値でない欄は ValueError)。"""
        out = []
        for r in range(self.table.rowCount()):
            patch = self.table.item(r, 0).text().strip()
            t = self.table.cellWidget(r, 1).currentData()
            allowed = set(BOUNDARY_REQUIRED.get(t, ())) | set(BOUNDARY_OPTIONAL.get(t, ()))

            def num(col: int, name: str) -> float | None:
                s = self.table.item(r, col).text().strip()
                if not s or name not in allowed:
                    return None
                try:
                    return float(s)
                except ValueError:
                    raise ValueError(f"境界条件 {patch or r + 1} の {name} が数値ではありません: {s}") from None

            vel = None
            vs = self.table.item(r, 4).text().strip()
            if vs and "velocity" in allowed:
                parts = [p for p in vs.replace(";", ",").split(",") if p.strip()]
                try:
                    vel = tuple(float(p) for p in parts)
                except ValueError:
                    raise ValueError(f"境界条件 {patch or r + 1} の壁速度が数値ではありません: {vs}") from None
                if len(vel) != 3:
                    raise ValueError(f"境界条件 {patch or r + 1} の壁速度は 3 成分です: {vs}")
            out.append(Boundary(patch, t, num(2, "temperature"), num(3, "pressure"), vel))
        return out


class CasePage(QWidget):
    """ケース設定の画面。"""

    case_saved = Signal(str)
    case_loaded = Signal(str)
    status = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self._path: Path | None = None
        self._doc = None  # tomlkit の文書(コメントを保つ)
        self._orig_gsis_keys: set[str] = set()
        self._dirty = False
        self._loading = False
        self._proc: QProcess | None = None
        self._tmpdir: str | None = None
        self._pending_save: Path | None = None
        self._mesh_cells: int | None = None
        self._mesh_patches: list[tuple[str, str]] = []
        self._build()
        self.ctx.theme.changed.connect(self._restyle)
        self.set_model(casefile.tube_preset(10.0), path=None, doc=None)

    # ------------------------------------------------------------------ 画面
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(heading("ケース設定"))
        top.addSpacing(12)
        self.open_btn = QPushButton("開く…")
        self.new_btn = QPushButton("新規")
        self.preset_combo = QComboBox()
        for d in PRESET_DELTAS:
            self.preset_combo.addItem(casefile.preset_label(d), d)
        self.preset_mode = QComboBox()
        self.preset_mode.addItem("GSIS-PBC(mode = gsis)", "gsis")
        self.preset_mode.addItem("CIS 単独(mode = cis)", "cis")
        self.preset_btn = QPushButton("プリセットを入れる")
        self.preset_btn.setToolTip("論文 §4.1 の管ケース(He、管半径 1 mm、30×24×24 の速度格子)を新しいケースとして入れる")
        for w in (self.open_btn, self.new_btn):
            top.addWidget(w)
        top.addSpacing(16)
        top.addWidget(QLabel("プリセット"))
        top.addWidget(self.preset_combo)
        top.addWidget(self.preset_mode)
        top.addWidget(self.preset_btn)
        top.addStretch(1)
        self.check_btn = QPushButton("検査(check-case)")
        self.save_btn = primary_button("保存")
        self.saveas_btn = QPushButton("名前を付けて保存…")
        top.addWidget(self.check_btn)
        top.addWidget(self.save_btn)
        top.addWidget(self.saveas_btn)
        root.addLayout(top)
        self.path_label = muted("", wrap=False)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.path_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # 左: 入力欄
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_host = QWidget()
        grid = QGridLayout(form_host)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        scroll.setWidget(form_host)
        split.addWidget(scroll)

        # [case]
        g_case = QGroupBox("ケース [case]")
        f = QFormLayout(g_case)
        self.name_edit = QLineEdit()
        self.mesh_field = PathField("open", "メッシュ (*.pmesh);;すべてのファイル (*)", "メッシュを選ぶ",
                                    start_dir=lambda: str(tube_mesh_dir(self.ctx.root)))
        self.mesh_info = muted("")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("gsis(GSIS-PBC)", "gsis")
        self.mode_combo.addItem("cis(CIS 単独)", "cis")
        f.addRow("名前 name", self.name_edit)
        f.addRow("メッシュ mesh", self.mesh_field)
        f.addRow("", self.mesh_info)
        f.addRow("モード mode", self.mode_combo)
        grid.addWidget(g_case, 0, 0)

        # [gas]
        g_gas = QGroupBox("気体 [gas]")
        f = QFormLayout(g_gas)
        self.m_field = FloatField(positive=True)
        self.mu_field = FloatField(positive=True)
        self.Tref_field = FloatField(positive=True)
        self.omega_field = FloatField(positive=True)
        he = QPushButton("He の値を入れる")
        he.clicked.connect(self._fill_helium)
        f.addRow("分子質量 m [kg]", self.m_field)
        f.addRow("粘性 μ_ref [Pa s]", self.mu_field)
        f.addRow("基準温度 T_ref [K]", self.Tref_field)
        f.addRow("べき指数 ω", self.omega_field)
        f.addRow("", he)
        grid.addWidget(g_gas, 1, 0)

        # [conditions]
        g_cond = QGroupBox("条件 [conditions]")
        f = QFormLayout(g_cond)
        self.L_field = FloatField(positive=True)
        self.T0_field = FloatField(positive=True)
        self.ref_kind = QComboBox()
        self.ref_kind.addItem("δ_rp(希薄度。p_in をこれから決める)", "delta_rp")
        self.ref_kind.addItem("Kn(式 (8)、基準状態)", "Kn")
        self.ref_kind.addItem("p_ref [Pa](直接)", "p_ref")
        self.ref_value = FloatField(positive=True)
        f.addRow("代表長さ L_ref [m]", self.L_field)
        f.addRow("温度 T0 [K]", self.T0_field)
        f.addRow("基準の与え方", self.ref_kind)
        f.addRow("値", self.ref_value)
        grid.addWidget(g_cond, 2, 0)

        # [gsis]
        g_gsis = QGroupBox("GSIS [gsis]")
        f = QFormLayout(g_gsis)
        self.ncis_spin = QSpinBox()
        self.ncis_spin.setRange(1, 1_000_000)
        self.maxpbc_spin = QSpinBox()
        self.maxpbc_spin.setRange(1, 1_000_000)
        self.cfl_field = FloatField(positive=True)
        self.tol_field = FloatField(positive=True)
        f.addRow("CIS の反復数 n_cis", self.ncis_spin)
        f.addRow("PBC の反復の上限 max_pbc", self.maxpbc_spin)
        f.addRow("PBC の CFL cfl_pbc", self.cfl_field)
        f.addRow("収束判定 tolerance", self.tol_field)
        f.addRow("", muted("既定値(5、10、500、1e-5)と同じで元のファイルにない項目は書かない"))
        grid.addWidget(g_gsis, 3, 0)

        # [[boundary]]
        g_bnd = QGroupBox("境界条件 [[boundary]]")
        v = QVBoxLayout(g_bnd)
        self.boundary_table = BoundaryTable()
        v.addWidget(self.boundary_table)
        grid.addWidget(g_bnd, 0, 1)

        # [velocity_grid]
        g_vel = QGroupBox("速度格子 [velocity_grid](単位 √(R T0))")
        gl = QGridLayout(g_vel)
        gl.addWidget(QLabel(""), 0, 0)
        for j, t in enumerate(("点数 n", "下限", "上限")):
            gl.addWidget(muted(t, wrap=False), 0, j + 1)
        self.vel_n: list[QSpinBox] = []
        self.vel_lo: list[FloatField] = []
        self.vel_hi: list[FloatField] = []
        for a, axis in enumerate("xyz"):
            gl.addWidget(QLabel(f"ξ{axis}"), a + 1, 0)
            sp = QSpinBox()
            sp.setRange(1, 1000)
            lo, hi = FloatField(), FloatField()
            gl.addWidget(sp, a + 1, 1)
            gl.addWidget(lo, a + 1, 2)
            gl.addWidget(hi, a + 1, 3)
            self.vel_n.append(sp)
            self.vel_lo.append(lo)
            self.vel_hi.append(hi)
        self.vel_info = muted("")
        gl.addWidget(self.vel_info, 4, 0, 1, 4)
        grid.addWidget(g_vel, 1, 1)

        # [kinetic] / [macro] / [output]
        g_mod = QGroupBox("ソルバーのパラメータ [kinetic]・[macro]・[output]")
        v = QVBoxLayout(g_mod)
        self.module_tabs = QTabWidget()
        self.kinetic_table = KeyValueTable(KNOWN_MODULE_KEYS["kinetic"])
        self.macro_table = KeyValueTable(KNOWN_MODULE_KEYS["macro"])
        self.output_table = KeyValueTable(KNOWN_MODULE_KEYS["output"])
        self.module_tabs.addTab(self.kinetic_table, "[kinetic] 運動論")
        self.module_tabs.addTab(self.macro_table, "[macro] PBC")
        self.module_tabs.addTab(self.output_table, "[output]")
        v.addWidget(self.module_tabs)
        v.addWidget(muted("書いたキーだけがソルバーの既定値を上書きする。知らないキーはソルバーがエラーにする(黄色)。"))
        grid.addWidget(g_mod, 2, 1, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(4, 1)

        # 右: 導出量と検査の結果
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        g_der = QGroupBox("導出量(画面で計算。C++ の check-case と同じ式)")
        dv = QVBoxLayout(g_der)
        self.derived_table = QTableWidget(0, 2)
        self.derived_table.setHorizontalHeaderLabels(["量", "値"])
        self.derived_table.verticalHeader().setVisible(False)
        self.derived_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.derived_table.horizontalHeader().setStretchLastSection(True)
        self.derived_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        dv.addWidget(self.derived_table)
        rv.addWidget(g_der, 2)
        g_chk = QGroupBox("検査(入力欄 → gsis-solver check-case)")
        cv = QVBoxLayout(g_chk)
        self.validate_label = StatusLabel("")
        self.check_status = StatusLabel("まだ検査していません")
        self.check_log = LogView(max_lines=2000)
        cv.addWidget(self.validate_label)
        cv.addWidget(self.check_status)
        cv.addWidget(self.check_log, 1)
        rv.addWidget(g_chk, 3)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([980, 560])

        # 変更の通知
        for w in (self.name_edit,):
            w.textChanged.connect(self._on_edit)
        self.mesh_field.changed.connect(self._on_mesh_changed)
        for w in (self.m_field, self.mu_field, self.Tref_field, self.omega_field, self.L_field, self.T0_field,
                  self.ref_value, self.cfl_field, self.tol_field, *self.vel_lo, *self.vel_hi):
            w.valueChanged.connect(self._on_edit)
        for w in (self.mode_combo, self.ref_kind):
            w.currentIndexChanged.connect(self._on_edit)
        for w in (self.ncis_spin, self.maxpbc_spin, *self.vel_n):
            w.valueChanged.connect(self._on_edit)
        self.boundary_table.changed.connect(self._on_edit)
        for t in (self.kinetic_table, self.macro_table, self.output_table):
            t.changed.connect(self._on_edit)
        self.boundary_table.from_mesh_btn.clicked.connect(self._boundaries_from_mesh)
        self.open_btn.clicked.connect(self._open_dialog)
        self.new_btn.clicked.connect(self._new_case)
        self.preset_btn.clicked.connect(self._apply_preset)
        self.check_btn.clicked.connect(lambda: self.run_check())
        self.save_btn.clicked.connect(lambda: self.save())
        self.saveas_btn.clicked.connect(lambda: self.save(ask=True))

    # ------------------------------------------------------------------ 状態
    def current_path(self) -> Path | None:
        return self._path

    def is_dirty(self) -> bool:
        return self._dirty

    def case_directory(self) -> Path | None:
        return None if self._path is None else self._path.parent

    def set_model(self, m: CaseModel, path: Path | None, doc=None, clear_check: bool = True) -> None:
        self._loading = True
        try:
            self._path = path
            self._doc = doc
            self._orig_gsis_keys = set(m.gsis) if doc is not None else set(GSIS_DEFAULTS)
            self.name_edit.setText(m.name)
            self.mesh_field.setText(m.mesh)
            self.mode_combo.setCurrentIndex(max(self.mode_combo.findData(m.mode), 0))
            self.m_field.setValue(m.molecular_mass)
            self.mu_field.setValue(m.mu_ref)
            self.Tref_field.setValue(m.T_ref)
            self.omega_field.setValue(m.omega)
            self.L_field.setValue(m.L_ref)
            self.T0_field.setValue(m.T0)
            self.ref_kind.setCurrentIndex(max(self.ref_kind.findData(m.ref_kind), 0))
            self.ref_value.setValue(m.ref_value)
            self.boundary_table.set_boundaries([b.copy() for b in m.boundaries])
            for a in range(3):
                self.vel_n[a].setValue(int(m.vel_n[a]))
                self.vel_lo[a].setValue(m.vel_range[a][0])
                self.vel_hi[a].setValue(m.vel_range[a][1])
            self.ncis_spin.setValue(int(m.gsis_value("n_cis")))
            self.maxpbc_spin.setValue(int(m.gsis_value("max_pbc")))
            self.cfl_field.setValue(float(m.gsis_value("cfl_pbc")))
            self.tol_field.setValue(float(m.gsis_value("tolerance")))
            self.kinetic_table.set_values(m.kinetic)
            self.macro_table.set_values(m.macro)
            self.output_table.set_values(m.output)
        finally:
            self._loading = False
        self._dirty = False
        self._on_mesh_changed()
        self._dirty = False
        self._refresh()
        if clear_check:
            self.check_status.set_status(None, "まだ検査していません")
            self.check_log.clear_log()

    def collect_model(self) -> CaseModel:
        """入力欄からモデルを作る(数値として読めない欄は ValueError)。"""
        m = CaseModel()
        m.name = self.name_edit.text().strip()
        m.mesh = self.mesh_field.text().replace("\\", "/")
        m.mode = self.mode_combo.currentData()

        def val(field: FloatField, name: str) -> float:
            if not field.is_valid() or field.value() is None:
                raise ValueError(f"{name} の値が正しくありません: {field.text()!r}")
            return float(field.value())

        m.molecular_mass = val(self.m_field, "分子質量 m")
        m.mu_ref = val(self.mu_field, "μ_ref")
        m.T_ref = val(self.Tref_field, "T_ref")
        m.omega = val(self.omega_field, "ω")
        m.L_ref = val(self.L_field, "L_ref")
        m.T0 = val(self.T0_field, "T0")
        m.ref_kind = self.ref_kind.currentData()
        m.ref_value = val(self.ref_value, "基準の値")
        m.boundaries = self.boundary_table.boundaries()
        m.vel_n = [sp.value() for sp in self.vel_n]
        m.vel_range = [[val(self.vel_lo[a], f"速度格子の下限({a})"), val(self.vel_hi[a], f"速度格子の上限({a})")]
                       for a in range(3)]
        gs = {"n_cis": self.ncis_spin.value(), "max_pbc": self.maxpbc_spin.value(),
              "cfl_pbc": val(self.cfl_field, "cfl_pbc"), "tolerance": val(self.tol_field, "tolerance")}
        m.gsis = {k: v for k, v in gs.items() if k in self._orig_gsis_keys or v != GSIS_DEFAULTS[k]}
        m.kinetic = self.kinetic_table.values()
        m.macro = self.macro_table.values()
        m.output = self.output_table.values()
        return m

    # ------------------------------------------------------------------ 読み込み・新規・プリセット
    def load_file(self, path: str | Path) -> bool:
        path = Path(path)
        try:
            cd = casefile.load_case(path)
        except (OSError, CaseFileError) as exc:
            QMessageBox.warning(self, "ケースを開けません", f"{path}\n\n{exc}")
            return False
        self.set_model(cd.model, path=path.resolve(), doc=cd.doc)
        self.ctx.add_recent("case", path.resolve())
        self.status.emit(f"ケースを開きました: {display_path(path, self.ctx.root)}")
        self.case_loaded.emit(str(path.resolve()))
        return True

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        r = QMessageBox.question(self, "変更を捨てますか", "保存していない変更があります。捨てて続けますか?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    def _open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        start = str(self.case_directory() or (self.ctx.root / "cases"))
        p, _ = QFileDialog.getOpenFileName(self, "ケースを開く", start, TOML_FILTER)
        if p:
            self.load_file(p)

    def _new_case(self) -> None:
        if not self._confirm_discard():
            return
        self.set_model(casefile.tube_preset(10.0, mode=self.preset_mode.currentData()), path=None, doc=None)
        self.status.emit("新しいケース(He 管 δ_rp = 10 を元にした。保存するまでファイルはない)")

    def _apply_preset(self) -> None:
        if not self._confirm_discard():
            return
        d = float(self.preset_combo.currentData())
        mode = self.preset_mode.currentData()
        self.apply_preset(d, mode)

    def apply_preset(self, delta: float, mode: str = "gsis") -> None:
        self.set_model(casefile.tube_preset(delta, mode=mode), path=None, doc=None)
        self._dirty = True
        self._refresh()
        self.status.emit(f"プリセット {casefile.preset_label(delta)}({mode})を入れました。保存先を選んで保存してください")

    def _fill_helium(self) -> None:
        self.m_field.setValue(HELIUM["molecular_mass"])
        self.mu_field.setValue(HELIUM["mu_ref"])
        self.Tref_field.setValue(HELIUM["T_ref"])
        self.omega_field.setValue(HELIUM["omega"])
        self._on_edit()

    def set_mesh(self, mesh_path: str | Path) -> None:
        """メッシュの画面から: このケースのメッシュにする(TOML の場所からの相対パスで書く)。"""
        p = Path(mesh_path).resolve()
        base = self.case_directory()
        self.mesh_field.setText(to_posix_relative(p, base) if base else p.as_posix())

    # ------------------------------------------------------------------ 表示の更新
    def _on_edit(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._refresh()

    def _resolved_mesh(self) -> Path | None:
        s = self.mesh_field.text()
        if not s:
            return None
        return casefile.resolve_mesh(s, self.case_directory() or self.ctx.root)

    def _on_mesh_changed(self, *_args) -> None:
        p = self._resolved_mesh()
        self._mesh_cells = None
        self._mesh_patches = []
        if p is None:
            self.mesh_info.setText("メッシュを指定してください")
        elif not p.is_file():
            self.mesh_info.setText(f"見つかりません: {p}")
        else:
            try:
                from ...pmesh import read_header  # gsis_tools.pmesh

                h = read_header(p)
                self._mesh_cells = int(h.get("n_cells", 0))
                self._mesh_patches = [(str(q["name"]), str(q.get("kind", ""))) for q in h.get("patches", [])]
                names = " / ".join(n for n, _ in self._mesh_patches)
                self.mesh_info.setText(f"{self._mesh_cells:,} セル、パッチ: {names}")
            except Exception as exc:
                self.mesh_info.setText(f"読めません: {exc}")
        self._on_edit()

    def _boundaries_from_mesh(self) -> None:
        if not self._mesh_patches:
            QMessageBox.information(self, "メッシュのパッチ", "メッシュのパッチが読めていません。メッシュのパスを確かめてください。")
            return
        T0 = self.T0_field.value() or 273.15
        items = []
        for name, kind in self._mesh_patches:
            key = (kind or name).lower()
            if "inlet" in key:
                items.append(Boundary(name, "pressure_inlet", T0))
            elif "outlet" in key:
                items.append(Boundary(name, "vacuum"))
            elif "sym" in key:
                items.append(Boundary(name, "symmetry"))
            else:
                items.append(Boundary(name, "diffuse_wall", T0))
        self.boundary_table.set_boundaries(items)
        self._on_edit()

    def _refresh(self) -> None:
        name = display_path(self._path, self.ctx.root) if self._path else "(保存していない新しいケース)"
        self.path_label.setText(f"ファイル: {name}" + ("  — 変更あり" if self._dirty else ""))
        try:
            m = self.collect_model()
        except ValueError as exc:
            self.validate_label.set_status("error", f"入力の誤り: {exc}")
            self._fill_derived(None)
            return
        errs = casefile.validate(m)
        mesh = self._resolved_mesh()
        if mesh is not None and not mesh.is_file():
            errs.append(f"メッシュが見つかりません: {mesh}")
        missing = [b.patch for b in m.boundaries if self._mesh_patches and b.patch not in {n for n, _ in self._mesh_patches}]
        if missing:
            errs.append("メッシュにないパッチ: " + ", ".join(missing))
        unset = [n for n, _ in self._mesh_patches if n not in {b.patch for b in m.boundaries}]
        if unset:
            errs.append("境界条件のないパッチ: " + ", ".join(unset))
        probs = [f"[{s}] {p}" for s, t in (("kinetic", self.kinetic_table), ("macro", self.macro_table),
                                            ("output", self.output_table)) for p in t.problems()]
        if errs:
            self.validate_label.set_status("error", "入力の問題: " + " / ".join(errs))
        elif probs:
            self.validate_label.set_status("warning", "注意: " + " / ".join(probs))
        else:
            self.validate_label.set_status("ok", "入力欄の検査: 問題なし(最終的な判断は check-case)")
        self._fill_derived(m)
        dxi = casefile.velocity_spacing(m)
        self.vel_info.setText(
            f"速度点 {casefile.velocity_points(m):,} 点、刻み Δξ = ({', '.join(f'{v:.4g}' for v in dxi)})(中点配置)")

    def _fill_derived(self, m: CaseModel | None) -> None:
        rows: list[tuple[str, str]] = []
        if m is not None:
            try:
                d = casefile.derive(m)
                rows += [
                    ("比気体定数 R = k_B/m", f"{d.R:.6g} J/(kg K)"),
                    ("粘性 μ0 = μ(T0)", f"{d.mu0:.6g} Pa s"),
                    ("√(2 R T0)", f"{d.most_probable_speed:.6g} m/s"),
                    ("p_in(= p_ref)", f"{d.p_ref:.6g} Pa"),
                    ("密度 ρ_in = p_in/(R T0)", f"{d.rho_ref:.6g} kg/m³"),
                    ("Kn(式 (8)、基準状態)", f"{d.Kn:.6g}"),
                    ("δ = √π/(2 Kn)", f"{d.delta:.6g}"),
                    ("平均自由行程 λ = Kn L_ref", f"{d.mean_free_path:.6g} m"),
                    ("質量流量の基準 √π L² p_in/√(2RT0)", f"{d.mass_flow_scale:.6g} kg/s"),
                ]
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                rows.append(("導出量", f"計算できません: {exc}"))
            nv = casefile.velocity_points(m)
            rows.append(("速度点", f"{nv:,}"))
            if self._mesh_cells:
                f32 = casefile.distribution_bytes(m, self._mesh_cells, "fp32")
                f64 = casefile.distribution_bytes(m, self._mesh_cells, "fp64")
                rows.append(("分布関数 f(1 本)", f"FP32 {_bytes_text(f32)}、FP64 {_bytes_text(f64)}"))
        self.derived_table.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            self.derived_table.setItem(r, 0, readonly_item(k))
            self.derived_table.setItem(r, 1, readonly_item(v))

    def _restyle(self, scheme: str) -> None:
        t = self.ctx.theme.tokens()
        self.check_log.restyle(t)
        for tb in (self.kinetic_table, self.macro_table, self.output_table):
            tb.restyle(t)

    # ------------------------------------------------------------------ 検査と保存
    def _target_dir_for_check(self, target: Path | None) -> Path:
        if target is not None:
            return target.parent
        return self.case_directory() or self.ctx.root

    def run_check(self, save_to: Path | None = None) -> bool:
        """check-case を非同期で実行する(``save_to`` があれば、終わったあと保存する)。始められなければ False。"""
        if self._proc is not None:
            return False
        try:
            m = self._model_for(save_to)
        except ValueError as exc:
            self.check_status.set_status("error", f"入力の誤り: {exc}")
            return False
        errs = casefile.validate(m)
        if errs:
            self.check_status.set_status("error", "入力欄に誤りがあるので check-case を実行しませんでした: " + " / ".join(errs))
            if save_to is not None and self._ask_save_anyway("入力欄に誤りがあります。"):
                self._write(save_to, m)
            return False
        if not self.ctx.solver_ok():
            self.check_status.set_status("warning", f"ソルバーが見つからないので検査できません: {self.ctx.solver_path()}")
            if save_to is not None and self._ask_save_anyway("ソルバーで検査できませんでした。"):
                self._write(save_to, m)
            return False
        # 一時ファイル(メッシュは絶対パスにする)
        target_dir = self._target_dir_for_check(save_to)
        tm = m.copy()
        tm.mesh = casefile.resolve_mesh(m.mesh, target_dir).as_posix()
        self._tmpdir = tempfile.mkdtemp(prefix="gsis_gui_check_")
        tmp = Path(self._tmpdir) / f"{m.name or 'case'}.toml"
        tmp.write_text(casefile.render_case(tm, self._doc), encoding="utf-8", newline="\n")
        self._check_tmp = tmp
        self._check_display = str(save_to or self._path or "(新しいケース)")
        self._pending_save = save_to
        self._pending_model = m
        self.check_log.clear_log()
        self.check_status.set_status("info", "check-case を実行しています…")
        self._out_buf = b""
        proc = QProcess(self)
        proc.setWorkingDirectory(str(self.ctx.root))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._read_check(proc))
        proc.finished.connect(lambda code, _st: self._check_finished(code))
        proc.errorOccurred.connect(lambda _e: self._check_error(proc))
        self._proc = proc
        self.check_btn.setEnabled(False)
        proc.start(str(self.ctx.solver_path()), ["check-case", str(tmp)])
        return True

    def _read_check(self, proc: QProcess) -> None:
        self._out_buf += bytes(proc.readAllStandardOutput())

    def _check_error(self, proc: QProcess) -> None:
        if proc.error() == QProcess.ProcessError.FailedToStart:
            self.check_status.set_status("error", f"ソルバーを起動できません: {proc.errorString()}")
            self._cleanup_check()

    def _check_finished(self, code: int) -> None:
        if self._proc is not None:
            self._out_buf += bytes(self._proc.readAllStandardOutput())
        text = self._out_buf.decode("utf-8", errors="replace").replace(str(self._check_tmp), self._check_display)
        self.check_log.append_lines(text)
        save_to, m = self._pending_save, self._pending_model
        self._cleanup_check()
        if code == 0:
            self.check_status.set_status("ok", "check-case: 問題なし(終了コード 0)")
            if save_to is not None:
                self._write(save_to, m)
        else:
            self.check_status.set_status("error", f"check-case: {explain_failure(code, text)}(終了コード {code})")
            if save_to is not None and self._ask_save_anyway("check-case でエラーが出ました。"):
                self._write(save_to, m)

    def _cleanup_check(self) -> None:
        self._proc = None
        self.check_btn.setEnabled(True)
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def _ask_save_anyway(self, why: str) -> bool:
        r = QMessageBox.question(self, "保存しますか", f"{why}\nそれでも保存しますか?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    def save(self, ask: bool = False, path: str | Path | None = None, check: bool = True) -> None:
        """保存する(既定では check-case を通してから)。"""
        target = Path(path) if path else self._path
        if ask or target is None:
            start = str(self._path or (self.ctx.root / "cases" / "tube_vacuum" / f"{self.name_edit.text() or 'case'}.toml"))
            p, _ = QFileDialog.getSaveFileName(self, "ケースを保存", start, TOML_FILTER)
            if not p:
                return
            target = Path(p)
        target = target.resolve()
        if check:
            self.run_check(save_to=target)  # 終わったら保存する(始められないときは中で保存するか尋ねる)
        else:
            try:
                m = self._model_for(target)
            except ValueError as exc:
                QMessageBox.warning(self, "保存できません", f"入力の誤り: {exc}")
                return
            self._write(target, m)

    def _model_for(self, target: Path | None) -> CaseModel:
        """入力欄のモデル。``target`` に保存するときは、メッシュの相対パスを新しい場所からのパスに直す
        (入力欄は保存した後に書き換わる。保存をやめたときは元のまま)。"""
        m = self.collect_model()
        if target is not None and m.mesh:
            old_dir = self.case_directory()
            if old_dir is None or old_dir != target.parent:
                m.mesh = casefile.rebase_mesh(m.mesh, old_dir if old_dir is not None else self.ctx.root, target.parent)
        return m

    def _write(self, target: Path, m: CaseModel) -> None:
        try:
            cd = casefile.save_case(target, m, self._doc)  # 元の文書があればコメントを保つ
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存できません", f"{target}\n\n{exc}")
            return
        self.set_model(cd.model, path=target, doc=cd.doc, clear_check=False)
        self._orig_gsis_keys = set(cd.model.gsis)
        self.check_status.set_status("ok", f"保存しました: {display_path(target, self.ctx.root)}")
        self.ctx.add_recent("case", target)
        self.status.emit(f"ケースを保存しました: {display_path(target, self.ctx.root)}")
        self.case_saved.emit(str(target))
