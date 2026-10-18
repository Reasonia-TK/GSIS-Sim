"""コマンドライン(``gsis-tools``)。

使い方の例(リポジトリのルートから):

    uv run --project python gsis-tools mesh tube --kind hex --target-cells 135000 \\
        --out cases/tube_vacuum/mesh/tube_hex.pmesh --vtu cases/tube_vacuum/mesh/tube_hex.vtu \\
        --report cases/tube_vacuum/mesh/tube_hex_quality.md
    uv run --project python gsis-tools mesh slab --length 1e-3 --nx 100 --out cases/slab/mesh/slab_nx100.pmesh
    uv run --project python gsis-tools mesh-info cases/tube_vacuum/mesh/tube_hex.pmesh
    uv run --project python gsis-tools convert model.msh --out model.pmesh
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger("gsis_tools")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def _outputs(args, mesh, *, title: str, report_kwargs: dict | None = None, cell_data_extra: dict | None = None) -> None:
    """--out / --vtu / --boundary-vtu / --report の書き出しと要約の表示。"""
    from .pmesh import write_pmesh
    from .quality import compute_quality, format_report, summary
    from .vtu import write_boundary_vtu, write_vtu

    t0 = time.perf_counter()
    if getattr(args, "out", None):
        write_pmesh(args.out, mesh)
        print(f"書き出し: {args.out}")
    q = compute_quality(mesh)
    if getattr(args, "vtu", None):
        data = q.cell_data()
        if cell_data_extra:
            data.update(cell_data_extra)
        write_vtu(args.vtu, mesh, data)
        print(f"書き出し: {args.vtu}")
    if getattr(args, "boundary_vtu", None):
        write_boundary_vtu(args.boundary_vtu, mesh)
        print(f"書き出し: {args.boundary_vtu}")
    if getattr(args, "report", None):
        text = format_report(mesh, q, title=title, **(report_kwargs or {}))
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8", newline="\n")
        print(f"書き出し: {args.report}")
    s = summary(mesh, q, scale=(report_kwargs or {}).get("length_scale", 1.0))
    print(mesh.summary())
    print("要素の内訳: " + ", ".join(f"{k} {v}" for k, v in s["cell_types"].items()))
    unit = (report_kwargs or {}).get("length_label", "m")
    print(
        f"セル寸法 h_V = V^(1/3) [{unit}]: 最小 {s['cell_size']['min']:.4g}、平均 {s['cell_size']['mean']:.4g}、"
        f"最大 {s['cell_size']['max']:.4g}(最大辺 {s['cell_max_edge']['max']:.4g})"
    )
    no = s["non_orthogonality"]
    print(f"非直交角 [deg]: 平均 {no['mean']:.2f}、99% {no['p99']:.2f}、最大 {no['max']:.2f}")
    print(f"歪み: 最大 {s['skewness']['max']:.3f}、体積比: 最大 {s['volume_ratio']['max']:.3f}")
    logger.debug("書き出しと品質計算: %.1f s", time.perf_counter() - t0)


# ---------------------------------------------------------------------------


def cmd_mesh_tube(args) -> int:
    from .geometry.tube import TubeDims, TubeHexOptions, TubeTetOptions, generate_tube_hex, generate_tube_tet

    dims = TubeDims(radius=args.radius)
    t0 = time.perf_counter()
    if args.kind == "hex":
        opt = TubeHexOptions()
        over = {k: v for k, v in {
            "n_theta": args.n_theta, "square": args.square, "ds_wall": args.ds_wall, "ds_mouth": args.ds_mouth,
            "h_tube": args.h_tube, "h_max": args.max_size, "growth": args.growth,
        }.items() if v is not None}
        if over:
            from dataclasses import replace

            opt = replace(opt, **over)
        if args.refine:
            opt = opt.refined(args.refine)
        mesh, info = generate_tube_hex(
            dims, opt, target_cells=args.target_cells, renumber=None if args.no_renumber else "rcm", msh_path=args.msh
        )
    else:
        opt = TubeTetOptions(threads=args.threads)
        over = {k: v for k, v in {
            "h_max": args.max_size, "h_tube": args.h_tube, "gradient": args.gradient, "algorithm": args.algorithm,
        }.items() if v is not None}
        if over:
            from dataclasses import replace

            opt = replace(opt, **over)
        mesh, info = generate_tube_tet(
            dims, opt, target_cells=args.target_cells, renumber=None if args.no_renumber else "rcm",
            msh_path=args.msh, verbosity=2 if not args.verbose else 4,
        )
    elapsed = time.perf_counter() - t0
    print(f"管メッシュ({args.kind}): {mesh.n_cells} セル、生成 {elapsed:.1f} s")
    print("設定: " + json.dumps(info["options"], ensure_ascii=False))
    notes = [
        "生成: `gsis-tools " + " ".join(args.argv) + "`",
        f"管半径 R = {dims.radius:g} m。寸法は R 単位で示す",
        "設定: `" + json.dumps(info["options"], ensure_ascii=False) + "`",
    ]
    if args.kind == "hex":
        notes.append(
            "断面のセル数(上流槽 / 管 / 下流槽): "
            + " / ".join(str(v) for v in info["section_cells"].values())
            + "、軸方向の層数: " + " / ".join(str(v) for v in info["layers"].values())
            + f"、リングの半径方向の分割 {info['n_ring']}"
        )
    else:
        notes.append(
            "生成の試行: " + "、".join(f"h_tube = {p['h_tube']:.4f} R → {p['n_cells']} セル({p['time']:.1f} s)" for p in info["passes"])
        )
    g = mesh.geometry()
    report_kwargs = dict(
        length_scale=dims.radius,
        length_label="R",
        regions=dims.regions(g.cell_centres),
        exact_volume=dims.volume(),
        exact_areas=dims.patch_areas(),
        notes=notes,
        elapsed={"生成時間(メッシュ生成 + 変換)": elapsed},
    )
    _outputs(args, mesh, title=f"管メッシュの品質({args.kind})", report_kwargs=report_kwargs)
    return 0


def cmd_mesh_slab(args) -> int:
    from .geometry.slab import generate_slab

    mesh = generate_slab(
        args.length, args.nx, first_cell=args.first_cell, cos_stretch=args.cos_stretch, thickness=args.thickness
    )
    _outputs(args, mesh, title="平板メッシュの品質")
    return 0


def cmd_mesh_info(args) -> int:
    from .pmesh import check_mesh, read_header, read_pmesh

    h = read_header(args.file)
    mesh = read_pmesh(args.file, check=False)
    res = check_mesh(mesh)
    print(f"ファイル: {args.file}")
    print(f"metadata: {json.dumps(h.get('metadata', {}), ensure_ascii=False)}")
    print(res.format())
    if not res.ok:
        return 1
    _outputs(args, mesh, title=f"メッシュ品質: {Path(args.file).name}")
    return 0


def cmd_convert(args) -> int:
    from .gmsh_convert import read_msh

    kinds = {}
    for item in args.patch_kind or []:
        name, _, kind = item.partition("=")
        if not kind:
            raise SystemExit(f"--patch-kind は NAME=KIND の形で指定してください: {item}")
        kinds[name] = kind
    order = [s for s in args.patch_order.split(",") if s] if args.patch_order else None
    mesh = read_msh(args.input, patch_kinds=kinds, patch_order=order, renumber=None if args.no_renumber else "rcm")
    _outputs(args, mesh, title=f"メッシュ品質: {Path(args.input).name}")
    return 0


# ---------------------------------------------------------------------------


def _add_output_args(p: argparse.ArgumentParser, out_required: bool) -> None:
    p.add_argument("--out", required=out_required, help="書き出す .pmesh")
    p.add_argument("--vtu", help="セルの VTU(品質の指標をセルデータとして持つ)")
    p.add_argument("--boundary-vtu", help="境界面の VTU(patch_id を面データとして持つ)")
    p.add_argument("--report", help="品質レポート(Markdown)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gsis-tools", description="GSIS-PBC の前処理ツール(メッシュの生成・変換・検査)")
    ap.add_argument("-v", "--verbose", action="store_true", help="詳しいログを出す")
    sub = ap.add_subparsers(dest="command", required=True)

    mesh = sub.add_parser("mesh", help="メッシュを生成する")
    msub = mesh.add_subparsers(dest="mesh_kind", required=True)

    tube = msub.add_parser("tube", help="管ケース(円管 → 真空)のメッシュ")
    tube.add_argument("--kind", choices=("tet", "hex"), default="hex", help="tet: 非構造の四面体、hex: O 型断面の構造六面体")
    tube.add_argument("--target-cells", type=int, default=None, help="目標セル数(例 135000)")
    tube.add_argument("--radius", type=float, default=1e-3, help="管半径 R [m](既定 1e-3)")
    tube.add_argument("--max-size", type=float, default=None, help="槽の中のセル寸法の上限(R 単位。既定 0.5)")
    tube.add_argument("--h-tube", type=float, default=None, help="hex: 管の中の軸方向の間隔の上限、tet: 管の中のセル寸法 h_V(R 単位)")
    tube.add_argument("--n-theta", type=int, default=None, help="hex: 1/4 周あたりの周方向のセル数(既定 16)")
    tube.add_argument("--square", type=float, default=None, help="hex: 中心の正方形の半幅(R 単位、既定 0.4)")
    tube.add_argument("--ds-wall", type=float, default=None, help="hex: 管壁での半径方向の最初のセル幅(R 単位)")
    tube.add_argument("--ds-mouth", type=float, default=None, help="hex: 管の出入口での軸方向の最初のセル幅(R 単位)")
    tube.add_argument("--growth", type=float, default=None, help="hex: 隣り合うセルの間隔の比の目安(既定 1.2)")
    tube.add_argument("--refine", type=float, default=None, help="hex: 全体を一様に細かくする倍率(メッシュ依存性の確認用)")
    tube.add_argument("--gradient", type=float, default=None, help="tet: 管からの距離に対するセル寸法の増加率(既定 0.15)")
    tube.add_argument("--algorithm", choices=("delaunay", "hxt"), default=None, help="tet: gmsh の 3 次元アルゴリズム")
    tube.add_argument("--threads", type=int, default=8, help="tet: gmsh のスレッド数")
    tube.add_argument("--msh", help="gmsh の .msh も書き出す")
    tube.add_argument("--no-renumber", action="store_true", help="セル番号を逆 Cuthill–McKee 順に付け直さない")
    _add_output_args(tube, out_required=True)
    tube.set_defaults(func=cmd_mesh_tube)

    slab = msub.add_parser("slab", help="平板メッシュ(x 方向に nx セル、y・z は 1 セル)")
    slab.add_argument("--length", type=float, default=1e-3, help="平板の間隔 L [m](既定 1e-3)")
    slab.add_argument("--nx", type=int, default=100, help="x 方向のセル数")
    grading = slab.add_mutually_exclusive_group()
    grading.add_argument("--first-cell", type=float, default=None, help="壁に接するセルの幅 [m](tanh 分布で両壁に向かって細かくする)")
    grading.add_argument("--cos-stretch", type=float, default=None, help="余弦分布の強さ s(0〜1。1 で Chebyshev–Lobatto 点)")
    slab.add_argument("--thickness", type=float, default=None, help="y・z 方向の厚さ [m](既定 L/nx)")
    _add_output_args(slab, out_required=True)
    slab.set_defaults(func=cmd_mesh_slab)

    info = sub.add_parser("mesh-info", help=".pmesh の検査と品質の表示")
    info.add_argument("file", help=".pmesh")
    info.add_argument("--vtu", help="セルの VTU を書き出す")
    info.add_argument("--boundary-vtu", help="境界面の VTU を書き出す")
    info.add_argument("--report", help="品質レポート(Markdown)を書き出す")
    info.set_defaults(func=cmd_mesh_info)

    conv = sub.add_parser("convert", help="gmsh の .msh を .pmesh に変換する")
    conv.add_argument("input", help=".msh")
    conv.add_argument("--patch-kind", action="append", help="パッチの kind(NAME=KIND、繰り返し可)")
    conv.add_argument("--patch-order", help="パッチの並び(カンマ区切りの名前)")
    conv.add_argument("--no-renumber", action="store_true", help="セル番号を付け直さない")
    _add_output_args(conv, out_required=True)
    conv.set_defaults(func=cmd_convert)
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    ap = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(argv)
    args.argv = argv
    _setup_logging(args.verbose)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:  # 想定内のエラーは 1 行で表示する
        from .pmesh import PMeshError

        if isinstance(exc, (PMeshError, FileNotFoundError, ValueError)) and not args.verbose:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
