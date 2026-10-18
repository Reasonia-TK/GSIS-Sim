// kinetic_gpu: 運動論ソルバーの GPU 版(GPU がなければ SKIP)
//   - CPU 参照実装との一致(FP32 は丸め誤差の範囲、FP64 はほぼ一致)。全種類の境界条件を含む小さなメッシュ
//   - 決定性(同じ入力で 2 回計算するとビット単位で同じ)
//   - 起動の分け方(セル範囲・スイープの範囲・速度チャンク)によらないこと
//   - GSIS の補正

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <string>
#include <vector>

#include "gpu/device_info.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "kinetic_test_cases.hpp"

using namespace gsis;
using gsis::test::KineticBc;
using gsis::test::KineticCaseOptions;
using gsis::test::make_kinetic_box;
using gsis::test::make_kinetic_case;

namespace {

struct RunResult {
  std::vector<double> f;
  KineticMoments m;
  BoundaryKineticFluxes fl;
  std::vector<double> residual;
};

// 全種類の境界: x− 圧力入口、x+ 圧力出口、y の 2 面は動く拡散壁、z の 2 面は対称面
RunResult run_mixed(const std::string& kinetic, int iterations, double distortion = 0.15) {
  const LoadedMesh lm = make_kinetic_box({5, 3, 2}, {0, 0, 0}, {1, 0.6, 0.4},
                                         {"inlet", "outlet", "wall", "wall", "sym", "sym"}, distortion);
  KineticCaseOptions o;
  o.Kn = 0.1;
  o.kinetic = kinetic;
  const CaseConfig cfg = make_kinetic_case({{"inlet", "pressure_inlet", 1.1, 1.0},
                                            {"outlet", "pressure_outlet", 1.0, 0.3},
                                            {"wall", "diffuse_wall", 0.9, 1.0, Vec3{0.1, 0.0, 0.05}},
                                            {"sym", "symmetry"}},
                                           o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  solver.initialize_uniform(0.7, Vec3{0.1, 0.0, 0.0}, 1.0);
  RunResult r;
  for (int i = 0; i < iterations; ++i) r.residual.push_back(solver.sweep().residual);
  r.f = solver.distribution();
  r.m = solver.moments();
  r.fl = solver.boundary_fluxes();
  return r;
}

// 閉じた領域(拡散壁 + 並進周期 + 真空なし): クエット流の平板
RunResult run_couette(const std::string& kinetic, int iterations) {
  const LoadedMesh lm = make_kinetic_box({6, 1, 1}, {0, 0, 0}, {1, 1.0 / 6, 1.0 / 6},
                                         {"wall_left", "wall_right", "sym_y", "sym_y", "sym_z", "sym_z"});
  KineticCaseOptions o;
  o.Kn = 0.5;
  o.kinetic = kinetic;
  const CaseConfig cfg = make_kinetic_case({{"wall_left", "diffuse_wall", 0.95, 1.0, Vec3{0, -0.4, 0}},
                                            {"wall_right", "diffuse_wall", 1.05, 1.0, Vec3{0, 0.4, 0}},
                                            {"sym_y", "periodic_slab"},
                                            {"sym_z", "symmetry"}},
                                           o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  solver.initialize_uniform(1.0, Vec3{}, 1.0);
  RunResult r;
  for (int i = 0; i < iterations; ++i) r.residual.push_back(solver.sweep().residual);
  r.f = solver.distribution();
  r.m = solver.moments();
  r.fl = solver.boundary_fluxes();
  return r;
}

double max_rel(const std::vector<double>& a, const std::vector<double>& b) {
  double d = 0.0, s = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    d = std::max(d, std::abs(a[i] - b[i]));
    s = std::max(s, std::abs(b[i]));
  }
  return s > 0.0 ? d / s : d;
}

void compare(const RunResult& g, const RunResult& c, double tol_f, double tol_m) {
  CHECK(max_rel(g.f, c.f) < tol_f);
  for (std::size_t i = 0; i < c.m.state.size(); ++i) {
    CHECK(g.m.state.rho[i] == Catch::Approx(c.m.state.rho[i]).epsilon(tol_m));
    CHECK(g.m.state.T[i] == Catch::Approx(c.m.state.T[i]).epsilon(tol_m));
    CHECK(g.m.state.ux[i] == Catch::Approx(c.m.state.ux[i]).margin(tol_m));
    CHECK(g.m.state.uy[i] == Catch::Approx(c.m.state.uy[i]).margin(tol_m));
    for (std::size_t k = 0; k < 3; ++k) CHECK(g.m.q[i][k] == Catch::Approx(c.m.q[i][k]).margin(10 * tol_m));
    for (std::size_t k = 0; k < 6; ++k) {
      CHECK(g.m.sigma[i][k] == Catch::Approx(c.m.sigma[i][k]).margin(10 * tol_m));
    }
  }
  double fs = 0.0;
  for (const auto& x : c.fl.F_plus) fs = std::max(fs, std::abs(x[0]));
  for (std::size_t b = 0; b < c.fl.F_plus.size(); ++b) {
    for (std::size_t k = 0; k < 5; ++k) {
      CHECK(g.fl.F_plus[b][k] == Catch::Approx(c.fl.F_plus[b][k]).margin(10 * tol_m * fs));
      CHECK(g.fl.F_minus[b][k] == Catch::Approx(c.fl.F_minus[b][k]).margin(10 * tol_m * fs));
    }
  }
  for (std::size_t i = 0; i < c.residual.size(); ++i) {
    CHECK(g.residual[i] == Catch::Approx(c.residual[i]).epsilon(100 * tol_m));
  }
}

bool same_bits(const std::vector<double>& a, const std::vector<double>& b) {
  return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin());
}

}  // namespace

