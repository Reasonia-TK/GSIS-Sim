#include "gpu/maxwell_moments.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <format>
#include <random>

#include "core/error.hpp"
#include "gpu/device_buffer.hpp"
#include "gpu/device_info.hpp"
#include "gpu/maxwell_moments_launch.hpp"
#include "physics/maxwell.hpp"

namespace gsis::gpu {
namespace {

using detail::kMomentsPerCell;
using detail::kStatePerCell;

// CUDA イベントの RAII
class Event {
 public:
  Event() { GSIS_CUDA_CHECK(cudaEventCreate(&event_)); }
  ~Event() { cudaEventDestroy(event_); }
  Event(const Event&) = delete;
  Event& operator=(const Event&) = delete;
  cudaEvent_t get() const { return event_; }

 private:
  cudaEvent_t event_{};
};

Moments unpack(const double* o) {
  Moments m;
  m.rho = o[0];
  m.u = Vec3{o[1], o[2], o[3]};
  m.T = o[4];
  m.p = o[5];
  m.sigma = {o[6], o[7], o[8], o[9], o[10], o[11]};
  m.q = Vec3{o[12], o[13], o[14]};
  return m;
}

template <class F>
std::vector<F> evaluate_maxwellian(const VelocityGrid& grid, const std::vector<F>& xs,
                                   const std::vector<F>& ys, const std::vector<F>& zs,
                                   const CellState& s, double R) {
  const MaxwellParams<F> mp = make_maxwell_params<F>(
      static_cast<F>(s.rho), vec_cast<F>(s.u), static_cast<F>(s.T), static_cast<F>(R));
  std::vector<F> f(static_cast<std::size_t>(grid.size()));
  for (std::size_t k = 0; k < f.size(); ++k) {
    f[k] = maxwellian(mp, Vec3T<F>{xs[k], ys[k], zs[k]});
  }
  return f;
}

}  // namespace

std::vector<Moments> maxwell_moments_gpu(const VelocityGrid& grid, std::span<const CellState> cells,
                                         double R, Precision precision, KernelStats* stats,
                                         int max_cells_per_launch) {
  GSIS_CHECK(device_count() > 0, "CUDA デバイスがありません");
  GSIS_CHECK(max_cells_per_launch >= 1, "1 回の起動で扱うセル数は 1 以上にしてください");
  GSIS_CHECK(cells.size() <= static_cast<std::size_t>(1) << 30, "セルが多すぎます");
  const auto n_cells = static_cast<int>(cells.size());
  std::vector<Moments> result(cells.size());
  if (n_cells == 0) return result;

  std::vector<double> states(kStatePerCell * cells.size());
  for (std::size_t c = 0; c < cells.size(); ++c) {
    GSIS_CHECK(cells[c].rho > 0.0 && cells[c].T > 0.0,
               std::format("セル {} の密度・温度が正ではありません", c));
    states[kStatePerCell * c + 0] = cells[c].rho;
    states[kStatePerCell * c + 1] = cells[c].u.x;
    states[kStatePerCell * c + 2] = cells[c].u.y;
    states[kStatePerCell * c + 3] = cells[c].u.z;
    states[kStatePerCell * c + 4] = cells[c].T;
  }

  const DeviceBuffer<double> xd{std::span<const double>(grid.xs())};
  const DeviceBuffer<double> yd{std::span<const double>(grid.ys())};
  const DeviceBuffer<double> zd{std::span<const double>(grid.zs())};
  const std::vector<float> xs32 = grid.xs_f32();
  const std::vector<float> ys32 = grid.ys_f32();
  const std::vector<float> zs32 = grid.zs_f32();
  const DeviceBuffer<float> xf{std::span<const float>(xs32)};
  const DeviceBuffer<float> yf{std::span<const float>(ys32)};
  const DeviceBuffer<float> zf{std::span<const float>(zs32)};
  const DeviceBuffer<double> d_states{std::span<const double>(states)};
  DeviceBuffer<double> d_out(kMomentsPerCell * cells.size());

  detail::DeviceGridView view;
  view.xd = xd.data();
  view.yd = yd.data();
  view.zd = zd.data();
  view.xf = xf.data();
  view.yf = yf.data();
  view.zf = zf.data();
  view.nv = grid.size();
  view.weight = grid.weight();

  Event start;
  Event stop;
  int launches = 0;
  GSIS_CUDA_CHECK(cudaEventRecord(start.get()));
  for (int begin = 0; begin < n_cells; begin += max_cells_per_launch) {
    const int count = std::min(max_cells_per_launch, n_cells - begin);
    detail::launch_maxwell_moments(precision == Precision::FP32, view, d_states.data(), begin, count,
                                   R, d_out.data());
    ++launches;
  }
  GSIS_CUDA_CHECK(cudaEventRecord(stop.get()));
  GSIS_CUDA_CHECK(cudaEventSynchronize(stop.get()));
  GSIS_CUDA_CHECK(cudaDeviceSynchronize());
  float ms = 0.0f;
  GSIS_CUDA_CHECK(cudaEventElapsedTime(&ms, start.get(), stop.get()));
  if (stats != nullptr) {
    stats->kernel_ms = ms;
    stats->launches = launches;
  }

  std::vector<double> out(kMomentsPerCell * cells.size());
  d_out.download(std::span<double>(out));
  for (std::size_t c = 0; c < cells.size(); ++c) {
    result[c] = unpack(out.data() + kMomentsPerCell * c);
  }
  return result;
}

std::vector<CellState> make_random_states(int n_cells, unsigned long long seed, double u_max) {
  std::mt19937_64 rng(seed);
  std::uniform_real_distribution<double> rho_dist(0.5, 2.0);
  std::uniform_real_distribution<double> u_dist(-u_max, u_max);
  std::uniform_real_distribution<double> t_dist(0.5, 2.0);
  std::vector<CellState> states(static_cast<std::size_t>(n_cells));
  for (CellState& s : states) {
    s.rho = rho_dist(rng);
    s.u = Vec3{u_dist(rng), u_dist(rng), u_dist(rng)};
    s.T = t_dist(rng);
  }
  return states;
}

std::vector<Moments> maxwell_moments_cpu(const VelocityGrid& grid, std::span<const CellState> cells,
                                         double R, Precision precision) {
  std::vector<Moments> result;
  result.reserve(cells.size());
  if (precision == Precision::FP32) {
    const std::vector<float> xs = grid.xs_f32();
    const std::vector<float> ys = grid.ys_f32();
    const std::vector<float> zs = grid.zs_f32();
    for (const CellState& s : cells) {
      const std::vector<float> f = evaluate_maxwellian<float>(grid, xs, ys, zs, s, R);
      result.push_back(compute_moments(grid, std::span<const float>(f), R));
    }
  } else {
    for (const CellState& s : cells) {
      const std::vector<double> f = evaluate_maxwellian<double>(grid, grid.xs(), grid.ys(), grid.zs(), s, R);
      result.push_back(compute_moments(grid, std::span<const double>(f), R));
    }
  }
  return result;
}

}  // namespace gsis::gpu
