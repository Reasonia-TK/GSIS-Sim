# フェーズ 4 設計: GSIS の反復(運動論 CIS + PBC マクロソルバー)

- 版: 0.2 / 2026-10-16(フェーズ 4 の実装。補正の低域フィルタ・負値の規則 `blend`・初期化の移流項・運動論の正値の制限(選択肢)を追加。結果と未解決の点は `docs/phase4_report.md`)
- 前提: `docs/spec.md` §3.7、`docs/paper_notes.md` §2・§6、論文 §3.1(Fig.1)、式 (12)–(14)、(59)、
  `docs/design_cis_gpu.md`(`KineticSolver`)、`docs/design_pbc.md`(`PbcSolver`)
- コード: `src/gsis/`(`gsis_solver.*`、`gsis_output.*`、`interfaces.hpp`)、`src/app/cmd_run.cpp`(`gsis-solver run`)、
  テストは `tests/gsis/`

## 1. 反復の手順(`GsisSolver`)

```cpp
GsisSolver g(cfg, mesh, geometry, kinetic_params, macro_params, GsisOptions::from_case(cfg, kinetic_params, macro_params));
g.initialize();                        // 一様な状態 → PBC 単独(NSF のみ、1 次の移流項)→ f = f^eq(W)
for (;;) {
  const GsisIterationInfo& it = g.iterate();
  if (it.iteration >= min_iter && it.change.epsilon < tol) break;
}
```

| 手順 | 内容 | 実装 |
|---|---|---|
| 初期化(仮定 A9) | 一様な静止状態(`[kinetic].initial_pressure`・`initial_temperature`)から PBC 単独(NSF のみ、HoT = 0、整合補正 0)を `[macro].init_iterations` 回まで。CFL は 5 から `[gsis].cfl_pbc` まで SER で上げる(`docs/phase3_report.md` §6 の 3)。移流項は `[macro].init_advection`(既定 1 次。GSIS の段は `advection`)。その状態の平衡分布で f を初期化する | `PbcSolver::solve_standalone`、`KineticSolver::initialize_equilibrium` |
| 1. CIS | `[gsis].n_cis` 回の CIS 反復: f^n → f^{n+1/2} | `KineticSolver::sweep` |
| 2. モーメント | f^{n+1/2} のモーメント W^{n+1/2}・σ・q、境界面の F^{±,0}・face_state(式 (35)、仮定 A7) | `KineticSolver::prepare`・`moments`・`boundary_fluxes` |
| 3. PBC 段 | HoT(式 (12))を固定し、整合補正つきの境界で最大 `[gsis].max_pbc` 回(CFL `[gsis].cfl_pbc`、内側の収束判定 `[macro].pbc_tolerance`)→ W^{n+1} | `PbcSolver::solve_gsis_stage` |
| 3b. 補正のフィルタ | W^{n+1} − W^{n+1/2} の、局所平均自由行程より短い波長の成分を弱める(§3a。論文にはない。`[gsis].filter_length = 0` で論文どおり) | `PbcSolver::filter_correction` |
| 4. 補正 | 式 (14) f^{n+1} = f^{n+1/2} − f^eq(W^{n+1/2}) + f^eq(W^{n+1})、負値の置換(§4) | `KineticSolver::apply_gsis_correction` |
| 5. 収束の指標 | 式 (59): W^{n+1} と W^n(PBC の出力をフィルタに通したものどうし。n = 0 は初期化の状態)の体積重みの相対変化。u の分母は `[kinetic].convergence_u_floor` で下限を置く | `relative_change` |

- 参考の指標: `change_half`(W^{n+1/2} の GSIS 反復どうしの相対変化)、`pbc_change`(PBC 段が W^{n+1/2} を動かした量)。
- 収束した解(出力)は補正の後の f(モーメントは W^{n+1})。質量流量は補正の後の f の境界の離散流束(`cis-run` と同じ定義)と、
  PBC の最後の状態の境界フラックスの両方を出す(§6)。
