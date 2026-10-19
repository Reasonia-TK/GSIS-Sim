// gsis-solver run: ケースの [case].mode に応じて、GSIS(mode = "gsis")か CIS 単独(mode = "cis")で定常解を求める
// (docs/design_gsis.md §6)。
//
//   gsis-solver run <case.toml> [--out <dir>] [--mode gsis|cis] [--max-iter N] [--min-iter N] [--tol X]
//                   [--backend cpu|cuda] [--precision fp32|fp64] [--set [kinetic.|macro.|gsis.]key=value]...
//                   [--threads N] [--print-every N] [--vtu-every N] [--no-vtu] [--cells-csv]
//                   [--init-from <cells.csv>] [--save-init <cells.csv>] [--sections <位置の並び>]
//
// - CIS 単独は cis-run と同じ計算・出力(出力先の既定だけ runs/gsis/<ケース名>/)。
// - GSIS: 初期化(一様な状態 → PBC 単独(NSF のみ)を [macro].init_iterations 回まで → f = f^eq(W)、仮定 A9)の後、
//   GSIS 反復(CIS [gsis].n_cis 回 → モーメント → PBC 段(M = [gsis].max_pbc、CFL = [gsis].cfl_pbc)→ 式 (14) の補正)を
//   式 (59) の ε < tol(既定 [gsis].tolerance)になるまで、--max-iter(既定 [gsis].max_iterations)回まで繰り返す。
// - --set は [kinetic](接頭辞なし、または kinetic.)・[macro](macro.)・[gsis](gsis.)の値を上書きする。
// - --init-from: cells.csv(cis-run・run の --cells-csv の出力)の状態の平衡分布から始める(PBC 単独の初期化は省く)。
// - --save-init: 初期化の後の状態(PBC 単独の解)を cells.csv の形で書く(同じケースの比較の計算を --init-from で始める)。
// - --sections: 質量流量を集計する断面の位置(L_ref 単位、例 "0,2.5,5,7.5,10"。[output].mass_flow_sections を上書きする。
//   軸は [output].mass_flow_section_axis、既定 x)。フェーズ 5c
// - 質量の釣り合い(フェーズ 5c): 境界の入口と出口の質量流量の相対差と、断面の流量のばらつきを history.csv・summary.json
//   ("mass_balance"・"mass_flow_sections")に書き、[gsis].mass_balance_tolerance を超えたら式 (59) の判定を満たしても
//   警告する。[gsis].mass_balance_required = true なら収束の判定にも加える
// - 出力(既定の出力先は runs/gsis/<ケース名>/。カレントディレクトリから):
//     history.csv      GSIS 反復ごとの ε、PBC 段の要約、負値の置換、パッチごとの質量流量(運動論・PBC)、時間の内訳
//     pbc_history.csv  PBC の反復ごとの残差・更新量・線形ソルバー(初期化の PBC 単独は gsis_iteration = 0)
//     summary.json     反復数、収束、時間の内訳、パッチごとの流量、管なら無次元質量流量 Ṁ(運動論・PBC)
//     fields.vtu       セル値(SI): rho, u, T, p, sigma, q, Ma, Kn_local, residual, hot_sigma, hot_q
//     boundary.vtu     境界面の値(SI): 質量流束、運動量流束、エネルギー流束、境界面の分布の rho, u, T
//     cells.csv        --cells-csv のとき(cis-run と同じ形式)
// - 終了コード: 計算が最後まで進めば 0(収束しなくても 0。summary.json の converged で判断)、エラー 1、引数の誤り 2。

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
#include "gsis/gsis_output.hpp"
#include "io/csv_writer.hpp"
#include "gsis/gsis_solver.hpp"
#include "kinetic/kinetic_output.hpp"
#include "kinetic/kinetic_params.hpp"
#include "macro/linear_solver.hpp"
#include "macro/macro_params.hpp"
#include "mesh/ordering.hpp"
#include "mesh/pmesh_io.hpp"

#if GSIS_WITH_CUDA
#include "gpu/device_info.hpp"
#endif

