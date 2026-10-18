"""フェーズ 5b-2: 管 δ_rp = 10 の質量流量の差(論文の GSIS-PBC 0.326、DSMC 0.330 / 0.32773 に対して本実装 0.3370)の感度調査。

コードは変えず、固定版のソルバー runs/phase5b/bin/gsis-solver-5a.exe で、メッシュ・速度格子・境界条件・マクロの離散化を
1 つずつ変えた計算を流してまとめる。

  make     ケースの TOML を cases/tube_vacuum/phase5b_d10/cases/ に書く(JOBS の全部)
  run      ジョブを順に流す(GPU の順番待ち runs/.gpu_lock を 1 本ごとに取る。summary.json があるジョブは飛ばす)
  summary  結果を cases/tube_vacuum/phase5b_d10/results/summary.csv にまとめ、表を表示する
  list     ジョブの一覧

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/study.py make
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/study.py run q_base q_v05
  uv run --project python --no-sync python cases/tube_vacuum/phase5b_d10/study.py summary
出力: runs/phase5b/d10/<ジョブ>/(gsis-solver run の出力、cells.csv)と runs/phase5b/d10/<ジョブ>.log

1/4 領域のメッシュ(make_mesh.py --quarter)の質量流量は 4 倍して全体の値にする(summary が行う)。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOLVER = ROOT / "runs" / "phase5b" / "bin" / "gsis-solver-5a.exe"
RUNS = ROOT / "runs" / "phase5b" / "d10"
CASES = HERE / "cases"
LOCK = ROOT / "runs" / ".gpu_lock"
REF = ROOT / "reference" / "paper_2610.07658"
DSMC_CL = ROOT / "validation" / "dsmc" / "runs" / "d10_q" / "analysis" / "centerline.csv"

# メッシュ: 名前 → (TOML からの相対パス, 対称の倍率)。q_* は make_mesh.py --quarter の出力
MESHES = {
    "full": ("../../mesh/tube_hex.pmesh", 1),
    "tet": ("../../mesh/tube_tet.pmesh", 1),
}
FULL_CELLS = {"full": 135104, "tet": 133586}
for _p in sorted((HERE / "mesh").glob("*.json")):
    _info = json.loads(_p.read_text(encoding="utf-8"))
    MESHES[_info["name"]] = (f"../mesh/{_info['name']}.pmesh", int(_info["symmetry_factor"]))

# 論文 Table 1・ユーザーの DSMC
PAPER = {1: dict(ref=0.197, gsis=0.198, cis=0.198), 10: dict(ref=0.330, gsis=0.326, cis=0.348),
         100: dict(ref=0.898, gsis=0.885, cis=0.597), 1000: dict(ref=1.267, gsis=1.275, cis=0.729)}
USER_DSMC = {1: 0.19773, 10: 0.32773}
# フェーズ 5a の本番(全体のメッシュ、runs/phase5/gsis/final_d*)
BASE_5A = {1: 0.19820, 10: 0.33698, 100: 0.89202}

VG_BASE = ((30, 24, 24), ((-10.0, 10.0), (-8.0, 8.0), (-8.0, 8.0)))


def vg(nx: int, ny: int, xr: float, yr: float):
    return ((nx, ny, ny), ((-xr, xr), (-yr, yr), (-yr, yr)))


# ジョブの定義。既定はフェーズ 5a の δ = 10 の本番の設定(cases/tube_vacuum/phase5/tube_delta10_p5.toml)
DEFAULT = dict(
    mesh="q_base", delta=10, vgrid=VG_BASE, tangential="zero", mode="gsis",
    kinetic={}, macro={}, gsis={}, init_from=None, max_iter=40, tol=None, extra=[], timeout_min=60, small=False,
)
JOBS: dict[str, dict] = {}


def job(name: str, note: str, **kw):
    d = dict(DEFAULT)
    d.update(kw)
    d["note"] = note
    JOBS[name] = d


# --- 0. 基準 -------------------------------------------------------------------------------------------
job("q_base", "1/4 領域の基準(本番 tube_hex の 1/4、33,776 セル)")
job("full_base", "全体の基準(tube_hex、135,104 セル)。固定版で 5a の値が再現するか", mesh="full", timeout_min=30)
# --- 1. 速度格子 ----------------------------------------------------------------------------------------
job("q_v100", "速度格子 Δξ = 1(20×16×16、範囲は同じ)", vgrid=vg(20, 16, 10.0, 8.0), small=True)
job("q_v080", "速度格子 Δξ = 0.8(25×20×20)", vgrid=vg(25, 20, 10.0, 8.0))
job("q_v050", "速度格子 Δξ = 0.5(40×32×32)", vgrid=vg(40, 32, 10.0, 8.0))
job("q_v040", "速度格子 Δξ = 0.4(50×40×40)", vgrid=vg(50, 40, 10.0, 8.0))
job("q_vwide", "速度格子の範囲を広げる [-12,12]×[-10,10]²(36×30×30、Δξ = 2/3)", vgrid=vg(36, 30, 12.0, 10.0))
job("q_v8cube", "範囲 [-8,8]³ で同じ点数(30×24×24、Δξx = 0.533)", vgrid=vg(30, 24, 8.0, 8.0))
# --- 2. メッシュ ----------------------------------------------------------------------------------------
job("q_r050", "全体を 0.5 倍に粗く(n_theta 8)", mesh="q_r050", small=True)
job("q_r075", "全体を 0.75 倍に粗く(n_theta 12)", mesh="q_r075", small=True)
job("q_r125", "全体を 1.25 倍細かく(n_theta 20)", mesh="q_r125")
job("q_r150", "全体を 1.5 倍細かく(n_theta 24)", mesh="q_r150")
job("q_r175", "全体を 1.75 倍細かく(n_theta 28)", mesh="q_r175", timeout_min=90)
job("q_hmax025", "槽を細かく(h_max 0.5 → 0.25)", mesh="q_hmax025")
job("q_mouth", "管の出入口のまわりを細かく(ds_mouth 0.045 → 0.015)", mesh="q_mouth")
job("q_nth32", "断面(周方向・半径方向)を細かく(n_theta 32)", mesh="q_nth32")
job("full_tet", "全体の四面体メッシュ tube_tet(133,586 セル、管の中は一様 h_V ≈ 0.065)。正値性のリミッタ、K = 1"
    "(フェーズ 2 で K = 3 は下流槽で発散)", mesh="tet", kinetic={"positivity_limiter": True, "limiter_k": 1.0},
    max_iter=25, timeout_min=40)
job("q_eqarea", "断面積を円と同じに(y・z を 1.0008 倍。64 角形の面積不足 0.16% の影響)", mesh="q_eqarea")
# --- 3. 入口の境界条件 -----------------------------------------------------------------------------------
job("q_tanext", "入口の流入の接線速度を外挿(論文の式 (65))", tangential="extrapolate")
job("q_brcell", "PBC の境界面の再構成をセルの値", macro={"boundary_reconstruction": "cell"})
job("q_d095", "δ = 9.5(dlnṀ/dlnδ を求める。入口の圧力の差・粘性の差を Ṁ に換算するため)", delta=9.5)
job("q_d105", "δ = 10.5(同上)", delta=10.5)
# --- 4. GSIS の不動点(マクロの離散化) --------------------------------------------------------------------
job("q_hotcell", "HoT をセル中心で作って面へ補間(論文どおり)", macro={"hot_interpolation": "cell"})
job("q_adv1", "PBC の移流項を 1 次風上", macro={"advection": "upwind1"})
job("q_hotcell_adv1", "HoT セル中心 + 移流項 1 次", macro={"hot_interpolation": "cell", "advection": "upwind1"})
job("q_nofilter", "補正のフィルタなし(論文どおり)", gsis={"filter_length": 0.0})
job("q_negall", "負値の規則を論文どおり置換(all)", kinetic={"gsis_negative_check": "all"})
job("q_freeze05", "凍結の Kn を 1 → 0.5", macro={"gsis_freeze_kn": 0.5})
job("q_freeze2", "凍結の Kn を 1 → 2", macro={"gsis_freeze_kn": 2.0})
job("q_nofreeze", "凍結なし(論文どおり)", macro={"gsis_freeze_kn": 0.0}, max_iter=25)
job("q_paperlike", "論文に近い組: 凍結なし・フィルタなし・負値は置換(all)・HoT セル中心・入口の接線速度は外挿",
    tangential="extrapolate", macro={"gsis_freeze_kn": 0.0, "hot_interpolation": "cell"},
    gsis={"filter_length": 0.0}, kinetic={"gsis_negative_check": "all"}, max_iter=25)
job("q_paperlike1", "q_paperlike + PBC の移流項 1 次(論文の式 (27)(28) のドナーセル)", tangential="extrapolate",
    macro={"gsis_freeze_kn": 0.0, "hot_interpolation": "cell", "advection": "upwind1"},
    gsis={"filter_length": 0.0}, kinetic={"gsis_negative_check": "all"}, max_iter=25)
# --- 2'. 粗い 1/4 領域での先行の確認(VRAM 2 GB 未満。GPU の順番待ちなしで流せる) ---------------------------------------
job("s50_eq", "粗い 0.5 倍のメッシュで断面積を円と同じに(32 角形の面積不足 0.64% の影響)", mesh="q_r050_eq", small=True)
job("s75_eq", "粗い 0.75 倍のメッシュで断面積を円と同じに(48 角形の面積不足 0.29% の影響)", mesh="q_r075_eq", small=True)
job("s75_hotcell", "粗い 0.75 倍のメッシュで HoT をセル中心", mesh="q_r075", macro={"hot_interpolation": "cell"}, small=True)
job("s75_adv1", "粗い 0.75 倍のメッシュで PBC の移流項 1 次", mesh="q_r075", macro={"advection": "upwind1"}, small=True)
job("s75_tanext", "粗い 0.75 倍のメッシュで入口の接線速度を外挿", mesh="q_r075", tangential="extrapolate", small=True)
job("s75_nofreeze", "粗い 0.75 倍のメッシュで凍結なし", mesh="q_r075", macro={"gsis_freeze_kn": 0.0}, max_iter=25, small=True)
job("s75_hotcell_adv1", "粗い 0.75 倍のメッシュで HoT セル中心 + 移流項 1 次", mesh="q_r075",
    macro={"hot_interpolation": "cell", "advection": "upwind1"}, small=True)
job("s75_paperlike", "粗い 0.75 倍のメッシュで論文に近い組(q_paperlike と同じ)", mesh="q_r075", tangential="extrapolate",
    macro={"gsis_freeze_kn": 0.0, "hot_interpolation": "cell"}, gsis={"filter_length": 0.0},
    kinetic={"gsis_negative_check": "all"}, max_iter=25, small=True)
job("s75_paperlike1", "粗い 0.75 倍のメッシュで論文に近い組 + 移流項 1 次(q_paperlike1 と同じ)", mesh="q_r075",
    tangential="extrapolate", macro={"gsis_freeze_kn": 0.0, "hot_interpolation": "cell", "advection": "upwind1"},
    gsis={"filter_length": 0.0}, kinetic={"gsis_negative_check": "all"}, max_iter=25, small=True)
job("s75_nsfinit", "粗い 0.75 倍のメッシュの NSF の初期場を保存する(GSIS 2 回だけ)", mesh="q_r075", max_iter=2,
    extra=["--save-init", "runs/phase5b/d10/s75_nsf_init.csv", "--min-iter", "2"], small=True)
job("s75_cis_paper", "粗い 0.75 倍のメッシュで、論文の CIS に近い比べ方: NSF の初期場から従来の CIS(LU-SGS 1 往復、"
    "ブロックの順序、擬似時間 velocity・CFL 10)を ε < 1e-5 で止める", mesh="q_r075", mode="cis", init_from="runs/phase5b/d10/s75_nsf_init.csv",
    kinetic={"sweep_order": "block", "inner_sweeps": 1, "inner_wall_update": False, "block_passes": 1,
             "positivity_limiter": True, "pseudo_time": "velocity", "pseudo_cfl": 10.0},
    max_iter=5000, tol=1e-5, small=True)  # 平衡分布は保存的な離散平衡(連続の Maxwell 分布では NSF の初期場の出口で 1 回目に負の密度)
job("s75_cis_nsf", "粗い 0.75 倍のメッシュで、NSF の初期場から CIS 単独(フェーズ 5a の強い CIS)を ε < 1e-5 で止める",
    mesh="q_r075", mode="cis", init_from="runs/phase5b/d10/s75_nsf_init.csv", max_iter=3000, tol=1e-5, small=True)
job("s75_cis", "粗い 0.75 倍のメッシュで CIS 単独(s75 の基準 q_r075 の収束解から)", mesh="q_r075", mode="cis",
    init_from="runs/phase5b/d10/q_r075/cells.csv", max_iter=800, tol=1e-7, small=True)
job("s75_d095", "粗い 0.75 倍のメッシュで δ = 9.5(dlnṀ/dlnδ)", mesh="q_r075", delta=9.5, small=True)
job("s75_d105", "粗い 0.75 倍のメッシュで δ = 10.5(dlnṀ/dlnδ)", mesh="q_r075", delta=10.5, small=True)
job("s1_75_base", "δ = 1、粗い 0.75 倍のメッシュの基準", mesh="q_r075", delta=1, max_iter=60, small=True)
job("s1_75_hotcell", "δ = 1、粗い 0.75 倍のメッシュで HoT をセル中心", mesh="q_r075", delta=1,
    macro={"hot_interpolation": "cell"}, max_iter=60, small=True)
job("s1_75_adv1", "δ = 1、粗い 0.75 倍のメッシュで移流項 1 次", mesh="q_r075", delta=1,
    macro={"advection": "upwind1"}, max_iter=60, small=True)
job("s100_75_base", "δ = 100、粗い 0.75 倍のメッシュの基準", mesh="q_r075", delta=100, max_iter=60, small=True)
job("s100_75_hotcell", "δ = 100、粗い 0.75 倍のメッシュで HoT をセル中心", mesh="q_r075", delta=100,
    macro={"hot_interpolation": "cell"}, max_iter=60, small=True)
job("s100_75_adv1", "δ = 100、粗い 0.75 倍のメッシュで移流項 1 次", mesh="q_r075", delta=100,
    macro={"advection": "upwind1"}, max_iter=60, small=True)
job("s1_75_eq", "δ = 1、粗い 0.75 倍のメッシュで断面積を円と同じに", mesh="q_r075_eq", delta=1, max_iter=60, small=True)
job("s100_75_eq", "δ = 100、粗い 0.75 倍のメッシュで断面積を円と同じに", mesh="q_r075_eq", delta=100, max_iter=60,
    small=True)
# --- 6. δ = 1・100 での確認(効いた要因だけ) ------------------------------------------------------------------
job("q1_base", "δ = 1 の 1/4 領域の基準", delta=1, max_iter=60)
job("q100_base", "δ = 100 の 1/4 領域の基準(境界面の再構成はセルの値)", delta=100, max_iter=60)
job("q1_v050", "δ = 1、速度格子 Δξ = 0.5", delta=1, vgrid=vg(40, 32, 10.0, 8.0), max_iter=60)
job("q100_v050", "δ = 100、速度格子 Δξ = 0.5", delta=100, vgrid=vg(40, 32, 10.0, 8.0), max_iter=60)
job("q1_r150", "δ = 1、全体を 1.5 倍細かく", delta=1, mesh="q_r150", max_iter=60)
job("q100_r150", "δ = 100、全体を 1.5 倍細かく", delta=100, mesh="q_r150", max_iter=60)
job("q1_hotcell", "δ = 1、HoT をセル中心", delta=1, macro={"hot_interpolation": "cell"}, max_iter=60)
job("q100_hotcell", "δ = 100、HoT をセル中心", delta=100, macro={"hot_interpolation": "cell"}, max_iter=60)
# --- 5. CIS 単独 ---------------------------------------------------------------------------------------
job("q_cis", "CIS 単独(フェーズ 5a の強い CIS)。q_base の収束解のセル値から", mode="cis",
    init_from="runs/phase5b/d10/q_base/cells.csv", max_iter=600, tol=1e-7, timeout_min=60)


def toml_text(name: str, j: dict) -> str:
    mesh_rel, sym = MESHES[j["mesh"]]
    (nx, ny, nz), ((x0, x1), (y0, y1), (z0, z1)) = j["vgrid"]
    delta = j["delta"]
    kin = dict(
        sweep_blocks=64, sweep_order="octant", inner_sweeps=4, inner_wall_update=True, block_passes=2,
        equilibrium="discrete", gsis_negative_check="blend", positivity_limiter=False, limiter_k=3.0,
    )
    mac = dict(
        advection="upwind2", boundary_reconstruction="cell" if delta >= 100 else "limited", hot_interpolation="face",
        gsis_freeze_kn=1.0, gsis_cfl_start=0.0, init_iterations=50, init_advection="upwind1",
    )
    gs = dict(n_cis=5, max_pbc=10, cfl_pbc=500.0, tolerance=1.0e-5, max_iterations=200, filter_length=1.0)
    kin.update(j["kinetic"])
    mac.update(j["macro"])
    gs.update(j["gsis"])
    if "velocity_chunk" not in kin:
        kin["velocity_chunk"] = velocity_chunk(j)

    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return f'"{v}"'
        if isinstance(v, float):
            return repr(v)
        return str(v)

    lines = [
        f"# フェーズ 5b-2(δ_rp = {delta} の質量流量の感度調査): {j['note']}",
        "# cases/tube_vacuum/phase5b_d10/study.py make で生成。実行は study.py run(固定版 runs/phase5b/bin/gsis-solver-5a.exe)",
        "# 既定はフェーズ 5a の本番の設定(cases/tube_vacuum/phase5/tube_delta10_p5.toml)。"
        + ("1/4 領域(対称面 sym_y・sym_z、Ṁ は 4 倍して比べる)" if sym == 4 else "全体の領域"),
        "",
        "[case]",
        f'name = "{name}"',
        f'mesh = "{mesh_rel}"',
        f'mode = "{j["mode"]}"',
        "",
        "[gas]",
        "molecular_mass = 6.65e-27",
        "mu_ref = 1.916e-5",
        "T_ref = 273.15",
        "omega = 0.66",
        "",
        "[conditions]",
        "L_ref = 1.0e-3",
        "T0 = 273.15",
        f"delta_rp = {delta}",
        "",
        "[[boundary]]",
        'patch = "inlet"',
        'type = "pressure_inlet"',
        "temperature = 273.15",
        f'tangential_velocity = "{j["tangential"]}"',
        "",
        "[[boundary]]",
        'patch = "outlet"',
        'type = "vacuum"',
        "",
        "[[boundary]]",
        'patch = "wall"',
        'type = "diffuse_wall"',
        "temperature = 273.15",
        "",
    ]
    if sym == 4:
        lines += ["[[boundary]]", 'patch = "sym_y"', 'type = "symmetry"', "",
                  "[[boundary]]", 'patch = "sym_z"', 'type = "symmetry"', ""]
    lines += [
        "[velocity_grid]",
        f"n = [{nx}, {ny}, {nz}]",
        f"range = [[{x0}, {x1}], [{y0}, {y1}], [{z0}, {z1}]]",
        "",
        "[gsis]",
        *[f"{k} = {fmt(v)}" for k, v in gs.items()],
        "",
        "[kinetic]",
        *[f"{k} = {fmt(v)}" for k, v in kin.items()],
        "",
        "[macro]",
        *[f"{k} = {fmt(v)}" for k, v in mac.items()],
        "",
    ]
    return "\n".join(lines)


def n_cells_of(j: dict) -> int:
    if j["mesh"] in FULL_CELLS:
        return FULL_CELLS[j["mesh"]]
    info = json.loads((HERE / "mesh" / f"{j['mesh']}.json").read_text(encoding="utf-8"))
    return int(info["n_cells"])


def velocity_chunk(j: dict) -> int:
    """作業領域(1 速度点あたり セル数 × 約 16 B)が約 2.5 GB に収まり、f と合わせて 12 GB 以下になるチャンク幅
    (256 の倍数、速度点の数まで)。全体のメッシュはフェーズ 5a の自動の値(2,304 点)にそろえる。"""
    if j["mesh"] == "full":
        return 2304
    if j.get("small"):
        # 小さな試験(VRAM 2 GB 未満、GPU の順番待ちなし): f と作業領域で 1.6 GB 以下
        (nx, ny, nz), _ = j["vgrid"]
        nv, nc = nx * ny * nz, n_cells_of(j)
        ch = int((1.6e9 - 4.0 * nc * nv) / (nc * 16.03)) // 256 * 256
        if ch < 256:
            raise SystemExit(f"小さな試験に収まりません: {j['note']}")
        return min(ch, (nv + 255) // 256 * 256)
    (nx, ny, nz), _ = j["vgrid"]
    nv = nx * ny * nz
    nc = n_cells_of(j)
    ws = min(2.5e9, 12.0e9 - 4.0 * nc * nv)
    ch = int(ws / (nc * 16.03)) // 256 * 256
    if ch < 2048:
        # 大きなメッシュはスイープの並列度(ブロック数 × チャンク幅)が落ちるので、f と合わせて 13 GB まで広げる
        ch2 = int((13.0e9 - 4.0 * nc * nv) / (nc * 16.03)) // 256 * 256
        ch = max(ch, min(ch2, 2048))
    return max(256, min(ch, (nv + 255) // 256 * 256))


def cmd_make(args) -> int:
    CASES.mkdir(parents=True, exist_ok=True)
    n = 0
    for name, j in JOBS.items():
        if j["mesh"] not in MESHES:
            print(f"(メッシュ {j['mesh']} がまだないので飛ばす: {name})")
            continue
        (CASES / f"{name}.toml").write_text(toml_text(name, j), encoding="utf-8")
        n += 1
    print(f"{n} 件の TOML を {CASES} に書いた")
    return 0


# ---------------------------------------------------------------------------------------------------------
# GPU の順番待ち(CLAUDE.md「GPU の順番待ち」)
# ---------------------------------------------------------------------------------------------------------


def gpu_free_mib() -> float:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30).stdout
        return float(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return -1.0


def wait_gpu_free(min_free_mib: float = 4000.0, poll_s: float = 10.0) -> None:
    """小さな試験の前に、大きな計算(全体の管は約 14 GB)が動いていないことを空き VRAM で確かめる。"""
    waited = False
    while True:
        free = gpu_free_mib()
        if free < 0 or free >= min_free_mib:
            if waited:
                print(f"[{now()}] VRAM の空き {free:.0f} MiB", flush=True)
            return
        if not waited:
            print(f"[{now()}] VRAM の空きが {free:.0f} MiB なので待つ(大きな計算の最中)", flush=True)
            waited = True
        time.sleep(poll_s)


def acquire_lock(owner: str, poll_s: float = 3.0) -> None:
    waited = False
    while True:
        try:
            LOCK.mkdir()
        except FileExistsError:
            if not waited:
                try:
                    who = (LOCK / "owner.txt").read_text(encoding="utf-8").strip().replace("\n", " / ")
                except OSError:
                    who = "(owner.txt なし)"
                print(f"[{now()}] GPU の順番待ち: {who}", flush=True)
                waited = True
            time.sleep(poll_s)
            continue
        (LOCK / "owner.txt").write_text(owner, encoding="utf-8")
        if waited:
            print(f"[{now()}] GPU の順番が来た", flush=True)
        return


def release_lock() -> None:
    shutil.rmtree(LOCK, ignore_errors=True)


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_one(name: str, j: dict) -> int:
    toml = CASES / f"{name}.toml"
    if not toml.exists():
        print(f"{name}: TOML がない(先に make)")
        return 2
    out = RUNS / name
    log = RUNS / f"{name}.log"
    cmd = [str(SOLVER), "run", str(toml.relative_to(ROOT)), "--out", str(out.relative_to(ROOT)), "--cells-csv",
           "--no-vtu", "--max-iter", str(j["max_iter"])]
    if j["tol"] is not None:
        cmd += ["--tol", str(j["tol"])]
    if j["init_from"]:
        init = ROOT / j["init_from"]
        if not init.exists():
            print(f"{name}: 初期値 {init} がない")
            return 2
        cmd += ["--init-from", str(init.relative_to(ROOT))]
    if j["mode"] == "cis":
        cmd += ["--print-every", "20"]
    cmd += list(j["extra"])
    owner = (f"担当: 5b-2(δ=10 の質量流量の感度調査)\n計算: {name}({j['note']})\n"
             f"コマンド: {' '.join(cmd)}\n開始: {now()}\nPID(study.py): {os.getpid()}\n")
    if j.get("small"):
        print(f"[{now()}] {name}: 小さな試験(VRAM 2 GB 未満)なので GPU の順番待ちをしない", flush=True)
        wait_gpu_free()
    else:
        acquire_lock(owner)
    t0 = time.time()
    rc = -1
    try:
        print(f"[{now()}] 開始 {name}: {j['note']}", flush=True)
        with log.open("w", encoding="utf-8") as fh:
            fh.write(f"# {now()} {' '.join(cmd)}\n")
            fh.flush()
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
            try:
                rc = proc.wait(timeout=j["timeout_min"] * 60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                fh.write(f"\n# 時間切れ({j['timeout_min']} 分)で止めた\n")
                rc = -9
            fh.write(f"\nexit {rc}\n")
    finally:
        if not j.get("small"):
            release_lock()
    print(f"[{now()}] 終了 {name}: exit {rc}、{time.time() - t0:.0f} s", flush=True)
    return rc


def cmd_run(args) -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    names = args.names
    for i, name in enumerate(names):
        if name not in JOBS:
            print(f"知らないジョブ: {name}")
            continue
        j = JOBS[name]
        if (RUNS / name / "summary.json").exists() and not args.force:
            print(f"{name}: 済(summary.json あり)")
            continue
        run_one(name, j)
        if i + 1 < len(names):
            time.sleep(args.pause)  # 他の担当が順番を取れるように少し空ける
    return 0


# ---------------------------------------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------------------------------------


def load_centerline_module():
    path = ROOT / "cases" / "tube_vacuum" / "kinetic" / "centerline.py"
    spec = importlib.util.spec_from_file_location("tube_centerline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def centerline_stats(cells: Path, delta: int, cl_mod) -> dict:
    import numpy as np

    cl = cl_mod.centerline(cells)
    out = {}
    for qty, col in (("p", 1), ("u", 2)):
        for kind in ("gsis", "cis"):
            r = cl_mod.compare(cl, REF / f"fig3_{'pressure' if qty == 'p' else 'velocity'}_{kind}_delta{delta}.csv", col)
            if r is not None:
                out[f"{qty}_rms_paper_{kind}"] = r[0]
    if delta == 10 and DSMC_CL.exists():
        d = np.genfromtxt(DSMC_CL, delimiter=",", names=True)
        X = d["x_over_R"]
        keep = (X >= 0.15) & (X <= 9.95)
        for qty, col, dcol in (("p", 1, "p_over_pin"), ("u", 2, "ux_over_sqrt2RT0")):
            model = np.interp(X[keep], cl[:, 0], cl[:, col])
            diff = model - d[dcol][keep]
            out[f"{qty}_rms_dsmc"] = float(np.sqrt(np.mean(diff**2)))
            out[f"{qty}_mean_dsmc"] = float(np.mean(diff))
    for X in (0.0, 5.0, 9.0, 10.0):
        out[f"p_X{X:g}"] = float(np.interp(X, cl[:, 0], cl[:, 1]))
        out[f"u_X{X:g}"] = float(np.interp(X, cl[:, 0], cl[:, 2]))
    return out


def summarize_job(name: str, j: dict | None, cl_mod, with_cl: bool) -> dict | None:
    sp = RUNS / name / "summary.json"
    if not sp.exists():
        return None
    s = json.loads(sp.read_text(encoding="utf-8"))
    mesh_key = j["mesh"] if j else "?"
    sym = MESHES.get(mesh_key, ("", 1))[1] if j else 1
    delta = int(round(float(s.get("delta", 10))))
    mf = s.get("mass_flow") or {}
    mp = s.get("mass_flow_pbc") or {}
    row = {
        "name": name,
        "note": j["note"] if j else "",
        "mode": s.get("mode", "cis"),
        "delta": delta,
        "delta_exact": float(s.get("delta", math.nan)),
        "mesh": mesh_key,
        "cells_full_eq": int(s.get("n_cells", 0)) * sym,
        "n_velocity": s.get("n_velocity"),
        "iterations": s.get("iterations"),
        "cis_iterations": s.get("cis_iterations", s.get("iterations")),
        "converged": s.get("converged"),
        "epsilon": s.get("epsilon"),
        "M_in": sym * mf.get("M_dot_inlet", math.nan),
        "M_out": sym * mf.get("M_dot_outlet", math.nan),
        "M_pbc_in": sym * mp.get("M_dot_inlet", math.nan) if mp else math.nan,
        "M_pbc_out": sym * mp.get("M_dot_outlet", math.nan) if mp else math.nan,
        "wall_time_s": s.get("wall_time_s"),
        "init_time_s": (s.get("initialization") or {}).get("time_s"),
        "time_per_iter_s": s.get("time_per_iteration_s"),
        "replaced_total": (s.get("negative_replacement") or {}).get("total_cells"),
        "replaced_last": (s.get("negative_replacement") or {}).get("last_iteration"),
        "pbc_change": s.get("pbc_change"),
        "memory_GB": (s.get("memory_bytes") or 0) / 1e9,
    }
    m = row["M_in"]
    if delta in PAPER:
        row["diff_paper_gsis_pct"] = 100.0 * (m / PAPER[delta]["gsis"] - 1.0)
        row["diff_paper_ref_pct"] = 100.0 * (m / PAPER[delta]["ref"] - 1.0)
    if delta in USER_DSMC:
        row["diff_user_dsmc_pct"] = 100.0 * (m / USER_DSMC[delta] - 1.0)
    if delta in BASE_5A:
        row["diff_5a_pct"] = 100.0 * (m / BASE_5A[delta] - 1.0)
    cells = RUNS / name / "cells.csv"
    if with_cl and cells.exists():
        cache = RUNS / name / "centerline_stats.json"
        if cache.exists() and cache.stat().st_mtime >= cells.stat().st_mtime:
            st = json.loads(cache.read_text(encoding="utf-8"))
        else:
            st = centerline_stats(cells, delta, cl_mod)
            cache.write_text(json.dumps(st, indent=1), encoding="utf-8")
        row.update(st)
    return row


def cmd_summary(args) -> int:
    cl_mod = load_centerline_module()
    names = args.names or [p.name for p in sorted(RUNS.iterdir()) if p.is_dir() and (p / "summary.json").exists()]
    rows = [r for r in (summarize_job(n, JOBS.get(n), cl_mod, not args.no_centerline) for n in names) if r]
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    out = HERE / "results"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    print(f"{'ジョブ':18s} {'δ':>4s} {'セル':>7s} {'速度点':>6s} {'反復':>4s} {'CIS':>4s} {'ε':>8s} {'Ṁ入口':>8s} {'Ṁ出口':>8s} "
          f"{'5a比%':>6s} {'論文%':>6s} {'DSMC%':>6s} {'時間s':>6s} {'p_DSMC':>7s}")
    for r in rows:
        print(f"{r['name']:18s} {r['delta']:4d} {r['cells_full_eq']:7d} {r['n_velocity']!s:>6s} {r['iterations']!s:>4s} "
              f"{r['cis_iterations']!s:>4s} {r['epsilon']:8.1e} {r['M_in']:8.5f} {r['M_out']:8.5f} "
              f"{r.get('diff_5a_pct', math.nan):+6.2f} {r.get('diff_paper_gsis_pct', math.nan):+6.2f} "
              f"{r.get('diff_user_dsmc_pct', math.nan):+6.2f} {r['wall_time_s']:6.0f} {r.get('p_mean_dsmc', math.nan):+7.4f}")
    print(f"書き出し: {out / 'summary.csv'}")
    return 0


def cmd_list(args) -> int:
    for name, j in JOBS.items():
        ok = "済" if (RUNS / name / "summary.json").exists() else "  "
        print(f"{ok} {name:18s} δ={j['delta']:<5d} {j['mesh']:10s} {j['note']}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make")
    p = sub.add_parser("run")
    p.add_argument("names", nargs="+")
    p.add_argument("--force", action="store_true")
    p.add_argument("--pause", type=float, default=40.0)
    p = sub.add_parser("summary")
    p.add_argument("names", nargs="*")
    p.add_argument("--no-centerline", action="store_true")
    sub.add_parser("list")
    args = ap.parse_args()
    return {"make": cmd_make, "run": cmd_run, "summary": cmd_summary, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
