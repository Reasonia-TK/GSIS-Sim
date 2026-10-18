#include "gsis_test_cases.hpp"

#include <format>

#include "kinetic/kinetic_params.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "macro/macro_params.hpp"

namespace gsis::test {

SlabProblem make_slab_problem(int nx, double Kn, double T_left, double T_right, double u_wall,
                              const std::string& kinetic, const std::string& extra) {
  SlabProblem p;
  p.mesh = make_kinetic_box({nx, 1, 1}, {0.0, 0.0, 0.0}, {1.0, 1.0 / nx, 1.0 / nx},
                            {"wall_left", "wall_right", "side_y", "side_y", "side_z", "side_z"});
  KineticCaseOptions o;
  o.Kn = Kn;
  o.nv = {16, 12, 12};  // ξ ∈ [−5.5, 5.5]³: 離散 Maxwell 分布のモーメントの誤差を 1e-9 程度にする
  o.vmax = 5.5;
  o.kinetic = kinetic;
  o.extra = extra;
  KineticBc left{"wall_left", "diffuse_wall", T_left};
  left.u = Vec3{0.0, -u_wall, 0.0};
  KineticBc right{"wall_right", "diffuse_wall", T_right};
  right.u = Vec3{0.0, u_wall, 0.0};
  p.config = make_kinetic_case({left, right, {"side_y", "periodic_slab"}, {"side_z", "periodic_slab"}}, o);
  return p;
}

BoxProblem make_tube_box_problem(std::array<int, 3> n, double Kn, const std::string& kinetic,
                                 const std::string& extra) {
  BoxProblem p;
  p.mesh = make_kinetic_box(n, {0.0, 0.0, 0.0}, {2.0, 1.0, 1.0}, {"inlet", "outlet", "wall", "wall", "wall", "wall"},
                            0.15);
  KineticCaseOptions o;
  o.Kn = Kn;
  o.nv = {12, 12, 12};
  o.vmax = 6.0;
  o.kinetic = kinetic;
  o.extra = extra;
  p.config = make_kinetic_case(
      {{"inlet", "pressure_inlet", 1.0, 1.0}, {"outlet", "vacuum"}, {"wall", "diffuse_wall", 1.0}}, o);
  return p;
}

SolveResult solve_cis(const CaseConfig& cfg, const LoadedMesh& lm, double tol, long long max_iter) {
  KineticSolver k(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
  k.initialize_uniform(1.0, Vec3{}, 1.0);
  SolveResult r;
  for (;;) {
    const KineticIterationInfo& p = k.prepare();
    r.epsilon = p.has_epsilon ? p.epsilon : 1.0;
    if (p.iteration >= 2 && p.has_epsilon && p.epsilon < tol) {
      r.converged = true;
      break;
    }
    if (p.iteration >= max_iter) break;
    k.sweep();
  }
  r.iterations = k.iteration();
  r.cis_iterations = r.iterations;
  r.moments = k.moments();
  r.state = r.moments.state;
  return r;
}

SolveResult solve_gsis(const CaseConfig& cfg, const LoadedMesh& lm, double tol, long long max_iter) {
  const KineticParams kp = parse_kinetic_params(cfg);
  const MacroParams mp = parse_macro_params(cfg);
  GsisSolver g(cfg, lm.mesh, lm.geometry, kp, mp, GsisOptions::from_case(cfg, kp, mp));
  g.initialize();
  SolveResult r;
  for (;;) {
    const GsisIterationInfo& it = g.iterate();
    r.epsilon = it.change.epsilon;
    if (it.iteration >= 2 && it.change.epsilon < tol) {
      r.converged = true;
      break;
    }
    if (it.iteration >= max_iter) break;
  }
  r.iterations = g.iteration();
  r.cis_iterations = g.cis_iterations();
  r.moments = g.kinetic().moments();
  r.state = g.state();
  return r;
}

double volume_mean(const std::vector<double>& v, const LoadedMesh& lm) {
  double s = 0.0;
  double vol = 0.0;
  for (std::size_t c = 0; c < v.size(); ++c) {
    s += v[c] * lm.geometry.cell_volume[c];
    vol += lm.geometry.cell_volume[c];
  }
  return s / vol;
}

}  // namespace gsis::test
