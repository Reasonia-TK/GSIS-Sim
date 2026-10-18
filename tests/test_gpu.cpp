// gpu: デバイス情報と、f^eq のモーメントの GPU / CPU の一致(GPU がなければスキップ)

#include <algorithm>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstring>
#include <vector>

#include <random>

#include "gpu/device_info.hpp"
#include "gpu/maxwell_moments.hpp"
#include "gpu/physics_check.hpp"
#include "physics/moments.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;

namespace {

bool bitwise_equal(const std::vector<Moments>& a, const std::vector<Moments>& b) {
  if (a.size() != b.size()) return false;
  for (std::size_t i = 0; i < a.size(); ++i) {
    if (std::memcmp(&a[i], &b[i], sizeof(Moments)) != 0) return false;
  }
  return true;
}

}  // namespace

TEST_CASE("GPU device query", "[gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  const std::vector<gpu::DeviceInfo> devices = gpu::query_devices();
  REQUIRE_FALSE(devices.empty());
  for (const gpu::DeviceInfo& d : devices) {
    CHECK_FALSE(d.name.empty());
    CHECK(d.cc_major >= 5);
    CHECK(d.total_memory > 0);
    CHECK(d.free_memory <= d.total_memory);
    CHECK(d.multiprocessors > 0);
    CHECK(d.warp_size == 32);
  }
  const gpu::RuntimeVersions v = gpu::runtime_versions();
  CHECK(v.runtime >= 13000);
  CHECK(v.driver >= v.runtime);
  const gpu::MemoryInfo m = gpu::memory_info(0);
  CHECK(m.free > 0);
  CHECK(m.free <= m.total);
}

TEST_CASE("GPU evaluation of the shared physics functions agrees with the CPU", "[gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  // ランダムな状態・速度・法線で、Maxwell・Shakhov・半空間流束とヤコビアンを比べる
  std::mt19937_64 rng(2026);
  std::uniform_real_distribution<double> uni(-1.0, 1.0);
  std::vector<gpu::PhysicsSample> samples(500);
  for (gpu::PhysicsSample& s : samples) {
    s.p = 0.5 + 0.5 * (uni(rng) + 1.0);
    s.u = Vec3{1.5 * uni(rng), 1.5 * uni(rng), 1.5 * uni(rng)};
    s.T = 0.6 + 0.5 * (uni(rng) + 1.0);
    s.q = Vec3{0.1 * uni(rng), 0.1 * uni(rng), 0.1 * uni(rng)};
    s.xi = Vec3{3.0 * uni(rng), 3.0 * uni(rng), 3.0 * uni(rng)};
    s.n = normalized(Vec3{uni(rng), uni(rng), uni(rng)});
  }
  const auto g = gpu::evaluate_physics_gpu(samples, 1.0, 2.0 / 3.0);
  const auto c = gpu::evaluate_physics_cpu(samples, 1.0, 2.0 / 3.0);
  REQUIRE(g.size() == c.size());
  double max_rel = 0.0;
  auto upd = [&](double a, double b, double scale) { max_rel = std::max(max_rel, std::abs(a - b) / scale); };
  for (std::size_t i = 0; i < g.size(); ++i) {
    upd(g[i].f_eq, c[i].f_eq, std::abs(c[i].f_eq) + 1e-300);
    upd(g[i].f_shakhov, c[i].f_shakhov, std::abs(c[i].f_eq) + 1e-300);
    double fs = 0.0, js = 0.0;
    for (int k = 0; k < 5; ++k) fs = std::max({fs, std::abs(c[i].flux_plus[k]), std::abs(c[i].flux_minus[k])});
    for (int r = 0; r < 5; ++r) {
      for (int k = 0; k < 5; ++k) {
        js = std::max({js, std::abs(c[i].jacobian_plus(r, k)), std::abs(c[i].jacobian_minus(r, k))});
      }
    }
    for (int k = 0; k < 5; ++k) {
      upd(g[i].flux_plus[k], c[i].flux_plus[k], fs);
      upd(g[i].flux_minus[k], c[i].flux_minus[k], fs);
    }
    for (int r = 0; r < 5; ++r) {
      for (int k = 0; k < 5; ++k) {
        upd(g[i].jacobian_plus(r, k), c[i].jacobian_plus(r, k), js);
        upd(g[i].jacobian_minus(r, k), c[i].jacobian_minus(r, k), js);
      }
    }
  }
  INFO("最大の相対差 " << max_rel);
  CHECK(max_rel < 1e-13);
}

TEST_CASE("GPU Maxwellian moments agree with the CPU", "[gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDA デバイスがありません");
  // 管の速度格子(無次元、R = 1)
  const VelocityGrid grid({30, 24, 24}, {-10.0, -8.0, -8.0}, {10.0, 8.0, 8.0});
  const std::vector<gpu::CellState> cells = gpu::make_random_states(2000, 12345ULL, 1.0);
  constexpr double R = 1.0;

  SECTION("FP64") {
    gpu::KernelStats stats;
    const auto gpu1 = gpu::maxwell_moments_gpu(grid, cells, R, gpu::Precision::FP64, &stats, 768);
    CHECK(stats.launches == 3);
    const auto cpu = gpu::maxwell_moments_cpu(grid, cells, R, gpu::Precision::FP64);
    const MomentDifference d = max_moment_difference(cpu, gpu1, R);
    INFO("ρ " << d.rho << ", u " << d.u << ", T " << d.T << ", σ " << d.sigma << ", q " << d.q);
    CHECK(d.max() < 1e-12);
    // 決定的(atomic を使わない): 同じ入力・起動の分け方が違っても、ビット単位で同じ
    const auto gpu2 = gpu::maxwell_moments_gpu(grid, cells, R, gpu::Precision::FP64, nullptr, 4096);
    CHECK(bitwise_equal(gpu1, gpu2));
    // 厳密な (ρ, u, T) にも近い(速度格子の打ち切り・中点則の誤差の範囲)
    for (std::size_t i = 0; i < cells.size(); ++i) {
      CHECK(std::abs(gpu1[i].rho / cells[i].rho - 1.0) < 1e-3);
      CHECK(std::abs(gpu1[i].T / cells[i].T - 1.0) < 1e-3);
    }
  }
  SECTION("FP32 distribution, FP64 accumulation") {
    const auto gpu1 = gpu::maxwell_moments_gpu(grid, cells, R, gpu::Precision::FP32);
    const auto cpu = gpu::maxwell_moments_cpu(grid, cells, R, gpu::Precision::FP32);
    const MomentDifference d = max_moment_difference(cpu, gpu1, R);
    INFO("ρ " << d.rho << ", u " << d.u << ", T " << d.T << ", σ " << d.sigma << ", q " << d.q);
    CHECK(d.max() < 1e-5);
    const auto gpu2 = gpu::maxwell_moments_gpu(grid, cells, R, gpu::Precision::FP32);
    CHECK(bitwise_equal(gpu1, gpu2));
    // FP64 の結果との差は FP32 の丸めの程度
    const auto gpu64 = gpu::maxwell_moments_gpu(grid, cells, R, gpu::Precision::FP64);
    CHECK(max_moment_difference(gpu64, gpu1, R).max() < 1e-5);
  }
}
