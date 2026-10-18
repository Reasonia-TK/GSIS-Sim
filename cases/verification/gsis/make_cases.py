"""平板の熱伝導・クエット流の検証ケース(GSIS)の TOML を作る。

条件は cases/verification/kinetic/make_cases.py(CIS 単独)と同じで、[case].mode = "gsis" と [gsis] だけが違う。
verification/ref1d と同じ定義(README §1–2): 平均密度と平均壁温を基準状態とし、Kn は式 (8)。
  heat_lin    : T_L/T0 = 0.95、T_R/T0 = 1.05
  heat_nl     : T_L/T0 = 0.5、 T_R/T0 = 1.5
  couette_lin : 壁速度 u_y = ∓0.1 √(R T0)(左 −、右 +)
  couette_nl  : 壁速度 u_y = ∓1 √(R T0)
側面: 熱伝導は sym_y・sym_z とも対称面。クエット流は sym_y(壁が動く y 方向に垂直)を並進周期、sym_z を対称面。
名前の末尾が _per の版は側面をすべて並進周期にしたもの。
メッシュ: cases/slab/mesh/slab_nx100.pmesh と cases/verification/kinetic/mesh/slab_nx50.pmesh・slab_nx200.pmesh。
GSIS の設定は管と同じ N_CIS = 5、M = 10、CFL = 500、補正の低域フィルタ ℓ = λ(filter_length = 1、既定)、
局所 Kn > 1 のセルの固定(gsis_freeze_kn = 1。Kn = 10 では全セル、Kn = 1 では半分ほどが固定になる)。
収束判定は CIS 単独の平板の計算(docs/phase2_report.md の表 2.2・2.3)とそろえる:
  既定の側面(対称面あり): ε < 1e-6、u の分母の下限 0.1
  _per(側面すべて並進周期): ε < 2e-7、u の分母の下限 1

実行: uv run --project verification/ref1d python cases/verification/gsis/make_cases.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
K_B = 1.380649e-23
MASS = 6.65e-27
T0 = 273.15
R = K_B / MASS
C0 = math.sqrt(R * T0)  # √(R T0) [m/s]

CASE_TYPES = {
    "heat_lin": dict(T_L=0.95, T_R=1.05, u_L=0.0, u_R=0.0),
    "heat_nl": dict(T_L=0.5, T_R=1.5, u_L=0.0, u_R=0.0),
    "couette_lin": dict(T_L=1.0, T_R=1.0, u_L=-0.1, u_R=0.1),
    "couette_nl": dict(T_L=1.0, T_R=1.0, u_L=-1.0, u_R=1.0),
}
KN_LIST = [0.01, 0.1, 1.0, 10.0]
NX_LIST = [50, 100, 200]


def kn_tag(kn: float) -> str:
    s = f"{kn:g}"
    return s.replace(".", "p")


def mesh_path(nx: int) -> str:
    if nx == 100:
        return "../../slab/mesh/slab_nx100.pmesh"
    return f"../kinetic/mesh/slab_nx{nx}.pmesh"


def wall_block(patch: str, T_rel: float, u_rel: float) -> str:
    text = f'[[boundary]]\npatch = "{patch}"\ntype = "diffuse_wall"\ntemperature = {T_rel * T0!r}\n'
    if u_rel != 0.0:
        text += f"velocity = [0.0, {u_rel * C0!r}, 0.0]\n"
    return text


def case_text(kind: str, kn: float, nx: int, periodic: bool = False) -> str:
    p = CASE_TYPES[kind]
    name = f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}" + ("_per" if periodic else "") + "_gsis"
    couette = kind.startswith("couette")
    side_y = "periodic_slab" if (couette or periodic) else "symmetry"
    side_z = "periodic_slab" if periodic else "symmetry"
    sides = "側面はすべて並進周期" if periodic else ("sym_y は並進周期、sym_z は対称面" if couette else "側面は対称面")
    return f"""# 平板の{'クエット流' if couette else '熱伝導'}({kind}、Kn = {kn:g}、{nx} セル、{sides})を GSIS で解く。
# cases/verification/gsis/make_cases.py で生成。参照解: verification/ref1d(results/summary_vmid30.csv)。
# 実行: gsis-solver run <この TOML> --cells-csv

[case]
name = "{name}"
mesh = "{mesh_path(nx)}"
mode = "gsis"

[gas]
molecular_mass = {MASS!r}
mu_ref = 1.916e-5
T_ref = {T0!r}
omega = 0.66

[conditions]
L_ref = 1.0e-3
T0 = {T0!r}
Kn = {kn!r}

{wall_block("wall_left", p["T_L"], p["u_L"])}
{wall_block("wall_right", p["T_R"], p["u_R"])}
[[boundary]]
patch = "sym_y"
type = "{side_y}"

[[boundary]]
patch = "sym_z"
type = "{side_z}"

[velocity_grid]
n = [30, 24, 24]
range = [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]]

[gsis]
n_cis = 5
max_pbc = 10
cfl_pbc = 500.0
filter_length = 1.0
tolerance = {"2.0e-7" if periodic else "1.0e-6"}
max_iterations = 5000

[kinetic]
sweep_blocks = 1
convergence_u_floor = {"1.0" if periodic else "0.1"}

[macro]
# 局所 Kn > 1 のセルは PBC の段で動かさない(管と同じ。NSF の合成方程式の段が不安定になる)
gsis_freeze_kn = 1.0
"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    count = 0
    for kind in CASE_TYPES:
        for kn in KN_LIST:
            for nx in NX_LIST:
                for periodic in (False, True):
                    tag = f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}" + ("_per" if periodic else "")
                    path = HERE / f"{tag}_gsis.toml"
                    path.write_text(case_text(kind, kn, nx, periodic), encoding="utf-8", newline="\n")
                    count += 1
    print(f"{count} 個のケースを書きました: {HERE}")


if __name__ == "__main__":
    main()
