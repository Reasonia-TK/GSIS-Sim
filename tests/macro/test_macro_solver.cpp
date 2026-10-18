// macro: PBC ソルバー全体の試験(保存、平衡の保持、閉じた領域の全質量、決定性、平板の解析解)

#include <algorithm>
#include <array>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <vector>

#include "macro/linear_solver.hpp"
#include "macro/pbc_solver.hpp"
#include "mesh/geometry.hpp"
#include "mesh/section_cut.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using namespace gsis::macro;

namespace {

struct Problem {
  PolyMesh mesh;
  MeshGeometry geometry;
};

Problem tube_box(std::array<int, 3> n, double distortion, bool tets) {
  test::BoxMeshOptions o = test::tube_like_box(n, Vec3{0.0, -1.0, -1.0}, Vec3{4.0, 1.0, 1.0});
  o.distortion = distortion;
  o.tetrahedra = tets;
  o.seed = 3;
  Problem p;
  p.mesh = test::make_box_mesh(o);
  p.geometry = compute_geometry(p.mesh);
  return p;
}

std::vector<MacroBoundaryCondition> tube_bcs(const PolyMesh& mesh, double p_in) {
  std::vector<MacroBoundaryCondition> bcs;
  for (const Patch& p : mesh.patches) {
    MacroBoundaryCondition b;
    if (p.name == "inlet") {
      b.kind = MacroBoundaryKind::PressureInlet;
      b.pressure = p_in;
      b.temperature = 1.0;
    } else if (p.name == "outlet") {
      b.kind = MacroBoundaryKind::PressureOutlet;
      b.pressure = 0.6;
    } else {
      b.kind = MacroBoundaryKind::DiffuseWall;
      b.temperature = 1.0;
    }
    bcs.push_back(b);
  }
  return bcs;
}

MacroGas gas_with_mu(double mu) {
  MacroGas g;
  g.mu_ref = mu;
  g.T_ref = 1.0;
  g.omega = 0.66;
  return g;
}

CellMacroState uniform_state(std::size_t n, double p = 1.0, double T = 1.0) {
  CellMacroState s;
  s.resize(n);
  std::fill(s.p.begin(), s.p.end(), p);
  std::fill(s.T.begin(), s.T.end(), T);
  std::fill(s.rho.begin(), s.rho.end(), p / T);
  return s;
}

CellMacroState random_state(const MeshGeometry& g, std::size_t n, unsigned seed) {
  std::mt19937 rng(seed);
  std::uniform_real_distribution<double> U(-1.0, 1.0);
  CellMacroState s;
  s.resize(n);
  for (std::size_t c = 0; c < n; ++c) {
    const Vec3& x = g.cell_center[c];
    s.p[c] = 1.0 - 0.1 * x.x + 0.02 * U(rng);
    s.T[c] = 1.0 + 0.05 * U(rng);
    s.ux[c] = 0.3 + 0.05 * U(rng);
    s.uy[c] = 0.05 * U(rng);
    s.uz[c] = 0.05 * U(rng);
    s.rho[c] = s.p[c] / s.T[c];
  }
  return s;
}

bool bitwise_equal(const std::vector<double>& a, const std::vector<double>& b) {
  return a.size() == b.size() && std::memcmp(a.data(), b.data(), a.size() * sizeof(double)) == 0;
}

}  // namespace

TEST_CASE("internal fluxes cancel: the sum of cell residuals equals the boundary flux", "[macro][conservation]") {
  for (const bool tets : {false, true}) {
    Problem pr = tube_box(tets ? std::array<int, 3>{4, 2, 2} : std::array<int, 3>{6, 4, 4}, tets ? 0.0 : 0.2, tets);
    for (const char* adv : {"upwind1", "upwind2"}) {
      MacroParams params;
      params.advection = adv;
      PbcSolver solver(pr.mesh, pr.geometry, 1.0, gas_with_mu(0.02), tube_bcs(pr.mesh, 1.2), params);
      const CellMacroState W = random_state(solver.geometry(), static_cast<std::size_t>(pr.mesh.n_cells), 9);
      const auto R = solver.evaluate_residual(W);
      const PbcBoundaryFaceData& bd = solver.last_boundary();
      for (int k = 0; k < 5; ++k) {
        double sum_r = 0.0;
        double sum_b = 0.0;
        double scale = 0.0;
        for (const auto& r : R) {
          sum_r += r[static_cast<std::size_t>(k)];
          scale += std::abs(r[static_cast<std::size_t>(k)]);
        }
        for (const Vec5& f : bd.flux) {
          sum_b += f[k];
          scale += std::abs(f[k]);
        }
        CHECK(std::abs(sum_r - sum_b) <= 1e-13 * scale);
      }
    }
  }
}

