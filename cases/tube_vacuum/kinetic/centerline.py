"""管の中心線上の p/p_in と u_x/√(2RT0) を cis-run の cells.csv から取り出し、論文 Fig.3 のデジタイズ値と比べる。

- cells.csv の値は無次元(p は p_ref = p_in、u は √(RT0) 単位)。管の入口 x = 8R が X = 0、出口 x = 18R が X = 10
- 中心線の値: 同じ x の層(六面体メッシュは x 方向に層になっている)で r < 0.3 のセルに a + b r² を最小二乗で当て、
  r = 0 の値 a を取る
- 比べる系列: reference/paper_2610.07658/fig3_{pressure,velocity}_{cis,gsis}_delta<δ>.csv(線が欠けている X < 0.15 は使わない)

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/tube_vacuum/kinetic/centerline.py runs/kinetic/tube_delta1_cis --delta 1
結果: <run>/centerline.csv と、標準出力の差の表
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / "reference" / "paper_2610.07658"


def centerline(cells_csv: Path):
    d = np.genfromtxt(cells_csv, delimiter=",", names=True)
    x = d["x"]
    r = np.hypot(d["y"], d["z"])
    sel = r < 0.3
    xs = np.round(x[sel], 6)
    out = []
    for xv in np.unique(xs):
        m = sel.copy()
        m[sel] = np.abs(xs - xv) < 1e-6
        rr = r[m]
        if rr.size < 3:
            continue
        A = np.vstack([np.ones_like(rr), rr**2]).T
        p0 = np.linalg.lstsq(A, d["p"][m], rcond=None)[0][0]
        u0 = np.linalg.lstsq(A, d["ux"][m], rcond=None)[0][0]
        T0 = np.linalg.lstsq(A, d["T"][m], rcond=None)[0][0]
        out.append((xv - 8.0, p0, u0 / math.sqrt(2.0), T0))
    return np.array(out)


def compare(cl: np.ndarray, ref_path: Path, col: int):
    if not ref_path.exists():
        return None
    ref = np.genfromtxt(ref_path, delimiter=",", names=True)
    names = ref.dtype.names
    X = ref[names[0]]
    V = ref[names[1]]
    keep = (X >= 0.15) & (X <= 10.0)
    model = np.interp(X[keep], cl[:, 0], cl[:, col])
    d = model - V[keep]
    return float(np.sqrt(np.mean(d**2))), float(np.max(np.abs(d))), float(X[keep][np.argmax(np.abs(d))])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="cis-run の出力先(cells.csv があること)")
    ap.add_argument("--delta", default="1")
    args = ap.parse_args()
    run = Path(args.run)
    cl = centerline(run / "cells.csv")
    with (run / "centerline.csv").open("w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["X", "p_over_pin", "ux_over_sqrt2RT0", "T_over_T0"])
        for row in cl:
            wr.writerow([f"{v:.6g}" for v in row])
    print(f"中心線: {len(cl)} 点 → {run / 'centerline.csv'}")
    for qty, col in (("pressure", 1), ("velocity", 2)):
        for method in ("cis", "gsis"):
            res = compare(cl, REF / f"fig3_{qty}_{method}_delta{args.delta}.csv", col)
            if res is None:
                continue
            rms, mx, at = res
            print(f"  {qty:8s} vs 論文 {method.upper():4s}(δ={args.delta}): 差の RMS {rms:.4f}、最大 {mx:.4f}(X = {at:.2f})")
    # 管の中の代表点
    for X in (0.0, 2.5, 5.0, 7.5, 10.0):
        p = np.interp(X, cl[:, 0], cl[:, 1])
        u = np.interp(X, cl[:, 0], cl[:, 2])
        print(f"  X = {X:4.1f}: p/p_in = {p:.4f}、u_x/√(2RT0) = {u:.4f}")


if __name__ == "__main__":
    main()
