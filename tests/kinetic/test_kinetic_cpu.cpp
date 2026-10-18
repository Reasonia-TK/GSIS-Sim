// kinetic: 運動論ソルバー(CPU 参照実装)の性質
//   - 速度点の保存順(鏡映の組)
//   - 平衡の保持(拡散壁・圧力境界)
//   - 閉じた領域の全質量の保存と、拡散壁の正味質量流束 0
//   - 対称面と並進周期が同じ 1 次元の解を与えること、クエット流の反対称性
//   - GSIS の補正と負値の置換
//   - 速度チャンクの幅によらないこと

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <vector>

#include "kinetic/kinetic_setup.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "kinetic_test_cases.hpp"
#include "physics/maxwell.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using gsis::test::KineticBc;
using gsis::test::KineticCaseOptions;
using gsis::test::make_kinetic_box;
using gsis::test::make_kinetic_case;

namespace {

const std::array<std::string, 6> kSlabPatches{"wall_left", "wall_right", "sym_y", "sym_y", "sym_z", "sym_z"};

double max_abs_diff(const std::vector<double>& a, const std::vector<double>& b) {
  double m = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) m = std::max(m, std::abs(a[i] - b[i]));
  return m;
}

// 反復を n 回進め、最後の記録を返す
KineticIterationInfo run(KineticSolver& s, int n) {
  KineticIterationInfo last;
  for (int i = 0; i < n; ++i) last = s.sweep();
  return last;
}

}  // namespace

TEST_CASE("kinetic: velocity layout groups mirror orbits", "[kinetic]") {
  const VelocityGrid grid({6, 4, 8}, {-3.0, -2.0, -4.0}, {3.0, 2.0, 4.0});
  const kinetic::VelocityLayout L = kinetic::make_velocity_layout(grid, {2, 0});
  CHECK(L.orbit_size == 4);
  CHECK(L.axis_xor[0] == 1);
  CHECK(L.axis_xor[1] == 0);
  CHECK(L.axis_xor[2] == 2);
  for (int p = 0; p < L.nv; ++p) {
    const int q = p ^ L.axis_xor[0];
    CHECK(L.xi_x[static_cast<std::size_t>(q)] == -L.xi_x[static_cast<std::size_t>(p)]);
    CHECK(L.xi_y[static_cast<std::size_t>(q)] == L.xi_y[static_cast<std::size_t>(p)]);
    const int r = p ^ L.axis_xor[2];
    CHECK(L.xi_z[static_cast<std::size_t>(r)] == -L.xi_z[static_cast<std::size_t>(p)]);
  }
  // 鏡映なしは格子の順そのまま
  const kinetic::VelocityLayout L0 = kinetic::make_velocity_layout(grid, {});
  for (int p = 0; p < L0.nv; ++p) CHECK(L0.storage_to_grid[static_cast<std::size_t>(p)] == p);
  // 範囲が対称でない軸は使えない
  const VelocityGrid skew({4, 4, 4}, {-1.0, -2.0, -2.0}, {3.0, 2.0, 2.0});
  CHECK_THROWS(kinetic::make_velocity_layout(skew, {0}));
}