- 閉じた領域: 運動論は `prepare` のたびに全質量を初期化直後の値に規格化し、PBC は段の初めの全質量を保つ(どちらも初期状態の全質量)。
- 決定性: 運動論(CPU・GPU)・PBC ともに総和の順序を固定しているので、同じ入力なら反復回数も含めてビット単位で同じ(試験済み)。

## 2. 受け渡し(`src/gsis/interfaces.hpp`)

すべてホスト側・FP64・無次元(p = ρT)、元のメッシュのセル番号・境界面番号。

| 型 | 内容 | 作る側 → 使う側 |
|---|---|---|
| `KineticMoments` | W^{n+1/2}(ρ, u, T, p)、σ(6 成分)、q | 運動論 → PBC(HoT、W⁰) |
| `BoundaryKineticFluxes` | 境界面ごとの F⁺・F⁻(面積を掛けた値)、face_state [ρ, u, T] | 運動論 → PBC(整合補正、勾配の境界値) |
| `CellMacroState` | W^{n+1} | PBC → 運動論(補正) |

境界の種類ごとの約束(フェーズ 2 の報告 §5 とフェーズ 3 の実装を突き合わせた結果):

| 境界 | 運動論の F⁺・F⁻ | PBC の扱い | 整合 |
|---|---|---|---|
| 拡散壁 | F⁻ は緩和しない ρ_Γ で作る(F⁺_ρ + F⁻_ρ = 0) | Ŧ⁻ = ρ̃ Ŧ⁻(W⁽¹⁾)、ρ̃ は F⁺_ρ から(式 (75)) | C⁻_ρ = F⁻⁰_ρ + F⁺⁰_ρ = 0 なので PBC の壁の質量流束も厳密に 0 |
| 対称面 | F⁻ = 鏡映した速度点の再構成値の流束 | Ŧ⁻ = M F⁺(流出フラックスの鏡映) | 離散的にも F⁻⁰ = M F⁺⁰(鏡映した点の積は符号だけ違う)なので C⁻ は丸め誤差だけ |
| 並進周期 | F⁺ = セル値の全速度の流束、F⁻ = 0 | 面を流束の和・ヤコビアン・Λ から除く。勾配は 0 | 使わない |
| 真空出口 | F⁻ = 0、face_state は流出側の半分だけのモーメント(密度は約半分) | Ŧ⁻ = 0、勾配の境界値は face_state | HoT の計算と PBC で同じ値を使う(§3 の選択肢 `gsis_face_state`) |
| 圧力入口・出口 | F⁻ = f^eq(W_b)(運動論側の再構成で作る W_b) | Ŧ⁻ = Ŧ⁻(W_res(W_b))(PBC 側の再構成) | 段の初めの差は C⁻ が吸収する |

## 3. PBC 段の選択肢(`[macro]`、フェーズ 4 で追加)

| キー | 既定 | 内容 |
|---|---|---|
| `boundary_reconstruction` | `limited` | 境界面の状態 W_{i,b}(フェーズ 3)。`cell` でも結果はほぼ同じ(報告 §3) |
| `hot_interpolation` | `face` | `cell`: HoT をセル中心で作って面へ補間(式 (12)(30)、論文どおり)/ `face`: 面の HoT を σ の補間 − σ^NSF_f(W^{n+1/2}) にする。W = W^{n+1/2} で面の粘性フラックスが運動論の σ・q の補間に厳密に一致する |
| `gsis_face_state` | `kinetic` | 境界セルの勾配の境界値: `kinetic`(face_state、仮定 A7)/ `walls`(開いた境界はセルの値)/ `cell` |
| `gsis_continuity_pseudo_time` | false | 連続式にも擬似時間項 V/(Δt T) を入れる(論文にはない) |
| `gsis_freeze_kn` | 0(管・平板の検証のケースは 1) | 局所 Kn(式 (8)、L_ref 基準)がこれを超えるセルの p, u, T を段の間は固定する(0 なら使わない)。固定したセルは補正 0(§3a のフィルタでも δ̃ = 0) |
| `init_advection` | `upwind1` | 初期化(PBC 単独)の移流項。GSIS の段は `advection`。初期化は f^eq の初期値を作るだけなので頑丈な 1 次にする(管の δ = 1・1000 で 2 次の初期化は真空の出口の近くに不自然なセルを残し、CIS が 2 回目で負の温度になった) |
| `gsis_cfl_start`・`gsis_cfl_growth` | 0・2 | (フェーズ 5a)正なら GSIS の段の CFL をこの値から残差に応じて `[gsis].cfl_pbc` まで上げる(SER、初期化と同じ式)。0 なら固定(論文どおり)。管 δ = 1000 では CFL 500 固定の最初の段が上流槽の淀んだ領域で発散した(`docs/phase5_report.md`) |

