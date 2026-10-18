"""フェーズ 5b-2: 管(円管 → 真空)の構造六面体メッシュを作る。全体(full)と 1/4 領域(quarter)。

gsis_tools の管の六面体メッシュ(tube_hex_elements)をそのまま使い、1/4 領域では y > 0・z > 0 のセルだけを残して、
y = 0・z = 0 の面を対称面のパッチ sym_y・sym_z にする(n_theta が偶数なら、O 型格子の節点の面が y = 0・z = 0 に乗る)。
軸対称な流れなので 1/4 領域の解は全体の解と同じになるはずで、同じ VRAM で 4 倍のセルを使える(速度格子の細分化にも使う)。

既定の設定は本番のメッシュ cases/tube_vacuum/mesh/tube_hex.pmesh(--target-cells 135000 で決まった値)と同じ。
--equal-area は y・z を一律に拡大して、正 4n_theta 角形の断面積を円と同じにする(多角形近似の影響の確認用)。

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/make_mesh.py --name q_base --quarter
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/make_mesh.py --name q_r150 --quarter --refine 1.5
出力: cases/tube_vacuum/phase5b_d10/mesh/<name>.pmesh と <name>.json(設定・セル数)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from gsis_tools.builder import ELEMENT_FACES, BoundaryGroup, polymesh_from_elements
from gsis_tools.geometry.tube import TubeDims, TubeHexOptions, tube_hex_elements
from gsis_tools.pmesh import check_mesh, write_pmesh

HERE = Path(__file__).resolve().parent

# 本番のメッシュ tube_hex(cases/tube_vacuum/mesh/tube_hex_quality.md の「設定」)
BASE = TubeHexOptions(
    n_theta=16, square=0.4, ds_wall=0.04455722492842637, ds_mouth=0.04455722492842637,
    h_tube=0.37131020773688644, h_max=0.5, growth=1.2,
)


def build(opt: TubeHexOptions, quarter: bool, equal_area: bool, radius: float = 1e-3):
    dims = TubeDims(radius=radius)
    pts, elements, groups, lay = tube_hex_elements(dims, opt)
    pts = pts.copy()
    R = dims.radius
    # 対称面の節点(三角関数の丸めで 1e-16 R 程度ずれる)を厳密に 0 にする
    for k in (1, 2):
        pts[np.abs(pts[:, k]) < 1e-9 * R, k] = 0.0
    if equal_area:
        n_poly = 4 * opt.n_theta
        ratio = n_poly / (2.0 * math.pi) * math.sin(2.0 * math.pi / n_poly)  # 正多角形の面積 / 円の面積
        pts[:, 1:] *= 1.0 / math.sqrt(ratio)
    etype, conn = elements[0]
    assert etype == "hex"
    if not quarter:
        return pts, elements, groups, lay
    cen = pts[conn].mean(axis=1)
    keep = (cen[:, 1] > 0.0) & (cen[:, 2] > 0.0)
    conn_q = conn[keep]
    new_groups = []
    for g in groups:
        faces = []
        for q in g.faces:
            c = pts[q].mean(axis=1)
            faces.append(q[(c[:, 1] > 0.0) & (c[:, 2] > 0.0)])
        new_groups.append(BoundaryGroup(g.name, g.kind, faces))
    # 残したセルの面のうち、y = 0・z = 0 の平面に乗る面を対称面にする
    fl = np.concatenate([conn_q[:, list(f)] for f in ELEMENT_FACES["hex"]])
    on_y = np.all(pts[fl][:, :, 1] == 0.0, axis=1)
    on_z = np.all(pts[fl][:, :, 2] == 0.0, axis=1)
    new_groups.append(BoundaryGroup("sym_y", "symmetry", [fl[on_y]]))
    new_groups.append(BoundaryGroup("sym_z", "symmetry", [fl[on_z]]))
    return pts, [("hex", conn_q)], new_groups, lay


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--quarter", action="store_true", help="1/4 領域(対称面 sym_y・sym_z)")
    ap.add_argument("--refine", type=float, default=None, help="全体を一様に細かくする倍率(TubeHexOptions.refined)")
    ap.add_argument("--n-theta", type=int, default=None)
    ap.add_argument("--ds-wall", type=float, default=None)
    ap.add_argument("--ds-mouth", type=float, default=None)
    ap.add_argument("--h-tube", type=float, default=None)
    ap.add_argument("--h-max", type=float, default=None)
    ap.add_argument("--growth", type=float, default=None)
    ap.add_argument("--equal-area", action="store_true", help="断面積を円と同じにする(y・z を一律に拡大)")
    ap.add_argument("--out-dir", default=str(HERE / "mesh"))
    args = ap.parse_args()

    opt = BASE
    over = {k: v for k, v in {
        "n_theta": args.n_theta, "ds_wall": args.ds_wall, "ds_mouth": args.ds_mouth, "h_tube": args.h_tube,
        "h_max": args.h_max, "growth": args.growth,
    }.items() if v is not None}
    if over:
        opt = replace(opt, **over)
    if args.refine:
        opt = opt.refined(args.refine)
    if args.quarter and opt.n_theta % 2 != 0:
        raise SystemExit("1/4 領域には n_theta が偶数である必要があります")
    t0 = time.perf_counter()
    pts, elements, groups, lay = build(opt, args.quarter, args.equal_area)
    md = {
        "source": "cases/tube_vacuum/phase5b_d10/make_mesh.py",
        "options": repr(asdict(opt)),
        "quarter": str(bool(args.quarter)),
        "equal_area": str(bool(args.equal_area)),
    }
    mesh = polymesh_from_elements(pts, elements, groups, renumber="rcm", metadata=md)
    rep = check_mesh(mesh)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.name}.pmesh"
    write_pmesh(out, mesh)
    g = mesh.geometry()
    R = 1e-3
    vol = float(np.sum(g.cell_volumes)) / R**3
    areas = {}
    for p in mesh.patches:
        fa = np.linalg.norm(g.face_area_vectors[p.start:p.start + p.count], axis=1)
        areas[p.name] = float(fa.sum()) / R**2
    up, inner, down = lay.section_cells()
    info = {
        "name": args.name,
        "pmesh": str(out.relative_to(HERE.parents[2])).replace("\\", "/"),
        "quarter": bool(args.quarter),
        "symmetry_factor": 4 if args.quarter else 1,
        "equal_area": bool(args.equal_area),
        "options": asdict(opt),
        "n_cells": int(mesh.n_cells),
        "n_cells_full_equivalent": int(mesh.n_cells) * (4 if args.quarter else 1),
        "section_cells_full": {"up": up, "tube": inner, "down": down},
        "layers": {"up": int(lay.x_up.size - 1), "tube": int(lay.x_tube.size - 1), "down": int(lay.x_down.size - 1)},
        "n_ring": int(lay.n_ring),
        "volume_R3": vol,
        "patch_area_R2": areas,
        "check_errors": [str(e) for e in getattr(rep, "errors", [])],
        "time_s": time.perf_counter() - t0,
    }
    (out_dir / f"{args.name}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
