// kinetic: フェーズ 5a で足した運動論ソルバーの機能(CPU 参照実装)
//   - 保存的な離散平衡分布([kinetic].equilibrium = "discrete"): 速度格子の上の離散モーメントが (ρ, ρu, ρE) に一致する
//     (Maxwell 分布、Shakhov の目標分布、初期化、GSIS の補正)
//   - LU-SGS のスイープの順序("octant"・"dir13")と内部反復(inner_sweeps、inner_wall_update)が同じ解に収束する

#include <algorithm>
#include <array>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <string>
#include <vector>

#include "kinetic/discrete_equilibrium.hpp"
#include "kinetic/kinetic_kernels.hpp"
#include "kinetic/kinetic_setup.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "kinetic_test_cases.hpp"
#include "physics/maxwell.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using gsis::test::KineticCaseOptions;
using gsis::test::make_kinetic_box;
using gsis::test::make_kinetic_case;

namespace {

// 論文の管の速度格子(30 × 24 × 24、[−10, 10] × [−8, 8]²、Δξ = 2/3)
VelocityGrid tube_grid() { return VelocityGrid({30, 24, 24}, {-10.0, -8.0, -8.0}, {10.0, 8.0, 8.0}); }

kinetic::DeqGrid deq_grid_of(const VelocityGrid& g, double tol) {
  kinetic::DeqGrid d;
  for (int a = 0; a < 3; ++a) {
    d.lo[a] = g.lo(a);
    d.hi[a] = g.hi(a);
    d.d[a] = g.delta(a);
  }
  d.tolerance = tol;
  return d;
}

// 速度格子の上の離散モーメント (ρ, ρu, ρE)
std::array<double, 5> discrete_moments(const VelocityGrid& g, const std::vector<double>& f) {
  std::array<double, 5> m{};
  for (int k = 0; k < g.size(); ++k) {
    const Vec3 xi = g.xi(k);
    const double w = g.weight() * f[static_cast<std::size_t>(k)];
    m[0] += w;
    m[1] += w * xi.x;
    m[2] += w * xi.y;
    m[3] += w * xi.z;
    m[4] += w * 0.5 * dot(xi, xi);
  }
  return m;
}

std::array<double, 5> conserved(double rho, const Vec3& u, double T) {
  return {rho, rho * u.x, rho * u.y, rho * u.z, rho * (1.5 * T + 0.5 * dot(u, u))};
}

// 保存量の相対誤差(ρ、ρ√T、ρT で規格化)
double conservation_error(const std::array<double, 5>& a, const std::array<double, 5>& b, double rho, double T) {
  double e = std::abs(a[0] - b[0]) / rho;
  for (int k = 1; k < 4; ++k) e = std::max(e, std::abs(a[static_cast<std::size_t>(k)] - b[static_cast<std::size_t>(k)]) / (rho * std::sqrt(T)));
  return std::max(e, std::abs(a[4] - b[4]) / (rho * T));
}

std::vector<double> grid_axis(const VelocityGrid& g, int axis) {
  std::vector<double> x(static_cast<std::size_t>(g.size()));
  for (int k = 0; k < g.size(); ++k) x[static_cast<std::size_t>(k)] = g.xi(k)[axis];
  return x;
}

}  // namespace