圧力入口の流入の速度(フェーズ 5a、`[[boundary]].tangential_velocity`、運動論と PBC の両方): `"extrapolate"`(既定、論文の式 (65)
u_b = u_{i,b} + Δu n)/ `"zero"`(接線成分を 0 にする u_b = (u_{i,b}·n + Δu) n。静止した大きな槽からの流入)。管 δ = 100 では、
入口で拘束されない旋回・循環のモードが GSIS の反復で 1 反復 1.65 倍に育って発散した(`docs/phase5_report.md`)。PBC のヤコビアン
(式 (82)–(84))の J_res は、接線成分を 0 にするときは速度の列を n nᵀ にする(差分近似との一致を試験)。

`hot_interpolation = "face"` の面の HoT:
HoT_σ,f = Σ_s w_s(HoT_σ,s + σ^NSF_s(W⁰)) − σ^NSF_f(W⁰) = (σ の補間) − σ^NSF_f(W⁰)、q も同様。
σ^NSF_s は `compute_hot` のセル中心の式、σ^NSF_f は PBC の面の式(式 (29) の面勾配、μ_f は両側の平均)。
段の間の面の粘性フラックスは (σ の補間) + [σ^NSF_f(W) − σ^NSF_f(W⁰)] になる。

## 3a. 補正の低域フィルタ(`[gsis].filter_length`、フェーズ 4 で追加、論文にはない)

PBC 段の出力 W^{n+1} と CIS の後の W^{n+1/2} の差のうち、局所平均自由行程 λ より短い波長の成分を弱めてから、
式 (14) の補正と式 (59) に使う。

(V_i − ∇·(ℓ²∇)) δ̃ = V_i δ、δ = [ln(ρ^{n+1}/ρ^{n+1/2}), u^{n+1} − u^{n+1/2}, ln(T^{n+1}/T^{n+1/2})] → W^{n+1} := W^{n+1/2} ⊕ δ̃

| 項目 | 内容 |
|---|---|
| 長さ | ℓ_i = `filter_length` × λ_i、λ_i = μ(T_i)/p_i √(πT_i/2)(W^{n+1/2} の値。式 (8) の局所 Kn × L_ref)。上限 `filter_max_length`(L_ref 単位。0 なら領域(セル中心の外接直方体)の対角線)。下限 `filter_min_cells` × h_i(h_i = V_i^{1/3}。フェーズ 5b で追加、0 なら使わない) |
| 離散化 | セル中心の 2 点差分。面の係数は ℓ_f² A_f / d_n,f、ℓ_f² は両側の ℓ² の調和平均(希薄な側の大きな ℓ が連続流の側へしみ出さない)。境界面は流束 0。`gsis_freeze_kn` で固定したセルは δ̃ = 0(隣のセルから見ると Dirichlet 条件) |
| 解き方 | 同じ行列の 5 つのスカラーの系を GMRES + AMG(相対 1e-8)。管で 1 回 約 1 s(AMG の準備を 5 回) |
| 効果 | 一様な λ なら、波数 k の成分を 1/(1 + k²ℓ²) 倍にする(単体テストで厳密に確認)。kλ ≲ 1(NSF が成り立ち、GSIS の加速が効く長い波長)はほぼそのまま通し、kλ ≫ 1 は消す |
| 不動点 | 変わらない(補正が 0 なら δ̃ = 0) |
| 正値 | 密度と温度は比の対数を均すので正のまま。閉じた領域の全質量は運動論の規格化(`prepare`)で保つ |

