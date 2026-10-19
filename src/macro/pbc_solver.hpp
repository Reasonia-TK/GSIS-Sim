#pragma once
// PBC マクロソルバー(論文 §3.3 の式 (18)–(58)、付録 B・C。docs/design_pbc.md)。
//
// フェーズ 4(GSIS)から使う API:
//   PbcSolver solver = PbcSolver::from_case(cfg, derived, mesh, geometry, params);
//   // 初期化(NSF のみ、単独モード)。一様な状態から始めるので CFL を残差に応じて立ち上げる(SER)
//   PbcStageOptions init;
//   init.max_iterations = static_cast<int>(params.init_iterations);
//   init.cfl = cfg.gsis.cfl_pbc;
//   init.cfl_start = 5.0;
//   solver.solve_standalone(state, init);
//   // GSIS の反復ごと(W^{n+1/2}、σ、q と境界の F^{±,0}・face_state を渡し、W^{n+1} を受け取る)
//   PbcStageReport rep = solver.solve_gsis_stage(moments, fluxes, cfg.gsis.max_pbc, cfg.gsis.cfl_pbc, out);
//
// - すべて無次元(spec §3.2。R = 1、p = ρT)、FP64。メッシュは SI のまま渡し、内部で L_ref で割る。
// - セルの並びはメッシュのセル番号、境界面の並びは境界面の番号(面番号 − n_internal_faces)。
// - 決定的: 面ごとの寄与を面の配列に書き、セル → 面の CSR の順にセルへ集める(atomic なし)。
//   総和はスレッド数によらない順序で行う。線形ソルバー(AMGCL)の内積も同様。
// - PbcSolver はメッシュ(PolyMesh)を参照で持つ。メッシュは PbcSolver より長く生きていること。
// - ラグ付きの面質量流量 ṁ̂(式 (26) の a_u に使う)は PbcSolver の中に持ち越す(GSIS の反復をまたぐ)。

#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "core/timer.hpp"
#include "gsis/interfaces.hpp"
#include "macro/boundary_flux.hpp"
#include "macro/face_flux.hpp"
#include "macro/linear_solver.hpp"
#include "macro/macro_params.hpp"
#include "mesh/fv_geometry.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

// 気体の性質(無次元、R = 1)
struct MacroGas {
  double mu_ref = 1.0;       // 粘性則の基準温度での無次元の粘性係数
  double T_ref = 1.0;        // 粘性則の基準温度(無次元)
  double omega = 0.5;        // 粘性のべき指数(式 (11))
  double Pr = 2.0 / 3.0;     // Prandtl 数

  double mu(double T) const { return mu_ref * std::pow(T / T_ref, omega); }
  double kappa(double T) const { return 2.5 / Pr * mu(T); }  // κ = 5/2 μ R / Pr(式 (10))
};

// 1 回の PBC 反復の診断量
struct PbcIterationInfo {
  int iteration = 0;                     // 段の中の反復番号 m(0 から)
  double cfl = 0.0;                      // この反復の CFL
  // 反復の初めの状態 W^m での残差の 2 ノルム(面積を掛けたフラックスの和、無次元)。
  // エネルギーは運動エネルギーの欠損 𝒦 を引いた R_E − Σ𝒦(式 (51)。収束すると R_E は Σ𝒦 に近づく)
  double residual_mass = 0.0;
  double residual_momentum = 0.0;
  double residual_energy = 0.0;
  double kinetic_energy_defect = 0.0;    // Σ_i Σ_j 𝒦_{i,ij}(全セルの和、符号付き)
  double residual_temperature = 0.0;     // 温度の段の R_T(p, u を更新した後)
  // 更新量 ‖W^{m+1} − W^m‖ / ‖W^m‖(体積重み)。速度は (|u|² + T) で規格化する
  double update_p = 0.0;
  double update_u = 0.0;
  double update_T = 0.0;
  double update_max() const { return std::max(update_p, std::max(update_u, update_T)); }
  // 線形ソルバー
  long long linear_iterations_pu = 0;
  double linear_residual_pu = 0.0;
  long long linear_iterations_T = 0;
  double linear_residual_T = 0.0;
  // 正値を保つために更新量を縮めたセルの数(下限で止めたセルを含む)
  long long clipped_p = 0;
  long long clipped_T = 0;
  // 線形ソルバーが収束せず、この反復の更新を捨てた(状態は W^m のまま。次の反復は CFL を半分にする)
  bool rejected = false;
  // パッチごとの外向きの質量流量(W^m での境界フラックス、無次元)
  std::vector<double> patch_mass_flow;
  double seconds = 0.0;                  // この反復の所要時間
};

