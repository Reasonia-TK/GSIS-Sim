// config: ケース TOML の読み込み・検証・導出量

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <cmath>
#include <string>

#include "config/case_config.hpp"
#include "config/case_mesh_check.hpp"
#include "core/error.hpp"
#include "core/log.hpp"
#include "core/math.hpp"
#include "core/paths.hpp"
#include "mesh/geometry.hpp"
#include "support/box_mesh.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using Catch::Approx;
using Catch::Matchers::ContainsSubstring;

namespace {

// docs/spec.md §4.6 の例
const std::string kSpecExample = R"(
[case]
name = "tube_vacuum_delta10"
mesh = "meshes/tube.pmesh"
mode = "gsis"                       # "gsis" | "cis"

[gas]
molecular_mass = 6.65e-27           # kg (He)
mu_ref = 1.916e-5                   # Pa s
T_ref = 273.15                      # K
omega = 0.66

[conditions]
L_ref = 1.0e-3                      # m(管半径)
T0 = 273.15                         # K
delta_rp = 10.0                     # p_in はこれから決める

[[boundary]]
patch = "inlet"
type = "pressure_inlet"
temperature = 273.15

[[boundary]]
patch = "outlet"
type = "vacuum"

[[boundary]]
patch = "wall"
type = "diffuse_wall"
temperature = 273.15

[velocity_grid]
n = [30, 24, 24]
range = [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]]   # 単位 sqrt(R*T0)

[gsis]
n_cis = 5
max_pbc = 10
cfl_pbc = 500.0
tolerance = 1.0e-5
)";

std::string replace(std::string s, const std::string& from, const std::string& to) {
  const std::size_t pos = s.find(from);
  REQUIRE(pos != std::string::npos);
  s.replace(pos, from.size(), to);
  return s;
}

}  // namespace

TEST_CASE("case TOML: spec example is parsed", "[config]") {
  const CaseConfig cfg = parse_case_toml(kSpecExample, path_from_utf8("cases/tube/case.toml"));
  CHECK(cfg.case_info.name == "tube_vacuum_delta10");
  CHECK(cfg.case_info.mode == SolverMode::Gsis);
  // パスは要素ごとに比べる(Windows では区切り文字 / と \ が混ざるため)
  CHECK((cfg.mesh_path() == path_from_utf8("cases/tube/meshes/tube.pmesh")));
  CHECK(cfg.gas.molecular_mass == 6.65e-27);
  CHECK(cfg.gas.mu_ref == 1.916e-5);
  CHECK(cfg.gas.T_ref == 273.15);
  CHECK(cfg.gas.omega == 0.66);
  CHECK(cfg.conditions.L_ref == 1.0e-3);
  CHECK(cfg.conditions.T0 == 273.15);
  CHECK(cfg.conditions.delta_rp == 10.0);
  REQUIRE(cfg.boundaries.size() == 3);
  CHECK(cfg.boundaries[0].type == BoundaryType::PressureInlet);
  CHECK(cfg.boundaries[0].temperature == 273.15);
  CHECK_FALSE(cfg.boundaries[0].pressure.has_value());
  CHECK(cfg.boundaries[1].type == BoundaryType::Vacuum);
  CHECK(cfg.find_boundary("wall")->type == BoundaryType::DiffuseWall);
  CHECK(cfg.find_boundary("nothing") == nullptr);
  CHECK(cfg.velocity_grid.n == std::array<int, 3>{30, 24, 24});
  CHECK(cfg.velocity_grid.lo[0] == -10.0);
  CHECK(cfg.velocity_grid.hi[2] == 8.0);
  CHECK(cfg.gsis.n_cis == 5);
  CHECK(cfg.gsis.max_pbc == 10);
  CHECK(cfg.gsis.cfl_pbc == 500.0);
  CHECK(cfg.gsis.tolerance == 1e-5);
  CHECK(cfg.ignored_sections.empty());
}

