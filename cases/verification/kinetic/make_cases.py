"""平板の熱伝導・クエット流の検証ケース(CIS 単独)の TOML を作る。

verification/ref1d と同じ定義(README §1–2): 平均密度と平均壁温を基準状態とし、Kn は式 (8)。
  heat_lin    : T_L/T0 = 0.95、T_R/T0 = 1.05
  heat_nl     : T_L/T0 = 0.5、 T_R/T0 = 1.5
  couette_lin : 壁速度 u_y = ∓0.1 √(R T0)(左 −、右 +)
  couette_nl  : 壁速度 u_y = ∓1 √(R T0)
側面: 熱伝導は sym_y・sym_z とも対称面。クエット流は sym_y(壁が動く y 方向に垂直)を並進周期、sym_z を対称面。
名前の末尾が _per の版は側面をすべて並進周期にしたもの(格子細分化の比較用。対称面の鏡映は前の反復の値で
固定するので 1 セル厚の平板では収束が遅く、反復の打ち切りの誤差が細分化の差に混ざる。収束した解は同じ)。
メッシュ: cases/slab/mesh/slab_nx100.pmesh と、このディレクトリの mesh/slab_nx50.pmesh・slab_nx200.pmesh
(L = 1 mm、y・z は 1 セル厚)。速度格子は管と同じ 30×24×24、ξ/√(RT0) ∈ [−10, 10]×[−8, 8]²。

実行: uv run --project verification/ref1d python cases/verification/kinetic/make_cases.py
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
    return f"mesh/slab_nx{nx}.pmesh"


def wall_block(patch: str, T_rel: float, u_rel: float) -> str:
    text = f'[[boundary]]\npatch = "{patch}"\ntype = "diffuse_wall"\ntemperature = {T_rel * T0!r}\n'
    if u_rel != 0.0:
        text += f"velocity = [0.0, {u_rel * C0!r}, 0.0]\n"
    return text


def case_text(kind: str, kn: float, nx: int, periodic: bool = False) -> str:
    p = CASE_TYPES[kind]
    name = f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}" + ("_per" if periodic else "")
    couette = kind.startswith("couette")
    side_y = "periodic_slab" if (couette or periodic) else "symmetry"
    side_z = "periodic_slab" if periodic else "symmetry"
    sides = "側面はすべて並進周期" if periodic else ("sym_y は並進周期、sym_z は対称面" if couette else "側面は対称面")
    return f"""# 平板の{'クエット流' if couette else '熱伝導'}({kind}、Kn = {kn:g}、{nx} セル、{sides})。make_cases.py で生成。
# 参照解: verification/ref1d(results/summary_vmid30.csv は同じ ξx 格子の値)

[case]
name = "{name}"
mesh = "{mesh_path(nx)}"
mode = "cis"

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
tolerance = 1.0e-9

[kinetic]
sweep_blocks = 1
max_iterations = 200000
"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    count = 0
    for kind in CASE_TYPES:
        for kn in KN_LIST:
            for nx in NX_LIST:
                path = HERE / f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}.toml"
                path.write_text(case_text(kind, kn, nx), encoding="utf-8", newline="\n")
                # 側面をすべて並進周期にした版(格子細分化の比較用。収束した解は同じ)
                path = HERE / f"slab_{kind}_Kn{kn_tag(kn)}_nx{nx}_per.toml"
                path.write_text(case_text(kind, kn, nx, periodic=True), encoding="utf-8", newline="\n")
                count += 2
    print(f"{count} 個のケースを書きました: {HERE}")


if __name__ == "__main__":
    main()