理由(報告 §3 の 7): NSF の合成方程式は、平均自由行程より短い波長の誤差に kλ 倍ほど強く応える
(速度の誤差 δu を連続式で消すための圧力の補正が μk|δu| ≈ kλ ρc|δu|)。元の GSIS の解析では CIS が輸送を
厳密に解くので、その成分は 1 回の CIS で 1/(kλ) に減り、積は O(1) に収まる。本実装の CIS(LU-SGS 1 往復、
2 次の欠損補正、Δt = ∞)は、λ ≫ h の格子規模の成分を 1 回で 0.6〜1 倍にしか減らさない(1 次の左辺と 2 次の
右辺の比。無衝突の極限で、3 セル周期で 0.87 倍、奇偶で −1 倍)ので、ループの利得が 1 を超えて発散する
(平板の Kn ≥ 1、対称面の側面では Kn = 0.1 でも)。フィルタは補正のその成分だけを消す。

## 3b. 局所 Kn による補正の減衰(`[gsis].damp_kn`、フェーズ 5b で追加、論文にはない)

PBC 段の補正(フィルタの後)を、セルごとに w_i = 1/(1 + (Kn_i/`damp_kn`)²) 倍にする(ln ρ・u・ln T の差に掛ける。Kn_i は
W^{n+1/2} の局所 Kn)。`gsis_freeze_kn` の凍結をなめらかにしたもので、不動点は変わらない。管 δ=1000 の粗い管で試したが、
減衰したセル(下流の噴流)は CIS の速さでしか収束しないので採らなかった(`docs/phase5b_d1000_report.md`)。

## 4. 負値の置換(`[kinetic].gsis_negative_check`)

| 値 | 判定 |
|---|---|
| `all` | 補正した f の最小値 min_k f^{n+1}_k < −10⁻⁸ ρ/T^{3/2} のセルを f^eq(W^{n+1}) にする(論文どおり) |
| `created` | 補正で生じた負の部分 min_k [f^{n+1}_k − min(f^{n+1/2}_k, 0)] で判定する。補正の前から負だった速度点は、補正で下がった量だけを見る |
| `limit` | 置き換えずに、そのセルの補正を α 倍に縮める。α = min(1, min_k [f_k − (min(f_k, 0) + thr)] / (−d_k))(d_k < 0 の点、d = f^eq(W^{n+1}) − f^eq(W^{n+1/2})、thr = −10⁻⁸ ρ/T^{3/2})。非平衡の部分を消さない。置換の数の代わりに縮めたセルの数を数える |
| `blend` | 補正した f(g = f + d)と f^eq(W^{n+1}) を、補正で生じた負の部分が閾値を超えない最小の割合 θ で混ぜる: f^{n+1} = (1 − θ) g + θ f^eq(W^{n+1})、θ = max(0, max_k [(min(f_k, 0) + thr) − g_k] / (f^eq_k − g_k))(1 で打ち切り)。θ = 1 が論文の置換。モーメントは θ によらず W^{n+1} で、わずかな負値のために非平衡の部分をすべて消すことがない。θ > 0 のセルを数える |

f が負にならない運動論の解では両者は同じ。本実装の CIS(2 次の再構成、弱いリミッタ)は f を少し負にすることがあり、
`all` では補正が 0 のセルも毎回平衡分布に戻される(報告 §3)。

## 5. `GsisOptions`(`[gsis]` などから)

