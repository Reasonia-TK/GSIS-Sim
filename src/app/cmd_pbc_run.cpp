// gsis-solver pbc-run: PBC マクロソルバーだけ(NSF のみの単独モード)で、一様な状態から定常解を求める。
// (docs/design_pbc.md §9)
//
//   pbc-run <case.toml> [--out <dir>] [--iterations N] [--cfl X] [--cfl-start X] [--cfl-growth G]
//           [--tolerance X] [--advection upwind1|upwind2] [--threads N] [--print-every N] [--no-vtu]
//           [--cells-csv] [--restart <cells.csv>]
//
// - 初期状態は一様(p = p_ref、u = 0、T = T0)。境界は運動論的なフラックス(整合補正 0、HoT = 0)。
// - CFL の既定は [gsis].cfl_pbc、収束判定の既定は [macro].pbc_tolerance(更新量 ‖ΔW‖/‖W‖)。
//   一様な状態から始めるので、CFL は --cfl-start(既定 5)から残差の減り方に応じて目標の CFL まで上げる
//   (SER。1 反復で --cfl-growth 倍(既定 2)まで。--cfl-start 0 で立ち上げなし)。
// - 出力(既定は runs/macro/<ケース名>/。カレントディレクトリから):
//     history.csv   反復ごとの残差・更新量・線形ソルバーの反復数・パッチごとの質量流量
//     cells.vtu     セル値(SI): p, rho, U, T, Ma, Kn_local
//     boundary.vtu  境界面の値(SI): 質量流束、運動量流束、エネルギー流束、境界面の状態
//     summary.json  要約(反復数、時間、パッチごとの質量流量、delta_rp があれば無次元質量流量 Ṁ)
//     cells.csv     (--cells-csv のとき)セル中心の座標(L_ref 単位)と無次元の p, u, T, ρ

#include <algorithm>
#include <charconv>
#include <cmath>
#include <filesystem>
#include <format>
#include <span>
#include <string>
#include <vector>

#include "app/cli.hpp"
#include "config/case_config.hpp"
#include "core/build_info.hpp"
#include "core/error.hpp"
#include "core/json.hpp"
#include "core/log.hpp"
#include "core/paths.hpp"
#include "core/timer.hpp"
#include "io/csv_writer.hpp"
#include "io/vtu_writer.hpp"
#include "macro/linear_solver.hpp"
#include "macro/macro_params.hpp"
#include "macro/pbc_solver.hpp"
#include "mesh/pmesh_io.hpp"

