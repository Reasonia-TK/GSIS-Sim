#include "kinetic/kinetic_solver.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <format>
#include <limits>

#include "core/error.hpp"
#include "core/log.hpp"
#include "core/timer.hpp"
#include "kinetic/kinetic_kernels.hpp"
#include "kinetic/kinetic_types.hpp"
#include "physics/limiter.hpp"

namespace gsis {

using kinetic::kBcDiffuseWall;
using kinetic::kBcPeriodic;
using kinetic::kBcPressure;
using kinetic::kBcSymmetry;
using kinetic::kBcVacuum;
using kinetic::kBoundarySumsPerFace;
using kinetic::kMomentsPerCell;

struct KineticSolver::Impl {
  kinetic::KineticSetup s;
  std::unique_ptr<kinetic::KineticBackend> backend;

  long long iteration = 0;
  long long version = 0;           // f を変えるたびに増やす
  long long prepared_version = -1;
  KineticIterationInfo info;

  std::vector<double> moments;       // f^ℓ のモーメント(内部の番号、15 個/セル、規格化の後)
  std::vector<double> prev_moments;  // f^(ℓ−1) のモーメント
  bool has_prev = false;
  std::vector<double> bsums;         // 境界の集計(20 個/面)
  std::vector<kinetic::InflowState> inflow;
  std::vector<std::array<double, 5>> F_plus, F_minus;  // 内部の番号
  std::vector<std::array<double, 5>> face_state;       // [ρ, ux, uy, uz, T](内部の番号)
  bool has_face_state = false;
  std::vector<double> residual_cell;
  std::vector<double> inner_residual;  // 内部反復の 2 回目以降の残差(使わない)
  std::vector<char> replaced_cells;  // 直前の GSIS の補正で置き換えたセル(元のセル番号)
  double mass_target = 0.0;
  bool has_mass_target = false;
  // 断面の質量流量(フェーズ 5c)。断面の面(内部の番号付け)と、面ごとの owner のセル・CSR の要素
  SectionSpec section_spec;
  bool has_sections = false;
  std::vector<SectionCut> section_cuts;
  std::vector<std::int32_t> section_cells, section_entries;

  void build_sections(const SectionSpec& spec) {
    if (has_sections && spec == section_spec) return;
    section_cuts = build_section_cuts(s.mesh, s.geometry, s.length_scale, spec);
    section_cells.clear();
    section_entries.clear();
    for (const SectionCut& cut : section_cuts) {
      for (const Index f : cut.faces) {
        const Index o = s.mesh.owner[static_cast<std::size_t>(f)];
        Offset e = -1;
        for (Offset k = s.csr.begin(o); k < s.csr.end(o); ++k) {
          if (s.csr.faces[static_cast<std::size_t>(k)] == f) {
            e = k;
            break;
          }
        }
        GSIS_CHECK(e >= 0 && s.csr.sign[static_cast<std::size_t>(e)] > 0,
                   std::format("断面の面 {} がセル {} の面の一覧にありません", f, o));
        section_cells.push_back(static_cast<std::int32_t>(o));
        section_entries.push_back(static_cast<std::int32_t>(e));
      }
    }
    section_spec = spec;
    has_sections = true;
  }

  Impl(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry, const KineticParams& params)
      : s(kinetic::build_kinetic_setup(config, mesh, geometry, params)) {
    if (params.backend == "cpu") {
      backend = kinetic::make_cpu_backend(s);
    } else {
      backend = kinetic::make_cuda_backend(s);
    }
    const auto nc = static_cast<std::size_t>(s.n_cells());
    const auto nb = static_cast<std::size_t>(s.n_bfaces());
    moments.assign(nc * kMomentsPerCell, 0.0);
    prev_moments.assign(nc * kMomentsPerCell, 0.0);
    bsums.assign(nb * kBoundarySumsPerFace, 0.0);
    inflow.assign(nb, kinetic::InflowState{});
    F_plus.assign(nb, {});
    F_minus.assign(nb, {});
    face_state.assign(nb, {});
    residual_cell.assign(nc, 0.0);
    init_static_inflow();
  }