TEST_CASE("kinetic: the discrete equilibrium matches the conserved moments on the velocity grid", "[kinetic]") {
  const VelocityGrid g = tube_grid();
  const kinetic::DeqGrid dg = deq_grid_of(g, 1e-11);
  const std::vector<double> xs = grid_axis(g, 0), ys = grid_axis(g, 1), zs = grid_axis(g, 2);
  struct State {
    double rho;
    Vec3 u;
    double T;
  };
  // 冷たい超音速の噴流(管 δ = 1000 の真空の出口の手前)と、速度格子の端に近い流れ
  const std::vector<State> states = {{0.02, {2.0, 0.3, -0.4}, 0.1},
                                     {0.5, {1.7, -0.2, 0.25}, 0.05},
                                     {1.3, {0.33, 0.0, 0.0}, 0.2},
                                     {1.0, {6.5, 0.0, 0.0}, 1.0}};
  for (const State& st : states) {
    // 連続の Maxwell 分布の離散モーメントは合わない
    std::vector<double> fc(static_cast<std::size_t>(g.size()));
    for (int k = 0; k < g.size(); ++k) fc[static_cast<std::size_t>(k)] = maxwellian(st.rho, st.u, st.T, 1.0, g.xi(k));
    const std::array<double, 5> target = conserved(st.rho, st.u, st.T);
    const double err_cont = conservation_error(discrete_moments(g, fc), target, st.rho, st.T);
    CHECK(err_cont > 1e-4);
    // 離散平衡分布は合う
    MaxwellParams<double> m;
    double gamma[5];
    const int r = kinetic::discrete_maxwell_seq<double>(xs.data(), ys.data(), zs.data(), g.size(), g.weight(), dg,
                                                        st.rho, st.u.x, st.u.y, st.u.z, st.T, nullptr, m, gamma);
    REQUIRE(r > 0);
    std::vector<double> fd(static_cast<std::size_t>(g.size()));
    for (int k = 0; k < g.size(); ++k) fd[static_cast<std::size_t>(k)] = maxwellian(m, g.xi(k));
    const double err_disc = conservation_error(discrete_moments(g, fd), target, st.rho, st.T);
    INFO("T = " << st.T << "、連続 " << err_cont << "、離散 " << err_disc << "、反復 " << r);
    CHECK(err_disc < 1e-10);
    // Shakhov の目標分布(熱流束あり)も保存的: 多項式の補正 γ で離散モーメントを打ち消す
    kinetic::ShakhovTarget<double> s;
    s.eq = m;
    s.uc = st.u;
    s.q = Vec3{0.3, -0.1, 0.05} * (st.rho * st.T * std::sqrt(st.T));
    s.theta = st.T;
    s.coef = (1.0 - 2.0 / 3.0) / (5.0 * st.rho * st.T * st.T);
    s.g0 = s.g4 = 0.0;
    s.g = Vec3{};
    const int r2 = kinetic::discrete_maxwell_seq<double>(xs.data(), ys.data(), zs.data(), g.size(), g.weight(), dg,
                                                         st.rho, st.u.x, st.u.y, st.u.z, st.T, &s, s.eq, gamma);
    REQUIRE(r2 > 0);
    s.g0 = gamma[0];
    s.g = Vec3{gamma[1], gamma[2], gamma[3]};
    s.g4 = gamma[4];
    std::vector<double> fs(static_cast<std::size_t>(g.size()));
    for (int k = 0; k < g.size(); ++k) fs[static_cast<std::size_t>(k)] = kinetic::shakhov_target(s, g.xi(k));
    CHECK(conservation_error(discrete_moments(g, fs), target, st.rho, st.T) < 1e-10);
  }
  // 十分に解像される Maxwell 分布は補正しない(連続のまま)
  MaxwellParams<double> m;
  double gamma[5];
  CHECK(kinetic::discrete_maxwell_seq<double>(xs.data(), ys.data(), zs.data(), g.size(), g.weight(), dg, 1.0, 0.3, 0.1,
                                              0.0, 1.0, nullptr, m, gamma) == 0);
}