// 1 回の PBC 段の結果
struct PbcStageReport {
  int iterations = 0;
  bool converged = false;                // 更新量が許容値未満で止まった
  double final_update = 0.0;
  double initial_residual_mass = 0.0;
  // 段の最後の状態で評価し直した残差(2 ノルム)と、パッチごとの外向きの質量流量(無次元)
  double final_residual_mass = 0.0;
  double final_residual_momentum = 0.0;
  double final_residual_energy = 0.0;    // R_E − Σ𝒦
  double final_kinetic_energy_defect = 0.0;
  std::vector<double> final_patch_mass_flow;
  long long linear_iterations_pu = 0;    // 合計
  long long linear_iterations_T = 0;
  long long max_linear_iterations_pu = 0;
  long long linear_not_converged = 0;    // 許容誤差に届かなかった線形ソルバーの回数
  long long rejected_iterations = 0;     // 更新を捨てた反復の数
  long long frozen_cells = 0;            // 段の間は固定したセルの数(gsis_freeze_kn)
  bool stopped = false;                  // should_stop で途中で終えた
  TimingTable timing;
};

// PBC 段の入力
struct PbcStageInput {
  const CellMacroState* initial = nullptr;              // W^0(p, u, T を使う。必須)
  const HigherOrderTerms* hot = nullptr;                // HoT(null なら 0)
  const BoundaryKineticFluxes* boundary = nullptr;      // 境界の F^{±,0} と face_state(null なら単独モード)
  const std::vector<std::array<double, 5>>* source = nullptr;  // 試験用の源項 b_i(残差は Σ F − b_i)
};

struct PbcStageOptions {
  int max_iterations = 10;
  double cfl = 500.0;
  // CFL の立ち上げ(単独モードで一様な状態から始めるときなど。GSIS の段では使わない)。
  // cfl_start > 0 なら、残差に応じて CFL を上げる(SER: switched evolution relaxation):
  //   CFL_m = min(cfl, CFL_{m−1} × cfl_growth, max(cfl_start, cfl_start × r_0 / r_m))
  //   r は質量・運動量・エネルギー(R_E − Σ𝒦)の残差の 2 ノルムの和。残差が増えれば CFL も下がる。
  // 線形ソルバーの失敗で更新を捨てたときは、次の CFL を半分にし、その後は 1 反復で cfl_growth 倍まで戻す
  // (立ち上げなしの場合も同じ)。
  double cfl_start = 0.0;
  double cfl_growth = 2.0;
  // 連続式にも擬似時間項 V/Δt ∂ρ/∂p = V/(Δt T) を入れる(論文の式 (57) にはない。一様な状態から
  // 始める単独モードの立ち上げ用。定常解は変わらない)
  bool continuity_pseudo_time = false;
  double tolerance = 1e-6;                              // 更新量の収束判定
  std::function<void(const PbcIterationInfo&)> on_iteration;
  // 反復の終わりに呼ぶ。true を返したらその反復で段を終える(穏やかな停止。PbcStageReport::stopped)
  std::function<bool()> should_stop;
};

// GSIS の補正の低域フィルタの記録(PbcSolver::filter_correction)
struct CorrectionFilterReport {
  bool applied = false;
  long long linear_iterations = 0;  // 5 つの系の線形ソルバーの反復の合計
  bool converged = true;            // すべての系が許容誤差に届いた
  double max_length = 0.0;          // 使ったフィルタの長さ ℓ の最大(無次元)
  double seconds = 0.0;
};

