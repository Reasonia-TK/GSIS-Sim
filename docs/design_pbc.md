# フェーズ 3 設計: PBC マクロソルバー(CPU、FP64)

- 版: 0.2 / 2026-10-16(実装に合わせて更新。結果と未解決の点は `docs/phase3_report.md`)
- 前提: `docs/spec.md` §3.6〜3.8、`docs/paper_notes.md` §4〜6、論文の式 (12)–(58) と付録 B・C
- コード: `src/macro/`(`pbc_solver.*`、`face_flux.hpp`、`boundary_flux.hpp`、`linear_solver.*`、`macro_params.*`、`small_matrix.hpp`)、
  `src/app/cmd_pbc_run.cpp`、テストは `tests/macro/`

## 1. 役割と入出力(フェーズ 4 向けの API)

| 区分 | 内容 |
|---|---|
| 入力 | メッシュ(SI のまま。内部で L_ref で割る)、$\boldsymbol W^0=\boldsymbol W^{n+1/2}$(`CellMacroState` の p, u, T)、運動論のモーメント σ・q(`KineticMoments`)、境界面ごとの $\boldsymbol F^{\pm,0}_b$ と境界面の分布のモーメント(`BoundaryKineticFluxes`)、境界条件、パラメータ(`[macro]`、M と CFL は `[gsis]`) |
| 出力 | $\boldsymbol W^{n+1}$(`CellMacroState`)、診断量(`PbcStageReport`、反復ごとの `PbcIterationInfo`)。ラグ付きの面質量流量 $\widehat{\dot m}$ は `PbcSolver` の中に持ち越す |
| 単独モード | HoT = 0、$\boldsymbol F^{\pm,0}_b=\widetilde{\boldsymbol F}^\pm_b(\boldsymbol W^0_{i,b})$(整合補正 0)。初期化(NSF のみ)・`pbc-run`・単体の検証に使う |

```cpp
PbcSolver solver = PbcSolver::from_case(cfg, derived, mesh, geometry, params);   // 境界条件・気体を無次元に
// 初期化(単独モード。一様な状態から始めるので CFL を立ち上げる)
PbcStageOptions init;  init.max_iterations = params.init_iterations;  init.cfl = cfg.gsis.cfl_pbc;  init.cfl_start = 5.0;
solver.solve_standalone(state, init);
// GSIS の反復ごと: HoT(式 (12))を作り、整合補正付きの境界で M 回まで
PbcStageReport rep = solver.solve_gsis_stage(moments, fluxes, cfg.gsis.max_pbc, cfg.gsis.cfl_pbc, W_next);
```

- 低水準の `run_stage(PbcStageInput{initial, hot, boundary, source}, PbcStageOptions, out)` もある(HoT・境界・試験用の源項を個別に渡す。製造解法の試験はこれを使う)。`compute_hot(moments, boundary)` は式 (12) だけを計算する。
- すべて FP64、無次元(R = 1、p = ρT)。並列化は OpenMP。面ごとの寄与を面の配列に書き、セル → 面の CSR の順にセルへ集める(atomic なし)。総和はスレッド数によらない固定長の区間に分けて足す(`deterministic_sum`)。同じ入力なら、スレッド数が違ってもビット単位で同じ結果になる(§8)。
- `PbcSolver` はメッシュを参照で持つ(メッシュの方が長く生きること)。

## 2. PBC 段の手順

**段の準備**(`run_stage` の初め)
1. W⁰ を読む。HoT(`compute_hot` の結果)を保存する。HoT の σ^NSF・q^NSF は、W^{n+1/2} のセル中心の最小二乗勾配(境界面の値は `face_state`)と μ(T_i)、κ(T_i) から作る。
2. GSIS の段: 境界面の勾配用の値を `face_state`(運動論の境界面分布のモーメント、仮定 A7)から作り、段の間は固定する。W⁰ で勾配とリミッタを計算して $\boldsymbol W^0_{i,b}$ を再構成し、整合補正 $\boldsymbol C^+=\boldsymbol F^{+,0}-\widetilde{\boldsymbol F}^+(\boldsymbol W^0_{i,b})$、$\boldsymbol C^-=\boldsymbol F^{-,0}-\widetilde{\boldsymbol F}^-(\boldsymbol W^0_{i,b},\boldsymbol F^{+,0})$ を保存する。これで m = 0 の境界フラックスは $\boldsymbol F^{+,0}+\boldsymbol F^{-,0}$ に厳密に一致する(試験済み)。
3. 単独モード: 補正 0。境界面の勾配用の値は §5.3。
4. 閉じた領域(圧力入口・出口・真空・遠方場の面がない)なら、W⁰ の全質量 Σρ_iV_i を覚える。

