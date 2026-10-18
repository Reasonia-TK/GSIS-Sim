#pragma once
// GSIS の反復(論文 §3.1 の Fig.1、式 (12)–(14)、(59)。docs/design_gsis.md)。
// 運動論ソルバー CIS(KineticSolver、GPU)と PBC マクロソルバー(PbcSolver、CPU)をつなぐ。
//
// 使い方:
//   GsisSolver solver(cfg, mesh, geometry, kinetic_params, macro_params, GsisOptions::from_case(...));
//   solver.initialize();                       // 一様な状態 → PBC 単独(NSF のみ)→ f = f^eq(W)(仮定 A9)
//   for (;;) {
//     const GsisIterationInfo& it = solver.iterate();
//     if (it.iteration >= min_iter && it.epsilon < tol) break;
//   }
//
// 1 回の GSIS 反復(iterate):
//   1. CIS を N_CIS 回: f^n → f^{n+1/2}
//   2. f^{n+1/2} のモーメント W^{n+1/2}・σ・q と、境界面の F^{±,0}・face_state(式 (35)、仮定 A7)
//   3. PBC 段: HoT(式 (12))を固定し、整合補正つきの境界で M 回まで(式 (13))→ W^{n+1}
//   4. 補正(式 (14))と負値の置換: f^{n+1} = f^{n+1/2} − f^eq(W^{n+1/2}) + f^eq(W^{n+1})
//   5. 収束の指標(式 (59)): W^{n+1} と W^n(どちらも PBC の出力。n = 0 の W^0 は初期化の状態)の
//      体積重みの相対変化
//
// - 入出力はすべて無次元量(spec §3.2、R = 1、p = ρT)、セル・境界面は元のメッシュの番号。
// - GsisSolver はメッシュ(PolyMesh)を参照で持つ(PbcSolver と同じ)。メッシュの方が長く生きること。
// - 決定的: 運動論(GPU・CPU)も PBC も総和の順序を固定しているので、同じ入力なら結果はビット単位で同じ。

#include <array>
#include <functional>
#include <limits>
#include <vector>

#include "config/case_config.hpp"
#include "gsis/interfaces.hpp"
#include "kinetic/kinetic_params.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "macro/macro_params.hpp"
#include "macro/pbc_solver.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"
#include "mesh/section_cut.hpp"

namespace gsis {

struct GsisOptions {
  int n_cis = 5;                 // GSIS 1 反復あたりの CIS 反復数 N_CIS([gsis].n_cis)
  int max_pbc = 10;              // PBC 段の反復の上限 M([gsis].max_pbc)
  double cfl_pbc = 500.0;        // PBC 段の CFL([gsis].cfl_pbc)
  long long init_iterations = 50;  // 初期化の PBC 単独の反復の上限([macro].init_iterations。0 なら初期化しない)
  double init_cfl_start = 5.0;   // 初期化の CFL の立ち上げ(SER)の初期値(docs/phase3_report.md §6 の 3)
  double init_cfl_growth = 2.0;  // 初期化の CFL の 1 反復あたりの増加の上限
  double u_floor = 1.0e-2;       // 式 (59) の u の分母の下限(√(RT0) 単位。[kinetic].convergence_u_floor)
  // 補正の低域フィルタ(PbcSolver::filter_correction。[gsis].filter_length・filter_max_length)。
  // ℓ = filter_length × 局所平均自由行程(0 なら使わない)、上限 filter_max_length(L_ref 単位。0 なら領域の対角線)
  double filter_length = 1.0;
  double filter_max_length = 0.0;
  // ℓ の下限(セルの大きさ h = V^{1/3} の倍数。[gsis].filter_min_cells、フェーズ 5b)。0 なら使わない
  double filter_min_cells = 0.0;
  // 局所 Kn による補正の減衰([gsis].damp_kn、フェーズ 5b)。w = 1/(1 + (Kn/damp_kn)²)。0 なら使わない
  double damp_kn = 0.0;
  // 質量流量を集計する断面([output].mass_flow_sections・mass_flow_section_axis、フェーズ 5c)。空なら集計しない
  SectionSpec sections;

