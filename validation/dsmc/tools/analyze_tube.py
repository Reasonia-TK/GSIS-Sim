"""管→真空ケースの DSMC 結果から、無次元質量流量と中心線分布を取り出す。

入力: run_dsmc.ps1 の実行フォルダ(params.json、ブロック平均の plotfile plt?????、solver_*.log)。
plotfile は output.sample_reset = 1 で書いた「直前 plot_int ステップの平均」とみなす。

質量流量:
    セル (i,j,k) の x 方向の質量流束の体積積分は ρ u φ ΔV = Fnum Σ(m v_x)/N_s(φ = 気体体積率)で、
    体積率の近似誤差を含まない。x 方向の断面(スラブ i)ごとに
        ṁ(x_i) = sym × Σ_jk ρ u φ ΔV / Δx      (sym: 1/4 領域なら 4)
    とし、Ṁ = ṁ / (sqrt(π) R² p_in / sqrt(2 R_g T0)) で無次元化する(論文と同じ)。
    定常なら ṁ(x) は x によらない。管のスラブ(0 ≤ x ≤ 10R)の平均を主な値とし、
    上流槽・下流槽のスラブの平均も確認用に出す。誤差はブロック間のばらつきから求める
    (隣り合うブロックを 2 個ずつまとめた値とも比べ、大きい方を採る)。

中心線(管軸 = 領域の y = z = 0 の角):
    各スラブで軸からの距離 r < r_fit の完全気体セル(φ = 1)に q(r) = a + b r² を最小二乗で当てはめ、
    a を中心線の値とする(p/p_in と u_x/sqrt(2 R_g T0))。軸に接するセルの値も併記する。

定常判定:
    --steady-from(ステップ)以降のブロックを平均する。既定は全ステップの 40%。
    窓の前半と後半の Ṁ の差を標準誤差で割った値(|z| < 2 が目安)を出力する。

論文の値との比較(--reference-dir、既定 reference/paper_2610.07658/。無ければ飛ばす):
    table1_tube.csv(M_ref, gsis_pbc_M, cis_M)と、ケースの δ に対応する Fig.3 のデジタイズ値
    fig3_{pressure,velocity}_{gsis,cis,dsmc}_delta<δ>.csv(列 X,P / X,Ux。X = x/R)を読む。
    論文の DSMC の中心線は δ=100 だけで、δ=1, 10 は GSIS-PBC の線と CIS の記号と比べる。
    DSMC の中心線を参照の X に線形補間して差(DSMC − 参照)を求める。外挿はしない
    (管内の DSMC のスラブ中心の範囲に入る参照点だけを使う)。

出力(既定: <run>/analysis/): summary.json, mdot_blocks.csv, mdot_profile.csv, centerline.csv,
    fig3_comparison.csv, mdot_history.png, mdot_profile.png, centerline.png, centerline_tube.png

使い方(リポジトリのルートから):
    uv run --project python python validation/dsmc/tools/analyze_tube.py validation/dsmc/runs/smoke
    uv run --project python python validation/dsmc/tools/analyze_tube.py validation/dsmc/runs/d10_q --steady-from 7000
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plotfile import PlotFile, list_plotfiles  # noqa: E402

KB = 1.380649e-23
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REF_DIR = REPO_ROOT / "reference" / "paper_2610.07658"

# dataviz の既定パレット(固定順)と文字色
C_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
C_TEXT = "#0b0b0b"
C_TEXT2 = "#52514e"
C_GRID = "#e4e3df"
C_REF = "#52514e"


# ---------------------------------------------------------------- 入力
def read_log(run_dir: Path) -> dict:
    """solver_*.log から粒子数の履歴と処理時間を読む。"""
    diag, timing, finished, setup = [], {}, {}, {}
    for log in sorted(run_dir.glob("solver_*.log")):
        for line in log.read_text(errors="replace").splitlines():
            m = re.match(r"^\s+(\d+)\s+([\d.eE+-]+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s*$", line)
            if m:
                diag.append([int(m.group(1)), float(m.group(2)), int(m.group(3)), int(m.group(4)),
                             float(m.group(5)), float(m.group(6))])
            if line.startswith("Timing [s]:"):
                for k, v in re.findall(r"([a-z/]+) ([\d.]+)", line):
                    timing[k] = timing.get(k, 0.0) + float(v)
            m = re.match(r"^Finished: step = (\d+).*particles = (\d+).*wall time = ([\d.]+) s", line)
            if m:
                finished = {"step": int(m.group(1)), "particles": int(m.group(2)),
                            "wall_time_s": float(m.group(3))}
            m = re.match(r"^Resolved gas volume: ([\d.eE+-]+)", line)
            if m:
                setup["resolved_gas_volume_m3"] = float(m.group(1))
            m = re.match(r"^\s*Fnum\s*:\s*([\d.eE+-]+)", line)
            if m:
                setup["fnum"] = float(m.group(1))
    return {"diag": np.array(diag) if diag else np.zeros((0, 6)), "timing": timing,
            "finished": finished, "setup": setup}


def block_quantities(pf: PlotFile, prm: dict, r_fit: float) -> dict:
    """1 ブロック(1 plotfile)から、スラブごとの ṁ と中心線の値を求める。"""
    d = pf.get_many(["rho", "u", "p", "n", "T", "gas_volume_fraction"])
    phi = d["gas_volume_fraction"]
    dx, dy, dz = pf.dx
    x, y, z = pf.cell_centers()
    R = prm["L_ref_m"]
    sym = 4 if (pf.prob_lo[1] >= -1e-12 and pf.prob_lo[2] >= -1e-12) else \
        (2 if (pf.prob_lo[1] >= -1e-12 or pf.prob_lo[2] >= -1e-12) else 1)
    mass_flux = np.nan_to_num(d["rho"] * d["u"] * phi) * (dx * dy * dz)      # [kg m/s]
    mdot_x = sym * mass_flux.sum(axis=(1, 2)) / dx / prm["mdot_ref_kg_s"]     # Ṁ(x)

    # 中心線: 軸からの距離(1/4 領域では角、全領域では中央)
    Y, Z = np.meshgrid(y, z, indexing="ij")
    r = np.hypot(Y, Z) / R
    sel = (r < r_fit)
    full = phi >= 1.0 - 1e-12
    p_in, c_m, n_in, t0 = prm["p_in_Pa"], prm["c_m_m_s"], prm["n_in_m3"], prm["T0_K"]
    rs = r[sel]
    A = np.stack([np.ones_like(rs), rs**2], axis=1)
    jc = int(np.argmin(np.abs(y)))
    kc = int(np.argmin(np.abs(z)))
    cl = {"p": [], "u": [], "n": [], "T": [], "p_axis_cell": [], "u_axis_cell": [], "ncells": []}
    for i in range(pf.shape[0]):
        ok = full[i][sel]
        if ok.sum() >= 3 and len(np.unique(np.round(rs[ok], 9))) >= 2:
            for key, arr, scale in (("p", d["p"], p_in), ("u", d["u"], c_m), ("n", d["n"], n_in),
                                    ("T", d["T"], t0)):
                coef, *_ = np.linalg.lstsq(A[ok], arr[i][sel][ok] / scale, rcond=None)
                cl[key].append(coef[0])
            cl["ncells"].append(int(ok.sum()))
        else:
            for key in ("p", "u", "n", "T"):
                cl[key].append(np.nan)
            cl["ncells"].append(int(ok.sum()))
        cl["p_axis_cell"].append(d["p"][i, jc, kc] / p_in if full[i, jc, kc] else np.nan)
        cl["u_axis_cell"].append(d["u"][i, jc, kc] / c_m if full[i, jc, kc] else np.nan)
    # 上流槽の圧力
    #   p_face: 入口面に接する 2 層のセル(x < -8R + 2Δx、r < 7R)。流入境界の条件による p_in からのずれ
    #   p_res : 入口面と管入口から離れた領域 x ∈ [-7R, -3R]、r < 6R(管入口へ向かう流れの圧力低下も含む)
    X = (x / R)[:, None, None]
    face_mask = (X < -8.0 + 2.0 * dx / R) & (r[None, :, :] < 7.0) & full
    res_mask = (X > -7.0) & (X < -3.0) & (r[None, :, :] < 6.0) & full
    p_face = float(np.mean(d["p"][face_mask]) / p_in) if face_mask.any() else math.nan
    p_res = float(np.mean(d["p"][res_mask]) / p_in) if res_mask.any() else math.nan
    return {"x_over_R": x / R, "mdot_x": mdot_x, "sym": sym, "p_res": p_res, "p_face": p_face,
            "centerline": {k: np.array(v) for k, v in cl.items()},
            "dx_over_R": dx / R, "dy_over_R": dy / R}


def region_mean(x, values, lo, hi):
    m = (x > lo) & (x < hi)
    return float(np.mean(values[m])) if m.any() else math.nan


def mean_se(series: np.ndarray) -> tuple[float, float, float]:
    """平均・標準誤差(ブロック独立)・2 ブロックずつまとめた標準誤差。"""
    s = np.asarray(series, dtype=float)
    n = len(s)
    if n == 0:
        return math.nan, math.nan, math.nan
    mean = float(s.mean())
    se1 = float(s.std(ddof=1) / math.sqrt(n)) if n > 1 else math.nan
    if n >= 4:
        pairs = s[: n // 2 * 2].reshape(-1, 2).mean(axis=1)
        se2 = float(pairs.std(ddof=1) / math.sqrt(len(pairs)))
    else:
        se2 = math.nan
    return mean, se1, se2


METHOD_LABEL = {"gsis": "論文 GSIS-PBC(Fig.3 の線)", "cis": "論文 CIS(Fig.3 の記号)",
                "dsmc": "論文 DSMC(Fig.3 の記号)"}


def read_named_csv(path: Path, xcol: str, ycol: str):
    xs, vs = [], []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                xs.append(float(row[xcol]))
                vs.append(float(row[ycol]))
            except (KeyError, TypeError, ValueError):
                continue
    order = np.argsort(xs)
    return np.array(xs)[order], np.array(vs)[order]


def load_reference(ref_dir, delta: float) -> dict:
    """論文の Table 1 と Fig.3 のデジタイズ値を読む。無いファイルは飛ばす。"""
    out = {"dir": str(ref_dir) if ref_dir else None, "fig3": {}, "table1": None}
    if ref_dir is None or not Path(ref_dir).is_dir():
        return out
    ref_dir = Path(ref_dir)
    d = int(round(delta))
    for qty, name, col in (("p", "pressure", "P"), ("u", "velocity", "Ux")):
        for method in ("gsis", "cis", "dsmc"):
            path = ref_dir / f"fig3_{name}_{method}_delta{d}.csv"
            if path.exists():
                X, V = read_named_csv(path, "X", col)
                if len(X):
                    out["fig3"][(qty, method)] = (X, V, path.name)
    t1 = ref_dir / "table1_tube.csv"
    if t1.exists():
        with open(t1, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    if int(float(row["delta_rp"])) == d:
                        out["table1"] = {"reference": float(row["M_ref"]), "ref_method": row.get("ref_method", ""),
                                         "gsis_pbc": float(row["gsis_pbc_M"]), "cis": float(row["cis_M"]),
                                         "source": t1.name}
                except (KeyError, ValueError):
                    continue
    return out


def compare_centerline(cl: dict, refs: dict):
    """DSMC の中心線を参照の X に線形補間し、差(DSMC − 参照)を求める。外挿しない。"""
    x = cl["x"]
    stats, rows = {}, []
    for (qty, method), (X, V, name) in refs.items():
        ok = (x >= 0.0) & (x <= 10.0) & np.isfinite(cl[qty])
        xs, ys, es = x[ok], cl[qty][ok], cl[qty + "_se"][ok]
        if len(xs) < 2:
            continue
        sel = (X >= xs[0]) & (X <= xs[-1])
        if not sel.any():
            continue
        dv = np.interp(X[sel], xs, ys)
        de = np.interp(X[sel], xs, es)
        diff = dv - V[sel]
        k = int(np.argmax(np.abs(diff)))
        stats[f"{qty}_{method}"] = {
            "file": name, "n_points": int(sel.sum()),
            "x_range": [float(X[sel][0]), float(X[sel][-1])],
            "mean_diff": float(diff.mean()), "rms_diff": float(np.sqrt(np.mean(diff**2))),
            "max_abs_diff": float(abs(diff[k])), "x_at_max_abs_diff": float(X[sel][k]),
            "rms_dsmc_se": float(np.sqrt(np.mean(de**2))),
        }
        for xi, ri, di, ei, ddi in zip(X[sel], V[sel], dv, de, diff):
            rows.append([qty, method, name, f"{xi:.4f}", f"{ri:.5f}", f"{di:.5f}", f"{ei:.5f}", f"{ddi:+.5f}"])
    return stats, rows


# ---------------------------------------------------------------- 図
def _style(ax, xlabel, ylabel):
    ax.set_xlabel(xlabel, color=C_TEXT2)
    ax.set_ylabel(ylabel, color=C_TEXT2)
    ax.tick_params(colors=C_TEXT2, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(C_GRID)
    ax.grid(True, color=C_GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def make_plots(out: Path, res: dict, title: str, refs: dict | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": ["Yu Gothic", "Meiryo", "MS Gothic", "DejaVu Sans"],
                         "axes.titlesize": 11, "axes.titlecolor": C_TEXT, "figure.facecolor": "white"})

    # Ṁ の履歴
    b = res["blocks"]
    fig, ax = plt.subplots(figsize=(7.0, 3.6), dpi=130)
    ax.plot(b["t"], b["tube"], color=C_SERIES[0], lw=1.6, marker="o", ms=4, label="管のスラブ平均")
    ax.plot(b["t"], b["down"], color=C_SERIES[1], lw=1.2, marker="s", ms=3.5, label="下流槽のスラブ平均")
    ax.plot(b["t"], b["up"], color=C_SERIES[2], lw=1.2, marker="^", ms=3.5, label="上流槽のスラブ平均")
    ref = res["paper"].get("reference") if res["paper"] else None
    if ref:
        ax.axhline(ref, color=C_REF, lw=1.0, ls="--")
        ax.annotate(f"論文 DSMC {ref:.3f}", (b["t"][0], ref), xytext=(4, 4), textcoords="offset points",
                    color=C_TEXT2, fontsize=8)
    ax.axvline(res["steady"]["t_from"], color=C_TEXT2, lw=0.8, ls=":")
    ax.annotate("平均の開始", (res["steady"]["t_from"], ax.get_ylim()[0]), xytext=(3, 3),
                textcoords="offset points", color=C_TEXT2, fontsize=8)
    # 縦軸は定常値の周りに限る(初期の過渡で下流槽の値が大きく振れるため)。範囲外の点は上端に印を付ける
    top = 2.0 * max(res["mdot"]["tube_mean"], ref or 0.0)
    bottom = max(-0.5 * top, min(0.0, float(np.nanmin([b["tube"], b["down"], b["up"]]))))
    ax.set_ylim(bottom, top)
    for series, color in ((b["tube"], C_SERIES[0]), (b["down"], C_SERIES[1]), (b["up"], C_SERIES[2])):
        for mask, edge, mk in ((series > top, top, "^"), (series < bottom, bottom, "v")):
            if mask.any():
                ax.plot(b["t"][mask], np.full(mask.sum(), edge), ls="none", marker=mk, ms=7, color=color,
                        clip_on=False)
    _style(ax, r"$t\,/\,(R/\sqrt{2RT_0})$", r"$\dot M$(ブロック平均)")
    ax.set_title(f"{title}: 無次元質量流量の履歴(▲▼は範囲外)")
    ax.legend(frameon=False, fontsize=8, labelcolor=C_TEXT2)
    fig.tight_layout()
    fig.savefig(out / "mdot_history.png")
    plt.close(fig)

    # Ṁ(x)
    pr = res["profile"]
    fig, ax = plt.subplots(figsize=(7.0, 3.4), dpi=130)
    ax.fill_between(pr["x"], pr["mean"] - 2 * pr["se"], pr["mean"] + 2 * pr["se"], color=C_SERIES[0],
                    alpha=0.18, linewidth=0, label="±2 標準誤差")
    ax.plot(pr["x"], pr["mean"], color=C_SERIES[0], lw=1.6, label=r"スラブごとの $\dot M(x)$")
    ax.axhline(res["mdot"]["tube_mean"], color=C_REF, lw=1.0, ls="--")
    ax.annotate(f"管の平均 {res['mdot']['tube_mean']:.4f}", (pr["x"][0], res["mdot"]["tube_mean"]),
                xytext=(4, 4), textcoords="offset points", color=C_TEXT2, fontsize=8)
    for xv in (0.0, 10.0):
        ax.axvline(xv, color=C_GRID, lw=1.0)
    _style(ax, r"$x/R$", r"$\dot M(x)$")
    ax.set_title(f"{title}: 断面ごとの質量流量(定常なら一定)")
    ax.legend(frameon=False, fontsize=8, labelcolor=C_TEXT2, loc="lower left")
    fig.tight_layout()
    fig.savefig(out / "mdot_profile.png")
    plt.close(fig)

    # 中心線(2 つの量は別の図に分ける)。全域と管内(論文 Fig.3 の範囲)の 2 枚
    cl = res["centerline"]
    refs = refs or {}
    styles = {"gsis": dict(color=C_SERIES[1], lw=1.2),
              "cis": dict(ls="none", marker="s", ms=6, mfc="white", mec=C_SERIES[2], mew=1.4),
              "dsmc": dict(ls="none", marker="o", ms=6, mfc="white", mec=C_SERIES[3], mew=1.4)}
    for fname, xlim, sub_title in (("centerline.png", None, "中心線の分布"),
                                   ("centerline_tube.png", (-0.3, 10.3), "管内の中心線(論文 Fig.3 の範囲)")):
        fig, axes = plt.subplots(2, 1, figsize=(7.0, 6.2), dpi=130, sharex=True)
        for ax, key, ylabel in ((axes[0], "p", r"$p/p_{in}$"), (axes[1], "u", r"$u_x/\sqrt{2RT_0}$")):
            if xlim is None:
                m = np.isfinite(cl["x"])
            else:
                m = (cl["x"] >= xlim[0]) & (cl["x"] <= xlim[1])
            xs, ys, es = cl["x"][m], cl[key][m], cl[key + "_se"][m]
            ax.fill_between(xs, ys - 2 * es, ys + 2 * es, color=C_SERIES[0], alpha=0.18, linewidth=0)
            ax.plot(xs, ys, color=C_SERIES[0], lw=1.6, label="この DSMC(軸近傍の当てはめ、帯は ±2 標準誤差)")
            for method in ("gsis", "cis", "dsmc"):
                if (key, method) in refs:
                    X, V, _ = refs[(key, method)]
                    ax.plot(X, V, label=METHOD_LABEL[method], **styles[method])
            for xv in (0.0, 10.0):
                ax.axvline(xv, color=C_GRID, lw=1.0)
            if xlim is not None:
                ax.set_xlim(*xlim)
            _style(ax, r"$x/R$" if key == "u" else "", ylabel)
            # p は下流ほど低く、u は下流ほど高いので、線の無い側に凡例を置く
            ax.legend(frameon=False, fontsize=8, labelcolor=C_TEXT2,
                      loc="upper right" if key == "p" else "upper left")
        axes[0].set_title(f"{title}: {sub_title}")
        fig.tight_layout()
        fig.savefig(out / fname)
        plt.close(fig)


# ---------------------------------------------------------------- 本体
def analyze(run_dir: Path, steady_from: int | None, r_fit: float | None, out: Path,
            ref_dir=DEFAULT_REF_DIR, plots: bool = True) -> dict:
    prm = json.loads((run_dir / "params.json").read_text(encoding="utf-8"))
    pfs = [p for p in list_plotfiles(run_dir) if int(p.name[3:]) > 0]
    if not pfs:
        raise SystemExit(f"plotfile がありません: {run_dir}")
    first = PlotFile(pfs[0])
    dy_r = first.dx[1] / prm["L_ref_m"]
    if r_fit is None:
        r_fit = max(0.35, 2.2 * dy_r)
    t_ref = prm["t_ref_s"]
    max_step = int(pfs[-1].name[3:])
    if steady_from is None:
        steady_from = int(round(0.4 * max_step))

    rows = []
    steady_blocks = []
    for path in pfs:
        pf = PlotFile(path)
        q = block_quantities(pf, prm, r_fit)
        x = q["x_over_R"]
        rec = {"step": pf.step, "t": pf.time / t_ref,
               "up": region_mean(x, q["mdot_x"], -8.0, 0.0),
               "tube": region_mean(x, q["mdot_x"], 0.0, 10.0),
               "down": region_mean(x, q["mdot_x"], 10.0, 14.0)}
        # 管の入口側・出口側の半分(定常なら一致する)
        rec["tube_first_half"] = region_mean(x, q["mdot_x"], 0.0, 5.0)
        rec["tube_second_half"] = region_mean(x, q["mdot_x"], 5.0, 10.0)
        rec["p_res"] = q["p_res"]
        rec["p_face"] = q["p_face"]
        rows.append(rec)
        # ブロック長: 直前の plotfile からのステップ数
        if pf.step - (rows[-2]["step"] if len(rows) > 1 else 0) > 0 and pf.step > steady_from:
            steady_blocks.append(q)
        del pf
    blocks = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    in_window = blocks["step"] > steady_from
    nwin = int(in_window.sum())
    if nwin < 2:
        raise SystemExit(f"--steady-from {steady_from} 以降のブロックが {nwin} 個しかありません")

    tube_mean, tube_se1, tube_se2 = mean_se(blocks["tube"][in_window])
    down_mean, down_se1, down_se2 = mean_se(blocks["down"][in_window])
    up_mean, up_se1, up_se2 = mean_se(blocks["up"][in_window])
    tube_se = np.nanmax([tube_se1, tube_se2])
    w = blocks["tube"][in_window]
    h = len(w) // 2
    z_half = ((w[h:].mean() - w[:h].mean()) / math.sqrt(w[:h].var(ddof=1) / h + w[h:].var(ddof=1) / (len(w) - h))
              if h >= 2 and len(w) - h >= 2 else math.nan)

    # スラブごとの Ṁ(x) と中心線(定常窓のブロックで平均)
    x = steady_blocks[0]["x_over_R"]
    mx = np.array([q["mdot_x"] for q in steady_blocks])
    prof = {"x": x, "mean": mx.mean(axis=0), "se": mx.std(axis=0, ddof=1) / math.sqrt(len(mx))}
    cl = {"x": x}
    for key in ("p", "u", "n", "T", "p_axis_cell", "u_axis_cell"):
        arr = np.array([q["centerline"][key] for q in steady_blocks])
        cl[key] = np.nanmean(arr, axis=0)
        if key in ("p", "u", "n", "T"):
            cl[key + "_se"] = np.nanstd(arr, axis=0, ddof=1) / math.sqrt(len(arr))
    cl["ncells"] = steady_blocks[0]["centerline"]["ncells"]

    log = read_log(run_dir)
    diag = log["diag"]
    reference = load_reference(ref_dir, prm["delta_rp"])
    # Table 1 は reference/ の CSV を優先し、無ければ params.json の値(make_inputs.py に転記した値)を使う
    paper = reference["table1"] or prm.get("paper_table1") or {}
    fig3_stats, fig3_rows = compare_centerline(cl, reference["fig3"])
    res = {
        "run_dir": str(run_dir),
        "case": prm["case"],
        "delta_rp": prm["delta_rp"],
        "symmetry_factor": steady_blocks[0]["sym"],
        "grid": {"dx_over_R": steady_blocks[0]["dx_over_R"], "dy_over_R": steady_blocks[0]["dy_over_R"],
                 "shape": list(first.shape)},
        "blocks": blocks,
        "steady": {"from_step": steady_from, "t_from": steady_from * prm["dsmc"]["dt_over_tref"],
                   "n_blocks": nwin, "z_second_minus_first_half": z_half},
        "mdot": {"tube_mean": tube_mean, "tube_se": float(tube_se), "tube_se_independent": tube_se1,
                 "tube_se_pairs": tube_se2,
                 "downstream_mean": down_mean, "downstream_se": float(np.nanmax([down_se1, down_se2])),
                 "upstream_mean": up_mean, "upstream_se": float(np.nanmax([up_se1, up_se2]))},
        "paper": paper,
        "profile": prof,
        "centerline": cl,
        "r_fit_over_R": r_fit,
        "fig3_comparison": {
            "reference_dir": reference["dir"],
            "note": ("差 = この DSMC − 参照。参照点の X が管内の DSMC のスラブ中心の範囲にあるものだけを使う(外挿しない)。"
                     "デジタイズの読み取り誤差は P・Ux で ±0.001 程度(傾きの急な所は ±0.004)"),
            "stats": fig3_stats},
        "log": {"timing_s": log["timing"], "finished": log["finished"], "setup": log["setup"]},
    }
    if paper:
        res["mdot"]["diff_vs_paper_reference_percent"] = 100.0 * (tube_mean / paper["reference"] - 1.0)
        res["mdot"]["diff_vs_paper_gsis_pbc_percent"] = 100.0 * (tube_mean / paper["gsis_pbc"] - 1.0)
    # 入口面の圧力 p_face が p_in からずれた分の補正の目安(感度の評価用。主な値は補正なしの Ṁ)
    #   Ṁ(δ) ≈ Ṁ_meas × (p_in/p_face)^(1+s)、s = d ln Ṁ / d ln δ(論文 Table 1 の参照値の傾きの目安)
    pr_mean, pr_se1, pr_se2 = mean_se(blocks["p_res"][in_window])
    pf_mean, pf_se1, pf_se2 = mean_se(blocks["p_face"][in_window])
    slope = {1: 0.2, 10: 0.33, 100: 0.45}.get(int(prm["delta_rp"]), 0.3)
    res["reservoir"] = {
        "p_face_over_pin": pf_mean, "p_face_se": float(np.nanmax([pf_se1, pf_se2])),
        "p_face_region": "入口面に接する 2 層のセル(x < -8R + 2Δx)、r < 7R",
        "p_res_over_pin": pr_mean, "p_res_se": float(np.nanmax([pr_se1, pr_se2])),
        "p_res_region": "x ∈ [-7R, -3R], r < 6R(管入口へ向かう流れの圧力低下を含む)",
        "slope_dlnMdot_dlndelta_assumed": slope,
        "mdot_corrected_to_p_face_estimate":
            tube_mean * (1.0 / pf_mean) ** (1.0 + slope) if pf_mean > 0 else math.nan,
    }
    if len(diag):
        res["particles"] = {"initial": int(diag[0, 2]), "final": int(diag[-1, 2])}
        steps = log["finished"].get("step", int(diag[-1, 0]))
        if log["timing"]:
            res["seconds_per_step"] = sum(log["timing"].values()) / max(steps, 1)
    # 入口近傍(x ∈ [-8R, -7R])の圧力(入口面の流入条件による p_in からのずれの確認)
    xin = (x > -8.0) & (x < -7.0)
    res["inlet_region_p_over_pin"] = float(np.nanmean(cl["p"][xin])) if xin.any() else math.nan

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "mdot_blocks.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        keys = list(blocks)
        wr.writerow(keys)
        for i in range(len(blocks["step"])):
            wr.writerow([blocks[k][i] for k in keys])
    with open(out / "mdot_profile.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["x_over_R", "Mdot", "Mdot_se"])
        for i in range(len(x)):
            wr.writerow([f"{x[i]:.6f}", f"{prof['mean'][i]:.6e}", f"{prof['se'][i]:.3e}"])
    with open(out / "centerline.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        cols = ["x", "p", "p_se", "u", "u_se", "n", "n_se", "T", "T_se", "p_axis_cell", "u_axis_cell", "ncells"]
        wr.writerow(["x_over_R", "p_over_pin", "p_se", "ux_over_sqrt2RT0", "ux_se", "n_over_nin", "n_se",
                     "T_over_T0", "T_se", "p_over_pin_axis_cell", "ux_axis_cell", "fit_cells"])
        for i in range(len(x)):
            wr.writerow([f"{cl[c][i]:.6e}" if c != "ncells" else cl[c][i] for c in cols])
    with open(out / "fig3_comparison.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["quantity", "method", "file", "X", "reference", "dsmc", "dsmc_se", "diff_dsmc_minus_ref"])
        wr.writerows(fig3_rows)
    if plots:
        title = f"{prm['case']}(δ={prm['delta_rp']})"
        make_plots(out, res, title, reference["fig3"])

    def jsonable(o):
        if isinstance(o, dict):
            return {k: jsonable(v) for k, v in o.items() if k not in ("profile", "centerline", "blocks")}
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, float) and math.isnan(o):
            return None
        return o
    summary = jsonable(res)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--steady-from", type=int, default=None, help="平均を始めるステップ(既定: 全体の 40%%)")
    ap.add_argument("--r-fit", type=float, default=None, help="中心線の当てはめ半径 [R](既定: max(0.35, 2.2Δy))")
    ap.add_argument("--out", type=Path, default=None, help="出力先(既定: <run_dir>/analysis)")
    ap.add_argument("--reference-dir", default=str(DEFAULT_REF_DIR),
                    help="論文の Table 1・Fig.3 のデジタイズ値の場所(既定: reference/paper_2610.07658。none で比較しない)")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args(argv)
    out = args.out or (args.run_dir / "analysis")
    ref_dir = None if args.reference_dir.lower() == "none" else Path(args.reference_dir)
    res = analyze(args.run_dir, args.steady_from, args.r_fit, out, ref_dir, not args.no_plots)
    m = res["mdot"]
    # コンソールが CP932 でも表示できるよう ASCII で出力する
    print(f"case {res['case']} (delta={res['delta_rp']}), steady from step {res['steady']['from_step']} "
          f"({res['steady']['n_blocks']} blocks), z(half diff) = {res['steady']['z_second_minus_first_half']:.2f}")
    print(f"Mdot (tube slabs)      = {m['tube_mean']:.5f} +- {m['tube_se']:.5f}")
    print(f"Mdot (downstream)      = {m['downstream_mean']:.5f} +- {m['downstream_se']:.5f}")
    print(f"Mdot (upstream)        = {m['upstream_mean']:.5f} +- {m['upstream_se']:.5f}")
    if "diff_vs_paper_reference_percent" in m:
        print(f"vs paper DSMC {res['paper']['reference']}: {m['diff_vs_paper_reference_percent']:+.2f} %, "
              f"vs paper GSIS-PBC {res['paper']['gsis_pbc']}: {m['diff_vs_paper_gsis_pbc_percent']:+.2f} %")
    print(f"p/p_in near inlet face (x in [-8,-7]R, centerline) = {res['inlet_region_p_over_pin']:.4f}")
    rv = res["reservoir"]
    print(f"p_face/p_in (2 cell layers at inlet face) = {rv['p_face_over_pin']:.5f} +- {rv['p_face_se']:.5f}, "
          f"p_res/p_in (x in [-7,-3]R, r<6R) = {rv['p_res_over_pin']:.5f} +- {rv['p_res_se']:.5f}")
    print(f"Mdot corrected to p_face = p_in (estimate, slope {rv['slope_dlnMdot_dlndelta_assumed']}) = "
          f"{rv['mdot_corrected_to_p_face_estimate']:.5f}")
    if "seconds_per_step" in res:
        print(f"seconds per step = {res['seconds_per_step']:.4f}, particles {res.get('particles')}")
    for k, v in res["fig3_comparison"]["stats"].items():
        print(f"Fig.3 {k:10s}: n={v['n_points']:4d} X=[{v['x_range'][0]:.2f},{v['x_range'][1]:.2f}] "
              f"mean diff {v['mean_diff']:+.4f}, rms {v['rms_diff']:.4f}, max |diff| {v['max_abs_diff']:.4f} "
              f"at X={v['x_at_max_abs_diff']:.2f} (DSMC se rms {v['rms_dsmc_se']:.4f})")
    print(f"outputs: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
