"""平板の GSIS の結果(runs/gsis/<ケース名>/cells.csv)を、verification/ref1d の参照解と CIS 単独の結果
(runs/kinetic/<ケース名>/cells.csv、フェーズ 2)と比べる。

比べる量(cases/verification/kinetic/compare_ref1d.py と同じ定義):
  - 熱伝導: q_x の区間平均(セル値の体積平均)、クエット流: P_xy の区間平均
  - 分布 ρ, T, u_y: ref1d の分布(801 節点)を 3 次スプラインでセル中心に補間した値との差の最大
    (ρ・T は基準値 1、u_y は壁速度 |u_w| に対する比)
  - CIS 単独の結果との差: 流束の相対差、分布の差の最大
  - 反復: GSIS の反復数、CIS 反復の合計(GSIS 1 回 = N_CIS 回)、CIS 単独の反復数

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/verification/gsis/compare.py [--suffix _variant]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / "verification" / "ref1d" / "results"


def kn_tag(kn: float) -> str:
    return f"{kn:g}".replace(".", "p")


def read_summary(path: Path) -> dict[tuple[str, float], dict[str, str]]:
    out = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[(row["case"], float(row["Kn"]))] = row
    return out


def read_profile(path: Path) -> dict[str, np.ndarray]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    names = lines[0].split(",")
    values = np.array([[float(v) for v in ln.split(",")] for ln in lines[1:]])
    return {name: values[:, i] for i, name in enumerate(names)}


def read_cells(path: Path) -> dict[str, np.ndarray]:
    data = np.genfromtxt(path, delimiter=",", names=True)
    return {name: np.asarray(data[name]) for name in data.dtype.names}


def parse_name(name: str, suffix: str):
    # slab_<kind>_Kn<tag>_nx<N>[_per]_gsis<suffix>
    body = name[len("slab_"):]
    body = body[: len(body) - len("_gsis" + suffix)]
    sides = "periodic" if body.endswith("_per") else "default"
    if sides == "periodic":
        body = body[: -len("_per")]
    kind, rest = body.split("_Kn")
    kn_s, nx_s = rest.split("_nx")
    return kind, float(kn_s.replace("p", ".")), int(nx_s), sides


def flux_of(c: dict[str, np.ndarray], couette: bool) -> float:
    w = c["volume"] / c["volume"].sum()
    return float(np.sum(w * (c["sxy"] if couette else c["qx"])))


def compare_run(run_dir: Path, suffix: str, ref_mid, ref_exact) -> dict | None:
    cells_path = run_dir / "cells.csv"
    summary_path = run_dir / "summary.json"
    if not cells_path.exists() or not summary_path.exists():
        return None
    kind, kn, nx, sides = parse_name(run_dir.name, suffix)
    couette = kind.startswith("couette")
    summ = json.loads(summary_path.read_text(encoding="utf-8"))
    c = read_cells(cells_path)
    order = np.argsort(c["x"])
    c = {k: v[order] for k, v in c.items()}
    x = c["x"]
    val = flux_of(c, couette)
    rm = ref_mid[(kind, kn)]
    rx = ref_exact[(kind, kn)]
    key = "Pxy_mean" if couette else "qx_mean"
    ref_m, ref_x = float(rm[key]), float(rx[key])
    uw = abs(float(rm["u_R"])) if couette else 1.0
    prof = read_profile(REF / "profiles_vmid30" / f"{kind}_Kn{kn_tag(kn)}.csv")
    diffs = {}
    for k, scale in (("rho", 1.0), ("T", 1.0), ("uy", uw)):
        spline = CubicSpline(prof["x"], prof[k])
        diffs[k] = float(np.max(np.abs(c[k] - spline(x))) / scale)
    # CIS 単独(フェーズ 2)の結果
    cis_dir = ROOT / "runs" / "kinetic" / (f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}" + ("_per" if sides == "periodic" else ""))
    cis = {"cis_value": math.nan, "rel_diff_cis": math.nan, "max_drho_cis": math.nan, "max_dT_cis": math.nan,
           "max_duy_cis": math.nan, "cis_iterations": None}
    if (cis_dir / "cells.csv").exists():
        cc = read_cells(cis_dir / "cells.csv")
        o2 = np.argsort(cc["x"])
        cc = {k: v[o2] for k, v in cc.items()}
        cv = flux_of(cc, couette)
        cis["cis_value"] = cv
        cis["rel_diff_cis"] = (val - cv) / abs(cv)
        cis["max_drho_cis"] = float(np.max(np.abs(c["rho"] - cc["rho"])))
        cis["max_dT_cis"] = float(np.max(np.abs(c["T"] - cc["T"])))
        cis["max_duy_cis"] = float(np.max(np.abs(c["uy"] - cc["uy"])) / uw) if couette else math.nan
        cs = json.loads((cis_dir / "summary.json").read_text(encoding="utf-8"))
        cis["cis_iterations"] = cs.get("iterations")
    return {
        "case": run_dir.name,
        "kind": kind,
        "Kn": kn,
        "nx": nx,
        "sides": sides,
        "quantity": "Pxy" if couette else "qx",
        "value": val,
        "ref_vmid30": ref_m,
        "rel_diff_vmid30": (val - ref_m) / abs(ref_m),
        "ref_exact": ref_x,
        "rel_diff_exact": (val - ref_x) / abs(ref_x),
        "max_drho": diffs["rho"],
        "max_dT": diffs["T"],
        "max_duy_rel": diffs["uy"] if couette else math.nan,
        **cis,
        "gsis_iterations": summ.get("iterations"),
        "cis_sweeps": summ.get("cis_iterations"),
        "converged": summ.get("converged"),
        "epsilon": summ.get("epsilon"),
        "replaced_total": (summ.get("negative_replacement") or {}).get("total_cells"),
        "wall_time_s": summ.get("wall_time_s"),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs" / "gsis"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()
    ref_mid = read_summary(REF / "summary_vmid30.csv")
    ref_exact = read_summary(REF / "summary.csv")
    rows = []
    for run_dir in sorted(Path(args.runs).glob("slab_*_gsis" + args.suffix)):
        if not run_dir.is_dir():
            continue
        r = compare_run(run_dir, args.suffix, ref_mid, ref_exact)
        if r is not None:
            rows.append(r)
    if not rows:
        print("比べる結果がありません")
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = "slab_gsis_comparison" + (args.suffix or "") + ".csv"
    with (out / name).open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    print(f"{'case':40s} {'q':4s} {'value':>13s} {'d_vmid30':>10s} {'d_CIS':>10s} {'drho':>8s} {'dT':>8s} {'duy':>8s} "
          f"{'GSIS':>5s} {'sweeps':>6s} {'CIS':>6s}")
    for r in rows:
        print(f"{r['case']:40s} {r['quantity']:4s} {r['value']:13.6e} {r['rel_diff_vmid30']:+10.3e} "
              f"{r['rel_diff_cis']:+10.3e} {r['max_drho']:8.1e} {r['max_dT']:8.1e} {r['max_duy_rel']:8.1e} "
              f"{r['gsis_iterations']!s:>5s} {r['cis_sweeps']!s:>6s} {r['cis_iterations']!s:>6s}")
    print(f"書き出し: {out / name}")


if __name__ == "__main__":
    main()
