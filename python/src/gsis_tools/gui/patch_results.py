"""境界の SI 集計。全エネルギー流束を熱流束と取り違えない。"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

MISSING = "ソルバーの出力にない"
COLUMNS = ("patch", "type", "accommodation", "area_m2", "mass_flow_kg_s", "heat_flow_W", "energy_flow_W", "force_x_N", "force_y_N", "force_z_N", "flow_sign",
           "pressure_Pa", "target_mass_flow_kg_s", "mass_flow_relative_error")
HEADERS = ("パッチ", "種類", "適応係数 α", "面積 [m²]", "質量流量 [kg/s]", "局所熱流量 [W]", "全エネルギー [W]", "力 x [N]", "力 y [N]", "力 z [N]", "流量の正方向",
           "最終 p_b [Pa]", "目標 [kg/s]", "相対誤差 [比率]")
INLET_COLUMNS = ("patch", "pressure_Pa", "target_mass_flow_kg_s", "mass_flow_kg_s", "mass_flow_relative_error")
INLET_HEADERS = ("質量流量入口", "最終 p_b [Pa]", "目標 [kg/s]", "最終流量 [kg/s]", "相対誤差 [%]")


def mass_flow_inlet_rows(summary):
    """最終の制御値。旧出力や欠測を 0 と取り違えない。誤差は比率で保持する。"""
    rows = []
    for p in summary.get("patches", []):
        if p.get("type") != "mass_flow_inlet":
            continue
        row = dict(patch=p.get("name", "不明"))
        for k in INLET_COLUMNS[1:]:
            v = p.get(k)
            row[k] = float(v) if isinstance(v, (int, float)) and np.isfinite(v) else None
        if row["mass_flow_relative_error"] is None:
            target, flow = row["target_mass_flow_kg_s"], row["mass_flow_kg_s"]
            if target is not None and target > 0 and flow is not None:
                row["mass_flow_relative_error"] = abs(flow - target) / target
        rows.append(row)
    return rows


def patch_rows(run, boundary=None):
    """S2 は流入正の共通 SI 値、旧出力は外向き正の従来集計を使う。"""
    patches = {i: dict(p) for i, p in enumerate(run.patches())}
    ids = None
    area = None
    if boundary is not None and "patch_id" in boundary.cell_data:
        ids = np.asarray(boundary.cell_data["patch_id"], dtype=int)
        area = np.asarray(boundary.compute_cell_sizes(length=False, volume=False).cell_data["Area"])
        names = run.patch_names()
        types = run.patch_types()
        for i in sorted(set(ids.tolist())):
            name = names[i] if 0 <= i < len(names) else f"patch_{i}"
            patches.setdefault(i, dict(name=name, type=types.get(name, "不明")))
    if not patches:
        patches = {i: dict(name=n, type=run.patch_types().get(n, "不明")) for i, n in enumerate(run.patch_names())}
    rows = []
    for i, patch in patches.items():
        name, kind = str(patch.get("name", f"patch_{i}")), patch.get("type", "不明")
        row = dict.fromkeys(COLUMNS)
        row.update(patch=name, type=kind)
        row["accommodation"] = patch.get("accommodation")
        for key in INLET_COLUMNS[1:]:
            if key != "mass_flow_kg_s":
                row[key] = patch.get(key)
        modern = "heat_flow_W" in patch or "force_N" in patch
        row["flow_sign"] = "流入" if modern else "外向き(旧出力)"
        row["area_m2"] = patch.get("area_m2")
        if modern:
            for key in ("mass_flow_kg_s", "heat_flow_W", "energy_flow_W"):
                value = patch.get(key)
                row[key] = float(value) if value is not None and np.isfinite(value) else None
            force = patch.get("force_N")
            if force is not None and np.shape(force) == (3,):
                for axis, value in zip("xyz", force):
                    row[f"force_{axis}_N"] = float(value) if np.isfinite(value) else None
            rows.append(row)
            continue
        mdot = patch.get("mdot_kg_s")
        if mdot is None:
            mdot = patch.get("mass_flow_kg_s")
        if mdot is not None and np.isfinite(mdot):
            row["mass_flow_kg_s"] = float(mdot)
        elif patch.get("mdot_nd") is not None and run.scales:
            row["mass_flow_kg_s"] = patch["mdot_nd"] * run.scales.mass_flux * run.scales.length**2
        force = None
        row["energy_flow_W"] = patch.get("energy_flow_W")
        if kind in ("diffuse_wall", "maxwell_wall") and patch.get("momentum_flux_nd") is not None and run.scales:
            force = np.asarray(patch["momentum_flux_nd"]) * run.scales.pressure * run.scales.length**2
        if ids is not None and np.any(mask := ids == i):
            cd = boundary.cell_data

            def integrate(key):
                values = np.asarray(cd[key], dtype=float)[mask]
                weights = area[mask]
                return np.sum(values * (weights[:, None] if values.ndim > 1 else weights), axis=0)

            if row["mass_flow_kg_s"] is None and "mass_flux" in cd:
                row["mass_flow_kg_s"] = float(integrate("mass_flux"))
            if "heat_flux_normal" in cd:
                row["heat_flow_W"] = float(integrate("heat_flux_normal"))
            if kind in ("diffuse_wall", "maxwell_wall") and "momentum_flux" in cd and force is None:
                force = integrate("momentum_flux")
        if force is not None and np.shape(force) == (3,):
            for axis, value in zip("xyz", force):
                row[f"force_{axis}_N"] = float(value) if np.isfinite(value) else None
        rows.append(row)
    return rows


def export_patch_csv(path: Path, rows) -> Path:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([MISSING if row.get(c) is None else row[c] for c in COLUMNS])
    return Path(path)