// 最後に評価した境界面の量(出力・診断用。境界面の番号順)
struct PbcBoundaryFaceData {
  std::vector<Vec5> flux;        // F_b(面積を掛けた値)
  std::vector<Vec5> face_state;  // 境界セルの勾配に使った境界面の値 (p, u, T)
  std::vector<Vec5> reconstructed;  // 境界面へ再構成した内部側の状態 W_{i,b}
};

class PbcSolver {
 public:
  // mesh・geometry は SI(長さ m)。length_scale(L_ref [m])で割って無次元にする。
  // patch_bcs はメッシュのパッチと同じ並び。
  PbcSolver(const PolyMesh& mesh, const MeshGeometry& geometry, double length_scale, const MacroGas& gas,
            std::vector<macro::MacroBoundaryCondition> patch_bcs, const MacroParams& params);

  // ケース定義から作る(境界条件・気体を無次元にする)
  static PbcSolver from_case(const CaseConfig& config, const DerivedQuantities& derived, const PolyMesh& mesh,
                             const MeshGeometry& geometry, const MacroParams& params);

  Index n_cells() const { return mesh_.n_cells; }
  Index n_boundary_faces() const { return mesh_.n_boundary_faces(); }
  const MacroParams& params() const { return params_; }
  void set_params(const MacroParams& params);
  const MacroGas& gas() const { return gas_; }
  const std::vector<macro::MacroBoundaryCondition>& boundary_conditions() const { return patch_bc_; }
  // 遠方場のパッチの面ごとの状態 (p, u, T)(製造解法の試験用。空なら一様の値)
  void set_far_field_states(int patch, std::vector<Vec5> states);
  bool closed_domain() const { return closed_domain_; }
  // 無次元の幾何量
  const MeshGeometry& geometry() const { return geo_; }

  // 高次項 HoT(式 (12))。σ^NSF・q^NSF は W^{n+1/2} のセル中心の最小二乗勾配(境界面の値は face_state)から
  HigherOrderTerms compute_hot(const KineticMoments& moments, const BoundaryKineticFluxes& boundary);

  // PBC 段(m = 0, ..., M−1)。結果を out に書く
  PbcStageReport run_stage(const PbcStageInput& input, const PbcStageOptions& options, CellMacroState& out);

  // GSIS の 1 反復分: HoT を作り、境界の整合補正付きで PBC 段を回す
  PbcStageReport solve_gsis_stage(const KineticMoments& moments, const BoundaryKineticFluxes& boundary,
                                  int max_iterations, double cfl, CellMacroState& out,
                                  std::function<void(const PbcIterationInfo&)> on_iteration = {});

  // 単独モード(NSF のみ: HoT = 0、F^{±,0} = Ŧ^±(W⁰_b) で整合補正 0)。state を上書きする。
  // options.tolerance が 0 以下なら params().pbc_tolerance を使う
  PbcStageReport solve_standalone(CellMacroState& state, const PbcStageOptions& options);
  PbcStageReport solve_standalone(CellMacroState& state, int max_iterations, double cfl);

  // GSIS の補正 W − W_half の低域フィルタ(フェーズ 4 で追加。論文にはない。docs/design_gsis.md §3)。
  //   (V_i − ∇·(ℓ² ∇)) δ̃ = V_i δ を解き(境界面は流束 0)、W = W_half + δ̃ にする。
  //   δ は [ln ρ, u_x, u_y, u_z, ln T] の差(密度と温度は比で扱うので正のまま)。
  //   ℓ_i = min(length_factor × λ_i, max_length)、λ_i = μ(T)/p √(πT/2) は W_half の局所平均自由行程
  //   (式 (8) の局所 Kn × L_ref)。面の係数は ℓ² の調和平均 × A/d_n。
  //   波長が平均自由行程より短い成分(NSF の合成方程式が過大に応える成分)を 1/(1 + k²ℓ²) に弱め、
  //   長い波長の補正はほぼそのまま通す。収束した解(補正 0)は変わらない。
  //   直前の GSIS 段で固定したセル(gsis_freeze_kn)は δ̃ = 0 にする(隣のセルから見ると Dirichlet 条件)。
  //   min_cells > 0 なら ℓ_i に下限 min_cells × h_i(h_i = V_i^{1/3}、セルの大きさ)を置く(フェーズ 5b)。格子規模の
  //   補正(2〜4 セル周期)を 1/(1 + 4 min_cells²) 程度に弱める。連続流の領域(ℓ = λ ≪ h)で、PBC 段が格子規模の
  //   モードを 1 反復ごとに符号を変えて増幅するのを抑える(管 δ = 1000 の上流槽。docs/phase5b_d1000_report.md)
  // length_factor <= 0 かつ min_cells <= 0 なら何もしない。max_length <= 0 なら上限なし。w の p は ρT に直す
  CorrectionFilterReport filter_correction(const CellMacroState& half, CellMacroState& w, double length_factor,
                                           double max_length, double min_cells = 0.0) const;

