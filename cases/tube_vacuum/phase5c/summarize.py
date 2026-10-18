"""フェーズ 5c: 管 4 条件の結果をまとめる(論文 Table 1・ユーザーの DSMC との比較の表と、収束の履歴・断面の流量の図)。

入力:
  GSIS-PBC: runs/phase5c/gsis/d{1,10,100}(δ=1000 は runs/phase5c/gsis/d1000 があれば。未収束)
  CIS 単独: runs/phase5c/cis/d{1,10,100}
  δ=1000 の GSIS の履歴(フェーズ 5b-1): runs/phase5b/d1000/f_p5b/history.csv
  論文: reference/paper_2610.07658/table1_tube.csv
  DSMC: validation/dsmc/runs/{d1_q,d10_q,d10_q_ppc14,d10_q_dt}/analysis/summary.json(あるものだけ)

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5c/summarize.py [--no-plot]
出力: cases/tube_vacuum/phase5c/results/tube_results.csv、標準出力の表(Markdown)、
      docs/figures/convergence_history.png、docs/figures/section_mass_flow.png
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNS = ROOT / "runs" / "phase5c"
TABLE1 = ROOT / "reference" / "paper_2610.07658" / "table1_tube.csv"
DSMC_RUNS = {"d1_q": 1, "d10_q": 10, "d10_q_ppc14": 10, "d10_q_dt": 10}
DELTAS = (1, 10, 100, 1000)

C_GSIS = "#2a78d6"
C_CIS = "#eb6834"
C_DSMC = "#1baf7a"
C_PAPER = "#0b0b0b"


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def table1():
    out = {}
    with TABLE1.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = int(float(r["delta_rp"]))
            out[d] = {k: (float(v) if v not in ("", None) and k != "ref_method" else v) for k, v in r.items()}
    return out


def dsmc():
    out = {}
    for name, delta in DSMC_RUNS.items():
        s = load_json(ROOT / "validation" / "dsmc" / "runs" / name / "analysis" / "summary.json")
        if s is None:
            continue
        m = s["mdot"]
        out[name] = dict(delta=delta, M=m["tube_mean"], se=m["tube_se"], M_down=m.get("downstream_mean"),
                         p_res=s.get("reservoir", {}).get("p_res_over_pin"), steps=s.get("log", {}).get("finished", {}).get("step"),
                         wall_s=s.get("log", {}).get("finished", {}).get("wall_time_s"))
    return out


def run_info(run: Path):
    s = load_json(run / "summary.json")
    if s is None:
        return None
    mf = s.get("mass_flow", {})
    info = dict(iterations=s["iterations"], converged=s["converged"], epsilon=s.get("epsilon"),
                wall_s=s["wall_time_s"], M_in=mf.get("M_dot_inlet"), M_out=mf.get("M_dot_outlet"),
                cis_iterations=s.get("cis_iterations", s["iterations"]))
    if "initialization" in s:
        info["init_s"] = s["initialization"].get("time_s")
    if "time_per_iteration_breakdown_s" in s:
        t = s["time_per_iteration_breakdown_s"]
        info["t_iter"] = t.get("total")
        info["t_cis"] = t.get("cis")
        info["t_pbc"] = t.get("pbc")
    elif "time_per_iteration_s" in s:
        info["t_iter"] = s["time_per_iteration_s"]
    mp = s.get("mass_flow_pbc")
    if mp:
        info["M_pbc_in"] = mp.get("M_dot_inlet")
        info["M_pbc_out"] = mp.get("M_dot_outlet")
    secs = s.get("mass_flow_sections", {}).get("sections", [])
    if secs:
        info["sec_X"] = [e["face_position"] - 8.0 for e in secs]
        info["sec_kin"] = [e.get("M_dot_kinetic") for e in secs]
        info["sec_half"] = [e.get("M_dot_kinetic_half") for e in secs]
        info["sec_pbc"] = [e.get("M_dot_pbc") for e in secs]
    mb = s.get("mass_balance", {})
    info["imbalance"] = mb.get("kinetic", {}).get("imbalance")
    info["imbalance_pbc"] = mb.get("pbc", {}).get("imbalance")
    info["within_tol"] = mb.get("within_tolerance")
    return info


def pct(a, b):
    if a is None or b is None or not (isinstance(a, float) and isinstance(b, float)) or b == 0:
        return float("nan")
    return 100.0 * (a / b - 1.0)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--fig-dir", default=str(ROOT / "docs" / "figures"))
    a = ap.parse_args()
    t1 = table1()
    ds = dsmc()
    rows = []
    gsis = {d: run_info(RUNS / "gsis" / f"d{d}") for d in DELTAS}
    cis = {d: run_info(RUNS / "cis" / f"d{d}") for d in DELTAS}
    cis_nsf100 = run_info(RUNS / "cis" / "d100_nsf")

    print("## GSIS-PBC(本実装)と論文・DSMC")
    print("| δ | 反復(論文) | CIS 回数 | 時間 [s](初期化) | Ṁ 入口 / 出口 | 断面 X=0〜10 | 論文 GSIS | 差(入口〜出口) | 参照 | 差 | ユーザーの DSMC | 差 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in DELTAS:
        g = gsis[d]
        p = t1[d]
        if g is None:
            continue
        secs = [x for x in g.get("sec_kin", []) if x is not None]
        lo = min([g["M_in"], g["M_out"]] + secs)
        hi = max([g["M_in"], g["M_out"]] + secs)
        u = [v for k, v in ds.items() if v["delta"] == d and k.endswith("_q")]
        ud = u[0] if u else None
        print(f"| {d} | {g['iterations']}({int(p['gsis_pbc_iter'])}) | {g['cis_iterations']} | {g['wall_s']:.0f}({g.get('init_s', 0):.0f}) | "
              f"{g['M_in']:.4f} / {g['M_out']:.4f} | {min(secs):.4f}〜{max(secs):.4f} | {p['gsis_pbc_M']:.3f} | "
              f"{pct(lo, p['gsis_pbc_M']):+.2f}〜{pct(hi, p['gsis_pbc_M']):+.2f}% | {p['M_ref']:.3f}({p['ref_method']}) | "
              f"{pct(g['M_in'], p['M_ref']):+.2f}% | {ud['M']:.5f} ± {ud['se']:.5f} | {pct(g['M_in'], ud['M']):+.2f}% |"
              if ud else
              f"| {d} | {g['iterations']}({int(p['gsis_pbc_iter'])}) | {g['cis_iterations']} | {g['wall_s']:.0f}({g.get('init_s', 0):.0f}) | "
              f"{g['M_in']:.4f} / {g['M_out']:.4f} | {min(secs):.4f}〜{max(secs):.4f} | {p['gsis_pbc_M']:.3f} | "
              f"{pct(lo, p['gsis_pbc_M']):+.2f}〜{pct(hi, p['gsis_pbc_M']):+.2f}% | {p['M_ref']:.3f}({p['ref_method']}) | "
              f"{pct(g['M_in'], p['M_ref']):+.2f}% | — | — |")
        rows.append(dict(method="GSIS-PBC", delta=d, iterations=g["iterations"], cis_iterations=g["cis_iterations"],
                         converged=g["converged"], wall_s=g["wall_s"], init_s=g.get("init_s"), M_inlet=g["M_in"],
                         M_outlet=g["M_out"], M_sections_min=min(secs), M_sections_max=max(secs),
                         M_pbc_inlet=g.get("M_pbc_in"), M_pbc_outlet=g.get("M_pbc_out"), imbalance=g["imbalance"],
                         imbalance_pbc=g["imbalance_pbc"], paper_M=p["gsis_pbc_M"], paper_iter=p["gsis_pbc_iter"],
                         paper_time_h=p["gsis_pbc_time_h"], ref_M=p["M_ref"], ref_method=p["ref_method"],
                         user_dsmc=ud["M"] if ud else None))
    print()
    print("## CIS 単独(本実装)と論文の CIS")
    print("| δ | 反復(論文) | 収束 | ε | 時間 [s] | 1 反復 [s] | Ṁ 入口 / 出口 | 断面 X=0〜10 | 論文 CIS | 論文 GSIS |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for d, c, start in [(d, cis[d], "一様") for d in DELTAS] + [(100, cis_nsf100, "NSF")]:
        p = t1[d]
        if c is None:
            continue
        secs = [x for x in c.get("sec_kin", []) if x is not None]
        print(f"| {d}({start}) | {c['iterations']}({int(p['cis_iter'])}) | {'○' if c['converged'] else '×'} | {c['epsilon']:.2e} | "
              f"{c['wall_s']:.0f} | {c.get('t_iter', float('nan')):.2f} | {c['M_in']:.4f} / {c['M_out']:.4f} | "
              f"{min(secs):.4f}〜{max(secs):.4f} | {p['cis_M']:.3f}({int(p['cis_iter'])} 回) | {p['gsis_pbc_M']:.3f} |")
        rows.append(dict(method="CIS" if start == "一様" else "CIS_from_NSF", delta=d, iterations=c["iterations"],
                         cis_iterations=c["iterations"],
                         converged=c["converged"], wall_s=c["wall_s"], M_inlet=c["M_in"], M_outlet=c["M_out"],
                         M_sections_min=min(secs) if secs else None, M_sections_max=max(secs) if secs else None,
                         imbalance=c["imbalance"], paper_M=p["cis_M"], paper_iter=p["cis_iter"], paper_time_h=p["cis_time_h"],
                         ref_M=p["M_ref"], ref_method=p["ref_method"]))
    print()
    print("## δ=100 の GSIS のメッシュ依存性(1/4 領域、Ṁ は 4 倍)")
    print("| メッシュ | セル(1/4) | 反復 | 時間 [s] | Ṁ 入口 / 出口 | 管の断面 | PBC の断面 |")
    print("|---|---|---|---|---|---|---|")
    for name, note in (("d100_q_base", "本番の 1/4"), ("d100_q_dsw025", "壁の最初のセル 1/4"), ("d100_q_r150", "全体 1.5 倍")):
        q = run_info(RUNS / "gsis_q" / name)
        s = load_json(RUNS / "gsis_q" / name / "summary.json")
        if q is None:
            continue
        k = 4.0
        secs = [k * x for x in q.get("sec_kin", []) if x is not None]
        psecs = [k * x for x in q.get("sec_pbc", []) if x is not None]
        print(f"| {note} | {s['n_cells']:,} | {q['iterations']} | {q['wall_s']:.0f} | {k * q['M_in']:.4f} / {k * q['M_out']:.4f} | "
              f"{min(secs):.4f}〜{max(secs):.4f} | {min(psecs):.4f}〜{max(psecs):.4f} |")
        rows.append(dict(method=f"GSIS-PBC quarter {name}", delta=100, iterations=q["iterations"], wall_s=q["wall_s"],
                         M_inlet=k * q["M_in"], M_outlet=k * q["M_out"], M_sections_min=min(secs), M_sections_max=max(secs)))
    print()
    print("## ユーザーの DSMC(δ=10 の感度)")
    for k, v in ds.items():
        print(f"- {k}: Ṁ = {v['M']:.5f} ± {v['se']:.5f}(下流槽 {v['M_down']:.5f}、上流槽の圧力 {v['p_res']:.4f} p_in、"
              f"{v['steps']} ステップ、{v['wall_s']:.0f} s)")
    out = HERE / "results" / "tube_results.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({k for r in rows for k in r})
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"\n表: {out}")
    if not a.no_plot:
        plot_history(Path(a.fig_dir), t1)
        # δ=100 の断面の図は NSF の初期場から始めた CIS 単独(一様な初期状態からは管に流れが行き渡る前に打ち切った)
        plot_sections(Path(a.fig_dir), gsis, {**cis, 100: cis_nsf100 if cis_nsf100 is not None else cis[100]}, ds,
                      nsf100=cis_nsf100 is not None)
    return 0


def read_history(path: Path, cols):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        data = {c: [] for c in cols}
        for r in rd:
            for c in cols:
                v = r.get(c, "")
                data[c].append(float(v) if v not in ("", "nan") else math.nan)
    return {c: np.array(v) for c, v in data.items()}


def style():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": ["BIZ UDPGothic", "Yu Gothic", "Meiryo", "DejaVu Sans"],
        "font.size": 10, "axes.edgecolor": "#52514e", "axes.labelcolor": "#0b0b0b", "xtick.color": "#52514e",
        "ytick.color": "#52514e", "axes.grid": True, "grid.color": "#e4e3df", "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False, "figure.dpi": 150,
    })
    return plt


def plot_history(fig_dir: Path, t1) -> None:
    """ε(式 (59))を CIS 反復の累計に対して描く(GSIS は 1 反復 = CIS 5 回)"""
    plt = style()
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.0), constrained_layout=True, sharey=True)
    for ax, d in zip(axes, DELTAS):
        g = read_history(RUNS / "gsis" / f"d{d}" / "history.csv", ["cis_iterations", "epsilon"])
        if g is None and d == 1000:
            g = read_history(ROOT / "runs/phase5b/d1000/f_p5b/history.csv", ["cis_iterations", "epsilon"])
        c = read_history(RUNS / "cis" / f"d{d}" / "history.csv", ["iteration", "epsilon"])
        if c is not None:
            ax.semilogy(c["iteration"], c["epsilon"], "-", color=C_CIS, lw=1.6, label="本実装 CIS 単独(一様な初期状態)")
        cn = read_history(RUNS / "cis" / f"d{d}_nsf" / "history.csv", ["iteration", "epsilon"])
        if cn is not None:
            ax.semilogy(cn["iteration"], cn["epsilon"], ":", color=C_CIS, lw=1.8, label="本実装 CIS 単独(NSF の初期場から)")
        if g is not None:
            ax.semilogy(g["cis_iterations"], g["epsilon"], "o-", color=C_GSIS, lw=2.0, ms=4,
                        label="本実装 GSIS-PBC" + ("(未収束)" if d == 1000 else ""))
        ax.axhline(1e-5, color="#52514e", lw=0.8, ls=":")
        p = t1[d]
        ax.axvline(5 * p["gsis_pbc_iter"], color=C_GSIS, lw=0.8, ls="--")
        ax.axvline(p["cis_iter"], color=C_CIS, lw=0.8, ls="--")
        ax.text(5 * p["gsis_pbc_iter"], 3e-1, f" 論文 GSIS\n {int(p['gsis_pbc_iter'])} 回", color="#52514e", fontsize=7.5, va="top")
        ax.text(p["cis_iter"], 3e-2, f" 論文 CIS\n {int(p['cis_iter'])} 回", color="#52514e", fontsize=7.5, va="top")
        ax.set_xscale("log")
        ax.set_xlim(1, 1.2e4)
        ax.set_ylim(3e-8, 10)  # 凡例を判定の線(10⁻⁵)の下に置く
        ax.set_title(f"δ = {d}", loc="left")
        ax.set_xlabel("CIS 反復の累計(GSIS は 1 回 = CIS 5 回)")
        if d == 1:
            ax.set_ylabel("ε(式 (59))")
        ax.legend(fontsize=8, loc="lower left")
    fig.suptitle("収束の履歴: 縦の破線は論文の反復数(GSIS は CIS 回数に換算)、点線は判定 ε = 10⁻⁵", x=0.01, ha="left", fontsize=11)
    out = fig_dir / "convergence_history.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"図: {out}")


def plot_sections(fig_dir: Path, gsis, cis, ds, nsf100: bool = False) -> None:
    """管の断面の質量流量(最後の f と PBC)と入口・出口の値、DSMC のスラブの ṁ(x)"""
    plt = style()
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2), constrained_layout=True)
    for ax, d in zip(axes, (1, 10, 100)):
        g = gsis[d]
        c = cis[d]
        vals = []
        # ユーザーの DSMC: 管のスラブの平均 ± 2σ(スラブごとの値は統計のばらつきが大きいので帯で示す)
        u = [v for k, v in ds.items() if v["delta"] == d and k == f"d{d}_q"]
        if u:
            m, se = u[0]["M"], u[0]["se"]
            ax.fill_between([0, 10], [m - 2 * se] * 2, [m + 2 * se] * 2, color=C_DSMC, alpha=0.25, lw=0,
                            label=f"ユーザーの DSMC(管の平均 ±2σ)")
            ax.plot([0, 10], [m, m], "-", color=C_DSMC, lw=1.2)
            vals += [m - 2 * se, m + 2 * se]
        if g is not None and "sec_X" in g:
            X = np.array(g["sec_X"])
            ax.plot(X, g["sec_kin"], "o-", color=C_GSIS, lw=2, ms=5, label="GSIS-PBC 運動論(最後の f)")
            ax.plot(X, g["sec_pbc"], "s--", color=C_GSIS, lw=1.2, ms=4.5, mfc="none", label="GSIS-PBC の PBC(Wⁿ⁺¹)")
            ax.plot([-8.0, 14.0], [g["M_in"], g["M_out"]], ">", color=C_GSIS, ms=8, ls="none",
                    label="GSIS-PBC 入口・出口(境界)")
            vals += [g["M_in"], g["M_out"]] + [v for v in g["sec_kin"] + g["sec_pbc"] if v is not None]
        if c is not None and "sec_X" in c:
            X = np.array(c["sec_X"])
            lab = "CIS 単独(NSF の初期場から、終了の時点)" if (d == 100 and nsf100) else "CIS 単独(終了の時点)"
            ax.plot(X, c["sec_kin"], "^-", color=C_CIS, lw=1.6, ms=5, label=lab)
            ax.plot([-8.0, 14.0], [c["M_in"], c["M_out"]], ">", color=C_CIS, ms=8, mfc="none", ls="none",
                    label="CIS 単独 入口・出口(境界)")
            vals += [c["M_out"]] + [v for v in c["sec_kin"] if v is not None]
        ax.axvspan(0, 10, color="#f0efec", zorder=0)
        ax.set_xlim(-8.8, 14.8)
        ax.set_xlabel("X = x/R(管 0〜10、上流槽の入口 −8、下流槽の出口 14)")
        ax.set_ylabel(r"$\dot{M}$")
        ax.set_title(f"δ = {d}", loc="left")
        if vals:
            lo, hi = min(vals), max(vals)
            pad = max(0.15 * (hi - lo), 0.002 * hi)
            ax.set_ylim(lo - pad, hi + pad)
            # 範囲の外の値(CIS 単独の入口など)は注記する
            if c is not None and not (lo - pad <= c["M_in"] <= hi + pad):
                ax.annotate(f"CIS 単独の入口 {c['M_in']:.3f}(範囲外)", xy=(-8.0, lo - pad), xytext=(-7.6, lo + 0.1 * pad),
                            fontsize=7.5, color=C_CIS, arrowprops=dict(arrowstyle="->", color=C_CIS, lw=0.8))
        ax.legend(fontsize=7.0, loc="best")
    fig.suptitle("断面の質量流量(管の X = 0, 2.5, 5, 7.5, 10 の面の離散的な流束の和)と境界の流量", x=0.01, ha="left", fontsize=11)
    out = fig_dir / "section_mass_flow.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"図: {out}")


if __name__ == "__main__":
    sys.exit(main())
