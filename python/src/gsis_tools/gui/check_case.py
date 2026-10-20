"""S2 の入力検査。入力の写しを非同期に検査し、古い要求の結果を捨てる。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import tomlkit
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QPlainTextEdit

from .general_settings import velocity_grid
from .project import case_document

TET_WARNING = "四面体の GSIS は検証中(流量がずれることがある)"


def grid_warnings(settings, result):
    grid = result.get("velocity_grid")
    if not grid:
        return []
    n, ranges = velocity_grid(settings)
    if n != grid.get("n") or not np.allclose(ranges, grid.get("range"), rtol=2e-13, atol=2e-13):
        return ["速度格子が Python の概算と C++ で食い違っています。C++ の値を使用します"]
    return []


class CheckCaseJob(QObject):
    completed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self.work = None
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._timed_out)

    def start(self, exe, project, *, no_mesh=False, precision=None):
        self.cancel()
        self.work = tempfile.TemporaryDirectory(prefix="gsis_check_")
        doc = case_document(project)
        doc["case"]["mesh"] = str(project.asset(project.mesh["path"]))
        if precision:
            doc["kinetic"]["precision"] = precision
        case = Path(self.work.name) / "case.toml"
        case.write_text(tomlkit.dumps(doc), encoding="utf-8")
        proc = QProcess(self)
        self.proc = proc
        # Windows では終了シグナル中も子の cwd のハンドルが残るため、削除する一時領域を cwd にしない。
        proc.setWorkingDirectory(str(project.directory))
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._error)
        # CPU の入力検査で CUDA を初期化しない。空きの仮定は GUI の明示入力を使う。
        args = ["check-case", str(case), "--json", "--free-vram-bytes", str(int(project.settings.free_vram_gib * 2**30))]
        if no_mesh:
            args.append("--no-mesh")
        self.command = [str(exe), *args]
        proc.start(str(exe), args)
        self.timeout.start(30000)

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._deliver(dict(ok=False, errors=[self.proc.errorString()], warnings=[]))

    def _timed_out(self):
        self.cancel()
        self.completed.emit(dict(ok=False, errors=["check-case が30秒以内に終わりませんでした"], warnings=[]))

    def _finished(self, code, _status):
        try:
            result = json.loads(bytes(self.proc.readAllStandardOutput()).decode("utf-8"))
            if not isinstance(result, dict) or result.get("schema_version") != 1:
                raise ValueError("対応していない check-case JSON です")
            if code != 0 and result.get("ok"):
                raise ValueError(f"check-case が終了コード {code} で停止しました")
        except (ValueError, UnicodeError) as exc:
            result = dict(ok=False, errors=[f"check-case の出力を読めません: {exc}"], warnings=[])
        self._deliver(result)

    def _deliver(self, result):
        self.cancel()
        self.completed.emit(result)

    def cancel(self):
        self.timeout.stop()
        if self.proc is not None:
            proc, self.proc = self.proc, None
            proc.finished.disconnect()
            proc.errorOccurred.disconnect()
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(3000)
            proc.deleteLater()
        if self.work is not None:
            self.work.cleanup()
            self.work = None


class CheckCasePanel(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumHeight(180)
        self.setPlainText("C++ 入力検査: 設定の編集後に非同期で更新します")

    def present(self, result):
        def fmt(value):
            return "未計算" if value is None else f"{value:.6g}" if isinstance(value, float) else str(value)

        lines = ["C++ check-case / " + ("メッシュ込み" if result.get("mesh_loaded") else "メッシュ前")]
        for key, label in (("R_J_kg_K", "R [J/(kg K)]"), ("mu_Pa_s", "μ [Pa s]"),
                           ("mean_free_path_m", "平均自由行程 [m]"), ("Kn", "Kn"), ("delta_rp", "δ_rp")):
            if key in result.get("derived", {}):
                lines.append(f"{label}: {fmt(result['derived'][key])}")
        grid = result.get("velocity_grid")
        if grid:
            lines.extend([f"速度格子: {grid['n']} / {grid['n_velocity']} 点", f"範囲 / sqrt(R T0): {grid['range']}"])
        memory = result.get("memory")
        if memory:
            lines.append(f"VRAM 確定見積もり(C++): {memory['total']/2**30:.4g} GiB / 予算 {memory['budget']/2**30:.4g} GiB"
                         f" / チャンク {memory['chunk']} / {'予算内' if memory['fits'] else '予算超過'}")
            lines.append("f / 作業領域 / 固定費 [bytes]: " + " / ".join(str(memory[k]) for k in ("distribution", "workspace", "overhead")))
            lines.append(f"モデル: {memory['model']} / 空きは設定の仮定: {memory.get('free', 0)/2**30:.4g} GiB")
        else:
            lines.append("VRAM: メッシュ前のため未確定。上の Python 値は概算です")
        for p in result.get("patches", []):
            lines.append(f"パッチ {p['name']} / {p['type']} / {fmt(p.get('faces'))} 面 / {fmt(p.get('area_m2'))} m²")
            if p.get("accommodation") is not None:
                lines.append(f"  適応係数 α: {fmt(p['accommodation'])}")
            if p.get("target_mass_flow_kg_s") is not None:
                lines.append(f"  目標流量: {fmt(p['target_mass_flow_kg_s'])} kg/s (流入正)"
                             f" / 初期 p_b = p_ref: {fmt(p.get('pressure_Pa'))} Pa"
                             f" / 接線速度: {p.get('tangential_velocity')}")
        lines.extend("警告: " + str(x) for x in result.get("warnings", []))
        lines.extend("エラー: " + str(x) for x in result.get("errors", []))
        self.setPlainText("\n".join(lines))
