// gsis-solver cis-run: 運動論ソルバー(CIS)だけで定常解を求める(docs/design_cis_gpu.md)。
//
//   gsis-solver cis-run <case.toml> [--out <dir>] [--max-iter N] [--min-iter N] [--tol X]
//                       [--backend cpu|cuda] [--precision fp32|fp64] [--set key=value]...
//                       [--print-every N] [--vtu-every N] [--no-vtu] [--cells-csv] [--init-from <cells.csv>]
//                       [--sections <位置の並び>]
//
// --init-from: 前回の cells.csv(無次元のセル値)から f = f^eq(ρ, u, T) で始める(マクロ量からの再開、spec §4.6)
// --sections: 質量流量を集計する断面の位置(L_ref 単位。[output].mass_flow_sections を上書き。フェーズ 5c)
// --set gsis.mass_balance_tolerance=X・gsis.mass_balance_required=true|false: [gsis] の質量の釣り合いの設定(フェーズ 5c)
// 質量の釣り合い(フェーズ 5c): 入口と出口の流量の相対差と断面の流量のばらつきを history.csv(mass_imbalance・
// M_dot_sec<k>)と summary.json(mass_balance・mass_flow_sections)に書き、許容値を超えたら警告する
//
// 収束判定は式 (59)(連続する CIS 反復のモーメントの相対変化 < tol。既定は [gsis].tolerance)。
// 出力(既定の出力先は runs/kinetic/<ケース名>/、現在のディレクトリから):
//   history.csv   反復ごとの ε、残差、パッチごとの質量流量、時間の内訳
//   summary.json  反復数、収束、時間、メモリ、パッチごとの流量と境界面の平均、管なら無次元質量流量
//   fields.vtu    セル値(SI): rho, u, T, p, sigma, q, Ma, Kn_local
//   boundary.vtu  境界面の値(SI): 質量流束、運動量流束、エネルギー流束、境界面の分布の rho, u, T
//   cells.csv     --cells-csv のとき。セル中心と無次元のセル値(平板の検証用)

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <format>
#include <limits>
#include <span>
#include <string>
#include <vector>

#include "app/cli.hpp"
#include "app/mass_balance_report.hpp"
#include "config/case_config.hpp"
#include "core/build_info.hpp"
#include "core/error.hpp"
#include "core/json.hpp"
#include "core/log.hpp"
#include "core/paths.hpp"
#include "core/timer.hpp"
#include "io/csv_writer.hpp"
#include "kinetic/kinetic_output.hpp"
#include "kinetic/kinetic_params.hpp"
#include "kinetic/kinetic_solver.hpp"
#include "mesh/ordering.hpp"
#include "mesh/pmesh_io.hpp"

#if GSIS_WITH_CUDA
#include "gpu/device_info.hpp"
#endif