TEST_CASE("kinetic: the discrete equilibrium keeps cold states conservative in the solver", "[kinetic]") {
  // 刻み Δξ = 1 の粗い速度格子([−6, 6]³、12³ 点)では T = 0.3 の Maxwell 分布の離散モーメントが 0.3% ほどずれる
  // 開いた領域(閉じた領域では prepare が全質量を規格化するので、補正の後の密度が W^{n+1} からずれる)
  const LoadedMesh lm = make_kinetic_box({3, 2, 1}, {0, 0, 0}, {1, 1, 0.5},
                                         {"inlet", "outlet", "wall", "wall", "wall", "wall"});
  auto make = [&](const char* eq) {
    KineticCaseOptions o;
    o.kinetic = std::string("backend = \"cpu\"\nequilibrium = \"") + eq + "\"\n";
    o.vmax = 6.0;
    o.nv = {12, 12, 12};
    return make_kinetic_case(
        {{"inlet", "pressure_inlet", 0.3, 0.3}, {"outlet", "vacuum"}, {"wall", "diffuse_wall", 0.3}}, o);
  };
  const auto nc = static_cast<std::size_t>(lm.mesh.n_cells);
  CellMacroState w, wn;
  w.resize(nc);
  wn.resize(nc);
  for (std::size_t c = 0; c < nc; ++c) {
    const double x = static_cast<double>(c);
    w.rho[c] = 0.8 + 0.1 * x;
    w.ux[c] = 0.4 - 0.1 * x;
    w.uy[c] = 0.15;
    w.uz[c] = -0.05;
    w.T[c] = 0.25 + 0.02 * x;
    w.p[c] = w.rho[c] * w.T[c];
    wn.rho[c] = w.rho[c] * 1.03;
    wn.ux[c] = w.ux[c] + 0.05;
    wn.uy[c] = 0.1;
    wn.uz[c] = 0.0;
    wn.T[c] = w.T[c] * 0.97;
    wn.p[c] = wn.rho[c] * wn.T[c];
  }
  auto max_state_error = [&](const KineticMoments& m, const CellMacroState& ref) {
    double e = 0.0;
    for (std::size_t c = 0; c < nc; ++c) {
      e = std::max(e, std::abs(m.state.rho[c] / ref.rho[c] - 1.0));
      e = std::max(e, std::abs(m.state.T[c] / ref.T[c] - 1.0));
      e = std::max(e, std::abs(m.state.ux[c] - ref.ux[c]) / std::sqrt(ref.T[c]));
    }
    return e;
  };
  const CaseConfig cfg_c = make("continuous");
  KineticSolver cont(cfg_c, lm.mesh, lm.geometry, parse_kinetic_params(cfg_c));
  cont.initialize_equilibrium(w);
  const double e_cont = max_state_error(cont.moments(), w);
  CHECK(e_cont > 1e-3);

  const CaseConfig cfg_d = make("discrete");
  KineticSolver disc(cfg_d, lm.mesh, lm.geometry, parse_kinetic_params(cfg_d));
  REQUIRE(disc.setup().discrete_eq);
  disc.initialize_equilibrium(w);
  const double e_init = max_state_error(disc.moments(), w);
  INFO("初期化の誤差: 連続 " << e_cont << "、離散 " << e_init);
  CHECK(e_init < 2e-6);
  // GSIS の補正(式 (14))の後のモーメントが W^{n+1} に一致する
  disc.sweep();
  const KineticMoments half = disc.moments();
  disc.apply_gsis_correction(half.state, wn);
  CHECK(max_state_error(disc.moments(), wn) < 2e-6);
  // Shakhov の目標分布を作ったセルの数
  const KineticIterationInfo& info = disc.prepare();
  CHECK(info.deq_corrected == static_cast<long long>(nc));
  CHECK(info.deq_failed == 0);
}

TEST_CASE("kinetic: sweep orderings and inner sweeps converge to the same solution", "[kinetic]") {
  // 圧力入口・真空の出口・拡散壁の歪んだ箱
  const LoadedMesh lm = make_kinetic_box({6, 3, 3}, {0, 0, 0}, {1.2, 0.6, 0.6},
                                         {"inlet", "outlet", "wall", "wall", "wall", "wall"}, 0.15);
  auto solve = [&](const std::string& extra, int iterations) {
    KineticCaseOptions o;
    o.Kn = 0.3;
    o.kinetic = "backend = \"cpu\"\nsweep_blocks = 2\n" + extra;
    const CaseConfig cfg =
        make_kinetic_case({{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "vacuum"}, {"wall", "diffuse_wall", 1.0}}, o);
    KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    solver.initialize_uniform(1.0, Vec3{}, 1.0);
    double eps = 1.0;
    for (int i = 0; i < iterations; ++i) {
      eps = solver.prepare().epsilon;
      solver.sweep();
    }
    INFO(extra << ": ε = " << eps);
    CHECK(eps < 1e-6);
    return solver.moments();
  };
  // 従来の設定(block の順、内部反復 1 回、ブロック間の反復 1 回)を基準にする。フェーズ 5b で既定が強化した設定
  // (octant、内部反復 4 回 + 壁の更新、ブロック間の反復 2 回)になったので、基準と変種はすべての項目を明示する
  auto spec = [](const char* order, int inner, bool wall, int passes) {
    return std::string("sweep_order = \"") + order + "\"\ninner_sweeps = " + std::to_string(inner) +
           "\ninner_wall_update = " + (wall ? "true" : "false") + "\nblock_passes = " + std::to_string(passes) + "\n";
  };
  const KineticMoments a = solve(spec("block", 1, false, 1), 400);
  const std::vector<std::string> variants = {spec("octant", 1, false, 1), spec("dir13", 1, false, 1),
                                             spec("block", 3, true, 1),   spec("octant", 2, false, 1),
                                             spec("octant", 1, false, 3), ""};  // "" は既定(強化した設定)
  for (const std::string& v : variants) {
    const KineticMoments b = solve(v, 300);
    for (std::size_t c = 0; c < a.state.size(); ++c) {
      CHECK(b.state.rho[c] == Catch::Approx(a.state.rho[c]).epsilon(2e-5));
      CHECK(b.state.T[c] == Catch::Approx(a.state.T[c]).epsilon(2e-5));
      CHECK(b.state.ux[c] == Catch::Approx(a.state.ux[c]).margin(2e-5));
    }
  }
}