TEST_CASE("the section flow of the internal face mass flux equals the mass residual of the cells before the section",
          "[macro][conservation][section]") {
  // フェーズ 5c: 断面(mesh/section_cut.hpp)を通る PBC の内部面の質量フラックスの和は、断面の手前のセルの質量の残差の和から
  // その境界面のフラックスを引いたものに等しい(任意の状態で成り立つ離散的な恒等式)
  for (const bool tets : {false, true}) {
    Problem pr = tube_box(tets ? std::array<int, 3>{4, 2, 2} : std::array<int, 3>{6, 4, 4}, tets ? 0.0 : 0.2, tets);
    for (const char* adv : {"upwind1", "upwind2"}) {
      MacroParams params;
      params.advection = adv;
      PbcSolver solver(pr.mesh, pr.geometry, 1.0, gas_with_mu(0.02), tube_bcs(pr.mesh, 1.2), params);
      const CellMacroState W = random_state(solver.geometry(), static_cast<std::size_t>(pr.mesh.n_cells), 9);
      const auto R = solver.evaluate_residual(W);
      const std::vector<double> face = solver.internal_face_mass_flux();
      REQUIRE(face.size() == static_cast<std::size_t>(pr.mesh.n_internal_faces()));
      const PbcBoundaryFaceData& bd = solver.last_boundary();
      SectionSpec spec;
      spec.positions = {0.7, 2.0, 3.1};
      const std::vector<SectionCut> cuts = build_section_cuts(pr.mesh, pr.geometry, 1.0, spec);
      const Index nif = pr.mesh.n_internal_faces();
      for (const SectionCut& cut : cuts) {
        REQUIRE(!cut.faces.empty());
        double lhs = 0.0;
        double scale = 0.0;
        for (Index c = 0; c < pr.mesh.n_cells; ++c) {
          if (!(pr.geometry.cell_center[static_cast<std::size_t>(c)].x < cut.position)) continue;
          lhs += R[static_cast<std::size_t>(c)][0];
          scale += std::abs(R[static_cast<std::size_t>(c)][0]);
        }
        for (Index b = 0; b < pr.mesh.n_boundary_faces(); ++b) {
          const Index o = pr.mesh.owner[static_cast<std::size_t>(b + nif)];
          if (!(pr.geometry.cell_center[static_cast<std::size_t>(o)].x < cut.position)) continue;
          lhs -= bd.flux[static_cast<std::size_t>(b)][0];
          scale += std::abs(bd.flux[static_cast<std::size_t>(b)][0]);
        }
        const double rhs = section_flow(cut, face);
        CHECK(std::abs(lhs - rhs) <= 1e-12 * (scale + std::abs(rhs)));
        CHECK(std::abs(rhs) > 1e-3);
      }
    }
  }
}

TEST_CASE("a uniform gas at rest in a closed box with walls at the same temperature stays at rest",
          "[macro][equilibrium]") {
  Problem pr = tube_box({5, 3, 3}, 0.2, false);
  std::vector<MacroBoundaryCondition> bcs(pr.mesh.patches.size());
  for (MacroBoundaryCondition& b : bcs) {
    b.kind = MacroBoundaryKind::DiffuseWall;
    b.temperature = 1.0;
  }
  PbcSolver solver(pr.mesh, pr.geometry, 1.0, gas_with_mu(0.05), bcs, MacroParams{});
  CHECK(solver.closed_domain());
  CellMacroState W = uniform_state(static_cast<std::size_t>(pr.mesh.n_cells));
  const auto R = solver.evaluate_residual(W);
  double rmax = 0.0;
  for (const auto& r : R) {
    for (double v : r) rmax = std::max(rmax, std::abs(v));
  }
  CHECK(rmax < 1e-13);
  const PbcStageReport rep = solver.solve_standalone(W, 5, 500.0);
  for (std::size_t c = 0; c < W.size(); ++c) {
    CHECK(std::abs(W.p[c] - 1.0) < 1e-12);
    CHECK(std::abs(W.T[c] - 1.0) < 1e-12);
    CHECK(std::abs(W.ux[c]) + std::abs(W.uy[c]) + std::abs(W.uz[c]) < 1e-12);
  }
  CHECK(rep.converged);
}

