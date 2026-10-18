// maxwell_moments のカーネル。物理の式は physics/maxwell.hpp の共有関数を使う。

#include <cuda_runtime.h>

#include "gpu/cuda_check.hpp"
#include "gpu/maxwell_moments_launch.hpp"
#include "physics/maxwell.hpp"

namespace gsis::gpu::detail {
namespace {

constexpr int kBlock = 256;  // 2 のべき(木構造の和のため)

// ブロック内の和を決まった順序で取る(atomic を使わない)。結果は sh[v * kBlock] に入る。
// 呼ぶ前後で全スレッドが同期している必要がある(関数の中で __syncthreads を呼ぶ)。
template <int N>
__device__ void block_sum(const double (&vals)[N], double* sh) {
  const int tid = static_cast<int>(threadIdx.x);
#pragma unroll
  for (int v = 0; v < N; ++v) sh[v * kBlock + tid] = vals[v];
  __syncthreads();
  for (int stride = kBlock / 2; stride > 0; stride >>= 1) {
    if (tid < stride) {
#pragma unroll
      for (int v = 0; v < N; ++v) sh[v * kBlock + tid] += sh[v * kBlock + tid + stride];
    }
    __syncthreads();
  }
}

// 1 ブロックが 1 セルを受け持つ。F は f^eq を評価する精度、集計は double。
template <class F>
__global__ void __launch_bounds__(kBlock)
    maxwell_moments_kernel(DeviceGridView grid, const F* __restrict__ xf, const F* __restrict__ yf,
                           const F* __restrict__ zf, const double* __restrict__ states,
                           int cell_begin, double R, double* __restrict__ out) {
  __shared__ double sh[10 * kBlock];
  __shared__ double ubar[3];

  const long long cell = cell_begin + static_cast<long long>(blockIdx.x);
  const double* st = states + kStatePerCell * cell;
  const MaxwellParams<F> mp = make_maxwell_params<F>(
      static_cast<F>(st[0]), Vec3T<F>{static_cast<F>(st[1]), static_cast<F>(st[2]), static_cast<F>(st[3])},
      static_cast<F>(st[4]), static_cast<F>(R));
  const int nv = grid.nv;
  const double* __restrict__ xd = grid.xd;
  const double* __restrict__ yd = grid.yd;
  const double* __restrict__ zd = grid.zd;

  // 1 段階目: Σf, Σf ξ
  double s1[4] = {0.0, 0.0, 0.0, 0.0};
  for (int k = static_cast<int>(threadIdx.x); k < nv; k += kBlock) {
    const double f = static_cast<double>(maxwellian(mp, Vec3T<F>{xf[k], yf[k], zf[k]}));
    s1[0] += f;
    s1[1] += f * xd[k];
    s1[2] += f * yd[k];
    s1[3] += f * zd[k];
  }
  block_sum<4>(s1, sh);
  const double s0 = sh[0];
  if (threadIdx.x == 0) {
    ubar[0] = sh[1 * kBlock] / s0;
    ubar[1] = sh[2 * kBlock] / s0;
    ubar[2] = sh[3 * kBlock] / s0;
  }
  __syncthreads();
  const double ux = ubar[0];
  const double uy = ubar[1];
  const double uz = ubar[2];

  // 2 段階目: c = ξ − u で Σf c c、Σf |c|² c、Σf |c|²
  double s2[10] = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
  for (int k = static_cast<int>(threadIdx.x); k < nv; k += kBlock) {
    const double f = static_cast<double>(maxwellian(mp, Vec3T<F>{xf[k], yf[k], zf[k]}));
    const double cx = xd[k] - ux;
    const double cy = yd[k] - uy;
    const double cz = zd[k] - uz;
    const double c2 = cx * cx + cy * cy + cz * cz;
    s2[0] += f * cx * cx;
    s2[1] += f * cy * cy;
    s2[2] += f * cz * cz;
    s2[3] += f * cx * cy;
    s2[4] += f * cy * cz;
    s2[5] += f * cx * cz;
    s2[6] += f * c2 * cx;
    s2[7] += f * c2 * cy;
    s2[8] += f * c2 * cz;
    s2[9] += f * c2;
  }
  block_sum<10>(s2, sh);

  if (threadIdx.x == 0) {
    const double w = grid.weight;
    const double rho = w * s0;
    const double p = w * sh[9 * kBlock] / 3.0;
    double* o = out + kMomentsPerCell * cell;
    o[0] = rho;
    o[1] = ux;
    o[2] = uy;
    o[3] = uz;
    o[4] = p / (rho * R);
    o[5] = p;
    o[6] = w * sh[0 * kBlock] - p;
    o[7] = w * sh[1 * kBlock] - p;
    o[8] = w * sh[2 * kBlock] - p;
    o[9] = w * sh[3 * kBlock];
    o[10] = w * sh[4 * kBlock];
    o[11] = w * sh[5 * kBlock];
    o[12] = 0.5 * w * sh[6 * kBlock];
    o[13] = 0.5 * w * sh[7 * kBlock];
    o[14] = 0.5 * w * sh[8 * kBlock];
  }
}

}  // namespace

void launch_maxwell_moments(bool fp32, const DeviceGridView& grid, const double* states,
                            int cell_begin, int cell_count, double R, double* out) {
  if (cell_count <= 0) return;
  const dim3 blocks(static_cast<unsigned>(cell_count));
  if (fp32) {
    maxwell_moments_kernel<float>
        <<<blocks, kBlock>>>(grid, grid.xf, grid.yf, grid.zf, states, cell_begin, R, out);
  } else {
    maxwell_moments_kernel<double>
        <<<blocks, kBlock>>>(grid, grid.xd, grid.yd, grid.zd, states, cell_begin, R, out);
  }
  GSIS_CUDA_CHECK_LAUNCH();
}

}  // namespace gsis::gpu::detail
