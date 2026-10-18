"""フェーズ 5b-2: 中心線(p, u_x)と断面の u_x(r)を、本実装・ユーザーの DSMC・論文 Fig.3 で重ねて描く。

実行(リポジトリのルートから。先に profiles.py と radial.py で CSV を作っておく):
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/plot_compare.py [ラベル ...]
出力: cases/tube_vacuum/phase5b_d10/results/compare_d10.png
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REF = ROOT / "reference" / "paper_2610.07658"
DSMC_CL = ROOT / "validation" / "dsmc" / "runs" / "d10_q" / "analysis" / "centerline.csv"


def main() -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("default")
    plt.rcParams["font.family"] = ["Meiryo", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    labels = sys.argv[1:] or ["q_base"]
    prof = HERE / "results" / "profiles"
    rad = HERE / "results" / "radial"
    fig, axs = plt.subplots(2, 3, figsize=(16, 9))
    dc = np.genfromtxt(DSMC_CL, delimiter=",", names=True)

    def ref(name):
        a = np.genfromtxt(REF / name, delimiter=",", names=True)
        nm = a.dtype.names
        o = np.argsort(a[nm[0]])
        return a[nm[0]][o], a[nm[1]][o]

    pg, pc = ref("fig3_pressure_gsis_delta10.csv"), ref("fig3_pressure_cis_delta10.csv")
    ug, uc = ref("fig3_velocity_gsis_delta10.csv"), ref("fig3_velocity_cis_delta10.csv")
    colors = ["C0", "C2", "C3", "C4", "C5", "C6"]
    for k, lab in enumerate(labels):
        cl = np.genfromtxt(prof / f"{lab}_centerline.csv", delimiter=",", names=True)
        c = colors[k % len(colors)]
        axs[0, 0].plot(cl["X"], cl["p_over_pin"], c, lw=1.5, label=f"本実装 {lab}")
        axs[0, 1].plot(cl["X"], cl["ux_over_sqrt2RT0"], c, lw=1.5, label=f"本実装 {lab}")
        # 相対差(DSMC の点で)
        m = (dc["x_over_R"] > 0.1) & (dc["x_over_R"] < 9.95)
        X = dc["x_over_R"][m]
        axs[0, 2].plot(X, 100 * (np.interp(X, cl["X"], cl["p_over_pin"]) / dc["p_over_pin"][m] - 1), c + "-",
                       lw=1, label=f"p: {lab} / DSMC − 1")
        axs[0, 2].plot(X, 100 * (np.interp(X, cl["X"], cl["ux_over_sqrt2RT0"]) / dc["ux_over_sqrt2RT0"][m] - 1),
                       c + "--", lw=1, label=f"u: {lab} / DSMC − 1")
        f = rad / f"{lab}_radial.csv"
        if f.exists():
            r = np.genfromtxt(f, delimiter=",", names=True)
            for j, Xs in enumerate((2.5, 5.0, 7.5)):
                s = r["X_station"] == Xs
                axs[1, j].plot(r["r_mid"][s], r["u"][s], c + "o-", ms=4, label=f"本実装 {lab}(X = {r['X_used'][s][0]:.2f})")
    m = (dc["x_over_R"] > -1) & (dc["x_over_R"] < 10.5)
    axs[0, 0].plot(dc["x_over_R"][m], dc["p_over_pin"][m], "k.", ms=3, label="ユーザーの DSMC d10_q")
    axs[0, 1].plot(dc["x_over_R"][m], dc["ux_over_sqrt2RT0"][m], "k.", ms=3, label="ユーザーの DSMC d10_q")
    axs[0, 0].plot(*pg, "r--", lw=1, label="論文 GSIS-PBC(デジタイズ)")
    axs[0, 0].plot(*pc, "bv", ms=5, label="論文 CIS(デジタイズ)")
    axs[0, 1].plot(*ug, "r--", lw=1, label="論文 GSIS-PBC(デジタイズ)")
    axs[0, 1].plot(*uc, "bv", ms=5, label="論文 CIS(デジタイズ)")
    for X0, Pp, lab2, st in ((pg[0], pg[1], "論文 GSIS / DSMC − 1", "r:"), (pc[0], pc[1], "論文 CIS / DSMC − 1", "bv")):
        mm = (X0 > 0.15) & (X0 < 9.9)
        axs[0, 2].plot(X0[mm], 100 * (Pp[mm] / np.interp(X0[mm], dc["x_over_R"], dc["p_over_pin"]) - 1), st, ms=4, lw=1,
                       label="p: " + lab2)
    dr = np.genfromtxt(rad / "dsmc_radial.csv", delimiter=",", names=True)
    for j, Xs in enumerate((2.5, 5.0, 7.5)):
        s = dr["X_station"] == Xs
        axs[1, j].plot(dr["r_mid"][s], dr["u"][s], "ks--", ms=4, label=f"DSMC(X = {dr['X_used'][s][0]:.2f})")
        axs[1, j].set_xlabel("r/R(区間の中心)")
        axs[1, j].set_ylabel("u_x/√(2RT0)(区間の平均)")
        axs[1, j].set_title(f"断面の u_x、X ≈ {Xs}")
    axs[0, 0].set_ylabel("p/p_in(中心線)")
    axs[0, 1].set_ylabel("u_x/√(2RT0)(中心線)")
    axs[0, 2].set_ylabel("DSMC に対する相対差 [%]")
    axs[0, 2].axhline(0, color="gray", lw=0.5)
    for ax in axs[0]:
        ax.set_xlabel("X = x/R − 8(管の入口 0、出口 10)")
        ax.set_xlim(-1, 10.5)
    axs[0, 2].set_ylim(-8, 8)
    for ax in axs.flat:
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.suptitle("管 δ_rp = 10: 本実装(GSIS-PBC)・ユーザーの DSMC・論文 Fig.3")
    fig.tight_layout()
    out = HERE / "results" / "compare_d10.png"
    fig.savefig(out, dpi=100, facecolor="white")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