TEST_CASE("closed domain: the total mass is kept and the solution converges", "[macro][closed]") {
  // 1 次元の熱伝導(両側の壁の温度が違う)。圧力境界がないので全質量の拘束が要る。
  // 単独モードの壁(半空間 Maxwell 分布の流束)は、温度跳躍があると壁の法線方向の運動量流束が
  // p_b (1 + sqrt(T_w/T_b))/2 になり p_b と少し違う。この差はセル中心の格子では壁の近くの小さな
  // 奇偶の振動(圧力と速度)として残る(相対の大きさは跳躍 ΔT/(4T) 程度)。
  const int nx = 20;
  test::BoxMeshOptions o;
  o.n = {nx, 1, 1};
  o.lo = {0.0, 0.0, 0.0};
  o.hi = {1.0, 1.0 / nx, 1.0 / nx};
  o.patch_names = {"wall_left", "wall_right", "side_y", "side_y", "side_z", "side_z"};
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);
  std::vector<MacroBoundaryCondition> bcs(mesh.patches.size());
  for (std::size_t p = 0; p < bcs.size(); ++p) {
    if (mesh.patches[p].name == "wall_left") {
      bcs[p].kind = MacroBoundaryKind::DiffuseWall;
      bcs[p].temperature = 0.9;
    } else if (mesh.patches[p].name == "wall_right") {
      bcs[p].kind = MacroBoundaryKind::DiffuseWall;
      bcs[p].temperature = 1.1;
    } else {
      bcs[p].kind = MacroBoundaryKind::Symmetry;
    }
  }
  PbcSolver solver(mesh, g, 1.0, gas_with_mu(0.02), bcs, MacroParams{});
  REQUIRE(solver.closed_domain());
  CellMacroState W = uniform_state(static_cast<std::size_t>(mesh.n_cells));
  auto mass = [&](const CellMacroState& s) {
    double m = 0.0;
    for (std::size_t c = 0; c < s.size(); ++c) m += s.p[c] / s.T[c] * solver.geometry().cell_volume[c];
    return m;
  };
  const double m0 = mass(W);
  PbcStageOptions opt;
  opt.max_iterations = 200;
  opt.cfl = 500.0;
  opt.tolerance = 1e-10;
  const PbcStageReport rep = solver.solve_standalone(W, opt);
  CHECK(rep.converged);
  CHECK(std::abs(mass(W) - m0) <= 1e-13 * m0);
  // ほぼ静止、圧力はほぼ一様(上の注の奇偶の振動の大きさ以下)
  double umax = 0.0;
  double pmin = 1e300;
  double pmax = 0.0;
  for (std::size_t c = 0; c < W.size(); ++c) {
    umax = std::max(umax, std::abs(W.ux[c]) + std::abs(W.uy[c]) + std::abs(W.uz[c]));
    pmin = std::min(pmin, W.p[c]);
    pmax = std::max(pmax, W.p[c]);
  }
  CHECK(umax < 2e-3);
  CHECK((pmax - pmin) / pmax < 1e-2);
}

