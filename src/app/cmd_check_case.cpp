// check-case: ケース定義(TOML)とメッシュを読み、設定・導出量・メモリ量を表示する。

#include <algorithm>
#include <cmath>
#include <format>
#include <string>
#include <vector>

#include "app/cli.hpp"
#include "config/case_config.hpp"
#include "config/case_mesh_check.hpp"
#include "core/error.hpp"
#include "core/math.hpp"
#include "core/paths.hpp"
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

}  // namespace

int cmd_check_case(std::span<const std::string> args) {
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
