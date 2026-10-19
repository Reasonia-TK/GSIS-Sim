// check-case: ケース定義(TOML)とメッシュを読み、設定・導出量・メモリ量を表示する。

#include <algorithm>
#include <cmath>
#include <format>
#include <string>
#include <vector>
#include <charconv>
#include <cstdio>

#include "app/cli.hpp"
#include "config/case_config.hpp"
#include "config/case_mesh_check.hpp"
#include "core/error.hpp"
#include "core/math.hpp"
#include "core/paths.hpp"
#include "core/json.hpp"
#include "core/log.hpp"
#include "kinetic/memory_estimate.hpp"
#include "macro/macro_params.hpp"
#include "mesh/mesh_quality.hpp"
#include "mesh/pmesh_io.hpp"
#include "physics/maxwell.hpp"
#include "physics/moments.hpp"
#include "velocity/velocity_grid.hpp"

#if GSIS_WITH_CUDA
#include "gpu/device_info.hpp"
#endif

namespace gsis::cli {
namespace {

std::string si(double v, std::string_view unit) { return std::format("{:.6g} {}", v, unit); }

// JSON 経路は途中の入力・メッシュエラーでも、それまでに求まった項目を保持する。
int check_json(std::span<const std::string> args) {
  json::Value root, warnings(json::Array{}), errors(json::Array{});
  root.set("schema_version", 1);
  root.set("mesh_loaded", false);
  root.set("memory", nullptr);
  int code = 0;
  set_log_sink([&](LogLevel level, std::string_view message) {
    if (level == LogLevel::Warn) warnings.push_back(std::string(message));
    else if (level == LogLevel::Error) std::fprintf(stderr, "%.*s\n", static_cast<int>(message.size()), message.data());
  });
  try {
    std::string file;
    bool no_mesh = false;
    std::int64_t free_bytes = static_cast<std::int64_t>(14.7 * (1LL << 30));
    std::string free_source = "default_14.7_GiB";
    bool explicit_free = false;
    for (std::size_t i = 0; i < args.size(); ++i) {
      const auto& a = args[i];
      if (a == "--json") continue;
      if (a == "--no-mesh") { no_mesh = true; continue; }
      if (a == "--free-vram-bytes") {
        if (++i == args.size()) throw UsageError{"--free-vram-bytes の値がありません"};
        const auto& s = args[i];
        const auto [end, ec] = std::from_chars(s.data(), s.data() + s.size(), free_bytes);
        if (ec != std::errc{} || end != s.data() + s.size() || free_bytes <= 0)
          throw UsageError{"--free-vram-bytes は正の整数です"};
        explicit_free = true;
        free_source = "argument";
      } else if (!a.empty() && a[0] == '-') throw UsageError{"知らないオプション " + a + " です"};
      else {
        if (!file.empty()) throw UsageError{"ケースファイルは 1 つだけ指定してください"};
        file = a;
      }
    }
    if (file.empty()) throw UsageError{"ケースファイル(.toml)を指定してください"};
    const CaseConfig cfg = load_case(path_from_utf8(file));
    const auto d = derive_quantities(cfg);
    const auto kp = parse_kinetic_params(cfg);
    (void)parse_macro_params(cfg);
    const auto& ref = d.ref;
    json::Value input, gas, conditions, derived, reference, grid;
    input.set("name", cfg.case_info.name);
    input.set("mode", to_string(cfg.case_info.mode));
    input.set("case_file", file);
    input.set("mesh", path_to_utf8(cfg.mesh_path()));
    gas.set("molecular_mass_kg", cfg.gas.molecular_mass);
    gas.set("mu_ref_Pa_s", cfg.gas.mu_ref);
    gas.set("T_ref_K", cfg.gas.T_ref);
    gas.set("omega", cfg.gas.omega);
    gas.set("Pr", cfg.gas.Pr);
    gas.set("gamma", cfg.gas.gamma);
    input.set("gas", std::move(gas));
    conditions.set("L_ref_m", cfg.conditions.L_ref);
    conditions.set("T0_K", cfg.conditions.T0);
    conditions.set("delta_rp", cfg.conditions.delta_rp ? json::Value(*cfg.conditions.delta_rp) : json::Value());
    conditions.set("Kn", cfg.conditions.Kn ? json::Value(*cfg.conditions.Kn) : json::Value());
    conditions.set("p_ref_Pa", cfg.conditions.p_ref ? json::Value(*cfg.conditions.p_ref) : json::Value());
    input.set("conditions", std::move(conditions));
    root.set("input", std::move(input));
    derived.set("R_J_kg_K", d.R);
    derived.set("mu_Pa_s", d.mu0);
    derived.set("mean_free_path_m", d.mean_free_path);
    derived.set("Kn", d.Kn);
    derived.set("delta_rp", d.delta_from_Kn);
    root.set("derived", std::move(derived));
    reference.set("length_m", ref.length);
    reference.set("temperature_K", ref.temperature);
    reference.set("pressure_Pa", ref.pressure);
    reference.set("density_kg_m3", ref.density);
    reference.set("velocity_m_s", ref.velocity);
    reference.set("time_s", ref.time);
    reference.set("distribution_kg_s3_m6", ref.distribution);
    reference.set("viscosity_Pa_s", ref.viscosity);
    reference.set("heat_flux_W_m2", ref.heat_flux);
    reference.set("mass_flux_kg_m2_s", ref.mass_flux);
    reference.set("mass_flow_scale_kg_s", d.mass_flow_scale);
    root.set("reference", std::move(reference));
    grid.set("auto", cfg.velocity_grid.automatic);
    json::Value counts(json::Array{}), ranges(json::Array{}), ranges_si(json::Array{}), steps(json::Array{});
    std::int64_t nv = 1;
    for (int a = 0; a < 3; ++a) {
      const auto& v = cfg.velocity_grid;
      counts.push_back(v.n[a]);
      ranges.push_back(json::Array{v.lo[a], v.hi[a]});
      ranges_si.push_back(json::Array{v.lo[a] * ref.velocity, v.hi[a] * ref.velocity});
      steps.push_back((v.hi[a] - v.lo[a]) / v.n[a]);
      nv *= v.n[a];
    }
    grid.set("n", std::move(counts));
    grid.set("range", std::move(ranges));
    grid.set("range_m_s", std::move(ranges_si));
    grid.set("step", std::move(steps));
    grid.set("n_velocity", nv);
    if (cfg.velocity_grid.automatic) {
      grid.set("T_min_K", cfg.velocity_grid.T_min);
      grid.set("T_max_K", cfg.velocity_grid.T_max);
      grid.set("u_max_m_s", cfg.velocity_grid.u_max);
    }
    root.set("velocity_grid", std::move(grid));
    json::Value patches(json::Array{});
    for (const auto& b : cfg.boundaries) {
      json::Value p;
      p.set("name", b.patch);
      p.set("type", to_string(b.type));
      p.set("faces", nullptr);
      p.set("area_m2", nullptr);
      p.set("temperature_K", b.temperature ? json::Value(*b.temperature) : json::Value());
      p.set("pressure_Pa", b.pressure ? json::Value(*b.pressure) :
            b.type == BoundaryType::PressureInlet ? json::Value(d.p_in) : json::Value());
      p.set("velocity_m_s", b.velocity ? json::Value(json::Array{b.velocity->x, b.velocity->y, b.velocity->z}) : json::Value());
      if (b.type == BoundaryType::PressureInlet) p.set("tangential_velocity", b.inlet_zero_tangential ? "zero" : "extrapolate");
      patches.push_back(std::move(p));
    }
    root.set("patches", std::move(patches));
    if (!no_mesh) {
      const auto lm = load_pmesh(cfg.mesh_path());
      root.set("mesh_loaded", true);
      json::Value mesh;
      mesh.set("n_cells", lm.mesh.n_cells);
      mesh.set("n_faces", lm.mesh.n_faces());
      mesh.set("volume_m3", lm.report.total_volume);
      root.set("mesh", std::move(mesh));
      // at() は読み取り専用なので、面情報を加えた配列を作り直す。
      json::Value pp = root.at("patches");
      const auto summaries = summarize_patches(lm.mesh, lm.geometry);
      for (auto& p : pp.as_array()) for (const auto& s : summaries) {
        if (p.at("name").as_string() == s.name) { p.set("faces", s.n_faces); p.set("area_m2", s.area); }
      }
      root.set("patches", std::move(pp));
      const VelocityGrid vgrid(cfg.velocity_grid.n, cfg.velocity_grid.lo, cfg.velocity_grid.hi);
      for (const auto& e : check_case_against_mesh(cfg, lm.mesh, lm.geometry, vgrid)) errors.push_back(e);
#if GSIS_WITH_CUDA
      if (!explicit_free && gpu::device_count() > 0) {
        free_bytes = static_cast<std::int64_t>(gpu::memory_info(0).free);
        free_source = "cuda_device_0";
      }
#else
      (void)explicit_free;
#endif
      if (cfg.case_info.mode == SolverMode::Nsf) {
        json::Value memory;
        for (const auto* key : {"distribution", "workspace", "overhead", "chunk", "total"}) memory.set(key, 0);
        memory.set("free", free_bytes);
        memory.set("reserve", 1024LL * 1024 * 1024);
        memory.set("budget", std::max(0LL, free_bytes - 1024LL * 1024 * 1024));
        memory.set("fits", true);
        memory.set("model", "nsf_cpu");
        memory.set("free_source", free_source);
        root.set("memory", std::move(memory));
      } else if (errors.as_array().empty()) {
        const auto setup = kinetic::build_kinetic_setup(cfg, lm.mesh, lm.geometry, kp);
        auto memory = kinetic::estimate_device_memory(setup, free_bytes);
        memory.set("free_source", free_source);
        if (!memory.at("fits").as_bool()) warnings.push_back("VRAM 予算に収まりません。セル数または速度点数を減らしてください");
        root.set("memory", std::move(memory));
      }
    }
  } catch (const UsageError& e) { errors.push_back(e.message); code = 2; }
    catch (const std::exception& e) { errors.push_back(e.what()); code = 1; }
  set_log_sink({});
  if (!errors.as_array().empty() && code == 0) code = 1;
  root.set("ok", code == 0);
  root.set("errors", std::move(errors));
  root.set("warnings", std::move(warnings));
  std::string output;
  try {
    output = json::dump(root) + "\n";
  } catch (const std::exception& e) {
    // 有限の入力でも導出計算がオーバーフローする場合がある。出力自体の失敗もJSONにする。
    json::Value failure;
    failure.set("schema_version", 1);
    failure.set("ok", false);
    failure.set("errors", json::Array{std::string(e.what())});
    failure.set("warnings", root.at("warnings"));
    output = json::dump(failure) + "\n";
    code = 1;
  }
  std::fwrite(output.data(), 1, output.size(), stdout);
  return code;
}

}  // namespace

int cmd_check_case(std::span<const std::string> args) {
  if (std::find(args.begin(), args.end(), "--json") != args.end()) return check_json(args);
  std::string case_file;
  for (const std::string& a : args) {
    if (!a.empty() && a[0] == '-') throw UsageError{"知らないオプション " + a + " です"};
    if (!case_file.empty()) throw UsageError{"ケースファイルは 1 つだけ指定してください"};
    case_file = a;
  }
  if (case_file.empty()) throw UsageError{"ケースファイル(.toml)を指定してください"};

  const CaseConfig cfg = load_case(path_from_utf8(case_file));
  const DerivedQuantities d = derive_quantities(cfg);
  const VelocityGrid vgrid(cfg.velocity_grid.n, cfg.velocity_grid.lo, cfg.velocity_grid.hi);
  const LoadedMesh lm = load_pmesh(cfg.mesh_path());
  const PolyMesh& mesh = lm.mesh;
  const std::vector<PatchSummary> patches = summarize_patches(mesh, lm.geometry);

  // 境界条件とパッチの対応、対称面の向き
  const std::vector<std::string> errors = check_case_against_mesh(cfg, mesh, lm.geometry, vgrid);
  if (!errors.empty()) {
    std::string msg = "ケースとメッシュが合いません:";
    for (const std::string& e : errors) msg += "\n  " + e;
    throw Error(msg);
  }

  const ConditionsSection& c = cfg.conditions;
  const GasSection& gas = cfg.gas;
  const ReferenceScales& ref = d.ref;

  print_heading("ケース");
  print_item("名前", cfg.case_info.name);
  print_item("モード", std::string(to_string(cfg.case_info.mode)));
  print_item("ケースファイル", case_file);
  print_item("メッシュ", path_to_utf8(cfg.mesh_path().lexically_normal()));
  if (!cfg.ignored_sections.empty()) {
    std::string s;
    for (const std::string& n : cfg.ignored_sections) s += (s.empty() ? "[" : ", [") + n + "]";
    print_item("読み飛ばした節", s);
  }

  print_heading("気体");
  print_item("分子質量 m", si(gas.molecular_mass, "kg"));
  print_item("比気体定数 R = k_B/m", si(d.R, "J/(kg K)"));
  print_item("粘性 μ_ref", std::format("{:.6g} Pa s(T_ref = {:.6g} K、ω = {:.6g})", gas.mu_ref,
                                       gas.T_ref, gas.omega));
  print_item("Pr、γ", std::format("{:.6g}、{:.6g}(Shakhov、単原子)", gas.Pr, gas.gamma));

  print_heading("条件と導出量");
  print_item("L_ref", si(c.L_ref, "m"));
  print_item("T0", si(c.T0, "K"));
  if (c.delta_rp) {
    print_item("δ_rp", std::format("{:.6g}", *c.delta_rp));
  } else if (c.Kn) {
    print_item("Kn(指定)", std::format("{:.6g}", *c.Kn));
  } else if (c.p_ref) {
    print_item("p_ref(指定)", si(*c.p_ref, "Pa"));
  }
  print_item("μ0 = μ(T0)", si(d.mu0, "Pa s"));
  print_item("sqrt(2 R T0)", si(d.most_probable_speed, "m/s"));
  if (d.has_p_in) {
    print_item("p_in = δ μ0 √(2RT0)/L_ref", si(d.p_in, "Pa"));
    print_item("ρ_in = p_in/(R T0)", si(d.rho_in, "kg/m^3"));
  } else {
    print_item("p_ref", si(d.p_ref, "Pa"));
    print_item("ρ_ref = p_ref/(R T0)", si(d.rho_in, "kg/m^3"));
  }
  print_item("Kn 式 (8)、基準状態", std::format("{:.6g}", d.Kn));
  if (c.delta_rp) {
    print_item("検算 √π/(2 Kn)", std::format("{:.12g}(δ_rp との差 {:.1e})", d.delta_from_Kn,
                                             d.delta_from_Kn - *c.delta_rp));
  } else {
    print_item("δ = √π/(2 Kn)", std::format("{:.6g}", d.delta_from_Kn));
  }
  print_item("平均自由行程 λ = Kn L_ref", si(d.mean_free_path, "m"));

  print_heading("無次元化の基準量(spec §3.2)");
  print_item("長さ L_ref", si(ref.length, "m"));
  print_item("温度 T_ref = T0", si(ref.temperature, "K"));
  print_item(d.has_p_in ? "圧力 p_ref = p_in" : "圧力 p_ref", si(ref.pressure, "Pa"));
  print_item("密度 p_ref/(R T_ref)", si(ref.density, "kg/m^3"));
  print_item("速度 √(R T_ref)", si(ref.velocity, "m/s"));
  print_item("時間 L_ref/√(R T_ref)", si(ref.time, "s"));
  print_item("分布関数 ρ_ref/(RT0)^1.5", si(ref.distribution, "kg s^3/m^6"));
  print_item("粘性 p_ref L_ref/√(RT0)", si(ref.viscosity, "Pa s"));
  print_item("熱流束 p_ref √(R T_ref)", si(ref.heat_flux, "W/m^2"));
  print_item("無次元の μ(T0)", std::format("{:.6g}{}", d.mu_hat0, c.delta_rp ? "(= 1/(√2 δ_rp))" : ""));
  if (d.has_p_in) {
    print_item("質量流量の基準", std::format("{:.6g} kg/s(√π L_ref² p_in/√(2RT0))", d.mass_flow_scale));
  }
  if (!cfg.module_sections.empty()) {
    std::string s;
    for (const auto& [name, sec] : cfg.module_sections) {
      s += std::format("{}[{}]({} 個のキー)", s.empty() ? "" : "、", name, sec.values.size());
    }
    print_item("モジュールの節", s);
  }

  print_heading("境界条件");
  for (const PatchSummary& s : patches) {
    const BoundarySpec* b = cfg.find_boundary(s.name);
    std::string detail = std::string(to_string(b->type));
    if (b->temperature) detail += std::format("、T = {:.6g} K", *b->temperature);
    if (b->type == BoundaryType::PressureInlet) {
      const double p = b->pressure.value_or(d.p_in);
      detail += std::format("、p = {:.6g} Pa{}", p, b->pressure ? "" : "(p_in)");
    } else if (b->pressure) {
      detail += std::format("、p = {:.6g} Pa", *b->pressure);
    }
    if (b->velocity) {
      detail += std::format("、u_w = ({:.6g}, {:.6g}, {:.6g}) m/s", b->velocity->x, b->velocity->y,
                            b->velocity->z);
    }
    detail += std::format("、{} 面、{:.6g} m^2", format_count(s.n_faces), s.area);
    print_item(s.name, detail);
  }

  print_heading("速度格子(単位 √(R T0))");
  print_item("点数", std::format("{} × {} × {} = {}", vgrid.n(0), vgrid.n(1), vgrid.n(2),
                                 format_count(vgrid.size())));
  print_item("範囲", std::format("[{:.6g}, {:.6g}] × [{:.6g}, {:.6g}] × [{:.6g}, {:.6g}]", vgrid.lo(0),
                                 vgrid.hi(0), vgrid.lo(1), vgrid.hi(1), vgrid.lo(2), vgrid.hi(2)));
  print_item("刻み Δξ", std::format("({:.6g}, {:.6g}, {:.6g})(中点配置・中点則)", vgrid.delta(0),
                                    vgrid.delta(1), vgrid.delta(2)));
  print_item("鏡映の対応表", std::format("x {}、y {}、z {}", vgrid.symmetric(0) ? "あり" : "なし",
                                         vgrid.symmetric(1) ? "あり" : "なし",
                                         vgrid.symmetric(2) ? "あり" : "なし"));
  {
    // 静止・T0 の Maxwell 分布のモーメントがどれだけ再現されるか(速度格子の打ち切りの目安)
    std::vector<double> f(static_cast<std::size_t>(vgrid.size()));
    const MaxwellParams<double> mp = make_maxwell_params(1.0, Vec3{0.0, 0.0, 0.0}, 1.0, 1.0);
    for (int k = 0; k < vgrid.size(); ++k) f[static_cast<std::size_t>(k)] = maxwellian(mp, vgrid.xi(k));
    const Moments m = compute_moments(vgrid, std::span<const double>(f), 1.0);
    print_item("静止 Maxwell の再現",
               std::format("f^eq(ρ=1, u=0, T=T0) の ρ の誤差 {:.1e}、T の誤差 {:.1e}", m.rho - 1.0, m.T - 1.0));
  }

  print_heading("メッシュ");
  print_item("セル・面", std::format("{} セル、{} 面(境界 {})", format_count(mesh.n_cells),
                                    format_count(mesh.n_faces()), format_count(mesh.n_boundary_faces())));
  print_item("総体積", std::format("{:.6e} m^3(無次元 {:.6g})", lm.report.total_volume,
                                  lm.report.total_volume / (ref.length * ref.length * ref.length)));

  print_heading("GSIS");
  print_item("n_cis、max_pbc", std::format("{}、{}", cfg.gsis.n_cis, cfg.gsis.max_pbc));
  print_item("cfl_pbc、tolerance", std::format("{:.6g}、{:.3g}", cfg.gsis.cfl_pbc, cfg.gsis.tolerance));

  print_heading("メモリ(分布関数 f を 1 本、spec §4.4)");
  const double n_unknowns = static_cast<double>(mesh.n_cells) * vgrid.size();
  print_item("セル × 速度点", std::format("{} × {} = {:.4g}", format_count(mesh.n_cells),
                                         format_count(vgrid.size()), n_unknowns));
  print_item("FP32", format_bytes(4.0 * n_unknowns));
  print_item("FP64", format_bytes(8.0 * n_unknowns));
  const int chunk = std::min(2048, vgrid.size());
  const double work = 4.0 * 4.0 * static_cast<double>(mesh.n_cells) * chunk;
  print_item("参考: 作業領域",
             std::format("{}(速度チャンク {} 点 × 4 配列、FP32)", format_bytes(work), chunk));
#if GSIS_WITH_CUDA
  if (gpu::device_count() > 0) {
    const gpu::MemoryInfo mem = gpu::memory_info(0);
    const double need = 4.0 * n_unknowns + work;
    print_item("GPU 0 の空き", std::format("{}(f + 作業領域 = {} → {})", format_bytes(static_cast<double>(mem.free)),
                                            format_bytes(need),
                                            need < static_cast<double>(mem.free) ? "収まる" : "収まらない"));
  } else {
    print_item("GPU", "CUDA デバイスが見つかりません");
  }
#else
  print_item("GPU", "CUDA なしでビルドされています");
#endif
  return 0;
}

}  // namespace gsis::cli
