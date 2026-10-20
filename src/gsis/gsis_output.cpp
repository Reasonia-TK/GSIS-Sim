#include "gsis/gsis_output.hpp"

#include <cmath>
#include <format>
#include <limits>

#include "kinetic/kinetic_output.hpp"

namespace gsis {

TubeMassFlow tube_mass_flow(const kinetic::KineticSetup& setup, const std::vector<double>& flow_nd) {
  TubeMassFlow m;
  if (!setup.derived.has_p_in) {
    m.inlet = m.outlet = std::numeric_limits<double>::quiet_NaN();
    return m;
  }
  const double scale = setup.derived.mass_flow_scale;
  double in = 0.0;
  double out = 0.0;
  for (std::size_t p = 0; p < setup.patches.size() && p < flow_nd.size(); ++p) {
    const BoundaryType t = setup.patches[p].type;
    if ((t == BoundaryType::PressureInlet || t == BoundaryType::MassFlowInlet)) {
      in -= flow_nd[p];
      m.has_inlet = true;
    } else if (t == BoundaryType::PressureOutlet || t == BoundaryType::Vacuum) {
      out += flow_nd[p];
      m.has_outlet = true;
    }
  }
  m.inlet = m.has_inlet ? mass_flow_to_si(setup, in) / scale : std::numeric_limits<double>::quiet_NaN();
  m.outlet = m.has_outlet ? mass_flow_to_si(setup, out) / scale : std::numeric_limits<double>::quiet_NaN();
  return m;
}

GsisHistoryWriter::GsisHistoryWriter(const std::filesystem::path& path, const kinetic::KineticSetup& setup,
                                     std::size_t gpu_memory_bytes, std::size_t n_sections)
    : setup_(setup), gpu_memory_bytes_(gpu_memory_bytes), n_sections_(n_sections) {
  std::vector<std::string> h = {"iteration", "cis_iterations", "epsilon", "eps_rho", "eps_u", "eps_T",
                                "eps_half", "pbc_change", "residual", "total_mass"};
  for (const char* c : {"pbc_iterations", "pbc_converged", "pbc_final_update", "pbc_initial_residual_mass",
                        "pbc_final_residual_mass", "pbc_final_residual_momentum", "pbc_final_residual_energy",
                        "pbc_linear_iterations_pu", "pbc_linear_iterations_T", "pbc_max_linear_iterations_pu",
                        "pbc_linear_not_converged", "pbc_rejected", "pbc_frozen_cells", "replaced_cells", "hot_sigma_rms",
                        "hot_q_rms"}) {
    h.emplace_back(c);
  }
  // パッチごとの質量流量: 運動論(f^{n+1/2} の境界の離散流束)と PBC(W^{n+1} の境界フラックス)
  for (const kinetic::PatchCondition& pc : setup.patches) h.push_back("mdot_" + pc.name + "_kg_s");
  for (const kinetic::PatchCondition& pc : setup.patches) h.push_back("mdot_" + pc.name + "_nd");
  for (const kinetic::PatchCondition& pc : setup.patches) h.push_back("mdot_pbc_" + pc.name + "_nd");
  for (const char* c : {"M_dot_inlet", "M_dot_outlet", "M_dot_pbc_inlet", "M_dot_pbc_outlet"}) h.emplace_back(c);
  // フェーズ 5c: 質量の釣り合い((outflow − inflow)/max)と断面の質量流量(運動論 f^{n+1/2}・PBC W^{n+1})
  h.emplace_back("mass_imbalance");
  h.emplace_back("mass_imbalance_pbc");
  const std::string pre = mass_flow_display_prefix(setup);
  for (std::size_t k = 0; k < n_sections_; ++k) h.push_back(std::format("{}sec{}", pre, k));
  for (std::size_t k = 0; k < n_sections_; ++k) h.push_back(std::format("{}pbc_sec{}", pre, k));
  for (const char* c : {"qx_mean", "qy_mean", "qz_mean", "sxx_mean", "syy_mean", "szz_mean", "sxy_mean",
                        "syz_mean", "sxz_mean"}) {
    h.emplace_back(c);
  }
  for (const char* c : {"time_iter_s", "time_total_s", "t_cis_s", "t_moments_s", "t_pbc_s", "t_correction_s",
                        "t_transfer_s", "t_cis_gradient_s", "t_cis_residual_s", "t_cis_sweep_s",
                        "t_pbc_linear_s", "filter_iterations", "t_filter_s", "gpu_memory_bytes"}) {
    h.emplace_back(c);
  }
  for (const auto& pc : setup.patches) if (pc.type == BoundaryType::MassFlowInlet) h.push_back("p_b_"+pc.name+"_Pa");
  csv_ = std::make_unique<CsvWriter>(path, std::move(h));
  csv_->set_autoflush(true);
}

void GsisHistoryWriter::write(const GsisIterationInfo& it, double elapsed_total_s) {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  std::vector<CsvValue> row;
  row.emplace_back(static_cast<std::int64_t>(it.iteration));
  row.emplace_back(static_cast<std::int64_t>(it.cis_iterations));
  row.emplace_back(it.change.epsilon);
  row.emplace_back(it.change.rho);
  row.emplace_back(it.change.u);
  row.emplace_back(it.change.T);
  row.emplace_back(it.change_half.epsilon);
  row.emplace_back(it.pbc_change.epsilon);
  row.emplace_back(it.kinetic.has_residual ? it.kinetic.residual : nan);
  row.emplace_back(it.kinetic.total_mass);
  const PbcStageReport& p = it.pbc;
  row.emplace_back(static_cast<std::int64_t>(p.iterations));
  row.emplace_back(static_cast<std::int64_t>(p.converged ? 1 : 0));
  row.emplace_back(p.final_update);
  row.emplace_back(p.initial_residual_mass);
  row.emplace_back(p.final_residual_mass);
  row.emplace_back(p.final_residual_momentum);
  row.emplace_back(p.final_residual_energy);
  row.emplace_back(static_cast<std::int64_t>(p.linear_iterations_pu));
  row.emplace_back(static_cast<std::int64_t>(p.linear_iterations_T));
  row.emplace_back(static_cast<std::int64_t>(p.max_linear_iterations_pu));
  row.emplace_back(static_cast<std::int64_t>(p.linear_not_converged));
  row.emplace_back(static_cast<std::int64_t>(p.rejected_iterations));
  row.emplace_back(static_cast<std::int64_t>(p.frozen_cells));
  row.emplace_back(static_cast<std::int64_t>(it.replaced_cells));
  row.emplace_back(it.hot_sigma_rms);
  row.emplace_back(it.hot_q_rms);
  const std::size_t np = setup_.patches.size();
  for (std::size_t i = 0; i < np; ++i) {
    row.emplace_back(i < it.patch_mass_flow_kinetic.size() ? mass_flow_to_si(setup_, it.patch_mass_flow_kinetic[i])
                                                           : nan);
  }
  for (std::size_t i = 0; i < np; ++i) {
    row.emplace_back(i < it.patch_mass_flow_kinetic.size() ? it.patch_mass_flow_kinetic[i] : nan);
  }
  for (std::size_t i = 0; i < np; ++i) {
    row.emplace_back(i < it.patch_mass_flow_pbc.size() ? it.patch_mass_flow_pbc[i] : nan);
  }
  const TubeMassFlow mk = tube_mass_flow(setup_, it.patch_mass_flow_kinetic);
  const TubeMassFlow mp = tube_mass_flow(setup_, it.patch_mass_flow_pbc);
  row.emplace_back(mk.has_inlet ? mk.inlet : nan);
  row.emplace_back(mk.has_outlet ? mk.outlet : nan);
  row.emplace_back(mp.has_inlet ? mp.inlet : nan);
  row.emplace_back(mp.has_outlet ? mp.outlet : nan);
  {
    const MassBalance bk = mass_balance(it.patch_mass_flow_kinetic);
    const MassBalance bp = mass_balance(it.patch_mass_flow_pbc);
    row.emplace_back(bk.valid ? bk.imbalance : nan);
    row.emplace_back(bp.valid ? bp.imbalance : nan);
    for (std::size_t k = 0; k < n_sections_; ++k) {
      row.emplace_back(k < it.section_flow_kinetic.size() ? mass_flow_display(setup_, it.section_flow_kinetic[k]) : nan);
    }
    for (std::size_t k = 0; k < n_sections_; ++k) {
      row.emplace_back(k < it.section_flow_pbc.size() ? mass_flow_display(setup_, it.section_flow_pbc[k]) : nan);
    }
  }
  for (const double x : it.kinetic.q_mean) row.emplace_back(x);
  for (const double x : it.kinetic.sigma_mean) row.emplace_back(x);
  row.emplace_back(it.t_total);
  row.emplace_back(elapsed_total_s);
  row.emplace_back(it.t_cis);
  row.emplace_back(it.t_moments);
  row.emplace_back(it.t_pbc);
  row.emplace_back(it.t_correction);
  row.emplace_back(it.t_moments + it.t_correction);
  const kinetic::StepTimings& kt = it.kinetic.timings;
  row.emplace_back(kt.gradient);
  row.emplace_back(kt.residual);
  row.emplace_back(kt.sweep_forward + kt.sweep_backward);
  row.emplace_back(p.timing.total("線形ソルバー(p, u)") + p.timing.total("線形ソルバー(T)"));
  row.emplace_back(static_cast<std::int64_t>(it.filter.linear_iterations));
  row.emplace_back(it.t_filter);
  row.emplace_back(static_cast<std::int64_t>(gpu_memory_bytes_));
  for (const auto& pc : setup_.patches) if (pc.type == BoundaryType::MassFlowInlet) row.emplace_back(pc.p*setup_.derived.ref.pressure);
  csv_->write_row(std::span<const CsvValue>(row));
}

PbcHistoryWriter::PbcHistoryWriter(const std::filesystem::path& path, const PolyMesh& mesh)
    : n_patches_(mesh.patches.size()) {
  std::vector<std::string> h = {"gsis_iteration", "pbc_iteration", "cfl", "residual_mass", "residual_momentum",
                                "residual_energy", "kinetic_energy_defect", "residual_temperature", "update_p",
                                "update_u", "update_T", "linear_iterations_pu", "linear_residual_pu",
                                "linear_iterations_T", "linear_residual_T", "clipped_p", "clipped_T", "rejected"};
  for (const Patch& p : mesh.patches) h.push_back("mdot_" + p.name + "_nd");
  h.emplace_back("seconds");
  csv_ = std::make_unique<CsvWriter>(path, std::move(h));
  csv_->set_autoflush(true);
}

void PbcHistoryWriter::write(long long gsis_iteration, const PbcIterationInfo& it) {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  std::vector<CsvValue> row = {static_cast<std::int64_t>(gsis_iteration),
                               static_cast<std::int64_t>(it.iteration + 1),
                               it.cfl,
                               it.residual_mass,
                               it.residual_momentum,
                               it.residual_energy,
                               it.kinetic_energy_defect,
                               it.residual_temperature,
                               it.update_p,
                               it.update_u,
                               it.update_T,
                               static_cast<std::int64_t>(it.linear_iterations_pu),
                               it.linear_residual_pu,
                               static_cast<std::int64_t>(it.linear_iterations_T),
                               it.linear_residual_T,
                               static_cast<std::int64_t>(it.clipped_p),
                               static_cast<std::int64_t>(it.clipped_T),
                               static_cast<std::int64_t>(it.rejected ? 1 : 0)};
  for (std::size_t i = 0; i < n_patches_; ++i) {
    row.emplace_back(i < it.patch_mass_flow.size() ? it.patch_mass_flow[i] : nan);
  }
  row.emplace_back(it.seconds);
  csv_->write_row(std::span<const CsvValue>(row));
}

}  // namespace gsis
