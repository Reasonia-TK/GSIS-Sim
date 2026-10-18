"""論文 arXiv:2610.07658v1 の比較用データ(``reference/paper_2610.07658/``)と比較の計算。

- Table 1(``table1_tube.csv``): δ_rp ごとの参照値(DSMC / NSF-FLUENT)、GSIS-PBC・CIS の無次元質量流量と反復回数
- Fig.3(``fig3_{pressure,velocity}_{gsis,cis,dsmc}_delta<δ>.csv``): 中心線の p/p_in と u_x/√(2RT0)。
  横軸 X = x/L_ref(管の入口 X = 0、出口 X = 10)。GSIS-PBC は線、CIS・DSMC は記号のデジタイズ値
- 比べるのは X ∈ [0.15, 10](線が欠けている X < 0.15 は外挿しない。docs/spec.md §1.4)

Qt には依存しない。
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .paths import reference_dir

PAPER_DELTAS = (1.0, 10.0, 100.0, 1000.0)
FIG3_DELTAS = (1.0, 10.0, 100.0)
FIG3_X_MIN = 0.15
FIG3_X_MAX = 10.0

# 量: ファイル名の語、CSV の列名、軸の名前
FIG3_QUANTITIES = {
    "pressure": ("P", "p / p_in"),
    "velocity": ("Ux", "u_x / √(2RT0)"),
}
FIG3_METHODS = {
    "gsis": "論文 GSIS-PBC(線)",
    "cis": "論文 CIS(記号)",
    "dsmc": "論文 DSMC(記号)",
}


@dataclass(frozen=True)
class Table1Row:
    delta_rp: float
    ref_method: str
    M_ref: float
    gsis_pbc_M: float
    gsis_pbc_error_pct: float
    gsis_pbc_iter: float
    gsis_pbc_time_h: float
    cis_M: float
    cis_error_pct: float
    cis_iter: float
    cis_time_h: float
    speedup: float


def _f(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return math.nan
    try:
        return float(s)
    except ValueError:
        return math.nan


def load_table1(ref: Path | None = None) -> list[Table1Row]:
    path = (ref or reference_dir()) / "table1_tube.csv"
    rows = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(
                Table1Row(
                    delta_rp=_f(r["delta_rp"]), ref_method=r["ref_method"].strip(), M_ref=_f(r["M_ref"]),
                    gsis_pbc_M=_f(r["gsis_pbc_M"]), gsis_pbc_error_pct=_f(r["gsis_pbc_error_pct"]),
                    gsis_pbc_iter=_f(r["gsis_pbc_iter"]), gsis_pbc_time_h=_f(r["gsis_pbc_time_h"]),
                    cis_M=_f(r["cis_M"]), cis_error_pct=_f(r["cis_error_pct"]), cis_iter=_f(r["cis_iter"]),
                    cis_time_h=_f(r["cis_time_h"]), speedup=_f(r["speedup"]),
                )
            )
    return rows


def paper_delta(delta: float | None, candidates=PAPER_DELTAS, rel_tol: float = 1e-3) -> float | None:
    """δ が論文の条件(1, 10, 100, 1000)のどれかに一致すればその値、しなければ None。"""
    if delta is None or not math.isfinite(delta) or delta <= 0:
        return None
    for d in candidates:
        if abs(delta - d) <= rel_tol * d:
            return d
    return None


def table1_row(rows: list[Table1Row], delta: float | None) -> Table1Row | None:
    d = paper_delta(delta)
    if d is None:
        return None
    for r in rows:
        if r.delta_rp == d:
            return r
    return None


def relative_error(value: float, ref: float) -> float:
    """(value − ref) / ref。どちらかが nan なら nan。"""
    if not (math.isfinite(value) and math.isfinite(ref)) or ref == 0:
        return math.nan
    return (value - ref) / ref


def fig3_path(quantity: str, method: str, delta: float, ref: Path | None = None) -> Path:
    return (ref or reference_dir()) / f"fig3_{quantity}_{method}_delta{delta:g}.csv"


def load_fig3(quantity: str, method: str, delta: float, ref: Path | None = None) -> tuple[np.ndarray, np.ndarray] | None:
    """Fig.3 のデジタイズ値 (X, 値)。ファイルがなければ None(δ=1000 の図はない。DSMC は δ=100 だけ)。"""
    path = fig3_path(quantity, method, delta, ref)
    if not path.is_file():
        return None
    col = FIG3_QUANTITIES[quantity][0]
    X, V = [], []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            X.append(_f(r["X"]))
            V.append(_f(r[col]))
    return np.asarray(X), np.asarray(V)


@dataclass(frozen=True)
class SeriesDiff:
    """計算 − 論文 の差(論文の点の X で計算の値を線形補間して比べる)。"""

    n: int
    rms: float
    max_abs: float
    x_at_max: float
    mean: float


def compare_series(
    X_model: np.ndarray,
    V_model: np.ndarray,
    X_ref: np.ndarray,
    V_ref: np.ndarray,
    x_min: float = FIG3_X_MIN,
    x_max: float = FIG3_X_MAX,
) -> SeriesDiff | None:
    """論文の点のうち x_min ≤ X ≤ x_max かつ計算の範囲内の点で、差の RMS・最大・平均を求める。"""
    X_model = np.asarray(X_model, dtype=float)
    V_model = np.asarray(V_model, dtype=float)
    order = np.argsort(X_model)
    X_model, V_model = X_model[order], V_model[order]
    X_ref = np.asarray(X_ref, dtype=float)
    V_ref = np.asarray(V_ref, dtype=float)
    if X_model.size < 2:
        return None
    keep = (X_ref >= x_min) & (X_ref <= x_max) & (X_ref >= X_model[0]) & (X_ref <= X_model[-1])
    keep &= np.isfinite(V_ref)
    if not np.any(keep):
        return None
    d = np.interp(X_ref[keep], X_model, V_model) - V_ref[keep]
    i = int(np.argmax(np.abs(d)))
    return SeriesDiff(
        n=int(d.size),
        rms=float(np.sqrt(np.mean(d**2))),
        max_abs=float(abs(d[i])),
        x_at_max=float(X_ref[keep][i]),
        mean=float(np.mean(d)),
    )