| 項目 | 出どころ | 既定 |
|---|---|---|
| `n_cis` | `[gsis].n_cis` | 5 |
| `max_pbc` | `[gsis].max_pbc` | 10 |
| `cfl_pbc` | `[gsis].cfl_pbc` | 500 |
| `init_iterations` | `[macro].init_iterations` | 50 |
| `init_cfl_start`・`init_cfl_growth` | (固定) | 5・2 |
| `u_floor` | `[kinetic].convergence_u_floor` | 0.01 |
| 収束判定・上限 | `[gsis].tolerance`・`[gsis].max_iterations`(フェーズ 4 で追加、既定 1000)、CLI の `--tol`・`--max-iter` | 1e-5・1000 |
| `filter_length`・`filter_max_length` | `[gsis].filter_length`・`filter_max_length`(フェーズ 4 で追加、§3a) | 1・0(領域の対角線) |
| `filter_min_cells` | `[gsis].filter_min_cells`(フェーズ 5b、§3a の長さの下限) | 0(使わない) |
| `damp_kn` | `[gsis].damp_kn`(フェーズ 5b、§3b) | 0(使わない) |
| `sections` | `[output].mass_flow_sections`・`mass_flow_section_axis`(フェーズ 5c、§6a)、CLI の `--sections` | なし(集計しない) |

質量の釣り合いの許容値 `[gsis].mass_balance_tolerance`(既定 2×10⁻³)と `[gsis].mass_balance_required`(既定 false)は
`gsis-solver run`・`cis-run` が使う(§6a。`GsisSolver` は使わない)。

## 6. `gsis-solver run`

```
gsis-solver run <case.toml> [--out <dir>] [--mode gsis|cis] [--max-iter N] [--min-iter N] [--tol X]
                [--backend cpu|cuda] [--precision fp32|fp64] [--set [kinetic.|macro.|gsis.]key=value]...
                [--threads N] [--print-every N] [--vtu-every N] [--dump-every N] [--no-vtu] [--cells-csv]
                [--init-from <cells.csv>] [--save-init <cells.csv>] [--sections <位置の並び>]
```

- `[case].mode`(または `--mode`)が `gsis` なら GSIS、`cis` なら CIS 単独(`cis-run` と同じ計算・出力)。出力先の既定は `runs/gsis/<ケース名>/`。
- `--set`: 接頭辞なし・`kinetic.` は `[kinetic]`、`macro.` は `[macro]`、`gsis.` は `[gsis]`(n_cis・max_pbc・cfl_pbc・tolerance・max_iterations・filter_length・filter_max_length・filter_min_cells・damp_kn・mass_balance_tolerance・mass_balance_required)。
- `--sections`: 質量流量を集計する断面の位置(L_ref 単位、例 `"8, 10.5, 13, 15.5, 18"`。`[output].mass_flow_sections` を上書き。§6a)。
- `--init-from`: `cells.csv`(cis-run・run・pbc-run の出力。列 cell, rho, ux, uy, uz, T を使う)の状態の平衡分布から始める(PBC 単独の初期化は省く)。
- `--dump-every N`: 診断用。N 回ごとに `diag_<反復>.csv`(W^{n+1/2}、W^{n+1}、HoT の大きさ、段の初めの PBC の残差と質量流量)。

| 出力 | 内容 |
|---|---|
| `history.csv` | GSIS 反復ごと: ε と成分、`eps_half`・`pbc_change`、最後の CIS 反復の残差、全質量、PBC 段の要約(反復数、収束、更新量、残差、線形ソルバーの反復、捨てた反復、固定したセル)、置換したセル数、HoT の大きさ、パッチごとの質量流量(運動論 `mdot_<パッチ>_kg_s`・`_nd`、PBC `mdot_pbc_<パッチ>_nd`)、管の Ṁ(運動論・PBC)、q・σ の体積平均、時間の内訳(CIS、モーメント、PBC、補正、転送、カーネル別)、補正のフィルタの線形ソルバーの反復 `filter_iterations` と時間 `t_filter_s`、最後の列は `gpu_memory_bytes` |
| `pbc_history.csv` | PBC の反復ごと(初期化は `gsis_iteration` = 0): CFL、残差、更新量、線形ソルバー、切ったセル、パッチごとの質量流量 |
| `summary.json` | `mode`、反復数(GSIS・CIS の合計)、収束、ε、時間の内訳(合計と 1 反復あたり)、初期化(PBC 単独の反復数・NSF の Ṁ)、PBC の合計、負値の置換、パラメータ、パッチごとの流量(補正の後の f、`cis-run` と同じ形式)、`mass_flow`(補正の後の f)・`mass_flow_pbc`(W^{n+1})・`mass_flow_half`(f^{n+1/2}) |
| `fields.vtu` | `cis-run` と同じセル値 + `hot_sigma`・`hot_q`(SI) |
| `boundary.vtu` | `cis-run` と同じ |
| `cells.csv` | `--cells-csv` のとき。`cis-run` と同じ形式 |

