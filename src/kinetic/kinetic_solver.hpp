#pragma once
// 運動論ソルバー CIS(docs/design_cis_gpu.md)。CIS 単独の計算(cis-run)と、フェーズ 4 の GSIS の反復
// (式 (14) の補正)から使う。
//
// 使い方(CIS 単独):
//   KineticSolver solver(config, mesh, geometry, params);
//   solver.initialize_uniform(1.0, Vec3{}, 1.0);
//   for (;;) {
//     const auto& info = solver.prepare();      // f^ℓ のモーメント・ε・境界条件の更新
//     if (info.iteration > 0 && info.epsilon < tol) break;
//     solver.sweep();                           // f^ℓ → f^(ℓ+1)
//   }
//   KineticMoments m = solver.moments();
// GSIS(フェーズ 4)では、N_CIS 回の prepare + sweep の後に moments() と boundary_fluxes() を取り、
// PBC の結果で apply_gsis_correction(W^{n+1/2}, W^{n+1}) を呼ぶ。
//
// 入出力はすべて無次元量(spec §3.2、p = ρT)・元のメッシュの番号(セル、境界面)。

#include <array>
#include <memory>
#include <span>
#include <vector>

#include "config/case_config.hpp"
#include "core/vec3.hpp"
#include "gsis/interfaces.hpp"
#include "kinetic/kinetic_backend.hpp"
#include "kinetic/kinetic_params.hpp"
#include "kinetic/kinetic_setup.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"
#include "mesh/section_cut.hpp"

namespace gsis {

// 1 回の CIS 反復の記録
struct KineticIterationInfo {
  long long iteration = 0;  // f^iteration(この反復で更新する前の分布関数)の番号
  // 式 (59) の相対変化(f^iteration と f^(iteration−1) のモーメント)。最初は NaN
  double epsilon = 0.0;
  double eps_rho = 0.0;
  double eps_u = 0.0;
  double eps_T = 0.0;
  bool has_epsilon = false;
  // f^iteration の定常残差のノルム √(Σ_i Σ_k ϖ R²/V_i / Σ V)(sweep の後に入る)
  double residual = 0.0;
  bool has_residual = false;
  double total_mass = 0.0;   // 規格化の前の全質量 Σ ρ V(無次元)
  double mass_scale = 1.0;   // 閉じた領域の全質量の規格化の倍率(開いた領域では 1)
  std::vector<double> patch_mass_flow;  // パッチごとの質量流量(無次元、領域から外向きが正)
  std::array<double, 3> q_mean{};      // 熱流束の体積平均(無次元、収束の監視用)
  std::array<double, 6> sigma_mean{};  // 偏差応力の体積平均(xx, yy, zz, xy, yz, xz)
  double seconds = 0.0;      // prepare と sweep の壁時計時間
  kinetic::StepTimings timings;
  // 保存的な離散平衡分布([kinetic].equilibrium = "discrete"):補正したセル、ニュートン法が収束しなかったセル、反復数の最大
  long long deq_corrected = 0;
  long long deq_failed = 0;
  int deq_max_iterations = 0;
};

class KineticSolver {
 public:
  KineticSolver(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry,
                const KineticParams& params);
  ~KineticSolver();
  KineticSolver(const KineticSolver&) = delete;
  KineticSolver& operator=(const KineticSolver&) = delete;

  const kinetic::KineticSetup& setup() const;
  const kinetic::KineticBackend& backend() const;

  // 初期化(無次元)。分布関数を平衡分布にし、反復の番号を 0 に戻す
  void initialize_uniform(double rho, const Vec3& u, double T);
  void initialize_equilibrium(const CellMacroState& state);  // 元のセル番号(rho, ux, uy, uz, T を使う)

  // f^ℓ のモーメント・収束の指標・境界条件(圧力境界の W_b、拡散壁の ρ_Γ)を更新する。
  // 同じ f に対して何度呼んでもよい(2 回目以降は前の結果を返す)
  const KineticIterationInfo& prepare();
  // f を 1 回更新する(必要なら先に prepare する)。residual と時間を入れた記録を返す
  const KineticIterationInfo& sweep();
  long long iteration() const;

  // 現在の f のモーメントと境界面の離散半空間フラックス(元の番号)
  KineticMoments moments();
  BoundaryKineticFluxes boundary_fluxes();
  // GSIS の補正 f ← f − f^eq(W_half) + f^eq(W_new) と負値の置換(元のセル番号)。置き換えたセル数を返す
  long long apply_gsis_correction(const CellMacroState& w_half, const CellMacroState& w_new);

  // 直前の apply_gsis_correction で置き換えたセル(元のセル番号、1 なら置き換えた。補正の前は空)
  const std::vector<char>& replaced_cells() const;

  // 分布関数(元のセル番号 × 速度格子の番号の順。テスト・保存用)
  std::vector<double> distribution();
  void set_distribution(std::span<const double> f);

  // 閉じた領域で保つ全質量(無次元。開いた領域や初期化前は 0)
  double mass_target() const;

  // 直前の sweep のセルごとの残差 √(ϖ Σ_k R²)/V(元のセル番号。診断用。sweep の前は 0)
  std::vector<double> residual_per_cell() const;

  // 断面を通る質量流量(フェーズ 5c、mesh/section_cut.hpp。無次元、軸の正の向きが正)。現在の f の内部面の離散的な
  // 質量流束(CIS の残差と同じ風上側の制限付き再構成)を、断面の面について足したもの。定常で質量が釣り合っていれば
  // 入口・出口の流量と同じになる。断面の面は内部の番号付けのメッシュで選ぶ(元のメッシュで選んでも同じ面の集まり)。
  // 先に prepare する(勾配・リミッタに使う境界の流入分布を現在の f に合わせる)。spec が空なら空を返す
  std::vector<double> section_mass_flow(const SectionSpec& spec);
  // 断面の情報(面の数・面積・位置。内部の番号付けのメッシュで作ったもの)
  const std::vector<SectionCut>& section_cuts(const SectionSpec& spec);

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace gsis