TEST_CASE("kinetic_gpu: GPU agrees with the CPU reference (FP32)", "[kinetic_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  const RunResult g = run_mixed("backend = \"cuda\"\nsweep_blocks = 3\n", 6);
  const RunResult c = run_mixed("backend = \"cpu\"\nsweep_blocks = 3\n", 6);
  compare(g, c, 2e-5, 2e-5);
  const RunResult gc = run_couette("backend = \"cuda\"\nsweep_blocks = 2\n", 10);
  const RunResult cc = run_couette("backend = \"cpu\"\nsweep_blocks = 2\n", 10);
  compare(gc, cc, 2e-5, 2e-5);
}

TEST_CASE("kinetic_gpu: GPU agrees with the CPU reference (FP64)", "[kinetic_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  // 従来の CIS の設定(block の順、内部反復 1 回、ブロック間の反復 1 回、連続の平衡分布)。フェーズ 5b で既定が
  // 強化した設定になったので、従来の経路の GPU と CPU の一致はここで明示して確かめる
  const std::string old_cis =
      "sweep_order = \"block\"\ninner_sweeps = 1\ninner_wall_update = false\nblock_passes = 1\nequilibrium = \"continuous\"\n";
  const RunResult g =
      run_mixed("backend = \"cuda\"\nprecision = \"fp64\"\nsweep_blocks = 4\nlimiter_eps = \"relative\"\n" + old_cis, 6);
  const RunResult c =
      run_mixed("backend = \"cpu\"\nprecision = \"fp64\"\nsweep_blocks = 4\nlimiter_eps = \"relative\"\n" + old_cis, 6);
  compare(g, c, 1e-11, 1e-11);
  // 既定(強化した設定)でも FP64 でほぼ一致する
  const RunResult g2 = run_mixed("backend = \"cuda\"\nprecision = \"fp64\"\nsweep_blocks = 4\n", 4);
  const RunResult c2 = run_mixed("backend = \"cpu\"\nprecision = \"fp64\"\nsweep_blocks = 4\n", 4);
  compare(g2, c2, 1e-10, 1e-10);
}

TEST_CASE("kinetic_gpu: GPU agrees with the CPU for direction sweeps, inner sweeps and the discrete equilibrium",
          "[kinetic_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  // 速度格子は Δξ = 1(±5 に 10 点)なので、T ≈ 1 でも離散平衡の補正がかかる
  const std::string opts =
      "sweep_blocks = 3\nsweep_order = \"octant\"\ninner_sweeps = 2\ninner_wall_update = true\n"
      "equilibrium = \"discrete\"\nblock_passes = 2\n";
  const RunResult g = run_mixed("backend = \"cuda\"\n" + opts, 5);
  const RunResult c = run_mixed("backend = \"cpu\"\n" + opts, 5);
  compare(g, c, 3e-5, 3e-5);
  const RunResult g13 = run_mixed("backend = \"cuda\"\nsweep_blocks = 2\nsweep_order = \"dir13\"\n", 4);
  const RunResult c13 = run_mixed("backend = \"cpu\"\nsweep_blocks = 2\nsweep_order = \"dir13\"\n", 4);
  compare(g13, c13, 2e-5, 2e-5);
}