**評価**(`evaluate`。1 反復に 2 回: 圧力–速度の段と温度の段の初め)
1. 最小二乗勾配(p, u_x, u_y, u_z, T)と Venkatakrishnan リミッタ(`physics/limiter.hpp`。ε² = (K h)³、h = V^{1/3}、最大・最小は自分・隣・境界面の値)。
2. 境界面: $\boldsymbol W_{i,b}$(§5.2)、境界ヤコビアン $\boldsymbol J_b$(W_i で評価、5×5)、$\|\boldsymbol B_{u,ib}\|_2$(3×3 の最大特異値、対称 3×3 の固有値の解析解)、擬似時間の Λ の寄与。
3. $a_{u,i}$(式 (26)。$\widehat{\dot m}$ はセル i から外向き。最初の評価は式 (26) の下の初期値)と $D_i=V_i/a_{u,i}$。
4. 内部面(`face_flux.hpp`): Rhie–Chow(式 (21)–(25))、ドナーセルと ṁ(式 (27))、移流フラックス(式 (28)。2 次は §6)、非直交補正付きの面勾配(式 (29))、HoT の面補間(式 (30))、t・Q と粘性フラックス(式 (31)(32))、運動エネルギーの欠損 $\mathcal K$(owner 側・neighbour 側の 2 つ)、ヤコビアンの部品(式 (44) の $\boldsymbol J_{\rho}$、1 次の ṁ、ドナーの速度、$\mu A/d_n$、$\kappa A/d_n$)。
5. 境界フラックス(式 (36)–(38)、`boundary_flux.hpp`)。
6. セルへ集める: $\boldsymbol{\mathcal R}_i=\sum\pm\boldsymbol F_f+\sum\boldsymbol F_b-\boldsymbol b_i$(b は試験用の源項)、$\sum\mathcal K$、Λ_i。
7. $\widehat{\dot m}\leftarrow\dot m$(RC の補正と選んだ再構成を含む値)。

**反復 m = 0, …, M−1**
1. 評価(W^m)。残差のノルム、パッチごとの質量流量を記録する。
2. 擬似時間刻み $\Delta t_i=\mathrm{CFL}\,V_i/\Lambda_i$(式 (56))。CFL の決め方は §7。
3. 圧力–速度の系 $(\boldsymbol J^{pu}+\boldsymbol M_{pu})\Delta\boldsymbol W_{pu}=-\boldsymbol{\mathcal R}^{pu}$ を組み立てて解き(§3)、p と u を更新する。
4. 評価(p, u を更新、T はそのまま)。温度の残差 $\mathcal R_T=\mathcal R_E-\boldsymbol u_i\cdot\boldsymbol{\mathcal R}_{mom}+(K_i-\tfrac32T_i)\mathcal R_\rho-\sum\mathcal K$(式 (51))と $\boldsymbol J_T+\boldsymbol M_T$(式 (53)–(55)、(57))を作って解き、T を更新する(§4)。
5. 閉じた領域なら、p を一様に掛け算で補正して全質量を段の初めの値に戻す。
6. 更新量 $\varepsilon=\max(\varepsilon_p,\varepsilon_u,\varepsilon_T)$。$\varepsilon_p=\|\Delta p\|/\|p\|$、$\varepsilon_T=\|\Delta T\|/\|T\|$、$\varepsilon_u=\|\Delta\boldsymbol u\|/\|(|\boldsymbol u|^2+T)^{1/2}\|$(体積重みの 2 ノルム。速度は熱速度も含めて規格化し、静止した気体でも意味を持つようにした)。ε が許容値(`pbc_tolerance`)未満なら終了。
7. 段の最後に、最終状態でもう一度評価する(出力・診断量と、次の段の $\widehat{\dot m}$ のため)。