  // 保存的な離散平衡分布([kinetic].equilibrium = "discrete"):流入の Maxwell 分布を、速度格子の上の離散モーメントが
  // (ρ, ρu, ρE) に一致する Maxwell 分布(係数を変えた (ρ', u', T'))に置き換える。十分に解像されていれば何もしない
  void correct_inflow(kinetic::InflowState& in) const {
    if (!s.discrete_eq || !(in.rho > 0.0) || !(in.T > 0.0)) return;
    if (kinetic::deq_resolved(s.deq_grid, in.u.x, in.u.y, in.u.z, in.T)) return;
    kinetic::DeqGrid g = s.deq_grid;
    g.tolerance = 1e-10;
    MaxwellParams<double> m;
    double gamma[5];
    const int r = kinetic::discrete_maxwell_seq<double>(s.velocity.xi_x.data(), s.velocity.xi_y.data(),
                                                        s.velocity.xi_z.data(), s.velocity.nv, s.velocity.weight, g,
                                                        in.rho, in.u.x, in.u.y, in.u.z, in.T, nullptr, m, gamma);
    if (r > 0) kinetic::maxwell_params_to_state(m, in.rho, in.u.x, in.u.y, in.u.z, in.T);
  }

  // 壁の Maxwell 分布(単位密度)と真空の流入分布を設定する。圧力境界は prepare で W_b から作る
  void init_static_inflow() {
    for (Index b = 0; b < s.n_bfaces(); ++b) {
      const kinetic::PatchCondition& pc = s.patches[static_cast<std::size_t>(s.bface_patch[static_cast<std::size_t>(b)])];
      kinetic::InflowState& in = inflow[static_cast<std::size_t>(b)];
      if (pc.kind == kBcDiffuseWall) {
        in.rho = 1.0;
        in.u = pc.u_wall;
        in.T = pc.T;
        in.scale = 1.0;  // ρ_Γ は prepare で決める(init_wall_scale_from_cells、process_boundary_sums)
        correct_inflow(in);
      } else if (pc.kind == kBcPressure) {
        in.scale = 1.0;
        in.rho = pc.p / pc.T;
        in.T = pc.T;
      } else {
        in = kinetic::InflowState{};
      }
    }
  }

  void reset_after_initialization() {
    ++version;
    iteration = 0;
    has_prev = false;
    has_face_state = false;
    has_mass_target = false;
    init_static_inflow();
  }

  // 初期化の直後の壁の ρ_Γ の仮の値(最初の境界の集計で、壁際のセルの勾配に使う)。
  // 隣接セルが平衡分布で壁に垂直な速度が 0 なら j⁺ = ρ_i √(T_i/2π)、j⁻⁽¹⁾ = −√(T_w/2π) なので
  // ρ_Γ = ρ_i √(T_i/T_w)
  void init_wall_scale_from_cells() {
    const Index n_int = s.mesh.n_internal_faces();
    for (Index b = 0; b < s.n_bfaces(); ++b) {
      const kinetic::PatchCondition& pc = s.patches[static_cast<std::size_t>(s.bface_patch[static_cast<std::size_t>(b)])];
      if (pc.kind != kBcDiffuseWall) continue;
      const Index c = s.mesh.owner[static_cast<std::size_t>(b + n_int)];
      inflow[static_cast<std::size_t>(b)].scale = cell_value(moments, c, 0) * std::sqrt(cell_value(moments, c, 4) / pc.T);
    }
  }

  double cell_value(const std::vector<double>& m, Index c, int k) const {
    return m[static_cast<std::size_t>(c) * kMomentsPerCell + static_cast<std::size_t>(k)];
  }

