#include "gsis/gsis_solver.hpp"

#include <algorithm>
#include <cmath>
#include <format>
#include <utility>

#include "core/error.hpp"
#include "core/math.hpp"
#include "core/timer.hpp"
#include "kinetic/kinetic_output.hpp"

namespace gsis {

namespace {

void check_state_size(const CellMacroState& s, std::size_t n, const char* what) {
  GSIS_CHECK(s.rho.size() == n && s.ux.size() == n && s.uy.size() == n && s.uz.size() == n && s.T.size() == n &&
                 s.p.size() == n,
             std::format("{}のセル数 {} がメッシュのセル数 {} と違います", what, s.rho.size(), n));
}

}  // namespace

GsisOptions GsisOptions::from_case(const CaseConfig& config, const KineticParams& kinetic, const MacroParams& macro) {
  GsisOptions o;
  o.n_cis = config.gsis.n_cis;
  o.max_pbc = config.gsis.max_pbc;
  o.cfl_pbc = config.gsis.cfl_pbc;
  o.init_iterations = macro.init_iterations;
  o.u_floor = kinetic.convergence_u_floor;
  o.filter_length = config.gsis.filter_length;
  o.filter_max_length = config.gsis.filter_max_length;
  o.filter_min_cells = config.gsis.filter_min_cells;
  o.damp_kn = config.gsis.damp_kn;
  o.sections = parse_output_sections(config);
  return o;
}

RelativeChange relative_change(const CellMacroState& a, const CellMacroState& b, const std::vector<double>& volume,
                               double u_floor) {
  const std::size_t n = volume.size();
  check_state_size(a, n, "相対変化の状態");
  check_state_size(b, n, "相対変化の状態");
  // 式 (59)。セル番号の順に足す(決定的)
  double num[3] = {0.0, 0.0, 0.0};
  double den[3] = {0.0, 0.0, 0.0};
  double vol = 0.0;
  for (std::size_t c = 0; c < n; ++c) {
    const double V = volume[c];
    const double dr = a.rho[c] - b.rho[c];
    num[0] += dr * dr * V;
    den[0] += b.rho[c] * b.rho[c] * V;
    const double dx = a.ux[c] - b.ux[c];
    const double dy = a.uy[c] - b.uy[c];
    const double dz = a.uz[c] - b.uz[c];
    num[1] += (dx * dx + dy * dy + dz * dz) * V;
    den[1] += (b.ux[c] * b.ux[c] + b.uy[c] * b.uy[c] + b.uz[c] * b.uz[c]) * V;
    const double dT = a.T[c] - b.T[c];
    num[2] += dT * dT * V;
    den[2] += b.T[c] * b.T[c] * V;
    vol += V;
  }
  den[1] = std::max(den[1], u_floor * u_floor * vol);
  auto ratio = [](double nn, double dd) { return dd > 0.0 ? std::sqrt(nn / dd) : (nn > 0.0 ? 1.0 : 0.0); };
  RelativeChange r;
  r.rho = ratio(num[0], den[0]);
  r.u = ratio(num[1], den[1]);
  r.T = ratio(num[2], den[2]);
  r.epsilon = std::max({r.rho, r.u, r.T});
  return r;
}

GsisSolver::GsisSolver(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry,
                       const KineticParams& kinetic_params, const MacroParams& macro_params,
                       const GsisOptions& options)
    : options_(options),
      kinetic_params_(kinetic_params),
      kinetic_(config, mesh, geometry, kinetic_params),
      pbc_(PbcSolver::from_case(config, derive_quantities(config), mesh, geometry, macro_params)) {
  GSIS_CHECK(options_.n_cis >= 1, std::format("GSIS の N_CIS は 1 以上です(値: {})", options_.n_cis));
  GSIS_CHECK(options_.max_pbc >= 1, std::format("GSIS の PBC の反復の上限 M は 1 以上です(値: {})", options_.max_pbc));
  GSIS_CHECK(options_.cfl_pbc > 0.0, "GSIS の PBC の CFL は正です");
  GSIS_CHECK(options_.init_iterations >= 0, "初期化の PBC の反復数は 0 以上です");
  GSIS_CHECK(options_.filter_length >= 0.0 && options_.filter_max_length >= 0.0 && options_.filter_min_cells >= 0.0,
             "補正のフィルタの長さ(filter_length・filter_max_length・filter_min_cells)は 0 以上です");
  volume_ = pbc_.geometry().cell_volume;
  // フィルタの長さの上限: 指定がなければ領域(セル中心の外接直方体)の対角線
  filter_cap_ = options_.filter_max_length;
  if (!(filter_cap_ > 0.0)) {
    const auto& xc = pbc_.geometry().cell_center;
    Vec3 lo = xc.empty() ? Vec3{0.0, 0.0, 0.0} : xc.front();
    Vec3 hi = lo;
    for (const Vec3& x : xc) {
      for (int a = 0; a < 3; ++a) {
        lo[a] = std::min(lo[a], x[a]);
        hi[a] = std::max(hi[a], x[a]);
      }
    }
    double d2 = 0.0;
    for (int a = 0; a < 3; ++a) d2 += (hi[a] - lo[a]) * (hi[a] - lo[a]);
    filter_cap_ = std::sqrt(d2);
  }
  // 断面(フェーズ 5c)。PBC は元のメッシュの番号なので、元のメッシュで作る
  if (!options_.sections.empty()) {
    pbc_cuts_ = build_section_cuts(mesh, geometry, kinetic_.setup().length_scale, options_.sections);
  }
}

const GsisInitReport& GsisSolver::initialize(const CellMacroState* initial, bool run_pbc,
                                             const std::function<void(const PbcIterationInfo&)>& on_pbc_iteration,
                                             const std::function<bool()>& should_stop) {
  Timer timer;
  init_ = GsisInitReport{};
  const std::size_t nc = volume_.size();
  CellMacroState w;
  if (initial != nullptr) {
    w = *initial;
    check_state_size(w, nc, "初期状態");
    for (std::size_t c = 0; c < nc; ++c) {
      GSIS_CHECK(w.rho[c] > 0.0 && w.T[c] > 0.0 && std::isfinite(w.rho[c]) && std::isfinite(w.T[c]),
                 std::format("初期状態のセル {} の密度・温度が正の有限値ではありません", c));
      w.p[c] = w.rho[c] * w.T[c];
    }
    init_.from_state = true;
  } else {
    const double p0 = kinetic_params_.initial_pressure;
    const double T0 = kinetic_params_.initial_temperature;
    GSIS_CHECK(p0 > 0.0 && T0 > 0.0, "一様な初期状態の圧力と温度は正にしてください");
    w.resize(nc);
    std::fill(w.p.begin(), w.p.end(), p0);
    std::fill(w.T.begin(), w.T.end(), T0);
    std::fill(w.rho.begin(), w.rho.end(), p0 / T0);
  }
  if (run_pbc && options_.init_iterations > 0) {
    // NSF のみの単独モード(仮定 A9)。一様な状態から始めるので CFL を残差に応じて立ち上げる(SER)
    PbcStageOptions o;
    o.max_iterations = static_cast<int>(options_.init_iterations);
    o.cfl = options_.cfl_pbc;
    o.cfl_start = options_.init_cfl_start;
    o.cfl_growth = options_.init_cfl_growth;
    o.tolerance = 0.0;  // [macro].pbc_tolerance
    o.on_iteration = on_pbc_iteration;
    o.should_stop = should_stop;
    // 初期化の移流項は [macro].init_advection(既定 1 次)。終わったら GSIS の段の設定に戻す
    const MacroParams stage_params = pbc_.params();
    if (stage_params.init_advection != stage_params.advection) {
      MacroParams ip = stage_params;
      ip.advection = stage_params.init_advection;
      pbc_.set_params(ip);
    }
    init_.pbc = pbc_.solve_standalone(w, o);
    if (stage_params.init_advection != stage_params.advection) pbc_.set_params(stage_params);
    init_.patch_mass_flow = init_.pbc.final_patch_mass_flow;
    init_.pbc_run = true;
  }
  kinetic_.initialize_equilibrium(w);
  w_ = std::move(w);
  iteration_ = 0;
  cis_iterations_ = 0;
  has_half_prev_ = false;
  last_ = GsisIterationInfo{};
  initialized_ = true;
  init_.seconds = timer.seconds();
  return init_;
}

const GsisIterationInfo& GsisSolver::iterate(const std::function<void(const PbcIterationInfo&)>& on_pbc_iteration) {
  GSIS_CHECK(initialized_, "GSIS の反復の前に initialize を呼んでください");
  Timer total;
  GsisIterationInfo info;

  // 1. CIS: f^n → f^{n+1/2}
  Timer t;
  double residual = 0.0;
  bool has_residual = false;
  kinetic::StepTimings cis_timings;
  for (int l = 0; l < options_.n_cis; ++l) {
    const KineticIterationInfo& s = kinetic_.sweep();
    residual = s.residual;
    has_residual = s.has_residual;
    cis_timings += s.timings;
  }
  cis_iterations_ += options_.n_cis;
  info.t_cis = t.seconds();

  // 2. f^{n+1/2} のモーメントと境界面の離散半空間フラックス
  t.reset();
  info.kinetic = kinetic_.prepare();
  info.kinetic.residual = residual;
  info.kinetic.has_residual = has_residual;
  info.kinetic.timings = cis_timings;
  KineticMoments m = kinetic_.moments();
  const BoundaryKineticFluxes fb = kinetic_.boundary_fluxes();
  info.patch_mass_flow_kinetic = info.kinetic.patch_mass_flow;
  if (!options_.sections.empty()) info.section_flow_kinetic = kinetic_.section_mass_flow(options_.sections);
  info.t_moments = t.seconds();

  // 3. PBC 段(HoT は段の間は固定、境界は整合補正つき)
  t.reset();
  CellMacroState w_new;
  info.pbc = pbc_.solve_gsis_stage(m, fb, options_.max_pbc, options_.cfl_pbc, w_new, on_pbc_iteration);
  info.patch_mass_flow_pbc = info.pbc.final_patch_mass_flow;
  if (!pbc_cuts_.empty()) {
    // 段の最後の状態 W^{n+1} で評価し直した内部面の質量フラックス
    const std::vector<double> face = pbc_.internal_face_mass_flux();
    for (const SectionCut& cut : pbc_cuts_) info.section_flow_pbc.push_back(section_flow(cut, face));
  }
  {
    const HigherOrderTerms hot = pbc_.last_hot();
    double ss = 0.0;
    double sq = 0.0;
    double vol = 0.0;
    for (std::size_t c = 0; c < volume_.size(); ++c) {
      const double V = volume_[c];
      double s2 = 0.0;
      for (double v : hot.sigma[c]) s2 += v * v;
      double q2 = 0.0;
      for (double v : hot.q[c]) q2 += v * v;
      ss += s2 * V;
      sq += q2 * V;
      vol += V;
    }
    info.hot_sigma_rms = vol > 0.0 ? std::sqrt(ss / vol) : 0.0;
    info.hot_q_rms = vol > 0.0 ? std::sqrt(sq / vol) : 0.0;
  }
  info.t_pbc = t.seconds();

  // 3b. 補正の低域フィルタ(論文にはない。docs/design_gsis.md §3)
  if (options_.filter_length > 0.0 || options_.filter_min_cells > 0.0) {
    t.reset();
    info.filter = pbc_.filter_correction(m.state, w_new, options_.filter_length, filter_cap_, options_.filter_min_cells);
    info.t_filter = t.seconds();
  }
  // 3c. 局所 Kn による補正の減衰(フェーズ 5b、[gsis].damp_kn。論文にはない): 希薄に近いセルほど補正を弱める
  //     (凍結 gsis_freeze_kn をなめらかにしたもの。不動点は変わらない)
  if (options_.damp_kn > 0.0) {
    const MacroGas& gas = pbc_.gas();
    for (std::size_t c = 0; c < volume_.size(); ++c) {
      const double T = m.state.T[c];
      const double kn = gas.mu(T) / (m.state.rho[c] * T) * std::sqrt(kPi * T / 2.0);
      const double s = kn / options_.damp_kn;
      const double wgt = 1.0 / (1.0 + s * s);
      if (!(wgt < 1.0)) continue;
      w_new.rho[c] = m.state.rho[c] * std::exp(wgt * std::log(w_new.rho[c] / m.state.rho[c]));
      w_new.ux[c] = m.state.ux[c] + wgt * (w_new.ux[c] - m.state.ux[c]);
      w_new.uy[c] = m.state.uy[c] + wgt * (w_new.uy[c] - m.state.uy[c]);
      w_new.uz[c] = m.state.uz[c] + wgt * (w_new.uz[c] - m.state.uz[c]);
      w_new.T[c] = T * std::exp(wgt * std::log(w_new.T[c] / T));
      w_new.p[c] = w_new.rho[c] * w_new.T[c];
    }
  }

  // 4. 補正(式 (14))と負値の置換
  t.reset();
  info.replaced_cells = kinetic_.apply_gsis_correction(m.state, w_new);
  info.t_correction = t.seconds();

  // 5. 収束の指標(式 (59))
  info.change = relative_change(w_new, w_, volume_, options_.u_floor);
  info.pbc_change = relative_change(w_new, m.state, volume_, options_.u_floor);
  if (has_half_prev_) info.change_half = relative_change(m.state, w_half_prev_, volume_, options_.u_floor);
  w_half_prev_ = std::move(m.state);
  has_half_prev_ = true;
  w_ = std::move(w_new);
  ++iteration_;
  info.iteration = iteration_;
  info.cis_iterations = cis_iterations_;
  info.t_total = total.seconds();
  last_ = std::move(info);
  return last_;
}

}  // namespace gsis
