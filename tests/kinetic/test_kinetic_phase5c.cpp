// kinetic: 断面の質量流量と質量の釣り合い(フェーズ 5c)。CPU 参照実装(FP64)
//   - 一様な流れの平衡分布なら、断面の流量は離散モーメントの ρu_x × 断面積(歪んだ六面体でも)
//   - 内部の番号付けで選んだ断面の面は、元のメッシュで選んだものと同じ集まり
//   - 定常に収束した解では、断面の流量が入口・出口の流量と一致する(離散的な保存)
//   - mass_balance・flow_spread・parse_output_sections

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <cmath>
#include <limits>
#include <string>
#include <vector>

#include "kinetic/kinetic_output.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "kinetic_test_cases.hpp"
#include "mesh/section_cut.hpp"

using namespace gsis;
using gsis::test::KineticCaseOptions;
using gsis::test::make_kinetic_box;
using gsis::test::make_kinetic_case;
using Catch::Approx;
using Catch::Matchers::ContainsSubstring;

TEST_CASE("kinetic: the section mass flow of a uniform flowing equilibrium is rho u_x times the area",
          "[kinetic][section]") {
  // 側面は対称面(一様な流れ u = (u_x, 0, 0) は鏡映で変わらないので勾配 0)。断面は入口・出口に接しないセルの間に置く
  const LoadedMesh lm = make_kinetic_box({8, 3, 2}, {0, 0, 0}, {1, 0.6, 0.4},
                                         {"inlet", "outlet", "sym_y", "sym_y", "sym_z", "sym_z"}, 0.2);
  KineticCaseOptions o;
  o.Kn = 0.1;
  o.kinetic = "backend = \"cpu\"\nprecision = \"fp64\"\nsweep_blocks = 2\n";
  const CaseConfig cfg = make_kinetic_case({{"inlet", "pressure_inlet", 1.0, 1.0},
                                            {"outlet", "pressure_outlet", 1.0, 0.5},
                                            {"sym_y", "symmetry"},
                                            {"sym_z", "symmetry"}},
                                           o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  solver.initialize_uniform(0.8, Vec3{0.3, 0.0, 0.0}, 1.1);
  SectionSpec spec;
  spec.positions = {0.37, 0.5, 0.63};
  const std::vector<double> flow = solver.section_mass_flow(spec);
  const KineticMoments m = solver.moments();
  REQUIRE(flow.size() == 3);
  const double rhou = m.state.rho[0] * m.state.ux[0];
  CHECK(rhou == Approx(0.24).epsilon(1e-6));
  for (const double f : flow) CHECK(f == Approx(rhou * 0.24).epsilon(1e-12));  // 断面積 0.6 × 0.4
  // 内部の番号付けで選んだ断面の面は、元のメッシュで選んだものと同じ数・面積
  const std::vector<SectionCut> ref = build_section_cuts(lm.mesh, lm.geometry, 1.0, spec);
  const std::vector<SectionCut>& kc = solver.section_cuts(spec);
  REQUIRE(kc.size() == ref.size());
  for (std::size_t k = 0; k < kc.size(); ++k) {
    CHECK(kc[k].faces.size() == ref[k].faces.size());
    CHECK(kc[k].area == Approx(ref[k].area).epsilon(1e-12));
    CHECK(kc[k].projected_area == Approx(0.24).epsilon(1e-12));
  }
  CHECK(solver.section_mass_flow(SectionSpec{}).empty());
}

TEST_CASE("kinetic: at a converged steady state the section flows equal the inlet and outlet flows",
          "[kinetic][section]") {
  // 管に似た箱(x− 圧力入口、x+ 真空、ほかは拡散壁)。歪んだ六面体
  const LoadedMesh lm = make_kinetic_box({8, 2, 2}, {0, 0, 0}, {1, 0.25, 0.25},
                                         {"inlet", "outlet", "wall", "wall", "wall", "wall"}, 0.15);
  KineticCaseOptions o;
  o.Kn = 0.2;
  o.nv = {8, 8, 8};
  o.kinetic = "backend = \"cpu\"\nprecision = \"fp64\"\nsweep_blocks = 1\n";
  const CaseConfig cfg = make_kinetic_case(
      {{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "vacuum"}, {"wall", "diffuse_wall"}}, o);
  KineticSolver solver(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  solver.initialize_uniform(0.5, Vec3{}, 1.0);
  SectionSpec spec;
  spec.positions = {0.2, 0.5, 0.8};
  // 収束の途中では断面の流量が入口・出口と違う
  for (int i = 0; i < 3; ++i) solver.sweep();
  {
    const KineticIterationInfo& info = solver.prepare();
    const MassBalance mb = mass_balance(info.patch_mass_flow);
    REQUIRE(mb.valid);
    CHECK(std::abs(mb.imbalance) > 1e-3);
  }
  double residual = 1.0;
  for (int i = 0; i < 1500 && residual > 1e-12; ++i) residual = solver.sweep().residual;
  REQUIRE(residual < 1e-10);
  const KineticIterationInfo& info = solver.prepare();
  const std::vector<double> flow = solver.section_mass_flow(spec);
  const MassBalance mb = mass_balance(info.patch_mass_flow);
  REQUIRE(mb.valid);
  CHECK(mb.inflow > 0.0);
  CHECK(std::abs(mb.imbalance) < 1e-7);
  for (const double f : flow) CHECK(f == Approx(mb.inflow).epsilon(1e-7));
  std::vector<double> all = {mb.inflow, mb.outflow};
  all.insert(all.end(), flow.begin(), flow.end());
  CHECK(flow_spread(all) < 1e-7);
}

TEST_CASE("kinetic: mass_balance, flow_spread and the [output] sections", "[kinetic][section]") {
  const std::vector<double> patches = {-2.0, 1.9, 0.0, 0.05};  // 入口 2、出口 1.9 + 壁 0.05
  const MassBalance b = mass_balance(patches);
  CHECK(b.valid);
  CHECK(b.inflow == 2.0);
  CHECK(b.outflow == Approx(1.95));
  CHECK(b.imbalance == Approx(-0.025));
  CHECK_FALSE(mass_balance(std::vector<double>{0.0, 0.0}).valid);
  CHECK_FALSE(mass_balance(std::vector<double>{}).valid);
  CHECK(flow_spread(std::vector<double>{1.0, 1.1, 0.9}) == Approx(0.2));
  CHECK(std::isnan(flow_spread(std::vector<double>{1.0})));
  CHECK(std::isnan(flow_spread(std::vector<double>{1.0, std::numeric_limits<double>::quiet_NaN()})));

  KineticCaseOptions o;
  o.extra = "[output]\nmass_flow_sections = \"0, 2.5, 5\"\nmass_flow_section_axis = \"z\"\n";
  const SectionSpec s = parse_output_sections(make_kinetic_case({{"x", "vacuum"}}, o));
  CHECK(s.axis == 2);
  CHECK(s.positions == std::vector<double>{0.0, 2.5, 5.0});
  CHECK(parse_output_sections(make_kinetic_case({{"x", "vacuum"}}, KineticCaseOptions{})).empty());
  KineticCaseOptions bad;
  bad.extra = "[output]\nmass_flow_sectoins = \"1\"\n";
  CHECK_THROWS_WITH(parse_output_sections(make_kinetic_case({{"x", "vacuum"}}, bad)),
                    ContainsSubstring("mass_flow_sectoins"));
  bad.extra = "[output]\nmass_flow_sections = \"1, x\"\n";
  CHECK_THROWS(parse_output_sections(make_kinetic_case({{"x", "vacuum"}}, bad)));
}