  // 圧力境界の状態 W_b(付録 A.2)。W_{i,b} はセルの (p, u, T) の制限付き再構成(境界の値は前回の
  // 境界面の分布のモーメント。仮定 A7)
  void update_pressure_inflow() {
    const Index n_int = s.mesh.n_internal_faces();
    const double K = s.params.limiter_k;
    const bool limit = s.limiter_mode != kinetic::kLimiterNone;
    for (const kinetic::PatchCondition& pc : s.patches) {
      if (pc.kind != kBcPressure) continue;
      for (Index b = pc.first_bface; b < pc.first_bface + pc.n_faces; ++b) {
        const auto f = static_cast<std::size_t>(b + n_int);
        const Index ci = s.mesh.owner[f];
        // セルの値 (p, ux, uy, uz, T)
        auto state_of_cell = [&](Index c) {
          return std::array<double, 5>{cell_value(moments, c, 5), cell_value(moments, c, 1), cell_value(moments, c, 2),
                                       cell_value(moments, c, 3), cell_value(moments, c, 4)};
        };
        const std::array<double, 5> wi = state_of_cell(ci);
        std::array<double, 5> wmax = wi, wmin = wi;
        std::array<Vec3, 5> grad{};
        for (Offset k = s.csr.begin(ci); k < s.csr.end(ci); ++k) {
          const auto ki = static_cast<std::size_t>(k);
          std::array<double, 5> wk;
          if (s.csr.other[ki] != kInvalidIndex) {
            wk = state_of_cell(s.csr.other[ki]);
          } else {
            const auto bk = static_cast<std::size_t>(s.csr.faces[ki] - n_int);
            const int kind = s.patches[static_cast<std::size_t>(s.bface_patch[bk])].kind;
            const auto& fs = face_state[bk];
            if (has_face_state && kind != kBcPeriodic && fs[0] > 0.0 && fs[4] > 0.0) {
              wk = {fs[0] * fs[4], fs[1], fs[2], fs[3], fs[4]};
            } else {
              wk = wi;
            }
          }
          for (int q = 0; q < 5; ++q) {
            const double d = wk[static_cast<std::size_t>(q)] - wi[static_cast<std::size_t>(q)];
            grad[static_cast<std::size_t>(q)] += d * s.lsq.coeff[ki];
            wmax[static_cast<std::size_t>(q)] = std::max(wmax[static_cast<std::size_t>(q)], wk[static_cast<std::size_t>(q)]);
            wmin[static_cast<std::size_t>(q)] = std::min(wmin[static_cast<std::size_t>(q)], wk[static_cast<std::size_t>(q)]);
          }
        }
        const Vec3 dxb = s.geometry.face_center[f] - s.geometry.cell_center[static_cast<std::size_t>(ci)];
        const double eps2 = K * K * K * s.volume[static_cast<std::size_t>(ci)];
        std::array<double, 5> wib{};
        for (int q = 0; q < 5; ++q) {
          const auto qi = static_cast<std::size_t>(q);
          double phi = 1.0;
          if (limit) {
            VenkatakrishnanAccumulator<double> acc(wmax[qi] - wi[qi], wmin[qi] - wi[qi], eps2);
            for (Offset k = s.csr.begin(ci); k < s.csr.end(ci); ++k) {
              const auto ki = static_cast<std::size_t>(k);
              const Index fk = s.csr.faces[ki];
              if (s.csr.other[ki] == kInvalidIndex &&
                  s.patches[static_cast<std::size_t>(s.bface_patch[static_cast<std::size_t>(fk - n_int)])].kind ==
                      kBcPeriodic) {
                continue;
              }
              acc.add(dot(grad[qi], s.geometry.face_center[static_cast<std::size_t>(fk)] -
                                        s.geometry.cell_center[static_cast<std::size_t>(ci)]));
            }
            phi = acc.value();
          }
          wib[qi] = reconstruct_limited(wi[qi], grad[qi], phi, dxb);
        }
        if (!(wib[0] > 0.0 && wib[4] > 0.0)) wib = wi;  // 正でなくなったらセルの値を使う
        if (s.params.pressure_boundary_reconstruction == "cell") wib = wi;  // セルの値(フェーズ 5b の選択肢)
        const double p_ib = wib[0];
        const Vec3 u_ib{wib[1], wib[2], wib[3]};
        const double T_ib = wib[4];
        const double rho_ib = p_ib / T_ib;
        const double cs = std::sqrt(s.gamma * T_ib);
        const Vec3& n = s.geometry.face_normal[f];
        double p_b, T_b;
        if (pc.type == BoundaryType::PressureInlet) {
          p_b = pc.p;
          T_b = pc.T;
        } else {
          p_b = pc.p;
          T_b = T_ib;
        }
        const double du = (p_ib - p_b) / (rho_ib * cs);
        kinetic::InflowState& in = inflow[static_cast<std::size_t>(b)];
        in.scale = 1.0;
        in.rho = p_b / T_b;
        // 接線成分を 0 にする入口(BoundarySpec::inlet_zero_tangential)は法線成分だけ
        in.u = pc.zero_tangential ? (dot(u_ib, n) + du) * n : u_ib + du * n;
        in.T = T_b;
        correct_inflow(in);
      }
    }
  }

