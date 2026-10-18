"""文献の数値表(平行平板間、完全拡散反射)。値は原文の桁数のまま写した(ページ画像で照合済み)。

δ は本ソルバーの δ = sqrt(π)/(2 Kn) = p0 L / (μ(T0) sqrt(2 R T0))。
- Siewert 系の論文の板間隔 2a(粘性基準の平均自由行程 ℓ_p = μ v0/p0、v0 = sqrt(2RT0) で測った値、ε = ε_p)は δ と同じ。
- Scherer ら Part II の熱伝導の表は熱伝導率基準(ε = ε_t、ℓ_t = ℓ_p / Pr)なので、S モデルでは δ = 1.5 × 2a に読み替えた。
- Thomas, Chang & Siewert (1973) の δ = d/[θ sqrt(2RT0)](θ は BGK の平均自由時間)も、μ = pθ より同じ δ。

規格化と向き(本ソルバーへの換算は verify.py の study_literature を参照):
- P_xy/P_xy,fm, q_x/q_x,fm: 自由分子値で割った値(δ → 0 で 1)。板の速度・温度差の大きさによらない。
- precision: 文献値の桁数。Scherer ら (2009) の表は四捨五入ではなく切り捨て("trunc")と判断した
  (同じ表の BGK の値が Barichello ら (2001) の 8 桁の値の切り捨てになっている。例: 9.9911754 → 9.99117)。
- Scherer ら Part I のクエット流: 壁 τ = -a が +1、τ = +a が -1 の速度(v0 単位)で動く。本ソルバーは x = 0 が -u_w、
  x = 1 が +u_w なので、u_C(τ) = -u_y(x)/u_w、q_C(τ) = -q_y(x)/u_w(τ/a = 2x - 1、u_y, q_y, u_w は本ソルバーの無次元量)。
  流量 U_C = (1/2a²)∫_0^a u_C dτ = <u_C>_{τ∈[0,a]} / δ、熱流量 Q_C も同様。
- Thomas ら (1973) の温度・密度の分布: 壁温を T0 ± ΔT とし、(T - T0)/ΔT、(n - n0)/n0 /(ΔT/T0)。
  x/δ = +0.5 が低温壁。本ソルバーでは低温壁が x = 0 なので x = 0.5 - x/δ。
- Misdanitis (2009): 非線形の Shakhov モデル・剛体球(ω = 0.5)。下の壁 T1 = T0(1+β) が -U0、上の壁 T2 = T0(1-β) が +U0 で動く
  (U0 は v0 単位)。σ_xy = P_xy/(2 p0)、p0 は平均密度に基づく(同論文の式 (6.4))。δ0 = p0 H/(μ0 v0) は本ソルバーの δ と同じ。
"""

SOURCES = {
    "BCRS2001": "L. B. Barichello, M. Camargo, P. Rodrigues, C. E. Siewert, Unified solutions to classical flow problems "
    "based on the BGK model, Z. angew. Math. Phys. (ZAMP) 52, 517-534 (2001). https://ces.math.ncsu.edu/pdfversions/221.pdf",
    "TCS1973": "J. R. Thomas Jr., T. S. Chang, C. E. Siewert, Heat transfer between parallel plates with arbitrary surface "
    "accommodation, Phys. Fluids 16, 2116-2120 (1973). https://ces.math.ncsu.edu/pdfversions/66.pdf",
    "SPB2009a": "C. S. Scherer, J. F. Prolo Filho, L. B. Barichello, An analytical approach to the unified solution of kinetic "
    "equations in rarefied gas dynamics. I. Flow problems, ZAMP 60, 70-115 (2009). https://repositorio.furg.br/handle/1/1062",
    "SPB2009b": "C. S. Scherer, J. F. Prolo Filho, L. B. Barichello, An analytical approach to the unified solution of kinetic "
    "equations in the rarefied gas dynamics. II. Heat transfer problems, ZAMP 60, 651-687 (2009). "
    "https://repositorio.furg.br/handle/1/1036",
    "SS2002": "C. E. Siewert, F. Sharipov, Model equations in rarefied gas dynamics: Viscous-slip and thermal-slip coefficients, "
    "Phys. Fluids 14, 4123-4129 (2002). https://ces.math.ncsu.edu/pdfversions/242.pdf",
    "GS2010": "R. D. M. Garcia, C. E. Siewert, Viscous-slip, thermal-slip, and temperature-jump coefficients based on the "
    "linearized Boltzmann equation (and five kinetic models) with the Cercignani-Lampis boundary condition, "
    "Eur. J. Mech. B/Fluids 29, 181-191 (2010). https://ces.math.ncsu.edu/pdfversions/272.pdf",
    "M2009": "G. Misdanitis, Rarefied gas flow between moving plates with heat transfer, MSc thesis, University of Thessaly "
    "(2009). http://hdl.handle.net/11615/14230",
}

