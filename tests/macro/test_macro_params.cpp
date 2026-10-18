// macro: [macro] の読み込み

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <string>

#include "config/case_config.hpp"
#include "core/error.hpp"
#include "macro/macro_params.hpp"

using namespace gsis;
using Catch::Matchers::ContainsSubstring;

namespace {

const std::string kBase = R"(
[case]
name = "macro_params"
mesh = "box.pmesh"

[gas]
molecular_mass = 6.65e-27
mu_ref = 1.916e-5
T_ref = 273.15
omega = 0.66

[conditions]
L_ref = 1.0e-3
T0 = 273.15
delta_rp = 10.0

[[boundary]]
patch = "wall"
type = "diffuse_wall"
temperature = 273.15

[velocity_grid]
n = [8, 8, 8]
range = [[-4.0, 4.0], [-4.0, 4.0], [-4.0, 4.0]]
)";

}  // namespace

TEST_CASE("macro params: defaults and values", "[macro]") {
  const MacroParams d = parse_macro_params(parse_case_toml(kBase));
  // 移流項の既定はフェーズ 5c で 2 次にした(初期化は 1 次のまま)
  CHECK(d.advection == "upwind2");
  CHECK(d.init_advection == "upwind1");
  CHECK(d.init_iterations == 50);

  const MacroParams p = parse_macro_params(parse_case_toml(
      kBase + "\n[macro]\nadvection = \"upwind1\"\nlinear_rtol = 1e-6\ninit_iterations = 0\n"));
  CHECK(p.advection == "upwind1");
  CHECK(p.linear_rtol == 1e-6);
  CHECK(p.init_iterations == 0);
}

TEST_CASE("macro params: errors", "[macro]") {
  CHECK_THROWS_WITH(parse_macro_params(parse_case_toml(kBase + "\n[macro]\nadvectoin = \"upwind1\"\n")),
                    ContainsSubstring("advectoin"));
  CHECK_THROWS_WITH(parse_macro_params(parse_case_toml(kBase + "\n[macro]\nrelaxation = 1.5\n")),
                    ContainsSubstring("macro.relaxation"));
}