フェーズ 5c で足した出力(既存の列・キーは変えていない。`history.csv` の最後の列は `gpu_memory_bytes`、その前は
`filter_iterations`・`t_filter_s` のまま): `history.csv` の `M_dot_pbc_outlet` の後に `mass_imbalance`・`mass_imbalance_pbc` と
断面ごとの `M_dot_sec<k>`(f^{n+1/2})・`M_dot_pbc_sec<k>`(W^{n+1})。管でなければ断面の列の接頭辞は `mdot_nd_`。`summary.json` の
`mass_balance`・`mass_flow_sections`、`gsis.mass_balance_tolerance`・`gsis.mass_balance_required`。

## 6a. 質量の釣り合いと断面の質量流量(フェーズ 5c)

ε(式 (59))は連続する反復の W の変化しか見ないので、変化の遅いモード(上流槽の質量の出入り、凍結した希薄な領域)が残っていても
判定を満たす(CIS 単独の δ=10 は ε < 10⁻⁵ の時点で入口の Ṁ が出口より 31% 小さかった)。そこで質量の釣り合いを出力し、
許容値を超えたら警告する。

| 量 | 定義 |
|---|---|
| 境界の釣り合い `imbalance` | パッチごとの外向きの質量流量 F_p から inflow = Σ max(−F_p, 0)、outflow = Σ max(F_p, 0)、(outflow − inflow)/max(inflow, outflow)。管なら (Ṁ_出口 − Ṁ_入口)/Ṁ(壁の正味の流量は 0)。閉じた領域では定義しない(NaN) |
| 断面の流量 | 軸 a の位置 X の断面 = セル中心が X の手前(x_c < X)と先(x_c ≥ X)に分かれる 2 つのセルの間の内部面の集まり(`mesh/section_cut.hpp`。軸にそろった六面体では平面、一般には階段状)。その面の離散的な質量流束の和(軸の正の向きが正)。この面は領域を前後に分けるので、定常で保存的なら入口・出口の流量と同じになる |
| 運動論の断面の流量 | 内部面の質量流束 Σ_k ϖ (ξ_k·S) f_face,k。面の値は CIS の残差と同じ風上側の制限付き線形再構成(`internal_face_flux_term`、勾配はその場で計算)。GPU は 1 面 = 1 ブロックで決まった順序で足す(`k_internal_face_mass_flux`) |
| PBC の断面の流量 | PBC 段の最後の状態 W^{n+1} で評価し直した内部面の質量フラックス F_ρ(`PbcSolver::internal_face_mass_flux`) |
| 断面のばらつき `section_spread` | {inflow, outflow, \|断面の流量\|} の (max − min)/平均 |

- 判定(`gsis-solver run`・`cis-run` の終わり): 運動論の最後の f(GSIS は補正の後)と、GSIS では PBC の W^{n+1} について、
  \|imbalance\| と section_spread を `[gsis].mass_balance_tolerance`(既定 2×10⁻³)と比べ、超えたら警告し、`summary.json` の
  `mass_balance.within_tolerance` を false、`warnings` に内容を書く。f^{n+1/2} の値も参考に書く(判定には使わない)