  // 境界の集計から ρ_Γ、F^±、境界面の分布のモーメント、パッチの質量流量を作る
  void process_boundary_sums() {
    const Index nb = s.n_bfaces();
    const double L = s.length_scale;
    const Index n_int = s.mesh.n_internal_faces();
    info.patch_mass_flow.assign(s.patches.size(), 0.0);
    for (Index b = 0; b < nb; ++b) {
      const auto bi = static_cast<std::size_t>(b);
      const double* sm = &bsums[bi * kBoundarySumsPerFace];
      const int pi = s.bface_patch[bi];
      const kinetic::PatchCondition& pc = s.patches[static_cast<std::size_t>(pi)];
      const double area = s.geometry.face_area[static_cast<std::size_t>(b + n_int)] / (L * L);
      double in_scale = 1.0;
      if (pc.kind == kBcDiffuseWall) {
        // 付録 A.1(j_Γ = 0): ρ_Γ = −j⁺ / j⁻⁽¹⁾
        const double jp = sm[0];
        const double jm = sm[5];
        const double rho_g = jm < 0.0 ? std::max(0.0, -jp / jm) : 0.0;
        const double w = s.params.wall_relaxation;
        inflow[bi].scale = w == 1.0 ? rho_g : w * rho_g + (1.0 - w) * inflow[bi].scale;
        in_scale = rho_g;  // F⁻ と境界面のモーメントは緩和しない値(壁の正味質量流束 0)
      } else if (pc.kind == kBcVacuum) {
        in_scale = 0.0;
      }
      std::array<double, 5> mom{};
      for (int k = 0; k < 5; ++k) {
        const auto ks = static_cast<std::size_t>(k);
        F_plus[bi][ks] = area * sm[k];
        F_minus[bi][ks] = area * in_scale * sm[5 + k];
        mom[ks] = sm[10 + k] + in_scale * sm[15 + k];
      }
      auto& fs = face_state[bi];
      if (mom[0] > 0.0) {
        const Vec3 u{mom[1] / mom[0], mom[2] / mom[0], mom[3] / mom[0]};
        fs = {mom[0], u.x, u.y, u.z, (2.0 * mom[4] / mom[0] - norm2(u)) / 3.0};
      } else {
        fs = {0.0, 0.0, 0.0, 0.0, 0.0};
      }
      info.patch_mass_flow[static_cast<std::size_t>(pi)] += F_plus[bi][0] + F_minus[bi][0];
    }
    has_face_state = true;
  }

  // 式 (59)。u の分母には下限 floor² ΣV を設ける
  void compute_epsilon() {
    double num[3] = {0, 0, 0};
    double den[3] = {0, 0, 0};
    double vol = 0.0;
    for (Index c = 0; c < s.n_cells(); ++c) {
      const double V = s.volume[static_cast<std::size_t>(c)];
      const double* a = &moments[static_cast<std::size_t>(c) * kMomentsPerCell];
      const double* b = &prev_moments[static_cast<std::size_t>(c) * kMomentsPerCell];
      num[0] += (a[0] - b[0]) * (a[0] - b[0]) * V;
      den[0] += b[0] * b[0] * V;
      const double du2 = (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]) + (a[3] - b[3]) * (a[3] - b[3]);
      num[1] += du2 * V;
      den[1] += (b[1] * b[1] + b[2] * b[2] + b[3] * b[3]) * V;
      num[2] += (a[4] - b[4]) * (a[4] - b[4]) * V;
      den[2] += b[4] * b[4] * V;
      vol += V;
    }
    const double floor = s.params.convergence_u_floor;
    den[1] = std::max(den[1], floor * floor * vol);
    auto ratio = [](double n, double d) { return d > 0.0 ? std::sqrt(n / d) : (n > 0.0 ? 1.0 : 0.0); };
    info.eps_rho = ratio(num[0], den[0]);
    info.eps_u = ratio(num[1], den[1]);
    info.eps_T = ratio(num[2], den[2]);
    info.epsilon = std::max({info.eps_rho, info.eps_u, info.eps_T});
    info.has_epsilon = true;
  }

