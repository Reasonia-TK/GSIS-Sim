"""管(tube_hex、135,104 セル × 17,280 速度点)で、空間ブロック数 P と速度チャンクによる 1 反復の時間を測る。
各設定で数反復だけ計算し(最初の反復は計時から除く)、カーネル別の時間を表にする。

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/tube_vacuum/kinetic/timing_study.py [--iterations 4]
結果: cases/tube_vacuum/kinetic/results/timing_study.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

VARIANTS = [
    ("P16", ["sweep_blocks=16"]),
    ("P32", ["sweep_blocks=32"]),
    ("P48", ["sweep_blocks=48"]),
    ("P64", ["sweep_blocks=64"]),
    ("P96", ["sweep_blocks=96"]),
    ("P128", ["sweep_blocks=128"]),
    ("P256", ["sweep_blocks=256"]),
    ("P64_chunk1024", ["sweep_blocks=64", "velocity_chunk=1024"]),
    ("P64_chunk1536", ["sweep_blocks=64", "velocity_chunk=1536"]),
    ("P64_rcm", ["sweep_blocks=64", "cell_ordering=rcm"]),
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=4)
    ap.add_argument("--solver", default=str(ROOT / "build" / "phase2" / "src" / "gsis-solver.exe"))
    args = ap.parse_args()
    case = HERE / "tube_delta1_cis.toml"
    rows = []
    for name, sets in VARIANTS:
        out = ROOT / "runs" / "kinetic" / "timing_study" / name
        cmd = [args.solver, "-q", "cis-run", str(case), "--out", str(out), "--no-vtu", "--print-every", "0",
               "--max-iter", str(args.iterations)]
        for s in sets:
            cmd += ["--set", s]
        res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            print(f"{name}: 失敗 {res.stderr.strip()[-300:]}", flush=True)
            continue
        s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        kt = s["kernel_time_per_iteration_s"]
        row = {
            "variant": name,
            "settings": " ".join(sets),
            "sweep_blocks": s["sweep_blocks"],
            "velocity_chunk": s["velocity_chunk"],
            "block_cut_faces": s.get("block_cut_faces"),
            "memory_GB": s["memory_bytes"] / 1e9,
            "time_per_iter_s": s["time_per_iteration_s"],
            "moments_ms": 1e3 * kt["moments"],
            "boundary_ms": 1e3 * kt["boundary"],
            "gradient_ms": 1e3 * kt["gradient"],
            "residual_ms": 1e3 * kt["residual"],
            "sweep_forward_ms": 1e3 * kt["sweep_forward"],
            "sweep_backward_ms": 1e3 * kt["sweep_backward"],
            "other_ms": 1e3 * kt["other"],
        }
        rows.append(row)
        print(f"{name:16s} P {row['sweep_blocks']:4d} chunk {row['velocity_chunk']:5d}  {row['time_per_iter_s']:.3f} s/it  "
              f"grad {row['gradient_ms']:.0f} res {row['residual_ms']:.0f} fwd {row['sweep_forward_ms']:.0f} "
              f"bwd {row['sweep_backward_ms']:.0f} ms", flush=True)
    out_csv = HERE / "results" / "timing_study.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with out_csv.open("w", encoding="utf-8", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            for r in rows:
                wr.writerow(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
