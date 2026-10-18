"""平板の CIS の結果(runs/kinetic/<ケース名>/cells.csv)を verification/ref1d の参照解と比べる。

比べる量(verification/ref1d/README.md §7):
  - 熱伝導: q_x の区間平均(セル値の体積平均)と ref1d の qx_mean
  - クエット流: P_xy の区間平均と ref1d の Pxy_mean
  - 分布 ρ, T, u_y: ref1d の分布(801 節点)を 3 次スプラインでセル中心に補間し、差の最大値
    (ρ・T は基準値 1、u_y は壁速度 |u_w| に対する比)
参照値は 2 つ: summary_vmid30.csv(同じ ξx 格子 30 点・±10。3D の空間離散化と実装の差だけが出る)と
summary.csv(格子収束した厳密な参照解。速度格子 Δξ = 2/3 の差を含む総合の誤差)。

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/verification/kinetic/compare_ref1d.py [--runs runs/kinetic]
      [--out cases/verification/kinetic/results] [--prefix slab_]
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


def parse_name(name: str):
    # slab_<kind>_Kn<tag>_nx<N>[_per]
    body = name[len("slab_"):]
    sides = "periodic" if body.endswith("_per") else "default"
    if sides == "periodic":
        body = body[: -len("_per")]
    kind, rest = body.split("_Kn")
    kn_s, nx_s = rest.split("_nx")
    return kind, float(kn_s.replace("p", ".")), int(nx_s), sides


def compare_run(run_dir: Path, ref_mid, ref_exact) -> dict | None:
    cells_path = run_dir / "cells.csv"
    summary_path = run_dir / "summary.json"
    if not cells_path.exists() or not summary_path.exists():
        return None
    name = run_dir.name
    kind, kn, nx, sides = parse_name(name)
    summ = json.loads(summary_path.read_text(encoding="utf-8"))
    c = read_cells(cells_path)
    order = np.argsort(c["x"])
    x = c["x"][order]
    vol = c["volume"][order]
    w = vol / vol.sum()
    qx = float(np.sum(w * c["qx"][order]))
    pxy = float(np.sum(w * c["sxy"][order]))
    rm = ref_mid[(kind, kn)]
    rx = ref_exact[(kind, kn)]
    couette = kind.startswith("couette")
    if couette:
        val, ref_m, ref_x = pxy, float(rm["Pxy_mean"]), float(rx["Pxy_mean"])
        uw = abs(float(rm["u_R"]))
    else:
        val, ref_m, ref_x = qx, float(rm["qx_mean"]), float(rx["qx_mean"])
        uw = 1.0
    prof = read_profile(REF / "profiles_vmid30" / f"{kind}_Kn{kn_tag(kn)}.csv")
    diffs = {}
    for key, scale in (("rho", 1.0), ("T", 1.0), ("uy", uw)):
        spline = CubicSpline(prof["x"], prof[key])
        d = c[key][order] - spline(x)
        diffs[key] = float(np.max(np.abs(d)) / scale)
    # 平板の内部(壁から 2 セル以上離れたところ)だけの差も出す(壁際はセル平均と節点値の違いが大きい)
    inner = (x > 2.0 / nx) & (x < 1.0 - 2.0 / nx)
    diffs_inner = {}
    for key, scale in (("rho", 1.0), ("T", 1.0), ("uy", uw)):
        spline = CubicSpline(prof["x"], prof[key])
        d = c[key][order][inner] - spline(x[inner])
        diffs_inner[key] = float(np.max(np.abs(d)) / scale) if np.any(inner) else math.nan
    flux_spread = float((np.max(c["sxy" if couette else "qx"]) - np.min(c["sxy" if couette else "qx"])) / abs(val))
    return {
        "case": name,
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
        "cell_spread": flux_spread,
        "max_drho": diffs["rho"],
        "max_dT": diffs["T"],
        "max_duy_rel": diffs["uy"] if couette else math.nan,
        "inner_max_drho": diffs_inner["rho"],
        "inner_max_dT": diffs_inner["T"],
        "inner_max_duy_rel": diffs_inner["uy"] if couette else math.nan,
        "iterations": summ.get("iterations"),
        "converged": summ.get("converged"),
        "epsilon": summ.get("epsilon"),
        "residual": summ.get("residual"),
        "wall_time_s": summ.get("wall_time_s"),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs" / "kinetic"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    ap.add_argument("--prefix", default="slab_")
    args = ap.parse_args()
    ref_mid = read_summary(REF / "summary_vmid30.csv")
    ref_exact = read_summary(REF / "summary.csv")
    rows = []
    for run_dir in sorted(Path(args.runs).glob(args.prefix + "*")):
        if not run_dir.is_dir():
            continue
        try:
            r = compare_run(run_dir, ref_mid, ref_exact)
        except (KeyError, ValueError) as e:
            print(f"読み飛ばし {run_dir.name}: {e}")
            continue
        if r is not None:
            rows.append(r)
    if not rows:
        print("比べる結果がありません")
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with (out / "slab_comparison.csv").open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    print(f"{'case':38s} {'q':4s} {'value':>13s} {'d_vmid30':>10s} {'d_exact':>10s} {'spread':>9s} "
          f"{'drho':>8s} {'dT':>8s} {'duy':>8s} {'iter':>7s}")
    for r in rows:
        print(f"{r['case']:38s} {r['quantity']:4s} {r['value']:13.6e} {r['rel_diff_vmid30']:+10.3e} "
              f"{r['rel_diff_exact']:+10.3e} {r['cell_spread']:9.2e} {r['max_drho']:8.1e} {r['max_dT']:8.1e} "
              f"{r['max_duy_rel']:8.1e} {r['iterations']!s:>7s}")
    print(f"書き出し: {out / 'slab_comparison.csv'}")


if __name__ == "__main__":
    main()