  const KineticIterationInfo& prepare() {
    if (prepared_version == version) return info;
    Timer timer;
    info = KineticIterationInfo{};
    info.iteration = iteration;
    backend->compute_moments(moments);
    // 密度と温度が正であること
    Index bad = 0;
    Index first_bad = -1;
    for (Index c = 0; c < s.n_cells(); ++c) {
      const double rho = cell_value(moments, c, 0);
      const double T = cell_value(moments, c, 4);
      if (!(rho > 0.0) || !(T > 0.0) || !std::isfinite(rho) || !std::isfinite(T)) {
        if (bad == 0) first_bad = c;
        ++bad;
      }
    }
    GSIS_CHECK(bad == 0, std::format("反復 {} で密度か温度が正でないセルが {} 個あります(例: 元のセル {}、ρ = {:.4g}、"
                                     "T = {:.4g})",
                                     iteration, bad, s.cell_new_to_old[static_cast<std::size_t>(first_bad)],
                                     cell_value(moments, first_bad, 0), cell_value(moments, first_bad, 4)));
    double mass = 0.0;
    for (Index c = 0; c < s.n_cells(); ++c) mass += cell_value(moments, c, 0) * s.volume[static_cast<std::size_t>(c)];
    info.total_mass = mass;
    if (s.closed_domain) {
      if (!has_mass_target) {
        mass_target = mass;
        has_mass_target = true;
      }
      const double scale = mass_target / mass;
      if (scale != 1.0) {
        backend->scale_distribution(scale);
        for (Index c = 0; c < s.n_cells(); ++c) {
          double* m = &moments[static_cast<std::size_t>(c) * kMomentsPerCell];
          m[0] *= scale;
          for (int k = 5; k < kMomentsPerCell; ++k) m[k] *= scale;
        }
      }
      info.mass_scale = scale;
    }
    if (has_prev) compute_epsilon();
    {
      double vol = 0.0;
      for (Index c = 0; c < s.n_cells(); ++c) {
        const double V = s.volume[static_cast<std::size_t>(c)];
        const double* m = &moments[static_cast<std::size_t>(c) * kMomentsPerCell];
        for (std::size_t k = 0; k < 3; ++k) info.q_mean[k] += V * m[12 + k];
        for (std::size_t k = 0; k < 6; ++k) info.sigma_mean[k] += V * m[6 + k];
        vol += V;
      }
      for (double& x : info.q_mean) x /= vol;
      for (double& x : info.sigma_mean) x /= vol;
    }
    backend->update_cell_params();
    if (s.discrete_eq) {
      const kinetic::DeqCounts dc = backend->deq_counts();
      info.deq_corrected = dc.corrected;
      info.deq_failed = dc.failed;
      info.deq_max_iterations = dc.max_iterations;
      if (dc.failed > 0) {
        if (s.params.deq_fallback == "collisionless") {
          log_info("反復 {}: 離散平衡のニュートン法が {} セルで収束しないので、そのセルは衝突なしにしました", iteration,
                   dc.failed);
        } else {
          log_warn("反復 {}: 離散平衡のニュートン法が {} セルで収束せず、連続の Maxwell 分布を使いました", iteration,
                   dc.failed);
        }
      }
    }
    if (!has_face_state) init_wall_scale_from_cells();
    update_pressure_inflow();
    backend->set_inflow(inflow);
    backend->boundary_sums(bsums);
    process_boundary_sums();
    backend->set_inflow(inflow);
    // f とセルの目標分布を固定し、再構成に使う境界値と流入分布の遅れだけを解消する。
    // 既定の1回では、上の従来の評価順をそのまま使う。
    for (long long b = 1; b < s.params.boundary_iterations; ++b) {
      update_pressure_inflow();
      backend->set_inflow(inflow);
      backend->boundary_sums(bsums);
      process_boundary_sums();
      backend->set_inflow(inflow);
    }
    prepared_version = version;
    info.seconds = timer.seconds();
    return info;
  }

