#include "gpu/physics_check.hpp"

#include <cuda_runtime.h>

#include "core/error.hpp"
#include "gpu/device_buffer.hpp"
#include "gpu/device_info.hpp"
#include "physics/maxwell.hpp"

namespace gsis::gpu {
namespace detail {
// physics_check_kernels.cu
void launch_physics_check(const PhysicsSample* in, PhysicsResult* out, int n, double R, double Pr);
}  // namespace detail

std::vector<PhysicsResult> evaluate_physics_gpu(std::span<const PhysicsSample> samples, double R, double Pr) {
  GSIS_CHECK(device_count() > 0, "CUDA デバイスがありません");
  std::vector<PhysicsResult> out(samples.size());
  if (samples.empty()) return out;
  const DeviceBuffer<PhysicsSample> d_in{samples};
  DeviceBuffer<PhysicsResult> d_out(samples.size());
  detail::launch_physics_check(d_in.data(), d_out.data(), static_cast<int>(samples.size()), R, Pr);
  GSIS_CUDA_CHECK(cudaDeviceSynchronize());
  d_out.download(std::span<PhysicsResult>(out));
  return out;
}

std::vector<PhysicsResult> evaluate_physics_cpu(std::span<const PhysicsSample> samples, double R, double Pr) {
  // GPU 版と同じヘッダの関数を、通常の CPU のコード(ホストのコンパイラ)で評価する
  std::vector<PhysicsResult> out;
  out.reserve(samples.size());
  for (const PhysicsSample& s : samples) {
    PhysicsResult r;
    const double rho = s.p / (R * s.T);
    r.f_eq = maxwellian(rho, s.u, s.T, R, s.xi);
    r.f_shakhov = shakhov(rho, s.u, s.T, s.q, R, Pr, s.xi);
    r.flux_plus = half_space_flux(+1, s.p, s.u, s.T, R, s.n);
    r.flux_minus = half_space_flux(-1, s.p, s.u, s.T, R, s.n);
    r.jacobian_plus = half_space_flux_jacobian(+1, s.p, s.u, s.T, R, s.n);
    r.jacobian_minus = half_space_flux_jacobian(-1, s.p, s.u, s.T, R, s.n);
    out.push_back(r);
  }
  return out;
}

}  // namespace gsis::gpu
