// kinetic: [kinetic] の読み込み

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <string>

#include "config/case_config.hpp"
#include "core/error.hpp"
#include "kinetic/kinetic_params.hpp"

using namespace gsis;
using Catch::Matchers::ContainsSubstring;

namespace {

const std::string kBase = R"(
[case]
name = "kinetic_params"
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

TEST_CASE("kinetic params: defaults and values", "[kinetic]") {
  const KineticParams d = parse_kinetic_params(parse_case_toml(kBase));
  CHECK(d.pseudo_cfl == 0.0);
  CHECK(d.sweep_blocks == 0);  // 自動
  CHECK(d.precision == "fp32");

  const KineticParams p = parse_kinetic_params(parse_case_toml(
      kBase + "\n[kinetic]\npseudo_cfl = 50\nsweep_blocks = 8\nprecision = \"fp64\"\nbackend = \"cpu\"\n"));
  CHECK(p.pseudo_cfl == 50.0);
  CHECK(p.sweep_blocks == 8);
  CHECK(p.precision == "fp64");
  CHECK(p.backend == "cpu");

  const KineticParams q = parse_kinetic_params(parse_case_toml(
      kBase + "\n[kinetic]\nlimiter = \"none\"\nlimiter_eps = \"relative\"\ninitial_pressure = 0.5\n"
              "initial_temperature = 1.2\nconvergence_u_floor = 0.01\nlaunch_cells = 100\n"
              "sweep_cells_per_launch = 10\n"));
  CHECK(q.limiter == "none");
  CHECK(q.limiter_eps == "relative");
  CHECK(q.initial_pressure == 0.5);
  CHECK(q.initial_temperature == 1.2);
  CHECK(q.convergence_u_floor == 0.01);
  CHECK(q.launch_cells == 100);
  CHECK(q.sweep_cells_per_launch == 10);
}

TEST_CASE("kinetic params: errors", "[kinetic]") {
  CHECK_THROWS_WITH(parse_kinetic_params(parse_case_toml(kBase + "\n[kinetic]\nsweep_block = 8\n")),
                    ContainsSubstring("sweep_block"));
  CHECK_THROWS_WITH(parse_kinetic_params(parse_case_toml(kBase + "\n[kinetic]\nprecision = \"fp16\"\n")),
                    ContainsSubstring("kinetic.precision"));
  CHECK_THROWS_WITH(parse_kinetic_params(parse_case_toml(kBase + "\n[kinetic]\nlimiter = \"minmod\"\n")),
                    ContainsSubstring("kinetic.limiter"));
  CHECK_THROWS_WITH(parse_kinetic_params(parse_case_toml(kBase + "\n[kinetic]\ninitial_pressure = 0\n")),
                    ContainsSubstring("initial_pressure"));
}
