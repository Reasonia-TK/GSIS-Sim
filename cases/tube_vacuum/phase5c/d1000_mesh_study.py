"""フェーズ 5c: 管 δ_rp = 1000 の NSF のみの PBC 単独(pbc-run、CPU)のメッシュ依存性。

フェーズ 5b-1 で、本番のメッシュ tube_hex の NSF のみの PBC 単独(2 次の移流項)の Ṁ = 1.3112 が、論文の GSIS(1.275)より +2.8%、
NS(FLUENT、1.267)より +3.5% 大きかった(docs/phase5b_d1000_report.md §2.5)。この差がメッシュから来るかを、1/4 領域(対称面 2 枚)の
六面体メッシュを一様に細かくして確かめる(メッシュはフェーズ 5b-2 の make_mesh.py で作ったもの。細かいものはこのフォルダの mesh/)。

- 設定は cases/tube_vacuum/phase5c/tube_d1000_p5c.toml と同じ(移流項 2 次、境界面の値はセルの値)。単独モードの壁は運動論と
  整合する半空間 Maxwell 流束(付録 B.2、整合補正なし)
- 1/4 領域の Ṁ は 4 倍して全体の値にする

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5c/d1000_mesh_study.py make
  uv run --project python --no-sync python cases/tube_vacuum/phase5c/d1000_mesh_study.py run [ジョブ...] [--threads 8]
  uv run --project python --no-sync python cases/tube_vacuum/phase5c/d1000_mesh_study.py summary
出力: ケース cases/tube_vacuum/phase5c/d1000_nsf/<ジョブ>.toml、計算 runs/phase5c/d1000_nsf/<ジョブ>/(summary.json・cells.csv)、
      まとめ cases/tube_vacuum/phase5c/results/d1000_nsf_mesh.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXE = ROOT / "runs" / "phase5c" / "bin" / "gsis-solver-5c.exe"
CASE_DIR = HERE / "d1000_nsf"
RUN_DIR = ROOT / "runs" / "phase5c" / "d1000_nsf"
RESULTS = HERE / "results"
M5B = ROOT / "cases" / "tube_vacuum" / "phase5b_d10" / "mesh"
M5C = HERE / "mesh"

# (ジョブ名, メッシュ, 1/4 領域か, 細かさの倍率, 等面積か, 移流項)
JOBS = [
    ("full_base_u2", ROOT / "cases" / "tube_vacuum" / "mesh" / "tube_hex.pmesh", False, 1.0, False, "upwind2"),
    ("full_base_u1", ROOT / "cases" / "tube_vacuum" / "mesh" / "tube_hex.pmesh", False, 1.0, False, "upwind1"),
    ("q_r050_u2", M5B / "q_r050.pmesh", True, 0.5, False, "upwind2"),
    ("q_r075_u2", M5B / "q_r075.pmesh", True, 0.75, False, "upwind2"),
    ("q_base_u2", M5B / "q_base.pmesh", True, 1.0, False, "upwind2"),
    ("q_r125_u2", M5B / "q_r125.pmesh", True, 1.25, False, "upwind2"),
    ("q_r150_u2", M5B / "q_r150.pmesh", True, 1.5, False, "upwind2"),
    ("q_r175_u2", M5B / "q_r175.pmesh", True, 1.75, False, "upwind2"),
    ("q_r200_u2", M5C / "q_r200.pmesh", True, 2.0, False, "upwind2"),
    ("q_r250_u2", M5C / "q_r250.pmesh", True, 2.5, False, "upwind2"),
    ("q_r050eq_u2", M5B / "q_r050_eq.pmesh", True, 0.5, True, "upwind2"),
    ("q_r075eq_u2", M5B / "q_r075_eq.pmesh", True, 0.75, True, "upwind2"),
    ("q_baseeq_u2", M5B / "q_eqarea.pmesh", True, 1.0, True, "upwind2"),
    ("q_r150eq_u2", M5C / "q_r150_eq.pmesh", True, 1.5, True, "upwind2"),
    ("q_r200eq_u2", M5C / "q_r200_eq.pmesh", True, 2.0, True, "upwind2"),
    ("q_r250eq_u2", M5C / "q_r250_eq.pmesh", True, 2.5, True, "upwind2"),
    # 1 次の移流項(論文の式 (27)(28) のドナーセル)
    ("q_base_u1", M5B / "q_base.pmesh", True, 1.0, False, "upwind1"),
    ("q_r150_u1", M5B / "q_r150.pmesh", True, 1.5, False, "upwind1"),
    ("q_r200_u1", M5C / "q_r200.pmesh", True, 2.0, False, "upwind1"),
    ("q_r250_u1", M5C / "q_r250.pmesh", True, 2.5, False, "upwind1"),
    # 部分的な細分化(フェーズ 5b-2 のメッシュ): 槽、管の出入口のまわり、周方向
    ("q_hmax025_u2", M5B / "q_hmax025.pmesh", True, 1.0, False, "upwind2"),
    ("q_mouth_u2", M5B / "q_mouth.pmesh", True, 1.0, False, "upwind2"),
    ("q_nth32_u2", M5B / "q_nth32.pmesh", True, 1.0, False, "upwind2"),
    # 壁の最初のセルだけを細かくする(ds_wall を本番の 1/2・1/4・1/8。半径方向の分割が 12 → 13・16・19 に増える)
    ("q_dsw050_u2", M5C / "q_dsw050.pmesh", True, 1.0, False, "upwind2"),
    ("q_dsw025_u2", M5C / "q_dsw025.pmesh", True, 1.0, False, "upwind2"),
    ("q_dsw0125_u2", M5C / "q_dsw0125.pmesh", True, 1.0, False, "upwind2"),
]

# 単独モードの壁の扱いを 2 次にした変種(勾配の境界値を壁の境界面の分布のモーメント、境界面の値を制限付きの再構成)。
# 収束した "cell" の解から始めても、CFL 1〜500 のどれでも 13〜21 回目に発散したので使わない(フェーズ 3 の報告の
# 「単独モードの制限付きの再構成は発散・停滞しやすい」と同じ)。{ジョブ名: (元のジョブ, [macro] に足す行)}
WALL_VARIANTS: dict[str, tuple[str, str]] = {}
for _w, (_src, _extra) in WALL_VARIANTS.items():
    _base = next(j for j in JOBS if j[0] == _src)
    JOBS.append((_w, *_base[1:]))

TEMPLATE = """# フェーズ 5c: 管 δ_rp = 1000 の NSF のみの PBC 単独(pbc-run)のメッシュ依存性(d1000_mesh_study.py で生成)。
# メッシュ: {mesh_rel}({domain})
[case]
name = "{name}"
mesh = "{mesh_rel_toml}"
mode = "gsis"