  // ラグ付きの ṁ̂ を捨てる(次の評価は式 (26) の下の初期値から)
  void reset_lagged_mass_flow() { has_mdot_hat_ = false; }

  // 与えた状態で残差だけを評価する(単独モード・HoT = 0・境界面の値はセルの値から作る。試験用)。
  // R はセルごとの [質量, 運動量 x, y, z, エネルギー](面積を掛けたフラックスの和)
  std::vector<std::array<double, 5>> evaluate_residual(const CellMacroState& state);

  // 診断・試験用(単独モードの扱い: HoT = 0、整合補正 0、境界面の値はセルの値から作る)。
  // ṁ̂ と境界面の値は呼ぶ前のまま使い、更新しない(残差を差分するときに同じ条件にするため)。
  //   residual_frozen: 残差 R(セルごとの 5 成分)
  //   linearize_pu:    圧力–速度の線形系 J^pu + M_pu(cfl から Δt を作る)と右辺 −R^pu
  //   linearize_T:     温度の線形系 J_T + M_T と右辺 −R_T(p, u は state のまま)
  std::vector<std::array<double, 5>> residual_frozen(const CellMacroState& state);
  void linearize_pu(const CellMacroState& state, double cfl, macro::BlockCsrMatrix& A, std::vector<double>& rhs);
  void linearize_T(const CellMacroState& state, double cfl, macro::BlockCsrMatrix& A, std::vector<double>& rhs);

  // 最後の評価の量(段の最後の状態で評価したもの)
  const PbcBoundaryFaceData& last_boundary() const { return boundary_out_; }
  const std::vector<std::array<double, 5>>& last_residual() const { return R_; }
  const std::vector<double>& last_kinetic_energy_defect() const { return defect_; }
  // 最後の段で使った HoT(単独モードなら 0。出力用)
  HigherOrderTerms last_hot() const;
  // 最後の状態・セル勾配の NSF 構成則。分布関数を確保せず共通出力を作る。
  KineticMoments nsf_moments() const;
  // 最後の評価のパッチごとの外向きの質量流量(無次元)
  std::vector<double> patch_mass_flow() const;
  // 最後の評価の内部面の質量流束 F_ρ(内部面の番号順、owner → neighbour の向き、無次元。フェーズ 5c の断面の流量用)。
  // 評価の前は空
  std::vector<double> internal_face_mass_flux() const;
  // 最後の評価のパッチごとの外向きのフラックスの和 [質量, 運動量 x, y, z, エネルギー](無次元)
  std::vector<Vec5> patch_flux() const;
  // 診断用: 最後の段の初めの状態 W⁰ での残差(セルごとの 5 成分)と、セルを通る質量流量 Σ|ṁ_f|(内部面と境界面)
  const std::vector<std::array<double, 5>>& initial_residual() const { return R0_; }
  const std::vector<double>& initial_mass_throughput() const { return throughput0_; }

 private:
  struct FaceWork;
  struct BoundaryWork;

