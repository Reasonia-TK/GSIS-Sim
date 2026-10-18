// gpu-info: GPU の情報を表示する。--smoke で GPU と CPU のモーメント計算を比べる。

#include <charconv>
#include <cstring>
#include <format>
#include <string>
#include <vector>

#include "app/cli.hpp"
#include "core/build_info.hpp"
#include "core/timer.hpp"

#if GSIS_WITH_CUDA
#include "gpu/device_info.hpp"
#include "gpu/maxwell_moments.hpp"
#include "physics/moments.hpp"
#include "velocity/velocity_grid.hpp"
#endif

namespace gsis::cli {

#if GSIS_WITH_CUDA
namespace {

// スモークテスト: 管ケースの速度格子(30×24×24、[−10,10]×[−8,8]²、無次元 R = 1)で、
// ランダムな状態の f^eq のモーメントを GPU と CPU で計算して比べる。
int run_smoke(int n_cells) {
  const VelocityGrid grid({30, 24, 24}, {-10.0, -8.0, -8.0}, {10.0, 8.0, 8.0});
  const std::vector<gpu::CellState> cells = gpu::make_random_states(n_cells, 20261015ULL);
  constexpr double R = 1.0;

  print_heading(std::format("スモークテスト(f^eq のモーメント、{} セル × {} 速度点)", format_count(n_cells),
                            format_count(grid.size())));
  bool ok = true;
  for (const gpu::Precision prec : {gpu::Precision::FP64, gpu::Precision::FP32}) {
    const bool fp32 = prec == gpu::Precision::FP32;
    gpu::KernelStats stats;
    const std::vector<Moments> g1 = gpu::maxwell_moments_gpu(grid, cells, R, prec, &stats);
    const std::vector<Moments> g2 = gpu::maxwell_moments_gpu(grid, cells, R, prec);
    Timer t;
    const std::vector<Moments> cpu = gpu::maxwell_moments_cpu(grid, cells, R, prec);
    const double cpu_ms = t.milliseconds();
    const MomentDifference diff = max_moment_difference(cpu, g1, R);
    // 同じ入力で 2 回計算し、ビット単位で同じか(atomic を使わない決定的な集計)
    bool same = true;
    for (std::size_t i = 0; i < g1.size(); ++i) {
      same = same && std::memcmp(&g1[i], &g2[i], sizeof(Moments)) == 0;
    }
    // 厳密な (ρ, u, T) との差(速度格子の打ち切りと中点則の誤差)
    std::vector<Moments> exact(cells.size());
    for (std::size_t i = 0; i < cells.size(); ++i) {
      exact[i].rho = cells[i].rho;
      exact[i].u = cells[i].u;
      exact[i].T = cells[i].T;
      exact[i].p = cells[i].rho * R * cells[i].T;
    }
    const MomentDifference vs_exact = max_moment_difference(exact, g1, R);
    const double tol = fp32 ? 1e-5 : 1e-12;
    const bool pass = diff.max() <= tol && same;
    ok = ok && pass;
    const char* label = fp32 ? "f を FP32 で評価" : "f を FP64 で評価";
    print_item(label, std::format("GPU {:.3f} ms({} 回起動)、CPU {:.1f} ms", stats.kernel_ms,
                                  stats.launches, cpu_ms));
    print_item("  GPU と CPU の差の最大",
               std::format("ρ {:.1e}、u {:.1e}、T {:.1e}、σ {:.1e}、q {:.1e}(許容 {:.0e})", diff.rho, diff.u,
                           diff.T, diff.sigma, diff.q, tol));
    print_item("  2 回の計算の一致", same ? "ビット単位で一致" : "不一致");
    print_item("  厳密値との差の最大", std::format("ρ {:.1e}、u {:.1e}、T {:.1e}", vs_exact.rho,
                                                   vs_exact.u, vs_exact.T));
    print_item("  判定", pass ? "OK" : "NG");
  }
  return ok ? 0 : 1;
}

}  // namespace
#endif

int cmd_gpu_info(std::span<const std::string> args) {
  bool smoke = false;
  int n_cells = 2048;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string& a = args[i];
    if (a == "--smoke") {
      smoke = true;
    } else if (a == "--cells") {
      if (i + 1 >= args.size()) throw UsageError{"--cells の後にセル数が必要です"};
      const std::string& v = args[++i];
      const auto [ptr, ec] = std::from_chars(v.data(), v.data() + v.size(), n_cells);
      if (ec != std::errc() || ptr != v.data() + v.size() || n_cells < 1 || n_cells > 1'000'000) {
        throw UsageError{"--cells には 1 以上 1,000,000 以下の整数を指定してください"};
      }
    } else {
      throw UsageError{"知らない引数 " + a + " です"};
    }
  }

#if GSIS_WITH_CUDA
  const gpu::RuntimeVersions v = gpu::runtime_versions();
  print_heading("CUDA");
  print_item("ビルド", build_description());
  print_item("ドライバが対応する CUDA", std::format("{}({})", gpu::version_to_string(v.driver), v.driver));
  print_item("ランタイム", std::format("{}({}、ビルド時 {})", gpu::version_to_string(v.runtime), v.runtime,
                                      gpu::version_to_string(v.compiled)));
  if (v.driver > 0 && v.driver < v.compiled) {
    print_item("注意", "ドライバの版がビルドに使った CUDA より古い(マイナー版の互換で動作)");
  }
  const std::vector<gpu::DeviceInfo> devices = gpu::query_devices();
  print_item("デバイス数", std::to_string(devices.size()));
  if (devices.empty()) {
    print_item("状態", "CUDA デバイスが見つかりません(ドライバと GPU を確認してください)");
    return smoke ? 1 : 0;
  }
  for (const gpu::DeviceInfo& d : devices) {
    print_heading(std::format("デバイス {}: {}", d.index, d.name));
    print_item("計算能力", std::format("{}.{}(sm_{}{})", d.cc_major, d.cc_minor, d.cc_major, d.cc_minor));
    print_item("SM 数", std::to_string(d.multiprocessors));
    print_item("メモリ", std::format("全体 {}、空き {}", format_bytes(static_cast<double>(d.total_memory)),
                                    format_bytes(static_cast<double>(d.free_memory))));
    print_item("クロック", std::format("{} MHz(メモリ {} MHz、バス {} ビット)", d.clock_khz / 1000,
                                      d.memory_clock_khz / 1000, d.memory_bus_width));
    print_item("L2 キャッシュ", std::format("{} KiB", d.l2_cache_bytes / 1024));
    print_item("共有メモリ", std::format("ブロックあたり {} KiB(opt-in {} KiB)、SM あたり {} KiB",
                                        d.shared_per_block / 1024, d.shared_per_block_optin / 1024,
                                        d.shared_per_multiprocessor / 1024));
    print_item("レジスタ / ブロック", std::to_string(d.registers_per_block));
    print_item("スレッド上限", std::format("ブロックあたり {}、SM あたり {}(warp {})", d.max_threads_per_block,
                                          d.max_threads_per_multiprocessor, d.warp_size));
    print_item("FP32 / FP64 の性能比", std::to_string(d.fp32_to_fp64_ratio));
    print_item("カーネル時間の上限 TDR", d.kernel_timeout ? "あり(WDDM。1 回のカーネルを短くする)" : "なし");
    print_item("ドライバのモデル", d.tcc_driver ? "TCC" : "WDDM");
    print_item("ECC", d.ecc_enabled ? "有効" : "無効");
    print_item("PCI", d.pci_bus_id);
  }
  if (smoke) return run_smoke(n_cells);
  return 0;
#else
  (void)n_cells;
  print_heading("CUDA");
  print_item("ビルド", build_description());
  print_item("状態", "この gsis-solver は CUDA なし(GSIS_ENABLE_CUDA=OFF)でビルドされています");
  return smoke ? 1 : 0;
#endif
}

}  // namespace gsis::cli