namespace gsis::cli {
namespace {

struct CisRunOptions {
  std::string case_file;
  std::string out_dir;
  long long max_iter = -1;
  long long min_iter = 2;
  double tol = -1.0;
  std::string backend;
  std::string precision;
  std::vector<std::pair<std::string, std::string>> overrides;  // --set key=value([kinetic])
  long long print_every = 10;
  long long vtu_every = 0;
  bool no_vtu = false;
  bool cells_csv = false;
  std::string init_from;  // 前回の cells.csv(無次元のセル値)から f = f^eq で初期化する
  std::string sections;   // 質量流量を集計する断面の位置の並び(フェーズ 5c。[output].mass_flow_sections を上書き)
};

long long parse_integer(const std::string& s, const std::string& name) {
  long long v = 0;
  const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
  if (ec != std::errc{} || ptr != s.data() + s.size()) throw UsageError{std::format("{} は整数です(値: {})", name, s)};
  return v;
}

double parse_real(const std::string& s, const std::string& name) {
  double v = 0.0;
  const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
  if (ec != std::errc{} || ptr != s.data() + s.size()) throw UsageError{std::format("{} は数値です(値: {})", name, s)};
  return v;
}

CisRunOptions parse_options(std::span<const std::string> args) {
  CisRunOptions o;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string& a = args[i];
    auto value = [&]() -> const std::string& {
      if (i + 1 >= args.size()) throw UsageError{std::format("{} の値がありません", a)};
      return args[++i];
    };
    if (a == "--out") {
      o.out_dir = value();
    } else if (a == "--max-iter") {
      o.max_iter = parse_integer(value(), a);
    } else if (a == "--min-iter") {
      o.min_iter = parse_integer(value(), a);
    } else if (a == "--tol") {
      o.tol = parse_real(value(), a);
    } else if (a == "--backend") {
      o.backend = value();
    } else if (a == "--precision") {
      o.precision = value();
    } else if (a == "--set") {
      const std::string& kv = value();
      const auto eq = kv.find('=');
      if (eq == std::string::npos || eq == 0) throw UsageError{std::format("--set は key=value の形です(値: {})", kv)};
      o.overrides.emplace_back(kv.substr(0, eq), kv.substr(eq + 1));
    } else if (a == "--print-every") {
      o.print_every = parse_integer(value(), a);
    } else if (a == "--vtu-every") {
      o.vtu_every = parse_integer(value(), a);
    } else if (a == "--no-vtu") {
      o.no_vtu = true;
    } else if (a == "--cells-csv") {
      o.cells_csv = true;
    } else if (a == "--init-from") {
      o.init_from = value();
    } else if (a == "--sections") {
      o.sections = value();
      try {
        (void)parse_section_spec(o.sections);
      } catch (const Error& e) {
        throw UsageError{std::format("--sections: {}", e.what())};
      }
    } else if (!a.empty() && a[0] == '-') {
      throw UsageError{std::format("知らないオプション {} です", a)};
    } else {
      if (!o.case_file.empty()) throw UsageError{"ケース定義(TOML)は 1 つだけ指定してください"};
      o.case_file = a;
    }
  }
  if (o.case_file.empty()) throw UsageError{"ケース定義(TOML)を指定してください"};
  if (o.max_iter == 0 || o.max_iter < -1) throw UsageError{"--max-iter は 1 以上です"};
  if (o.min_iter < 1) throw UsageError{"--min-iter は 1 以上です"};
  if (o.print_every < 0 || o.vtu_every < 0) throw UsageError{"--print-every・--vtu-every は 0 以上です"};
  return o;
}

// --set の値を [kinetic] の値にする(整数 → 実数 → 真偽値 → 文字列の順に解釈する)
ParamValue parse_param_value(const std::string& s) {
  long long i = 0;
  {
    const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), i);
    if (ec == std::errc{} && ptr == s.data() + s.size()) return i;
  }
  double d = 0.0;
  {
    const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), d);
    if (ec == std::errc{} && ptr == s.data() + s.size()) return d;
  }
  if (s == "true") return true;
  if (s == "false") return false;
  std::string t = s;
  if (t.size() >= 2 && t.front() == '"' && t.back() == '"') t = t.substr(1, t.size() - 2);
  return t;
}

json::Value timings_json(const kinetic::StepTimings& t, double scale) {
  json::Value o;
  o.set("moments", t.moments * scale);
  o.set("boundary", t.boundary * scale);
  o.set("gradient", t.gradient * scale);
  o.set("residual", t.residual * scale);
  o.set("sweep_forward", t.sweep_forward * scale);
  o.set("sweep_backward", t.sweep_backward * scale);
  o.set("other", t.other * scale);
  o.set("total", t.total() * scale);
  return o;
}

double finite_or_zero(double x) { return std::isfinite(x) ? x : 0.0; }

}  // namespace

