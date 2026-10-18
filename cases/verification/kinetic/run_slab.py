"""平板の検証ケースを gsis-solver cis-run で順に計算する(結果は runs/kinetic/<ケース名>/)。

実行(リポジトリのルートから):
  uv run --project verification/ref1d python cases/verification/kinetic/run_slab.py
      [--kinds heat_lin couette_lin ...] [--kn 0.01 0.1 1 10] [--nx 50 100 200]
      [--tol 1e-6] [--max-iter 100000] [--solver build/phase2/src/gsis-solver.exe] [--extra "--set limiter_k=1"]
収束判定は式 (59) の ε < tol(既定 1e-6。論文の 1e-5 より厳しくして、熱流束・せん断応力が相対 1e-5 以下まで
変わらなくなるところまで回す。runs/kinetic/<ケース名>/history.csv の qx_mean・sxy_mean で確かめられる)。
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
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
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--max-iter", type=int, default=100000)
    ap.add_argument("--solver", default=str(ROOT / "build" / "phase2" / "src" / "gsis-solver.exe"))
    ap.add_argument("--extra", default="", help="cis-run に足す引数(例: \"--set limiter_k=1\")")
    ap.add_argument("--suffix", default="", help="出力先の名前に足す文字列(パラメータの比較用)")
    ap.add_argument("--periodic", action="store_true", help="側面をすべて並進周期にした版(_per)を計算する")
    args = ap.parse_args()
    failed = 0
    for kind in args.kinds:
        for kn in args.kn:
            for nx in args.nx:
                name = f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}" + ("_per" if args.periodic else "")
                case = HERE / f"{name}.toml"
                out = ROOT / "runs" / "kinetic" / (name + args.suffix)
                cmd = [args.solver, "-q", "cis-run", str(case), "--out", str(out), "--cells-csv", "--no-vtu",
                       "--print-every", "0", "--tol", f"{args.tol:g}", "--max-iter", str(args.max_iter)]
                cmd += shlex.split(args.extra)
                t0 = time.time()
                res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
                dt = time.time() - t0
                status = "ok" if res.returncode == 0 else f"失敗(終了コード {res.returncode})"
                print(f"{name + args.suffix:45s} {dt:8.1f} s  {status}", flush=True)
                if res.returncode != 0:
                    failed += 1
                    print(res.stderr[-2000:], file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
