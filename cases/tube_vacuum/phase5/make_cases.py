"""フェーズ 5a の管(円管 → 真空、論文 §4.1)の GSIS-PBC のケースを作る(tube_delta{1,10,100,1000}_p5.toml)。

既定はフェーズ 5a で決めた設定(CIS の強化: スイープの順序 octant・内部反復・壁の更新、保存的な離散平衡、
論文どおりフィルタと凍結なし)。比較の計算は gsis-solver run の --set で上書きする。
実行(リポジトリのルートから): uv run --project verification/ref1d python cases/tube_vacuum/phase5/make_cases.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

TEMPLATE = """# 円管 → 真空(論文 §4.1)の GSIS-PBC(δ_rp = {delta:g})。メッシュは構造六面体 tube_hex(135,104 セル)。
# cases/tube_vacuum/phase5/make_cases.py で生成。実行: gsis-solver run <この TOML>(出力は runs/gsis/<ケース名>/)
# 論文: GSIS 1 回 = CIS 5 回 + PBC 10 回、CFL = 500、式 (59) の ε < 1e-5(Table 1)。
# フェーズ 5a の設定(docs/phase5_report.md):
#   CIS: 速度点の向きごとのスイープ順序(octant)、内部反復 {inner} 回(間で拡散壁の流入密度を更新)、ブロック間の反復 2 回、
#        保存的な離散平衡分布、負値は blend
#   論文との違い(残した安定化): 補正の低域フィルタ ℓ = λ、局所 Kn > 1 のセルの凍結、入口の流入の接線速度 0
#   {extra_comment}

[case]
name = "tube_delta{delta:g}_p5"
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
delta_rp = {delta:g}

[[boundary]]
patch = "inlet"
type = "pressure_inlet"
temperature = 273.15
# 流入の速度の接線成分を 0 にする(静止した槽からの流入。論文の式 (65) は内側の値を外挿するが、δ = 100 で入口を通る
# 旋回・循環のモードが 1 反復 1.65 倍で育った。docs/phase5_report.md)
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

[kinetic]
sweep_blocks = 64
sweep_order = "octant"
inner_sweeps = {inner}
inner_wall_update = true
block_passes = 2
equilibrium = "discrete"
gsis_negative_check = "blend"
positivity_limiter = {positivity}
limiter_k = {limiter_k}

[macro]
advection = "upwind2"
boundary_reconstruction = "{brec}"
hot_interpolation = "face"
gsis_freeze_kn = {freeze}
gsis_cfl_start = {cfl_start}
init_iterations = 50
init_advection = "upwind1"
"""


# δ ごとの追加の設定(試験の結果で決めた。docs/phase5_report.md)
#   δ = 100・1000: 境界面の再構成はセルの値(論文どおりの limited では入口の層で PBC 段が逆流を作って発散した。フェーズ 4 と同じ)
#   δ = 1000: PBC 段の CFL を 5 から残差に応じて上げる(CFL 500 固定では最初の段が上流槽で発散)、正値の制限と
#             運動論のリミッタ K = 1(真空の出口の手前の冷たい噴流で CIS が負の温度になる)。この設定でも GSIS は 8〜9 回目に
#             出口の近くから発散する(未解決。docs/phase5_report.md §6)
EXTRA = {
    1: dict(brec="limited", positivity="false", cfl_start=0.0, freeze=1.0, limiter_k=3.0,
            extra_comment="δ = 1 の追加の設定なし"),
    10: dict(brec="limited", positivity="false", cfl_start=0.0, freeze=1.0, limiter_k=3.0,
             extra_comment="δ = 10 の追加の設定なし"),
    100: dict(brec="cell", positivity="false", cfl_start=0.0, freeze=1.0, limiter_k=3.0,
              extra_comment="δ = 100: 境界面の再構成はセルの値(limited では入口の層で PBC 段が発散)"),
    1000: dict(brec="cell", positivity="true", cfl_start=5.0, freeze=1.0, limiter_k=1.0,
               extra_comment="δ = 1000(未解決): 境界面の再構成はセルの値、PBC 段の CFL の立ち上げ(5 から)、正値の制限、"
                             "運動論のリミッタ K = 1(K = 3 では CIS 単独も真空の出口の手前の冷たい噴流で 78 回目に負の温度)"),
}


def main() -> None:
    for delta in (1, 10, 100, 1000):
        path = HERE / f"tube_delta{delta}_p5.toml"
        path.write_text(TEMPLATE.format(delta=delta, inner=4, **EXTRA[delta]), encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