TEST_CASE("case TOML: derived quantities", "[config]") {
  const CaseConfig cfg = parse_case_toml(kSpecExample);
  const DerivedQuantities d = derive_quantities(cfg);
  const double R = 1.380649e-23 / 6.65e-27;
  CHECK(d.R == Approx(R).epsilon(1e-15));
  CHECK(d.R == Approx(2076.164).epsilon(1e-6));
  CHECK(d.mu0 == Approx(1.916e-5).epsilon(1e-15));  // T0 = T_ref
  const double vmp = std::sqrt(2.0 * R * 273.15);
  CHECK(d.most_probable_speed == Approx(vmp).epsilon(1e-15));
  const double p_in = 10.0 * 1.916e-5 * vmp / 1.0e-3;
  CHECK(d.p_in == Approx(p_in).epsilon(1e-14));
  // δ_rp = √π / (2 Kn)
  CHECK(d.Kn == Approx(kSqrtPi / 20.0).epsilon(1e-13));
  CHECK(d.Kn == Approx(8.86e-2).epsilon(1e-3));  // paper_notes §7.1 の値
  CHECK(d.delta_from_Kn == Approx(10.0).epsilon(1e-13));
  CHECK(d.mean_free_path == Approx(d.Kn * 1e-3).epsilon(1e-15));
  // 無次元化
  CHECK(d.ref.pressure == d.p_in);
  CHECK(d.ref.density == Approx(p_in / (R * 273.15)).epsilon(1e-14));
  CHECK(d.ref.velocity == Approx(std::sqrt(R * 273.15)).epsilon(1e-15));
  CHECK(d.ref.time == Approx(1e-3 / std::sqrt(R * 273.15)).epsilon(1e-14));
  CHECK(d.ref.distribution ==
        Approx(d.ref.density / std::pow(R * 273.15, 1.5)).epsilon(1e-13));
  CHECK(d.mu_hat0 == Approx(1.0 / (std::sqrt(2.0) * 10.0)).epsilon(1e-13));
  CHECK(d.mass_flow_scale == Approx(kSqrtPi * 1e-6 * p_in / vmp).epsilon(1e-14));
}

TEST_CASE("case TOML: viscosity power law and T0 different from T_ref", "[config]") {
  std::string text = replace(kSpecExample, "T0 = 273.15", "T0 = 300");
  const CaseConfig cfg = parse_case_toml(text);
  CHECK(cfg.conditions.T0 == 300.0);  // 整数でも数値として読む
  const DerivedQuantities d = derive_quantities(cfg);
  CHECK(d.mu0 == Approx(1.916e-5 * std::pow(300.0 / 273.15, 0.66)).epsilon(1e-14));
  CHECK(d.delta_from_Kn == Approx(10.0).epsilon(1e-13));
}

TEST_CASE("case TOML: optional parameters", "[config]") {
  std::string text = kSpecExample;
  text = replace(text, "[gsis]\nn_cis = 5\nmax_pbc = 10\ncfl_pbc = 500.0\ntolerance = 1.0e-5\n", "");
  text = replace(text, "type = \"vacuum\"", "type = \"pressure_outlet\"\npressure = 0.0");
  text = replace(text, "type = \"diffuse_wall\"\ntemperature = 273.15",
                 "type = \"diffuse_wall\"\ntemperature = 300.0\nvelocity = [0.0, 10, 0.0]");
  text += "\n[future_section]\nkey = 1\n";
  const CaseConfig cfg = parse_case_toml(text);
  CHECK(cfg.gsis.n_cis == 5);  // 既定値
  CHECK(cfg.gsis.tolerance == 1e-5);
  CHECK(cfg.find_boundary("outlet")->type == BoundaryType::PressureOutlet);
  CHECK(*cfg.find_boundary("outlet")->pressure == 0.0);
  REQUIRE(cfg.find_boundary("wall")->velocity.has_value());
  CHECK(cfg.find_boundary("wall")->velocity->y == 10.0);
  REQUIRE(cfg.ignored_sections.size() == 1);
  CHECK(cfg.ignored_sections[0] == "future_section");
}

