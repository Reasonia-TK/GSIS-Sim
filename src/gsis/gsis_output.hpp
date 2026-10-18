#pragma once
// GSIS の反復の記録の書き出し(gsis-solver run が使う。docs/design_gsis.md §6)。
//   - history.csv:     GSIS 反復ごとの 1 行(ε、PBC 段の要約、負値の置換、パッチごとの質量流量、時間の内訳)
//   - pbc_history.csv: PBC の反復ごとの 1 行(初期化の PBC 単独は gsis_iteration = 0)
// 列の名前は cis-run の history.csv と共通のもの(iteration, epsilon, eps_*, residual, total_mass,
// mdot_<パッチ>_kg_s・_nd, q*_mean, s*_mean, time_iter_s, time_total_s)をそろえてある。

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "gsis/gsis_solver.hpp"
#include "io/csv_writer.hpp"
#include "kinetic/kinetic_setup.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

class GsisHistoryWriter {
 public:
  // パッチの名前と種類(管の無次元質量流量 Ṁ の入口・出口の判定)は setup.patches(メッシュのパッチの順)から取る
  // gpu_memory_bytes: このプロセスが GPU に確保した量(最後の列 gpu_memory_bytes に毎行書く。CPU の実行は 0)
  // n_sections: 断面の質量流量の列の数(フェーズ 5c。M_dot_inlet などの後に mass_imbalance・mass_imbalance_pbc と
  // 断面ごとの M_dot_sec<k>・M_dot_pbc_sec<k> を書く。管でなければ接頭辞は mdot_nd_)
  GsisHistoryWriter(const std::filesystem::path& path, const kinetic::KineticSetup& setup,
                    std::size_t gpu_memory_bytes = 0, std::size_t n_sections = 0);
  void write(const GsisIterationInfo& info, double elapsed_total_s);

 private:
  const kinetic::KineticSetup& setup_;
  std::unique_ptr<CsvWriter> csv_;
  std::size_t gpu_memory_bytes_ = 0;
  std::size_t n_sections_ = 0;
};

class PbcHistoryWriter {
 public:
  PbcHistoryWriter(const std::filesystem::path& path, const PolyMesh& mesh);
  // gsis_iteration: 0 は初期化(PBC 単独)、1 以上は GSIS 反復の番号(n + 1)
  void write(long long gsis_iteration, const PbcIterationInfo& info);

 private:
  std::size_t n_patches_ = 0;
  std::unique_ptr<CsvWriter> csv_;
};

// 管の無次元質量流量 Ṁ(入口から入る量、出口から出る量)。p_in が決まっていなければ NaN
struct TubeMassFlow {
  double inlet = 0.0;
  double outlet = 0.0;
  bool has_inlet = false;
  bool has_outlet = false;
  double mean() const { return 0.5 * (inlet + outlet); }
};
TubeMassFlow tube_mass_flow(const kinetic::KineticSetup& setup, const std::vector<double>& patch_mass_flow_nd);

}  // namespace gsis
