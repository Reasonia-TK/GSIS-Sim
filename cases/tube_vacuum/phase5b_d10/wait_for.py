"""ファイルに正規表現の行が現れるまで待つ(作業用の補助。見つかったら 0、時間切れなら 1 で終わる)。

  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/wait_for.py <ファイル> <正規表現> [--timeout 秒]
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("pattern")
    ap.add_argument("--timeout", type=float, default=580.0)
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()
    rx = re.compile(args.pattern)
    t0 = time.time()
    path = Path(args.file)
    while True:
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if rx.search(line):
                    print(line)
                    return 0
        if time.time() - t0 > args.timeout:
            print(f"(時間切れ {args.timeout:.0f} s)")
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
