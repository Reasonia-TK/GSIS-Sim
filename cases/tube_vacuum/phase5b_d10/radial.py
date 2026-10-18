"""フェーズ 5b-2: 管の断面の半径方向の分布(u_x、n、T、p)を、ユーザーの DSMC(d10_q)と本実装の結果で比べる。

- DSMC: validation/dsmc/runs/d10_q の plotfile(1,000 ステップ = 20 t_ref ごとのブロック平均)を、定常の判定と同じ
  ステップ 8,000 以降で平均する(気体の体積率 φ で重み付け)。読み取りは validation/dsmc/tools/plotfile.py
- 本実装: cells.csv(gsis-solver run --cells-csv)。X に最も近い x の層のセル
- どちらも r の幅 0.1R の区間に分けて平均する(DSMC の格子幅 0.1R に合わせる)

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/radial.py runs/phase5b/d10/q_base [--labels q_base]
出力: cases/tube_vacuum/phase5b_d10/results/radial/<ラベル>_radial.csv、dsmc_radial.csv と標準出力の表
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DSMC_RUN = ROOT / "validation" / "dsmc" / "runs" / "d10_q"
STATIONS = (0.5, 2.5, 5.0, 7.5, 9.0, 9.75)
BINS = np.arange(0.0, 1.0001, 0.1)


def dsmc_radial(cache: Path) -> dict:
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    sys.path.insert(0, str(ROOT / "validation" / "dsmc" / "tools"))
    from plotfile import PlotFile, list_plotfiles  # noqa: E402

    pfs = [p for p in list_plotfiles(DSMC_RUN) if PlotFile(p).step >= 8000]
    acc = None
    for p in pfs:
        pf = PlotFile(p)
        d = pf.get_many(["n", "u", "T", "p", "gas_volume_fraction"])
        phi = np.nan_to_num(d["gas_volume_fraction"])
        cur = {k: np.nan_to_num(d[k]) * phi for k in ("n", "u", "T", "p")}
        cur["phi"] = phi
        if acc is None:
            acc = cur
            x, y, z = pf.cell_centers()
        else:
            for k in acc:
                acc[k] += cur[k]
    npf = len(pfs)
    import json

    prm = json.loads((DSMC_RUN / "params.json").read_text(encoding="utf-8"))
    R = prm["L_ref_m"]
    X = x / R + 0.0  # DSMC の x は管の入口が 0
    Y, Z = np.meshgrid(y / R, z / R, indexing="ij")
    r = np.hypot(Y, Z)
    out = {"X": np.array(STATIONS), "r_mid": 0.5 * (BINS[1:] + BINS[:-1])}
    for key in ("n", "u", "T", "p", "phi"):
        out[key] = np.full((len(STATIONS), len(BINS) - 1), np.nan)
    for i, Xs in enumerate(STATIONS):
        ix = int(np.argmin(np.abs(X - Xs)))
        ph = acc["phi"][ix]
        for b in range(len(BINS) - 1):
            m = (r >= BINS[b]) & (r < BINS[b + 1]) & (ph > 0.0)
            w = ph[m].sum()
            if w <= 0:
                continue
            out["phi"][i, b] = w / m.sum() / npf
            out["n"][i, b] = acc["n"][ix][m].sum() / w / prm["n_in_m3"]
            out["u"][i, b] = acc["u"][ix][m].sum() / w / prm["c_m_m_s"]
            out["T"][i, b] = acc["T"][ix][m].sum() / w / prm["T0_K"]
            out["p"][i, b] = acc["p"][ix][m].sum() / w / prm["p_in_Pa"]
    out["X_used"] = np.array([X[int(np.argmin(np.abs(X - Xs)))] for Xs in STATIONS])
    np.savez(cache, **out)
    return out


def model_radial(cells: Path) -> dict:
    import csv

    with cells.open(encoding="utf-8") as fh:
        head = next(csv.reader(fh))
    cols = ["x", "y", "z", "volume", "rho", "ux", "T", "p"]
    a = np.loadtxt(cells, delimiter=",", skiprows=1, usecols=[head.index(c) for c in cols])
    d = {c: a[:, k] for k, c in enumerate(cols)}
    X = d["x"] - 8.0
    r = np.hypot(d["y"], d["z"])
    layers = np.unique(np.round(X[(X > 0) & (X < 10)], 6))
    out = {"X": np.array(STATIONS), "r_mid": 0.5 * (BINS[1:] + BINS[:-1])}
    for key in ("n", "u", "T", "p"):
        out[key] = np.full((len(STATIONS), len(BINS) - 1), np.nan)
    used = []
    for i, Xs in enumerate(STATIONS):
        xl = layers[int(np.argmin(np.abs(layers - Xs)))]
        used.append(xl)
        lay = np.abs(np.round(X, 6) - xl) < 1e-6
        for b in range(len(BINS) - 1):
            m = lay & (r >= BINS[b]) & (r < BINS[b + 1])
            if not m.any():
                continue
            V = d["volume"][m]
            out["n"][i, b] = np.sum(d["rho"][m] * V) / V.sum()
            out["u"][i, b] = np.sum(d["ux"][m] * V) / V.sum() / math.sqrt(2.0)
            out["T"][i, b] = np.sum(d["T"][m] * V) / V.sum()
            out["p"][i, b] = np.sum(d["p"][m] * V) / V.sum()
    out["X_used"] = np.array(used)
    return out


def write_csv(path: Path, d: dict) -> None:
    rows = ["X_station,X_used,r_mid,n,u,T,p"]
    for i, Xs in enumerate(d["X"]):
        for b, rm in enumerate(d["r_mid"]):
            rows.append(f"{Xs:g},{d['X_used'][i]:.4f},{rm:.3f},{d['n'][i, b]:.6g},{d['u'][i, b]:.6g},"
                        f"{d['T'][i, b]:.6g},{d['p'][i, b]:.6g}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--labels", nargs="*")
    ap.add_argument("--out", default=str(HERE / "results" / "radial"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ds = dsmc_radial(out / "dsmc_radial_cache.npz")
    write_csv(out / "dsmc_radial.csv", ds)
    labels = args.labels or [Path(r).name for r in args.runs]
    w = (BINS[1:] ** 2 - BINS[:-1] ** 2)  # 面積の重み(円環)
    for run, lab in zip(args.runs, labels):
        md = model_radial(Path(run) / "cells.csv")
        write_csv(out / f"{lab}_radial.csv", md)
        print(f"== {lab}(本実装 / DSMC。区間 r ∈ [0, 0.1) … [0.9, 1.0)、u は √(2RT0) 単位)")
        for i, Xs in enumerate(STATIONS):
            um, ud = md["u"][i], ds["u"][i]
            nm, nd = md["n"][i], ds["n"][i]
            ok = np.isfinite(um) & np.isfinite(ud)
            ubar_m = np.sum((nm * um * w)[ok]) / np.sum((nm * w)[ok])
            ubar_d = np.sum((nd * ud * w)[ok]) / np.sum((nd * w)[ok])
            flux_m = np.sum((nm * um * w)[ok])
            flux_d = np.sum((nd * ud * w)[ok])
            print(f"X={Xs:5.2f}(本 {md['X_used'][i]:.2f} / D {ds['X_used'][i]:.2f}): "
                  f"n 平均 {np.sum((nm*w)[ok])/np.sum(w[ok]):.4f}/{np.sum((nd*w)[ok])/np.sum(w[ok]):.4f}  "
                  f"u 軸 {um[0]:.4f}/{ud[0]:.4f}  u 壁際(0.9–1.0) {um[-1]:.4f}/{ud[-1]:.4f}  "
                  f"ū {ubar_m:.4f}/{ubar_d:.4f}  u軸/ū {um[0]/ubar_m:.3f}/{ud[0]/ubar_d:.3f}  "
                  f"Σnu 比 {flux_m/flux_d:.4f}  T 壁際 {md['T'][i][-1]:.4f}/{ds['T'][i][-1]:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
