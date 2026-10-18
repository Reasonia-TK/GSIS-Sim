"""管の GSIS の結果(gsis-solver run の出力)をまとめ、論文 Table 1 と Fig.3 のデジタイズ値と比べる。

- 無次元質量流量 Ṁ: 補正の後の f の入口・出口の離散流束(summary.json の mass_flow)と、PBC の最後の状態の値(mass_flow_pbc)
- 反復: GSIS の反復数、CIS 反復の合計、ε、時間(初期化を含む)、置換(負値)の合計
- 中心線: cases/tube_vacuum/kinetic/centerline.py と同じ取り出し方で、論文の GSIS・CIS の線との差の RMS と最大

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/tube_vacuum/gsis/summarize.py
      [--runs runs/gsis/tube_delta1_gsis runs/gsis/tube_delta10_gsis ...] [--out cases/tube_vacuum/gsis/results]
結果: <out>/tube_gsis_summary.csv と標準出力の表
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / "reference" / "paper_2610.07658"

# 論文 Table 1(参照は δ = 1, 10, 100 が DSMC、1000 が FLUENT の NS)
PAPER = {
    1.0: dict(ref=0.197, gsis=0.198, gsis_iter=17, gsis_h=0.20, cis=0.198, cis_iter=189),
    10.0: dict(ref=0.330, gsis=0.326, gsis_iter=9, gsis_h=0.11, cis=0.348, cis_iter=484),
    100.0: dict(ref=0.898, gsis=0.885, gsis_iter=7, gsis_h=0.09, cis=0.597, cis_iter=2796),
    1000.0: dict(ref=1.267, gsis=1.275, gsis_iter=6, gsis_h=0.08, cis=0.729, cis_iter=7847),
}
# ユーザーの DSMC(docs/spec.md §5.3)
USER_DSMC = {1.0: 0.19773, 10.0: 0.32773}


def load_centerline_module():
    path = ROOT / "cases" / "tube_vacuum" / "kinetic" / "centerline.py"
    spec = importlib.util.spec_from_file_location("tube_centerline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def delta_tag(delta: float) -> str:
    return f"{delta:g}"


def summarize(run: Path, cl_mod) -> dict | None:
    sp = run / "summary.json"
    if not sp.exists():
        return None
    s = json.loads(sp.read_text(encoding="utf-8"))
    delta = float(s.get("delta", math.nan))
    if math.isfinite(delta):
        delta = float(f"{delta:.6g}")  # 例: 0.9999999999999999 → 1
    mf = s.get("mass_flow") or {}
    mp = s.get("mass_flow_pbc") or {}
    row = {
        "run": str(run.relative_to(ROOT)) if run.is_relative_to(ROOT) else str(run),
        "delta": delta,
        "iterations": s.get("iterations"),
        "cis_iterations": s.get("cis_iterations"),
        "converged": s.get("converged"),
        "epsilon": s.get("epsilon"),
        "M_dot_inlet": mf.get("M_dot_inlet", math.nan),
        "M_dot_outlet": mf.get("M_dot_outlet", math.nan),
        "M_dot_pbc_inlet": mp.get("M_dot_inlet", math.nan),
        "M_dot_pbc_outlet": mp.get("M_dot_outlet", math.nan),
        "wall_time_s": s.get("wall_time_s"),
        "time_per_iteration_s": s.get("time_per_iteration_s"),
        "replaced_total": (s.get("negative_replacement") or {}).get("total_cells"),
        "replaced_last": (s.get("negative_replacement") or {}).get("last_iteration"),
    }
    paper = PAPER.get(delta)
    m = row["M_dot_inlet"]
    if paper:
        row["paper_gsis"] = paper["gsis"]
        row["diff_paper_gsis_pct"] = 100.0 * (m - paper["gsis"]) / paper["gsis"]
        row["paper_ref"] = paper["ref"]
        row["diff_paper_ref_pct"] = 100.0 * (m - paper["ref"]) / paper["ref"]
        row["paper_gsis_iterations"] = paper["gsis_iter"]
        row["paper_gsis_hours"] = paper["gsis_h"]
    if delta in USER_DSMC:
        row["user_dsmc"] = USER_DSMC[delta]
        row["diff_user_dsmc_pct"] = 100.0 * (m - USER_DSMC[delta]) / USER_DSMC[delta]
    cells = run / "cells.csv"
    if cells.exists():
        cl = cl_mod.centerline(cells)
        for qty, col in (("pressure", 1), ("velocity", 2)):
            for kind in ("gsis", "cis"):
                r = cl_mod.compare(cl, REF / f"fig3_{qty}_{kind}_delta{delta_tag(delta)}.csv", col)
                if r is not None:
                    row[f"{qty}_vs_paper_{kind}_rms"] = r[0]
                    row[f"{qty}_vs_paper_{kind}_max"] = r[1]
    return row


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+",
                    default=[str(ROOT / "runs" / "gsis" / f"tube_delta{d}_gsis") for d in (1, 10, 100, 1000)])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    args = ap.parse_args()
    cl_mod = load_centerline_module()
    rows = [r for r in (summarize(Path(p).resolve(), cl_mod) for p in args.runs) if r is not None]
    if not rows:
        print("まとめる結果がありません")
        return 1
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "tube_gsis_summary.csv").open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    print(f"{'δ':>6s} {'反復':>4s} {'CIS':>5s} {'ε':>9s} {'Ṁ入口':>8s} {'Ṁ出口':>8s} {'論文GSIS':>8s} {'差%':>6s} "
          f"{'参照':>6s} {'差%':>6s} {'時間s':>7s} {'p RMS':>7s} {'u RMS':>7s}")
    for r in rows:
        print(f"{r['delta']:6g} {r['iterations']!s:>4s} {r['cis_iterations']!s:>5s} {r['epsilon']:9.2e} "
              f"{r['M_dot_inlet']:8.4f} {r['M_dot_outlet']:8.4f} {r.get('paper_gsis', math.nan):8.3f} "
              f"{r.get('diff_paper_gsis_pct', math.nan):+6.2f} {r.get('paper_ref', math.nan):6.3f} "
              f"{r.get('diff_paper_ref_pct', math.nan):+6.2f} {r['wall_time_s']:7.0f} "
              f"{r.get('pressure_vs_paper_gsis_rms', math.nan):7.4f} {r.get('velocity_vs_paper_gsis_rms', math.nan):7.4f}")
    print(f"書き出し: {out / 'tube_gsis_summary.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
