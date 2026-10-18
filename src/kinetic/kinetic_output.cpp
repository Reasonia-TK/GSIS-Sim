#include "kinetic/kinetic_output.hpp"

#include <algorithm>
#include <cmath>
#include <format>
#include <limits>

#include "core/error.hpp"
#include "core/math.hpp"
#include "core/paths.hpp"
#include "io/vtu_writer.hpp"

namespace gsis {

double mass_flow_to_si(const kinetic::KineticSetup& setup, double nondimensional) {
  const ReferenceScales& r = setup.derived.ref;
  return nondimensional * r.mass_flux * r.length * r.length;
}

double mass_flow_display(const kinetic::KineticSetup& setup, double nondimensional) {
  if (!setup.derived.has_p_in) return nondimensional;
  return mass_flow_to_si(setup, nondimensional) / setup.derived.mass_flow_scale;
}

std::string mass_flow_display_prefix(const kinetic::KineticSetup& setup) {
  return setup.derived.has_p_in ? "M_dot_" : "mdot_nd_";
}

MassBalance mass_balance(std::span<const double> patch_flow_nd) {
  MassBalance b;
  for (const double F : patch_flow_nd) {
    if (!std::isfinite(F)) return b;
    if (F > 0.0) {
      b.outflow += F;
    } else {
      b.inflow -= F;
    }
  }
  const double scale = std::max(b.inflow, b.outflow);
  if (scale > 0.0) {
    b.imbalance = (b.outflow - b.inflow) / scale;
    b.valid = true;
  }
  return b;
}

double flow_spread(std::span<const double> flows) {
  if (flows.size() < 2) return std::numeric_limits<double>::quiet_NaN();
  double lo = flows[0], hi = flows[0], sum = 0.0;
  for (const double x : flows) {
    if (!std::isfinite(x)) return std::numeric_limits<double>::quiet_NaN();
    lo = std::min(lo, x);
    hi = std::max(hi, x);
    sum += x;
  }
  const double mean = sum / static_cast<double>(flows.size());
  return mean != 0.0 ? (hi - lo) / std::abs(mean) : std::numeric_limits<double>::quiet_NaN();
}

SectionSpec parse_output_sections(const CaseConfig& config) {
  const ModuleSection& s = config.module_section("output");
  const std::string positions = s.string("mass_flow_sections", "");
  const std::string axis = s.string("mass_flow_section_axis", "x");
  s.finish();
  return parse_section_spec(positions, axis);
}

void write_kinetic_cell_vtu(const std::filesystem::path& path, const PolyMesh& mesh, const KineticMoments& m,
                            const kinetic::KineticSetup& setup, std::span<const double> residual,
                            const HigherOrderTerms* hot) {
  const ReferenceScales& r = setup.derived.ref;
  const auto n = static_cast<std::size_t>(mesh.n_cells);
  GSIS_CHECK(m.state.size() == n, "モーメントのセル数がメッシュと合いません");
  std::vector<double> rho(n), T(n), p(n), ma(n), kn(n), sigma(6 * n);
  std::vector<Vec3> u(n), q(n);
  for (std::size_t c = 0; c < n; ++c) {
    rho[c] = m.state.rho[c] * r.density;
    T[c] = m.state.T[c] * r.temperature;
    p[c] = m.state.p[c] * r.pressure;
    u[c] = Vec3{m.state.ux[c], m.state.uy[c], m.state.uz[c]} * r.velocity;
    q[c] = Vec3{m.q[c][0], m.q[c][1], m.q[c][2]} * r.heat_flux;
    for (std::size_t k = 0; k < 6; ++k) sigma[6 * c + k] = m.sigma[c][k] * r.pressure;
    const double Th = m.state.T[c];
    const double uh = std::sqrt(m.state.ux[c] * m.state.ux[c] + m.state.uy[c] * m.state.uy[c] +
                                m.state.uz[c] * m.state.uz[c]);
    ma[c] = Th > 0.0 ? uh / std::sqrt(setup.gamma * Th) : 0.0;
    // 局所 Kn(式 (8) をセルの状態で、L_ref 基準): μ̂(T)/p̂ √(π T̂/2)
    const double mu = setup.mu0 * std::pow(std::max(Th, 0.0), setup.omega);
    kn[c] = m.state.p[c] > 0.0 ? mu / m.state.p[c] * std::sqrt(kPi * Th / 2.0) : 0.0;
  }
  VtuWriter w = VtuWriter::polyhedra(mesh);
  w.add_cell_scalar("rho", std::span<const double>(rho));
  w.add_cell_vector("u", std::span<const Vec3>(u));
  w.add_cell_scalar("T", std::span<const double>(T));
  w.add_cell_scalar("p", std::span<const double>(p));
  w.add_cell_field("sigma", 6, std::span<const double>(sigma));
  w.add_cell_vector("q", std::span<const Vec3>(q));
  w.add_cell_scalar("Ma", std::span<const double>(ma));
  w.add_cell_scalar("Kn_local", std::span<const double>(kn));
  if (residual.size() == n) w.add_cell_scalar("residual", residual);
  if (hot != nullptr) {
    GSIS_CHECK(hot->sigma.size() == n && hot->q.size() == n, "HoT のセル数がメッシュと合いません");
    std::vector<double> hs(6 * n);
    std::vector<Vec3> hq(n);
    for (std::size_t c = 0; c < n; ++c) {
      for (std::size_t k = 0; k < 6; ++k) hs[6 * c + k] = hot->sigma[c][k] * r.pressure;
      hq[c] = Vec3{hot->q[c][0], hot->q[c][1], hot->q[c][2]} * r.heat_flux;
    }
    w.add_cell_field("hot_sigma", 6, std::span<const double>(hs));
    w.add_cell_vector("hot_q", std::span<const Vec3>(hq));
  }
  w.write(path);
}

void write_kinetic_cells_csv(const std::filesystem::path& path, const PolyMesh& mesh, const MeshGeometry& geometry,
                             const KineticMoments& m, double L, std::span<const double> residual) {
  CsvWriter csv(path, {"cell", "x", "y", "z", "volume", "rho", "ux", "uy", "uz", "T", "p", "sxx", "syy", "szz", "sxy",
                       "syz", "sxz", "qx", "qy", "qz", "residual"});
  for (Index c = 0; c < mesh.n_cells; ++c) {
    const auto ci = static_cast<std::size_t>(c);
    const Vec3 x = geometry.cell_center[ci] / L;
    std::vector<double> row = {static_cast<double>(c), x.x, x.y, x.z, geometry.cell_volume[ci] / (L * L * L),
                               m.state.rho[ci], m.state.ux[ci], m.state.uy[ci], m.state.uz[ci], m.state.T[ci],
                               m.state.p[ci]};
    for (double s : m.sigma[ci]) row.push_back(s);
    for (double q : m.q[ci]) row.push_back(q);
    row.push_back(ci < residual.size() ? residual[ci] : 0.0);
    csv.write_row(std::span<const double>(row));
  }
}

CellMacroState read_kinetic_cells_csv(const std::filesystem::path& path, Index n_cells) {
  const std::string text = read_file(path);
  CellMacroState w;
  w.resize(static_cast<std::size_t>(n_cells));
  std::vector<char> seen(static_cast<std::size_t>(n_cells), 0);
  std::size_t pos = 0;
  int col_cell = -1, col_rho = -1, col_ux = -1, col_uy = -1, col_uz = -1, col_T = -1;
  bool first = true;
  while (pos < text.size()) {
    std::size_t end = text.find('\n', pos);
    if (end == std::string::npos) end = text.size();
    std::string line = text.substr(pos, end - pos);
    pos = end + 1;
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    std::vector<std::string> f;
    std::size_t a = 0;
    while (true) {
      const std::size_t b = line.find(',', a);
      f.push_back(line.substr(a, b == std::string::npos ? std::string::npos : b - a));
      if (b == std::string::npos) break;
      a = b + 1;
    }
    if (first) {
      first = false;
      for (int i = 0; i < static_cast<int>(f.size()); ++i) {
        const std::string& h = f[static_cast<std::size_t>(i)];
        if (h == "cell") col_cell = i;
        if (h == "rho") col_rho = i;
        if (h == "ux") col_ux = i;
        if (h == "uy") col_uy = i;
        if (h == "uz") col_uz = i;
        if (h == "T") col_T = i;
      }
      GSIS_CHECK(col_cell >= 0 && col_rho >= 0 && col_ux >= 0 && col_uy >= 0 && col_uz >= 0 && col_T >= 0,
                 std::format("{} に cell, rho, ux, uy, uz, T の列がありません", path_to_utf8(path)));
      continue;
    }
    auto num = [&](int c) { return std::stod(f.at(static_cast<std::size_t>(c))); };
    const auto c = static_cast<long long>(num(col_cell));
    GSIS_CHECK(c >= 0 && c < n_cells, std::format("{} のセル番号 {} が範囲外です", path_to_utf8(path), c));
    const auto ci = static_cast<std::size_t>(c);
    w.rho[ci] = num(col_rho);
    w.ux[ci] = num(col_ux);
    w.uy[ci] = num(col_uy);
    w.uz[ci] = num(col_uz);
    w.T[ci] = num(col_T);
    w.p[ci] = w.rho[ci] * w.T[ci];
    seen[ci] = 1;
  }
  for (Index c = 0; c < n_cells; ++c) {
    GSIS_CHECK(seen[static_cast<std::size_t>(c)], std::format("{} にセル {} の値がありません", path_to_utf8(path), c));
  }
  return w;
}

KineticPatchSummary summarize_kinetic_patches(const PolyMesh& mesh, const MeshGeometry& geometry,
                                              const BoundaryKineticFluxes& fluxes,
                                              const kinetic::KineticSetup& s) {
  KineticPatchSummary out;
  const Index n_int = mesh.n_internal_faces();
  const double L2 = s.length_scale * s.length_scale;
  out.mdot.assign(mesh.patches.size(), 0.0);
  for (std::size_t pi = 0; pi < mesh.patches.size(); ++pi) {
    const Patch& patch = mesh.patches[pi];
    double F[5] = {0, 0, 0, 0, 0};
    double area = 0.0;
    double face[5] = {0, 0, 0, 0, 0};
    for (Index f = patch.start; f < patch.end(); ++f) {
      const auto b = static_cast<std::size_t>(f - n_int);
      const double A = geometry.face_area[static_cast<std::size_t>(f)] / L2;
      for (std::size_t k = 0; k < 5; ++k) {
        F[k] += fluxes.F_plus[b][k] + fluxes.F_minus[b][k];
        face[k] += A * fluxes.face_state[b][k];
      }
      area += A;
    }
    const kinetic::PatchCondition* pc = nullptr;
    for (const auto& c : s.patches) {
      if (c.name == patch.name) pc = &c;
    }
    GSIS_CHECK(pc != nullptr, std::format("パッチ {} の境界条件がありません", patch.name));
    json::Value pj;
    pj.set("name", patch.name);
    pj.set("type", std::string(to_string(pc->type)));
    pj.set("faces", static_cast<std::int64_t>(patch.count));
    pj.set("area_nd", area);
    pj.set("mdot_nd", F[0]);
    pj.set("mdot_kg_s", mass_flow_to_si(s, F[0]));
    pj.set("momentum_flux_nd", json::Array{F[1], F[2], F[3]});
    pj.set("energy_flux_nd", F[4]);
    if (area > 0.0) {
      pj.set("face_rho_nd", face[0] / area);
      pj.set("face_u_nd", json::Array{face[1] / area, face[2] / area, face[3] / area});
      pj.set("face_T_nd", face[4] / area);
    }
    out.patches.push_back(std::move(pj));
    out.mdot[pi] = F[0];
    if (pc->type == BoundaryType::PressureOutlet || pc->type == BoundaryType::Vacuum) {
      out.mdot_out += F[0];
      out.has_out = true;
    } else if (pc->type == BoundaryType::PressureInlet) {
      out.mdot_in -= F[0];
      out.has_in = true;
    }
  }
  return out;
}

json::Value tube_mass_flow_json(const kinetic::KineticSetup& s, double mdot_in, bool has_in, double mdot_out,
                                bool has_out) {
  if (!s.derived.has_p_in || !(has_in || has_out)) return json::Value(nullptr);
  // 無次元質量流量 Ṁ = ṁ / (√π L_ref² p_in / √(2 R T0))
  const double scale = s.derived.mass_flow_scale;
  json::Value mf;
  mf.set("scale_kg_s", scale);
  if (has_in) mf.set("M_dot_inlet", mass_flow_to_si(s, mdot_in) / scale);
  if (has_out) mf.set("M_dot_outlet", mass_flow_to_si(s, mdot_out) / scale);
  if (has_in && has_out) mf.set("M_dot", 0.5 * (mass_flow_to_si(s, mdot_in) + mass_flow_to_si(s, mdot_out)) / scale);
  return mf;
}

void write_kinetic_boundary_vtu(const std::filesystem::path& path, const PolyMesh& mesh, const MeshGeometry& geometry,
                                const BoundaryKineticFluxes& fl, const kinetic::KineticSetup& setup) {
  const ReferenceScales& r = setup.derived.ref;
  const Index n_int = mesh.n_internal_faces();
  const auto nb = static_cast<std::size_t>(mesh.n_boundary_faces());
  GSIS_CHECK(fl.F_plus.size() == nb, "境界面の流束の数がメッシュと合いません");
  const double L2 = r.length * r.length;
  std::vector<double> mass(nb), pn(nb), energy(nb), frho(nb), fT(nb);
  std::vector<Vec3> shear(nb), mom(nb), fu(nb);
  for (std::size_t b = 0; b < nb; ++b) {
    const auto f = static_cast<std::size_t>(n_int) + b;
    const double area = geometry.face_area[f] / L2;  // 無次元の面積
    const Vec3& n = geometry.face_normal[f];
    double F[5];
    for (std::size_t k = 0; k < 5; ++k) F[k] = (fl.F_plus[b][k] + fl.F_minus[b][k]) / area;
    mass[b] = F[0] * r.mass_flux;
    const Vec3 M{F[1], F[2], F[3]};
    mom[b] = M * r.pressure;
    pn[b] = dot(M, n) * r.pressure;
    shear[b] = (M - dot(M, n) * n) * r.pressure;
    energy[b] = F[4] * r.heat_flux;
    frho[b] = fl.face_state[b][0] * r.density;
    fu[b] = Vec3{fl.face_state[b][1], fl.face_state[b][2], fl.face_state[b][3]} * r.velocity;
    fT[b] = fl.face_state[b][4] * r.temperature;
  }
  VtuWriter w = VtuWriter::boundary_polygons(mesh);
  w.add_cell_scalar("mass_flux", std::span<const double>(mass));
  w.add_cell_vector("momentum_flux", std::span<const Vec3>(mom));
  w.add_cell_scalar("normal_stress", std::span<const double>(pn));
  w.add_cell_vector("shear_stress", std::span<const Vec3>(shear));
  w.add_cell_scalar("energy_flux", std::span<const double>(energy));
  w.add_cell_scalar("face_rho", std::span<const double>(frho));
  w.add_cell_vector("face_u", std::span<const Vec3>(fu));
  w.add_cell_scalar("face_T", std::span<const double>(fT));
  w.write(path);
}

KineticHistoryWriter::KineticHistoryWriter(const std::filesystem::path& path, const kinetic::KineticSetup& setup,
                                           std::size_t gpu_memory_bytes, std::size_t n_sections)
    : setup_(setup), gpu_memory_bytes_(gpu_memory_bytes), n_sections_(n_sections) {
  std::vector<std::string> header = {"iteration", "epsilon", "eps_rho", "eps_u", "eps_T", "residual",
                                     "residual_rel", "total_mass", "mass_scale"};
  for (const kinetic::PatchCondition& pc : setup.patches) header.push_back("mdot_" + pc.name + "_kg_s");
  for (const kinetic::PatchCondition& pc : setup.patches) header.push_back("mdot_" + pc.name + "_nd");
  for (const char* h : {"qx_mean", "qy_mean", "qz_mean", "sxx_mean", "syy_mean", "szz_mean", "sxy_mean", "syz_mean",
                        "sxz_mean"}) {
    header.emplace_back(h);
  }
  for (const char* h : {"time_iter_s", "time_total_s", "t_moments_s", "t_boundary_s", "t_gradient_s",
                        "t_residual_s", "t_sweep_forward_s", "t_sweep_backward_s", "t_other_s"}) {
    header.emplace_back(h);
  }
  // フェーズ 5c: 質量の釣り合いと断面の質量流量(gpu_memory_bytes は最後の列のまま)
  header.emplace_back("mass_imbalance");
  for (std::size_t k = 0; k < n_sections_; ++k) header.push_back(std::format("{}sec{}", mass_flow_display_prefix(setup), k));
  header.emplace_back("gpu_memory_bytes");
  csv_ = std::make_unique<CsvWriter>(path, std::move(header));
  csv_->set_autoflush(true);
}

void KineticHistoryWriter::write(const KineticIterationInfo& info, double elapsed_total_s,
                                 std::span<const double> section_flow_nd) {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  if (info.has_residual && first_residual_ == 0.0) first_residual_ = info.residual;
  std::vector<CsvValue> row;
  row.emplace_back(static_cast<std::int64_t>(info.iteration));
  row.emplace_back(info.has_epsilon ? info.epsilon : nan);
  row.emplace_back(info.has_epsilon ? info.eps_rho : nan);
  row.emplace_back(info.has_epsilon ? info.eps_u : nan);
  row.emplace_back(info.has_epsilon ? info.eps_T : nan);
  row.emplace_back(info.has_residual ? info.residual : nan);
  row.emplace_back(info.has_residual && first_residual_ > 0.0 ? info.residual / first_residual_ : nan);
  row.emplace_back(info.total_mass);
  row.emplace_back(info.mass_scale);
  for (std::size_t i = 0; i < setup_.patches.size(); ++i) {
    row.emplace_back(i < info.patch_mass_flow.size() ? mass_flow_to_si(setup_, info.patch_mass_flow[i]) : nan);
  }
  for (std::size_t i = 0; i < setup_.patches.size(); ++i) {
    row.emplace_back(i < info.patch_mass_flow.size() ? info.patch_mass_flow[i] : nan);
  }
  for (const double x : info.q_mean) row.emplace_back(x);
  for (const double x : info.sigma_mean) row.emplace_back(x);
  const kinetic::StepTimings& t = info.timings;
  row.emplace_back(info.seconds);
  row.emplace_back(elapsed_total_s);
  row.emplace_back(t.moments);
  row.emplace_back(t.boundary);
  row.emplace_back(t.gradient);
  row.emplace_back(t.residual);
  row.emplace_back(t.sweep_forward);
  row.emplace_back(t.sweep_backward);
  row.emplace_back(t.other);
  const MassBalance mb = mass_balance(info.patch_mass_flow);
  row.emplace_back(mb.valid ? mb.imbalance : nan);
  for (std::size_t k = 0; k < n_sections_; ++k) {
    row.emplace_back(k < section_flow_nd.size() ? mass_flow_display(setup_, section_flow_nd[k]) : nan);
  }
  row.emplace_back(static_cast<std::int64_t>(gpu_memory_bytes_));
  csv_->write_row(std::span<const CsvValue>(row));
}

}  // namespace gsis