  void build_pattern();
  void load_state(const CellMacroState& s);
  void store_state(CellMacroState& s) const;
  void set_face_state_from_kinetic(const BoundaryKineticFluxes& boundary);
  void set_face_state_from_cells();
  void compute_gradients();
  void compute_limiters();
  void build_face_hot();  // hot_interpolation = "face" の面の HoT(W⁰ の勾配から)
  Vec5 reconstruct_to(Index cell, const Vec3& x) const;
  void evaluate();
  void assemble_pu(macro::BlockCsrMatrix& A, std::vector<double>& rhs, bool continuity_pseudo_time = false) const;
  void assemble_T(macro::BlockCsrMatrix& A, std::vector<double>& rhs) const;
  void apply_mass_constraint(double target_mass);
  void residual_norms(double& mass, double& momentum, double& energy, double& defect) const;
  double total_mass() const;
  macro::BoundaryFaceExtra extra_of(Index bface) const;
  bool is_periodic(Index bface) const;
  bool uses_face_moments(Index bface) const;
  bool reconstructs_to(Index bface) const;  // 境界面へ制限付きの再構成をするか(しなければ W_i)

  const PolyMesh& mesh_;
  MeshGeometry geo_;  // 無次元
  FvGeometry fv_;
  CellFaceCsr csr_;
  LsqGradient lsq_;
  MacroGas gas_;
  MacroParams params_;
  macro::LinearSolverOptions linear_;
  std::vector<macro::MacroBoundaryCondition> patch_bc_;
  std::vector<std::vector<Vec5>> far_field_faces_;  // パッチごと(遠方場の面ごとの状態)
  std::vector<int> bface_patch_;                    // 境界面 → パッチ番号
  bool closed_domain_ = false;
  Index pin_cell_ = 0;

  // ブロック CSR の構造(セルの隣接)
  std::vector<std::ptrdiff_t> row_ptr_;
  std::vector<std::ptrdiff_t> col_;
  std::vector<std::ptrdiff_t> diag_block_;    // セル → 対角ブロックの番号
  std::vector<std::ptrdiff_t> other_block_;   // CSR の要素 → (セル, 相手のセル) のブロックの番号(境界面は −1)
  std::vector<double> eps2_;                  // リミッタの ε² = (K h)³

  // 状態(無次元)
  std::vector<double> W_[5];                  // p, ux, uy, uz, T
  std::vector<std::array<double, 6>> hot_sigma_;
  std::vector<Vec3> hot_q_;
  bool has_hot_ = false;
  // hot_interpolation = "face" のときの面の HoT(内部面の番号順。GSIS の段の初めに作る)
  std::vector<std::array<double, 6>> face_hot_sigma_;
  std::vector<Vec3> face_hot_q_;
  bool face_hot_ = false;
  // gsis_freeze_kn で段の間は固定するセル(1 なら固定)
  std::vector<char> frozen_;
  Index n_frozen_ = 0;
  // 診断用(段の初めの残差と質量流量)
  std::vector<std::array<double, 5>> R0_;
  std::vector<double> throughput0_;
  const std::vector<std::array<double, 5>>* source_ = nullptr;
  bool standalone_ = true;
  bool freeze_lag_ = false;        // true なら evaluate() で ṁ̂ と境界面の値を更新しない(診断用)
  std::vector<double> mdot_hat_;
  bool has_mdot_hat_ = false;

  // 境界
  std::vector<Vec5> face_state_;   // 勾配に使う境界面の値 (p, u, T)
  std::vector<Vec5> C_plus_;
  std::vector<Vec5> C_minus_;

  // 評価の作業領域
  std::vector<Vec3> grad_[5];
  std::vector<double> lim_[5];
  std::vector<double> rho_, mu_, kappa_, cs_, a_u_, D_;
  std::vector<std::array<double, 5>> R_;
  std::vector<double> defect_, lambda_;
  std::vector<macro::InternalFaceResult> face_res_;
  std::vector<macro::FacePuJacobianData> face_jac_;
  std::vector<double> face_ckappa_;
  std::vector<double> face_lam_o_, face_lam_n_;
  std::vector<Vec5> bW_;           // 再構成した W_{i,b}
  std::vector<Vec5> bF_;           // F_b
  std::vector<Mat5> bJ_;           // J_b(W_i で評価)
  std::vector<double> bLam_;
  std::vector<double> bBnorm_;
  std::vector<Vec5> next_face_state_;  // 単独モードで次の評価に使う境界面の値
  PbcBoundaryFaceData boundary_out_;
  std::vector<double> dt_;
};

}  // namespace gsis