TEST_CASE("case and mesh are checked against each other", "[config]") {
  // パッチ: inlet(x−)、outlet(x+)、sym_y(y− と y+)、wall(z− と z+)、unused(面 0 個)
  test::BoxMeshOptions o;
  o.n = {3, 2, 2};
  o.patch_names = {"inlet", "outlet", "sym_y", "sym_y", "wall", "wall"};
  o.patch_kinds = {"inlet", "outlet", "symmetry", "symmetry", "wall", "wall"};
  PolyMesh mesh = test::make_box_mesh(o);
  Patch unused;
  unused.name = "unused";
  unused.start = mesh.n_faces();
  unused.count = 0;
  mesh.patches.push_back(unused);
  const MeshGeometry g = compute_geometry(mesh);

  std::string text = kSpecExample;
  text = replace(text, "[[boundary]]\npatch = \"wall\"",
                 "[[boundary]]\npatch = \"sym_y\"\ntype = \"symmetry\"\n\n"
                 "[[boundary]]\npatch = \"unused\"\ntype = \"symmetry\"\n\n"
                 "[[boundary]]\npatch = \"wall\"");
  const CaseConfig cfg = parse_case_toml(text);
  const VelocityGrid symmetric({8, 8, 8}, {-4.0, -4.0, -4.0}, {4.0, 4.0, 4.0});
  CHECK(check_case_against_mesh(cfg, mesh, g, symmetric).empty());  // 面 0 個の対称面は調べない

  // y の範囲が対称でない速度格子では、sym_y が使えない
  const VelocityGrid asym({8, 8, 8}, {-4.0, -3.0, -4.0}, {4.0, 4.0, 4.0});
  const auto e1 = check_case_against_mesh(cfg, mesh, g, asym);
  REQUIRE(e1.size() == 1);
  CHECK_THAT(e1[0], ContainsSubstring("sym_y"));

  // 法線の軸が混ざったパッチ(inlet と outlet の他は y と z が混ざる)を対称面にはできない
  CaseConfig mixed = cfg;
  for (BoundarySpec& b : mixed.boundaries) {
    if (b.patch == "wall") b.type = BoundaryType::Symmetry;
  }
  const auto e2 = check_case_against_mesh(mixed, mesh, g, symmetric);
  CHECK(e2.empty());  // wall は z− と z+ だけなので軸 z に揃っている
  const PolyMesh tube = test::make_box_mesh(test::tube_like_box({3, 2, 2}, Vec3{0, 0, 0}, Vec3{3, 1, 1}));
  const MeshGeometry gt = compute_geometry(tube);
  CaseConfig tube_cfg = parse_case_toml(kSpecExample);
  for (BoundarySpec& b : tube_cfg.boundaries) {
    if (b.patch == "wall") b.type = BoundaryType::Symmetry;
  }
  const auto e3 = check_case_against_mesh(tube_cfg, tube, gt, symmetric);
  REQUIRE(e3.size() == 1);
  CHECK_THAT(e3[0], ContainsSubstring("揃っていません"));

  // パッチと境界条件の過不足
  CaseConfig missing = parse_case_toml(kSpecExample);
  const auto e4 = check_case_against_mesh(missing, mesh, g, symmetric);
  CHECK(e4.size() == 2);  // sym_y と unused に境界条件がない
}

