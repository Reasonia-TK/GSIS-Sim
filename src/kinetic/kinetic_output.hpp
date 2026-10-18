#pragma once
// 運動論ソルバーの結果の書き出し(cis-run が使う)。値は SI 単位に戻して書く(spec §4.6)。
//   - セルの VTU: rho, u, T, p, sigma(6 成分)、q、Ma、Kn_local
//   - 境界面の VTU: 質量流束、運動量流束(法線応力・せん断)、エネルギー流束、境界面の分布の ρ, u, T
//   - 履歴の CSV: 反復、ε、残差、パッチごとの質量流量、時間の内訳

#include <filesystem>
#include <memory>
#include <span>
#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "core/json.hpp"
#include "gsis/interfaces.hpp"
#include "io/csv_writer.hpp"
#include "kinetic/kinetic_setup.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"
#include "mesh/section_cut.hpp"

namespace gsis {

// セルの VTU(mesh は元のメッシュ)
// residual は任意(セルごとの残差。KineticSolver::residual_per_cell。空なら書かない)。
// hot は任意(GSIS の高次項 HoT。与えたら hot_sigma(6 成分)・hot_q を SI で書く)
void write_kinetic_cell_vtu(const std::filesystem::path& path, const PolyMesh& mesh, const KineticMoments& m,
                            const kinetic::KineticSetup& setup, std::span<const double> residual = {},
                            const HigherOrderTerms* hot = nullptr);

// 境界面の VTU(mesh・geometry は元のメッシュ)
void write_kinetic_boundary_vtu(const std::filesystem::path& path, const PolyMesh& mesh, const MeshGeometry& geometry,
                                const BoundaryKineticFluxes& fluxes, const kinetic::KineticSetup& setup);

// 履歴の CSV(1 反復 1 行)
class KineticHistoryWriter {
 public:
  // gpu_memory_bytes: このプロセスが GPU に確保した量(最後の列 gpu_memory_bytes に毎行書く。CPU の実行は 0)
  // n_sections: 断面の質量流量の列の数(フェーズ 5c。0 なら書かない)
  KineticHistoryWriter(const std::filesystem::path& path, const kinetic::KineticSetup& setup,
                       std::size_t gpu_memory_bytes = 0, std::size_t n_sections = 0);
  // info は sweep の後の記録(収束して sweep しなかった最後の行は residual なし)。
  // section_flow_nd は info と同じ f の断面の質量流量(無次元。KineticSolver::section_mass_flow)
  void write(const KineticIterationInfo& info, double elapsed_total_s, std::span<const double> section_flow_nd = {});

 private:
  const kinetic::KineticSetup& setup_;
  std::unique_ptr<CsvWriter> csv_;
  double first_residual_ = 0.0;
  std::size_t gpu_memory_bytes_ = 0;
  std::size_t n_sections_ = 0;
};

// 質量流量の換算: 無次元 → kg/s
double mass_flow_to_si(const kinetic::KineticSetup& setup, double nondimensional);

// 質量流量の表示用の換算(フェーズ 5c): 管(delta_rp を与えたケース)なら無次元質量流量 Ṁ、そうでなければ無次元の値のまま
double mass_flow_display(const kinetic::KineticSetup& setup, double nondimensional);
// mass_flow_display の列名の接頭辞("M_dot_" か "mdot_nd_")
std::string mass_flow_display_prefix(const kinetic::KineticSetup& setup);

// 境界の質量の釣り合い(フェーズ 5c)。patch_flow_nd はパッチごとの外向きの質量流量(無次元)。
//   inflow = Σ_p max(−F_p, 0)、outflow = Σ_p max(F_p, 0)、imbalance = (outflow − inflow) / max(inflow, outflow)
// 管なら imbalance = (Ṁ_出口 − Ṁ_入口) / Ṁ(壁の正味の流量は 0)。流れがなければ imbalance は NaN
struct MassBalance {
  double inflow = 0.0;
  double outflow = 0.0;
  double imbalance = 0.0;
  bool valid = false;
};
MassBalance mass_balance(std::span<const double> patch_flow_nd);

// 流量の並び(断面と入口・出口)のばらつき (max − min) / |平均|。2 つ未満か平均が 0 なら NaN
double flow_spread(std::span<const double> flows);

// [output] の断面の指定(フェーズ 5c): mass_flow_sections(位置の並びの文字列、L_ref 単位。例 "0, 2.5, 5, 7.5, 10")と
// mass_flow_section_axis("x"・"y"・"z"、既定 "x")。[output] の知らないキーはエラー。節がなければ断面なし
SectionSpec parse_output_sections(const CaseConfig& config);

// cells.csv(cis-run・run の --cells-csv): セル番号、セル中心(L_ref 単位)、無次元の体積、無次元のセル値
// (rho, ux, uy, uz, T, p, σ 6 成分, q 3 成分)と residual(空なら 0)
void write_kinetic_cells_csv(const std::filesystem::path& path, const PolyMesh& mesh, const MeshGeometry& geometry,
                             const KineticMoments& m, double length_scale, std::span<const double> residual);

// cells.csv からセルのマクロ量を読む(列 cell, rho, ux, uy, uz, T を使う。p = ρT)。全セルの値が要る
CellMacroState read_kinetic_cells_csv(const std::filesystem::path& path, Index n_cells);

// パッチごとの境界流束の要約(summary.json の "patches" と、管の無次元質量流量)
struct KineticPatchSummary {
  json::Value patches;        // パッチごとの名前・種類・面数・面積・質量流量・運動量流束・エネルギー流束・境界面の平均
  std::vector<double> mdot;   // パッチごとの外向きの質量流量(無次元)
  double mdot_in = 0.0;       // 圧力入口から入る質量流量(無次元、正)
  double mdot_out = 0.0;      // 圧力出口・真空から出る質量流量(無次元、正)
  bool has_in = false;
  bool has_out = false;
};
KineticPatchSummary summarize_kinetic_patches(const PolyMesh& mesh, const MeshGeometry& geometry,
                                              const BoundaryKineticFluxes& fluxes,
                                              const kinetic::KineticSetup& setup);

// 管の無次元質量流量 Ṁ = ṁ / (√π L_ref² p_in / √(2 R T0))(summary.json の "mass_flow")。
// mdot_in・mdot_out は無次元の質量流量(正)。p_in が決まっていない(delta_rp がない)か、入口も出口もなければ null
json::Value tube_mass_flow_json(const kinetic::KineticSetup& setup, double mdot_in, bool has_in, double mdot_out,
                                bool has_out);

}  // namespace gsis