  // 内部反復の間の拡散壁の流入密度 ρ_Γ の更新(inner_wall_update)。いまの f の流出側の離散流束 j⁺ から
  // ρ_Γ = −j⁺/j⁻⁽¹⁾ を作り直す(F^±・境界面のモーメント・パッチの流量は prepare の値のまま)
  void refresh_wall_inflow() {
    backend->boundary_sums(bsums);
    bool any = false;
    for (Index b = 0; b < s.n_bfaces(); ++b) {
      const auto bi = static_cast<std::size_t>(b);
      const kinetic::PatchCondition& pc = s.patches[static_cast<std::size_t>(s.bface_patch[bi])];
      if (pc.kind != kBcDiffuseWall) continue;
      const double* sm = &bsums[bi * kBoundarySumsPerFace];
      const double jp = sm[0];
      const double jm = sm[5];
      const double rho_g = jm < 0.0 ? std::max(0.0, -jp / jm) : 0.0;
      const double w = s.params.wall_relaxation;
      inflow[bi].scale = w == 1.0 ? rho_g : w * rho_g + (1.0 - w) * inflow[bi].scale;
      any = true;
    }
    if (any) backend->set_inflow(inflow);
  }

  const KineticIterationInfo& sweep() {
    prepare();
    Timer timer;
    backend->sweep(residual_cell);
    // 内部反復(inner_sweeps > 1): 目標分布 f^S(と圧力境界の W_b)を固定したまま LU-SGS をくり返す。
    // 残差の記録は最初の 1 回(f^ℓ の残差)
    for (long long m = 1; m < s.params.inner_sweeps; ++m) {
      if (s.params.inner_wall_update) refresh_wall_inflow();
      inner_residual.resize(residual_cell.size());
      backend->sweep(inner_residual);
    }
    double sum = 0.0;
    double vol = 0.0;
    for (Index c = 0; c < s.n_cells(); ++c) {
      const double V = s.volume[static_cast<std::size_t>(c)];
      sum += residual_cell[static_cast<std::size_t>(c)] / V;
      vol += V;
    }
    info.residual = std::sqrt(sum * s.velocity.weight / vol);
    info.has_residual = true;
    prev_moments = moments;
    has_prev = true;
    ++version;
    ++iteration;
    info.seconds += timer.seconds();
    info.timings = backend->take_timings();
    return info;
  }
};

KineticSolver::KineticSolver(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry,
                             const KineticParams& params)
    : impl_(std::make_unique<Impl>(config, mesh, geometry, params)) {}

KineticSolver::~KineticSolver() = default;

const kinetic::KineticSetup& KineticSolver::setup() const { return impl_->s; }
const kinetic::KineticBackend& KineticSolver::backend() const { return *impl_->backend; }
long long KineticSolver::iteration() const { return impl_->iteration; }
double KineticSolver::mass_target() const { return impl_->has_mass_target ? impl_->mass_target : 0.0; }

void KineticSolver::initialize_uniform(double rho, const Vec3& u, double T) {
  GSIS_CHECK(rho > 0.0 && T > 0.0, "初期状態の密度と温度は正にしてください");
  const auto nc = static_cast<std::size_t>(impl_->s.n_cells());
  std::vector<double> states(nc * 5);
  for (std::size_t c = 0; c < nc; ++c) {
    states[c * 5 + 0] = rho;
    states[c * 5 + 1] = u.x;
    states[c * 5 + 2] = u.y;
    states[c * 5 + 3] = u.z;
    states[c * 5 + 4] = T;
  }
  impl_->backend->set_equilibrium(states);
  impl_->reset_after_initialization();
}

