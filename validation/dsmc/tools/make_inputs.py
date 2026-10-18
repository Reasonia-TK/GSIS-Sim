"""管→真空ケースの DSMC 入力(AMReX ParmParse 形式)と派生量(params.json)を作る。

ケースは CASES に定義する。長さは管半径 R、時間は t_ref = R / sqrt(2 R_g T0) を単位にする。
He の VHS 直径 d_ref は、論文の粘性 μ0 = 1.916e-5 Pa·s(T0 = 273.15 K)、ω = 0.66、
m = 6.65e-27 kg に合うように Bird (1994) 式 (4.62) から決める。

    μ_ref = 15 sqrt(m k T_ref / π) / (2 (5 - 2ω)(7 - 2ω) d_ref²)

出力(ケースごと): validation/dsmc/tube_vacuum/cases/<case>/inputs.tube, params.json

使い方(リポジトリのルートから):
    uv run --project python python validation/dsmc/tools/make_inputs.py            # 全ケース
    uv run --project python python validation/dsmc/tools/make_inputs.py smoke_d1   # 指定ケース
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_cut_cells import count_unresolved  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]          # validation/dsmc
CASE_DIR = ROOT / "tube_vacuum" / "cases"
GEOMETRY = ROOT / "tube_vacuum" / "geometry" / "tube_vacuum_n128.stl"

# ---- 物理定数・論文の条件(§4.1)
KB = 1.380649e-23            # J/K(DSMC コードの Constants.H と同じ値)
MASS = 6.65e-27              # kg(He)
T0 = 273.15                  # K
MU0 = 1.916e-5               # Pa s @ T0
OMEGA = 0.66
L_REF = 1.0e-3               # m(管半径 R。結果は無次元で評価するので任意)

# 論文 Table 1(参照値: δ=1,10,100 は DSMC、δ=1000 は NS)
TABLE1 = {
    1: {"reference": 0.197, "gsis_pbc": 0.198, "cis": 0.198},
    10: {"reference": 0.330, "gsis_pbc": 0.326, "cis": 0.348},
    100: {"reference": 0.898, "gsis_pbc": 0.885, "cis": 0.597},
    1000: {"reference": 1.267, "gsis_pbc": 1.275, "cis": 0.729},
}

# ---- 計算条件
#   dx, dt は R, t_ref 単位。ppc は入口状態(n_in)でのセルあたり粒子数。
#   steps は全ステップ数、block はサンプリングのブロック長(sample_reset = 1 の出力間隔)。
#   max_grid_size: 入口面に接するボックスが多いと流入処理が遅いので、本計算は 128(実測は docs/dsmc_survey.md)。
CASES = {
    # 試運転: δ=1、粗い格子、短時間
    "smoke_d1": dict(delta=1, symmetry="quarter", dx=0.25, ppc=10, dt=0.08, steps=3000, block=250,
                     subcell="none", chk_int=0, max_grid_size=32,
                     note="試運転(粗い格子・短時間)。流入・流出・壁の動作確認と 1 ステップの時間の測定"),
    # δ=1 本計算(1/4 領域)
    "d1_q": dict(delta=1, symmetry="quarter", dx=0.125, ppc=16, dt=0.05, steps=8000, block=400,
                 subcell="none", chk_int=8000, max_grid_size=128,
                 note="δ=1 の本計算(1/4 領域、Δx = R/8)"),
    # δ=1 全領域(対称面を使わない確認用。任意)
    "d1_full": dict(delta=1, symmetry="full", dx=0.125, ppc=16, dt=0.05, steps=8000, block=400,
                    subcell="none", chk_int=8000, max_grid_size=128,
                    note="δ=1 の全領域計算(1/4 領域の対称面の妥当性確認用、任意)"),
    # δ=10 本計算(1/4 領域、仮想サブセル)
    "d10_q": dict(delta=10, symmetry="quarter", dx=0.1, ppc=28, dt=0.02, steps=18000, block=1000,
                  subcell="nearest", chk_int=6000, max_grid_size=128,
                  note="δ=10 の本計算(1/4 領域、Δx = R/10、仮想サブセル)"),
    # ---- 感度の確認(任意)。本計算と比べて DSMC の数値誤差の大きさを見る
    # δ=1: セル幅 2 倍(Fnum は d1_q と同じになるよう ppc を 8 倍)
    "d1_q_coarse": dict(delta=1, symmetry="quarter", dx=0.25, ppc=128, dt=0.05, steps=8000, block=400,
                        subcell="none", chk_int=8000, max_grid_size=128,
                        note="δ=1 の格子感度(Δx = R/4、Fnum は d1_q と同じ)"),
    # δ=1: 時間刻み 1/2
    "d1_q_dt": dict(delta=1, symmetry="quarter", dx=0.125, ppc=16, dt=0.025, steps=16000, block=800,
                    subcell="none", chk_int=16000, max_grid_size=128,
                    note="δ=1 の時間刻み感度(Δt = 0.025 t_ref)"),
    # δ=10: 粒子数 1/2(仮想サブセルの MCS/λ が約 0.29 に増える)
    "d10_q_ppc14": dict(delta=10, symmetry="quarter", dx=0.1, ppc=14, dt=0.02, steps=18000, block=1000,
                        subcell="nearest", chk_int=6000, max_grid_size=128,
                        note="δ=10 の粒子数感度(ppc 14、MCS/λ ≈ 0.29)"),
    # δ=10: 時間刻み 1/2
    "d10_q_dt": dict(delta=10, symmetry="quarter", dx=0.1, ppc=28, dt=0.01, steps=30000, block=2000,
                     subcell="nearest", chk_int=10000, max_grid_size=128,
                     note="δ=10 の時間刻み感度(Δt = 0.01 t_ref)"),
    # δ=10 の時間・メモリ測定(本計算と同じ設定で数十ステップだけ)
    "d10_probe": dict(delta=10, symmetry="quarter", dx=0.1, ppc=28, dt=0.02, steps=40, block=0,
                      subcell="nearest", chk_int=0, max_grid_size=128,
                      note="δ=10 本計算と同じ設定で 40 ステップだけ動かし、1 ステップの時間と GPU メモリを測る"),
}


N_THETA = 128               # tube_geometry.py の既定(等面積の正 128 角形)
VOLUME_SAMPLES = 16         # surfaces.volume_samples


def choose_cross_cell(dx: float, max_shift: float = 0.03) -> tuple[float, float]:
    """y, z 方向のセル幅を Δx の近くで選ぶ。

    R/Δx が整数だと、円(r = 1, 4, 8)が格子点のごく近くを通り、体積率の中点積分で拾えない
    微小な流体部分(未解像セル)ができる。DSMC コードはそこに粒子が入ると停止するので、
    未解像セルが 0 で、多角形の半径を ±1e-6・±1e-5 だけ変えても 0 のままの幅を、Δx に近い順に探す。
    """
    scale = math.sqrt(2.0 * math.pi / (N_THETA * math.sin(2.0 * math.pi / N_THETA)))
    for i in range(int(max_shift / 0.0005) + 1):
        for sign in ((1,) if i == 0 else (1, -1)):
            shift = sign * i * 0.0005
            dy = dx * (1.0 + shift)
            ok = True
            for f in (1.0, 1.0 - 1e-6, 1.0 + 1e-6, 1.0 - 1e-5, 1.0 + 1e-5):
                r = count_unresolved(dy, dy, VOLUME_SAMPLES, N_THETA, scale * f)
                if sum(v["unresolved"] for v in r.values()):
                    ok = False
                    break
            if ok:
                return dy, shift
    raise RuntimeError(f"Δx = {dx} の近くで未解像セルのない y, z のセル幅が見つかりません")


def gas_properties() -> dict:
    c_m = math.sqrt(2.0 * KB * T0 / MASS)             # sqrt(2 R_g T0)
    cbar = math.sqrt(8.0 * KB * T0 / (math.pi * MASS))
    d_ref = math.sqrt(15.0 * math.sqrt(MASS * KB * T0 / math.pi)
                      / (2.0 * (5.0 - 2.0 * OMEGA) * (7.0 - 2.0 * OMEGA) * MU0))
    # 組み込みの He(Bird 1994: d = 2.33e-10 m @ 273 K)の粘性
    d_b, t_b = 2.33e-10, 273.0
    mu_builtin = (15.0 * math.sqrt(MASS * KB * t_b / math.pi)
                  / (2.0 * (5.0 - 2.0 * OMEGA) * (7.0 - 2.0 * OMEGA) * d_b**2)) * (T0 / t_b) ** OMEGA
    return dict(c_m=c_m, cbar=cbar, d_ref=d_ref, mu_builtin_at_T0=mu_builtin)


def derive(name: str, case: dict) -> dict:
    g = gas_properties()
    delta = case["delta"]
    p_in = delta * MU0 * g["c_m"] / L_REF
    n_in = p_in / (KB * T0)
    lam = 1.0 / (math.sqrt(2.0) * math.pi * g["d_ref"] ** 2 * n_in)        # Bird 式 (4.65)、T = T_ref
    nu = 4.0 * g["d_ref"] ** 2 * n_in * math.sqrt(math.pi * KB * T0 / MASS)  # Bird 式 (4.64)
    tau = 1.0 / nu
    t_ref = L_REF / g["c_m"]

    dx = case["dx"] * L_REF
    dt = case["dt"] * t_ref
    # 計算領域: x ∈ [-8R, 14R](x = 0, 10R の壁はセル境界に一致)。
    # y, z のセル幅 dy は未解像セルが出ないよう Δx から少しずらし、槽(半径 8R)より 1.5 セル以上広くする
    nx = round(22.0 / case["dx"])
    dy_r, dy_shift = choose_cross_cell(case["dx"])
    ny_half = math.ceil((8.0 * 1.0002008339103527 + 1.5 * dy_r) / dy_r)
    ly = ny_half * dy_r
    if case["symmetry"] == "quarter":
        lo = [-8.0, 0.0, 0.0]
        hi = [14.0, ly, ly]
        ncell = [nx, ny_half, ny_half]
        sym_factor = 4
    elif case["symmetry"] == "full":
        lo = [-8.0, -ly, -ly]
        hi = [14.0, ly, ly]
        ncell = [nx, 2 * ny_half, 2 * ny_half]
        sym_factor = 1
    else:
        raise ValueError(case["symmetry"])
    if abs(nx * case["dx"] - 22.0) > 1e-9:
        raise ValueError(f"{name}: Δx が 22R を割り切りません")

    v_cell = dx * (dy_r * L_REF) ** 2
    fnum = n_in * v_cell / case["ppc"]
    gas_volume = 4.0 * 460.24332375090455 / sym_factor * L_REF**3   # tube_geometry.py の値(領域内)
    particles_initial = n_in * gas_volume / fnum
    mcs_random = 0.6 * dx  # セル内ランダム選択の平均衝突間隔の目安(一様分布の 2 点間距離 ≈ 0.66Δx)
    mcs_nearest = 0.554 * (v_cell / case["ppc"]) ** (1.0 / 3.0)
    mcs = mcs_nearest if case["subcell"] == "nearest" else mcs_random
    mdot_ref = math.sqrt(math.pi) * L_REF**2 * p_in / g["c_m"]   # 論文の無次元化の分母 [kg/s]

    return {
        "case": name,
        "note": case["note"],
        "delta_rp": delta,
        "Kn_inlet_paper": math.sqrt(math.pi) / (2.0 * delta),
        "symmetry": case["symmetry"],
        "symmetry_factor": sym_factor,
        "L_ref_m": L_REF,
        "T0_K": T0,
        "gas": {
            "species": "He", "mass_kg": MASS, "mu0_Pa_s": MU0, "T_ref_K": T0, "omega": OMEGA,
            "d_ref_m": g["d_ref"], "model": "VHS",
            "builtin_He": {"d_ref_m": 2.33e-10, "T_ref_K": 273.0,
                           "mu_at_T0_Pa_s": g["mu_builtin_at_T0"],
                           "mu_ratio_paper_over_builtin": MU0 / g["mu_builtin_at_T0"]},
        },
        "p_in_Pa": p_in,
        "n_in_m3": n_in,
        "rho_in_kg_m3": n_in * MASS,
        "c_m_m_s": g["c_m"],
        "cbar_m_s": g["cbar"],
        "t_ref_s": t_ref,
        "lambda_in_m": lam,
        "lambda_in_over_R": lam / L_REF,
        "tau_in_s": tau,
        "mdot_ref_kg_s": mdot_ref,
        "grid": {
            "dx_over_R": case["dx"], "dx_m": dx,
            "dy_over_R": dy_r, "dy_relative_shift": dy_shift,
            "dy_note": "y, z のセル幅。未解像の切断セルを避けるため Δx から少しずらした値(check_cut_cells.py)",
            "n_cell": ncell,
            "prob_lo_over_R": lo, "prob_hi_over_R": hi,
            "prob_lo_m": [v * L_REF for v in lo], "prob_hi_m": [v * L_REF for v in hi],
            "max_grid_size": case["max_grid_size"], "total_cells": ncell[0] * ncell[1] * ncell[2],
            "gas_cells": gas_volume / v_cell,
        },
        "dsmc": {
            "ppc_at_inlet_state": case["ppc"], "fnum": fnum, "dt_s": dt, "dt_over_tref": case["dt"],
            "subcell": case["subcell"], "max_step": case["steps"], "plot_int": case["block"],
            "chk_int": case["chk_int"], "physical_time_over_tref": case["steps"] * case["dt"],
        },
        "estimates": {
            "particles_initial": particles_initial,
            "lambda_in_over_dx": lam / dx,
            "tau_in_over_dt": tau / dt,
            "cbar_dt_over_dx": g["cbar"] * dt / dx,
            "mcs_over_lambda_in": mcs / lam,
        },
        "paper_table1": TABLE1.get(delta),
    }


def write_inputs(path: Path, p: dict, seed: int) -> None:
    g = p["grid"]
    d = p["dsmc"]
    lines = [
        f"# 管→真空ケース(arXiv:2610.07658 §4.1)  case = {p['case']}",
        f"# {p['note']}",
        f"# δ_rp = {p['delta_rp']}, Kn(入口, 論文の定義) = {p['Kn_inlet_paper']:.4g}, "
        f"p_in = {p['p_in_Pa']:.6g} Pa, T0 = {p['T0_K']} K, R = {p['L_ref_m']} m",
        f"# λ_in(VHS) = {p['lambda_in_over_R']:.4g} R, Δx = {g['dx_over_R']} R, "
        f"Δt = {d['dt_over_tref']} R/sqrt(2RT0)",
        "# make_inputs.py が生成。手で編集せず make_inputs.py の CASES を変更して再生成する",
        "",
        "# ---- 計算領域(管軸 = x 軸。入口端面 x=-8R、出口端面 x=14R)",
        "geometry.prob_lo = " + " ".join(f"{v:.10g}" for v in g["prob_lo_m"]),
        "geometry.prob_hi = " + " ".join(f"{v:.10g}" for v in g["prob_hi_m"]),
        "amr.n_cell = " + " ".join(str(v) for v in g["n_cell"]),
        f"amr.max_grid_size = {g['max_grid_size']}",
        "",
        "# ---- ガス: He, VHS。d_ref は論文の μ0 = 1.916e-5 Pa s (273.15 K), ω = 0.66 に合わせた値",
        "gas.species = He",
        f"species.He.mass = {MASS:.10g}",
        f"species.He.d_ref = {p['gas']['d_ref_m']:.10g}",
        f"species.He.T_ref = {T0}",
        f"species.He.omega = {OMEGA}",
        "dsmc.collision_model = VHS",
        "# 初期状態: 全域を入口の槽状態(静止)にする",
        f"gas.n = {p['n_in_m3']:.10g}",
        f"gas.T = {T0}",
        "gas.velocity = 0 0 0",
        "",
        "# ---- DSMC",
        f"dsmc.ppc = {d['ppc_at_inlet_state']}",
        f"dsmc.dt = {d['dt_s']:.10g}",
        f"dsmc.max_step = {d['max_step']}",
        f"dsmc.subcell = {d['subcell']}",
        f"dsmc.seed = {seed}",
        "",
        "# ---- 境界: 入口端面 = 槽状態からの流入(到達した粒子は吸収)、出口端面 = 真空",
        "bc.xlo.type = inflow",
        f"bc.xlo.n = {p['n_in_m3']:.10g}",
        f"bc.xlo.T = {T0}",
        "bc.xlo.velocity = 0 0 0",
        "bc.xhi.type = outflow",
    ]
    if p["symmetry"] == "quarter":
        lines += [
            "# 1/4 領域: y=0 と z=0 を対称面にする(管軸は両面の交線)",
            "bc.ylo.type = symmetry",
            "bc.zlo.type = symmetry",
        ]
    else:
        lines += [
            "# 全領域: y, z の下端は槽の外側(固体中)で、粒子は届かない",
            "bc.ylo.type = specular",
            "bc.zlo.type = specular",
        ]
    lines += [
        "# y, z の上端は槽の外側(固体中)で、粒子は届かない",
        "bc.yhi.type = specular",
        "bc.zhi.type = specular",
        "",
        "# ---- 形状: 流体領域を囲む閉じた STL(内部が流体)。壁は完全拡散 (T0)",
        "surfaces.n = 1",
        "surfaces.volume_samples = 16",
        "surfaces.max_bounces = 128",
        f"surface0.kind = stl",
        f"surface0.file = {GEOMETRY.name}",
        f"surface0.scale = {p['L_ref_m']:.10g}",
        "surface0.fluid_inside = 1",
        "surface0.type = diffuse",
        f"surface0.T = {T0}",
        "",
        "# ---- 出力: ブロック平均(plot_int ステップごとに出力し、累積をリセット)",
        f"output.plot_int = {d['plot_int']}",
        "output.plot_prefix = plt",
        "output.write_particles = 0",
        "output.sample_start = 1",
        "output.sample_reset = 1",
        "output.profile_axis = 0",
        f"output.diag_int = {max(10, min(200, d['max_step'] // 40)) if d['max_step'] >= 400 else 10}",
        f"output.chk_int = {d['chk_int'] if d['chk_int'] > 0 else -1}",
        "output.chk_prefix = chk",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", help="作るケース(省略時は全ケース)")
    args = ap.parse_args(argv)
    names = args.cases or list(CASES)
    for i, name in enumerate(names):
        if name not in CASES:
            ap.error(f"未知のケース: {name}(候補: {', '.join(CASES)})")
        p = derive(name, CASES[name])
        out = CASE_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        write_inputs(out / "inputs.tube", p, seed=20261015 + list(CASES).index(name))
        (out / "params.json").write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
        e = p["estimates"]
        # コンソールが CP932 でも表示できるよう ASCII だけで出力する
        print(f"{name:10s} delta={p['delta_rp']:<4} cells={p['grid']['total_cells']:>9,d} "
              f"gas_cells={p['grid']['gas_cells']:>9,.0f} particles~{e['particles_initial']:>12,.0f} "
              f"lambda/dx={e['lambda_in_over_dx']:.3f} tau/dt={e['tau_in_over_dt']:.2f} "
              f"cbar*dt/dx={e['cbar_dt_over_dx']:.3f} MCS/lambda~{e['mcs_over_lambda_in']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