**残差のノルム**: 質量・運動量は面積を掛けたフラックスの和の 2 ノルム。エネルギーは $\mathcal R_E-\sum\mathcal K$ の 2 ノルム。式 (51) の温度の式が収束すると $\mathcal R_E$ は 0 ではなく $\sum\mathcal K$(風上化で失われる運動エネルギー)に近づく(論文の設計どおり。全エネルギーは離散的には $\mathcal K$ の分だけ保存しない)。

## 3. 圧力–速度の線形系

- 未知数はセルごとの [p, u_x, u_y, u_z]。4×4 ブロックの CSR(行 i の列は i と面で隣り合うセル。同じ組の面が複数あれば 1 つのブロックにまとめる)。構造は構築時に 1 回作る。
- 内部面: 式 (44)(47)。owner の行に $\boldsymbol J_{oo},\boldsymbol J_{on}$、neighbour の行に $-\boldsymbol J_{oo},-\boldsymbol J_{on}$(`face_pu_blocks`)。ドナー・RC 係数・平均勾配・輸送係数・HoT は固定、1 次のドナーの値で作る(2 次の移流でも同じ)。
- 境界面: $\boldsymbol J_b$ の左上 4×4 を対角ブロックへ。並進周期の面は除く。
- 擬似時間: 対角ブロックに $\frac{V_i}{\Delta t_i}\mathrm{diag}(0,\rho_i,\rho_i,\rho_i)$(式 (57))。任意で連続式にも $V_i/(\Delta t_iT_i)$(`continuity_pseudo_time`。論文にはない。既定は使わない)。
- 閉じた領域: 連続式の行の和が 0 になって系が特異になるので、体積が最大のセルの連続式を Δp = 0 に置き換える。更新後に全質量を合わせる(§2 の 5)。
- 更新の安全策(論文に記述なし):
  - 緩和係数 `relaxation`(既定 1)
  - 1 回の更新で p・T を下げる割合の上限 `max_relative_decrease`(既定 0.5。セルごとに切る)
  - p・T の下限 `pressure_floor`・`temperature_floor`(既定 0 = 使わない)
  - 線形ソルバーの相対残差が `reject_linear_residual`(既定 0.1)を超えたら、その反復の更新を捨てて状態・$\widehat{\dot m}$・境界面の値を W^m に戻し、次の反復の CFL を半分にする(AMGCL が破綻して例外を出した場合も同じ)
- 線形ソルバー(`linear_solver.*`): AMGCL 1.5.0(ヘッダのみ、Boost なし、OpenMP)。値の型 `static_matrix<double,4,4>`、反復法は GMRES(30)(既定)か BiCGStab、前処理は ILU0(既定)・AMG(平滑化集約 + ILU0)・CPR(圧力の部分系(ブロックの最初の未知数)に AMG(平滑化集約 + ILU0)、全体に ILU0)。初期値 0、相対許容誤差 `linear_rtol`(既定 1e-3)、上限 `linear_max_iterations`(既定 200)。既定の組み合わせは報告 §5 の比較で決めた。全 12 通り(ブロック・スカラー × 反復法 × 前処理)が真の残差 1e-9 まで解けることを試験している(`test_macro_linear_solver.cpp`)。

## 4. 温度の線形系

- スカラーの CSR(同じ構造)。内部面は式 (54)(セル i から見た 1 次の ṁ と $C_\kappa=\kappa_fA/d_n$)、境界面は式 (55)(`temperature_boundary_coefficient`)、対角に $M_{T,i}=\tfrac32\rho_iV_i/\Delta t_i$。
- 面の量(ṁ、ドナー、t、輸送係数、HoT、非直交部)は温度の段の初めの評価の値で固定。
- 右辺 $-\mathcal R_T$。$\mathcal R_T$ は保存量の残差の変換(式 (51))で作る。内部面ごとの式 (52) の和と一致する(試験済み)。

