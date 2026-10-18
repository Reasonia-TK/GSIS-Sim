"""平板の検証ケース(GSIS)を gsis-solver run で順に計算する(結果は runs/gsis/<ケース名>/)。

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/verification/gsis/run_slab.py
      [--kinds heat_lin couette_lin ...] [--kn 0.01 0.1 1 10] [--nx 100] [--periodic]
      [--tol 1e-6] [--max-iter 5000] [--solver build/phase4/src/gsis-solver.exe] [--extra "--set macro.x=y"]
      [--suffix _variant] [--jobs 4] [--skip-done] [--out-root runs/phase5/slab]
収束判定はケースの [gsis].tolerance(--tol を与えたときはその値)。--jobs で同時にいくつ計算するか(既定 1。
CPU の運動論ソルバーなら --extra "--backend cpu --threads 4" と組み合わせる)。
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def kn_tag(kn: float) -> str:
    return f"{kn:g}".replace(".", "p")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", nargs="+", default=["heat_lin", "heat_nl", "couette_lin", "couette_nl"])
    ap.add_argument("--kn", nargs="+", type=float, default=[0.01, 0.1, 1.0, 10.0])
    ap.add_argument("--nx", nargs="+", type=int, default=[100])
    ap.add_argument("--tol", type=float, default=0.0, help="0 ならケースの [gsis].tolerance")
    ap.add_argument("--max-iter", type=int, default=5000)
    ap.add_argument("--solver", default=str(ROOT / "build" / "phase4" / "src" / "gsis-solver.exe"))
    ap.add_argument("--extra", default="", help="run に足す引数(例: \"--set macro.hot_interpolation=face\")")
    ap.add_argument("--suffix", default="", help="出力先の名前に足す文字列(設定の比較用)")
    ap.add_argument("--periodic", action="store_true", help="側面をすべて並進周期にした版(_per)を計算する")
    ap.add_argument("--jobs", type=int, default=1, help="同時に計算するケースの数")
    ap.add_argument("--out-root", default=str(ROOT / "runs" / "gsis"),
                    help="出力先の親ディレクトリ(既定 runs/gsis。フェーズ 5a は runs/phase5/slab)")
    ap.add_argument("--skip-done", action="store_true",
                    help="出力先の summary.json が収束済みのケースは計算しない(途中から再開する)")
    args = ap.parse_args()
    jobs = []
    for kind in args.kinds:
        for kn in args.kn:
            for nx in args.nx:
                tag = f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}" + ("_per" if args.periodic else "")
                case = HERE / f"{tag}_gsis.toml"
                out = Path(args.out_root) / (tag + "_gsis" + args.suffix)
                if args.skip_done and (out / "summary.json").exists():
                    done = json.loads((out / "summary.json").read_text(encoding="utf-8"))
                    if done.get("converged") and (out / "cells.csv").exists():
                        print(f"{tag + args.suffix:45s} 済み(計算しない)", flush=True)
                        continue
                cmd = [args.solver, "-q", "run", str(case), "--out", str(out), "--cells-csv", "--no-vtu",
                       "--print-every", "0", "--max-iter", str(args.max_iter)]
                if args.tol > 0.0:
                    cmd += ["--tol", f"{args.tol:g}"]
                cmd += shlex.split(args.extra)
                jobs.append((tag + args.suffix, cmd))

    def run_one(job):
        name, cmd = job
        t0 = time.time()
        res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        return name, time.time() - t0, res

    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for name, dt, res in pool.map(run_one, jobs):
            status = "ok" if res.returncode == 0 else f"失敗(終了コード {res.returncode})"
            print(f"{name:45s} {dt:8.1f} s  {status}", flush=True)
            if res.returncode != 0:
                failed += 1
                print(res.stderr[-2000:], file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