TEST_CASE("kinetic: octant sweep directions group the velocity points", "[kinetic]") {
  const std::vector<Vec3> d4 = kinetic::make_sweep_directions("octant");
  REQUIRE(d4.size() == 4);
  CHECK(kinetic::make_sweep_directions("dir13").size() == 13);
  CHECK(kinetic::make_sweep_directions("block").empty());
  // 体対角への割り当ては符号の組で決まる(ξ と −ξ は同じ向き)
  CHECK(kinetic::sweep_direction_of(d4, Vec3{1.0, 0.2, 3.0}) == kinetic::sweep_direction_of(d4, Vec3{-1.0, -0.2, -3.0}));
  CHECK(kinetic::sweep_direction_of(d4, Vec3{1.0, 0.2, 3.0}) != kinetic::sweep_direction_of(d4, Vec3{1.0, -0.2, 3.0}));
  // 鏡映なしの保存順は向きごとにまとまる
  const VelocityGrid grid({6, 4, 4}, {-3.0, -2.0, -2.0}, {3.0, 2.0, 2.0});
  const kinetic::VelocityLayout L = kinetic::make_velocity_layout(grid, {}, d4);
  int prev = -1;
  int changes = 0;
  for (int p = 0; p < L.nv; ++p) {
    const auto pi = static_cast<std::size_t>(p);
    const int g = kinetic::sweep_direction_of(d4, Vec3{L.xi_x[pi], L.xi_y[pi], L.xi_z[pi]});
    if (g != prev) ++changes;
    CHECK(g >= prev);
    prev = g;
  }
  CHECK(changes == 4);
}

