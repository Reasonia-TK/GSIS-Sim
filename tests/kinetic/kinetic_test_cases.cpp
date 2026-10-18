#include "kinetic_test_cases.hpp"

#include <cmath>
#include <format>

namespace gsis::test {
namespace {

std::string base_toml(const KineticCaseOptions& o) {
  return std::format(R"(
[case]
name = "kinetic_test"
mesh = "unused.pmesh"
mode = "cis"

[gas]
molecular_mass = 6.65e-27
mu_ref = 1.916e-5
T_ref = 273.15
omega = 0.66

[conditions]
L_ref = 1.0
T0 = 273.15
Kn = {}

[velocity_grid]
n = [{}, {}, {}]
range = [[{}, {}], [{}, {}], [{}, {}]]
)",
                     o.Kn, o.nv[0], o.nv[1], o.nv[2], -o.vmax, o.vmax, -o.vmax, o.vmax, -o.vmax, o.vmax);
}

}  // namespace

CaseConfig make_kinetic_case(const std::vector<KineticBc>& bcs, const KineticCaseOptions& options) {
  // 基準量を知るため、仮の境界で一度読む
  const CaseConfig probe = parse_case_toml(base_toml(options) +
                                           "\n[[boundary]]\npatch = \"x\"\ntype = \"vacuum\"\n");
  const DerivedQuantities d = derive_quantities(probe);
  std::string text = base_toml(options);
  for (const KineticBc& b : bcs) {
    text += std::format("\n[[boundary]]\npatch = \"{}\"\ntype = \"{}\"\n", b.patch, b.type);
    if (b.type == "diffuse_wall") {
      text += std::format("temperature = {:.17g}\n", b.T * d.ref.temperature);
      if (norm(b.u) > 0.0) {
        text += std::format("velocity = [{:.17g}, {:.17g}, {:.17g}]\n", b.u.x * d.ref.velocity,
                            b.u.y * d.ref.velocity, b.u.z * d.ref.velocity);
      }
    } else if (b.type == "pressure_inlet") {
      text += std::format("temperature = {:.17g}\npressure = {:.17g}\n", b.T * d.ref.temperature,
                          b.p * d.ref.pressure);
    } else if (b.type == "pressure_outlet") {
      text += std::format("pressure = {:.17g}\n", b.p * d.ref.pressure);
    }
  }
  if (!options.kinetic.empty()) text += "\n[kinetic]\n" + options.kinetic + "\n";
  if (!options.extra.empty()) text += "\n" + options.extra + "\n";
  return parse_case_toml(text);
}

LoadedMesh make_kinetic_box(std::array<int, 3> n, Vec3 lo, Vec3 hi, std::array<std::string, 6> patch_names,
                            double distortion, bool tetrahedra, unsigned seed) {
  BoxMeshOptions o;
  o.n = n;
  o.lo = lo;
  o.hi = hi;
  o.patch_names = patch_names;
  o.distortion = distortion;
  o.tetrahedra = tetrahedra;
  o.seed = seed;
  return validate_mesh(make_box_mesh(o));
}

}  // namespace gsis::test