TEST_CASE("case TOML: reference pressure from Kn or p_ref", "[config]") {
  const DerivedQuantities d_delta = derive_quantities(parse_case_toml(kSpecExample));

  // Kn = √π/20 は δ_rp = 10 と同じ基準圧力になる。圧力入口の pressure は明示が必要
  std::string with_kn = replace(kSpecExample, "delta_rp = 10.0", "Kn = 0.08862269254527580");
  with_kn = replace(with_kn, "type = \"pressure_inlet\"\ntemperature = 273.15",
                    "type = \"pressure_inlet\"\ntemperature = 273.15\npressure = 20.0");
  const CaseConfig cfg_kn = parse_case_toml(with_kn);
  CHECK_FALSE(cfg_kn.conditions.delta_rp.has_value());
  const DerivedQuantities d_kn = derive_quantities(cfg_kn);
  CHECK_FALSE(d_kn.has_p_in);
  CHECK(d_kn.p_ref == Approx(d_delta.p_ref).epsilon(1e-13));
  CHECK(d_kn.Kn == Approx(0.0886226925452758).epsilon(1e-13));
  CHECK(d_kn.ref.pressure == d_kn.p_ref);

  std::string with_p = replace(with_kn, "Kn = 0.08862269254527580", "p_ref = 12.5");
  const DerivedQuantities d_p = derive_quantities(parse_case_toml(with_p));
  CHECK(d_p.p_ref == 12.5);
  CHECK(d_p.Kn == Approx(d_delta.Kn * d_delta.p_ref / 12.5).epsilon(1e-13));
  CHECK(d_delta.has_p_in);
  CHECK(d_delta.p_ref == d_delta.p_in);
}

TEST_CASE("case TOML: periodic_slab boundary", "[config]") {
  // 1 セル厚(y 方向)の箱: y− と y+ が同じパッチなら、各セルの 2 面が対になる
  test::BoxMeshOptions o;
  o.n = {3, 1, 1};
  o.patch_names = {"inlet", "outlet", "per_y", "per_y", "wall", "wall"};
  o.patch_kinds = {"inlet", "outlet", "patch", "patch", "wall", "wall"};
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);
  std::string text = replace(kSpecExample, "[[boundary]]\npatch = \"wall\"",
                             "[[boundary]]\npatch = \"per_y\"\ntype = \"periodic_slab\"\n\n"
                             "[[boundary]]\npatch = \"wall\"");
  const CaseConfig cfg = parse_case_toml(text);
  CHECK(cfg.find_boundary("per_y")->type == BoundaryType::PeriodicSlab);
  CHECK(to_string(BoundaryType::PeriodicSlab) == "periodic_slab");
  const VelocityGrid vg({8, 8, 8}, {-4.0, -4.0, -4.0}, {4.0, 4.0, 4.0});
  CHECK(check_case_against_mesh(cfg, mesh, g, vg).empty());

  // y 方向に 2 セルあると、各セルの per_y の面は 1 枚だけで対にならない
  o.n = {3, 2, 1};
  const PolyMesh mesh2 = test::make_box_mesh(o);
  const MeshGeometry g2 = compute_geometry(mesh2);
  const auto e = check_case_against_mesh(cfg, mesh2, g2, vg);
  REQUIRE(e.size() == 1);
  CHECK_THAT(e[0], ContainsSubstring("対になっていません"));
}