## 5. 境界(付録 B・C)

### 5.1 フラックスとヤコビアン

`boundary_flux.hpp`(半空間フラックスとヤコビアンは `physics/half_space_flux.hpp`)。

| 種類 | $\widetilde{\boldsymbol F}^-$ | ヤコビアン $\boldsymbol J_b$ |
|---|---|---|
| 拡散壁(壁温 T_w、壁速度は任意) | $A\tilde\rho\,\boldsymbol F^-_{eq}(\boldsymbol W^{(1)}_\Gamma)$、$\tilde\rho=F^+_\rho/(A\sqrt{T_w/2\pi})$(式 (75)、$j_\Gamma=0$)。$\boldsymbol W^{(1)}_\Gamma=(T_w,\boldsymbol u_{w,t},T_w)$(壁速度は面ごとに法線成分を除く) | $(\boldsymbol I+\sqrt{2\pi/T_w}\boldsymbol F^-_{eq}(\boldsymbol W^{(1)})\boldsymbol e_\rho^T)A\boldsymbol J^+_{eq}$(式 (81)) |
| 圧力入口・出口 | $A\boldsymbol F^-_{eq}(\boldsymbol W_{res})$、$\boldsymbol W_{res}$ は式 (64)(65)。p_out = 0 なら 0 | $A\boldsymbol J^+_{eq}+A\boldsymbol J^-_{eq}(\boldsymbol W_{res})\boldsymbol J_{res}$(式 (82)–(84)) |
| 真空 | 0 | $A\boldsymbol J^+_{eq}$ |
| 対称面 | $\boldsymbol M\boldsymbol F^+$(質量・エネルギーは符号反転、運動量は $2\boldsymbol n\boldsymbol n^T-\boldsymbol I$) | $(\boldsymbol I+\boldsymbol M)A\boldsymbol J^+_{eq}$ |
| 遠方場(論文の B.4。製造解法の試験用、面ごとの状態も可) | $A\boldsymbol F^-_{eq}(\boldsymbol W_\infty)$ | $A\boldsymbol J^+_{eq}$ |
| 並進周期 | 流束の和・ヤコビアン・Λ から除く。勾配の境界値はセルの値(勾配 0) | — |

全種類について、$\boldsymbol F_b(\boldsymbol W)$ の中心差分と $\boldsymbol J_b$ が相対 1e-8 未満で一致する(`test_macro_jacobian.cpp`)。

### 5.2 境界面の状態 $\boldsymbol W_{i,b}$

| 設定 | 内容 | 既定 |
|---|---|---|
| `boundary_reconstruction`(GSIS の段) | `limited`: 制限付きの線形再構成(論文の「(15) と同じ」)/ `walls`: 壁と対称面だけ再構成、開いた境界は W_i / `cell`: すべて W_i | `limited` |
| `standalone_boundary_reconstruction`(単独モード) | 同上 | `cell` |
| `boundary_jacobian` | `cell`: $\boldsymbol W_{i,b}=\boldsymbol W_i$ で評価(論文)/ `reconstruction`: 再構成のセル自身への依存 $\partial W_{b,v}/\partial W_{i,v}=1-\varphi_v(\sum_kc_k)\cdot(\boldsymbol x_b-\boldsymbol x_i)$ を列に掛ける(リミッタと隣のセルは固定。[0.5, 2] に制限) | `cell` |

単独モードの既定を `cell` にした理由は `docs/phase3_report.md` §3(一様な状態から始めると、再構成を使う設定は発散・停滞する。管・箱で確認)。

### 5.3 境界セルの勾配に使う境界面の値(仮定 A7)

- GSIS の段: `face_state`(運動論の境界面分布 f_b のモーメント [ρ, u, T])を (p, u, T) にして使い、段の間は固定する。ρ ≤ 0 などの退化した値は W_i にする。並進周期の面は常に W_i。
- 単独モード(`standalone_face_state`): `cell`(既定。W_b = W_i、評価のたびにいまのセルの値)/ `moments`(流出側は再構成した状態の Maxwell 分布、流入側は境界条件の分布の和のモーメント。運動論側の face_state のマクロ版。前回の評価の値を使うラグ付き)/ `wall_moments`(壁と対称面だけ `moments`)。`moments` 系は一様な状態からの単独の計算で発散しやすい(報告 §3)。
- 境界面の値はリミッタの最大・最小にも入る(運動論側と同じ)。