namespace gsis::cli {
namespace {

double parse_number(const std::string& opt, const std::string& s) {
  double v = 0.0;
  const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
  if (ec != std::errc() || ptr != s.data() + s.size() || !std::isfinite(v)) {
    throw UsageError{std::format("{} の値 \"{}\" は数値ではありません", opt, s)};
  }
  return v;
}

long long parse_integer(const std::string& opt, const std::string& s) {
  long long v = 0;
  const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
  if (ec != std::errc() || ptr != s.data() + s.size()) {
    throw UsageError{std::format("{} の値 \"{}\" は整数ではありません", opt, s)};
  }
  return v;
}

// --cells-csv で書いた cells.csv から無次元の p, u, T を読む(セルの並びはメッシュと同じであること)
void read_cells_csv(const std::filesystem::path& path, CellMacroState& state) {
  const std::string text = read_file(path);
  std::size_t pos = text.find('\n');
  GSIS_CHECK(pos != std::string::npos, "cells.csv に見出し行がありません");
  GSIS_CHECK(text.substr(0, pos) == "cell,x,y,z,volume,p,ux,uy,uz,T,rho",
             "cells.csv の見出しが pbc-run --cells-csv の形式ではありません");
  std::size_t n = 0;
  while (pos + 1 < text.size()) {
    const std::size_t end = std::min(text.find('\n', pos + 1), text.size());
    const std::string line = text.substr(pos + 1, end - pos - 1);
    pos = end;
    if (line.empty()) continue;
    double v[11];
    std::size_t start = 0;
    for (int k = 0; k < 11; ++k) {
      const std::size_t comma = k < 10 ? line.find(',', start) : line.size();
      GSIS_CHECK(comma != std::string::npos, "cells.csv の行の欄が足りません");
      const std::string field = line.substr(start, comma - start);
      const auto [ptr, ec] = std::from_chars(field.data(), field.data() + field.size(), v[k]);
      GSIS_CHECK(ec == std::errc(), std::format("cells.csv の値 \"{}\" が読めません", field));
      start = comma + 1;
    }
    const auto c = static_cast<std::size_t>(v[0]);
    GSIS_CHECK(c == n && c < state.size(), "cells.csv のセルの並びがメッシュと合いません");
    state.p[c] = v[5];
    state.ux[c] = v[6];
    state.uy[c] = v[7];
    state.uz[c] = v[8];
    state.T[c] = v[9];
    state.rho[c] = v[5] / v[9];
    ++n;
  }
  GSIS_CHECK(n == state.size(), std::format("cells.csv のセル数 {} がメッシュのセル数 {} と違います", n, state.size()));
}

struct Options {
  std::string case_file;
  std::string out_dir;
  long long iterations = 2000;
  double cfl = -1.0;
  double cfl_start = 5.0;
  double cfl_growth = 2.0;
  double tolerance = -1.0;
  std::string advection;
  int threads = 0;
  long long print_every = 10;
  bool vtu = true;
  bool cells_csv = false;
  bool continuity_pseudo_time = false;
  std::string restart;  // 初期状態を読む cells.csv(--cells-csv で書いたもの)
};

Options parse_options(std::span<const std::string> args) {
  Options o;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string& a = args[i];
    auto value = [&]() -> const std::string& {
      if (i + 1 >= args.size()) throw UsageError{a + " の後に値が必要です"};
      return args[++i];
    };
    if (a == "--out") {
      o.out_dir = value();
    } else if (a == "--iterations") {
      o.iterations = parse_integer(a, value());
      if (o.iterations < 1) throw UsageError{"--iterations は 1 以上にしてください"};
    } else if (a == "--cfl") {
      o.cfl = parse_number(a, value());
      if (!(o.cfl > 0.0)) throw UsageError{"--cfl は正にしてください"};
    } else if (a == "--cfl-start") {
      o.cfl_start = parse_number(a, value());
      if (o.cfl_start < 0.0) throw UsageError{"--cfl-start は 0 以上にしてください(0 なら立ち上げなし)"};
    } else if (a == "--cfl-growth") {
      o.cfl_growth = parse_number(a, value());
      if (!(o.cfl_growth >= 1.0)) throw UsageError{"--cfl-growth は 1 以上にしてください"};
    } else if (a == "--tolerance") {
      o.tolerance = parse_number(a, value());
      if (!(o.tolerance > 0.0)) throw UsageError{"--tolerance は正にしてください"};
    } else if (a == "--advection") {
      o.advection = value();
      if (o.advection != "upwind1" && o.advection != "upwind2") {
        throw UsageError{"--advection は upwind1 か upwind2 です"};
      }
    } else if (a == "--threads") {
      o.threads = static_cast<int>(parse_integer(a, value()));
      if (o.threads < 1) throw UsageError{"--threads は 1 以上にしてください"};
    } else if (a == "--print-every") {
      o.print_every = parse_integer(a, value());
      if (o.print_every < 1) throw UsageError{"--print-every は 1 以上にしてください"};
    } else if (a == "--no-vtu") {
      o.vtu = false;
    } else if (a == "--continuity-pseudo-time") {
      o.continuity_pseudo_time = true;
    } else if (a == "--cells-csv") {
      o.cells_csv = true;
    } else if (a == "--restart") {
      o.restart = value();
    } else if (!a.empty() && a[0] == '-') {
      throw UsageError{"知らないオプション " + a + " です"};
    } else if (o.case_file.empty()) {
      o.case_file = a;
    } else {
      throw UsageError{"ケース定義(TOML)は 1 つだけ指定してください"};
    }
  }
  if (o.case_file.empty()) throw UsageError{"ケース定義(TOML)を指定してください"};
  return o;
}

}  // namespace

