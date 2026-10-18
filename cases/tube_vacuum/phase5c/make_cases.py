"""フェーズ 5c: 管(円管 → 真空)の 4 条件の最終のケースを作る。

フェーズ 5b-1 の確認用ケース(cases/tube_vacuum/phase5b_d1000/verify_d{1,10,100}.toml、tube_delta1000_p5b.toml)と同じ設定に、
フェーズ 5c の診断(断面の質量流量 [output].mass_flow_sections)を足したもの。CIS の設定はコードの既定(フェーズ 5b で既定にした
強化した CIS)で、移流項は 2 次(フェーズ 5c でコードの既定にしたが、明示しておく)。

実行(リポジトリのルートから):
  uv run --project python --no-sync python cases/tube_vacuum/phase5c/make_cases.py
出力: cases/tube_vacuum/phase5c/tube_d{1,10,100,1000}_p5c.toml
GSIS:     gsis-solver run cases/tube_vacuum/phase5c/tube_d10_p5c.toml --out runs/phase5c/gsis/d10 --cells-csv
CIS 単独: gsis-solver run cases/tube_vacuum/phase5c/tube_d10_p5c.toml --mode cis --out runs/phase5c/cis/d10 --cells-csv
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

HEADER = """# 円管 → 真空(論文 §4.1)の GSIS-PBC(δ_rp = {delta})。フェーズ 5c の最終のケース(make_cases.py で生成)。
# メッシュは構造六面体 tube_hex(135,104 セル)、速度格子 30 × 24 × 24(論文と同じ)。
# 論文: GSIS 1 回 = CIS 5 回 + PBC 10 回、CFL = 500、式 (59) の ε < 1e-5(Table 1)。
# CIS はコードの既定(フェーズ 5b): octant の順序、内部反復 4 回 + 壁の更新、ブロック間の反復 2 回、保存的な離散平衡、負値は blend。
# 論文との違い(spec §3.7、ユーザーの判断 2026-10-17 で採用): 局所 Kn > 1 の凍結、補正の低域フィルタ ℓ = λ、入口の流入の
# 接線速度 0、面の HoT、2 次の移流項{extra_note}
# フェーズ 5c の診断: 管の断面 X = 0, 2.5, 5, 7.5, 10(メッシュの x = 8〜18)の質量流量と、入口・出口の質量の釣り合い(許容 0.2%、超えたら警告)
"""

COMMON = """
[case]
name = "tube_d{delta}_p5c"
mesh = "../mesh/tube_hex.pmesh"
mode = "gsis"

[gas]
molecular_mass = 6.65e-27           # kg (He)
mu_ref = 1.916e-5                   # Pa s
T_ref = 273.15                      # K
omega = 0.66

[conditions]
L_ref = 1.0e-3                      # m(管半径)
T0 = 273.15                         # K
delta_rp = {delta}

[[boundary]]
patch = "inlet"
type = "pressure_inlet"
temperature = 273.15
tangential_velocity = "zero"

[[boundary]]
patch = "outlet"
type = "vacuum"

[[boundary]]
patch = "wall"
type = "diffuse_wall"
temperature = 273.15

[velocity_grid]
n = [30, 24, 24]
range = [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]]   # 単位 sqrt(R*T0)

[gsis]
n_cis = 5
max_pbc = 10
cfl_pbc = 500.0
tolerance = 1.0e-5
max_iterations = 200
filter_length = 1.0
mass_balance_tolerance = 2.0e-3

[output]
mass_flow_sections = "8, 10.5, 13, 15.5, 18"   # 管の X = x − 8 = 0, 2.5, 5, 7.5, 10(メッシュの x は上流槽の入口が 0)
"""

KINETIC = {
    "default": """
[kinetic]
sweep_blocks = 64
positivity_limiter = false
limiter_k = 3.0
max_iterations = 4000
""",
    "d1000": """
[kinetic]
sweep_blocks = 64
positivity_limiter = true
limiter_k = 1.0
cold_bgk_temperature = 0.1
max_iterations = 4000
""",
}

MACRO = """
[macro]
advection = "upwind2"
boundary_reconstruction = "{brec}"
hot_interpolation = "face"
gsis_face_state = "{fstate}"
gsis_freeze_kn = 1.0
gsis_cfl_start = {cfl_start}
init_iterations = 50
init_advection = "upwind1"
"""

CASES = {
    1: dict(brec="limited", fstate="kinetic", cfl_start="0.0", kinetic="default", extra_note=""),
    10: dict(brec="limited", fstate="kinetic", cfl_start="0.0", kinetic="default", extra_note=""),
    100: dict(brec="cell", fstate="kinetic", cfl_start="0.0", kinetic="default",
              extra_note="、境界面の再構成はセルの値(δ = 100)"),
    1000: dict(brec="cell", fstate="cell", cfl_start="5.0", kinetic="d1000",
               extra_note="。δ = 1000 だけの設定(フェーズ 5a・5b-1、未解決): 境界面の再構成と GSIS の段の境界セルの勾配の\n"
                          "# 境界値はセルの値、PBC 段の CFL を 5 から立ち上げ、運動論のリミッタ K = 1 と正値の制限、冷たいセル(T < 0.1)の\n"
                          "# 目標分布は BGK"),
}


# δ = 100 のメッシュ依存性(1/4 領域、対称面 sym_y・sym_z。フェーズ 5c): (名前, メッシュ(このフォルダから), 説明)
QUARTER_D100 = [
    ("q_base", "../phase5b_d10/mesh/q_base.pmesh", "本番のメッシュの 1/4"),
    ("q_r150", "../phase5b_d10/mesh/q_r150.pmesh", "全体を 1.5 倍細かく"),
    ("q_dsw025", "mesh/q_dsw025.pmesh", "壁の最初のセルだけ 1/4"),
]

SYM_BOUNDARIES = """
[[boundary]]
patch = "sym_y"
type = "symmetry"

[[boundary]]
patch = "sym_z"
type = "symmetry"
"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for delta, c in CASES.items():
        text = HEADER.format(delta=delta, extra_note=c["extra_note"])
        text += COMMON.format(delta=delta)
        text += KINETIC[c["kinetic"]]
        text += MACRO.format(brec=c["brec"], fstate=c["fstate"], cfl_start=c["cfl_start"])
        out = HERE / f"tube_d{delta}_p5c.toml"
        out.write_text(text, encoding="utf-8", newline="\n")
        print(out)
    # δ = 100 の 1/4 領域(Ṁ は summary.json の値の 4 倍。sweep_blocks は自動)
    c = CASES[100]
    for name, mesh, note in QUARTER_D100:
        text = HEADER.format(delta=100, extra_note=c["extra_note"])
        text += f"# 1/4 領域のメッシュ依存性の確認: {note}({mesh})。Ṁ は 4 倍して全体の値にする\n"
        common = COMMON.format(delta=100).replace('name = "tube_d100_p5c"', f'name = "tube_d100_{name}"')
        common = common.replace('mesh = "../mesh/tube_hex.pmesh"', f'mesh = "{mesh}"')
        common = common.replace('[velocity_grid]', SYM_BOUNDARIES.lstrip("\n") + "\n[velocity_grid]")
        text += common
        text += KINETIC["default"].replace("sweep_blocks = 64\n", "")
        text += MACRO.format(brec=c["brec"], fstate=c["fstate"], cfl_start=c["cfl_start"])
        out = HERE / f"tube_d100_{name}.toml"
        out.write_text(text, encoding="utf-8", newline="\n")
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
