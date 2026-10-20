"""``history.csv``(ソルバーの反復の履歴)の解析と追いかけ読み。

列の形はサブコマンドで違う(``src/app/cmd_cis_run.cpp``・``cmd_pbc_run.cpp`` の冒頭):

- ``cis-run``: ``iteration, epsilon, eps_rho, eps_u, eps_T, residual, ..., mdot_<patch>_kg_s, mdot_<patch>_nd, ...,
  time_iter_s, time_total_s, ...``
- ``pbc-run``: ``iteration, cfl, residual_mass, ..., update_p, update_u, update_T, ..., mdot_<patch>, seconds,
  elapsed_seconds``(``mdot_<patch>`` は無次元)
- ``run``(フェーズ 4): ``iteration, cis_iterations, epsilon, ..., mdot_<patch>_kg_s, mdot_<patch>_nd,
  mdot_pbc_<patch>_nd, M_dot_inlet, ...``(2026-10-16 の build/phase4 の出力で確認)。``mdot_pbc_<patch>_nd`` は
  PBC 側の質量流量で、運動論側(``mdot_<patch>_nd``)とは別の系列として扱う

質量流量の無次元量はソルバーの単位(ρ_ref √(RT0) L_ref²)で、外向きが正。論文の無次元質量流量
Ṁ = ṁ / (√π L_ref² p_in / √(2RT0)) には :class:`~gsis_tools.gui.casefile.Derived` の係数で直す。

Qt には依存しない。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KIND_KINETIC = "kinetic"
KIND_MACRO = "macro"
KIND_GSIS = "gsis"
KIND_UNKNOWN = "unknown"

KIND_LABELS = {
    KIND_KINETIC: "運動論ソルバー CIS(cis-run)",
    KIND_MACRO: "PBC マクロソルバー(pbc-run)",
    KIND_GSIS: "GSIS-PBC(run)",
    KIND_UNKNOWN: "不明な形式",
}

# 列の説明(グラフの凡例・列の選択に出す)
COLUMN_LABELS = {
    "epsilon": "ε(式 (59))",
    "eps_rho": "ε_ρ",
    "eps_u": "ε_u",
    "eps_T": "ε_T",
    "residual": "残差",
    "residual_rel": "残差(初回比)",
    "residual_mass": "残差(質量)",
    "residual_momentum": "残差(運動量)",
    "residual_energy": "残差(エネルギー)",
    "residual_temperature": "残差(温度)",
    "update_p": "更新量 p",
    "update_u": "更新量 u",
    "update_T": "更新量 T",
    "cfl": "CFL",
}

_MDOT_ND = re.compile(r"^mdot_(.+)_nd$")
_MDOT_SI = re.compile(r"^mdot_(.+)_kg_s$")
_MDOT_PLAIN = re.compile(r"^mdot_(.+)$")


def _to_float(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return math.nan


@dataclass(frozen=True)
class MassFlowColumn:
    """パッチの質量流量の列。``unit`` は ``"nd"``(ソルバーの無次元量)か ``"kg_s"``。

    ``source`` は ``"main"``(運動論側、または単独のソルバー)か ``"pbc"``(GSIS の PBC 側)。
    """

    patch: str
    column: str
    unit: str
    source: str = "main"


class History:
    """読み込んだ履歴(列名 → float の配列)。"""

    def __init__(self, columns: list[str], rows: np.ndarray):
        self.columns = list(columns)
        rows = np.asarray(rows, dtype=float).reshape(-1, len(self.columns)) if self.columns else np.zeros((0, 0))
        self._rows = rows
        self.data = {c: rows[:, i] for i, c in enumerate(self.columns)}

    @property
    def n(self) -> int:
        return int(self._rows.shape[0])

    def __len__(self) -> int:
        return self.n

    def has(self, name: str) -> bool:
        return name in self.data

    def column(self, name: str) -> np.ndarray:
        return self.data[name]

    @property
    def kind(self) -> str:
        c = set(self.columns)
        if {"epsilon", "residual_rel"} <= c and "time_iter_s" in c:
            return KIND_KINETIC
        if {"update_p", "residual_mass"} <= c and "epsilon" not in c:
            return KIND_MACRO
        if "epsilon" in c:
            return KIND_GSIS
        return KIND_UNKNOWN

    def iterations(self) -> np.ndarray:
        if "iteration" in self.data:
            return self.data["iteration"]
        return np.arange(self.n, dtype=float)

    def numeric_columns(self) -> list[str]:
        """グラフに描ける列(反復番号以外で、有限な値が 1 つ以上ある列)。"""
        out = []
        for c in self.columns:
            if c == "iteration":
                continue
            v = self.data[c]
            if v.size and np.any(np.isfinite(v)):
                out.append(c)
        return out

    def convergence_columns(self) -> list[str]:
        """収束のグラフに既定で描く列。"""
        c = self.columns
        if "epsilon" in c:
            return ["epsilon"]
        if "update_p" in c:
            return [k for k in ("update_p", "update_u", "update_T", "residual_mass") if k in c]
        return [k for k in c if "eps" in k or "residual" in k or "update" in k][:4]

    def convergence_candidates(self) -> list[str]:
        """収束のグラフに描ける列(残差・更新量・ε の類)。"""
        keys = ("eps", "residual", "update", "defect", "linear_residual")
        return [k for k in self.numeric_columns() if any(s in k for s in keys)]

    def mass_flow_columns(self) -> list[MassFlowColumn]:
        """パッチごとの質量流量の列(無次元の列があればそれを、なければ kg/s の列を使う)。"""
        nd: dict[str, str] = {}
        si: dict[str, str] = {}
        plain: dict[str, str] = {}
        order: list[str] = []
        for col in self.columns:
            for rx, dst in ((_MDOT_ND, nd), (_MDOT_SI, si)):
                m = rx.match(col)
                if m:
                    dst[m.group(1)] = col
                    if m.group(1) not in order:
                        order.append(m.group(1))
                    break
            else:
                m = _MDOT_PLAIN.match(col)
                if m:
                    plain[m.group(1)] = col
                    if m.group(1) not in order:
                        order.append(m.group(1))
        out = []
        # mdot_pbc_<patch>_nd は、<patch> が別にあれば GSIS の PBC 側の流量(パッチ名 pbc_<patch> とはみなさない)
        main = {p for p in order if not (p.startswith("pbc_") and p[4:] in order)}
        for p in order:
            source, name = ("main", p) if p in main else ("pbc", p[4:])
            if p in nd:
                out.append(MassFlowColumn(name, nd[p], "nd", source))
            elif p in plain:
                out.append(MassFlowColumn(name, plain[p], "nd", source))
            elif p in si:
                out.append(MassFlowColumn(name, si[p], "kg_s", source))
        return out

    def elapsed_seconds(self) -> np.ndarray | None:
        for k in ("time_total_s", "elapsed_seconds", "elapsed_s"):
            if k in self.data:
                return self.data[k]
        return None

    def inlet_pressure_columns(self) -> dict[str, str]:
        """S3 の入口圧力(Pa)。パッチ名に _ や - があっても全体を保つ。"""
        return {c[4:-3]: c for c in self.columns if c.startswith("p_b_") and c.endswith("_Pa") and c[4:-3]}

    def last(self, name: str) -> float:
        """列の最後の有限な値(なければ nan)。"""
        if name not in self.data:
            return math.nan
        v = self.data[name]
        ok = np.flatnonzero(np.isfinite(v))
        return float(v[ok[-1]]) if ok.size else math.nan


def read_history(path: str | Path) -> History:
    """``history.csv`` を全部読む。"""
    tail = HistoryTail(path)
    tail.poll()
    return tail.history()


class HistoryTail:
    """書き込み中の ``history.csv`` を少しずつ読む(ソルバーは 1 反復ごとに 1 行を足して flush する)。

    - 前回の位置から後ろだけを読み、行の途中で切れた部分は次回に回す
    - ファイルが短くなった・先頭(見出しと最初の行)が変わった → 作り直されたとみなして読み直す
    - ``min_mtime`` より古いファイルは読まない(実行を始める前に残っていた前回の履歴を拾わないため)
    """

    def __init__(self, path: str | Path, min_mtime: float | None = None):
        self.path = Path(path)
        self.min_mtime = min_mtime
        self.reset()

    def reset(self) -> None:
        self._offset = 0
        self._buf = b""
        self._head = b""  # 見出しと最初のデータ行(作り直しの検出用)
        self.columns: list[str] | None = None
        self._rows: list[list[float]] = []
        self._cache: History | None = None
        self.generation = getattr(self, "generation", 0) + 1

    @property
    def n_rows(self) -> int:
        return len(self._rows)

    def _restarted(self, size: int) -> bool:
        if size < self._offset:
            return True
        if self._head:
            try:
                with open(self.path, "rb") as fh:
                    head = fh.read(len(self._head))
            except OSError:
                return False
            return head != self._head
        return False

    def poll(self) -> int:
        """新しく読めた行の数を返す。"""
        try:
            st = self.path.stat()
        except OSError:
            if self.columns is not None:
                self.reset()
            return 0
        if self.min_mtime is not None and st.st_mtime < self.min_mtime:
            return 0
        if self._restarted(st.st_size):
            self.reset()
        if st.st_size == self._offset:
            return 0
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read(st.st_size - self._offset)
        except OSError:
            return 0
        self._offset += len(chunk)
        data = self._buf + chunk
        lines = data.split(b"\n")
        self._buf = lines.pop()
        new = 0
        for raw in lines:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if not line.strip():
                continue
            if self.columns is None:
                self.columns = [c.strip() for c in line.split(",")]
                self._head = raw + b"\n"
                continue
            fields = line.split(",")
            if len(fields) != len(self.columns):
                continue
            if len(self._rows) == 0 and len(self._head) < 1 << 16:
                self._head += raw + b"\n"
            self._rows.append([_to_float(x) for x in fields])
            new += 1
        if new:
            self._cache = None
        return new

    def history(self) -> History:
        if self._cache is None:
            cols = self.columns or []
            rows = np.array(self._rows, dtype=float) if self._rows else np.zeros((0, len(cols)))
            self._cache = History(cols, rows)
        return self._cache


# ---------------------------------------------------------------------------
# 質量流量の系列
# ---------------------------------------------------------------------------

INFLOW_TYPES = ("pressure_inlet", "mass_flow_inlet")
OUTFLOW_TYPES = ("pressure_outlet", "vacuum")


def patch_role(patch: str, patch_types: dict[str, str] | None = None) -> str:
    """パッチの役割: ``"in"``(流入)/ ``"out"``(流出)/ ``"other"``(壁・対称面など)。

    境界条件の種類が分かればそれで、分からなければ名前で決める。
    """
    t = (patch_types or {}).get(patch)
    if t is not None:
        if t in INFLOW_TYPES:
            return "in"
        if t in OUTFLOW_TYPES:
            return "out"
        return "other"
    name = patch.lower()
    if "inlet" in name or name.startswith("in"):
        return "in"
    if "outlet" in name or name.startswith("out") or "vacuum" in name:
        return "out"
    return "other"


@dataclass
class FlowSeries:
    label: str
    patch: str
    role: str
    x: np.ndarray
    y: np.ndarray
    source: str = "main"


def mass_flow_series(
    hist: History,
    *,
    nd_to_Mdot: float | None = None,
    mass_flow_scale: float | None = None,
    patch_types: dict[str, str] | None = None,
    include_other: bool = False,
    include_pbc: bool = True,
) -> tuple[list[FlowSeries], str]:
    """グラフに描く質量流量の系列と縦軸の名前。

    ``nd_to_Mdot``・``mass_flow_scale`` が分かれば(管ケース)論文の Ṁ にする。入口は流入を正、
    出口は流出を正にする(summary.json の M_dot_inlet・M_dot_outlet と同じ向き)。
    """
    x = hist.iterations()
    out: list[FlowSeries] = []
    as_Mdot = nd_to_Mdot is not None and mass_flow_scale is not None
    units = set()
    for mc in hist.mass_flow_columns():
        role = patch_role(mc.patch, patch_types)
        if role == "other" and not include_other:
            continue
        if mc.source == "pbc" and not include_pbc:
            continue
        v = hist.column(mc.column).copy()
        if as_Mdot:
            v = v * nd_to_Mdot if mc.unit == "nd" else v / mass_flow_scale
        units.add(mc.unit)
        sign = -1.0 if role == "in" else 1.0
        label = {"in": f"{mc.patch}(流入)", "out": f"{mc.patch}(流出)"}.get(role, mc.patch)
        if mc.source == "pbc":
            label = label.replace("(", "(PBC 側、", 1) if "(" in label else f"{label}(PBC 側)"
        out.append(FlowSeries(label, mc.patch, role, x, sign * v, mc.source))
    if as_Mdot:
        ylabel = r"無次元質量流量 $\dot{M}$"
    elif units == {"kg_s"}:
        ylabel = "質量流量 [kg/s]"
    else:
        ylabel = "質量流量(無次元、ρ_ref√(RT0)L_ref² 単位)"
    return out, ylabel


def mass_balance(series: list[FlowSeries]) -> float:
    """最後の反復での流入と流出の相対差 |in − out| / 平均(どちらかがなければ nan)。"""
    ins = [s.y for s in series if s.role == "in" and s.source == "main"]
    outs = [s.y for s in series if s.role == "out" and s.source == "main"]
    if not ins or not outs:
        return math.nan
    a = float(sum(v[-1] for v in ins))
    b = float(sum(v[-1] for v in outs))
    m = 0.5 * (a + b)
    return abs(a - b) / abs(m) if m != 0 else math.nan