int cmd_pbc_run(std::span<const std::string> args) {
  const Options opt = parse_options(args);
  Timer total_timer;
  const CaseConfig cfg = load_case(path_from_utf8(opt.case_file));
  const DerivedQuantities d = derive_quantities(cfg);
  MacroParams params = parse_macro_params(cfg);
  if (!opt.advection.empty()) params.advection = opt.advection;
  if (opt.threads > 0) macro::set_openmp_threads(opt.threads);
  const double cfl = opt.cfl > 0.0 ? opt.cfl : cfg.gsis.cfl_pbc;
  const double tolerance = opt.tolerance > 0.0 ? opt.tolerance : params.pbc_tolerance;
  const std::filesystem::path out_dir =
      opt.out_dir.empty() ? std::filesystem::path("runs") / "macro" / path_from_utf8(cfg.case_info.name)
                          : path_from_utf8(opt.out_dir);
  std::filesystem::create_directories(out_dir);
  clear_stop_request(out_dir);

  Timer load_timer;
  const LoadedMesh lm = load_pmesh(cfg.mesh_path());
  const PolyMesh& mesh = lm.mesh;
  PbcSolver solver = PbcSolver::from_case(cfg, d, mesh, lm.geometry, params);
  const double t_setup = load_timer.seconds();

  print_heading("PBC マクロソルバー(単独モード: NSF のみ、整合補正 0)");
  print_item("ケース", std::format("{}({})", cfg.case_info.name, opt.case_file));
  print_item("メッシュ", std::format("{}(セル {}、内部面 {}、境界面 {})", path_to_utf8(cfg.mesh_path()),
                                     format_count(mesh.n_cells), format_count(mesh.n_internal_faces()),
                                     format_count(mesh.n_boundary_faces())));
  print_item("Kn(基準状態)、δ", std::format("{:.4g}、{:.6g}", d.Kn, d.delta_from_Kn));
  print_item("移流項", params.advection);
  print_item("線形ソルバー", std::format("{} + {}(相対許容誤差 {:.3g}、上限 {} 回)", params.linear_solver,
                                         params.preconditioner, params.linear_rtol, params.linear_max_iterations));
  print_item("CFL・反復の上限・収束判定", std::format("{:.6g}・{}・{:.3g}", cfl, opt.iterations, tolerance));
  print_item("CFL の立ち上げ", opt.cfl_start > 0.0 && opt.cfl_start < cfl
                                  ? std::format("{:.6g} から残差に応じて(1 反復で {:.6g} 倍まで)", opt.cfl_start, opt.cfl_growth)
                                  : std::string("なし"));
  print_item("境界面の状態・勾配の境界値", std::format("{}・{}(ヤコビアン {})", params.standalone_boundary_reconstruction,
                                                    params.standalone_face_state, params.boundary_jacobian));
  print_item("OpenMP のスレッド数", std::format("{}", macro::openmp_max_threads()));
  print_item("閉じた領域", solver.closed_domain() ? "はい(全質量を初期値に保つ)" : "いいえ");
  print_item("出力先", path_to_utf8(out_dir));

  // 履歴
  std::vector<std::string> header = {"iteration",   "cfl", "residual_mass", "residual_momentum", "residual_energy",
                                     "kinetic_energy_defect", "residual_temperature", "update_p", "update_u",
                                     "update_T",
                                     "linear_iterations_pu", "linear_residual_pu", "linear_iterations_T",
                                     "linear_residual_T", "clipped_p", "clipped_T", "rejected"};
  for (const Patch& p : mesh.patches) header.push_back("mdot_" + p.name);
  header.push_back("seconds");
  header.push_back("elapsed_seconds");
  CsvWriter history(out_dir / "history.csv", header);
  history.set_autoflush(true);
  Timer run_timer;
  auto on_iteration = [&](const PbcIterationInfo& it) {
    std::vector<CsvValue> row = {static_cast<std::int64_t>(it.iteration + 1),
                                 it.cfl,
                                 it.residual_mass,
                                 it.residual_momentum,
                                 it.residual_energy,
                                 it.kinetic_energy_defect,
                                 it.residual_temperature,
                                 it.update_p,
                                 it.update_u,
                                 it.update_T,
                                 static_cast<std::int64_t>(it.linear_iterations_pu),
                                 it.linear_residual_pu,
                                 static_cast<std::int64_t>(it.linear_iterations_T),
                                 it.linear_residual_T,
                                 static_cast<std::int64_t>(it.clipped_p),
                                 static_cast<std::int64_t>(it.clipped_T),
                                 static_cast<std::int64_t>(it.rejected ? 1 : 0)};
    for (double m : it.patch_mass_flow) row.emplace_back(m);
    row.emplace_back(it.seconds);
    row.emplace_back(run_timer.seconds());
    history.write_row(std::span<const CsvValue>(row));
    if ((it.iteration + 1) % opt.print_every == 0 || it.iteration == 0) {
      log_info("反復 {:5d}: CFL {:.3g}、残差(質量 {:.3e}、運動量 {:.3e}、エネルギー {:.3e})、更新量 {:.3e}、"
               "線形 {}/{} 回、{:.2f} s",
               it.iteration + 1, it.cfl, it.residual_mass, it.residual_momentum, it.residual_energy,
               it.update_max(), it.linear_iterations_pu, it.linear_iterations_T, run_timer.seconds());
    }
  };

  CellMacroState state;
  state.resize(static_cast<std::size_t>(mesh.n_cells));
  std::fill(state.p.begin(), state.p.end(), 1.0);
  std::fill(state.T.begin(), state.T.end(), 1.0);
  std::fill(state.rho.begin(), state.rho.end(), 1.0);
  if (!opt.restart.empty()) read_cells_csv(path_from_utf8(opt.restart), state);
  PbcStageOptions stage;
  stage.max_iterations = static_cast<int>(opt.iterations);
  stage.cfl = cfl;
  stage.cfl_start = opt.cfl_start;
  stage.cfl_growth = opt.cfl_growth;
  stage.continuity_pseudo_time = opt.continuity_pseudo_time;
  stage.tolerance = tolerance;
  stage.on_iteration = on_iteration;
  // 穏やかな停止(出力先の STOP。その反復で終えて結果を書く)
  stage.should_stop = [&out_dir]() { return stop_requested(out_dir); };
  const PbcStageReport rep = solver.solve_standalone(state, stage);
  const double t_run = run_timer.seconds();

  // 最終状態での残差と境界フラックス(段の最後に評価し直した値)
  const std::vector<std::array<double, 5>>& R = solver.last_residual();
  const std::vector<double>& flow_nd = rep.final_patch_mass_flow;
  const ReferenceScales& ref = d.ref;
  const double mass_flow_si = ref.mass_flux * ref.length * ref.length;  // 無次元の質量流量 → kg/s
  double inflow = 0.0;
  double outflow = 0.0;
  for (std::size_t p = 0; p < mesh.patches.size(); ++p) {
    const macro::MacroBoundaryKind k = solver.boundary_conditions()[p].kind;
    if (k == macro::MacroBoundaryKind::PressureInlet) inflow -= flow_nd[p];
    if (k == macro::MacroBoundaryKind::PressureOutlet || k == macro::MacroBoundaryKind::Vacuum) {
      outflow += flow_nd[p];
    }
  }
  double net = 0.0;
  for (double f : flow_nd) net += f;

  print_heading("結果");
  print_item("反復", std::format("{}({})", rep.iterations,
                                 rep.converged ? "収束" : (rep.stopped ? "STOP で停止" : "上限に達した")));
  print_item("最後の更新量", std::format("{:.3e}", rep.final_update));
  print_item("最後の残差", std::format("質量 {:.3e}、運動量 {:.3e}、エネルギー(R_E − Σ𝒦){:.3e}",
                                       rep.final_residual_mass, rep.final_residual_momentum,
                                       rep.final_residual_energy));
  print_item("運動エネルギーの欠損 Σ𝒦", std::format("{:.3e}", rep.final_kinetic_energy_defect));
  print_item("線形ソルバーの反復(p, u)", std::format("合計 {}、最大 {}、許容誤差に届かず {} 回、更新を捨てた反復 {}",
                                                    rep.linear_iterations_pu, rep.max_linear_iterations_pu,
                                                    rep.linear_not_converged, rep.rejected_iterations));
  print_item("時間", std::format("準備 {:.2f} s、反復 {:.2f} s", t_setup, t_run));
  for (std::size_t p = 0; p < mesh.patches.size(); ++p) {
    print_item("質量流量 " + mesh.patches[p].name,
               std::format("{:+.6e} kg/s(無次元 {:+.6e}、外向きが正)", flow_nd[p] * mass_flow_si, flow_nd[p]));
  }
  json::Value summary;
  summary.set("case", cfg.case_info.name);
  summary.set("case_file", opt.case_file);
  summary.set("mesh", path_to_utf8(cfg.mesh_path()));
  summary.set("n_cells", static_cast<std::int64_t>(mesh.n_cells));
  summary.set("solver", build_description());
  summary.set("mode", "standalone_nsf");
  summary.set("advection", params.advection);
  summary.set("linear_solver", params.linear_solver + "+" + params.preconditioner);
  summary.set("linear_rtol", params.linear_rtol);
  summary.set("standalone_face_state", params.standalone_face_state);
  summary.set("standalone_boundary_reconstruction", params.standalone_boundary_reconstruction);
  summary.set("boundary_jacobian", params.boundary_jacobian);
  summary.set("limiter_k", params.limiter_k);
  summary.set("cfl", cfl);
  summary.set("cfl_start", opt.cfl_start);
  summary.set("cfl_growth", opt.cfl_growth);
  summary.set("tolerance", tolerance);
  summary.set("threads", static_cast<std::int64_t>(macro::openmp_max_threads()));
  summary.set("iterations", static_cast<std::int64_t>(rep.iterations));
  summary.set("converged", rep.converged);
  summary.set("stopped", rep.stopped);
  summary.set("Kn", d.Kn);
  summary.set("delta", d.delta_from_Kn);
  {
    // 無次元化の基準量(cis-run・run の summary.json と同じ形)
    json::Value r;
    r.set("length_m", ref.length);
    r.set("temperature_K", ref.temperature);
    r.set("pressure_Pa", ref.pressure);
    r.set("density_kg_m3", ref.density);
    r.set("velocity_m_s", ref.velocity);
    r.set("heat_flux_W_m2", ref.heat_flux);
    r.set("mass_flux_kg_m2_s", ref.mass_flux);
    summary.set("reference", std::move(r));
  }
  summary.set("final_update", rep.final_update);
  summary.set("initial_residual_mass", rep.initial_residual_mass);
  summary.set("final_residual_mass", rep.final_residual_mass);
  summary.set("final_residual_momentum", rep.final_residual_momentum);
  summary.set("final_residual_energy", rep.final_residual_energy);
  summary.set("final_kinetic_energy_defect", rep.final_kinetic_energy_defect);
  summary.set("linear_iterations_pu_total", static_cast<std::int64_t>(rep.linear_iterations_pu));
  summary.set("linear_iterations_T_total", static_cast<std::int64_t>(rep.linear_iterations_T));
  summary.set("linear_iterations_pu_max", static_cast<std::int64_t>(rep.max_linear_iterations_pu));
  summary.set("linear_not_converged", static_cast<std::int64_t>(rep.linear_not_converged));
  summary.set("rejected_iterations", static_cast<std::int64_t>(rep.rejected_iterations));
  summary.set("pressure_floor", params.pressure_floor);
  summary.set("temperature_floor", params.temperature_floor);
  summary.set("continuity_pseudo_time", opt.continuity_pseudo_time);
  summary.set("setup_seconds", t_setup);
  summary.set("run_seconds", t_run);
  json::Value patches;
  const std::vector<Vec5> pflux = solver.patch_flux();
  const double force_si = ref.pressure * ref.length * ref.length;   // 無次元の運動量流束 → N
  const double power_si = ref.heat_flux * ref.length * ref.length;  // 無次元のエネルギー流束 → W
  for (std::size_t p = 0; p < mesh.patches.size(); ++p) {
    json::Value e;
    e.set("name", mesh.patches[p].name);
    e.set("type", macro::to_string(solver.boundary_conditions()[p].kind));
    e.set("mass_flow_nondim", flow_nd[p]);
    e.set("mass_flow_kg_s", flow_nd[p] * mass_flow_si);
    // 外向きの運動量流束(流体がパッチに及ぼす力)とエネルギー流束(流体からパッチへ出る熱と仕事)
    json::Value force;
    for (int k = 1; k <= 3; ++k) force.push_back(pflux[p][k] * force_si);
    e.set("force_N", std::move(force));
    e.set("energy_flow_W", pflux[p][4] * power_si);
    json::Value nd;
    for (int k = 0; k < 5; ++k) nd.push_back(pflux[p][k]);
    e.set("flux_nondim", std::move(nd));
    patches.push_back(std::move(e));
  }
  summary.set("patches", std::move(patches));
  summary.set("net_mass_flow_nondim", net);
  if (d.has_p_in && (inflow != 0.0 || outflow != 0.0)) {
    const double Min = inflow * mass_flow_si / d.mass_flow_scale;
    const double Mout = outflow * mass_flow_si / d.mass_flow_scale;
    const double M = 0.5 * (Min + Mout);
    print_item("無次元質量流量 Ṁ", std::format("{:.6f}(入口 {:.6f}、出口 {:.6f}、差 {:.2e})", M, Min, Mout,
                                              (Min - Mout) / std::max(std::abs(M), 1e-300)));
    summary.set("M_dot", M);
    summary.set("M_dot_inlet", Min);
    summary.set("M_dot_outlet", Mout);
    summary.set("mass_flow_scale_kg_s", d.mass_flow_scale);
    // cis-run・run と同じ形の "mass_flow"
    json::Value mf;
    mf.set("scale_kg_s", d.mass_flow_scale);
    mf.set("M_dot_inlet", Min);
    mf.set("M_dot_outlet", Mout);
    mf.set("M_dot", M);
    summary.set("mass_flow", std::move(mf));
  }
  {
    json::Value timing;
    for (const TimingTable::Entry& e : rep.timing.entries()) timing.set(e.name, e.seconds);
    summary.set("timing_seconds", std::move(timing));
  }

  if (opt.vtu) {
    const std::size_t nc = static_cast<std::size_t>(mesh.n_cells);
    std::vector<double> p(nc), rho(nc), T(nc), Ma(nc), Kn(nc), rmass(nc);
    std::vector<Vec3> U(nc);
    const MacroGas& gas = solver.gas();
    for (std::size_t c = 0; c < nc; ++c) {
      const double Tn = state.T[c];
      const Vec3 u{state.ux[c], state.uy[c], state.uz[c]};
      p[c] = state.p[c] * ref.pressure;
      rho[c] = state.p[c] / Tn * ref.density;
      T[c] = Tn * ref.temperature;
      U[c] = u * ref.velocity;
      Ma[c] = norm(u) / std::sqrt(macro::kGamma * Tn);
      Kn[c] = gas.mu(Tn) / state.p[c] * std::sqrt(kPi * Tn / 2.0);
      rmass[c] = R[c][0];
    }
    VtuWriter w = VtuWriter::polyhedra(mesh);
    w.add_cell_scalar("p", p);
    w.add_cell_scalar("rho", rho);
    w.add_cell_vector("U", U);
    w.add_cell_scalar("T", T);
    w.add_cell_scalar("Ma", Ma);
    w.add_cell_scalar("Kn_local", Kn);
    w.add_cell_scalar("residual_mass_nondim", rmass);
    w.write(out_dir / "cells.vtu");

    const PbcBoundaryFaceData& bd = solver.last_boundary();
    const std::size_t nb = static_cast<std::size_t>(mesh.n_boundary_faces());
    const MeshGeometry& g = solver.geometry();
    std::vector<double> mflux(nb), eflux(nb), pface(nb), Tface(nb), pn(nb);
    std::vector<Vec3> mom(nb), shear(nb), Uface(nb);
    for (std::size_t b = 0; b < nb; ++b) {
      const std::size_t f = b + static_cast<std::size_t>(mesh.n_internal_faces());
      const double A = g.face_area[f];
      const Vec3& n = g.face_normal[f];
      const Vec5& F = bd.flux[b];
      const Vec3 m{F[1] / A, F[2] / A, F[3] / A};
      mflux[b] = F[0] / A * ref.mass_flux;
      eflux[b] = F[4] / A * ref.heat_flux;
      mom[b] = m * ref.pressure;
      pn[b] = dot(m, n) * ref.pressure;
      shear[b] = (m - dot(m, n) * n) * ref.pressure;
      const Vec5& fs = bd.face_state[b];
      pface[b] = fs[0] * ref.pressure;
      Tface[b] = fs[4] * ref.temperature;
      Uface[b] = macro::velocity_of(fs) * ref.velocity;
    }
    VtuWriter wb = VtuWriter::boundary_polygons(mesh);
    wb.add_cell_scalar("mass_flux", mflux);
    wb.add_cell_scalar("energy_flux", eflux);
    wb.add_cell_vector("momentum_flux", mom);
    wb.add_cell_scalar("normal_momentum_flux", pn);
    wb.add_cell_vector("shear", shear);
    wb.add_cell_scalar("face_p", pface);
    wb.add_cell_scalar("face_T", Tface);
    wb.add_cell_vector("face_U", Uface);
    wb.write(out_dir / "boundary.vtu");
  }
  if (opt.cells_csv) {
    // セル中心の座標(L_ref 単位)と無次元の値(後処理・中心線の取り出し用)
    CsvWriter cells(out_dir / "cells.csv", {"cell", "x", "y", "z", "volume", "p", "ux", "uy", "uz", "T", "rho"});
    const MeshGeometry& g = solver.geometry();
    for (std::size_t c = 0; c < state.size(); ++c) {
      const Vec3& x = g.cell_center[c];
      cells.write_row({static_cast<std::int64_t>(c), x.x, x.y, x.z, g.cell_volume[c], state.p[c], state.ux[c],
                       state.uy[c], state.uz[c], state.T[c], state.p[c] / state.T[c]});
    }
    // 最後の評価の残差と運動エネルギーの欠損(診断用)
    CsvWriter diag(out_dir / "cells_residual.csv",
                   {"cell", "R_mass", "R_mom_x", "R_mom_y", "R_mom_z", "R_energy", "kinetic_energy_defect"});
    const std::vector<double>& defect = solver.last_kinetic_energy_defect();
    for (std::size_t c = 0; c < state.size(); ++c) {
      diag.write_row({static_cast<std::int64_t>(c), R[c][0], R[c][1], R[c][2], R[c][3], R[c][4], defect[c]});
    }
  }
  summary.set("total_seconds", total_timer.seconds());
  write_file(out_dir / "summary.json", json::dump(summary, 2) + "\n");
  print_item("出力", path_to_utf8(out_dir));
  return 0;
}

}  // namespace gsis::cli