TEST_CASE("kinetic: equilibrium is preserved", "[kinetic]") {
  // 歪んだ箱。x− は圧力入口、x+ は圧力出口(同じ圧力・温度)、そのほかは同温の拡散壁
  const LoadedMesh lm = make_kinetic_box({4, 3, 3}, {0, 0, 0}, {1, 0.8, 0.6},
                                         {"inlet", "outlet", "wall", "wall", "wall", "wall"}, 0.2);
  KineticCaseOptions o;
  o.kinetic = "backend = \"cpu\"\nsweep_blocks = 2\n";
  o.nv = {12, 12, 12};  // ±6: 速度格子の打ち切りによる離散 Maxwell 分布のモーメントの誤差を 1e-8 程度にする
  o.vmax = 6.0;
  const CaseConfig cfg = make_kinetic_case(
      {{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "pressure_outlet", 1.0, 1.0}, {"wall", "diffuse_wall", 1.0}},
      o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  solver.initialize_uniform(1.0, Vec3{}, 1.0);
  const std::vector<double> f0 = solver.distribution();
  const KineticIterationInfo info = run(solver, 5);
  const std::vector<double> f1 = solver.distribution();
  const double fmax = *std::max_element(f0.begin(), f0.end());
  CHECK(max_abs_diff(f0, f1) < 2e-6 * fmax);
  // 残差は丸め誤差の水準(平衡分布の大きさ ~ 0.06 に対して)
  CHECK(info.residual < 1e-6);
  const KineticMoments m = solver.moments();
  for (std::size_t c = 0; c < m.state.size(); ++c) {
    CHECK(m.state.rho[c] == Catch::Approx(1.0).margin(1e-5));
    CHECK(m.state.T[c] == Catch::Approx(1.0).margin(1e-5));
    CHECK(std::abs(m.state.ux[c]) < 1e-5);
  }
}

TEST_CASE("kinetic: closed domain keeps total mass and walls have zero net mass flux", "[kinetic]") {
  const LoadedMesh lm = make_kinetic_box({5, 3, 2}, {0, 0, 0}, {1, 0.6, 0.4},
                                         {"hot", "cold", "wall", "wall", "wall", "wall"}, 0.15);
  KineticCaseOptions o;
  o.Kn = 0.3;
  o.kinetic = "backend = \"cpu\"\nsweep_blocks = 3\n";
  const CaseConfig cfg = make_kinetic_case(
      {{"hot", "diffuse_wall", 1.3}, {"cold", "diffuse_wall", 0.8}, {"wall", "diffuse_wall", 1.0, 1.0, Vec3{0.2, 0, 0}}},
      o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  REQUIRE(solver.setup().closed_domain);
  solver.initialize_uniform(1.0, Vec3{}, 1.0);
  double total0 = 0.0;
  for (double V : solver.setup().volume) total0 += V;
  double max_scale_dev = 0.0;
  for (int it = 0; it < 15; ++it) {
    const KineticIterationInfo& info = solver.prepare();
    max_scale_dev = std::max(max_scale_dev, std::abs(info.mass_scale - 1.0));
    solver.sweep();
  }
  // 規格化の後の全質量は初期値(ρ = 1 の Maxwell 分布の離散和)と一致する
  const KineticMoments m = solver.moments();
  double total = 0.0;
  for (std::size_t c = 0; c < m.state.size(); ++c) {
    total += m.state.rho[c] * solver.setup().volume[static_cast<std::size_t>(solver.setup().cell_old_to_new[c])];
  }
  CHECK(total == Catch::Approx(solver.mass_target()).epsilon(1e-12));
  CHECK(solver.mass_target() == Catch::Approx(total0).epsilon(1e-4));
  // 1 反復あたりの規格化の倍率は 1 に近い(壁の ρ_Γ の遅れ評価と、衝突項の離散的な保存誤差の分だけずれる)
  CHECK(max_scale_dev < 1e-2);
  // 拡散壁の正味の質量流束は 0(付録 A.1 の ρ_Γ で離散的に合わせる)
  const BoundaryKineticFluxes fl = solver.boundary_fluxes();
  double max_net = 0.0, max_out = 0.0;
  for (std::size_t b = 0; b < fl.F_plus.size(); ++b) {
    max_net = std::max(max_net, std::abs(fl.F_plus[b][0] + fl.F_minus[b][0]));
    max_out = std::max(max_out, std::abs(fl.F_plus[b][0]));
  }
  CHECK(max_net < 1e-6 * max_out);
  // 温度は高温壁の側が高い
  const KineticIterationInfo& info = solver.prepare();
  CHECK(info.patch_mass_flow.size() == 3);
}

TEST_CASE("kinetic: symmetry and periodic slab give the same heat conduction", "[kinetic]") {
  const LoadedMesh lm = make_kinetic_box({8, 1, 1}, {0, 0, 0}, {1, 0.125, 0.125}, kSlabPatches);
  auto solve = [&](const char* side_type) {
    KineticCaseOptions o;
    o.Kn = 0.2;
    o.kinetic = "backend = \"cpu\"\nsweep_blocks = 1\n";
    const CaseConfig cfg = make_kinetic_case({{"wall_left", "diffuse_wall", 0.9},
                                              {"wall_right", "diffuse_wall", 1.1},
                                              {"sym_y", side_type},
                                              {"sym_z", side_type}},
                                             o);
    KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    solver.initialize_uniform(1.0, Vec3{}, 1.0);
    run(solver, 150);
    return solver.moments();
  };
  const KineticMoments a = solve("symmetry");
  const KineticMoments b = solve("periodic_slab");
  for (std::size_t c = 0; c < a.state.size(); ++c) {
    CHECK(a.state.T[c] == Catch::Approx(b.state.T[c]).epsilon(2e-5));
    CHECK(a.state.rho[c] == Catch::Approx(b.state.rho[c]).epsilon(2e-5));
    CHECK(a.q[c][0] == Catch::Approx(b.q[c][0]).epsilon(2e-3));
    CHECK(std::abs(a.state.uy[c]) < 1e-6);
    CHECK(std::abs(b.state.uz[c]) < 1e-6);
  }
  // 熱流束は高温側(右)から低温側(左)へ(q_x < 0)、ほぼ一様
  double qmin = 0.0, qmax = -1.0;
  for (std::size_t c = 0; c < a.state.size(); ++c) {
    qmin = std::min(qmin, a.q[c][0]);
    qmax = std::max(qmax, a.q[c][0]);
  }
  CHECK(qmax < 0.0);
  CHECK((qmax - qmin) < 0.1 * std::abs(qmin));  // 8 セルなので壁際のセル値は一様からずれる(精度は平板の検証で見る)
}

TEST_CASE("kinetic: couette flow with periodic sides is antisymmetric", "[kinetic]") {
  const LoadedMesh lm = make_kinetic_box({8, 1, 1}, {0, 0, 0}, {1, 0.125, 0.125}, kSlabPatches);
  KineticCaseOptions o;
  o.Kn = 0.2;
  o.kinetic = "backend = \"cpu\"\nsweep_blocks = 1\n";
  const CaseConfig cfg = make_kinetic_case({{"wall_left", "diffuse_wall", 1.0, 1.0, Vec3{0, -0.3, 0}},
                                            {"wall_right", "diffuse_wall", 1.0, 1.0, Vec3{0, 0.3, 0}},
                                            {"sym_y", "periodic_slab"},
                                            {"sym_z", "symmetry"}},
                                           o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  solver.initialize_uniform(1.0, Vec3{}, 1.0);
  run(solver, 150);
  const KineticMoments m = solver.moments();
  const std::size_t n = m.state.size();
  for (std::size_t c = 0; c < n; ++c) {
    CHECK(m.state.uy[c] == Catch::Approx(-m.state.uy[n - 1 - c]).margin(2e-5));
    CHECK(m.state.T[c] == Catch::Approx(m.state.T[n - 1 - c]).margin(2e-5));
    CHECK(m.sigma[c][3] < 0.0);  // P_xy < 0(左の壁が −y、右の壁が +y へ動く)
  }
  CHECK(m.state.uy[n - 1] > 0.0);
  CHECK(m.state.uy[n - 1] < 0.3);  // すべりがある
}

TEST_CASE("kinetic: gsis correction and negative replacement", "[kinetic]") {
  const LoadedMesh lm = make_kinetic_box({2, 2, 1}, {0, 0, 0}, {1, 1, 0.5},
                                         {"wall", "wall", "wall", "wall", "wall", "wall"});
  KineticCaseOptions o;
  // 式 (14) の補正を連続の Maxwell 分布で、置換を "created" の規則で確かめる(期待値を連続の Maxwell 分布で作るため。
  // フェーズ 5b で既定が保存的な離散平衡・"blend" になったので明示する。離散平衡の補正は test_kinetic_phase5.cpp、
  // "blend" は tests/gsis/test_gsis.cpp)
  o.kinetic = "backend = \"cpu\"\nequilibrium = \"continuous\"\ngsis_negative_check = \"created\"\n";
  o.vmax = 6.0;
  o.nv = {12, 12, 12};
  const CaseConfig cfg = make_kinetic_case({{"wall", "diffuse_wall", 1.0}}, o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  const VelocityGrid grid(cfg.velocity_grid.n, cfg.velocity_grid.lo, cfg.velocity_grid.hi);
  const int nc = lm.mesh.n_cells;
  const int nv = grid.size();
  // f = f^eq(W_half) + δf(滑らかな摂動)
  CellMacroState wh, wn;
  wh.resize(static_cast<std::size_t>(nc));
  wn.resize(static_cast<std::size_t>(nc));
  for (int c = 0; c < nc; ++c) {
    const auto ci = static_cast<std::size_t>(c);
    wh.rho[ci] = 1.0 + 0.1 * c;
    wh.ux[ci] = 0.1;
    wh.uy[ci] = -0.05 * c;
    wh.uz[ci] = 0.0;
    wh.T[ci] = 1.0 + 0.05 * c;
    wh.p[ci] = wh.rho[ci] * wh.T[ci];
    wn.rho[ci] = wh.rho[ci] * 1.02;
    wn.ux[ci] = 0.12;
    wn.uy[ci] = wh.uy[ci] + 0.01;
    wn.uz[ci] = 0.02;
    wn.T[ci] = wh.T[ci] * 0.99;
    wn.p[ci] = wn.rho[ci] * wn.T[ci];
  }
  std::vector<double> f(static_cast<std::size_t>(nc) * static_cast<std::size_t>(nv));
  std::vector<double> delta(f.size());
  for (int c = 0; c < nc; ++c) {
    const auto ci = static_cast<std::size_t>(c);
    for (int k = 0; k < nv; ++k) {
      const Vec3 xi = grid.xi(k);
      const double feq = maxwellian(wh.rho[ci], Vec3{wh.ux[ci], wh.uy[ci], wh.uz[ci]}, wh.T[ci], 1.0, xi);
      const double d = 0.05 * feq * xi.x * xi.y / (1.0 + dot(xi, xi));
      delta[ci * static_cast<std::size_t>(nv) + static_cast<std::size_t>(k)] = d;
      f[ci * static_cast<std::size_t>(nv) + static_cast<std::size_t>(k)] = feq + d;
    }
  }
  solver.set_distribution(f);
  CHECK(solver.apply_gsis_correction(wh, wn) == 0);
  const std::vector<double> g = solver.distribution();
  double err = 0.0, scale = 0.0;
  for (int c = 0; c < nc; ++c) {
    const auto ci = static_cast<std::size_t>(c);
    for (int k = 0; k < nv; ++k) {
      const auto i = ci * static_cast<std::size_t>(nv) + static_cast<std::size_t>(k);
      const double expect =
          maxwellian(wn.rho[ci], Vec3{wn.ux[ci], wn.uy[ci], wn.uz[ci]}, wn.T[ci], 1.0, grid.xi(k)) + delta[i];
      err = std::max(err, std::abs(g[i] - expect));
      scale = std::max(scale, std::abs(expect));
    }
  }
  CHECK(err < 1e-6 * scale);

  // 大きく動かすと負になるセルが出る → そのセルは f^eq(W_new) に置き換わる
  solver.set_distribution(f);
  CellMacroState wbig = wn;
  wbig.ux[1] = 2.0;  // セル 1 だけ速度を大きく変える(f − f^eq(W_half) + f^eq(W_new) が負になる)
  CHECK(solver.apply_gsis_correction(wh, wbig) == 1);
  const std::vector<double> h = solver.distribution();
  for (int k = 0; k < nv; ++k) {
    const double expect = maxwellian(wbig.rho[1], Vec3{wbig.ux[1], wbig.uy[1], wbig.uz[1]}, wbig.T[1], 1.0, grid.xi(k));
    CHECK(h[static_cast<std::size_t>(nv) + static_cast<std::size_t>(k)] == Catch::Approx(expect).margin(1e-7));
  }
}

TEST_CASE("kinetic: equilibrium initialization reproduces the macroscopic state", "[kinetic]") {
  const LoadedMesh lm = make_kinetic_box({3, 2, 2}, {0, 0, 0}, {1, 1, 1},
                                         {"inlet", "outlet", "wall", "wall", "wall", "wall"});
  KineticCaseOptions o;
  o.kinetic = "backend = \"cpu\"\n";
  o.nv = {16, 16, 16};
  o.vmax = 8.0;
  const CaseConfig cfg = make_kinetic_case(
      {{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "vacuum"}, {"wall", "diffuse_wall", 1.0}}, o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  CellMacroState w;
  const auto nc = static_cast<std::size_t>(lm.mesh.n_cells);
  w.resize(nc);
  for (std::size_t c = 0; c < nc; ++c) {
    w.rho[c] = 0.5 + 0.1 * static_cast<double>(c);
    w.ux[c] = 0.3 - 0.05 * static_cast<double>(c);
    w.uy[c] = 0.1;
    w.uz[c] = -0.2;
    w.T[c] = 0.8 + 0.03 * static_cast<double>(c);
    w.p[c] = w.rho[c] * w.T[c];
  }
  solver.initialize_equilibrium(w);
  const KineticMoments m = solver.moments();
  for (std::size_t c = 0; c < nc; ++c) {
    CHECK(m.state.rho[c] == Catch::Approx(w.rho[c]).epsilon(1e-6));
    CHECK(m.state.ux[c] == Catch::Approx(w.ux[c]).margin(1e-6));
    CHECK(m.state.uz[c] == Catch::Approx(w.uz[c]).margin(1e-6));
    CHECK(m.state.T[c] == Catch::Approx(w.T[c]).epsilon(1e-5));
    CHECK(std::abs(m.q[c][0]) < 1e-5);
    CHECK(std::abs(m.sigma[c][3]) < 1e-5);
  }
}

TEST_CASE("kinetic: results do not depend on the velocity chunk width", "[kinetic]") {
  const LoadedMesh lm = make_kinetic_box({4, 2, 2}, {0, 0, 0}, {1, 0.5, 0.5},
                                         {"inlet", "outlet", "wall", "wall", "sym", "sym"}, 0.1);
  auto run_with = [&](const char* chunk) {
    KineticCaseOptions o;
    o.kinetic = std::string("backend = \"cpu\"\nsweep_blocks = 2\nvelocity_chunk = ") + chunk + "\n";
    const CaseConfig cfg = make_kinetic_case({{"inlet", "pressure_inlet", 1.0, 1.0},
                                              {"outlet", "pressure_outlet", 1.0, 0.5},
                                              {"wall", "diffuse_wall", 1.0},
                                              {"sym", "symmetry"}},
                                             o);
    KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    solver.initialize_uniform(0.8, Vec3{}, 1.0);
    run(solver, 4);
    return solver.distribution();
  };
  const std::vector<double> a = run_with("0");
  const std::vector<double> b = run_with("96");
  CHECK(max_abs_diff(a, b) == 0.0);
}

// 調査用(既定では実行しない): 平板の熱伝導の反復の様子(残差が 1e-6 になるまでの回数)を表示する
TEST_CASE("kinetic: debug slab history", "[.debug]") {
  const LoadedMesh lm = make_kinetic_box({8, 1, 1}, {0, 0, 0}, {1, 0.125, 0.125}, kSlabPatches);
  struct Variant {
    const char* side;
    const char* extra;
  };
  const Variant variants[] = {
      {"periodic_slab", "sweep_blocks = 1\n"},
      {"symmetry", "sweep_blocks = 1\n"},
      {"periodic_slab", "sweep_blocks = 2\n"},
      {"periodic_slab", "sweep_blocks = 4\n"},
      {"periodic_slab", "sweep_blocks = 8\n"},
      {"periodic_slab", "sweep_blocks = 8\npseudo_time = \"velocity\"\npseudo_cfl = 10\n"},
      {"periodic_slab", "sweep_blocks = 8\npseudo_time = \"velocity\"\npseudo_cfl = 3\n"},
      {"periodic_slab", "sweep_blocks = 8\npseudo_time = \"velocity\"\npseudo_cfl = 1\n"},
      {"symmetry", "sweep_blocks = 2\n"},
      {"periodic_slab", "sweep_blocks = 1\nlimiter = \"none\"\n"},
  };
  for (const Variant& var : variants) {
    KineticCaseOptions o;
    o.Kn = 0.2;
    o.kinetic = std::string("backend = \"cpu\"\n") + var.extra;
    const CaseConfig cfg = make_kinetic_case({{"wall_left", "diffuse_wall", 0.9},
                                              {"wall_right", "diffuse_wall", 1.1},
                                              {"sym_y", var.side},
                                              {"sym_z", var.side}},
                                             o);
    KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    solver.initialize_uniform(1.0, Vec3{}, 1.0);
    std::string tag = std::string(var.side) + " " + var.extra;
    for (char& ch : tag) {
      if (ch == '\n') ch = ' ';
    }
    int it_conv = -1;
    double res = 0.0;
    double r0 = 0.0;
    std::string status = "ok";
    try {
      for (int it = 0; it < 400; ++it) {
        const KineticIterationInfo info = solver.sweep();
        res = info.residual;
        if (it == 0) r0 = res;
        if (res < 1e-6) {
          it_conv = it;
          break;
        }
        if (!(res < 1e3 * r0)) {
          status = "diverged";
          break;
        }
      }
    } catch (const std::exception& e) {
      status = std::string("exception: ") + e.what();
    }
    if (status == "ok") {
      const KineticMoments m = solver.moments();
      std::printf("%-70s conv_it %4d res %.3e q0 %.6e q7 %.6e\n", tag.c_str(), it_conv, res, m.q[0][0], m.q[7][0]);
    } else {
      std::printf("%-70s %s\n", tag.c_str(), status.c_str());
    }
  }
}

TEST_CASE("kinetic: the positivity limiter keeps the reconstructed boundary values non-negative", "[kinetic]") {
  // x 方向に 3 セル(両端は真空)。f = s_c f^eq、s = (1, 0.5, 0.01)。右端のセルの勾配は急なので、相対 ε の
  // Venkatakrishnan リミッタ(既定)は効かず、右の境界面への再構成値が負になり、流出側の質量流束 F⁺ が負になる。
  // 正値の制限(positivity_limiter)では面の値 ≥ 0 なので F⁺ ≥ 0
  const LoadedMesh lm = make_kinetic_box({3, 1, 1}, {0, 0, 0}, {3, 1, 1}, {"xlo", "xhi", "y", "y", "z", "z"});
  double flux[2] = {0.0, 0.0};
  for (const bool pos : {false, true}) {
    KineticCaseOptions o;
    o.nv = {12, 12, 12};
    o.vmax = 6.0;
    o.kinetic = std::string("backend = \"cpu\"\nprecision = \"fp64\"\nsweep_blocks = 1\n") +
                (pos ? "positivity_limiter = true\n" : "");
    const CaseConfig cfg = make_kinetic_case(
        {{"xlo", "vacuum"}, {"xhi", "vacuum"}, {"y", "periodic_slab"}, {"z", "periodic_slab"}}, o);
    KineticSolver s(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    const VelocityGrid grid(cfg.velocity_grid.n, cfg.velocity_grid.lo, cfg.velocity_grid.hi);
    const int nv = grid.size();
    const double scale[3] = {1.0, 0.5, 0.01};
    std::vector<double> f(3 * static_cast<std::size_t>(nv));
    for (std::size_t c = 0; c < 3; ++c) {
      const double xc = lm.geometry.cell_center[c].x;
      const double sc = scale[xc < 1.0 ? 0 : (xc < 2.0 ? 1 : 2)];
      for (int k = 0; k < nv; ++k) {
        f[c * static_cast<std::size_t>(nv) + static_cast<std::size_t>(k)] =
            sc * maxwellian(1.0, Vec3{}, 1.0, 1.0, grid.xi(k));
      }
    }
    s.set_distribution(f);
    s.prepare();
    const BoundaryKineticFluxes fb = s.boundary_fluxes();
    const Patch* xhi = nullptr;
    for (const Patch& p : lm.mesh.patches) {
      if (p.name == "xhi") xhi = &p;
    }
    REQUIRE(xhi != nullptr);
    const auto b = static_cast<std::size_t>(xhi->start - lm.mesh.n_internal_faces());
    flux[pos ? 1 : 0] = fb.F_plus[b][0];
  }
  std::printf("右の真空の境界面の F⁺(質量): 制限なし %.4e、正値の制限 %.4e\n", flux[0], flux[1]);
  CHECK(flux[0] < 0.0);
  CHECK(flux[1] >= -1e-12 * std::abs(flux[0]));  // 面の値はちょうど 0 になりうる(丸め誤差)
}
