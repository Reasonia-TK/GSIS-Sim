#include "kinetic/kinetic_params.hpp"

#include <format>

#include "core/error.hpp"

namespace gsis {

KineticParams parse_kinetic_params(const CaseConfig& config) {
  const ModuleSection& s = config.module_section("kinetic");
  KineticParams p;
  p.pseudo_cfl = s.number("pseudo_cfl", p.pseudo_cfl);
  p.pseudo_time = s.string("pseudo_time", p.pseudo_time);
  p.limiter_k = s.number("limiter_k", p.limiter_k);
  p.limiter = s.string("limiter", p.limiter);
  p.limiter_eps = s.string("limiter_eps", p.limiter_eps);
  p.lsq_weight_power = s.number("lsq_weight_power", p.lsq_weight_power);
  p.positivity_limiter = s.boolean("positivity_limiter", p.positivity_limiter);
  p.velocity_chunk = s.integer("velocity_chunk", p.velocity_chunk);
  p.sweep_blocks = s.integer("sweep_blocks", p.sweep_blocks);
  p.cell_ordering = s.string("cell_ordering", p.cell_ordering);
  p.precision = s.string("precision", p.precision);
  p.backend = s.string("backend", p.backend);
  p.max_iterations = s.integer("max_iterations", p.max_iterations);
  p.initial_pressure = s.number("initial_pressure", p.initial_pressure);
  p.initial_temperature = s.number("initial_temperature", p.initial_temperature);
  p.convergence_u_floor = s.number("convergence_u_floor", p.convergence_u_floor);
  p.wall_relaxation = s.number("wall_relaxation", p.wall_relaxation);
  p.boundary_iterations = s.integer("boundary_iterations", p.boundary_iterations);
  p.interface_damping = s.number("interface_damping", p.interface_damping);
  p.gsis_negative_check = s.string("gsis_negative_check", p.gsis_negative_check);
  p.launch_cells = s.integer("launch_cells", p.launch_cells);
  p.sweep_cells_per_launch = s.integer("sweep_cells_per_launch", p.sweep_cells_per_launch);
  p.inner_sweeps = s.integer("inner_sweeps", p.inner_sweeps);
  p.inner_wall_update = s.boolean("inner_wall_update", p.inner_wall_update);
  p.block_passes = s.integer("block_passes", p.block_passes);
  p.sweep_order = s.string("sweep_order", p.sweep_order);
  p.equilibrium = s.string("equilibrium", p.equilibrium);
  p.deq_fallback = s.string("deq_fallback", p.deq_fallback);
  p.pressure_boundary_reconstruction = s.string("pressure_boundary_reconstruction", p.pressure_boundary_reconstruction);
  p.cold_bgk_temperature = s.number("cold_bgk_temperature", p.cold_bgk_temperature);
  s.finish();

  GSIS_CHECK(p.pseudo_cfl >= 0.0, std::format("kinetic.pseudo_cfl は 0 以上です(値: {})", p.pseudo_cfl));
  GSIS_CHECK(p.pseudo_time == "global" || p.pseudo_time == "velocity",
             std::format("kinetic.pseudo_time は \"global\" か \"velocity\" です(値: \"{}\")", p.pseudo_time));
  GSIS_CHECK(p.limiter_k >= 0.0, std::format("kinetic.limiter_k は 0 以上です(値: {})", p.limiter_k));
  GSIS_CHECK(p.limiter == "venkatakrishnan" || p.limiter == "none",
             std::format("kinetic.limiter は \"venkatakrishnan\" か \"none\" です(値: \"{}\")", p.limiter));
  GSIS_CHECK(p.limiter_eps == "absolute" || p.limiter_eps == "relative",
             std::format("kinetic.limiter_eps は \"absolute\" か \"relative\" です(値: \"{}\")", p.limiter_eps));
  GSIS_CHECK(p.lsq_weight_power >= 0.0, "kinetic.lsq_weight_power は 0 以上です");
  GSIS_CHECK(p.velocity_chunk >= 0, "kinetic.velocity_chunk は 0 以上です");
  GSIS_CHECK(p.sweep_blocks >= 0, "kinetic.sweep_blocks は 0(自動)以上です");
  GSIS_CHECK(p.cell_ordering == "rcb" || p.cell_ordering == "rcm" || p.cell_ordering == "none",
             std::format("kinetic.cell_ordering は \"rcb\"・\"rcm\"・\"none\" のどれかです(値: \"{}\")",
                         p.cell_ordering));
  GSIS_CHECK(p.precision == "fp32" || p.precision == "fp64",
             std::format("kinetic.precision は \"fp32\" か \"fp64\" です(値: \"{}\")", p.precision));
  GSIS_CHECK(p.backend == "cuda" || p.backend == "cpu",
             std::format("kinetic.backend は \"cuda\" か \"cpu\" です(値: \"{}\")", p.backend));
  GSIS_CHECK(p.max_iterations >= 1, "kinetic.max_iterations は 1 以上です");
  GSIS_CHECK(p.initial_pressure > 0.0, "kinetic.initial_pressure は正です");
  GSIS_CHECK(p.initial_temperature > 0.0, "kinetic.initial_temperature は正です");
  GSIS_CHECK(p.convergence_u_floor >= 0.0, "kinetic.convergence_u_floor は 0 以上です");
  GSIS_CHECK(p.wall_relaxation > 0.0 && p.wall_relaxation <= 1.0, "kinetic.wall_relaxation は (0, 1] です");
  GSIS_CHECK(p.boundary_iterations >= 1 && p.boundary_iterations <= 1000,
             "kinetic.boundary_iterations は 1〜1000 です");
  GSIS_CHECK(p.interface_damping >= 0.0, "kinetic.interface_damping は 0 以上です");
  GSIS_CHECK(p.gsis_negative_check == "all" || p.gsis_negative_check == "created" || p.gsis_negative_check == "limit" ||
                 p.gsis_negative_check == "blend",
             std::format("kinetic.gsis_negative_check は \"all\"・\"created\"・\"limit\"・\"blend\" のどれかです(値: \"{}\")",
                         p.gsis_negative_check));
  GSIS_CHECK(p.launch_cells >= 1, "kinetic.launch_cells は 1 以上です");
  GSIS_CHECK(p.sweep_cells_per_launch >= 1, "kinetic.sweep_cells_per_launch は 1 以上です");
  GSIS_CHECK(p.inner_sweeps >= 1 && p.inner_sweeps <= 1000,
             std::format("kinetic.inner_sweeps は 1〜1000 です(値: {})", p.inner_sweeps));
  GSIS_CHECK(p.block_passes >= 1 && p.block_passes <= 100,
             std::format("kinetic.block_passes は 1〜100 です(値: {})", p.block_passes));
  GSIS_CHECK(p.sweep_order == "block" || p.sweep_order == "octant" || p.sweep_order == "dir13",
             std::format("kinetic.sweep_order は \"block\"・\"octant\"・\"dir13\" のどれかです(値: \"{}\")",
                         p.sweep_order));
  GSIS_CHECK(p.equilibrium == "continuous" || p.equilibrium == "discrete",
             std::format("kinetic.equilibrium は \"continuous\" か \"discrete\" です(値: \"{}\")", p.equilibrium));
  GSIS_CHECK(p.deq_fallback == "collisionless" || p.deq_fallback == "continuous",
             std::format("kinetic.deq_fallback は \"collisionless\" か \"continuous\" です(値: \"{}\")", p.deq_fallback));
  GSIS_CHECK(p.pressure_boundary_reconstruction == "limited" || p.pressure_boundary_reconstruction == "cell",
             std::format("kinetic.pressure_boundary_reconstruction は \"limited\" か \"cell\" です(値: \"{}\")",
                         p.pressure_boundary_reconstruction));
  GSIS_CHECK(p.cold_bgk_temperature >= 0.0, "kinetic.cold_bgk_temperature は 0 以上です");
  return p;
}

}  // namespace gsis
