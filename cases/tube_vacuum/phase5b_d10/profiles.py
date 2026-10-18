"""フェーズ 5b-2: 管の中心線と断面平均の分布を取り出し、ユーザーの DSMC(d10_q)・論文 Fig.3 と比べる。

- 中心線: cases/tube_vacuum/kinetic/centerline.py と同じ(同じ x の層で r < 0.3 のセルに a + b r² を当てる)
- 断面平均(管の中 8 ≤ x ≤ 18 の各層): 面積重みの p・ρ・u_x・T の平均と、質量流束の積分 Ṁ(x) = Σ ρ u_x A / (√π/√2 ...)
  (1/4 領域は 4 倍。層の質量流量は無次元 Ṁ の定義 ṁ / (√π R² p_in / √(2RT0)) にそろえる)

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/profiles.py runs/phase5/gsis/final_d10 runs/phase5b/d10/q_base ...
      [--labels a b ...] [--out cases/tube_vacuum/phase5b_d10/results/profiles] [--plot]
出力: <out>/<ラベル>_centerline.csv、<ラベル>_section.csv、比較の表(標準出力)、--plot で PNG
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REF = ROOT / "reference" / "paper_2610.07658"
DSMC_CL = ROOT / "validation" / "dsmc" / "runs" / "d10_q" / "analysis" / "centerline.csv"
DSMC_MDOT = ROOT / "validation" / "dsmc" / "runs" / "d10_q" / "analysis" / "mdot_profile.csv"


def load_cl_module():
    path = ROOT / "cases" / "tube_vacuum" / "kinetic" / "centerline.py"
    spec = importlib.util.spec_from_file_location("tube_centerline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_cells(path: Path):
    import csv

    with path.open(encoding="utf-8") as fh:
        rd = csv.reader(fh)
        head = next(rd)
    cols = ["x", "y", "z", "volume", "rho", "ux", "uy", "uz", "T", "p"]
    idx = [head.index(c) for c in cols]
    a = np.loadtxt(path, delimiter=",", skiprows=1, usecols=idx)
    return {c: a[:, k] for k, c in enumerate(cols)}


def section_profile(d, sym: int):
    """管の中の層ごとの断面平均。層の厚さ Δx はセル中心の並びから求める(六面体は x 方向の層)。"""
    x = d["x"]
    tube = (x > 8.0) & (x < 18.0)
    xs = np.round(x[tube], 6)
    layers = np.unique(xs)
    # 層の境界: 押し出しの六面体はセル中心が両側の節点の面の中点なので、x = 8 から e_{k+1} = 2 c_k − e_k でたどる
    edges = [8.0]
    for c in layers:
        edges.append(2.0 * c - edges[-1])
    dx = np.diff(np.array(edges))
    out = []
    for k, xv in enumerate(layers):
        m = tube.copy()
        m[tube] = np.abs(xs - xv) < 1e-6
        V = d["volume"][m]
        A = V.sum() / dx[k]  # 断面積(体積 / 厚さ)
        rho, ux, T, p = d["rho"][m], d["ux"][m], d["T"][m], d["p"][m]
        # 無次元: ρ は ρ_ref、u は √(RT0) 単位。Ṁ = ṁ / (√π R² p_in / √(2RT0)) = Σ ρ u A × √2 / √π(ρ_ref √(RT0) R² で割った量)
        mdot = sym * float(np.sum(rho * ux * V) / dx[k]) * math.sqrt(2.0) / math.sqrt(math.pi)
        out.append((xv - 8.0, sym * A, float(np.sum(p * V) / V.sum()), float(np.sum(rho * V) / V.sum()),
                    float(np.sum(rho * ux * V) / np.sum(rho * V)) / math.sqrt(2.0), float(np.sum(T * V) / V.sum()), mdot))
    return np.array(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--labels", nargs="*")
    ap.add_argument("--sym", nargs="*", type=int, help="対称の倍率(1/4 領域は 4)。省略時は cells.csv の y の最小値で判定")
    ap.add_argument("--out", default=str(HERE / "results" / "profiles"))
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()
    cl_mod = load_cl_module()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    labels = args.labels or [Path(r).name for r in args.runs]
    dsmc = np.genfromtxt(DSMC_CL, delimiter=",", names=True) if DSMC_CL.exists() else None
    res = {}
    for i, (run, lab) in enumerate(zip(args.runs, labels)):
        cells = Path(run) / "cells.csv"
        d = read_cells(cells)
        sym = args.sym[i] if args.sym else (4 if d["y"].min() > -1e-9 and d["z"].min() > -1e-9 else 1)
        cl = cl_mod.centerline(cells)
        sec = section_profile(d, sym)
        np.savetxt(out / f"{lab}_centerline.csv", cl, delimiter=",", header="X,p_over_pin,ux_over_sqrt2RT0,T_over_T0",
                   comments="", fmt="%.6g")
        np.savetxt(out / f"{lab}_section.csv", sec, delimiter=",",
                   header="X,area_R2,p_mean,rho_mean,ux_massavg_over_sqrt2RT0,T_mean,M_dot_layer", comments="", fmt="%.6g")
        res[lab] = (cl, sec)
        line = f"{lab:16s} sym {sym} 中心線"
        for X in (0.0, 2.5, 5.0, 7.5, 9.0, 9.5, 10.0):
            line += f" | X={X:g}: p {np.interp(X, cl[:, 0], cl[:, 1]):.4f} u {np.interp(X, cl[:, 0], cl[:, 2]):.4f}"
        print(line)
        print(f"{'':16s} 断面の Ṁ(x): 平均 {sec[:, 6].mean():.5f}、最小 {sec[:, 6].min():.5f}、最大 {sec[:, 6].max():.5f}")
        if dsmc is not None:
            X = dsmc["x_over_R"]
            keep = (X >= 0.15) & (X <= 9.95)
            for q, col, dc in (("p", 1, "p_over_pin"), ("u", 2, "ux_over_sqrt2RT0"), ("T", 3, "T_over_T0")):
                diff = np.interp(X[keep], cl[:, 0], cl[:, col]) - dsmc[dc][keep]
                rel = diff / dsmc[dc][keep]
                print(f"{'':16s} DSMC との差 {q}: 平均 {diff.mean():+.4f}、RMS {np.sqrt(np.mean(diff**2)):.4f}、"
                      f"相対の平均 {100*rel.mean():+.2f}%(X<5: {100*rel[X[keep]<5].mean():+.2f}%、X>5: {100*rel[X[keep]>=5].mean():+.2f}%)")
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("default")
        plt.rcParams["font.family"] = ["Meiryo", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, axs = plt.subplots(2, 2, figsize=(12, 8))
        for lab, (cl, sec) in res.items():
            axs[0, 0].plot(cl[:, 0], cl[:, 1], label=lab)
            axs[0, 1].plot(cl[:, 0], cl[:, 2], label=lab)
            axs[1, 0].plot(cl[:, 0], cl[:, 3], label=lab)
            axs[1, 1].plot(sec[:, 0], sec[:, 6], label=lab)
        if dsmc is not None:
            X = dsmc["x_over_R"]
            m = (X > -2) & (X < 14)
            axs[0, 0].plot(X[m], dsmc["p_over_pin"][m], "k.", ms=2, label="DSMC d10_q")
            axs[0, 1].plot(X[m], dsmc["ux_over_sqrt2RT0"][m], "k.", ms=2, label="DSMC d10_q")
            axs[1, 0].plot(X[m], dsmc["T_over_T0"][m], "k.", ms=2, label="DSMC d10_q")
        for qty, ax in (("pressure", axs[0, 0]), ("velocity", axs[0, 1])):
            for kind, sty in (("gsis", "r--"), ("cis", "bs")):
                f = REF / f"fig3_{qty}_{kind}_delta10.csv"
                if f.exists():
                    r = np.genfromtxt(f, delimiter=",", names=True)
                    nm = r.dtype.names
                    ax.plot(r[nm[0]], r[nm[1]], sty, ms=4, lw=1, label=f"論文 {kind.upper()}")
        axs[0, 0].set_ylabel("p/p_in(中心線)")
        axs[0, 1].set_ylabel("u_x/√(2RT0)(中心線)")
        axs[1, 0].set_ylabel("T/T0(中心線)")
        axs[1, 1].set_ylabel("断面の Ṁ(x)")
        for ax in axs.flat:
            ax.set_xlabel("X = x/R − 8(管の入口 0、出口 10)")
            ax.set_xlim(-1, 11)
            ax.grid(alpha=0.3)
            ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out / "profiles.png", dpi=110, facecolor="white")
        print(f"図: {out / 'profiles.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