[gas]
molecular_mass = 6.65e-27
mu_ref = 1.916e-5
T_ref = 273.15
omega = 0.66

[conditions]
L_ref = 1.0e-3
T0 = 273.15
delta_rp = 1000

[[boundary]]
patch = "inlet"
type = "pressure_inlet"
temperature = 273.15
tangential_velocity = "zero"

[[boundary]]
patch = "outlet"
type = "vacuum"

[[boundary]]
patch = "wall"
type = "diffuse_wall"
temperature = 273.15
{symmetry}
[velocity_grid]
n = [30, 24, 24]
range = [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]]

[gsis]
cfl_pbc = 500.0

[macro]
advection = "{advection}"
boundary_reconstruction = "cell"
"""

SYM = """
[[boundary]]
patch = "sym_y"
type = "symmetry"

[[boundary]]
patch = "sym_z"
type = "symmetry"
"""


def make() -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    for name, mesh, quarter, _, _, adv in JOBS:
        rel = Path("..") / mesh.relative_to(HERE) if mesh.is_relative_to(HERE) else Path(
            "../../" + str(mesh.relative_to(ROOT / "cases" / "tube_vacuum")).replace("\\", "/"))
        text = TEMPLATE.format(
            name=name, mesh_rel=str(mesh.relative_to(ROOT)).replace("\\", "/"),
            mesh_rel_toml=str(rel).replace("\\", "/"), domain="1/4 領域、対称面 sym_y・sym_z" if quarter else "全体",
            symmetry=SYM if quarter else "", advection=adv)
        if name in WALL_VARIANTS:
            text += WALL_VARIANTS[name][1]
        (CASE_DIR / f"{name}.toml").write_text(text, encoding="utf-8", newline="\n")
    print(f"{len(JOBS)} 個のケースを {CASE_DIR} に書きました")


def run(names: list[str], threads: int, iterations: int) -> None:
    sel = [j for j in JOBS if not names or j[0] in names]
    for name, *_ in sel:
        out = RUN_DIR / name
        if (out / "summary.json").exists():
            print(f"{name}: 済み")
            continue
        out.mkdir(parents=True, exist_ok=True)
        cmd = [str(EXE), "pbc-run", str(CASE_DIR / f"{name}.toml"), "--out", str(out), "--iterations", str(iterations),
               "--threads", str(threads), "--print-every", "10", "--no-vtu", "--cells-csv"]
        if name in WALL_VARIANTS:
            src = RUN_DIR / WALL_VARIANTS[name][0] / "cells.csv"
            if not src.exists():
                print(f"{name}: 元の解 {src} がないので飛ばします")
                continue
            # 収束した解から始めるので CFL の立ち上げはしない
            cmd += ["--restart", str(src), "--cfl-start", "0"]
        t0 = time.perf_counter()
        with (RUN_DIR / f"{name}.log").open("w", encoding="utf-8") as log:
            code = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        print(f"{name}: 終了コード {code}、{time.perf_counter() - t0:.0f} s", flush=True)


def summary() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, mesh, quarter, refine, eq, adv in JOBS:
        sj = RUN_DIR / name / "summary.json"
        if not sj.exists():
            continue
        s = json.loads(sj.read_text(encoding="utf-8"))
        k = 4.0 if quarter else 1.0
        mf = s.get("mass_flow", {})
        rows.append(dict(
            job=name, refine=refine, quarter=quarter, equal_area=eq, advection=adv,
            wall="2nd" if name in WALL_VARIANTS else "cell",
            n_cells=s["n_cells"], n_cells_full=int(s["n_cells"] * k),
            iterations=s["iterations"], converged=s["converged"], time_s=round(s.get("run_seconds", 0.0), 1),
            M_dot_inlet=k * mf.get("M_dot_inlet", math.nan), M_dot_outlet=k * mf.get("M_dot_outlet", math.nan),
            final_residual_mass=s.get("final_residual_mass", math.nan)))
    if not rows:
        print("結果がありません")
        return
    out = RESULTS / "d1000_nsf_mesh.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{'ジョブ':<14} {'倍率':>5} {'等面積':>5} {'移流':>8} {'全体相当のセル':>14} {'反復':>5} {'時間 s':>7} {'Ṁ 入口':>9} {'Ṁ 出口':>9} {'論文 GSIS 比':>10} {'NS 比':>8}")
    for r in rows:
        m = r["M_dot_inlet"]
        print(f"{r['job']:<14} {r['refine']:>5.2f} {str(r['equal_area']):>5} {r['advection']:>8} {r['n_cells_full']:>14,} "
              f"{r['iterations']:>5} {r['time_s']:>7.0f} {m:>9.5f} {r['M_dot_outlet']:>9.5f} {100 * (m / 1.275 - 1):>+9.2f}% "
              f"{100 * (m / 1.267 - 1):>+7.2f}%")
    print(f"表: {out}")


def plot(fig_dir: Path) -> None:
    """Ṁ のメッシュ依存性の図(左: 一様な細分化、右: 壁の最初のセルだけの細分化)"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": ["BIZ UDPGothic", "Yu Gothic", "Meiryo", "DejaVu Sans"], "font.size": 10,
        "axes.edgecolor": "#52514e", "xtick.color": "#52514e", "ytick.color": "#52514e", "axes.grid": True,
        "grid.color": "#e4e3df", "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "figure.dpi": 150,
    })
    res = {}
    for name, mesh, quarter, refine, eq, adv in JOBS:
        sj = RUN_DIR / name / "summary.json"
        if sj.exists():
            s = json.loads(sj.read_text(encoding="utf-8"))
            res[name] = (4.0 if quarter else 1.0) * s["mass_flow"]["M_dot_inlet"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), constrained_layout=True)
    ax = axes[0]
    series = [("2 次の移流項", "_u2", "#2a78d6", "o-", False), ("2 次・断面積を円と同じに", "eq_u2", "#1baf7a", "s--", True),
              ("1 次の移流項", "_u1", "#eb6834", "^-", False)]
    for label, suf, color, style, eqf in series:
        pts = []
        for name, mesh, quarter, refine, eq, adv in JOBS:
            if not quarter or name not in res or eq != eqf or not name.startswith("q_r") and not name.startswith("q_base"):
                continue
            if (suf == "_u1") != (adv == "upwind1"):
                continue
            pts.append((1.0 / refine, res[name]))
        if pts:
            pts.sort()
            h, m = zip(*pts)
            ax.plot(h, m, style, color=color, lw=1.8, ms=5, label=label)
    for v, lab, ls in ((1.275, "論文 GSIS-PBC 1.275", "--"), (1.267, "論文の NS(FLUENT)1.267", ":")):
        ax.axhline(v, color="#0b0b0b", lw=1.0, ls=ls, label=lab)
    ax.axhline(1.331, color="#7a7974", lw=1.0, ls="-.", label="本実装 GSIS(未収束、本番のメッシュ)1.331")
    ax.set_xlim(0, 2.1)
    ax.set_xlabel("セルの大きさ h / h_本番(1 = 本番のメッシュ。1/4 領域で一様に細分化)")
    ax.set_ylabel(r"$\dot{M}$(NSF のみの PBC 単独)")
    ax.set_title("一様な細分化", loc="left")
    ax.legend(fontsize=7.5, loc="lower left")
    ax = axes[1]
    pts = [(1.0, res.get("q_base_u2"))] + [(f, res.get(n)) for f, n in ((0.5, "q_dsw050_u2"), (0.25, "q_dsw025_u2"),
                                                                       (0.125, "q_dsw0125_u2"))]
    pts = [(f, m) for f, m in pts if m is not None]
    if pts:
        f, m = zip(*pts)
        ax.plot(f, m, "o-", color="#2a78d6", lw=1.8, ms=5, label="壁の最初のセルだけ細かく(ほかは本番)")
    for v, lab, ls in ((1.275, "論文 GSIS-PBC", "--"), (1.267, "論文の NS(FLUENT)", ":")):
        ax.axhline(v, color="#0b0b0b", lw=1.0, ls=ls, label=lab)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("壁の最初のセルの厚さ / 本番の 0.0446R")
    ax.set_ylabel(r"$\dot{M}$")
    ax.set_title("壁の最初のセルだけの細分化", loc="left")
    ax.legend(fontsize=7.5, loc="center left")
    fig.suptitle("管 δ = 1000 の NSF のみの PBC 単独(pbc-run)の質量流量のメッシュ依存性", x=0.01, ha="left", fontsize=11)
    fig_dir.mkdir(parents=True, exist_ok=True)
    out = fig_dir / "d1000_nsf_mesh.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"図: {out}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["make", "run", "summary", "list", "plot"])
    ap.add_argument("jobs", nargs="*")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--iterations", type=int, default=400)
    a = ap.parse_args()
    if a.action == "make":
        make()
    elif a.action == "run":
        run(a.jobs, a.threads, a.iterations)
    elif a.action == "summary":
        summary()
    elif a.action == "plot":
        plot(ROOT / "docs" / "figures")
    else:
        for j in JOBS:
            done = (RUN_DIR / j[0] / "summary.json").exists()
            print(f"{j[0]:<14} {'済み' if done else '未'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