# ----------------------------------------------------------------------------
# 線形化した問題。rows = [(δ, 位置などの付加パラメータ, 値)]
LINEAR = [
    dict(
        key="P_bgk",
        precision=("sig", 8),
        source="BCRS2001",
        table="Table 8",
        model="BGK",
        problem="couette",
        quantity="P_xy/P_xy,fm",
        rows=[
            (1.0e-4, None, 9.9991142e-1),
            (1.0e-3, None, 9.9911754e-1),
            (5.0e-3, None, 9.9564273e-1),
            (1.0e-2, None, 9.9139801e-1),
            (2.0e-2, None, 9.8317550e-1),
            (1.0e-1, None, 9.2579682e-1),
            (1.00, None, 6.0072919e-1),
            (1.25, None, 5.5110153e-1),
            (1.50, None, 5.0958224e-1),
            (1.75, None, 4.7421167e-1),
            (2.00, None, 4.4364669e-1),
            (2.50, None, 3.9334018e-1),
            (3.00, None, 3.5353372e-1),
            (4.00, None, 2.9431859e-1),
            (5.00, None, 2.5224614e-1),
            (7.00, None, 1.9627566e-1),
            (10.0, None, 1.4731246e-1),
            (20.0, None, 8.0447692e-2),
            (100.0, None, 1.7371483e-2),
        ],
    ),
    dict(
        key="P_s",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 10 (S, ε=ε_p)",
        model="S",
        problem="couette",
        quantity="P_xy/P_xy,fm",
        rows=[
            (1.0e-3, None, 9.99117e-1),
            (1.0e-1, None, 9.25789e-1),
            (1.0, None, 6.00543e-1),
            (10.0, None, 1.47259e-1),
        ],
    ),
    dict(
        key="uC_bgk",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 8 (BGK)",
        model="BGK",
        problem="couette",
        quantity="u_C(τ/a), δ=1",
        rows=[
            (1.0, 0.1, -4.44498e-2),
            (1.0, 0.2, -8.90638e-2),
            (1.0, 0.3, -1.34019e-1),
            (1.0, 0.4, -1.79525e-1),
            (1.0, 0.5, -2.25844e-1),
            (1.0, 0.6, -2.73338e-1),
            (1.0, 0.7, -3.22558e-1),
            (1.0, 0.8, -3.74467e-1),
            (1.0, 0.9, -4.31189e-1),
            (1.0, 1.0, -5.03722e-1),
        ],
    ),
    dict(
        key="uC_s",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 8 (S, ε=ε_p)",
        model="S",
        problem="couette",
        quantity="u_C(τ/a), δ=1",
        rows=[
            (1.0, 0.1, -4.46405e-2),
            (1.0, 0.2, -8.94424e-2),
            (1.0, 0.3, -1.34580e-1),
            (1.0, 0.4, -1.80258e-1),
            (1.0, 0.5, -2.26734e-1),
            (1.0, 0.6, -2.74364e-1),
            (1.0, 0.7, -3.23689e-1),
            (1.0, 0.8, -3.75654e-1),
            (1.0, 0.9, -4.32348e-1),
            (1.0, 1.0, -5.04599e-1),
        ],
    ),
    dict(
        key="qC_bgk",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 9 (BGK)",
        model="BGK",
        problem="couette",
        quantity="q_C(τ/a), δ=1",
        rows=[
            (1.0, 0.1, 5.27864e-3),
            (1.0, 0.2, 1.06394e-2),
            (1.0, 0.3, 1.61711e-2),
            (1.0, 0.4, 2.19778e-2),
            (1.0, 0.5, 2.81909e-2),
            (1.0, 0.6, 3.49916e-2),
            (1.0, 0.7, 4.26555e-2),
            (1.0, 0.8, 5.16635e-2),
            (1.0, 0.9, 6.30784e-2),
            (1.0, 1.0, 8.23987e-2),
        ],
    ),
    dict(
        key="qC_s",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 9 (S, ε=ε_p)",
        model="S",
        problem="couette",
        quantity="q_C(τ/a), δ=1",
        rows=[
            (1.0, 0.1, 6.07574e-3),
            (1.0, 0.2, 1.22241e-2),
            (1.0, 0.3, 1.85236e-2),
            (1.0, 0.4, 2.50653e-2),
            (1.0, 0.5, 3.19638e-2),
            (1.0, 0.6, 3.93759e-2),
            (1.0, 0.7, 4.75391e-2),
            (1.0, 0.8, 5.68671e-2),
            (1.0, 0.9, 6.82756e-2),
            (1.0, 1.0, 8.65721e-2),
        ],
    ),
    dict(
        key="UC_bgk",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 11 (BGK)",
        model="BGK",
        problem="couette",
        quantity="U_C",
        rows=[(0.1, None, -6.85779e-1), (1.0, None, -2.32188e-1), (10.0, None, -4.22811e-2)],
    ),
    dict(
        key="UC_s",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 11 (S, ε=ε_p)",
        model="S",
        problem="couette",
        quantity="U_C",
        rows=[(0.1, None, -6.86397e-1), (1.0, None, -2.32964e-1), (10.0, None, -4.23132e-2)],
    ),
    dict(
        key="QC_bgk",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 12 (BGK)",
        model="BGK",
        problem="couette",
        quantity="Q_C",
        rows=[(0.1, None, 2.12308e-1), (1.0, None, 3.13628e-2), (10.0, None, 3.62529e-4)],
    ),
    dict(
        key="QC_s",
        precision=("sig", 6, "trunc"),
        source="SPB2009a",
        table="Table 12 (S, ε=ε_p)",
        model="S",
        problem="couette",
        quantity="Q_C",
        rows=[(0.1, None, 2.15085e-1), (1.0, None, 3.47257e-2), (10.0, None, 4.95270e-4)],
    ),
    dict(
        key="q_bgk",
        precision=("dec", 6),
        source="TCS1973",
        table="Table I",
        model="BGK",
        problem="heat",
        quantity="q_x/q_x,fm",
        rows=[
            (0.01, None, 0.992484),
            (0.1, None, 0.935159),
            (0.5, None, 0.768262),
            (1.0, None, 0.640853),
            (1.25, None, 0.593761),
            (1.5, None, 0.553805),
            (1.75, None, 0.519333),
            (2.0, None, 0.489203),
            (2.5, None, 0.438866),
            (3.0, None, 0.398324),
            (4.0, None, 0.336724),
            (5.0, None, 0.291918),
            (7.0, None, 0.230810),
            (10.0, None, 0.175788),
        ],
    ),
    dict(
        key="Tprof_bgk",
        precision=("sig", 5),
        source="TCS1973",
        table="Table III (δ=0.1), IV (δ=7)",
        model="BGK",
        problem="heat",
        quantity="(T-T0)/ΔT at x/δ",
        rows=[
            (0.1, 0.25, -0.063721),
            (0.1, 0.45, -0.12058),
            (0.1, 0.50, -0.13909),
            (7.0, 0.25, -0.37818),
            (7.0, 0.45, -0.70618),
            (7.0, 0.50, -0.82192),
        ],
    ),
    dict(
        key="nprof_bgk",
        precision=("sig", 5),
        source="TCS1973",
        table="Table III (δ=0.1), IV (δ=7)",
        model="BGK",
        problem="heat",
        quantity="(n-n0)/n0/(ΔT/T0) at x/δ",
        rows=[
            (0.1, 0.25, 0.057355),
            (0.1, 0.45, 0.10888),
            (0.1, 0.50, 0.12594),
            (7.0, 0.25, 0.37435),
            (7.0, 0.45, 0.69277),
            (7.0, 0.50, 0.80097),
        ],
    ),
    dict(
        key="q_s",
        precision=("sig", 7, "trunc"),
        source="SPB2009b",
        table="Table 6 (2a_t=0.2), Table 5 (2a_t=5)",
        model="S",
        problem="heat",
        quantity="q_x/q_x,fm",
        # δ = 1.5 × 2a(ε_t)
        rows=[(0.3, None, 8.731437e-1), (7.5, None, 2.915091e-1)],
    ),
    dict(
        key="Qx_s",
        precision=("sig", 7, "trunc"),
        source="SPB2009b",
        table="Table 16 (2a_t=2, δ1=8, δ2=1)",
        model="S",
        problem="heat",
        # 規格化前の熱流 Q_x。壁温の摂動が δ1 = 8、δ2 = 1 なので Q_x = (q_x/q_x,fm) × Q_fm、Q_fm = 7/sqrt(π)
        quantity="Q_x (δ1=8, δ2=1)",
        rows=[(3.0, None, 1.917923)],
    ),
]