void KineticSolver::initialize_equilibrium(const CellMacroState& state) {
  const kinetic::KineticSetup& s = impl_->s;
  const auto nc = static_cast<std::size_t>(s.n_cells());
  GSIS_CHECK(state.rho.size() == nc && state.ux.size() == nc && state.uy.size() == nc && state.uz.size() == nc &&
                 state.T.size() == nc,
             "初期状態のセル数がメッシュと合いません");
  std::vector<double> states(nc * 5);
  for (std::size_t c = 0; c < nc; ++c) {
    const auto o = static_cast<std::size_t>(s.cell_new_to_old[c]);
    GSIS_CHECK(state.rho[o] > 0.0 && state.T[o] > 0.0, std::format("セル {} の初期状態の密度・温度が正ではありません", o));
    states[c * 5 + 0] = state.rho[o];
    states[c * 5 + 1] = state.ux[o];
    states[c * 5 + 2] = state.uy[o];
    states[c * 5 + 3] = state.uz[o];
    states[c * 5 + 4] = state.T[o];
  }
  impl_->backend->set_equilibrium(states);
  impl_->reset_after_initialization();
}

const KineticIterationInfo& KineticSolver::prepare() { return impl_->prepare(); }
const KineticIterationInfo& KineticSolver::sweep() { return impl_->sweep(); }

KineticMoments KineticSolver::moments() {
  impl_->prepare();
  const kinetic::KineticSetup& s = impl_->s;
  const auto nc = static_cast<std::size_t>(s.n_cells());
  KineticMoments out;
  out.state.resize(nc);
  out.sigma.resize(nc);
  out.q.resize(nc);
  for (std::size_t c = 0; c < nc; ++c) {
    const auto o = static_cast<std::size_t>(s.cell_new_to_old[c]);
    const double* m = &impl_->moments[c * kMomentsPerCell];
    out.state.rho[o] = m[0];
    out.state.ux[o] = m[1];
    out.state.uy[o] = m[2];
    out.state.uz[o] = m[3];
    out.state.T[o] = m[4];
    out.state.p[o] = m[5];
    out.sigma[o] = {m[6], m[7], m[8], m[9], m[10], m[11]};
    out.q[o] = {m[12], m[13], m[14]};
  }
  return out;
}

BoundaryKineticFluxes KineticSolver::boundary_fluxes() {
  impl_->prepare();
  const kinetic::KineticSetup& s = impl_->s;
  const auto nb = static_cast<std::size_t>(s.n_bfaces());
  BoundaryKineticFluxes out;
  out.F_plus.resize(nb);
  out.F_minus.resize(nb);
  out.face_state.resize(nb);
  for (std::size_t b = 0; b < nb; ++b) {
    const auto o = static_cast<std::size_t>(s.bface_new_to_old[b]);
    out.F_plus[o] = impl_->F_plus[b];
    out.F_minus[o] = impl_->F_minus[b];
    out.face_state[o] = impl_->face_state[b];
  }
  return out;
}

long long KineticSolver::apply_gsis_correction(const CellMacroState& w_half, const CellMacroState& w_new) {
  const kinetic::KineticSetup& s = impl_->s;
  const auto nc = static_cast<std::size_t>(s.n_cells());
  GSIS_CHECK(w_half.size() == nc && w_new.size() == nc, "GSIS の補正のマクロ量のセル数がメッシュと合いません");
  std::vector<double> wh(nc * 5), wn(nc * 5);
  for (std::size_t c = 0; c < nc; ++c) {
    const auto o = static_cast<std::size_t>(s.cell_new_to_old[c]);
    GSIS_CHECK(w_new.rho[o] > 0.0 && w_new.T[o] > 0.0 && w_half.rho[o] > 0.0 && w_half.T[o] > 0.0,
               std::format("セル {} の補正のマクロ量の密度・温度が正ではありません", o));
    const double a[5] = {w_half.rho[o], w_half.ux[o], w_half.uy[o], w_half.uz[o], w_half.T[o]};
    const double b[5] = {w_new.rho[o], w_new.ux[o], w_new.uy[o], w_new.uz[o], w_new.T[o]};
    for (std::size_t k = 0; k < 5; ++k) {
      wh[c * 5 + k] = a[k];
      wn[c * 5 + k] = b[k];
    }
  }
  std::vector<int> flags(nc, 0);
  const long long replaced = impl_->backend->gsis_correction(wh, wn, std::span<int>(flags));
  impl_->replaced_cells.assign(nc, 0);
  long long skipped = 0;
  for (std::size_t c = 0; c < nc; ++c) {
    impl_->replaced_cells[static_cast<std::size_t>(s.cell_new_to_old[c])] = static_cast<char>(flags[c] != 0);
    if (flags[c] == kinetic::kCorrectionSkipped) ++skipped;
  }
  if (skipped > 0) {
    log_info("GSIS の補正: 離散平衡が作れない {} セルは補正しませんでした(kinetic.deq_fallback = \"collisionless\")",
             skipped);
  }
  ++impl_->version;
  return replaced;
}