  static GsisOptions from_case(const CaseConfig& config, const KineticParams& kinetic, const MacroParams& macro);
};

// 式 (59) の相対変化(b が前の値。u の分母は max(Σ|u_b|²V, floor² ΣV))
struct RelativeChange {
  double epsilon = 0.0;
  double rho = 0.0;
  double u = 0.0;
  double T = 0.0;
};
RelativeChange relative_change(const CellMacroState& a, const CellMacroState& b, const std::vector<double>& volume,
                               double u_floor);

// 初期化の記録
struct GsisInitReport {
  bool pbc_run = false;      // PBC 単独(NSF のみ)を回した
  bool from_state = false;   // 与えた状態から始めた
  PbcStageReport pbc;        // PBC 単独の結果(pbc_run のとき)
  std::vector<double> patch_mass_flow;  // PBC 単独の最後の状態のパッチごとの外向きの質量流量(無次元)
  double seconds = 0.0;
};

// 1 回の GSIS 反復の記録
struct GsisIterationInfo {
  long long iteration = 0;       // 終えた GSIS 反復の数(n + 1)
  long long cis_iterations = 0;  // ここまでの CIS 反復の合計
  // 式 (59): W^{n+1} と W^n の相対変化
  RelativeChange change;
  // 参考: CIS の後のモーメント W^{n+1/2} の、前の GSIS 反復との相対変化(最初の反復は NaN)
  RelativeChange change_half{std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::quiet_NaN(),
                             std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::quiet_NaN()};
  // PBC 段が W^{n+1/2} から W^{n+1} へ動かした量(式 (59) と同じ形の相対変化)
  RelativeChange pbc_change;
  // f^{n+1/2} の記録(パッチの質量流量、全質量など)。residual は最後の CIS 反復の残差、
  // timings は N_CIS 回の CIS 反復のカーネル別の時間の合計
  KineticIterationInfo kinetic;
  PbcStageReport pbc;            // PBC 段の結果
  CorrectionFilterReport filter;  // 補正の低域フィルタ(filter_length > 0 のとき)
  long long replaced_cells = 0;  // 負値の置換で f^eq(W^{n+1}) にしたセルの数
  std::vector<double> patch_mass_flow_kinetic;  // f^{n+1/2} の境界の離散流束(無次元、外向きが正)
  std::vector<double> patch_mass_flow_pbc;      // W^{n+1} での PBC の境界フラックス(無次元、外向きが正)
  // 断面を通る質量流量(フェーズ 5c、GsisOptions::sections の順。無次元、軸の正の向きが正。断面がなければ空)
  std::vector<double> section_flow_kinetic;     // f^{n+1/2} の内部面の離散流束の和
  std::vector<double> section_flow_pbc;         // W^{n+1} での PBC の内部面の質量フラックスの和
  double hot_sigma_rms = 0.0;    // HoT_σ の大きさ(体積重みの RMS、成分の 2 乗和の平方根)
  double hot_q_rms = 0.0;        // HoT_q の大きさ
  // 時間 [s]
  double t_cis = 0.0;            // CIS(N_CIS 回の prepare + sweep)
  double t_moments = 0.0;        // f^{n+1/2} のモーメントと境界の流束(GPU の集計、ホストへの転送と並べ替え)
  double t_pbc = 0.0;            // HoT と PBC 段
  double t_filter = 0.0;         // 補正の低域フィルタ
  double t_correction = 0.0;     // 補正(W のホストからの転送と GPU の計算)
  double t_total = 0.0;
};

class GsisSolver {
 public:
  GsisSolver(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry,
             const KineticParams& kinetic_params, const MacroParams& macro_params, const GsisOptions& options);
  GsisSolver(const GsisSolver&) = delete;
  GsisSolver& operator=(const GsisSolver&) = delete;

  // 初期化(仮定 A9)。
  //   initial = null: 一様な状態(p = [kinetic].initial_pressure、T = initial_temperature、静止)から
  //                   PBC 単独(NSF のみ、CFL は init_cfl_start から SER で cfl_pbc まで)を init_iterations 回まで
  //                   回し、その状態の平衡分布で f を初期化する
  //   initial あり:   その状態から(run_pbc なら PBC 単独をかけてから)f = f^eq(W) にする(再開用)
  // GSIS の反復の番号を 0 に戻す。
  // should_stop: PBC 単独の反復の終わりに呼び、true なら初期化の PBC をそこで終える(穏やかな停止)
  const GsisInitReport& initialize(const CellMacroState* initial = nullptr, bool run_pbc = true,
                                   const std::function<void(const PbcIterationInfo&)>& on_pbc_iteration = {},
                                   const std::function<bool()>& should_stop = {});

  // 1 回の GSIS 反復(先に initialize を呼ぶこと)
  const GsisIterationInfo& iterate(const std::function<void(const PbcIterationInfo&)>& on_pbc_iteration = {});

  long long iteration() const { return iteration_; }
  long long cis_iterations() const { return cis_iterations_; }
  // W^n(最後の PBC 段の出力。初期化の直後は初期状態)
  const CellMacroState& state() const { return w_; }
  // 最後の GSIS 反復の W^{n+1/2}(CIS の後のモーメント。診断用。反復の前は空)
  const CellMacroState& half_state() const { return w_half_prev_; }
  const GsisIterationInfo& last() const { return last_; }
  const GsisInitReport& init_report() const { return init_; }
  const GsisOptions& options() const { return options_; }
  KineticSolver& kinetic() { return kinetic_; }
  PbcSolver& macro() { return pbc_; }
  const std::vector<double>& cell_volumes() const { return volume_; }  // 無次元
  // 断面(元のメッシュの番号。GsisOptions::sections から作ったもの)
  const std::vector<SectionCut>& section_cuts() const { return pbc_cuts_; }

 private:
  GsisOptions options_;
  KineticParams kinetic_params_;
  KineticSolver kinetic_;
  PbcSolver pbc_;
  std::vector<double> volume_;  // 無次元のセル体積(元の番号)
  double filter_cap_ = 0.0;     // 補正のフィルタの長さの上限(無次元)
  std::vector<SectionCut> pbc_cuts_;  // 断面(元のメッシュの番号。PBC の内部面の質量フラックスを足す)

  bool initialized_ = false;
  long long iteration_ = 0;
  long long cis_iterations_ = 0;
  CellMacroState w_;              // W^n
  CellMacroState w_half_prev_;    // 前の反復の W^{n+1/2}
  bool has_half_prev_ = false;
  GsisInitReport init_;
  GsisIterationInfo last_;
};

}  // namespace gsis