namespace gsis::cli {
namespace {

struct Override {
  std::string section;  // "kinetic" | "macro" | "gsis"
  std::string key;
  std::string value;
};

struct RunOptions {
  std::string case_file;
  std::string out_dir;
  std::string mode;  // 空ならケースの [case].mode
  long long max_iter = -1;
  long long min_iter = -1;
  double tol = -1.0;
  std::string backend;
  std::string precision;
  std::vector<Override> overrides;
  int threads = 0;
  long long print_every = -1;
  long long vtu_every = 0;
  long long dump_every = 0;  // 診断用: GSIS 反復ごとのセルの状態(W^{n+1/2}・W^{n+1}・HoT)を diag_<反復>.csv に書く
  bool no_vtu = false;
  bool cells_csv = false;
  std::string init_from;
  std::string save_init;  // 初期化(PBC 単独)の後の状態を cells.csv の形で書く(--init-from で再利用する)
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
  if (ec != std::errc{} || ptr != s.data() + s.size() || !std::isfinite(v)) {
    throw UsageError{std::format("{} は数値です(値: {})", name, s)};
  }
  return v;
}

RunOptions parse_options(std::span<const std::string> args) {
  RunOptions o;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string& a = args[i];
    auto value = [&]() -> const std::string& {
      if (i + 1 >= args.size()) throw UsageError{std::format("{} の値がありません", a)};
      return args[++i];
    };
    if (a == "--out") {
      o.out_dir = value();
    } else if (a == "--mode") {
      o.mode = value();
      if (o.mode != "gsis" && o.mode != "cis") throw UsageError{"--mode は gsis か cis です"};
    } else if (a == "--max-iter") {
      o.max_iter = parse_integer(value(), a);
      if (o.max_iter < 1) throw UsageError{"--max-iter は 1 以上です"};
    } else if (a == "--min-iter") {
      o.min_iter = parse_integer(value(), a);
      if (o.min_iter < 1) throw UsageError{"--min-iter は 1 以上です"};
    } else if (a == "--tol") {
      o.tol = parse_real(value(), a);
      if (!(o.tol > 0.0)) throw UsageError{"--tol は正です"};
    } else if (a == "--backend") {
      o.backend = value();
    } else if (a == "--precision") {
      o.precision = value();
    } else if (a == "--set") {
      const std::string& kv = value();
      const auto eq = kv.find('=');
      if (eq == std::string::npos || eq == 0) throw UsageError{std::format("--set は key=value の形です(値: {})", kv)};
      Override ov;
      std::string key = kv.substr(0, eq);
      ov.value = kv.substr(eq + 1);
      ov.section = "kinetic";
      const auto dot = key.find('.');
      if (dot != std::string::npos) {
        ov.section = key.substr(0, dot);
        key = key.substr(dot + 1);
        if (ov.section != "kinetic" && ov.section != "macro" && ov.section != "gsis") {
          throw UsageError{std::format("--set の節は kinetic・macro・gsis のどれかです(値: {})", kv)};
        }
        if (key.empty()) throw UsageError{std::format("--set のキーがありません(値: {})", kv)};
      }
      ov.key = key;
      o.overrides.push_back(std::move(ov));
    } else if (a == "--threads") {
      o.threads = static_cast<int>(parse_integer(value(), a));
      if (o.threads < 1) throw UsageError{"--threads は 1 以上です"};
    } else if (a == "--print-every") {
      o.print_every = parse_integer(value(), a);
      if (o.print_every < 0) throw UsageError{"--print-every は 0 以上です"};
    } else if (a == "--vtu-every") {
      o.vtu_every = parse_integer(value(), a);
      if (o.vtu_every < 0) throw UsageError{"--vtu-every は 0 以上です"};
    } else if (a == "--dump-every") {
      o.dump_every = parse_integer(value(), a);
      if (o.dump_every < 0) throw UsageError{"--dump-every は 0 以上です"};
    } else if (a == "--no-vtu") {
      o.no_vtu = true;
    } else if (a == "--cells-csv") {
      o.cells_csv = true;
    } else if (a == "--init-from") {
      o.init_from = value();
    } else if (a == "--save-init") {
      o.save_init = value();
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
  return o;
}

// --set の値を [kinetic]・[macro] の値にする(整数 → 実数 → 真偽値 → 文字列の順に解釈する。cis-run と同じ)
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

// [gsis] の値の上書き(知らないキー・範囲外はエラー)
void apply_gsis_override(GsisSection& g, const std::string& key, const std::string& value) {
  auto integer = [&]() {
    long long v = 0;
    const auto [ptr, ec] = std::from_chars(value.data(), value.data() + value.size(), v);
    GSIS_CHECK(ec == std::errc{} && ptr == value.data() + value.size(),
               std::format("gsis.{} は整数です(値: {})", key, value));
    GSIS_CHECK(v >= 1, std::format("gsis.{} は 1 以上です(値: {})", key, value));
    return v;
  };
  auto real = [&]() {
    double v = 0.0;
    const auto [ptr, ec] = std::from_chars(value.data(), value.data() + value.size(), v);
    GSIS_CHECK(ec == std::errc{} && ptr == value.data() + value.size() && v > 0.0 && std::isfinite(v),
               std::format("gsis.{} は正の数値です(値: {})", key, value));
    return v;
  };
  if (key == "n_cis") {
    g.n_cis = static_cast<int>(std::min<long long>(integer(), 1'000'000));
  } else if (key == "max_pbc") {
    g.max_pbc = static_cast<int>(std::min<long long>(integer(), 1'000'000));
  } else if (key == "cfl_pbc") {
    g.cfl_pbc = real();
  } else if (key == "tolerance") {
    g.tolerance = real();
  } else if (key == "max_iterations") {
    g.max_iterations = integer();
  } else if (key == "mass_balance_tolerance") {
    g.mass_balance_tolerance = real();
  } else if (key == "mass_balance_required") {
    GSIS_CHECK(value == "true" || value == "false",
               std::format("gsis.mass_balance_required は true か false です(値: {})", value));
    g.mass_balance_required = value == "true";
  } else if (key == "filter_length" || key == "filter_max_length" || key == "filter_min_cells" || key == "damp_kn") {
    double v = 0.0;
    const auto [ptr, ec] = std::from_chars(value.data(), value.data() + value.size(), v);
    GSIS_CHECK(ec == std::errc{} && ptr == value.data() + value.size() && v >= 0.0 && std::isfinite(v),
               std::format("gsis.{} は 0 以上の数値です(値: {})", key, value));
    (key == "filter_length"      ? g.filter_length
     : key == "filter_max_length" ? g.filter_max_length
     : key == "filter_min_cells"  ? g.filter_min_cells
                                  : g.damp_kn) = v;
  } else {
    GSIS_FAIL(std::format("[gsis] に {} というキーはありません(n_cis・max_pbc・cfl_pbc・tolerance・max_iterations・"
                          "filter_length・filter_max_length・filter_min_cells・damp_kn・mass_balance_tolerance・"
                          "mass_balance_required)",
                          key));
  }
}

void apply_overrides(CaseConfig& cfg, const RunOptions& opt) {
  for (const Override& o : opt.overrides) {
    if (o.section == "gsis") {
      apply_gsis_override(cfg.gsis, o.key, o.value);
    } else {
      ModuleSection& sec = cfg.module_sections.try_emplace(o.section, ModuleSection{o.section}).first->second;
      sec.values.insert_or_assign(o.key, parse_param_value(o.value));
    }
  }
  ModuleSection& kin = cfg.module_sections.try_emplace("kinetic", ModuleSection{"kinetic"}).first->second;
  if (!opt.backend.empty()) kin.values.insert_or_assign("backend", opt.backend);
  if (!opt.precision.empty()) kin.values.insert_or_assign("precision", opt.precision);
}

// CIS 単独: cis-run に引数を渡して同じ計算をする
int run_cis_mode(const RunOptions& opt, const CaseConfig& cfg, const std::filesystem::path& out_dir) {
  std::vector<std::string> a = {opt.case_file, "--out", path_to_utf8(out_dir)};
  if (opt.max_iter > 0) a.insert(a.end(), {"--max-iter", std::to_string(opt.max_iter)});
  if (opt.min_iter > 0) a.insert(a.end(), {"--min-iter", std::to_string(opt.min_iter)});
  bool tol_override = false;
  for (const Override& o : opt.overrides) {
    if (o.section == "kinetic") {
      a.insert(a.end(), {"--set", o.key + "=" + o.value});
    } else if (o.section == "gsis" && o.key == "tolerance") {
      tol_override = true;
    } else if (o.section == "gsis" && (o.key == "mass_balance_tolerance" || o.key == "mass_balance_required")) {
      a.insert(a.end(), {"--set", "gsis." + o.key + "=" + o.value});
    } else {
      log_warn("CIS 単独では使わない設定なので無視します: {}.{}={}", o.section, o.key, o.value);
    }
  }
  if (opt.tol > 0.0) {
    a.insert(a.end(), {"--tol", std::format("{:.17g}", opt.tol)});
  } else if (tol_override) {
    a.insert(a.end(), {"--tol", std::format("{:.17g}", cfg.gsis.tolerance)});
  }
  if (!opt.backend.empty()) a.insert(a.end(), {"--backend", opt.backend});
  if (!opt.precision.empty()) a.insert(a.end(), {"--precision", opt.precision});
  if (opt.print_every >= 0) a.insert(a.end(), {"--print-every", std::to_string(opt.print_every)});
  if (opt.vtu_every > 0) a.insert(a.end(), {"--vtu-every", std::to_string(opt.vtu_every)});
  if (opt.no_vtu) a.emplace_back("--no-vtu");
  if (opt.cells_csv) a.emplace_back("--cells-csv");
  if (!opt.init_from.empty()) a.insert(a.end(), {"--init-from", opt.init_from});
  if (!opt.sections.empty()) a.insert(a.end(), {"--sections", opt.sections});
  if (opt.threads > 0) macro::set_openmp_threads(opt.threads);
  return cmd_cis_run(std::span<const std::string>(a));
}

json::Value finite_or_null(double x) { return std::isfinite(x) ? json::Value(x) : json::Value(nullptr); }

// 診断用: セルの状態(CIS の後の W^{n+1/2}、PBC の後の W^{n+1}、HoT の大きさ。無次元、座標は L_ref 単位)
void write_diag_csv(const std::filesystem::path& path, const PolyMesh& mesh, const MeshGeometry& geometry, double L,
                    const CellMacroState& half, const CellMacroState& w, const HigherOrderTerms& hot,
                    const std::vector<std::array<double, 5>>& R0, const std::vector<double>& throughput0,
                    const std::vector<char>& replaced) {
  CsvWriter csv(path, {"cell", "x", "y", "z", "volume", "rho_half", "ux_half", "uy_half", "uz_half", "T_half", "rho",
                       "ux", "uy", "uz", "T", "hot_sigma", "hot_q", "R0_mass", "R0_mom_x", "R0_mom_y", "R0_mom_z",
                       "R0_energy", "mass_throughput0", "replaced"});
  const auto n = static_cast<std::size_t>(mesh.n_cells);
  for (std::size_t c = 0; c < n; ++c) {
    const Vec3 x = geometry.cell_center[c] / L;
    double s2 = 0.0;
    double q2 = 0.0;
    if (c < hot.sigma.size()) {
      for (double v : hot.sigma[c]) s2 += v * v;
      for (double v : hot.q[c]) q2 += v * v;
    }
    const bool hh = c < half.size();
    const std::vector<double> row = {static_cast<double>(c),
                                     x.x,
                                     x.y,
                                     x.z,
                                     geometry.cell_volume[c] / (L * L * L),
                                     hh ? half.rho[c] : 0.0,
                                     hh ? half.ux[c] : 0.0,
                                     hh ? half.uy[c] : 0.0,
                                     hh ? half.uz[c] : 0.0,
                                     hh ? half.T[c] : 0.0,
                                     w.rho[c],
                                     w.ux[c],
                                     w.uy[c],
                                     w.uz[c],
                                     w.T[c],
                                     std::sqrt(s2),
                                     std::sqrt(q2),
                                     c < R0.size() ? R0[c][0] : 0.0,
                                     c < R0.size() ? R0[c][1] : 0.0,
                                     c < R0.size() ? R0[c][2] : 0.0,
                                     c < R0.size() ? R0[c][3] : 0.0,
                                     c < R0.size() ? R0[c][4] : 0.0,
                                     c < throughput0.size() ? throughput0[c] : 0.0,
                                     c < replaced.size() ? static_cast<double>(replaced[c]) : 0.0};
    csv.write_row(std::span<const double>(row));
  }
}

json::Value tube_mass_flow_value(const TubeMassFlow& m, double scale) {
  json::Value mf;
  mf.set("scale_kg_s", scale);
  if (m.has_inlet) mf.set("M_dot_inlet", finite_or_null(m.inlet));
  if (m.has_outlet) mf.set("M_dot_outlet", finite_or_null(m.outlet));
  if (m.has_inlet && m.has_outlet) mf.set("M_dot", finite_or_null(m.mean()));
  return mf;
}

// 進み具合の 1 行
void print_iteration(const GsisIterationInfo& it, const kinetic::KineticSetup& s, double elapsed) {
  std::string flows;
  const TubeMassFlow mk = tube_mass_flow(s, it.patch_mass_flow_kinetic);
  const TubeMassFlow mp = tube_mass_flow(s, it.patch_mass_flow_pbc);
  if (mk.has_inlet && mk.has_outlet) {
    flows = std::format("  Ṁ 入口 {:.5f} 出口 {:.5f}(PBC {:.5f} / {:.5f})", mk.inlet, mk.outlet, mp.inlet, mp.outlet);
    if (!it.section_flow_kinetic.empty()) {
      double lo = it.section_flow_kinetic.front(), hi = lo;
      for (const double x : it.section_flow_kinetic) {
        lo = std::min(lo, x);
        hi = std::max(hi, x);
      }
      flows += std::format("  断面 {:.5f}〜{:.5f}", mass_flow_display(s, lo), mass_flow_display(s, hi));
    }
  } else {
    for (std::size_t p = 0; p < s.patches.size() && p < it.patch_mass_flow_kinetic.size(); ++p) {
      const auto k = s.patches[p].kind;
      if (k == kinetic::kBcDiffuseWall || k == kinetic::kBcSymmetry || k == kinetic::kBcPeriodic) continue;
      flows += std::format("  {} {:.6g}", s.patches[p].name, it.patch_mass_flow_kinetic[p]);
    }
  }
  const std::string line =
      std::format("GSIS {:4}  ε {:.3e}(ρ {:.2e} u {:.2e} T {:.2e})  PBC {:2} 回{}  置換 {}{}  CIS {:.2f} s  PBC {:.2f} s  "
                  "転送 {:.2f} s  計 {:.1f} s\n",
                  it.iteration, it.change.epsilon, it.change.rho, it.change.u, it.change.T, it.pbc.iterations,
                  (it.pbc.rejected_iterations > 0 ? std::format("(捨てた {})", it.pbc.rejected_iterations) : std::string()) +
                      (it.pbc.frozen_cells > 0 ? std::format("(固定 {} セル)", it.pbc.frozen_cells) : std::string()),
                  it.replaced_cells, flows, it.t_cis, it.t_pbc, it.t_moments + it.t_correction, elapsed);
  std::fwrite(line.data(), 1, line.size(), stdout);
  std::fflush(stdout);
}

int run_gsis_mode(const RunOptions& opt, const CaseConfig& cfg, const std::filesystem::path& out_dir) {
  const KineticParams kp = parse_kinetic_params(cfg);
  const MacroParams mp = parse_macro_params(cfg);
  if (opt.threads > 0) macro::set_openmp_threads(opt.threads);
  const long long max_iter = opt.max_iter > 0 ? opt.max_iter : cfg.gsis.max_iterations;
  const long long min_iter = opt.min_iter > 0 ? opt.min_iter : 2;
  const double tol = opt.tol > 0.0 ? opt.tol : cfg.gsis.tolerance;
  const long long print_every = opt.print_every >= 0 ? opt.print_every : 1;
  std::filesystem::create_directories(out_dir);
  clear_stop_request(out_dir);

  Timer total_timer;
  const LoadedMesh lm = load_pmesh(cfg.mesh_path());
  Timer setup_timer;
  GsisOptions gopt = GsisOptions::from_case(cfg, kp, mp);
  if (!opt.sections.empty()) {
    gopt.sections = parse_section_spec(opt.sections, section_axis_name(gopt.sections.axis));
  }
  GsisSolver solver(cfg, lm.mesh, lm.geometry, kp, mp, gopt);
  const kinetic::KineticSetup& s = solver.kinetic().setup();
  const double setup_s = setup_timer.seconds();

  print_heading("GSIS(運動論 CIS + PBC マクロソルバー、run)");
  print_item("ケース", std::format("{}({})", cfg.case_info.name, opt.case_file));
  print_item("メッシュ", std::format("{} セル、境界面 {}", format_count(s.n_cells()), format_count(s.n_bfaces())));
  print_item("速度格子", std::format("{} × {} × {} = {} 点(鏡映の組 {})", cfg.velocity_grid.n[0], cfg.velocity_grid.n[1],
                                      cfg.velocity_grid.n[2], format_count(s.nv()), s.velocity.orbit_size));
  print_item("Kn(基準状態)・δ", std::format("{:.6g}・{:.6g}", s.derived.Kn, s.derived.delta_from_Kn));
  print_item("運動論: 実行先・精度", std::format("{}・{}", solver.kinetic().backend().name(), kp.precision));
  const BlockPartitionStats block_stats = block_partition_stats(s.mesh, s.block_offsets);
  print_item("運動論: 空間ブロック P・チャンク",
             std::format("{}({}、ブロック間の面 {})・{} 点", s.n_blocks(), kp.cell_ordering,
                         format_count(block_stats.n_cut_faces), solver.kinetic().backend().chunk_width()));
  print_item("運動論: リミッタ", kp.limiter == "none" ? std::string("なし")
                                                   : std::format("Venkatakrishnan K = {:.6g}({})", kp.limiter_k,
                                                                 kp.limiter_eps));
  print_item("運動論: スイープの順序・内部反復・ブロック間の反復",
             std::format("{}・{} 回{}・{} 回", kp.sweep_order, kp.inner_sweeps,
                         kp.inner_wall_update ? "(壁の更新あり)" : "", kp.block_passes));
  print_item("運動論: 平衡分布", kp.equilibrium == "discrete" ? "保存的な離散平衡" : "連続の Maxwell 分布");
  print_item("運動論: メモリ", format_bytes(static_cast<double>(solver.kinetic().backend().memory_bytes())));
  print_item("GSIS: N_CIS・M・CFL", std::format("{}・{}・{:.6g}", gopt.n_cis, gopt.max_pbc, gopt.cfl_pbc));
  if (gopt.filter_length > 0.0 || gopt.filter_min_cells > 0.0) {
    print_item("GSIS: 補正のフィルタ",
               std::format("ℓ = {:.6g} λ{}(上限{})", gopt.filter_length,
                           gopt.filter_min_cells > 0.0 ? std::format("、下限 {:.6g} h", gopt.filter_min_cells)
                                                       : std::string(),
                           gopt.filter_max_length > 0.0 ? std::format(" {:.6g} L_ref", gopt.filter_max_length)
                                                        : std::string("は領域の対角線")));
  }
  if (gopt.damp_kn > 0.0) {
    print_item("GSIS: 補正の減衰", std::format("w = 1/(1 + (Kn/{:.6g})²)", gopt.damp_kn));
  }
  print_item("GSIS: 収束判定", std::format("式 (59) の ε < {:.3g}(上限 {} 回、最低 {} 回)", tol, max_iter, min_iter));
  print_item("質量の釣り合い", std::format("許容 {:.1e}({})", cfg.gsis.mass_balance_tolerance,
                                           cfg.gsis.mass_balance_required ? "収束の判定に加える" : "超えたら警告"));
  if (!gopt.sections.empty()) {
    std::string pos;
    for (const SectionCut& c : solver.section_cuts()) {
      pos += std::format("{}{:.4g}({} 面)", pos.empty() ? "" : "、", c.face_position, c.faces.size());
    }
    print_item("質量流量の断面", std::format("{} = {}", section_axis_name(gopt.sections.axis), pos));
  }
  print_item("PBC: 移流項・線形ソルバー", std::format("{}・{} + {}(相対 {:.3g}、上限 {} 回)", mp.advection, mp.linear_solver,
                                                     mp.preconditioner, mp.linear_rtol, mp.linear_max_iterations));
  print_item("PBC: 境界面の再構成・ヤコビアン", std::format("{}・{}", mp.boundary_reconstruction, mp.boundary_jacobian));
  print_item("PBC: OpenMP のスレッド数", std::format("{}", macro::openmp_max_threads()));
  print_item("初期化", opt.init_from.empty()
                           ? std::format("一様(p = {:.6g} p_ref、T = {:.6g} T0)→ PBC 単独(NSF){} 回まで → f^eq",
                                         kp.initial_pressure, kp.initial_temperature, gopt.init_iterations)
                           : std::format("{} のセル値の平衡分布", opt.init_from));
  print_item("閉じた領域", s.closed_domain ? "はい(全質量を初期値に保つ)" : "いいえ");
  print_item("出力先", path_to_utf8(out_dir));
  print_item("前処理の時間", std::format("{:.3f} s", setup_s));
#if GSIS_WITH_CUDA
  std::size_t gpu_used_bytes = 0;
  if (kp.backend == "cuda" && gpu::device_count() > 0) {
    const gpu::MemoryInfo mem = gpu::memory_info(0);
    gpu_used_bytes = mem.total - mem.free;
    print_item("GPU のメモリ(全体 − 空き)", std::format("{} / {}(cudaMemGetInfo。WDDM では他の用途を含む)",
                                                       format_bytes(static_cast<double>(gpu_used_bytes)),
                                                       format_bytes(static_cast<double>(mem.total))));
  }
#endif

  PbcHistoryWriter pbc_history(out_dir / "pbc_history.csv", lm.mesh);
  const std::size_t gpu_memory_bytes = kp.backend == "cuda" ? solver.kinetic().backend().memory_bytes() : 0;
  GsisHistoryWriter history(out_dir / "history.csv", s, gpu_memory_bytes, gopt.sections.positions.size());
  long long current = 0;
  auto on_pbc = [&](const PbcIterationInfo& it) { pbc_history.write(current, it); };

  // 初期化
  auto should_stop = [&out_dir]() { return stop_requested(out_dir); };
  const GsisInitReport& init = [&]() -> const GsisInitReport& {
    if (opt.init_from.empty()) return solver.initialize(nullptr, true, on_pbc, should_stop);
    const CellMacroState w0 = read_kinetic_cells_csv(path_from_utf8(opt.init_from), lm.mesh.n_cells);
    return solver.initialize(&w0, false, on_pbc, should_stop);
  }();
  if (!opt.save_init.empty()) {
    // 初期化の状態(σ = q = 0)を cells.csv の形で書く。--init-from で読むと同じ平衡分布から始まる
    KineticMoments m0;
    m0.state = solver.state();
    m0.sigma.assign(m0.state.size(), std::array<double, 6>{});
    m0.q.assign(m0.state.size(), std::array<double, 3>{});
    write_kinetic_cells_csv(path_from_utf8(opt.save_init), lm.mesh, lm.geometry, m0, s.length_scale, {});
  }
  {
    std::string msg = std::format("{:.2f} s", init.seconds);
    if (init.pbc_run) {
      msg = std::format("PBC 単独 {} 回({}、最後の更新量 {:.2e})、{:.2f} s", init.pbc.iterations,
                        init.pbc.converged ? "収束" : "上限", init.pbc.final_update, init.seconds);
      const TubeMassFlow m = tube_mass_flow(s, init.patch_mass_flow);
      if (m.has_inlet && m.has_outlet) msg += std::format("、NSF の Ṁ 入口 {:.5f} 出口 {:.5f}", m.inlet, m.outlet);
    }
    print_item("初期化の結果", msg);
  }

  // GSIS の反復(STOP があれば初期化の後は反復せずに結果を書く)
  bool converged = false;
  bool stopped = init.pbc.stopped || stop_requested(out_dir);
  double sum_cis = 0.0, sum_moments = 0.0, sum_pbc = 0.0, sum_corr = 0.0, sum_total = 0.0;
  long long pbc_iterations = 0, pbc_lin_pu = 0, pbc_lin_T = 0, pbc_not_conv = 0, pbc_rejected = 0, pbc_stage_conv = 0;
  long long replaced_total = 0, replaced_first = 0, replaced_iters = 0;
  while (!stopped) {
    current = solver.iteration() + 1;
    const GsisIterationInfo& it = solver.iterate(on_pbc);
    history.write(it, total_timer.seconds());
    sum_cis += it.t_cis;
    sum_moments += it.t_moments;
    sum_pbc += it.t_pbc;
    sum_corr += it.t_correction;
    sum_total += it.t_total;
    pbc_iterations += it.pbc.iterations;
    pbc_lin_pu += it.pbc.linear_iterations_pu;
    pbc_lin_T += it.pbc.linear_iterations_T;
    pbc_not_conv += it.pbc.linear_not_converged;
    pbc_rejected += it.pbc.rejected_iterations;
    if (it.pbc.converged) ++pbc_stage_conv;
    replaced_total += it.replaced_cells;
    if (it.iteration == 1) replaced_first = it.replaced_cells;
    if (it.replaced_cells > 0) ++replaced_iters;
    if (print_every > 0 && (it.iteration % print_every == 0 || it.iteration <= 3)) {
      print_iteration(it, s, total_timer.seconds());
    }
    if (opt.dump_every > 0 && it.iteration % opt.dump_every == 0) {
      write_diag_csv(out_dir / std::format("diag_{:06}.csv", it.iteration), lm.mesh, lm.geometry, s.length_scale,
                     solver.half_state(), solver.state(), solver.macro().last_hot(), solver.macro().initial_residual(),
                     solver.macro().initial_mass_throughput(), solver.kinetic().replaced_cells());
    }
    if (opt.vtu_every > 0 && !opt.no_vtu && it.iteration % opt.vtu_every == 0) {
      const HigherOrderTerms hot = solver.macro().last_hot();
      write_kinetic_cell_vtu(out_dir / std::format("fields_{:06}.vtu", it.iteration), lm.mesh,
                             solver.kinetic().moments(), s, solver.kinetic().residual_per_cell(), &hot);
    }
    // 収束の判定(式 (59))。mass_balance_required なら CIS の後の f^{n+1/2} の境界の質量の釣り合いも見る(フェーズ 5c)
    const MassBalance mb_half = mass_balance(it.patch_mass_flow_kinetic);
    const bool balance_ok = !cfg.gsis.mass_balance_required || !mb_half.valid ||
                            std::abs(mb_half.imbalance) < cfg.gsis.mass_balance_tolerance;
    if (it.iteration >= min_iter && it.change.epsilon < tol && balance_ok) {
      converged = true;
      break;
    }
    if (it.iteration >= max_iter) break;
    if (stop_requested(out_dir)) {
      // 穏やかな停止: いまの状態で結果を書いて終える
      stopped = true;
      log_info("{} があるので GSIS の反復 {} で止めます", kStopFileName, it.iteration);
    }
  }
  const GsisIterationInfo last = solver.last();
  const double wall_s = total_timer.seconds();

  // 結果: 補正した後の f(= 収束した解)のモーメントと境界の流束
  Timer output_timer;
  const KineticMoments moments = solver.kinetic().moments();
  const BoundaryKineticFluxes fluxes = solver.kinetic().boundary_fluxes();
  const HigherOrderTerms hot = solver.macro().last_hot();
  const std::vector<double> residual_cell = solver.kinetic().residual_per_cell();
  if (!opt.no_vtu) {
    write_kinetic_cell_vtu(out_dir / "fields.vtu", lm.mesh, moments, s, residual_cell, &hot);
    write_kinetic_boundary_vtu(out_dir / "boundary.vtu", lm.mesh, lm.geometry, fluxes, s);
  }
  if (opt.cells_csv) write_kinetic_cells_csv(out_dir / "cells.csv", lm.mesh, lm.geometry, moments, s.length_scale,
                                             residual_cell);
  KineticPatchSummary ps = summarize_kinetic_patches(lm.mesh, lm.geometry, fluxes, s);
  const TubeMassFlow m_pbc = tube_mass_flow(s, last.patch_mass_flow_pbc);
  const TubeMassFlow m_half = tube_mass_flow(s, last.patch_mass_flow_kinetic);
  const long long n_iter = last.iteration;
  // 質量の釣り合いと断面の質量流量(フェーズ 5c): 最後の f(補正の後)、CIS の後の f^{n+1/2}、PBC の W^{n+1}
  const std::vector<double> sec_final = solver.kinetic().section_mass_flow(gopt.sections);
  BalanceReport balance;
  balance.tolerance = cfg.gsis.mass_balance_tolerance;
  balance.required = cfg.gsis.mass_balance_required;
  balance.entries.push_back(make_balance_entry("kinetic", "運動論(最後の f)", ps.mdot, sec_final));
  balance.entries.push_back(make_balance_entry("kinetic_half", "運動論(CIS の後の f^{n+1/2})",
                                               last.patch_mass_flow_kinetic, last.section_flow_kinetic, false));
  balance.entries.push_back(make_balance_entry("pbc", "PBC(W^{n+1})", last.patch_mass_flow_pbc, last.section_flow_pbc));
  evaluate_balance(balance);

  json::Value root;
  root.set("program", build_description());
  root.set("case", cfg.case_info.name);
  root.set("case_file", opt.case_file);
  root.set("mesh", path_to_utf8(cfg.mesh_path()));
  root.set("mode", "gsis");
  root.set("n_cells", static_cast<std::int64_t>(s.n_cells()));
  root.set("n_boundary_faces", static_cast<std::int64_t>(s.n_bfaces()));
  root.set("n_velocity", static_cast<std::int64_t>(s.nv()));
  root.set("Kn", s.derived.Kn);
  root.set("delta", s.derived.delta_from_Kn);
  root.set("iterations", static_cast<std::int64_t>(n_iter));
  root.set("cis_iterations", static_cast<std::int64_t>(last.cis_iterations));
  root.set("converged", converged);
  root.set("stopped", stopped);
  root.set("tolerance", tol);
  root.set("epsilon", finite_or_null(last.change.epsilon));
  root.set("eps_rho", finite_or_null(last.change.rho));
  root.set("eps_u", finite_or_null(last.change.u));
  root.set("eps_T", finite_or_null(last.change.T));
  root.set("eps_half", finite_or_null(last.change_half.epsilon));
  root.set("pbc_change", finite_or_null(last.pbc_change.epsilon));
  root.set("wall_time_s", wall_s);
  root.set("setup_time_s", setup_s);
  root.set("time_per_iteration_s", n_iter > 0 ? sum_total / static_cast<double>(n_iter) : 0.0);
  {
    json::Value t;
    t.set("initialization", init.seconds);
    t.set("cis", sum_cis);
    t.set("moments", sum_moments);
    t.set("pbc", sum_pbc);
    t.set("correction", sum_corr);
    t.set("transfer", sum_moments + sum_corr);
    t.set("iterations_total", sum_total);
    root.set("time_breakdown_s", std::move(t));
  }
  {
    json::Value t;
    const double k = n_iter > 0 ? 1.0 / static_cast<double>(n_iter) : 0.0;
    t.set("cis", sum_cis * k);
    t.set("moments", sum_moments * k);
    t.set("pbc", sum_pbc * k);
    t.set("correction", sum_corr * k);
    t.set("total", sum_total * k);
    root.set("time_per_iteration_breakdown_s", std::move(t));
  }
  {
    json::Value j;
    j.set("pbc_run", init.pbc_run);
    j.set("from_state", init.from_state);
    if (!opt.init_from.empty()) j.set("init_from", opt.init_from);
    j.set("pbc_iterations", static_cast<std::int64_t>(init.pbc.iterations));
    j.set("pbc_converged", init.pbc.converged);
    j.set("pbc_final_update", finite_or_null(init.pbc.final_update));
    j.set("time_s", init.seconds);
    if (init.pbc_run) {
      const TubeMassFlow m = tube_mass_flow(s, init.patch_mass_flow);
      if (m.has_inlet || m.has_outlet) j.set("mass_flow_nsf", tube_mass_flow_value(m, s.derived.mass_flow_scale));
    }
    root.set("initialization", std::move(j));
  }
  {
    json::Value g;
    g.set("n_cis", static_cast<std::int64_t>(gopt.n_cis));
    g.set("max_pbc", static_cast<std::int64_t>(gopt.max_pbc));
    g.set("cfl_pbc", gopt.cfl_pbc);
    g.set("max_iterations", static_cast<std::int64_t>(max_iter));
    g.set("min_iterations", static_cast<std::int64_t>(min_iter));
    g.set("init_iterations", static_cast<std::int64_t>(gopt.init_iterations));
    g.set("u_floor", gopt.u_floor);
    g.set("filter_length", gopt.filter_length);
    g.set("filter_max_length", gopt.filter_max_length);
    g.set("filter_min_cells", gopt.filter_min_cells);
    g.set("damp_kn", gopt.damp_kn);
    g.set("mass_balance_tolerance", cfg.gsis.mass_balance_tolerance);
    g.set("mass_balance_required", cfg.gsis.mass_balance_required);
    root.set("gsis", std::move(g));
  }
  {
    json::Value p;
    p.set("iterations_total", static_cast<std::int64_t>(pbc_iterations));
    p.set("stages_converged", static_cast<std::int64_t>(pbc_stage_conv));
    p.set("linear_iterations_pu_total", static_cast<std::int64_t>(pbc_lin_pu));
    p.set("linear_iterations_T_total", static_cast<std::int64_t>(pbc_lin_T));
    p.set("linear_not_converged", static_cast<std::int64_t>(pbc_not_conv));
    p.set("rejected_iterations", static_cast<std::int64_t>(pbc_rejected));
    p.set("last_stage_iterations", static_cast<std::int64_t>(last.pbc.iterations));
    p.set("last_stage_final_update", finite_or_null(last.pbc.final_update));
    p.set("last_stage_final_residual_mass", finite_or_null(last.pbc.final_residual_mass));
    root.set("pbc", std::move(p));
  }
  {
    json::Value r;
    r.set("total_cells", static_cast<std::int64_t>(replaced_total));
    r.set("first_iteration", static_cast<std::int64_t>(replaced_first));
    r.set("iterations_with_replacement", static_cast<std::int64_t>(replaced_iters));
    r.set("last_iteration", static_cast<std::int64_t>(last.replaced_cells));
    root.set("negative_replacement", std::move(r));
  }
  root.set("hot_sigma_rms", last.hot_sigma_rms);
  root.set("hot_q_rms", last.hot_q_rms);
  root.set("backend", solver.kinetic().backend().name());
  root.set("precision", kp.precision);
  root.set("memory_bytes", static_cast<std::int64_t>(solver.kinetic().backend().memory_bytes()));
#if GSIS_WITH_CUDA
  if (gpu_used_bytes > 0) root.set("gpu_used_bytes_after_setup", static_cast<std::int64_t>(gpu_used_bytes));
#endif
  root.set("velocity_chunk", static_cast<std::int64_t>(solver.kinetic().backend().chunk_width()));
  root.set("sweep_blocks", static_cast<std::int64_t>(s.n_blocks()));
  root.set("cell_ordering", kp.cell_ordering);
  root.set("block_cut_faces", static_cast<std::int64_t>(block_stats.n_cut_faces));
  root.set("threads", static_cast<std::int64_t>(macro::openmp_max_threads()));
  {
    json::Value p;
    p.set("pseudo_cfl", kp.pseudo_cfl);
    p.set("pseudo_time", kp.pseudo_time);
    p.set("limiter", kp.limiter);
    p.set("limiter_k", kp.limiter_k);
    p.set("limiter_eps", kp.limiter_eps);
    p.set("lsq_weight_power", kp.lsq_weight_power);
    p.set("cell_ordering", kp.cell_ordering);
    p.set("initial_pressure", kp.initial_pressure);
    p.set("initial_temperature", kp.initial_temperature);
    p.set("convergence_u_floor", kp.convergence_u_floor);
    p.set("wall_relaxation", kp.wall_relaxation);
    p.set("boundary_iterations", static_cast<std::int64_t>(kp.boundary_iterations));
    p.set("interface_damping", kp.interface_damping);
    p.set("gsis_negative_check", kp.gsis_negative_check);
    p.set("positivity_limiter", kp.positivity_limiter);
    p.set("sweep_order", kp.sweep_order);
    p.set("inner_sweeps", static_cast<std::int64_t>(kp.inner_sweeps));
    p.set("inner_wall_update", kp.inner_wall_update);
    p.set("block_passes", static_cast<std::int64_t>(kp.block_passes));
    p.set("equilibrium", kp.equilibrium);
    p.set("deq_fallback", kp.deq_fallback);
    p.set("pressure_boundary_reconstruction", kp.pressure_boundary_reconstruction);
    p.set("cold_bgk_temperature", kp.cold_bgk_temperature);
    root.set("params", std::move(p));
  }
  {
    json::Value p;
    p.set("advection", mp.advection);
    p.set("linear_solver", mp.linear_solver);
    p.set("preconditioner", mp.preconditioner);
    p.set("linear_rtol", mp.linear_rtol);
    p.set("linear_max_iterations", static_cast<std::int64_t>(mp.linear_max_iterations));
    p.set("pbc_tolerance", mp.pbc_tolerance);
    p.set("limiter_k", mp.limiter_k);
    p.set("boundary_reconstruction", mp.boundary_reconstruction);
    p.set("boundary_jacobian", mp.boundary_jacobian);
    p.set("standalone_boundary_reconstruction", mp.standalone_boundary_reconstruction);
    p.set("standalone_face_state", mp.standalone_face_state);
    p.set("init_iterations", static_cast<std::int64_t>(mp.init_iterations));
    p.set("init_advection", mp.init_advection);
    p.set("hot_interpolation", mp.hot_interpolation);
    p.set("gsis_face_state", mp.gsis_face_state);
    p.set("gsis_continuity_pseudo_time", mp.gsis_continuity_pseudo_time);
    p.set("gsis_freeze_kn", mp.gsis_freeze_kn);
    p.set("gsis_cfl_start", mp.gsis_cfl_start);
    p.set("gsis_cfl_growth", mp.gsis_cfl_growth);
    p.set("gsis_freeze_tmin", mp.gsis_freeze_tmin);
    p.set("gsis_hot_kn", mp.gsis_hot_kn);
    root.set("macro_params", std::move(p));
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
    json::Value mf = tube_mass_flow_json(s, ps.mdot_in, ps.has_in, ps.mdot_out, ps.has_out);
    if (!mf.is_null()) root.set("mass_flow", std::move(mf));
  }
  {
    // PBC の最後の状態 W^{n+1} の境界フラックスと、CIS の後の f^{n+1/2} の境界の流束(参考)
    json::Value pp;
    for (std::size_t p = 0; p < lm.mesh.patches.size(); ++p) {
      json::Value e;
      e.set("name", lm.mesh.patches[p].name);
      e.set("mdot_pbc_nd", p < last.patch_mass_flow_pbc.size() ? finite_or_null(last.patch_mass_flow_pbc[p])
                                                               : json::Value(nullptr));
      e.set("mdot_half_nd", p < last.patch_mass_flow_kinetic.size()
                                ? finite_or_null(last.patch_mass_flow_kinetic[p])
                                : json::Value(nullptr));
      pp.push_back(std::move(e));
    }
    root.set("patches_last_iteration", std::move(pp));
    if (s.derived.has_p_in && (m_pbc.has_inlet || m_pbc.has_outlet)) {
      root.set("mass_flow_pbc", tube_mass_flow_value(m_pbc, s.derived.mass_flow_scale));
      root.set("mass_flow_half", tube_mass_flow_value(m_half, s.derived.mass_flow_scale));
    }
  }
  root.set("mass_balance", balance_json(balance, s));
  if (!gopt.sections.empty()) {
    root.set("mass_flow_sections",
             sections_json(gopt.sections, solver.section_cuts(),
                           {{"kinetic", sec_final}, {"kinetic_half", last.section_flow_kinetic}, {"pbc", last.section_flow_pbc}},
                           s));
  }
  root.set("residual", last.kinetic.has_residual && std::isfinite(last.kinetic.residual) ? last.kinetic.residual : 0.0);
  root.set("output_time_s", output_timer.seconds());
  write_file(out_dir / "summary.json", json::dump(root, 2) + "\n");

  print_heading("結果");
  print_item("GSIS の反復", std::format("{}({}、CIS 反復の合計 {})", n_iter,
                                        converged ? "収束" : (stopped ? "STOP で停止" : "上限に達した"),
                                        last.cis_iterations));
  print_item("ε", std::format("{:.3e}", last.change.epsilon));
  print_item("時間", std::format("{:.2f} s(初期化 {:.2f} s、1 反復 {:.2f} s: CIS {:.2f}・PBC {:.2f}・転送 {:.2f})", wall_s,
                                 init.seconds, n_iter > 0 ? sum_total / n_iter : 0.0,
                                 n_iter > 0 ? sum_cis / n_iter : 0.0, n_iter > 0 ? sum_pbc / n_iter : 0.0,
                                 n_iter > 0 ? (sum_moments + sum_corr) / n_iter : 0.0));
  print_item("負値の置換", std::format("合計 {} セル(最初の反復 {}、置換のあった反復 {})", replaced_total, replaced_first,
                                       replaced_iters));
  if (s.derived.has_p_in && ps.has_in && ps.has_out) {
    const double scale = s.derived.mass_flow_scale;
    print_item("無次元質量流量 Ṁ", std::format("入口 {:.5f}、出口 {:.5f}(PBC: 入口 {:.5f}、出口 {:.5f})",
                                               mass_flow_to_si(s, ps.mdot_in) / scale,
                                               mass_flow_to_si(s, ps.mdot_out) / scale, m_pbc.inlet, m_pbc.outlet));
  }
  print_balance(balance, gopt.sections, solver.section_cuts(), s);
  if (converged && !balance.warnings.empty()) {
    log_warn("式 (59) の判定(ε < {:.3g})は満たしましたが、質量の釣り合いが許容 {:.1e} を超えています(summary.json の "
             "mass_balance)",
             tol, balance.tolerance);
  }
  print_item("出力", path_to_utf8(out_dir));
  if (!converged && !stopped) {
    log_warn("GSIS の反復の上限 {} 回で収束しませんでした(ε = {:.3e})", max_iter, last.change.epsilon);
  }
  return 0;
}

}  // namespace

int cmd_run(std::span<const std::string> args) {
  const RunOptions opt = parse_options(args);
  CaseConfig cfg = load_case(path_from_utf8(opt.case_file));
  apply_overrides(cfg, opt);
  const bool gsis_mode = opt.mode.empty() ? cfg.case_info.mode == SolverMode::Gsis : opt.mode == "gsis";
  const std::filesystem::path out_dir =
      opt.out_dir.empty() ? std::filesystem::path("runs") / "gsis" / path_from_utf8(cfg.case_info.name)
                          : path_from_utf8(opt.out_dir);
  if (!gsis_mode) return run_cis_mode(opt, cfg, out_dir);
  return run_gsis_mode(opt, cfg, out_dir);
}

}  // namespace gsis::cli