- `[gsis].mass_balance_required = true` なら、収束の判定を「ε < tolerance かつ CIS の後の f^{n+1/2}(CIS 単独は f^ℓ)の
  \|imbalance\| < mass_balance_tolerance」にする(既定 false: 論文どおり ε だけで判定し、警告だけ出す)
- 注意: 局所 Kn > 1 の凍結がある条件(δ=1)では、GSIS の不動点そのものが PBC で動かす上流槽と凍結した管の境目で質量を保存せず、
  入口と管の流量が 0.55% 食い違ったまま頭打ちになる(ε = 10⁻⁷ まで続けても変わらない。`docs/phase5c_report.md` §2)。このとき
  `mass_balance_required = true` は判定を満たさない。δ=100 の凍結した下流槽(噴流の膨張部)も、ε が 2 桁下がる間 CIS の後の差が
  0.77% → 0.73% とほぼ一定で、同じ食い違いと考える。管を流れる量は断面の流量で読む
- 断面の流量は管の解析で、どこで質量が釣り合っていないかを見るのに使う(例: δ=100 の GSIS の PBC の断面は管の中で一定で、
  入口と出口の差は凍結した下流槽で生じる。`docs/phase5c_report.md`)

## 7. 決めたこと(理由と試験の結果は `docs/phase4_report.md`)

| 項目 | 決めたこと | 論文との違い |
|---|---|---|
| 反復の構造 | Fig.1 のとおり(CIS N 回 → モーメント・境界の流束 → PBC 段 → 式 (14) の補正 → 式 (59))。初期化は PBC 単独(SER)→ f^eq | なし |
| 収束判定 | 式 (59) を PBC の出力 W^{n+1} と W^n の間で。u の分母の下限 0.01 | 論文は W の種類を書いていない |
| HoT の面の値 | `hot_interpolation = "face"`(既定) | 論文はセル中心の HoT を補間(`"cell"` で選べる) |
| 負値の置換 | `gsis_negative_check = "created"`(フェーズ 4 の既定。フェーズ 5b で既定を `"blend"` に変えた。§7a) | 論文は補正後の f の最小値(`"all"`)。CIS の f が負にならなければ同じ。`"blend"`(最小の割合で平衡分布と混ぜる)は管の δ=10 で ε の減り方が `created` とほぼ同じ(15 回目で 5.5×10⁻³ と 5.2×10⁻³)で、θ > 0 のセルが残る反復がかえって長かったので既定にしなかった |
| 補正の低域フィルタ | `[gsis].filter_length = 1`(既定、ℓ = λ、§3a) | 論文にはない(`0` で論文どおり)。平板の Kn ≥ 1(対称面の側面では Kn = 0.1 も)と管で、フィルタなしでは発散・停滞した |
| 初期化の移流項 | `[macro].init_advection = "upwind1"`(既定) | 論文は初期化の方法を書いていない(仮定 A9) |
| 境界面の再構成 | `boundary_reconstruction = "limited"`(論文どおり、フェーズ 3 の既定)。管 δ=100 のケースだけ `"cell"`(と `blend`) | 管 δ=100 では `"limited"` だと入口の近くの振動で 10 回目に PBC 段が発散した。`"cell"` で入口は落ち着く(不動点は変わらない)が、δ=100 は別の理由でまだ収束しない(報告 §5.2) |
| 管の移流項 | 管のケースは `advection = "upwind2"` | 論文は「高次化してよい」 |
| 希薄なセル | 管と平板の検証のケースは `gsis_freeze_kn = 1`(局所 Kn > 1 のセルは PBC 段で動かさない。コードの既定は 0) | 論文にはない。フィルタがあっても、固定しないと管の δ=10 の下流槽で PBC が毎回 7% 動かし 2 万セルの置換が続き、平板の Kn = 10 では PBC 段の出力がでたらめになった |
| N_CIS・M・CFL | 5・10・500(論文どおり) | なし |

### 7a. フェーズ 5a の決定(理由と試験の結果は `docs/phase5_report.md`)