TEST_CASE("case TOML: module sections", "[config]") {
  set_log_level(LogLevel::Error);
  std::string text = kSpecExample;
  text += "\n[kinetic]\npseudo_cfl = 0\nlimiter_k = 5.0\nsweep_blocks = 64\nprecision = \"fp32\"\n"
          "fixed_mass = true\n\n[macro]\nadvection = \"upwind1\"\n";
  const CaseConfig cfg = parse_case_toml(text);
  CHECK(cfg.ignored_sections.empty());
  REQUIRE(cfg.module_sections.size() == 2);
  const ModuleSection& k = cfg.module_section("kinetic");
  CHECK(k.name() == "kinetic");
  CHECK(k.number("pseudo_cfl", 1.0) == 0.0);  // 整数も数値として読む
  CHECK(k.number("limiter_k", 1.0) == 5.0);
  CHECK(k.integer("sweep_blocks", 1) == 64);
  CHECK(k.string("precision", "fp64") == "fp32");
  CHECK(k.boolean("fixed_mass", false));
  CHECK(k.integer("velocity_chunk", 0) == 0);  // 無ければ既定値
  CHECK_NOTHROW(k.finish());
  CHECK_THROWS_AS(k.integer("limiter_k", 0), Error);  // 実数を整数としては読めない

  const ModuleSection& m = cfg.module_section("macro");
  CHECK_THROWS_WITH(m.finish(), ContainsSubstring("advection"));  // 読んでいないキー
  CHECK(m.string("advection", "") == "upwind1");
  CHECK_NOTHROW(m.finish());

  const ModuleSection& none = cfg.module_section("output");  // 無い節は空
  CHECK(none.values.empty());
  CHECK_NOTHROW(none.finish());

  CHECK_THROWS_WITH(parse_case_toml(kSpecExample + "\n[kinetic]\nlist = [1, 2]\n"),
                    ContainsSubstring("[kinetic].list はスカラー"));
  set_log_level(LogLevel::Info);
}

TEST_CASE("case TOML: errors are reported", "[config]") {
  set_log_level(LogLevel::Error);
  auto expect_error = [](const std::string& text, const std::string& fragment) {
    try {
      parse_case_toml(text, path_from_utf8("bad.toml"));
      FAIL("例外が出ませんでした: " + fragment);
    } catch (const Error& e) {
      CHECK_THAT(e.what(), ContainsSubstring(fragment));
    }
  };
  expect_error(replace(kSpecExample, "omega = 0.66", "omega = \"0.66\""), "gas.omega");
  expect_error(replace(kSpecExample, "omega = 0.66\n", ""), "omega がありません");
  expect_error(replace(kSpecExample, "mode = \"gsis\"", "mode = \"dsmc\""), "case.mode");
  expect_error(replace(kSpecExample, "type = \"vacuum\"", "type = \"open\""), "知らない境界条件");
  expect_error(replace(kSpecExample, "patch = \"outlet\"", "patch = \"inlet\""), "重複");
  expect_error(replace(kSpecExample, "omega = 0.66", "omega = 0.66\nomegaa = 1"), "知らないキー omegaa");
  expect_error(replace(kSpecExample, "delta_rp = 10.0", "delta_rp = -1.0"), "conditions.delta_rp は正");
  expect_error(replace(kSpecExample, "n = [30, 24, 24]", "n = [30, 24]"), "velocity_grid.n");
  expect_error(replace(kSpecExample, "[-8.0, 8.0], [-8.0, 8.0]]", "[8.0, -8.0], [-8.0, 8.0]]"), "下限");
  expect_error(replace(kSpecExample, "type = \"vacuum\"", "type = \"pressure_outlet\""), "pressure がありません");
  expect_error(replace(kSpecExample, "[velocity_grid]", "[velocity_grid_typo]"), "必須の節 [velocity_grid]");
  expect_error(replace(kSpecExample, "max_pbc = 10", "max_pbc = 1.5"), "gsis.max_pbc は整数");
  expect_error(replace(kSpecExample, "molecular_mass = 6.65e-27", "molecular_mass = 6.65"), "大きすぎ");
  expect_error("[case\nname = 1", "TOML の解析に失敗");
  expect_error(replace(kSpecExample, "delta_rp = 10.0", "delta_rp = 10.0\nKn = 0.1"), "どれか 1 つ");
  expect_error(replace(kSpecExample, "delta_rp = 10.0", ""), "どれか 1 つ");
  expect_error(replace(kSpecExample, "delta_rp = 10.0", "Kn = 0.1"), "delta_rp を与えたときだけ");
  expect_error(replace(kSpecExample, "delta_rp = 10.0", "p_ref = 0.0"), "conditions.p_ref は正");
  set_log_level(LogLevel::Info);
}