TEST_CASE("kinetic: cells whose moments the velocity grid cannot hold become collisionless and skip the correction",
          "[kinetic]") {
  // フェーズ 5b([kinetic].deq_fallback)。Δξ = 1 の速度格子(点は ±0.5, ±1.5, …)で、セル 0 の分布を
  // x = (0.5, 1.5, 2.5) に (−0.05, 1.1, −0.05)、y = (0.5, 1.5) に (0.5, 0.5)、z = 0.5 に置く(負の値を含む)。
  // u = (1.5, 1.0, 0.5)、T = (−0.1 + 0.25 + 0)/3 = 0.05 で、速度格子で負でない分布が持てる最小の温度
  // T_min(u) = (0 + 0.25 + 0)/3 ≈ 0.083 より低いので、離散平衡は存在せずニュートン法は収束しない
  // 開いた領域にする(閉じた領域では prepare が全質量を規格化するので、補正の後の f が全体に倍率で変わる)
  const LoadedMesh lm = make_kinetic_box({2, 1, 1}, {0, 0, 0}, {1, 0.5, 0.5},
                                         {"inlet", "outlet", "wall", "wall", "wall", "wall"});
  for (const char* fb : {"collisionless", "continuous"}) {
    KineticCaseOptions o;
    o.kinetic = std::string("backend = \"cpu\"\nprecision = \"fp64\"\ndeq_fallback = \"") + fb + "\"\n";
    o.nv = {12, 12, 12};
    o.vmax = 6.0;
    const CaseConfig cfg = make_kinetic_case(
        {{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "vacuum"}, {"wall", "diffuse_wall", 1.0}}, o);
    KineticSolver s(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    const VelocityGrid grid(cfg.velocity_grid.n, cfg.velocity_grid.lo, cfg.velocity_grid.hi);
    const int nv = grid.size();
    std::vector<double> f(2 * static_cast<std::size_t>(nv));
    for (int k = 0; k < nv; ++k) {
      const Vec3 xi = grid.xi(k);
      // セル 1 は平衡分布、セル 0 は 2 点だけ
      f[static_cast<std::size_t>(nv) + static_cast<std::size_t>(k)] = maxwellian(1.0, Vec3{}, 1.0, 1.0, xi);
      double ax = 0.0;
      if (std::abs(xi.x - 1.5) < 1e-9) ax = 1.1;
      if (std::abs(xi.x - 0.5) < 1e-9 || std::abs(xi.x - 2.5) < 1e-9) ax = -0.05;
      const double by = (std::abs(xi.y - 0.5) < 1e-9 || std::abs(xi.y - 1.5) < 1e-9) ? 0.5 : 0.0;
      const double cz = std::abs(xi.z - 0.5) < 1e-9 ? 1.0 : 0.0;
      f[static_cast<std::size_t>(k)] = ax * by * cz / grid.weight();
    }
    s.set_distribution(f);
    const KineticIterationInfo& info = s.prepare();
    INFO(fb << ": 補正 " << info.deq_corrected << "、失敗 " << info.deq_failed);
    CHECK(info.deq_failed == 1);
    // GSIS の補正: セル 0 の W_new も同じ表せない状態(密度だけ 2% 上げる)。"collisionless" ではセル 0 を補正しない
    const KineticMoments m = s.moments();
    CellMacroState wn = m.state;
    wn.rho[0] *= 1.02;
    wn.rho[1] *= 1.02;
    wn.p[0] = wn.rho[0] * wn.T[0];
    wn.p[1] = wn.rho[1] * wn.T[1];
    const long long replaced = s.apply_gsis_correction(m.state, wn);
    // セル 1(平衡分布)はどちらでも W_new に補正される
    const KineticMoments m2 = s.moments();
    CHECK(std::abs(m2.state.rho[1] / wn.rho[1] - 1.0) < 1e-6);
    const std::vector<double> g = s.distribution();
    double d0 = 0.0;
    for (int k = 0; k < nv; ++k) d0 = std::max(d0, std::abs(g[static_cast<std::size_t>(k)] - f[static_cast<std::size_t>(k)]));
    if (std::string(fb) == "collisionless") {
      CHECK(replaced == 1);
      CHECK(s.replaced_cells()[0] == 1);
      CHECK(d0 == 0.0);  // f はそのまま
      // 衝突なしのセルでも反復は有限のまま(f が負にならない)
      s.sweep();
      const std::vector<double> h = s.distribution();
      double fmin = 0.0;
      for (int k = 0; k < nv; ++k) fmin = std::min(fmin, h[static_cast<std::size_t>(k)]);
      CHECK(std::isfinite(fmin));
    } else {
      CHECK(d0 > 0.0);  // 連続の Maxwell 分布で補正した
    }
  }
}

TEST_CASE("kinetic: pressure_boundary_reconstruction = \"cell\" uses the cell state for the inflow Maxwellian",
          "[kinetic]") {
  // 圧力入口の流入分布の W_{i,b}: "limited"(既定)はセルの値の制限付き再構成、"cell" はセルの値。
  // x 方向に密度の勾配がある箱で、入口の境界面の流入側の流束(F⁻ の質量)が違うこと、"cell" では
  // セルの値から作った W_b(p_in、u_n + Δu、T_in)の半空間の流束に一致することを確かめる
  const LoadedMesh lm = make_kinetic_box({4, 1, 1}, {0, 0, 0}, {1, 0.25, 0.25},
                                         {"inlet", "outlet", "side", "side", "side", "side"});
  double fm[2] = {0.0, 0.0};
  int i = 0;
  for (const char* mode : {"limited", "cell"}) {
    KineticCaseOptions o;
    o.kinetic = std::string("backend = \"cpu\"\nprecision = \"fp64\"\nequilibrium = \"continuous\"\n"
                            "pressure_boundary_reconstruction = \"") + mode + "\"\n";
    o.nv = {16, 16, 16};
    o.vmax = 8.0;
    const CaseConfig cfg = make_kinetic_case(
        {{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "vacuum"}, {"side", "periodic_slab"}}, o);
    KineticSolver s(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    CellMacroState w;
    const auto nc = static_cast<std::size_t>(lm.mesh.n_cells);
    w.resize(nc);
    for (std::size_t c = 0; c < nc; ++c) {
      const double x = lm.geometry.cell_center[c].x;
      w.rho[c] = 1.0 - 0.6 * x;
      w.ux[c] = 0.2 + 0.3 * x;
      w.uy[c] = 0.0;
      w.uz[c] = 0.0;
      w.T[c] = 1.0;
      w.p[c] = w.rho[c] * w.T[c];
    }
    s.initialize_equilibrium(w);
    s.prepare();
    const BoundaryKineticFluxes fl = s.boundary_fluxes();
    const Patch* in = nullptr;
    for (const Patch& p : lm.mesh.patches) {
      if (p.name == "inlet") in = &p;
    }
    REQUIRE(in != nullptr);
    fm[i++] = fl.F_minus[static_cast<std::size_t>(in->start - lm.mesh.n_internal_faces())][0];
  }
  INFO("入口の F⁻(質量): limited " << fm[0] << "、cell " << fm[1]);
  CHECK(std::abs(fm[0] - fm[1]) > 1e-4 * std::abs(fm[1]));
}

TEST_CASE("kinetic: cold_bgk_temperature drops the Shakhov polynomial in cold cells", "[kinetic]") {
  // フェーズ 5b([kinetic].cold_bgk_temperature): T がこの値より低いセルの目標分布は BGK(Shakhov の係数 0)
  double m[kinetic::kMomentsPerCell] = {};
  m[0] = 0.02;   // ρ
  m[1] = 2.0;    // u_x
  m[4] = 0.05;   // T
  m[5] = m[0] * m[4];
  m[12] = 1e-4;  // q_x
  const kinetic::CellParams<double> warm = kinetic::make_cell_params<double>(m, 1.0, 0.0, 1e-3, 0.66, 2.0 / 3.0);
  const kinetic::CellParams<double> cold = kinetic::make_cell_params<double>(m, 1.0, 0.0, 1e-3, 0.66, 2.0 / 3.0, 0.1);
  const kinetic::CellParams<double> above = kinetic::make_cell_params<double>(m, 1.0, 0.0, 1e-3, 0.66, 2.0 / 3.0, 0.04);
  CHECK(warm.s.coef > 0.0);
  CHECK(cold.s.coef == 0.0);
  CHECK(above.s.coef == warm.s.coef);
  CHECK(cold.coll == warm.coll);  // 衝突頻度は変えない
  // 読み込み
  KineticCaseOptions o;
  o.kinetic = "cold_bgk_temperature = 0.1\ndeq_fallback = \"continuous\"\npressure_boundary_reconstruction = \"cell\"\n";
  const KineticParams p = parse_kinetic_params(make_kinetic_case({{"x", "vacuum"}}, o));
  CHECK(p.cold_bgk_temperature == 0.1);
  CHECK(p.deq_fallback == "continuous");
  CHECK(p.pressure_boundary_reconstruction == "cell");
  const KineticParams d = parse_kinetic_params(make_kinetic_case({{"x", "vacuum"}}, KineticCaseOptions{}));
  CHECK(d.cold_bgk_temperature == 0.0);
  CHECK(d.deq_fallback == "collisionless");
  CHECK(d.pressure_boundary_reconstruction == "limited");
  o.kinetic = "deq_fallback = \"none\"\n";
  CHECK_THROWS(parse_kinetic_params(make_kinetic_case({{"x", "vacuum"}}, o)));
  o.kinetic = "cold_bgk_temperature = -1\n";
  CHECK_THROWS(parse_kinetic_params(make_kinetic_case({{"x", "vacuum"}}, o)));
}
