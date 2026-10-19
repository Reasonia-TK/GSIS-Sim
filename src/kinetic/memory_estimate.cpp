#include "kinetic/memory_estimate.hpp"

#include <algorithm>
#include <cmath>

#include "core/error.hpp"

namespace gsis::kinetic {
namespace {
template <class F>
json::Value estimate(const KineticSetup& s, std::int64_t free_bytes) {
  const std::int64_t nc = s.n_cells(), nv = s.nv(), nb = std::max(s.n_bfaces(), 1);
  const std::int64_t reserve = 1024LL * 1024 * 1024;  // CUDA バックエンドと同じ 1 GiB
  const std::int64_t budget = std::max(0LL, static_cast<long long>(free_bytes - reserve));
  const std::int64_t f = nc * nv * sizeof(F);
  // 最終的に保持する固定配列。volume/vdt/moments/shift/residual/w_half/w_new は FP64。
  const std::int64_t overhead = 3 * nv * sizeof(F) + (nc + 1) * 4 +
      static_cast<std::int64_t>(s.csr.faces.size()) * sizeof(CsrEntry<F>) + nc * sizeof(F) +
      static_cast<std::int64_t>(s.n_bfaces()) * sizeof(BoundaryFaceInfo<F>) + nb * sizeof(BoundaryInflow<F>) +
      nc * sizeof(CellParams<F>) + (s.n_blocks() + 1LL) * 4 + nc * (31 * 8 + 4) + nb * 20 * 8 +
      (s.n_sweep_dirs > 0 ? 2LL * s.n_sweep_dirs * nc * 4 + nv : 0);
  auto workspace = [&](std::int64_t k) -> std::int64_t {
    return 4LL * static_cast<std::int64_t>(sizeof(F)) * nc * k + nc * ((k + 255) / 256) * 8;
  };
  std::int64_t width = 0;
  if (s.params.velocity_chunk > 0) {
    const int orbit = s.velocity.orbit_size;
    width = std::min(nv, (s.params.velocity_chunk + orbit - 1) / orbit * orbit);
  } else {
    const double per_velocity = static_cast<double>(nc) * (4 * sizeof(F) + 8.0 / 256);
    width = std::min(nv, std::max(0LL, static_cast<long long>((budget - f - overhead) / per_velocity)) / 256 * 256);
    if (width > 0 && workspace(width) > budget - f - overhead) width = std::max(0LL, static_cast<long long>(width - 256));
  }
  const std::int64_t work = workspace(width);
  json::Value m;
  m.set("distribution", f);
  m.set("workspace", work);
  m.set("overhead", overhead);
  m.set("reserve", reserve);
  m.set("free", free_bytes);
  m.set("budget", budget);
  m.set("chunk", width);
  m.set("total", f + work + overhead);
  m.set("fits", width > 0 && f + work + overhead <= budget);
  m.set("model", "cuda_arrays_v1");
  m.set("precision", s.params.precision);
  return m;
}
}  // namespace

json::Value estimate_device_memory(const KineticSetup& s, std::int64_t free_bytes) {
  GSIS_CHECK(free_bytes > 0, "空き VRAM は正のバイト数です");
  return s.params.precision == "fp64" ? estimate<double>(s, free_bytes) : estimate<float>(s, free_bytes);
}
}  // namespace gsis::kinetic
