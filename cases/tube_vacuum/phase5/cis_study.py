"""フェーズ 5a: CIS の強化の候補(内部反復、壁の更新、スイープの順序、ブロック数)を比べる。

粗い管(cases/tube_vacuum/kinetic/mesh/tube_hex_coarse.pmesh、14,464 セル)または本番の管(tube_hex、135,104 セル)の
CIS 単独(cis-run、全域 p_in に静止から、式 (59) の ε < 1e-5)で、反復数・1 反復の時間・質量流量を記録する。

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/tube_vacuum/phase5/cis_study.py --mesh coarse --delta 1 --only base oct
結果: runs/phase5/cis_<mesh>/<名前>/(cis-run の出力)と cases/tube_vacuum/phase5/results/cis_study_<mesh>.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

# (名前, --set の一覧)
VARIANTS = {
    "base": [],
    "P1": ["sweep_blocks=1"],
    "in2": ["inner_sweeps=2"],
    "in4": ["inner_sweeps=4"],
    "in8": ["inner_sweeps=8"],
    "in2w": ["inner_sweeps=2", "inner_wall_update=true"],
    "in4w": ["inner_sweeps=4", "inner_wall_update=true"],
    "in8w": ["inner_sweeps=8", "inner_wall_update=true"],
    "oct": ["sweep_order=octant"],
    "octP1": ["sweep_order=octant", "sweep_blocks=1"],
    "d13": ["sweep_order=dir13"],
    "d13P1": ["sweep_order=dir13", "sweep_blocks=1"],
    "oct_in2": ["sweep_order=octant", "inner_sweeps=2"],
    "oct_in4": ["sweep_order=octant", "inner_sweeps=4"],
    "oct_in2w": ["sweep_order=octant", "inner_sweeps=2", "inner_wall_update=true"],
    "oct_in4w": ["sweep_order=octant", "inner_sweeps=4", "inner_wall_update=true"],
    "d13_in2w": ["sweep_order=dir13", "inner_sweeps=2", "inner_wall_update=true"],
    "d13_in4w": ["sweep_order=dir13", "inner_sweeps=4", "inner_wall_update=true"],
    "oct_bp2": ["sweep_order=octant", "block_passes=2"],
    "oct_bp3": ["sweep_order=octant", "block_passes=3"],
    "oct_in2w_bp2": ["sweep_order=octant", "inner_sweeps=2", "inner_wall_update=true", "block_passes=2"],
    "oct_in4w_bp2": ["sweep_order=octant", "inner_sweeps=4", "inner_wall_update=true", "block_passes=2"],
    "oct_damp0": ["sweep_order=octant", "interface_damping=0"],
    "oct_damp0.25": ["sweep_order=octant", "interface_damping=0.25"],
}

MESHES = {
    "coarse": "cases/tube_vacuum/kinetic/tube_coarse_delta{d}_cis.toml",
    "fine": "cases/tube_vacuum/kinetic/tube_delta{d}_cis.toml",
}


def first_below(hist: list[dict], tol: float) -> int | None:
    for r in hist:
        e = r.get("epsilon", "")
        if e not in ("", "nan") and int(r["iteration"]) > 1 and float(e) < tol:
            return int(r["iteration"])
    return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", choices=list(MESHES), default="coarse")
    ap.add_argument("--delta", nargs="+", type=int, default=[1])
    ap.add_argument("--max-iter", type=int, default=3000)
    ap.add_argument("--tol", type=float, default=1e-5)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--extra", nargs="*", default=[], help="全部の組に足す --set")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--solver", default=str(ROOT / "build" / "phase5" / "src" / "gsis-solver.exe"))
    args = ap.parse_args()
    out_csv = HERE / "results" / f"cis_study_{args.mesh}.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if out_csv.exists():
        with out_csv.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    names = args.only if args.only else list(VARIANTS)
    for delta in args.delta:
        case = ROOT / MESHES[args.mesh].format(d=delta)
        for name in names:
            sets = VARIANTS[name] + args.extra
            tag = f"d{delta}_{name}{args.suffix}"
            out = ROOT / "runs" / "phase5" / f"cis_{args.mesh}" / tag
            cmd = [args.solver, "-q", "cis-run", str(case), "--out", str(out), "--no-vtu", "--print-every", "0",
                   "--max-iter", str(args.max_iter), "--tol", str(args.tol)]
            for s in sets:
                cmd += ["--set", s]
            t0 = time.time()
            res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            dt = time.time() - t0
            if res.returncode != 0:
                print(f"{tag}: 失敗 ({res.returncode}) {res.stderr.strip()[:400]}", flush=True)
                row = {"tag": tag, "delta": delta, "variant": name, "sets": " ".join(sets), "iterations": "fail"}
            else:
                summ = json.loads((out / "summary.json").read_text(encoding="utf-8"))
                with (out / "history.csv").open(encoding="utf-8") as fh:
                    hist = list(csv.DictReader(fh))
                mf = summ.get("mass_flow", {})
                row = {
                    "tag": tag,
                    "delta": delta,
                    "variant": name,
                    "sets": " ".join(sets),
                    "iterations": summ["iterations"],
                    "converged": summ["converged"],
                    "it_1e-4": first_below(hist, 1e-4),
                    "it_1e-5": first_below(hist, 1e-5),
                    "it_1e-6": first_below(hist, 1e-6),
                    "time_per_iteration_s": f"{summ['time_per_iteration_s']:.4f}",
                    "wall_time_s": f"{dt:.1f}",
                    "Mdot_inlet": f"{mf.get('M_dot_inlet', float('nan')):.5f}",
                    "Mdot_outlet": f"{mf.get('M_dot_outlet', float('nan')):.5f}",
                }
                print(f"{tag}: 反復 {row['iterations']}(1e-4: {row['it_1e-4']}、1e-6: {row['it_1e-6']})"
                      f" 1 反復 {row['time_per_iteration_s']} s、Ṁ {row['Mdot_inlet']} / {row['Mdot_outlet']}",
                      flush=True)
            rows = [r for r in rows if r.get("tag") != tag] + [row]
            keys: list[str] = []
            for r in rows:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            with out_csv.open("w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=keys)
                w.writeheader()
                w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