| 項目 | 決めたこと | 論文との違い |
|---|---|---|
| CIS(運動論の段) | 管のケースは octant の順序、内部反復 4 回 + 壁の更新、ブロック間の反復 2 回、保存的な離散平衡(`docs/design_cis_gpu.md` §14)。コードの既定は従来のまま | 論文に記載なし。従来の 1 往復では平板の Kn ≥ 1 と管が発散・停滞した |
| 補正の低域フィルタ | CIS を強くした後は不要(平板 16 ケース、管 δ=1・10・100 がフィルタなしで収束)。管のケースは δ=100 の反復を 17 → 10 回に減らすので残した | 論文にはない |
| 局所 Kn > 1 の凍結 | 必要(管のケース) | 論文にはない。凍結しないと希薄なセルで PBC 段が運動論と食い違う補正を毎回作り、負値の処理で運動論の解が壊れる |
| 負値の規則 | 管・平板のケースは `blend` | 論文は置換(`all`) |
| 圧力入口の流入の接線速度 | 0(`[[boundary]].tangential_velocity = "zero"`) | 論文の式 (65) は外挿。δ=100 で入口を通る旋回・循環のモードが育って発散した |
| PBC 段の CFL | δ=1000 のケースは 5 から立ち上げ(`[macro].gsis_cfl_start`) | 論文は 500 固定 |


### 7b. フェーズ 5b-1 の追加(管 δ=1000。理由と試験の結果は `docs/phase5b_d1000_report.md`)

| 項目 | 決めたこと・足したもの | 論文との違い |
|---|---|---|
| 境界セルの勾配の境界値(`[macro].gsis_face_state`) | δ=1000 のケースは `"cell"`(既定は `"kinetic"` のまま) | 仮定 A7 を変える(論文に記載なし)。運動論の境界面の分布のモーメントが GSIS の 1 反復遅れで PBC 段の勾配に戻る経路が、連続流の上流槽(CIS は W をほとんど動かさない)で外側の壁と入口の近くの格子規模のモードを 1 反復 2〜2.5 倍に育てた |
| 冷たいセルの目標分布(`[kinetic].cold_bgk_temperature`) | δ=1000 のケースは 0.1(既定 0) | 論文は Shakhov のまま。速度格子で解像できない冷たいセルで目標分布が負になった |
| 離散平衡が作れないセル(`[kinetic].deq_fallback`) | 既定 `"collisionless"`(衝突なし・GSIS の補正なし) | 論文に記載なし |
| 補正のフィルタの長さの下限(`[gsis].filter_min_cells`)、補正の減衰(`[gsis].damp_kn`)、連続流の HoT の減衰(`[macro].gsis_hot_kn`)、圧力境界の W_{i,b}(`[kinetic].pressure_boundary_reconstruction`) | 選択肢として足した(既定は使わない)。δ=1000 の上流槽のゆっくり育つモードには効かなかった(`docs/phase5b_d1000_report.md` §2) | — |

### 7c. フェーズ 5c の追加(診断。理由と結果は `docs/phase5c_report.md`)

| 項目 | 決めたこと・足したもの | 論文との違い |
|---|---|---|
| 質量の釣り合いの診断 | `history.csv`・`summary.json` に境界の釣り合いと断面の質量流量を出し、`[gsis].mass_balance_tolerance`(既定 2×10⁻³)を超えたら警告(§6a)。`mass_balance_required = true` で収束の判定にも加えられる(既定 false) | 論文の判定は式 (59) だけ(既定はそのまま) |
| PBC の移流項の既定 | `[macro].advection` の既定を `"upwind2"` にした(GSIS の段と `pbc-run`。初期化 `init_advection` は `"upwind1"` のまま)。管のケースはフェーズ 4 から TOML で指定していたので結果は変わらない | 論文の式 (27)(28) はドナーセル(1 次)。1 次は GSIS の不動点を動かし、管 δ=10 で入口と出口が 4% 食い違った(`docs/phase5b_d10_report.md` §4.4) |