# ----------------------------------------------------------------------------
# 非線形: Misdanitis (2009) Table 7.2(Shakhov、剛体球 ω = 0.5)の σ_xy = P_xy/(2 p0)。rows = [(β, U0, δ0, 値)]
NONLINEAR = dict(
    key="sigma_shakhov_hs",
    source="M2009",
    table="Table 7.2",
    model="S",
    omega=0.5,
    quantity="P_xy/(2 p0)",
    rows=[
        (0.0, 0.1, 0.1, -0.0522), (0.0, 0.1, 1.0, -0.0339), (0.0, 0.1, 10.0, -0.0083), (0.0, 0.1, 100.0, -0.0010),
        (0.0, 0.5, 0.1, -0.2625), (0.0, 0.5, 1.0, -0.1719), (0.0, 0.5, 10.0, -0.0427), (0.0, 0.5, 100.0, -0.0050),
        (0.5, 0.1, 0.1, -0.0473), (0.5, 0.1, 1.0, -0.0314), (0.5, 0.1, 10.0, -0.0081), (0.5, 0.1, 100.0, -0.0010),
        (0.5, 0.5, 0.1, -0.2376), (0.5, 0.5, 1.0, -0.1595), (0.5, 0.5, 10.0, -0.0415), (0.5, 0.5, 100.0, -0.0050),
        (0.9, 0.1, 0.1, -0.0286), (0.9, 0.1, 1.0, -0.0222), (0.9, 0.1, 10.0, -0.0072), (0.9, 0.1, 100.0, -0.0009),
        (0.9, 0.5, 0.1, -0.1445), (0.9, 0.5, 1.0, -0.1146), (0.9, 0.5, 10.0, -0.0375), (0.9, 0.5, 100.0, -0.0048),
    ],
    # 自由分子値(δ0 = 0)も表にある。解析解との照合用
    fm_rows=[(0.0, 0.1, -0.0564), (0.0, 0.5, -0.2821), (0.5, 0.1, -0.0506), (0.5, 0.5, -0.2529),
             (0.9, 0.1, -0.0290), (0.9, 0.5, -0.1451)],
)

# すべり係数・温度跳躍係数(ℓ_p = μ v0/p0 単位、完全拡散反射)
COEFFICIENTS = [
    dict(model="BGK", name="sigma_P", value=1.016191, source="SS2002 Table I; GS2010 Table 10"),
    dict(model="S", name="sigma_P", value=1.018372, source="SS2002 Table I; GS2010 Table 10"),
    dict(model="BGK", name="zeta_T", value=1.302716, source="GS2010 Table 6"),
    dict(model="S", name="zeta_T", value=1.954073, source="GS2010 Table 6"),
]