## 6. 移流項の精度

- `advection = "upwind1"`: ドナーセルの値(式 (27)(28))。フェーズ 4 までの既定
- `"upwind2"`(フェーズ 5c から既定。GSIS の段と `pbc-run`。初期化は `init_advection` の `"upwind1"` のまま): 下の 2 次の再構成。1 次は GSIS の不動点を動かし、管 δ=10 で入口と出口の質量流量が 4% 食い違った(`docs/phase5b_d10_report.md` §4.4)
- `"upwind2"`: (p, u, T) を両側から面へ制限付きで再構成し、$\dot{\mathcal V}^{RC}$ の符号で風上側を選んで ρ_up, u_up, H_up に使う。$\mathcal K$ も再構成した u_up で計算する。ヤコビアンは常に 1 次。
- 再構成で p・T が正でなくなったときは、その面だけ 1 次にする。

## 7. 擬似時間と CFL

- GSIS の段: 固定 CFL(`[gsis].cfl_pbc`、論文は 500)。
- 単独モードで一様な状態から始めるとき(`pbc-run`、初期化): `PbcStageOptions::cfl_start > 0` で残差に応じて立ち上げる(SER)。$\mathrm{CFL}_m=\min(\mathrm{CFL},\ \mathrm{CFL}_{m-1}\cdot g,\ \max(\mathrm{CFL}_0,\ \mathrm{CFL}_0\,r_0/r_m))$、r は質量・運動量・エネルギーの残差の和、g = `cfl_growth`(既定 2)。`pbc-run` の既定は CFL_0 = 5。
- 更新を捨てたときは次の CFL を半分にし、その後は 1 反復で g 倍まで戻す(固定 CFL の段でも同じ)。

## 8. 決定性

- 自前のループ: 面ごとに面の配列へ書き、セルは CSR の順に足す。総和は固定長(2048)の区間の和を順に足す。
- AMGCL: 反復法の内積を自前の決定的な内積(同じ区間分け)に置き換えた。ILU0 の三角解法は、AMGCL が 4 スレッド未満で逐次版・以上で並列版(行の中の足し方が違う)を選ぶので、常に並列版(レベル分割。行ごとの計算順序はスレッド数によらない)にした。
- 試験: 同じ入力で 2 回、スレッド数 4 と 1 で、状態と線形ソルバーの反復回数がビット単位で一致する(`test_macro_solver.cpp`)。

## 9. パラメータ(`[macro]`)

| キー | 既定 | 内容 |
|---|---|---|
| `advection` | `upwind2`(フェーズ 5c で `upwind1` から変更) | 移流項(`upwind1` / `upwind2`) |
| `linear_solver` | `gmres` | `gmres` / `bicgstab` |
| `preconditioner` | `ilu0` | `ilu0` / `amg` / `cpr`(温度の系は cpr のとき ilu0) |
| `linear_rtol` | 1e-3 | 線形ソルバーの相対許容誤差 |
| `linear_max_iterations` | 200 | 線形ソルバーの反復の上限 |
| `gmres_restart` | 30 | GMRES の再始動 |
| `pbc_tolerance` | 1e-6 | PBC 内側の収束判定(更新量 ε) |
| `relaxation` | 1 | 更新の緩和係数 |
| `init_iterations` | 50 | 初期化(単独モード)の PBC 反復数(フェーズ 4 が使う) |
| `init_advection` | `upwind1` | 初期化(GSIS の PBC 単独)の移流項(フェーズ 4 で追加。GSIS の段は `advection`) |
| `limiter_k` | 5 | Venkatakrishnan の K |
| `lsq_weight_power` | 0 | 最小二乗の重み $|r|^{-p}$ |
| `max_relative_decrease` | 0.5 | 1 回の更新で p・T を下げる割合の上限 |
| `pressure_floor`・`temperature_floor` | 0 | p・T の下限(無次元。0 = 使わない) |
| `reject_linear_residual` | 0.1 | これより大きい相対残差の線形解は捨てる |
| `boundary_reconstruction` | `limited` | GSIS の段の $\boldsymbol W_{i,b}$(§5.2) |
| `standalone_boundary_reconstruction` | `cell` | 単独モードの $\boldsymbol W_{i,b}$ |
| `boundary_jacobian` | `cell` | 境界ヤコビアン(§5.2) |
| `standalone_face_state` | `cell` | 単独モードの勾配の境界値(§5.3) |
| `hot_interpolation`・`gsis_face_state`・`gsis_continuity_pseudo_time`・`gsis_freeze_kn` | `face`・`kinetic`・false・0 | GSIS の段の選択肢(フェーズ 4 で追加。`docs/design_gsis.md` §3) |