TEST_CASE("kinetic_gpu: GPU results are deterministic and independent of launch splitting", "[kinetic_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  const RunResult a = run_mixed("backend = \"cuda\"\nsweep_blocks = 3\n", 5);
  const RunResult b = run_mixed("backend = \"cuda\"\nsweep_blocks = 3\n", 5);
  CHECK(same_bits(a.f, b.f));
  CHECK(same_bits(a.residual, b.residual));
  // セル範囲・スイープの範囲・速度チャンクを細かく分けても同じ
  const RunResult c = run_mixed(
      "backend = \"cuda\"\nsweep_blocks = 3\nlaunch_cells = 7\nsweep_cells_per_launch = 3\nvelocity_chunk = 256\n", 5);
  CHECK(same_bits(a.f, c.f));
  for (std::size_t i = 0; i < a.m.state.size(); ++i) CHECK(a.m.state.rho[i] == c.m.state.rho[i]);
}

TEST_CASE("kinetic_gpu: section mass flows on the GPU agree with the CPU and are deterministic", "[kinetic_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  // 全種類の境界(run_mixed と同じ)の途中の状態で、断面の質量流量(フェーズ 5c)を比べる
  auto sections = [](const std::string& kinetic) {
    const LoadedMesh lm = make_kinetic_box({5, 3, 2}, {0, 0, 0}, {1, 0.6, 0.4},
                                           {"inlet", "outlet", "wall", "wall", "sym", "sym"}, 0.15);
    KineticCaseOptions o;
    o.Kn = 0.1;
    o.kinetic = kinetic;
    const CaseConfig cfg = make_kinetic_case({{"inlet", "pressure_inlet", 1.1, 1.0},
                                              {"outlet", "pressure_outlet", 1.0, 0.3},
                                              {"wall", "diffuse_wall", 0.9, 1.0, Vec3{0.1, 0.0, 0.05}},
                                              {"sym", "symmetry"}},
                                             o);
    KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    solver.initialize_uniform(0.7, Vec3{0.1, 0.0, 0.0}, 1.0);
    for (int i = 0; i < 4; ++i) solver.sweep();
    SectionSpec spec;
    spec.positions = {0.1, 0.3, 0.5, 0.75, 0.95};
    return solver.section_mass_flow(spec);
  };
  const std::vector<double> g = sections("backend = \"cuda\"\nsweep_blocks = 3\n");
  const std::vector<double> c = sections("backend = \"cpu\"\nsweep_blocks = 3\n");
  REQUIRE(g.size() == 5);
  REQUIRE(c.size() == 5);
  CHECK(max_rel(g, c) < 3e-5);
  const std::vector<double> g2 = sections("backend = \"cuda\"\nsweep_blocks = 3\n");
  CHECK(same_bits(g, g2));
  // FP64 ではほぼ一致する
  const std::vector<double> g64 = sections("backend = \"cuda\"\nprecision = \"fp64\"\nsweep_blocks = 3\n");
  const std::vector<double> c64 = sections("backend = \"cpu\"\nprecision = \"fp64\"\nsweep_blocks = 3\n");
  CHECK(max_rel(g64, c64) < 1e-10);
}

TEST_CASE("kinetic_gpu: GSIS correction on the GPU agrees with the CPU", "[kinetic_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  auto correct = [](const char* backend) {
    const LoadedMesh lm = make_kinetic_box({3, 2, 1}, {0, 0, 0}, {1, 1, 0.5},
                                           {"wall", "wall", "wall", "wall", "wall", "wall"});
    KineticCaseOptions o;
    o.kinetic = std::string("backend = \"") + backend + "\"\n";
    const CaseConfig cfg = make_kinetic_case({{"wall", "diffuse_wall", 1.0}}, o);
    KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    CellMacroState wh, wn;
    const auto nc = static_cast<std::size_t>(lm.mesh.n_cells);
    wh.resize(nc);
    wn.resize(nc);
    for (std::size_t c = 0; c < nc; ++c) {
      const double x = static_cast<double>(c);
      wh.rho[c] = 1.0 + 0.05 * x;
      wh.ux[c] = 0.05 * x;
      wh.uy[c] = 0.0;
      wh.uz[c] = -0.02;
      wh.T[c] = 1.0 - 0.02 * x;
      wn.rho[c] = wh.rho[c] * 1.01;
      wn.ux[c] = c == 4 ? 2.5 : wh.ux[c] + 0.01;
      wn.uy[c] = 0.01;
      wn.uz[c] = -0.02;
      wn.T[c] = wh.T[c] * 1.01;
    }
    solver.initialize_equilibrium(wh);
    for (int i = 0; i < 2; ++i) solver.sweep();
    const long long replaced = solver.apply_gsis_correction(solver.moments().state, wn);
    return std::make_pair(replaced, solver.distribution());
  };
  const auto g = correct("cuda");
  const auto c = correct("cpu");
  CHECK(g.first == c.first);
  CHECK(g.first >= 1);
  CHECK(max_rel(g.second, c.second) < 2e-5);
}
