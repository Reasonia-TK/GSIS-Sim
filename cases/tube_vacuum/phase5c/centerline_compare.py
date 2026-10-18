"""フェーズ 5c: 管の中心線の p/p_in と u_x/√(2RT0) を、論文 Fig.3(デジタイズ値)とユーザーの DSMC(d1_q・d10_q)と比べる。

入力:
  本実装: <run>/cells.csv(GSIS-PBC は runs/phase5c/gsis/d<δ>、CIS 単独は runs/phase5c/cis/d<δ>。ないものは飛ばす)
  論文:   reference/paper_2610.07658/fig3_{pressure,velocity}_{gsis,cis,dsmc}_delta<δ>.csv(DSMC は δ=100 だけ)
  DSMC:   validation/dsmc/runs/d{1,10}_q/analysis/centerline.csv(管の入口が x = 0。p・u の当てはめ値と標準誤差)
中心線の値は cases/tube_vacuum/kinetic/centerline.py と同じ(同じ x の層の r < 0.3 のセルに a + b r² を当て、r = 0 の値)。
メッシュの x は上流槽の入口が 0 なので、管の X = x − 8。

比べ方(spec §1.4): 参照の点(論文の線・記号、DSMC のスラブの中心)のうち X ∈ [0.15, 10] のものだけを使い(外挿しない)、
比べる側を参照の X に線形補間して 差 = 比べる側 − 参照 を取る。平均・RMS・最大(とその X)。
論文の線どうし・DSMC どうしの差(論文 GSIS − ユーザーの DSMC など)も参考に出す。

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5c/centerline_compare.py [--gsis-root runs/phase5c/gsis]
      [--cis-root runs/phase5c/cis] [--fig-dir docs/figures]
出力: cases/tube_vacuum/phase5c/results/centerline_diff.csv、centerline_<系列>_d<δ>.csv、
      docs/figures/centerline_tube.png(中心線)、centerline_diff.png(差)
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REF = ROOT / "reference" / "paper_2610.07658"
DSMC = {1: ROOT / "validation/dsmc/runs/d1_q/analysis/centerline.csv",
        10: ROOT / "validation/dsmc/runs/d10_q/analysis/centerline.csv"}
DELTAS = (1, 10, 100)
X_MIN, X_MAX = 0.15, 10.0

# 色(データ可視化の参照パレットの 1〜3 番。論文の値は中立の黒・灰色)
C_GSIS = "#2a78d6"   # 本実装 GSIS-PBC
C_CIS = "#eb6834"    # 本実装 CIS 単独
C_DSMC = "#1baf7a"   # ユーザーの DSMC
C_PAPER = "#0b0b0b"
C_PAPER_CIS = "#7a7974"


def load_centerline_module():
    path = ROOT / "cases" / "tube_vacuum" / "kinetic" / "centerline.py"
    spec = importlib.util.spec_from_file_location("tube_centerline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_xy(path: Path):
    d = np.genfromtxt(path, delimiter=",", names=True)
    n = d.dtype.names
    return np.asarray(d[n[0]], float), np.asarray(d[n[1]], float)


def ours(run: Path, cl_mod):
    """本実装の中心線(X, p, u, T)。cells.csv がなければ None"""
    cells = run / "cells.csv"
    if not cells.exists():
        return None
    cl = cl_mod.centerline(cells)  # 列: x − 8, p, u_x/√2, T
    return {"X": cl[:, 0], "p": cl[:, 1], "u": cl[:, 2], "T": cl[:, 3]}


def dsmc(delta: int):
    path = DSMC.get(delta)
    if path is None or not path.exists():
        return None
    d = np.genfromtxt(path, delimiter=",", names=True)
    return {"X": d["x_over_R"], "p": d["p_over_pin"], "p_se": d["p_se"], "u": d["ux_over_sqrt2RT0"],
            "u_se": d["ux_se"]}


def paper(qty: str, method: str, delta: int):
    path = REF / f"fig3_{'pressure' if qty == 'p' else 'velocity'}_{method}_delta{delta}.csv"
    if not path.exists():
        return None
    return read_xy(path)


def diff_stats(X_cmp, V_cmp, X_ref, V_ref):
    """比べる側(X_cmp, V_cmp、X の昇順)を参照の点に補間した差の統計。参照の点は [X_MIN, X_MAX] かつ比べる側の範囲の中だけ"""
    keep = (X_ref >= X_MIN) & (X_ref <= X_MAX) & (X_ref >= X_cmp.min()) & (X_ref <= X_cmp.max())
    if not np.any(keep):
        return None
    xr = X_ref[keep]
    d = np.interp(xr, X_cmp, V_cmp) - V_ref[keep]
    k = int(np.argmax(np.abs(d)))
    rel = d / np.where(np.abs(V_ref[keep]) > 1e-12, V_ref[keep], np.nan)
    return dict(n=int(keep.sum()), x_lo=float(xr.min()), x_hi=float(xr.max()), mean=float(np.mean(d)),
                rms=float(np.sqrt(np.mean(d**2))), max_abs=float(abs(d[k])), x_at_max=float(xr[k]),
                mean_rel=float(np.nanmean(rel)))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--gsis-root", default=str(ROOT / "runs/phase5c/gsis"))
    ap.add_argument("--cis-root", default=str(ROOT / "runs/phase5c/cis"))
    ap.add_argument("--fig-dir", default=str(ROOT / "docs/figures"))
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    ap.add_argument("--name-format", default="d{delta}", help="計算のフォルダ名(例: final_d{delta})")
    ap.add_argument("--cis-name-100", default="d100_nsf",
                    help="δ=100 の CIS 単独のフォルダ名(既定は NSF の初期場から始めたもの。なければ d100)")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()
    cl_mod = load_centerline_module()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = {}
    for delta in DELTAS:
        name = a.name_format.format(delta=delta)
        cis_name = name
        if delta == 100 and (Path(a.cis_root) / a.cis_name_100 / "cells.csv").exists():
            cis_name = a.cis_name_100
        s = {"gsis": ours(Path(a.gsis_root) / name, cl_mod), "cis": ours(Path(a.cis_root) / cis_name, cl_mod),
             "dsmc": dsmc(delta), "cis_name": cis_name}
        for q in ("p", "u"):
            s[f"paper_gsis_{q}"] = paper(q, "gsis", delta)
            s[f"paper_cis_{q}"] = paper(q, "cis", delta)
            s[f"paper_dsmc_{q}"] = paper(q, "dsmc", delta)
        data[delta] = s
        for key in ("gsis", "cis"):
            c = s[key]
            if c is None:
                continue
            with (out_dir / f"centerline_{key}_d{delta}.csv").open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["X", "p_over_pin", "ux_over_sqrt2RT0", "T_over_T0"])
                for row in zip(c["X"], c["p"], c["u"], c["T"]):
                    w.writerow([f"{v:.6g}" for v in row])

    # 差の表
    rows = []
    labels = {"gsis": "本実装 GSIS-PBC", "cis": "本実装 CIS 単独", "paper_gsis": "論文 GSIS-PBC(線)",
              "paper_cis": "論文 CIS(記号)", "paper_dsmc": "論文 DSMC(記号)", "dsmc": "ユーザーの DSMC"}
    for delta, s in data.items():
        for q in ("p", "u"):
            refs = {}
            for k in ("paper_gsis", "paper_cis", "paper_dsmc"):
                if s[f"{k}_{q}"] is not None:
                    refs[k] = s[f"{k}_{q}"]
            if s["dsmc"] is not None:
                refs["dsmc"] = (s["dsmc"]["X"], s["dsmc"][q])
            cmps = {}
            for k in ("gsis", "cis"):
                if s[k] is not None:
                    cmps[k] = (s[k]["X"], s[k][q])
            # 論文の線(GSIS)も比べる側にする(DSMC との差の参考)
            if s[f"paper_gsis_{q}"] is not None:
                xg, vg = s[f"paper_gsis_{q}"]
                o = np.argsort(xg)
                cmps["paper_gsis"] = (xg[o], vg[o])
            for ck, (xc, vc) in cmps.items():
                for rk, (xr, vr) in refs.items():
                    if ck == rk or (ck == "paper_gsis" and rk not in ("dsmc", "paper_dsmc", "paper_cis")):
                        continue
                    st = diff_stats(xc, vc, xr, vr)
                    if st is None:
                        continue
                    rows.append(dict(delta=delta, quantity="p/p_in" if q == "p" else "u_x/sqrt(2RT0)",
                                     compared=(labels[ck] + ("(NSF から)" if ck == "cis" and s.get("cis_name", "").endswith("_nsf") else "")), reference=labels[rk], **st))
    with (out_dir / "centerline_diff.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.5g}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"{'δ':>4} {'量':<15} {'比べる側':<18} {'参照':<18} {'点':>4} {'平均':>9} {'RMS':>8} {'最大':>8} {'X':>6} {'平均(相対)':>10}")
    for r in rows:
        print(f"{r['delta']:>4} {r['quantity']:<15} {r['compared']:<18} {r['reference']:<18} {r['n']:>4} {r['mean']:>+9.4f} "
              f"{r['rms']:>8.4f} {r['max_abs']:>8.4f} {r['x_at_max']:>6.2f} {100 * r['mean_rel']:>+9.2f}%")
    print(f"表: {out_dir / 'centerline_diff.csv'}")
    # 代表点の値
    for delta, s in data.items():
        for k in ("gsis", "cis"):
            c = s[k]
            if c is None:
                continue
            vals = "、".join(f"X={X:g}: p {np.interp(X, c['X'], c['p']):.4f}・u {np.interp(X, c['X'], c['u']):.4f}"
                            for X in (0.0, 2.5, 5.0, 7.5, 10.0))
            print(f"δ={delta} {labels[k]}: {vals}")

    if not a.no_plot:
        plot(data, Path(a.fig_dir))
    return 0


def plot(data, fig_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": ["BIZ UDPGothic", "Yu Gothic", "Meiryo", "DejaVu Sans"],
        "font.size": 10, "axes.edgecolor": "#52514e", "axes.labelcolor": "#0b0b0b", "xtick.color": "#52514e",
        "ytick.color": "#52514e", "axes.grid": True, "grid.color": "#e4e3df", "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False, "figure.dpi": 150,
    })
    fig_dir.mkdir(parents=True, exist_ok=True)

    # 図 1: 中心線(行: p・u、列: δ)
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 7.6), constrained_layout=True)
    for j, delta in enumerate(DELTAS):
        s = data[delta]
        for i, q in enumerate(("p", "u")):
            ax = axes[i, j]
            pg = s[f"paper_gsis_{q}"]
            if pg is not None:
                ax.plot(pg[0], pg[1], ".", color=C_PAPER, ms=1.6, label="論文 GSIS-PBC(線のデジタイズ)")
            pc = s[f"paper_cis_{q}"]
            if pc is not None:
                ax.plot(pc[0], pc[1], "s", mfc="none", mec=C_PAPER_CIS, ms=6, mew=1.2, label="論文 CIS(記号)")
            pd = s[f"paper_dsmc_{q}"]
            if pd is not None:
                ax.plot(pd[0], pd[1], "o", mfc="none", mec=C_PAPER, ms=6.5, mew=1.2, label="論文 DSMC(δ=100)")
            ds = s["dsmc"]
            if ds is not None:
                m = (ds["X"] >= -0.5) & (ds["X"] <= 10.5)
                ax.errorbar(ds["X"][m][::2], ds[q][m][::2], yerr=2 * ds[f"{q}_se"][m][::2], fmt="^", color=C_DSMC, ms=4.5,
                            elinewidth=0.8, capsize=0, label="ユーザーの DSMC(±2σ)", zorder=3)
            c = s["cis"]
            if c is not None:
                cis_label = "本実装 CIS 単独" + ("(NSF の初期場から)" if s.get("cis_name", "").endswith("_nsf") else "")
                ax.plot(c["X"], c[q], "--", color=C_CIS, lw=1.8, label=cis_label, zorder=4)
            g = s["gsis"]
            if g is not None:
                ax.plot(g["X"], g[q], "-", color=C_GSIS, lw=2.0, label="本実装 GSIS-PBC", zorder=5)
            ax.set_xlim(-0.3, 10.3)
            ax.set_xlabel("X = x/R(管の入口 0、出口 10)")
            ax.set_ylabel("p / p_in" if q == "p" else "u_x / √(2RT₀)")
            ax.set_title(f"δ = {delta}:{'圧力' if q == 'p' else '軸方向速度'}", loc="left", fontsize=10.5)
            if q == "p":
                ax.set_ylim(0.0, 1.02)
            else:
                ax.set_ylim(0.0, 1.0 if delta < 100 else 1.05)
            ax.legend(fontsize=7.5, loc="upper left" if q == "u" else "lower left")
    fig.suptitle("管の中心線の分布:本実装と論文 Fig.3(デジタイズ値)・ユーザーの DSMC", fontsize=12, x=0.01, ha="left")
    out1 = fig_dir / "centerline_tube.png"
    fig.savefig(out1)
    plt.close(fig)

    # 図 2: 差(本実装 − 参照)
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 6.8), constrained_layout=True, sharex=True)
    for j, delta in enumerate(DELTAS):
        s = data[delta]
        for i, q in enumerate(("p", "u")):
            ax = axes[i, j]
            ax.axhline(0.0, color="#52514e", lw=0.8)
            cis_label = "本実装 CIS 単独" + ("(NSF から)" if s.get("cis_name", "").endswith("_nsf") else "")
            for key, color, style, name in (("gsis", C_GSIS, "-", "本実装 GSIS-PBC"), ("cis", C_CIS, "--", cis_label)):
                c = s[key]
                if c is None:
                    continue
                pg = s[f"paper_gsis_{q}"]
                if pg is not None:
                    keep = (pg[0] >= X_MIN) & (pg[0] <= X_MAX)
                    xr = pg[0][keep]
                    o = np.argsort(xr)
                    d = np.interp(xr[o], c["X"], c[q]) - pg[1][keep][o]
                    ax.plot(xr[o], d, style, color=color, lw=1.6, label=f"{name} − 論文 GSIS-PBC")
                ref = None
                if s["dsmc"] is not None:
                    ref = (s["dsmc"]["X"], s["dsmc"][q], "ユーザーの DSMC")
                elif s[f"paper_dsmc_{q}"] is not None:
                    ref = (s[f"paper_dsmc_{q}"][0], s[f"paper_dsmc_{q}"][1], "論文 DSMC")
                if ref is not None:
                    keep = (ref[0] >= X_MIN) & (ref[0] <= X_MAX)
                    xr = ref[0][keep]
                    d = np.interp(xr, c["X"], c[q]) - ref[1][keep]
                    mk = "^" if ref[2] == "ユーザーの DSMC" else "o"
                    ax.plot(xr[::2] if mk == "^" else xr, d[::2] if mk == "^" else d, mk, mfc="none", mec=color, ms=4.5,
                            mew=1.0, label=f"{name} − {ref[2]}")
            ax.set_xlim(0.0, 10.2)
            ax.set_ylabel("差 Δ(p / p_in)" if q == "p" else "差 Δ(u_x / √(2RT₀))")
            ax.set_title(f"δ = {delta}:{'圧力' if q == 'p' else '軸方向速度'}の差", loc="left", fontsize=10.5)
            if i == 1:
                ax.set_xlabel("X = x/R")
            ax.legend(fontsize=7.0, loc="best", framealpha=0.85, frameon=True, edgecolor="none")
    fig.suptitle("中心線の差(本実装 − 参照)。参照の点で比べる(X ≥ 0.15、外挿なし)", fontsize=12, x=0.01, ha="left")
    out2 = fig_dir / "centerline_diff.png"
    fig.savefig(out2)
    plt.close(fig)
    print(f"図: {out1}、{out2}")


if __name__ == "__main__":
    sys.exit(main())
