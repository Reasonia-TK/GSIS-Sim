#include "app/mass_balance_report.hpp"

#include <cmath>
#include <format>

#include "app/cli.hpp"
#include "core/log.hpp"

namespace gsis::cli {
namespace {

json::Value finite_or_null(double x) { return std::isfinite(x) ? json::Value(x) : json::Value(nullptr); }

}  // namespace

BalanceEntry make_balance_entry(std::string name, std::string label, std::span<const double> patch_flow_nd,
                                std::span<const double> sections_nd, bool checked) {
  BalanceEntry e;
  e.name = std::move(name);
  e.label = std::move(label);
  e.boundary = mass_balance(patch_flow_nd);
  e.sections_nd.assign(sections_nd.begin(), sections_nd.end());
  e.checked = checked;
  if (e.boundary.valid) {
    std::vector<double> v = {e.boundary.inflow, e.boundary.outflow};
    for (const double s : e.sections_nd) v.push_back(std::abs(s));
    e.spread = flow_spread(v);
    e.spread_valid = std::isfinite(e.spread);
  }
  return e;
}

void evaluate_balance(BalanceReport& r) {
  r.warnings.clear();
  for (const BalanceEntry& e : r.entries) {
    if (!e.checked || !e.boundary.valid) continue;
    if (std::abs(e.boundary.imbalance) > r.tolerance) {
      r.warnings.push_back(std::format("{}: 入口と出口の質量流量の相対差 {:+.3e}(許容 {:.1e})", e.label,
                                       e.boundary.imbalance, r.tolerance));
    }
    if (!e.sections_nd.empty() && e.spread_valid && e.spread > r.tolerance) {
      r.warnings.push_back(std::format("{}: 断面・入口・出口の質量流量のばらつき {:.3e}(許容 {:.1e})", e.label, e.spread,
                                       r.tolerance));
    }
  }
}

json::Value balance_json(const BalanceReport& r, const kinetic::KineticSetup& s) {
  json::Value root;
  root.set("tolerance", r.tolerance);
  root.set("required", r.required);
  root.set("definition",
           "imbalance = (outflow - inflow) / max(inflow, outflow)(境界の外向きの質量流量から)、section_spread = "
           "{inflow, outflow, |断面|} の (max - min) / 平均");
  for (const BalanceEntry& e : r.entries) {
    json::Value j;
    j.set("inflow_nd", e.boundary.inflow);
    j.set("outflow_nd", e.boundary.outflow);
    if (s.derived.has_p_in) {
      j.set("M_dot_inflow", mass_flow_display(s, e.boundary.inflow));
      j.set("M_dot_outflow", mass_flow_display(s, e.boundary.outflow));
    }
    j.set("imbalance", e.boundary.valid ? finite_or_null(e.boundary.imbalance) : json::Value(nullptr));
    j.set("section_spread", e.spread_valid ? finite_or_null(e.spread) : json::Value(nullptr));
    j.set("checked", e.checked);
    root.set(e.name, std::move(j));
  }
  root.set("within_tolerance", r.warnings.empty());
  json::Array w;
  for (const std::string& m : r.warnings) w.emplace_back(m);
  root.set("warnings", std::move(w));
  return root;
}

json::Value sections_json(const SectionSpec& spec, const std::vector<SectionCut>& cuts,
                          const std::vector<SectionFlowSeries>& flows, const kinetic::KineticSetup& s) {
  json::Value root;
  root.set("axis", section_axis_name(spec.axis));
  root.set("definition",
           "セル中心が位置の手前と先に分かれる内部面の集まりを通る離散的な質量流束の和(軸の正の向きが正)。kinetic は "
           "最後の f(GSIS は補正の後)、kinetic_half は CIS の後の f^{n+1/2}、pbc は PBC の W^{n+1}");
  json::Array arr;
  for (std::size_t k = 0; k < cuts.size(); ++k) {
    const SectionCut& c = cuts[k];
    json::Value e;
    e.set("index", static_cast<std::int64_t>(k));
    e.set("position", c.position);
    e.set("face_position", c.face_position);
    e.set("n_faces", static_cast<std::int64_t>(c.faces.size()));
    e.set("area_nd", c.area);
    e.set("projected_area_nd", c.projected_area);
    for (const SectionFlowSeries& f : flows) {
      if (k >= f.nd.size()) continue;
      e.set("mdot_" + f.key + "_nd", finite_or_null(f.nd[k]));
      if (s.derived.has_p_in) e.set("M_dot_" + f.key, finite_or_null(mass_flow_display(s, f.nd[k])));
    }
    arr.push_back(std::move(e));
  }
  root.set("sections", std::move(arr));
  return root;
}

void print_balance(const BalanceReport& r, const SectionSpec& spec, const std::vector<SectionCut>& cuts,
                   const kinetic::KineticSetup& s) {
  const char* unit = s.derived.has_p_in ? "Ṁ" : "無次元の流量";
  for (const BalanceEntry& e : r.entries) {
    if (!e.boundary.valid) continue;
    std::string v = std::format("入口 {:.5f}・出口 {:.5f}(相対差 {:+.2e})", mass_flow_display(s, e.boundary.inflow),
                                mass_flow_display(s, e.boundary.outflow), e.boundary.imbalance);
    if (!e.sections_nd.empty()) {
      v += "、断面";
      for (std::size_t k = 0; k < e.sections_nd.size() && k < cuts.size(); ++k) {
        v += std::format(" {}={:.4g}: {:.5f}", section_axis_name(spec.axis), cuts[k].face_position,
                         mass_flow_display(s, e.sections_nd[k]));
      }
      if (e.spread_valid) v += std::format("(ばらつき {:.2e})", e.spread);
    }
    print_item(std::format("{}の {}", e.label, unit), v);
  }
  if (r.warnings.empty()) {
    print_item("質量の釣り合い", std::format("許容 {:.1e} 以内", r.tolerance));
  } else {
    for (const std::string& m : r.warnings) log_warn("質量が釣り合っていません。{}", m);
  }
}

}  // namespace gsis::cli