## 10. `gsis-solver pbc-run`

```
gsis-solver pbc-run <case.toml> [--out <dir>] [--iterations N] [--cfl X] [--cfl-start X] [--cfl-growth G]
                    [--tolerance X] [--advection upwind1|upwind2] [--threads N] [--print-every N] [--no-vtu]
                    [--cells-csv] [--restart <cells.csv>] [--continuity-pseudo-time]
```

- 単独モードで一様な状態(p = p_ref、u = 0、T = T0)から定常まで回す(`--restart` で cells.csv から再開)。既定は 2000 反復、CFL は `[gsis].cfl_pbc` まで SER で立ち上げる(CFL_0 = 5、g = 2)、収束判定は `[macro].pbc_tolerance`。
- 出力(既定は `runs/macro/<ケース名>/`、カレントディレクトリから): `history.csv`(反復、CFL、残差、欠損、更新量、線形ソルバーの反復数と残差、切ったセル数、捨てた反復、パッチごとの外向きの質量流量(無次元)、時間)、`cells.vtu`(p, rho, U, T, Ma, Kn_local、SI)、`boundary.vtu`(質量流束、運動量流束、エネルギー流束、せん断、境界面の状態、SI)、`summary.json`(反復数、収束、残差、線形ソルバー、時間の内訳、パッチごとの質量流量(kg/s と無次元)、`delta_rp` があれば無次元質量流量 $\dot M$ = 入口と出口の平均)、`--cells-csv` で `cells.csv`(セル中心の座標(L_ref 単位)と無次元の p, u, T, ρ)と `cells_residual.csv`。
- 終了コード: 計算が最後まで進めば 0(収束しなくても 0。要約の `converged` で判断)、エラー 1、引数の誤り 2。

## 11. 完了条件(フェーズ 3)と結果

| 試験 | 合格の目安 | 結果(詳細は報告) |
|---|---|---|
| 内部面のヤコビアン | ドナーと RC 係数を固定した 1 次のフラックスの差分近似と一致 | 相対 1e-8 未満 |
| 境界のヤコビアン | 各種類の差分近似と一致 | 全 8 設定で相対 1e-8 未満 |
| 温度のヤコビアン | 差分近似と一致 | 内部面・境界とも相対 1e-8 未満。式 (51) の変換と式 (52) の和は 1e-12 で一致 |
| 製造解法 | 六面体・四面体・歪んだ六面体で誤差が減る(拡散 2 次、1 次風上 1 次) | 拡散: u・T は全メッシュで約 2 次、p は六面体 1.8 次・歪んだ六面体と四面体で約 1 次(面の圧力 (28) に歪みの補正がない)。移流: upwind1 約 1 次、upwind2 は六面体で約 2 次 |
| NSF のみの管(δ=1000、単独) | CFL = 500 で収束、$\dot M\approx1.267$ | 26 反復・65 秒で収束(SER で CFL 5 → 500)、$\dot M$ = 1.2905(+1.9%、すべり 2λ の効果程度)。δ=100 は 37 反復・37 秒、0.8965(DSMC 0.898)。報告 §4 |
| 決定性 | 同じ入力で同じ結果(反復回数を含む) | スレッド数 4 と 1 でもビット一致 |