TEST_CASE("results are bitwise reproducible, also with a different number of threads", "[macro][determinism]") {
  Problem pr = tube_box({8, 4, 4}, 0.15, false);
  MacroParams params;
  params.advection = "upwind2";
  auto run = [&](int threads) {
    const int saved = openmp_max_threads();
    set_openmp_threads(threads);
    PbcSolver solver(pr.mesh, pr.geometry, 1.0, gas_with_mu(0.05), tube_bcs(pr.mesh, 1.3), params);
    CellMacroState W = uniform_state(static_cast<std::size_t>(pr.mesh.n_cells));
    PbcStageOptions opt;
    opt.max_iterations = 25;
    opt.cfl = 500.0;
    opt.cfl_start = 5.0;
    opt.tolerance = 1e-14;
    std::vector<long long> lin;
    opt.on_iteration = [&](const PbcIterationInfo& it) {
      lin.push_back(it.linear_iterations_pu);
      lin.push_back(it.linear_iterations_T);
    };
    const PbcStageReport rep = solver.solve_standalone(W, opt);
    set_openmp_threads(saved);
    return std::make_tuple(W, rep.iterations, lin);
  };
  const auto [a, ia, la] = run(4);
  const auto [b, ib, lb] = run(4);
  const auto [c, ic, lc] = run(1);
  CHECK(ia == ib);
  CHECK(la == lb);
  CHECK(bitwise_equal(a.p, b.p));
  CHECK(bitwise_equal(a.ux, b.ux));
  CHECK(bitwise_equal(a.T, b.T));
  CHECK(ia == ic);
  CHECK(la == lc);
  CHECK(bitwise_equal(a.p, c.p));
  CHECK(bitwise_equal(a.uy, c.uy));
  CHECK(bitwise_equal(a.T, c.T));
}

namespace {

// 平板(x 方向 nx セル、y・z は 1 セル)の問題を単独モードで解く
struct SlabResult {
  double q = 0.0;        // 左の(冷たい)壁の外向きのエネルギー流束(単位面積)= 気体から壁へ流れる熱流束
  double tau = 0.0;      // 左の壁の外向きの y 方向の運動量流束(単位面積)= 壁が受けるせん断応力
  double T_jump_L = 0.0; // 左の壁の再構成した気体の温度 − 壁温
  double slip_L = 0.0;   // 左の壁の再構成した気体の速度 − 壁速度
  bool converged = false;
};

SlabResult solve_slab(int nx, double mu, double TL, double TR, double U, const char* face_state) {
  test::BoxMeshOptions o;
  o.n = {nx, 1, 1};
  o.lo = {0.0, 0.0, 0.0};
  o.hi = {1.0, 1.0 / nx, 1.0 / nx};
  o.patch_names = {"wall_left", "wall_right", "side_y", "side_y", "side_z", "side_z"};
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);
  std::vector<MacroBoundaryCondition> bcs(mesh.patches.size());
  int left = -1;
  for (std::size_t p = 0; p < bcs.size(); ++p) {
    const std::string& name = mesh.patches[p].name;
    if (name == "wall_left") {
      bcs[p].kind = MacroBoundaryKind::DiffuseWall;
      bcs[p].temperature = TL;
      bcs[p].velocity = Vec3{0.0, -U, 0.0};
      left = static_cast<int>(p);
    } else if (name == "wall_right") {
      bcs[p].kind = MacroBoundaryKind::DiffuseWall;
      bcs[p].temperature = TR;
      bcs[p].velocity = Vec3{0.0, U, 0.0};
    } else if (name == "side_y") {
      bcs[p].kind = U != 0.0 ? MacroBoundaryKind::PeriodicSlab : MacroBoundaryKind::Symmetry;
    } else {
      bcs[p].kind = MacroBoundaryKind::Symmetry;
    }
  }
  MacroParams params;
  params.standalone_face_state = face_state;
  params.standalone_boundary_reconstruction = "limited";  // 壁で線形に外挿する(解析解の跳躍・すべりの定義に合わせる)
  params.linear_rtol = 1e-10;
  PbcSolver solver(mesh, g, 1.0, gas_with_mu(mu), bcs, params);
  CellMacroState W = uniform_state(static_cast<std::size_t>(mesh.n_cells), 1.0, 0.5 * (TL + TR));
  PbcStageOptions opt;
  opt.max_iterations = 400;
  opt.cfl = 500.0;
  opt.tolerance = 1e-12;
  const PbcStageReport rep = solver.solve_standalone(W, opt);
  SlabResult r;
  r.converged = rep.converged;
  const Patch& pl = mesh.patches[static_cast<std::size_t>(left)];
  const auto b = static_cast<std::size_t>(pl.start - mesh.n_internal_faces());
  const PbcBoundaryFaceData& bd = solver.last_boundary();
  const double A = solver.geometry().face_area[static_cast<std::size_t>(pl.start)];
  r.q = bd.flux[b][4] / A;
  r.tau = bd.flux[b][2] / A;
  r.T_jump_L = bd.reconstructed[b][4] - TL;
  r.slip_L = bd.reconstructed[b][2] - (-U);
  return r;
}

}  // namespace

