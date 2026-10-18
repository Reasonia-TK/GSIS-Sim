"""CIS のパラメータ(空間ブロック数 P、速度チャンク、擬似時間の CFL、リミッタ)による反復回数・時間の変化を、
粗い管メッシュ(mesh/tube_hex_coarse.pmesh、14,464 セル × 17,280 速度点)で調べる。

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/tube_vacuum/kinetic/param_study.py [--delta 1 10] [--max-iter 3000]
結果: runs/kinetic/param_study/<名前>/(cis-run の出力)と cases/tube_vacuum/kinetic/results/param_study.csv
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

# (名前, cis-run に渡す --set の一覧)。base は既定値(RCB のブロック、P = 自動(粗い管で 7)、チャンク = 自動、
# Δt = ∞、リミッタ relative・K = 1、ブロック境界の対角補償 β = 0.5)
VARIANTS = [
    ("base", []),
    ("P1", ["sweep_blocks=1"]),
    ("P4", ["sweep_blocks=4"]),
    ("P16", ["sweep_blocks=16"]),
    ("P64", ["sweep_blocks=64"]),
    ("P256", ["sweep_blocks=256"]),
    ("rcm", ["cell_ordering=rcm"]),
    ("rcmP64", ["cell_ordering=rcm", "sweep_blocks=64"]),
    ("chunk512", ["velocity_chunk=512"]),
    ("chunk2048", ["velocity_chunk=2048"]),
    ("chunk8192", ["velocity_chunk=8192"]),
    ("gcfl100", ["pseudo_cfl=100"]),
    ("gcfl30", ["pseudo_cfl=30"]),
    ("vcfl10", ["pseudo_cfl=10", "pseudo_time=velocity"]),
    ("vcfl3", ["pseudo_cfl=3", "pseudo_time=velocity"]),
    ("vcfl1", ["pseudo_cfl=1", "pseudo_time=velocity"]),
    ("Krel0.3", ["limiter_k=0.3"]),
    ("Krel3", ["limiter_k=3"]),
    ("Krel10", ["limiter_k=10"]),
    ("Kabs5", ["limiter_eps=absolute", "limiter_k=5"]),
    ("nolimiter", ["limiter=none"]),
    ("wallrelax0.5", ["wall_relaxation=0.5"]),
    ("damp0", ["interface_damping=0"]),
    ("damp0.25", ["interface_damping=0.25"]),
    ("damp1", ["interface_damping=1"]),
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", nargs="+", type=int, default=[1, 10])
    ap.add_argument("--max-iter", type=int, default=3000)
    ap.add_argument("--only", nargs="*", default=None, help="この名前の組だけ計算する")
    ap.add_argument("--solver", default=str(ROOT / "build" / "phase2" / "src" / "gsis-solver.exe"))
    args = ap.parse_args()
    out_csv = HERE / "results" / "param_study.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if out_csv.exists():
        with out_csv.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    for delta in args.delta:
        case = HERE / f"tube_coarse_delta{delta}_cis.toml"
        for name, sets in VARIANTS:
            if args.only is not None and name not in args.only:
                continue
            tag = f"d{delta}_{name}"
            out = ROOT / "runs" / "kinetic" / "param_study" / tag
            cmd = [args.solver, "-q", "cis-run", str(case), "--out", str(out), "--no-vtu", "--print-every", "0",
                   "--max-iter", str(args.max_iter)]
            for s in sets:
                cmd += ["--set", s]
            t0 = time.time()
            res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            dt = time.time() - t0
            if res.returncode != 0:
                print(f"{tag:22s} 失敗: {res.stderr.strip()[-300:]}", flush=True)
                row = {"tag": tag, "delta": delta, "variant": name, "settings": " ".join(sets), "status": "failed"}
            else:
                s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
                mf = s.get("mass_flow", {})
                kt = s["kernel_time_per_iteration_s"]
                row = {
                    "tag": tag,
                    "delta": delta,
                    "variant": name,
                    "settings": " ".join(sets),
                    "status": "converged" if s["converged"] else "max_iter",
                    "iterations": s["iterations"],
                    "epsilon": s.get("epsilon"),
                    "M_dot": mf.get("M_dot"),
                    "M_dot_inlet": mf.get("M_dot_inlet"),
                    "M_dot_outlet": mf.get("M_dot_outlet"),
                    "time_per_iter_s": s["time_per_iteration_s"],
                    "wall_time_s": s["wall_time_s"],
                    "sweep_blocks": s["sweep_blocks"],
                    "velocity_chunk": s["velocity_chunk"],
                    "t_gradient": kt["gradient"],
                    "t_residual": kt["residual"],
                    "t_sweep": kt["sweep_forward"] + kt["sweep_backward"],
                    "t_moments": kt["moments"],
                    "t_boundary": kt["boundary"],
                }
                print(f"{tag:22s} {row['status']:9s} it {row['iterations']:5d}  Mdot {row['M_dot']:.5f}  "
                      f"{row['time_per_iter_s'] * 1e3:7.1f} ms/it  {dt:6.1f} s", flush=True)
            rows = [r for r in rows if r.get("tag") != tag] + [row]
            keys = []
            for r in rows:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            with out_csv.open("w", encoding="utf-8", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=keys)
                wr.writeheader()
                for r in rows:
                    wr.writerow(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
