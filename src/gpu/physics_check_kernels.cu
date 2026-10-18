// physics_check のカーネル: physics の共有関数(GSIS_HD)を 1 スレッド 1 標本で評価する。

#include <cuda_runtime.h>

#include "gpu/cuda_check.hpp"
#include "gpu/physics_check.hpp"
#include "physics/half_space_flux.hpp"
#include "physics/maxwell.hpp"

namespace gsis::gpu::detail {
namespace {

__global__ void physics_check_kernel(const PhysicsSample* __restrict__ in, PhysicsResult* __restrict__ out,
                                     int n, double R, double Pr) {
  const int i = static_cast<int>(blockIdx.x * blockDim.x + threadIdx.x);
  if (i >= n) return;
  const PhysicsSample s = in[i];
  PhysicsResult r;
  const double rho = s.p / (R * s.T);
  r.f_eq = maxwellian(rho, s.u, s.T, R, s.xi);
  r.f_shakhov = shakhov(rho, s.u, s.T, s.q, R, Pr, s.xi);
  r.flux_plus = half_space_flux(+1, s.p, s.u, s.T, R, s.n);
  r.flux_minus = half_space_flux(-1, s.p, s.u, s.T, R, s.n);
  r.jacobian_plus = half_space_flux_jacobian(+1, s.p, s.u, s.T, R, s.n);
  r.jacobian_minus = half_space_flux_jacobian(-1, s.p, s.u, s.T, R, s.n);
  out[i] = r;
}

}  // namespace

void launch_physics_check(const PhysicsSample* in, PhysicsResult* out, int n, double R, double Pr) {
  if (n <= 0) return;
  constexpr int kBlock = 128;
  physics_check_kernel<<<(n + kBlock - 1) / kBlock, kBlock>>>(in, out, n, R, Pr);
  GSIS_CUDA_CHECK_LAUNCH();
}

}  // namespace gsis::gpu::detail
