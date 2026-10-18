#include "macro/macro_params.hpp"

#include <format>

#include "core/error.hpp"

namespace gsis {

MacroParams parse_macro_params(const CaseConfig& config) {
  const ModuleSection& s = config.module_section("macro");
  MacroParams p;
  p.advection = s.string("advection", p.advection);
  p.linear_solver = s.string("linear_solver", p.linear_solver);
  p.preconditioner = s.string("preconditioner", p.preconditioner);
  p.linear_rtol = s.number("linear_rtol", p.linear_rtol);
  p.linear_max_iterations = s.integer("linear_max_iterations", p.linear_max_iterations);
  p.gmres_restart = s.integer("gmres_restart", p.gmres_restart);
  p.pbc_tolerance = s.number("pbc_tolerance", p.pbc_tolerance);
  p.relaxation = s.number("relaxation", p.relaxation);
  p.init_iterations = s.integer("init_iterations", p.init_iterations);
  p.init_advection = s.string("init_advection", p.init_advection);
  p.limiter_k = s.number("limiter_k", p.limiter_k);
  p.lsq_weight_power = s.number("lsq_weight_power", p.lsq_weight_power);
  p.max_relative_decrease = s.number("max_relative_decrease", p.max_relative_decrease);
  p.pressure_floor = s.number("pressure_floor", p.pressure_floor);
  p.temperature_floor = s.number("temperature_floor", p.temperature_floor);
  p.reject_linear_residual = s.number("reject_linear_residual", p.reject_linear_residual);
  p.standalone_face_state = s.string("standalone_face_state", p.standalone_face_state);
  p.boundary_reconstruction = s.string("boundary_reconstruction", p.boundary_reconstruction);
  p.standalone_boundary_reconstruction =
      s.string("standalone_boundary_reconstruction", p.standalone_boundary_reconstruction);
  p.boundary_jacobian = s.string("boundary_jacobian", p.boundary_jacobian);
  p.hot_interpolation = s.string("hot_interpolation", p.hot_interpolation);
  p.gsis_face_state = s.string("gsis_face_state", p.gsis_face_state);
  p.gsis_continuity_pseudo_time = s.boolean("gsis_continuity_pseudo_time", p.gsis_continuity_pseudo_time);
  p.gsis_freeze_kn = s.number("gsis_freeze_kn", p.gsis_freeze_kn);
  p.gsis_cfl_start = s.number("gsis_cfl_start", p.gsis_cfl_start);
  p.gsis_cfl_growth = s.number("gsis_cfl_growth", p.gsis_cfl_growth);
  p.gsis_freeze_tmin = s.number("gsis_freeze_tmin", p.gsis_freeze_tmin);
  p.gsis_hot_kn = s.number("gsis_hot_kn", p.gsis_hot_kn);
  s.finish();
  validate_macro_params(p);
  return p;
}

void validate_macro_params(const MacroParams& p) {
  GSIS_CHECK(p.advection == "upwind1" || p.advection == "upwind2",
             std::format("macro.advection は \"upwind1\" か \"upwind2\" です(値: \"{}\")", p.advection));
  GSIS_CHECK(p.init_advection == "upwind1" || p.init_advection == "upwind2",
             std::format("macro.init_advection は \"upwind1\" か \"upwind2\" です(値: \"{}\")", p.init_advection));
  GSIS_CHECK(p.linear_solver == "bicgstab" || p.linear_solver == "gmres",
             std::format("macro.linear_solver は \"bicgstab\" か \"gmres\" です(値: \"{}\")", p.linear_solver));
  GSIS_CHECK(p.preconditioner == "ilu0" || p.preconditioner == "amg" || p.preconditioner == "cpr",
             std::format("macro.preconditioner は \"ilu0\"・\"amg\"・\"cpr\" のどれかです(値: \"{}\")",
                         p.preconditioner));
  GSIS_CHECK(p.linear_rtol > 0.0 && p.linear_rtol < 1.0, "macro.linear_rtol は 0 と 1 の間です");
  GSIS_CHECK(p.linear_max_iterations >= 1, "macro.linear_max_iterations は 1 以上です");
  GSIS_CHECK(p.gmres_restart >= 1 && p.gmres_restart <= 1000, "macro.gmres_restart は 1 以上 1000 以下です");
  GSIS_CHECK(p.pbc_tolerance > 0.0, "macro.pbc_tolerance は正です");
  GSIS_CHECK(p.relaxation > 0.0 && p.relaxation <= 1.0, "macro.relaxation は (0, 1] です");
  GSIS_CHECK(p.init_iterations >= 0, "macro.init_iterations は 0 以上です");
  GSIS_CHECK(p.limiter_k >= 0.0, "macro.limiter_k は 0 以上です");
  GSIS_CHECK(p.lsq_weight_power >= 0.0, "macro.lsq_weight_power は 0 以上です");
  GSIS_CHECK(p.max_relative_decrease > 0.0 && p.max_relative_decrease < 1.0,
             "macro.max_relative_decrease は (0, 1) です");
  GSIS_CHECK(p.pressure_floor >= 0.0 && p.pressure_floor < 1.0, "macro.pressure_floor は [0, 1) です");
  GSIS_CHECK(p.temperature_floor >= 0.0 && p.temperature_floor < 1.0, "macro.temperature_floor は [0, 1) です");
  GSIS_CHECK(p.reject_linear_residual > 0.0, "macro.reject_linear_residual は正です");
  GSIS_CHECK(p.standalone_face_state == "moments" || p.standalone_face_state == "cell" ||
                 p.standalone_face_state == "wall_moments",
             std::format("macro.standalone_face_state は \"moments\"・\"wall_moments\"・\"cell\" のどれかです(値: \"{}\")",
                         p.standalone_face_state));
  for (const std::string* v : {&p.boundary_reconstruction, &p.standalone_boundary_reconstruction}) {
    GSIS_CHECK(*v == "limited" || *v == "walls" || *v == "cell",
               std::format("macro.boundary_reconstruction・standalone_boundary_reconstruction は \"limited\"・"
                           "\"walls\"・\"cell\" のどれかです(値: \"{}\")",
                           *v));
  }
  GSIS_CHECK(p.boundary_jacobian == "cell" || p.boundary_jacobian == "reconstruction",
             std::format("macro.boundary_jacobian は \"cell\" か \"reconstruction\" です(値: \"{}\")",
                         p.boundary_jacobian));
  GSIS_CHECK(p.hot_interpolation == "cell" || p.hot_interpolation == "face",
             std::format("macro.hot_interpolation は \"cell\" か \"face\" です(値: \"{}\")", p.hot_interpolation));
  GSIS_CHECK(p.gsis_freeze_kn >= 0.0, "macro.gsis_freeze_kn は 0 以上です");
  GSIS_CHECK(p.gsis_cfl_start >= 0.0, "macro.gsis_cfl_start は 0 以上です");
  GSIS_CHECK(p.gsis_cfl_growth >= 1.0, "macro.gsis_cfl_growth は 1 以上です");
  GSIS_CHECK(p.gsis_freeze_tmin >= 0.0, "macro.gsis_freeze_tmin は 0 以上です");
  GSIS_CHECK(p.gsis_hot_kn >= 0.0, "macro.gsis_hot_kn は 0 以上です");
  GSIS_CHECK(p.gsis_face_state == "kinetic" || p.gsis_face_state == "walls" || p.gsis_face_state == "cell",
             std::format("macro.gsis_face_state は \"kinetic\"・\"walls\"・\"cell\" のどれかです(値: \"{}\")",
                         p.gsis_face_state));
}

}  // namespace gsis
