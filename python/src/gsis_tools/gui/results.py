"""ソルバーの出力フォルダ(summary.json・history.csv・VTU・cells.csv)の読み込み。

出力の形式は ``src/app/cmd_cis_run.cpp``・``cmd_pbc_run.cpp`` の冒頭の説明による:

- ``cis-run``: ``fields.vtu``(セル値 SI: rho, u, T, p, sigma, q, Ma, Kn_local, residual)、``boundary.vtu``、
  ``summary.json``(``mass_flow.M_dot`` など、``reference`` に無次元化の基準量)、``cells.csv``(任意、無次元)
- ``pbc-run``: ``cells.vtu``(p, rho, U, T, Ma, Kn_local, residual_mass_nondim)、``boundary.vtu``、
  ``summary.json``(``M_dot`` などは最上位。基準量はない → ケース TOML から計算する)、``cells.csv``(任意)
- ``run``(フェーズ 4): どちらかの形を想定し、ファイル名とキーを順に探す

VTU は重いので、ここでは場所を決めるだけにし、読み込みは :mod:`gsis_tools.gui.fields`(pyvista)で行う。
Qt には依存しない。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .casefile import CaseModel, Derived, derive, load_case
from .centerline import TUBE_X_OFFSET, Centerline, extract_centerline
from .history import History, read_history
from .paths import repo_root

CELL_VTU_NAMES = ("fields.vtu", "cells.vtu")
SQRT2 = math.sqrt(2.0)


def read_json(path: Path) -> dict:
    """UTF-8 の JSON(壊れた文字は置き換えて読む)。"""
    return json.loads(Path(path).read_bytes().decode("utf-8", errors="replace"))


@dataclass
class Scales:
    """無次元化の基準量(SI)。p_ref = p_in(管ケース)。"""

    length: float
    temperature: float
    pressure: float
    density: float
    velocity: float  # √(R T0)
    heat_flux: float
    mass_flux: float

    @staticmethod
    def from_derived(d: Derived) -> "Scales":
        return Scales(d.L_ref, d.T0, d.p_ref, d.rho_ref, d.v_ref, d.heat_flux_ref, d.mass_flux_ref)

    @staticmethod
    def from_summary(ref: dict) -> "Scales | None":
        try:
            return Scales(
                float(ref["length_m"]), float(ref["temperature_K"]), float(ref["pressure_Pa"]),
                float(ref["density_kg_m3"]), float(ref["velocity_m_s"]), float(ref["heat_flux_W_m2"]),
                float(ref["mass_flux_kg_m2_s"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class RunResult:
    """1 つの出力フォルダ。"""

    directory: Path
    summary: dict = field(default_factory=dict)
    case_path: Path | None = None
    case: CaseModel | None = None
    derived: Derived | None = None
    scales: Scales | None = None
    cell_vtu: Path | None = None
    boundary_vtu: Path | None = None
    history_path: Path | None = None
    cells_csv: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_tube_example(self) -> bool:
        marker = self.directory / "gui_run.json"
        if marker.is_file():
            try:
                return read_json(marker).get("example") == "tube_vacuum"
            except (OSError, ValueError):
                return False
        if self.case is not None:
            from .casefile import is_tube_case
            return is_tube_case(self.case, self.case_path.parent if self.case_path else None)
        return False

    @property
    def name(self) -> str:
        return str(self.summary.get("case") or (self.case.name if self.case else self.directory.name))

    @property
    def kind(self) -> str:
        """出力の形: ``"kinetic"``(cis-run)/ ``"macro"``(pbc-run)/ ``"gsis"`` / ``"unknown"``。"""
        s = self.summary
        if s.get("mode") in ("nsf", "standalone_nsf") or (self.cell_vtu is not None and self.cell_vtu.name == "cells.vtu"):
            return "macro"
        if "mass_flow" in s or (self.cell_vtu is not None and self.cell_vtu.name == "fields.vtu"):
            # run(フェーズ 4)は summary.json に mode("gsis" / "cis")を書く。cis-run は mode を書かない
            return "gsis" if s.get("mode") == "gsis" else "kinetic"
        return "unknown"

    @property
    def kind_label(self) -> str:
        return {"kinetic": "CIS 単独", "macro": "PBC 単独(NSF)", "gsis": "GSIS-PBC"}.get(self.kind, "不明")

    @property
    def delta(self) -> float | None:
        if self.derived is not None:
            return self.derived.delta
        d = self.summary.get("delta")
        return float(d) if isinstance(d, (int, float)) else None

    @property
    def iterations(self) -> int | None:
        it = self.summary.get("iterations")
        return int(it) if isinstance(it, (int, float)) else None

    @property
    def converged(self) -> bool | None:
        c = self.summary.get("converged")
        return bool(c) if isinstance(c, bool) else None

    @property
    def wall_time_s(self) -> float | None:
        for k in ("wall_time_s", "total_seconds", "run_seconds"):
            v = self.summary.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    def mass_flow(self) -> dict[str, float]:
        """論文の無次元質量流量 Ṁ(``M_dot``・``M_dot_inlet``・``M_dot_outlet``)。ないキーは含めない。"""
        src = self.summary.get("mass_flow") if isinstance(self.summary.get("mass_flow"), dict) else self.summary
        out = {}
        for k in ("M_dot", "M_dot_inlet", "M_dot_outlet"):
            v = src.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v):
                out[k] = float(v)
        if "M_dot" not in out and "M_dot_inlet" in out and "M_dot_outlet" in out:
            out["M_dot"] = 0.5 * (out["M_dot_inlet"] + out["M_dot_outlet"])
        return out

    def mdot_conversion(self) -> tuple[float | None, float | None]:
        """履歴の質量流量を Ṁ に直す係数 (無次元 → Ṁ, Ṁ の基準 [kg/s])。管ケース(delta_rp)でなければ (None, None)。"""
        if self.derived is not None:
            if self.derived.has_p_in or self.is_tube_example:
                return self.derived.nd_to_Mdot, self.derived.mass_flow_scale
            return None, None
        mf = self.summary.get("mass_flow")
        scale = mf.get("scale_kg_s") if isinstance(mf, dict) else self.summary.get("mass_flow_scale_kg_s")
        if isinstance(scale, (int, float)) and scale > 0:
            return math.sqrt(2.0 / math.pi), float(scale)
        return None, None

    def patches(self) -> list[dict]:
        p = self.summary.get("patches")
        return p if isinstance(p, list) else []

    def patch_names(self) -> list[str]:
        """メッシュのパッチの並び(boundary.vtu の patch_id の順)。"""
        names = [str(p.get("name")) for p in self.patches() if isinstance(p, dict)]
        if not names and self.case is not None:
            # VTU の ID はメッシュ順。TOML の境界の記述順とは限らない。
            try:
                from ..pmesh import read_header
                from .casefile import resolve_mesh
                names = [str(p["name"]) for p in read_header(resolve_mesh(self.case.mesh, self.case_path.parent))["patches"]]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                if self.boundary_vtu is None:
                    names = [b.patch for b in self.case.boundaries]
        return names

    def patch_types(self) -> dict[str, str]:
        out = {str(p.get("name")): str(p.get("type")) for p in self.patches() if isinstance(p, dict)}
        if self.case is not None:
            for b in self.case.boundaries:
                out.setdefault(b.patch, b.type)
        return out

    def history(self) -> History | None:
        if self.history_path is None or not self.history_path.is_file():
            return None
        return read_history(self.history_path)


def _find_case(summary: dict, directory: Path, root: Path) -> Path | None:
    if (directory / "case.toml").is_file():
        return (directory / "case.toml").resolve()
    cf = summary.get("case_file")
    if not isinstance(cf, str) or not cf:
        return None
    p = Path(cf.replace("\\", os.sep))
    candidates = [p] if p.is_absolute() else [root / p, directory / p, Path.cwd() / p]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def summary_warnings(summary: dict) -> list[str]:
    """残差の収束と、補正後の保存性・既存の断面診断を別々に伝える。"""
    warnings = []
    if summary.get("mode") in ("nsf", "standalone_nsf") and any(
            p.get("type") == "maxwell_wall" and p.get("accommodation", 1) < 1 for p in summary.get("patches", [])):
        from .boundaries import NSF_MAXWELL_WARNING
        warnings.append(NSF_MAXWELL_WARNING)
    from .patch_results import mass_flow_inlet_rows
    for inlet in mass_flow_inlet_rows(summary):
        error = inlet["mass_flow_relative_error"]
        if error is None:
            warnings.append(f"質量流量入口 {inlet['patch']}: 目標との相対誤差を確認できません")
        elif error > 0.001:
            warnings.append(f"質量流量入口 {inlet['patch']}: 目標との相対誤差 {error * 100:.6g}% が 0.1% を超えています"
                            "(未収束・停止時の値を確認してください)")
    d = summary.get("conservation_diagnostics") or {}

    def fmt(value):
        return f"{value:.6g}" if isinstance(value, (float, int)) else "未計算"

    if d.get("warning"):
        prefix = "残差では収束扱いだが、" if summary.get("converged") else "未収束で、"
        diff = d.get("relative_imbalance")
        warnings.append(prefix + "補正後の質量の保存がずれている"
                        f"(入口 {fmt(d.get('inlet_kg_s'))} kg/s、出口 {fmt(d.get('outlet_kg_s'))} kg/s、"
                        f"差 {fmt(diff * 100 if isinstance(diff, (float, int)) else None)} %)")
    mb = summary.get("mass_balance") or {}
    tolerance = mb.get("tolerance", 0.002)
    for key in ("kinetic", "pbc", "kinetic_half"):
        check = mb.get(key) or {}
        if check.get("checked") and any(isinstance(check.get(k), (int, float)) and abs(check[k]) > tolerance
                                       for k in ("imbalance", "section_spread")):
            warnings.append(f"mass_balance ({key}): 質量の釣り合い・断面流量の差が許容値を超えています"
                            f"(差 {fmt(check.get('imbalance'))}、断面ばらつき {fmt(check.get('section_spread'))})")
    if d.get("warning") and d.get("last_pbc_correction"):
        correction = d["last_pbc_correction"]
        warnings.append("last_pbc_correction: " + "、".join(f"{k}={fmt(correction.get(k))}" for k in ("epsilon", "rho", "u", "T")))
    return warnings


def load_run(directory: str | Path, root: Path | None = None) -> RunResult:
    """出力フォルダを調べる(summary.json がなくても、あるファイルだけで作る)。"""
    directory = Path(directory)
    root = root or repo_root()
    if not directory.is_dir():
        raise FileNotFoundError(f"フォルダがありません: {directory}")
    res = RunResult(directory=directory)
    sj = directory / "summary.json"
    if sj.is_file():
        try:
            res.summary = read_json(sj)
        except (OSError, ValueError) as exc:
            res.notes.append(f"summary.json を読めません: {exc}")
    else:
        res.notes.append("summary.json がありません(実行中か、途中で止まった計算)")
    for name in CELL_VTU_NAMES:
        if (directory / name).is_file():
            res.cell_vtu = directory / name
            break
    if (directory / "boundary.vtu").is_file():
        res.boundary_vtu = directory / "boundary.vtu"
    if (directory / "history.csv").is_file():
        res.history_path = directory / "history.csv"
    if (directory / "cells.csv").is_file():
        res.cells_csv = directory / "cells.csv"

    res.case_path = _find_case(res.summary, directory, root)
    if res.case_path is not None:
        try:
            res.case = load_case(res.case_path).model
            res.derived = derive(res.case)
        except Exception as exc:  # ケースが読めなくても結果は見られるようにする
            res.notes.append(f"ケース {res.case_path} を読めません: {exc}")
    elif res.summary.get("case_file"):
        res.notes.append(f"ケース {res.summary.get('case_file')} が見つかりません(無次元化は summary.json の値を使う)")
    ref = res.summary.get("reference")
    if isinstance(ref, dict):
        res.scales = Scales.from_summary(ref)
    if res.scales is None and res.derived is not None:
        res.scales = Scales.from_derived(res.derived)
    return res


def discover_runs(base: str | Path, max_depth: int = 4, limit: int = 500) -> list[Path]:
    """``base`` の下の出力フォルダ(summary.json か history.csv があるフォルダ)を新しい順に並べる。"""
    base = Path(base)
    found: list[tuple[float, Path]] = []
    if not base.is_dir():
        return []
    base_depth = len(base.parts)
    for dirpath, dirnames, filenames in os.walk(base):
        depth = len(Path(dirpath).parts) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        if "summary.json" in filenames or "history.csv" in filenames:
            p = Path(dirpath)
            f = p / ("summary.json" if "summary.json" in filenames else "history.csv")
            try:
                found.append((f.stat().st_mtime, p))
            except OSError:
                continue
        if len(found) >= limit:
            break
    found.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in found]


# ---------------------------------------------------------------------------
# 中心線
# ---------------------------------------------------------------------------

CENTERLINE_LABELS = {"p": "p / p_in", "ux": "u_x / √(2RT0)", "T": "T / T0"}


def _read_cells_csv(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
    want = {"x", "y", "z", "p", "ux", "T"}
    if not want <= set(header):
        raise ValueError(f"{path.name} に x, y, z, p, ux, T の列がありません")
    cols = [header.index(k) for k in ("x", "y", "z", "p", "ux", "T")]
    a = np.loadtxt(path, delimiter=",", skiprows=1, usecols=cols, ndmin=2)
    return a[:, :3], {"p": a[:, 3], "ux": a[:, 4] / SQRT2, "T": a[:, 5]}


def centerline_inputs_from_grid(grid: Any, scales: Scales) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """pyvista の UnstructuredGrid(SI のセル値)から、セル中心(L_ref 単位)と無次元の値を作る。"""
    centers = np.asarray(grid.cell_centers().points, dtype=float) / scales.length
    cd = grid.cell_data
    vel = cd["u"] if "u" in cd.keys() else cd["U"]
    vel = np.asarray(vel, dtype=float)
    vals = {
        "p": np.asarray(cd["p"], dtype=float) / scales.pressure,
        "ux": vel[:, 0] / (SQRT2 * scales.velocity),
        "T": np.asarray(cd["T"], dtype=float) / scales.temperature,
    }
    return centers, vals


def centerline_from_arrays(
    centers: np.ndarray,
    values: dict[str, np.ndarray],
    *,
    r_max: float = 0.3,
    x_offset: float = TUBE_X_OFFSET,
    method: str = "auto",
) -> Centerline:
    return extract_centerline(centers[:, 0], centers[:, 1], centers[:, 2], values, r_max=r_max, x_offset=x_offset,
                              method=method)


def centerline_from_cells_csv(path: Path, **kw) -> Centerline:
    centers, vals = _read_cells_csv(Path(path))
    return centerline_from_arrays(centers, vals, **kw)


def write_centerline_csv(path: str | Path, cl: Centerline, extra: dict[str, np.ndarray] | None = None) -> Path:
    """中心線を CSV に書く(列: X, p_over_pin, ux_over_sqrt2RT0, T_over_T0 と ``extra``)。"""
    path = Path(path)
    names = {"p": "p_over_pin", "ux": "ux_over_sqrt2RT0", "T": "T_over_T0"}
    cols = [("X", cl.X)] + [(names.get(k, k), v) for k, v in cl.values.items()]
    if extra:
        cols += list(extra.items())
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(c for c, _ in cols) + "\n")
        for i in range(cl.X.size):
            fh.write(",".join(f"{np.asarray(v)[i]:.8g}" for _, v in cols) + "\n")
    return path
