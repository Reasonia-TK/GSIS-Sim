#pragma once
// 質量の釣り合いと断面の質量流量の報告(フェーズ 5c。gsis-solver run・cis-run が使う。docs/design_gsis.md §6a)。
//   - 境界の釣り合い: (outflow − inflow) / max(inflow, outflow)(kinetic/kinetic_output.hpp の mass_balance)
//   - 断面のばらつき: {inflow, outflow, |断面の流量|} の (max − min) / 平均
// どちらかが [gsis].mass_balance_tolerance を超えたら警告する(式 (59) の判定を満たしていても)。

#include <span>
#include <string>
#include <vector>

#include "core/json.hpp"
#include "kinetic/kinetic_output.hpp"
#include "kinetic/kinetic_setup.hpp"
#include "mesh/section_cut.hpp"

namespace gsis::cli {

// 1 つの状態(最後の f、CIS の後の f^{n+1/2}、PBC の W^{n+1} など)の釣り合い
struct BalanceEntry {
  std::string name;                  // JSON のキー("kinetic"・"kinetic_half"・"pbc")
  std::string label;                 // 表示名
  MassBalance boundary;              // 境界の流量の釣り合い
  std::vector<double> sections_nd;   // 断面の流量(無次元。断面がなければ空)
  double spread = 0.0;               // {inflow, outflow, |断面|} のばらつき(断面がなければ inflow と outflow だけ)
  bool spread_valid = false;
  bool checked = true;               // 警告の判定に使う
};

BalanceEntry make_balance_entry(std::string name, std::string label, std::span<const double> patch_flow_nd,
                                std::span<const double> sections_nd, bool checked = true);

struct BalanceReport {
  double tolerance = 0.0;
  bool required = false;
  std::vector<BalanceEntry> entries;
  std::vector<std::string> warnings;  // 許容値を超えた項目の説明(空なら釣り合っている)
};

// entries の checked の組を tolerance と比べて warnings を作る
void evaluate_balance(BalanceReport& report);

// summary.json の "mass_balance"
json::Value balance_json(const BalanceReport& report, const kinetic::KineticSetup& setup);

// summary.json の "mass_flow_sections"。flows は (キー, 断面ごとの無次元の流量) の組
struct SectionFlowSeries {
  std::string key;  // "kinetic"・"kinetic_half"・"pbc"
  std::vector<double> nd;
};
json::Value sections_json(const SectionSpec& spec, const std::vector<SectionCut>& cuts,
                          const std::vector<SectionFlowSeries>& flows, const kinetic::KineticSetup& setup);

// 結果の表示(断面の流量と釣り合い)。許容値を超えたら警告を出す
void print_balance(const BalanceReport& report, const SectionSpec& spec, const std::vector<SectionCut>& cuts,
                   const kinetic::KineticSetup& setup);

}  // namespace gsis::cli
