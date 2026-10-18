// gsis_gpu: GSIS の反復を GPU の運動論ソルバーで行う試験(GPU がなければ SKIP)
//   - 決定性: 同じ入力で 2 回計算するとビット単位で同じ(分布関数、W^n、PBC の反復数・線形ソルバーの反復数・置換の数)
//   - CPU 参照実装(FP64)との一致: 3 回の GSIS 反復の後の W^n の差が FP32 の丸めの影響の範囲

#include <algorithm>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "gpu/device_info.hpp"
#include "gsis/gsis_solver.hpp"
#include "gsis_test_cases.hpp"
#include "kinetic/kinetic_params.hpp"
#include "macro/macro_params.hpp"

using namespace gsis;
using namespace gsis::test;

namespace {

struct Run {
  std::vector<double> f;
  CellMacroState w;
  std::vector<long long> counts;
};

Run run_gsis(const std::string& kinetic, int iterations) {
  const BoxProblem p = make_tube_box_problem({6, 3, 3}, 0.5, kinetic,
                                             "[gsis]\nn_cis = 3\nmax_pbc = 5\n[macro]\ninit_iterations = 10\n"
                                             "hot_interpolation = \"face\"\n");
  const KineticParams kp = parse_kinetic_params(p.config);
  const MacroParams mp = parse_macro_params(p.config);
  GsisSolver g(p.config, p.mesh.mesh, p.mesh.geometry, kp, mp, GsisOptions::from_case(p.config, kp, mp));
  Run r;
  r.counts.push_back(g.initialize().pbc.iterations);
  for (int n = 0; n < iterations; ++n) {
    const GsisIterationInfo& it = g.iterate();
    r.counts.push_back(it.pbc.iterations);
    r.counts.push_back(it.pbc.linear_iterations_pu);
    r.counts.push_back(it.pbc.linear_iterations_T);
    r.counts.push_back(it.replaced_cells);
  }
  r.f = g.kinetic().distribution();
  r.w = g.state();
  return r;
}

bool bitwise_equal(const std::vector<double>& a, const std::vector<double>& b) {
  return a.size() == b.size() && std::memcmp(a.data(), b.data(), a.size() * sizeof(double)) == 0;
}

}  // namespace

TEST_CASE("gsis_gpu: GSIS with the GPU kinetic solver is deterministic and agrees with the CPU", "[gsis_gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA のデバイスがありません");
  const Run a = run_gsis("backend = \"cuda\"\nprecision = \"fp32\"\n", 3);
  const Run b = run_gsis("backend = \"cuda\"\nprecision = \"fp32\"\n", 3);
  CHECK(a.counts == b.counts);
  CHECK(bitwise_equal(a.f, b.f));
  CHECK(bitwise_equal(a.w.p, b.w.p));
  CHECK(bitwise_equal(a.w.ux, b.w.ux));
  CHECK(bitwise_equal(a.w.T, b.w.T));
  const Run c = run_gsis("backend = \"cpu\"\nprecision = \"fp64\"\n", 3);
  double dp = 0.0, du = 0.0, dT = 0.0;
  for (std::size_t i = 0; i < a.w.size(); ++i) {
    dp = std::max(dp, std::abs(a.w.p[i] / c.w.p[i] - 1.0));
    dT = std::max(dT, std::abs(a.w.T[i] / c.w.T[i] - 1.0));
    du = std::max({du, std::abs(a.w.ux[i] - c.w.ux[i]), std::abs(a.w.uy[i] - c.w.uy[i]),
                   std::abs(a.w.uz[i] - c.w.uz[i])});
  }
  std::printf("GPU(FP32)と CPU(FP64)の W^n の差の最大: p %.2e、u %.2e、T %.2e\n", dp, du, dT);
  CHECK(dp < 1e-4);
  CHECK(du < 1e-4);
  CHECK(dT < 1e-4);
}