const std::vector<char>& KineticSolver::replaced_cells() const { return impl_->replaced_cells; }

std::vector<double> KineticSolver::residual_per_cell() const {
  const kinetic::KineticSetup& s = impl_->s;
  const auto nc = static_cast<std::size_t>(s.n_cells());
  std::vector<double> out(nc, 0.0);
  for (std::size_t c = 0; c < nc; ++c) {
    const auto o = static_cast<std::size_t>(s.cell_new_to_old[c]);
    out[o] = std::sqrt(s.velocity.weight * impl_->residual_cell[c]) / s.volume[c];
  }
  return out;
}

std::vector<double> KineticSolver::section_mass_flow(const SectionSpec& spec) {
  if (spec.empty()) return {};
  impl_->prepare();
  impl_->build_sections(spec);
  std::vector<double> face(impl_->section_cells.size(), 0.0);
  impl_->backend->internal_face_mass_flux(impl_->section_cells, impl_->section_entries, face);
  // 面の流束は owner(内部の番号付けのメッシュ)から見て外向き = 面積ベクトルの向き。断面ごとに sign を掛けて足す
  std::vector<double> out;
  out.reserve(impl_->section_cuts.size());
  std::size_t k = 0;
  for (const SectionCut& cut : impl_->section_cuts) {
    double sum = 0.0;
    for (std::size_t j = 0; j < cut.faces.size(); ++j, ++k) sum += static_cast<double>(cut.sign[j]) * face[k];
    out.push_back(sum);
  }
  return out;
}

const std::vector<SectionCut>& KineticSolver::section_cuts(const SectionSpec& spec) {
  impl_->build_sections(spec);
  return impl_->section_cuts;
}

std::vector<double> KineticSolver::distribution() {
  const kinetic::KineticSetup& s = impl_->s;
  const auto nc = static_cast<std::size_t>(s.n_cells());
  const auto nv = static_cast<std::size_t>(s.nv());
  std::vector<double> internal(nc * nv);
  impl_->backend->get_distribution(internal);
  std::vector<double> out(nc * nv);
  for (std::size_t c = 0; c < nc; ++c) {
    const auto o = static_cast<std::size_t>(s.cell_new_to_old[c]);
    for (std::size_t p = 0; p < nv; ++p) {
      out[o * nv + static_cast<std::size_t>(s.velocity.storage_to_grid[p])] = internal[c * nv + p];
    }
  }
  return out;
}

void KineticSolver::set_distribution(std::span<const double> f) {
  const kinetic::KineticSetup& s = impl_->s;
  const auto nc = static_cast<std::size_t>(s.n_cells());
  const auto nv = static_cast<std::size_t>(s.nv());
  GSIS_CHECK(f.size() == nc * nv, "分布関数の長さがセル数 × 速度点の数と合いません");
  std::vector<double> internal(nc * nv);
  for (std::size_t c = 0; c < nc; ++c) {
    const auto o = static_cast<std::size_t>(s.cell_new_to_old[c]);
    for (std::size_t p = 0; p < nv; ++p) {
      internal[c * nv + p] = f[o * nv + static_cast<std::size_t>(s.velocity.storage_to_grid[p])];
    }
  }
  impl_->backend->set_distribution(internal);
  impl_->reset_after_initialization();
}

}  // namespace gsis