TEST_CASE("slab heat conduction and Couette flow match the NSF solution with the half-Maxwellian wall flux",
          "[macro][slab]") {
  // 単独モードの壁(流出側は再構成した状態の Maxwell 分布、流入側は壁の Maxwell 分布)は、線形の範囲で
  //   温度跳躍 T_b − T_w = (15/8)√(2π) (μ/p) dT/dn = 3.75 λ dT/dn、すべり u_b − u_w = √(2π) μ/(ρ a) du/dn = 2 λ du/dn
  // (λ = (μ/p)√(π T/2)、T = 1、p = 1)を与える。平板 [0, 1] の解析解:
  //   熱伝導: q = κ (T_R − T_L)/(1 + 7.5 λ)、κ = 15/4 μ
  //   クエット: τ = μ (2U)/(1 + 4 λ)
  const double lambda = 0.01;
  const double mu = lambda / std::sqrt(kPi / 2.0);
  const double kappa = 3.75 * mu;
  const double dT = 0.01;  // 線形の範囲
  const double q_exact = kappa * (2.0 * dT) / (1.0 + 7.5 * lambda);
  const double U = 0.01;
  const double tau_exact = mu * (2.0 * U) / (1.0 + 4.0 * lambda);
  for (const char* fs : {"moments", "cell"}) {
    std::vector<double> eq;
    std::vector<double> et;
    for (int nx : {25, 50, 100}) {
      const SlabResult h = solve_slab(nx, mu, 1.0 - dT, 1.0 + dT, 0.0, fs);
      const SlabResult c = solve_slab(nx, mu, 1.0, 1.0, U, fs);
      CHECK(h.converged);
      CHECK(c.converged);
      eq.push_back(std::abs(h.q / q_exact - 1.0));
      et.push_back(std::abs(c.tau / tau_exact - 1.0));
      std::printf("平板 %-8s nx=%3d: q/q_ex = %.5f(跳躍 %.3e、解析 %.3e)、τ/τ_ex = %.5f(すべり %.3e、解析 %.3e)\n", fs,
                  nx, h.q / q_exact, h.T_jump_L, 3.75 * lambda * (2.0 * dT) / (1.0 + 7.5 * lambda), c.tau / tau_exact,
                  c.slip_L, 2.0 * lambda * (2.0 * U) / (1.0 + 4.0 * lambda));
    }
    // "cell"(境界面の値 = セルの値): 格子を細かくすると解析解に近づく(最も細かい格子で 0.5% 以内)。
    // "moments": 境界面の値が半空間の混合(壁温との中間)になるぶん、跳躍・すべりが少し変わる(1% 以内)
    if (std::string(fs) == "cell") {
      CHECK(eq.back() < 0.005);
      CHECK(et.back() < 0.005);
      CHECK(eq.back() < eq.front());
      CHECK(et.back() < et.front());
    } else {
      CHECK(eq.back() < 0.01);
      CHECK(et.back() < 0.01);
    }
  }
}