int cmd_cis_run(std::span<const std::string> args) {
  const CisRunOptions opt = parse_options(args);
  CaseConfig cfg = load_case(path_from_utf8(opt.case_file));
  {
    ModuleSection& sec = cfg.module_sections.try_emplace("kinetic", ModuleSection{"kinetic"}).first->second;
    for (const auto& [k, v] : opt.overrides) {
      // 質量の釣り合いの設定だけは [gsis] の値を上書きできる(gsis-solver run --mode cis から渡す。フェーズ 5c)
      if (k == "gsis.mass_balance_tolerance") {
        double x = 0.0;
        const auto [ptr, ec] = std::from_chars(v.data(), v.data() + v.size(), x);
        GSIS_CHECK(ec == std::errc{} && ptr == v.data() + v.size() && x > 0.0 && std::isfinite(x),
                   std::format("gsis.mass_balance_tolerance は正の数値です(値: {})", v));
        cfg.gsis.mass_balance_tolerance = x;
        continue;
      }
      if (k == "gsis.mass_balance_required") {
        GSIS_CHECK(v == "true" || v == "false", std::format("gsis.mass_balance_required は true か false です(値: {})", v));
        cfg.gsis.mass_balance_required = v == "true";
        continue;
      }
      sec.values.insert_or_assign(k, parse_param_value(v));
    }
    if (!opt.backend.empty()) sec.values.insert_or_assign("backend", opt.backend);
    if (!opt.precision.empty()) sec.values.insert_or_assign("precision", opt.precision);
  }
  const KineticParams params = parse_kinetic_params(cfg);
  SectionSpec sections = parse_output_sections(cfg);
  if (!opt.sections.empty()) sections = parse_section_spec(opt.sections, section_axis_name(sections.axis));
  const long long max_iter = opt.max_iter > 0 ? opt.max_iter : params.max_iterations;
  const double tol = opt.tol > 0.0 ? opt.tol : cfg.gsis.tolerance;
  const std::filesystem::path out_dir =
      opt.out_dir.empty() ? std::filesystem::path("runs") / "kinetic" / path_from_utf8(cfg.case_info.name)
                          : path_from_utf8(opt.out_dir);
  std::filesystem::create_directories(out_dir);
  clear_stop_request(out_dir);

  Timer total_timer;
  const LoadedMesh lm = load_pmesh(cfg.mesh_path());
  Timer setup_timer;
  KineticSolver solver(cfg, lm.mesh, lm.geometry, params);
  const kinetic::KineticSetup& s = solver.setup();
  const double setup_s = setup_timer.seconds();
  if (opt.init_from.empty()) {
    solver.initialize_uniform(params.initial_pressure / params.initial_temperature, Vec3{}, params.initial_temperature);
  } else {
    solver.initialize_equilibrium(read_kinetic_cells_csv(path_from_utf8(opt.init_from), lm.mesh.n_cells));
  }

  print_heading("運動論ソルバー CIS(cis-run)");
  print_item("ケース", std::format("{}({})", cfg.case_info.name, opt.case_file));
  print_item("メッシュ", std::format("{} セル、境界面 {}", format_count(s.n_cells()), format_count(s.n_bfaces())));
  print_item("速度格子", std::format("{} × {} × {} = {} 点(鏡映の組 {})", cfg.velocity_grid.n[0], cfg.velocity_grid.n[1],
                                      cfg.velocity_grid.n[2], format_count(s.nv()), s.velocity.orbit_size));
  print_item("Kn(基準状態)・δ", std::format("{:.6g}・{:.6g}", s.derived.Kn, s.derived.delta_from_Kn));
  print_item("実行先・精度", std::format("{}・{}", solver.backend().name(), params.precision));
  const BlockPartitionStats block_stats = block_partition_stats(s.mesh, s.block_offsets);
  print_item("空間ブロック P・チャンク",
             std::format("{}({}、{}〜{} セル、ブロック間の面 {})・{} 点", s.n_blocks(), params.cell_ordering,
                         format_count(block_stats.min_cells), format_count(block_stats.max_cells),
                         format_count(block_stats.n_cut_faces), solver.backend().chunk_width()));
  print_item("擬似時間", params.pseudo_cfl == 0.0 ? std::string("Δt = ∞")
                                                   : std::format("CFL {:.6g}({})", params.pseudo_cfl, params.pseudo_time));
  print_item("リミッタ", params.limiter == "none" ? std::string("なし")
                                                  : std::format("Venkatakrishnan K = {:.6g}({})", params.limiter_k,
                                                                params.limiter_eps));
  print_item("メモリ", format_bytes(static_cast<double>(solver.backend().memory_bytes())));
  print_item("収束判定", std::format("ε < {:.3g}(上限 {} 回)", tol, max_iter));
  print_item("質量の釣り合い", std::format("許容 {:.1e}({})", cfg.gsis.mass_balance_tolerance,
                                           cfg.gsis.mass_balance_required ? "収束の判定に加える" : "超えたら警告"));
  if (!sections.empty()) {
    std::string pos;
    for (const SectionCut& c : solver.section_cuts(sections)) {
      pos += std::format("{}{:.4g}({} 面)", pos.empty() ? "" : "、", c.face_position, c.faces.size());
    }
    print_item("質量流量の断面", std::format("{} = {}", section_axis_name(sections.axis), pos));
  }
  print_item("閉じた領域", s.closed_domain ? "はい(反復ごとに全質量を規格化)" : "いいえ");
  print_item("初期状態", opt.init_from.empty()
                             ? std::format("一様(p = {:.6g} p_ref、T = {:.6g} T0、静止)", params.initial_pressure,
                                           params.initial_temperature)
                             : std::format("{} のセル値の平衡分布", opt.init_from));
  print_item("出力先", path_to_utf8(out_dir));
  print_item("前処理の時間", std::format("{:.3f} s", setup_s));
#if GSIS_WITH_CUDA
  std::size_t gpu_used_bytes = 0;
  if (params.backend == "cuda" && gpu::device_count() > 0) {
    const gpu::MemoryInfo mem = gpu::memory_info(0);
    gpu_used_bytes = mem.total - mem.free;
    print_item("GPU のメモリ(全体 − 空き)", std::format("{} / {}(cudaMemGetInfo。WDDM では他の用途を含む)",
                                                       format_bytes(static_cast<double>(gpu_used_bytes)),
                                                       format_bytes(static_cast<double>(mem.total))));
  }
#endif

  const std::size_t gpu_memory_bytes = params.backend == "cuda" ? solver.backend().memory_bytes() : 0;
  KineticHistoryWriter history(out_dir / "history.csv", s, gpu_memory_bytes, sections.positions.size());
  bool converged = false;
  bool stopped = false;
  KineticIterationInfo last;
  kinetic::StepTimings timing_sum;
  long long timed_iterations = 0;
  double iter_time_sum = 0.0;
  for (;;) {
    const KineticIterationInfo& prep = solver.prepare();
    // f^ℓ の断面の質量流量(フェーズ 5c。境界の流量と同じ f の値)
    const std::vector<double> sec = solver.section_mass_flow(sections);
    // 収束の判定(式 (59))。mass_balance_required なら境界の質量の釣り合いも見る(フェーズ 5c)
    const MassBalance mb = mass_balance(prep.patch_mass_flow);
    const bool balance_ok = !cfg.gsis.mass_balance_required || !mb.valid ||
                            std::abs(mb.imbalance) < cfg.gsis.mass_balance_tolerance;
    if (prep.iteration >= opt.min_iter && prep.has_epsilon && prep.epsilon < tol && balance_ok) {
      converged = true;
      last = prep;
      history.write(prep, total_timer.seconds(), sec);
      break;
    }
    if (prep.iteration >= max_iter) {
      last = prep;
      history.write(prep, total_timer.seconds(), sec);
      break;
    }
    const KineticIterationInfo& info = solver.sweep();
    history.write(info, total_timer.seconds(), sec);
    if (info.iteration >= 1) {  // 最初の反復(初期化の直後)は計時から外す
      timing_sum += info.timings;
      iter_time_sum += info.seconds;
      ++timed_iterations;
    }
    if (opt.print_every > 0 && (info.iteration % opt.print_every == 0 || info.iteration < 3)) {
      std::string flows;
      for (std::size_t p = 0; p < s.patches.size(); ++p) {
        if (s.patches[p].kind == kinetic::kBcDiffuseWall || s.patches[p].kind == kinetic::kBcSymmetry ||
            s.patches[p].kind == kinetic::kBcPeriodic) {
          continue;
        }
        flows += std::format("  {} {:.6g}", s.patches[p].name, info.patch_mass_flow[p]);
      }
      const std::string line = std::format("反復 {:6}  ε {:.3e}  残差 {:.3e}  {:.3f} s{}\n", info.iteration,
                                           info.has_epsilon ? info.epsilon : 0.0, info.residual, info.seconds, flows);
      std::fwrite(line.data(), 1, line.size(), stdout);
      std::fflush(stdout);
    }
    if (opt.vtu_every > 0 && !opt.no_vtu && solver.iteration() % opt.vtu_every == 0) {
      write_kinetic_cell_vtu(out_dir / std::format("fields_{:06}.vtu", solver.iteration()), lm.mesh,
                             solver.moments(), s, solver.residual_per_cell());
    }
    if (stop_requested(out_dir)) {
      // 穏やかな停止: いまの f で結果を書いて終える
      stopped = true;
      last = solver.prepare();
      history.write(last, total_timer.seconds(), solver.section_mass_flow(sections));
      log_info("{} があるので反復 {} で止めます", kStopFileName, last.iteration);
      break;
    }
  }
  const double wall_s = total_timer.seconds();

  const KineticMoments moments = solver.moments();
  const BoundaryKineticFluxes fluxes = solver.boundary_fluxes();
  if (!opt.no_vtu) {
    write_kinetic_cell_vtu(out_dir / "fields.vtu", lm.mesh, moments, s, solver.residual_per_cell());
    write_kinetic_boundary_vtu(out_dir / "boundary.vtu", lm.mesh, lm.geometry, fluxes, s);
  }
  if (opt.cells_csv) {
    write_kinetic_cells_csv(out_dir / "cells.csv", lm.mesh, lm.geometry, moments, s.length_scale,
                            solver.residual_per_cell());
  }

  // パッチごとの流量と、境界面の分布の面積平均
  KineticPatchSummary ps = summarize_kinetic_patches(lm.mesh, lm.geometry, fluxes, s);
  const double mdot_in = ps.mdot_in;
  const double mdot_out = ps.mdot_out;
  const bool has_in = ps.has_in;
  const bool has_out = ps.has_out;
  // 質量の釣り合いと断面の質量流量(フェーズ 5c)
  const std::vector<double> sec_final = solver.section_mass_flow(sections);
  BalanceReport balance;
  balance.tolerance = cfg.gsis.mass_balance_tolerance;
  balance.required = cfg.gsis.mass_balance_required;
  balance.entries.push_back(make_balance_entry("kinetic", "運動論(最後の f)", ps.mdot, sec_final));
  evaluate_balance(balance);

  json::Value root;
  root.set("program", build_description());
  root.set("case", cfg.case_info.name);
  root.set("case_file", opt.case_file);
  root.set("mesh", path_to_utf8(cfg.mesh_path()));
  root.set("n_cells", static_cast<std::int64_t>(s.n_cells()));
  root.set("n_boundary_faces", static_cast<std::int64_t>(s.n_bfaces()));
  root.set("n_velocity", static_cast<std::int64_t>(s.nv()));
  root.set("Kn", s.derived.Kn);
  root.set("delta", s.derived.delta_from_Kn);
  root.set("iterations", static_cast<std::int64_t>(last.iteration));
  root.set("converged", converged);
  root.set("stopped", stopped);
  root.set("tolerance", tol);
  root.set("epsilon", last.has_epsilon ? last.epsilon : json::Value(nullptr));
  root.set("eps_rho", last.has_epsilon ? last.eps_rho : json::Value(nullptr));
  root.set("eps_u", last.has_epsilon ? last.eps_u : json::Value(nullptr));
  root.set("eps_T", last.has_epsilon ? last.eps_T : json::Value(nullptr));
  root.set("wall_time_s", wall_s);
  root.set("setup_time_s", setup_s);
  root.set("time_per_iteration_s", timed_iterations > 0 ? iter_time_sum / timed_iterations : 0.0);
  root.set("kernel_time_per_iteration_s",
           timings_json(timing_sum, timed_iterations > 0 ? 1.0 / static_cast<double>(timed_iterations) : 0.0));
  root.set("backend", solver.backend().name());
  root.set("precision", params.precision);
  root.set("memory_bytes", static_cast<std::int64_t>(solver.backend().memory_bytes()));
#if GSIS_WITH_CUDA
  if (gpu_used_bytes > 0) root.set("gpu_used_bytes_after_setup", static_cast<std::int64_t>(gpu_used_bytes));
#endif
  root.set("velocity_chunk", static_cast<std::int64_t>(solver.backend().chunk_width()));
  root.set("sweep_blocks", static_cast<std::int64_t>(s.n_blocks()));
  root.set("cell_ordering", params.cell_ordering);
  root.set("block_cut_faces", static_cast<std::int64_t>(block_stats.n_cut_faces));
  {
    json::Value p;
    p.set("pseudo_cfl", params.pseudo_cfl);
    p.set("pseudo_time", params.pseudo_time);
    p.set("limiter", params.limiter);
    p.set("limiter_k", params.limiter_k);
    p.set("limiter_eps", params.limiter_eps);
    p.set("lsq_weight_power", params.lsq_weight_power);
    p.set("cell_ordering", params.cell_ordering);
    p.set("initial_pressure", params.initial_pressure);
    p.set("initial_temperature", params.initial_temperature);
    p.set("convergence_u_floor", params.convergence_u_floor);
    p.set("wall_relaxation", params.wall_relaxation);
    p.set("interface_damping", params.interface_damping);
    // フェーズ 5a・5b の CIS の設定(フェーズ 5c で記録に足した)
    p.set("positivity_limiter", params.positivity_limiter);
    p.set("sweep_order", params.sweep_order);
    p.set("inner_sweeps", static_cast<std::int64_t>(params.inner_sweeps));
    p.set("inner_wall_update", params.inner_wall_update);
    p.set("block_passes", static_cast<std::int64_t>(params.block_passes));
    p.set("equilibrium", params.equilibrium);
    p.set("deq_fallback", params.deq_fallback);
    p.set("pressure_boundary_reconstruction", params.pressure_boundary_reconstruction);
    p.set("cold_bgk_temperature", params.cold_bgk_temperature);
    root.set("params", std::move(p));
  }
  {
    const ReferenceScales& r = s.derived.ref;
    json::Value ref;
    ref.set("length_m", r.length);
    ref.set("temperature_K", r.temperature);
    ref.set("pressure_Pa", r.pressure);
    ref.set("density_kg_m3", r.density);
    ref.set("velocity_m_s", r.velocity);
    ref.set("heat_flux_W_m2", r.heat_flux);
    ref.set("mass_flux_kg_m2_s", r.mass_flux);
    root.set("reference", std::move(ref));
  }
  root.set("patches", std::move(ps.patches));
  {
    json::Value mf = tube_mass_flow_json(s, mdot_in, has_in, mdot_out, has_out);
    if (!mf.is_null()) root.set("mass_flow", std::move(mf));
  }
  root.set("mass_balance", balance_json(balance, s));
  if (!sections.empty()) {
    root.set("mass_flow_sections", sections_json(sections, solver.section_cuts(sections), {{"kinetic", sec_final}}, s));
  }
  root.set("residual", finite_or_zero(last.residual));
  write_file(out_dir / "summary.json", json::dump(root, 2) + "\n");

  print_heading("結果");
  print_item("反復", std::format("{}({})", last.iteration,
                                 converged ? "収束" : (stopped ? "STOP で停止" : "上限に達した")));
  if (last.has_epsilon) print_item("ε", std::format("{:.3e}", last.epsilon));
  print_item("時間", std::format("{:.2f} s(1 反復 {:.3f} s)", wall_s,
                                 timed_iterations > 0 ? iter_time_sum / timed_iterations : 0.0));
  if (s.derived.has_p_in && has_in && has_out) {
    const double scale = s.derived.mass_flow_scale;
    print_item("無次元質量流量 Ṁ", std::format("入口 {:.5f}、出口 {:.5f}", mass_flow_to_si(s, mdot_in) / scale,
                                               mass_flow_to_si(s, mdot_out) / scale));
  }
  print_balance(balance, sections, solver.section_cuts(sections), s);
  if (converged && !balance.warnings.empty()) {
    log_warn("式 (59) の判定(ε < {:.3g})は満たしましたが、質量の釣り合いが許容 {:.1e} を超えています(summary.json の "
             "mass_balance)",
             tol, balance.tolerance);
  }
  print_item("出力", path_to_utf8(out_dir));
  if (!converged && !stopped) {
    log_warn("反復の上限 {} 回で収束しませんでした(ε = {:.3e})", max_iter, last.has_epsilon ? last.epsilon : 0.0);
  }
  return 0;
}

}  // namespace gsis::cli
