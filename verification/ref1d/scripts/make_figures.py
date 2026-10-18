"""結果の CSV から図(PNG)を作る。

実行(リポジトリのルートから。run_cases.py, convergence.py, verify.py の後):
    uv run --project verification/ref1d python verification/ref1d/scripts/make_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ref1d import analytic as A  # noqa: E402
from ref1d import plotstyle as S  # noqa: E402
from ref1d.cases import CASE_TYPES, kn_tag, make_problem, read_profile, read_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
FIG = RES / "figures"

CASE_LABEL = {
    "heat_lin": r"熱伝導 $T_L/T_0=0.95,\ T_R/T_0=1.05$",
    "heat_nl": r"熱伝導 $T_L/T_0=0.5,\ T_R/T_0=1.5$",
    "couette_lin": r"クエット流 $u_w/\sqrt{RT_0}=0.1$",
    "couette_nl": r"クエット流 $u_w/\sqrt{RT_0}=1$",
}
PROFILE_KN = [0.01, 0.1, 1.0, 10.0]


def load_profile(case: str, Kn: float):
    return read_profile(RES / "profiles" / f"{case}_{kn_tag(Kn)}.csv")


def save(fig, name: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name)
    plt.close(fig)
    print(f"  {FIG / name}")


def legend_kn(ax, **kw):
    ax.legend(title="Kn", title_fontsize=8.5, **kw)


def set_log_ticks(ax, values) -> None:
    """対数軸の目盛を格子の値だけにする(補助目盛のラベルが重ならないように)。"""
    from matplotlib.ticker import FixedLocator, NullFormatter, NullLocator

    ax.xaxis.set_major_locator(FixedLocator(list(values)))
    ax.set_xticklabels([f"{v:g}" for v in values])
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_minor_formatter(NullFormatter())


# ----------------------------------------------------------------------------
def fig_profiles() -> None:
    cols = S.ordinal_colors(len(PROFILE_KN))
    for case in CASE_TYPES:
        heat = case.startswith("heat")
        if heat:
            panels = [("T", r"温度 $T/T_0$"), ("rho", r"密度 $\rho/\rho_0$"), ("p", r"圧力 $p/p_0=\rho T$"), ("qx", r"熱流束 $q_x/(p_0\sqrt{RT_0})$")]
            fig, axs = plt.subplots(2, 2, figsize=(9.0, 6.2))
        else:
            panels = [("uy", r"速度 $u_y/\sqrt{RT_0}$"), ("T", r"温度 $T/T_0$"), ("rho", r"密度 $\rho/\rho_0$"),
                      ("p", r"圧力 $p/p_0=\rho T$"), ("qx", r"熱流束 $q_x/(p_0\sqrt{RT_0})$"), ("qy", r"熱流束 $q_y/(p_0\sqrt{RT_0})$")]
            fig, axs = plt.subplots(2, 3, figsize=(12.0, 6.2))
        for (key, label), ax in zip(panels, axs.ravel()):
            for Kn, c in zip(PROFILE_KN, cols):
                d = load_profile(case, Kn)
                ax.plot(d["x"], d[key], color=c, label=f"{Kn:g}")
            ax.set_xlabel(r"$x/L$")
            ax.set_title(label, loc="left")
        legend_kn(axs.ravel()[0])
        fig.suptitle(f"{CASE_LABEL[case]}(Shakhov, Pr=2/3, ω=0.66)。Kn = 0.01, 0.1, 1, 10", x=0.01, ha="left")
        fig.tight_layout()
        save(fig, f"profiles_{case}.png")


# ----------------------------------------------------------------------------
def fig_flux_vs_kn() -> None:
    rows = read_rows(RES / "summary.csv")
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 4.2))
    kn_ns = np.logspace(np.log10(0.005), np.log10(0.5), 30)
    for ax, pair, key, ylab in [
        (axs[0], ("heat_lin", "heat_nl"), "qx_over_fm", r"熱流束 $q_x/q_{x,\mathrm{fm}}$"),
        (axs[1], ("couette_lin", "couette_nl"), "Pxy_over_fm", r"せん断応力 $P_{xy}/P_{xy,\mathrm{fm}}$"),
    ]:
        for i, case in enumerate(pair):
            rr = [r for r in rows if r["case"] == case]
            kn = np.array([r["Kn"] for r in rr])
            v = np.array([r[key] for r in rr])
            o = np.argsort(kn)
            ax.plot(kn[o], v[o], color=S.series(i), ls="-", marker=S.MARKERS[i], label=f"{CASE_LABEL[case]}(本ソルバー)")
            ns = []
            for Kn in kn_ns:
                pr = make_problem(case, Kn)
                sol = A.nsf_slip(pr)
                fm = A.free_molecular(pr)
                ns.append(np.mean(sol.qx) / fm.qx if key == "qx_over_fm" else sol.Pxy / fm.Pxy)
            ax.plot(kn_ns, ns, color=S.series(i), ls="--", lw=1.2, label=f"{CASE_LABEL[case]}(NSF + すべり・跳躍)")
        ax.axhline(1.0, color=S.tokens()["text_muted"], lw=1.0, ls=":")
        ax.text(12, 1.0, "自由分子極限", va="bottom", ha="right", color=S.tokens()["text_muted"], fontsize=8)
        ax.set_xscale("log")
        ax.set_xlabel("Kn")
        ax.set_title(ylab, loc="left")
        ax.set_ylim(0, 1.08)
        ax.legend(loc="upper left", fontsize=7.5)
    fig.tight_layout()
    save(fig, "flux_vs_kn.png")


def fig_wall_vs_kn() -> None:
    rows = read_rows(RES / "summary.csv")
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 4.2))
    ax = axs[0]
    k = 0
    for case in ("heat_lin", "heat_nl"):
        rr = sorted([r for r in rows if r["case"] == case], key=lambda r: r["Kn"])
        kn = np.array([r["Kn"] for r in rr])
        dT = rr[0]["T_R"] - rr[0]["T_L"]
        for wall, lab in (("L", "低温壁 x=0"), ("R", "高温壁 x=L")):
            v = np.array([abs(r[f"T_jump_{wall}"]) for r in rr]) / dT
            ax.plot(kn, v, color=S.series(k), ls=S.LINESTYLES[k], marker=S.MARKERS[k], label=f"{CASE_LABEL[case]}, {lab}")
            k += 1
    ax.set_xscale("log")
    ax.set_xlabel("Kn")
    ax.set_title(r"温度跳躍 $|T_\mathrm{gas}-T_w|/(T_R-T_L)$(壁の節点)", loc="left")
    ax.legend(fontsize=7.5)
    ax = axs[1]
    for i, case in enumerate(("couette_lin", "couette_nl")):
        rr = sorted([r for r in rows if r["case"] == case], key=lambda r: r["Kn"])
        kn = np.array([r["Kn"] for r in rr])
        du = rr[0]["u_R"] - rr[0]["u_L"]
        v = np.array([abs(r["slip_R"]) for r in rr]) / du
        ax.plot(kn, v, color=S.series(i), ls=S.LINESTYLES[i], marker=S.MARKERS[i], label=CASE_LABEL[case])
    ax.set_xscale("log")
    ax.set_xlabel("Kn")
    ax.set_title(r"速度すべり $|u_{y,\mathrm{gas}}-u_w|/(u_R-u_L)$(壁の節点)", loc="left")
    ax.legend()
    fig.tight_layout()
    save(fig, "wall_jump_slip_vs_kn.png")


# ----------------------------------------------------------------------------
def _main_key(case: str) -> str:
    return "qx_mean" if case.startswith("heat") else "Pxy_mean"


def fig_convergence() -> None:
    p = RES / "convergence" / "spatial.csv"
    if not p.exists():
        return
    rows = read_rows(p)
    kns = sorted({r["Kn"] for r in rows})
    cols = S.ordinal_colors(len(kns))
    fig, axs = plt.subplots(2, 2, figsize=(10, 7.2))
    for ax, case in zip(axs.ravel(), CASE_TYPES):
        key = _main_key(case)
        for Kn, c in zip(kns, cols):
            rr = sorted([r for r in rows if r["case"] == case and r["Kn"] == Kn], key=lambda r: r["N"])
            N = np.array([r["N"] for r in rr])
            v = np.array([r[key] for r in rr])
            err = np.abs(v[:-1] / v[-1] - 1.0)
            ax.loglog(N[:-1], np.maximum(err, 1e-16), color=c, marker="o", label=f"{Kn:g}")
            if not case.startswith("heat"):
                vw = np.array([r["qx_wallL"] for r in rr])
                ew = np.abs(vw[:-1] / vw[-1] - 1.0)
                ax.loglog(N[:-1], np.maximum(ew, 1e-16), color=c, marker="s", ls="--", lw=1.0)
        Nr = np.array([50, 800])
        for order, ls in ((2, ":"), (4, "-.")):
            ax.loglog(Nr, 3e-3 * (Nr / 50.0) ** (-order), color=S.tokens()["text_muted"], ls=ls, lw=1.0)
            ax.text(Nr[-1], 3e-3 * (Nr[-1] / 50.0) ** (-order), rf" $N^{{-{order}}}$", color=S.tokens()["text_muted"], fontsize=8, va="center")
        ax.axhline(1e-4, color=S.tokens()["accent"], lw=1.0)
        ax.set_xlabel("セル数 N")
        set_log_ticks(ax, [50, 100, 200, 400, 800])
        title = r"$q_x$(平均)" if case.startswith("heat") else r"$P_{xy}$(実線)、壁の $q_x$(破線)"
        ax.set_title(f"{CASE_LABEL[case]}: {title}", loc="left", fontsize=9)
        ax.set_ylim(1e-13, 1e-1)
    legend_kn(axs[0, 0], loc="lower left")
    fig.suptitle("空間格子の収束(N=1600 との相対差、速度格子 v3、3 次補間)。橙の横線は相対 1e-4", x=0.01, ha="left")
    fig.tight_layout()
    save(fig, "convergence_spatial.png")

    p = RES / "convergence" / "velocity.csv"
    if p.exists():
        rows = read_rows(p)
        fig, axs = plt.subplots(2, 2, figsize=(10, 7.2))
        for ax, case in zip(axs.ravel(), CASE_TYPES):
            key = _main_key(case)
            for Kn, c in zip(kns, cols):
                rr = [r for r in rows if r["case"] == case and r["Kn"] == Kn]
                rr = sorted(rr, key=lambda r: r["Nv"])
                Nv = np.array([r["Nv"] for r in rr])
                v = np.array([r[key] for r in rr])
                err = np.abs(v[:-1] / v[-1] - 1.0)
                ax.loglog(Nv[:-1], np.maximum(err, 1e-16), color=c, marker="o", label=f"{Kn:g}")
            ax.axhline(1e-4, color=S.tokens()["accent"], lw=1.0)
            ax.set_xlabel(r"速度点の数 $N_v$($\xi_x$ 方向)")
            set_log_ticks(ax, [96, 156, 288, 400])
            ax.set_title(CASE_LABEL[case], loc="left", fontsize=9)
            ax.set_ylim(1e-16, 1e-2)
        legend_kn(axs[0, 0], loc="upper right")
        fig.suptitle(r"速度格子の収束(v4 との相対差、N=800)。熱伝導は $q_x$、クエット流は $P_{xy}$。橙の横線は相対 1e-4", x=0.01, ha="left")
        fig.tight_layout()
        save(fig, "convergence_velocity.png")

    p = RES / "convergence" / "order.csv"
    if p.exists():
        rows = read_rows(p)
        cases = ("heat_nl", "couette_nl")
        kns_o = sorted({r["Kn"] for r in rows})
        fig, axs = plt.subplots(len(cases), len(kns_o), figsize=(11, 6.6))
        for i, case in enumerate(cases):
            key = _main_key(case)
            for j, Kn in enumerate(kns_o):
                ax = axs[i, j]
                for k, pp in enumerate((1, 2, 3)):
                    rr = sorted([r for r in rows if r["case"] == case and r["Kn"] == Kn and r["p"] == pp], key=lambda r: r["N"])
                    N = np.array([r["N"] for r in rr])
                    v = np.array([r[key] for r in rr])
                    err = np.abs(v[:-1] / v[-1] - 1.0)
                    ax.loglog(N[:-1], np.maximum(err, 1e-16), color=S.series(k), ls=S.LINESTYLES[k], marker=S.MARKERS[k], label=f"p={pp}")
                ax.set_title(f"{CASE_LABEL[case]}, Kn={Kn:g}", loc="left", fontsize=8.5)
                ax.set_xlabel("セル数 N")
                set_log_ticks(ax, [50, 100, 200, 400, 800])
                ax.set_ylim(1e-12, 1e-1)
        axs[0, 0].legend(title="補間次数")
        fig.suptitle("目標分布の補間次数 p と空間収束(各 p の N=1600 との相対差、速度格子 v2)", x=0.01, ha="left")
        fig.tight_layout()
        save(fig, "convergence_order.png")


# ----------------------------------------------------------------------------
def fig_verification() -> None:
    p = RES / "verification" / "free_molecular.csv"
    if p.exists():
        rows = read_rows(p)
        fig, ax = plt.subplots(figsize=(6.4, 4.4))
        for i, case in enumerate(CASE_TYPES):
            rr = sorted([r for r in rows if r["case"] == case and np.isfinite(r["Kn"])], key=lambda r: r["Kn"])
            kn = np.array([r["Kn"] for r in rr])
            key = "qx_rel_dev" if case.startswith("heat") else "Pxy_rel_dev"
            v = np.abs(np.array([r[key] for r in rr]))
            ax.loglog(kn, v, color=S.series(i), ls=S.LINESTYLES[i], marker=S.MARKERS[i], label=CASE_LABEL[case])
        kr = np.array([10, 1e6])
        ax.loglog(kr, 0.5 / kr, color=S.tokens()["text_muted"], ls=":", lw=1.0)
        ax.text(1e6, 0.5e-6, r" $\mathrm{Kn}^{-1}$", color=S.tokens()["text_muted"], fontsize=8, va="center")
        ax.set_xlabel("Kn")
        ax.set_title(r"自由分子解析解との相対差(熱伝導は $q_x$、クエット流は $P_{xy}$)", loc="left", fontsize=9.5)
        ax.legend(fontsize=7.5)
        fig.tight_layout()
        save(fig, "verification_free_molecular.png")

    p = RES / "verification" / "nsf.csv"
    if p.exists():
        rows = read_rows(p)
        fig, ax = plt.subplots(figsize=(6.4, 4.4))
        for i, case in enumerate(CASE_TYPES):
            rr = sorted([r for r in rows if r["case"] == case], key=lambda r: r["Kn"])
            kn = np.array([r["Kn"] for r in rr])
            key = "qx_rel_diff" if case.startswith("heat") else "Pxy_rel_diff"
            v = np.abs(np.array([r[key] for r in rr]))
            ax.loglog(kn, v, color=S.series(i), ls=S.LINESTYLES[i], marker=S.MARKERS[i], label=CASE_LABEL[case])
        kr = np.array([0.0025, 0.1])
        ax.loglog(kr, 2.0 * kr**2, color=S.tokens()["text_muted"], ls=":", lw=1.0)
        ax.text(0.1, 2.0 * 0.1**2, r" $\mathrm{Kn}^{2}$", color=S.tokens()["text_muted"], fontsize=8, va="center")
        ax.set_xlabel("Kn")
        ax.set_title("NSF + 1 次のすべり・温度跳躍の解との相対差", loc="left", fontsize=9.5)
        ax.legend(fontsize=7.5)
        fig.tight_layout()
        save(fig, "verification_nsf.png")

    p = RES / "verification" / "literature.csv"
    if p.exists():
        rows = read_rows(p)
        fig, axs = plt.subplots(1, 3, figsize=(14.0, 4.3))
        # (a) クエット流のせん断応力、(b) 熱流束: 文献値(白抜き)と本ソルバー(線)
        for ax, qname, title in [
            (axs[0], "P_xy/P_xy,fm", r"クエット流 $P_{xy}/P_{xy,\mathrm{fm}}$(線形化)"),
            (axs[1], "q_x/q_x,fm", r"熱伝導 $q_x/q_{x,\mathrm{fm}}$(線形化)"),
        ]:
            for i, model in enumerate(("BGK", "S")):
                rr = sorted([r for r in rows if r["quantity"] == qname and r["model"] == model], key=lambda r: r["delta"])
                if not rr:
                    continue
                d = np.array([r["delta"] for r in rr])
                src = rr[0]["source"]
                # 点の少ない系列を線でつなぐと形を誤って示すので、本ソルバーの値は小さな塗りつぶしの点で描く
                ax.plot(d, [r["ref1d"] for r in rr], color=S.series(i), ls="-" if len(rr) > 8 else "none", lw=0.8,
                        marker=".", ms=6, label=f"本ソルバー({model})")
                ax.plot(d, [r["reference"] for r in rr], ls="none", marker=S.MARKERS[i], mfc="none", ms=8,
                        color=S.series(i), label=f"{src}({model})")
            ax.set_xscale("log")
            ax.set_xlabel(r"$\delta=\sqrt{\pi}/(2\,\mathrm{Kn})$")
            ax.set_title(title, loc="left", fontsize=9.5)
            ax.legend(fontsize=7.5, loc="lower left")
        # (c) すべての比較項目: 文献の最後の桁の何単位の差か(丸めなら 0.5、切り捨てなら 1 が境界)
        ax = axs[2]
        cats = []
        for r in rows:
            c = (r["source"], r["ref_rounding"])
            if c not in cats:
                cats.append(c)
        for i, c in enumerate(cats):
            rr = [r for r in rows if (r["source"], r["ref_rounding"]) == c]
            d = np.array([max(r["delta"], 1e-4) for r in rr])
            # 文献値が負の量もあるので、絶対値の差 (|本ソルバー| - |文献値|) / 1 単位 で描く
            v = np.array([np.sign(r["reference"]) * r["diff_in_last_digit"] for r in rr])
            ax.plot(d, v, ls="none", marker=S.MARKERS[i % len(S.MARKERS)], mfc="none", ms=6,
                    color=S.series(i), label=f"{c[0]}({c[1]})")
        muted = S.tokens()["text_muted"]
        for yv, ls in ((0.5, ":"), (-0.5, ":"), (1.0, "--"), (0.0, "-")):  # 四捨五入は ±0.5、切り捨ては [0, 1)
            ax.axhline(yv, color=muted, lw=0.9, ls=ls)
        ax.text(1.5e2, 0.5, "四捨五入の幅", color=muted, fontsize=7.5, ha="right", va="bottom")
        ax.text(1.5e2, 1.0, "切り捨ての幅", color=muted, fontsize=7.5, ha="right", va="bottom")
        ax.set_xscale("log")
        ax.set_ylim(-1.2, 1.3)
        ax.set_xlabel(r"$\delta$(自由分子の行は $10^{-4}$ に置いた)")
        ax.set_title("(|本ソルバー| − |文献値|)/ 文献の最後の桁の 1 単位", loc="left", fontsize=9.5)
        ax.legend(fontsize=7, loc="lower left", ncol=2)
        fig.tight_layout()
        save(fig, "verification_literature.png")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    S.apply("light")
    which = set(sys.argv[1:]) or {"profiles", "flux", "wall", "conv", "verif"}
    if "profiles" in which:
        fig_profiles()
    if "flux" in which:
        fig_flux_vs_kn()
    if "wall" in which:
        fig_wall_vs_kn()
    if "conv" in which:
        fig_convergence()
    if "verif" in which:
        fig_verification()


if __name__ == "__main__":
    main()