TEST_CASE("a GSIS stage fed with kinetic data consistent with a converged NSF solution stays at the fixed point",
          "[macro][gsis]") {
  // 単独モードで収束させた W* から、運動論側が渡すデータを W* と整合するように作る:
  //   F⁺⁰ = Ŧ⁺(W_b)、F⁻⁰ = Ŧ⁻(W_b, F⁺⁰)(W_b は単独モードの境界面の状態)、face_state = セルの値、
  //   σ = σ^NSF(W*)、q = q^NSF(W*)(→ HoT = 0)
  // GSIS の段(既定の制限付き再構成。整合補正がその差を吸収する)は m = 0 の残差が単独モードの残差と同じになり、
  // W* からほとんど動かないはず。
  Problem pr = tube_box({8, 4, 4}, 0.15, false);
  // セル中心の HoT(論文どおり)。σ = σ^NSF(W*) なら HoT = 0 で、段の粘性フラックスは単独モードと同じになる
  // (面の HoT("face"、フェーズ 4 の既定)では σ のセルの値の補間になり、面の NSF との差の分だけ動く)
  MacroParams cell_hot;
  cell_hot.hot_interpolation = "cell";
  PbcSolver solver(pr.mesh, pr.geometry, 1.0, gas_with_mu(0.05), tube_bcs(pr.mesh, 1.3), cell_hot);
  CellMacroState W = uniform_state(static_cast<std::size_t>(pr.mesh.n_cells));
  PbcStageOptions opt;
  opt.max_iterations = 300;
  opt.cfl = 500.0;
  opt.cfl_start = 5.0;
  opt.tolerance = 1e-12;
  REQUIRE(solver.solve_standalone(W, opt).converged);

  const PbcBoundaryFaceData bd = solver.last_boundary();
  const MeshGeometry& g = solver.geometry();
  const Index nif = pr.mesh.n_internal_faces();
  const Index nbf = pr.mesh.n_boundary_faces();
  BoundaryKineticFluxes bkf;
  bkf.F_plus.resize(static_cast<std::size_t>(nbf));
  bkf.F_minus.resize(static_cast<std::size_t>(nbf));
  bkf.face_state.resize(static_cast<std::size_t>(nbf));
  for (Index b = 0; b < nbf; ++b) {
    const auto bi = static_cast<std::size_t>(b);
    const auto f = static_cast<std::size_t>(b + nif);
    const MacroBoundaryCondition& bc =
        solver.boundary_conditions()[static_cast<std::size_t>(pr.mesh.patch_of_face(b + nif))];
    const Vec5& Wb = bd.reconstructed[bi];
    const Vec5 Fp = outgoing_flux(Wb, g.face_normal[f], g.face_area[f]);
    const Vec5 Fm = incoming_flux(bc, BoundaryFaceExtra{}, Wb, g.face_normal[f], g.face_area[f], Fp);
    for (int k = 0; k < 5; ++k) {
      bkf.F_plus[bi][static_cast<std::size_t>(k)] = Fp[k];
      bkf.F_minus[bi][static_cast<std::size_t>(k)] = Fm[k];
    }
    const auto c = static_cast<std::size_t>(pr.mesh.owner[f]);
    bkf.face_state[bi] = {W.p[c] / W.T[c], W.ux[c], W.uy[c], W.uz[c], W.T[c]};
  }
  KineticMoments km;
  km.state = W;
  km.sigma.assign(W.size(), std::array<double, 6>{});
  km.q.assign(W.size(), std::array<double, 3>{});
  const HigherOrderTerms minus_nsf = solver.compute_hot(km, bkf);  // σ = q = 0 なら HoT = −NSF
  for (std::size_t c = 0; c < W.size(); ++c) {
    for (std::size_t k = 0; k < 6; ++k) km.sigma[c][k] = -minus_nsf.sigma[c][k];
    for (std::size_t k = 0; k < 3; ++k) km.q[c][k] = -minus_nsf.q[c][k];
  }
  const HigherOrderTerms hot = solver.compute_hot(km, bkf);
  double hmax = 0.0;
  for (const auto& s : hot.sigma) {
    for (double v : s) hmax = std::max(hmax, std::abs(v));
  }
  CHECK(hmax < 1e-15);

  CellMacroState out;
  const PbcStageReport rep = solver.solve_gsis_stage(km, bkf, 10, 500.0, out);
  double dmax = 0.0;
  for (std::size_t c = 0; c < W.size(); ++c) {
    dmax = std::max(dmax, std::abs(out.p[c] - W.p[c]) / W.p[c]);
    dmax = std::max(dmax, std::abs(out.T[c] - W.T[c]) / W.T[c]);
    dmax = std::max(dmax, std::abs(out.ux[c] - W.ux[c]) + std::abs(out.uy[c] - W.uy[c]) + std::abs(out.uz[c] - W.uz[c]));
  }
  std::printf("GSIS の段: 反復 %d、最初の残差(質量)%.3e、W* からの最大の変化 %.3e\n", rep.iterations,
              rep.initial_residual_mass, dmax);
  CHECK(rep.initial_residual_mass < 1e-9);
  CHECK(dmax < 1e-8);
}
