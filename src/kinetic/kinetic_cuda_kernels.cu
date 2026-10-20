// 運動論ソルバーの CUDA カーネル。1 セル・1 速度点の計算は kinetic_kernels.hpp の共通関数を使う。
// 集計は atomic を使わず、スレッドごとに決まった順に足してから、共有メモリの木構造で足す。

#include <cuda_runtime.h>

#include "gpu/cuda_check.hpp"
#include "kinetic/kinetic_cuda_launch.hpp"
#include "kinetic/kinetic_kernels.hpp"

namespace gsis::kinetic::cuda {
namespace {

constexpr int kMomentThreads = 256;
constexpr int kBoundaryThreads = 128;
constexpr int kCellTile = 256;   // 勾配・残差: 1 ブロック = 1 セル × 256 点
constexpr int kSweepTile = 128;  // LU-SGS: 1 ブロック = 1 空間ブロック × 128 点
constexpr int kCorrectionThreads = 256;
constexpr int kDeqThreads = 256;  // 離散平衡のセルの係数: 1 ブロック = 1 セル

// ブロック内の和(決まった順序)。結果は sh[v * B] に入る。前後で全スレッドが同期していること
template <int N, int B>
__device__ void block_reduce(const double (&vals)[N], double* sh) {
  const int tid = static_cast<int>(threadIdx.x);
#pragma unroll
  for (int v = 0; v < N; ++v) sh[v * B + tid] = vals[v];
  __syncthreads();
  for (int stride = B / 2; stride > 0; stride >>= 1) {
    if (tid < stride) {
#pragma unroll
      for (int v = 0; v < N; ++v) sh[v * B + tid] += sh[v * B + tid + stride];
    }
    __syncthreads();
  }
}

// ブロック内の和(warp の中はシャッフルの木、warp の間は番号順。決まった順序)。結果はスレッド 0 の vals に入る。
// sh は N × (B/32) 個。前後で全スレッドが同期していること
template <int N, int B>
__device__ void block_sum_shfl(double (&vals)[N], double* sh) {
  static_assert(B % 32 == 0, "ブロックのスレッド数は 32 の倍数");
  constexpr int W = B / 32;
#pragma unroll
  for (int off = 16; off > 0; off >>= 1) {
#pragma unroll
    for (int v = 0; v < N; ++v) vals[v] += __shfl_down_sync(0xffffffffu, vals[v], off);
  }
  const int tid = static_cast<int>(threadIdx.x);
  const int lane = tid & 31;
  const int warp = tid >> 5;
  if (lane == 0) {
#pragma unroll
    for (int v = 0; v < N; ++v) sh[v * W + warp] = vals[v];
  }
  __syncthreads();
  if (tid == 0) {
#pragma unroll
    for (int v = 0; v < N; ++v) {
      double s = 0.0;
      for (int w = 0; w < W; ++w) s += sh[v * W + w];
      vals[v] = s;
    }
  }
  __syncthreads();
}

// 保存的な離散平衡分布の係数をブロック全体(B スレッド)で求める(kinetic/discrete_equilibrium.hpp)。
// 全スレッドが同じ引数で呼ぶこと。poly が null でなければ Shakhov の多項式の補正 γ も求める。
// 結果は全スレッドの out・gamma に入る。戻り値: ニュートン法の反復数(0 = 補正不要)、−1 = 失敗(連続の Maxwell 分布)。
// sh_red は kDeqSums × (B/32) 個、sh_state は 12 個の共有メモリ
template <class F, int B>
__device__ int block_discrete_maxwell(const KineticView<F>& v, double rho, double ux, double uy, double uz, double T,
                                      const ShakhovTarget<F>* poly, MaxwellParams<F>& out, double (&gamma)[5],
                                      double* sh_red, double* sh_state) {
  for (double& x : gamma) x = 0.0;
  out = maxwell_params_from<F>(rho, ux, uy, uz, T);
  if (deq_resolved(v.deq, ux, uy, uz, T)) return 0;
  const int tid = static_cast<int>(threadIdx.x);
  // sh_state: [0, 5) β、[5] 状態(0 = 続ける、1 = 受け入れ、−1 = 失敗)、[6, 11) γ、[11] 前回の相対誤差
  if (tid == 0) {
    double beta[5];
    deq_initial_beta(rho, T, beta);
    for (int k = 0; k < 5; ++k) sh_state[k] = beta[k];
    sh_state[5] = 0.0;
    for (int k = 0; k < 5; ++k) sh_state[6 + k] = 0.0;
    sh_state[11] = 1e300;
  }
  __syncthreads();
  const F fux = static_cast<F>(ux);
  const F fuy = static_cast<F>(uy);
  const F fuz = static_cast<F>(uz);
  const F w = v.weight;
  int it = 0;
  for (;;) {
    const double beta[5] = {sh_state[0], sh_state[1], sh_state[2], sh_state[3], sh_state[4]};
    const MaxwellParams<F> m = deq_params<F>(beta, ux, uy, uz);
    Accum<F> acc[kDeqSums];
    F t[kDeqSums];
    for (int p = tid; p < v.nv; p += B) {
      const Vec3T<F> xi = velocity_at(v, p);
      const Vec3T<F> c{xi.x - fux, xi.y - fuy, xi.z - fuz};
      const F e = maxwellian(m, xi);
      const F pm = poly != nullptr ? shakhov_poly(*poly, c, dot(c, c)) : F(0);
      deq_terms<F>(e, pm, c.x, c.y, c.z, t);
#pragma unroll
      for (int k = 0; k < kDeqSums; ++k) acc[k].add(t[k]);
    }
    double vals[kDeqSums];
#pragma unroll
    for (int k = 0; k < kDeqSums; ++k) vals[k] = acc[k].value();
    block_sum_shfl<kDeqSums, B>(vals, sh_red);
    if (tid == 0) {
      double b[5] = {beta[0], beta[1], beta[2], beta[3], beta[4]};
      double rel = 0.0;
      const int r = deq_newton_step(vals, static_cast<double>(w), rho, T, v.deq.tolerance, b, rel);
      if (deq_accept(r, rel, sh_state[11], it + 1 == v.deq.max_iterations)) {
        sh_state[5] = 1.0;
        if (poly != nullptr) {
          double gm[5] = {0.0, 0.0, 0.0, 0.0, 0.0};
          if (deq_shakhov_correction(vals, static_cast<double>(w), gm)) {
            for (int k = 0; k < 5; ++k) sh_state[6 + k] = gm[k];
          }
        }
      } else if (r < 0 || it + 1 >= v.deq.max_iterations) {
        sh_state[5] = -1.0;
      } else {
        for (int k = 0; k < 5; ++k) sh_state[k] = b[k];
        sh_state[11] = rel;
      }
    }
    __syncthreads();
    const double status = sh_state[5];
    if (status != 0.0) {
      int result = it + 1;
      if (status > 0.0) {
        out = m;
        for (int k = 0; k < 5; ++k) gamma[k] = sh_state[6 + k];
      } else {
        result = -1;
      }
      __syncthreads();  // 共有メモリを呼び出し側が使い回せるように
      return result;
    }
    ++it;
  }
}

template <class F>
__global__ void __launch_bounds__(kMomentThreads)
    k_set_equilibrium(KineticView<F> v, const double* __restrict__ states, double* __restrict__ shift, int c0) {
  __shared__ double sh_red[kDeqSums * (kMomentThreads / 32)];
  __shared__ double sh_state[12];
  const int c = c0 + static_cast<int>(blockIdx.x);
  const double* st = states + 5 * static_cast<i64>(c);
  MaxwellParams<F> m = maxwell_params_from<F>(st[0], st[1], st[2], st[3], st[4]);
  if (v.discrete_eq != 0) {
    double gamma[5];
    block_discrete_maxwell<F, kMomentThreads>(v, st[0], st[1], st[2], st[3], st[4], nullptr, m, gamma, sh_red,
                                              sh_state);
  }
  F* fc = v.f + static_cast<i64>(c) * v.nv;
  for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kMomentThreads) {
    fc[p] = maxwellian(m, velocity_at(v, p));
  }
  if (threadIdx.x == 0) {
    shift[3 * static_cast<i64>(c) + 0] = st[1];
    shift[3 * static_cast<i64>(c) + 1] = st[2];
    shift[3 * static_cast<i64>(c) + 2] = st[3];
  }
}

template <class F>
__global__ void __launch_bounds__(kMomentThreads)
    k_moments(KineticView<F> v, double* __restrict__ shift, double* __restrict__ out, int c0) {
  __shared__ double sh[kMomentSums * kMomentThreads];
  const int c = c0 + static_cast<int>(blockIdx.x);
  // シフトは F に丸めた値を使い、仕上げでも同じ値を使う
  const F u0x = static_cast<F>(shift[3 * static_cast<i64>(c) + 0]);
  const F u0y = static_cast<F>(shift[3 * static_cast<i64>(c) + 1]);
  const F u0z = static_cast<F>(shift[3 * static_cast<i64>(c) + 2]);
  const F* fc = v.f + static_cast<i64>(c) * v.nv;
  Accum<F> acc[kMomentSums];
  F t[kMomentSums];
  for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kMomentThreads) {
    moment_terms<F>(fc[p], v.xi_x[p] - u0x, v.xi_y[p] - u0y, v.xi_z[p] - u0z, t);
#pragma unroll
    for (int k = 0; k < kMomentSums; ++k) acc[k].add(t[k]);
  }
  double vals[kMomentSums];
#pragma unroll
  for (int k = 0; k < kMomentSums; ++k) vals[k] = acc[k].value();
  block_reduce<kMomentSums, kMomentThreads>(vals, sh);
  if (threadIdx.x == 0) {
    double sums[kMomentSums];
#pragma unroll
    for (int k = 0; k < kMomentSums; ++k) sums[k] = sh[k * kMomentThreads];
    double* m = out + kMomentsPerCell * static_cast<i64>(c);
    finalize_moments(sums, static_cast<double>(v.weight), static_cast<double>(u0x), static_cast<double>(u0y),
                     static_cast<double>(u0z), m);
    if (m[0] > 0.0) {
      shift[3 * static_cast<i64>(c) + 0] = m[1];
      shift[3 * static_cast<i64>(c) + 1] = m[2];
      shift[3 * static_cast<i64>(c) + 2] = m[3];
    }
  }
}

template <class F>
__global__ void k_scale(F* __restrict__ f, long long n, F s) {
  const long long stride = static_cast<long long>(gridDim.x) * blockDim.x;
  for (long long i = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x; i < n; i += stride) f[i] *= s;
}

__global__ void k_scale_moments(double* __restrict__ m, int n_cells, double s) {
  const int c = static_cast<int>(blockIdx.x * blockDim.x + threadIdx.x);
  if (c >= n_cells) return;
  double* mc = m + kMomentsPerCell * static_cast<i64>(c);
  mc[0] *= s;
  for (int k = 5; k < kMomentsPerCell; ++k) mc[k] *= s;
}

template <class F>
__global__ void k_cell_params(const double* __restrict__ moments, const double* __restrict__ volume,
                              const double* __restrict__ vdt, double mu0, double omega, double Pr,
                              CellParams<F>* __restrict__ out, int n_cells, double cold_bgk_T) {
  const int c = static_cast<int>(blockIdx.x * blockDim.x + threadIdx.x);
  if (c >= n_cells) return;
  out[c] = make_cell_params<F>(moments + kMomentsPerCell * static_cast<i64>(c), volume[c], vdt[c], mu0, omega, Pr,
                               cold_bgk_T);
}

// セルの係数(保存的な離散平衡分布)。1 ブロック = 1 セル。flags[c] にニュートン法の反復数(−1 は失敗)
template <class F>
__global__ void __launch_bounds__(kDeqThreads)
    k_cell_params_deq(KineticView<F> v, const double* __restrict__ moments, const double* __restrict__ volume,
                      const double* __restrict__ vdt, double mu0, double omega, double Pr, CellParams<F>* __restrict__ out,
                      int* __restrict__ flags, int c0) {
  __shared__ double sh_red[kDeqSums * (kDeqThreads / 32)];
  __shared__ double sh_state[12];
  const int c = c0 + static_cast<int>(blockIdx.x);
  const double* m = moments + kMomentsPerCell * static_cast<i64>(c);
  CellParams<F> cp = make_cell_params<F>(m, volume[c], vdt[c], mu0, omega, Pr, v.cold_bgk_temperature);
  MaxwellParams<F> eq;
  double gamma[5];
  const int r = block_discrete_maxwell<F, kDeqThreads>(v, m[0], m[1], m[2], m[3], m[4], &cp.s, eq, gamma, sh_red,
                                                       sh_state);
  if (threadIdx.x == 0) {
    if (r > 0) {
      cp.s.eq = eq;
      cp.s.g0 = static_cast<F>(gamma[0]);
      cp.s.g = Vec3T<F>{static_cast<F>(gamma[1]), static_cast<F>(gamma[2]), static_cast<F>(gamma[3])};
      cp.s.g4 = static_cast<F>(gamma[4]);
    } else if (r < 0 && v.deq_fallback != 0) {
      // ニュートン法が収束しない(モーメントが速度格子で表せる限界に近い): 衝突なし(目標分布 = f)
      cp.coll = F(0);
      cp.diag = static_cast<F>(vdt[c]);
    }
    out[c] = cp;
    flags[c] = r;
  }
}

template <bool Reconstruct, class F>
__global__ void k_maxwell_reflection(KineticView<F> v, int b0) {
  const int b = b0 + static_cast<int>(blockIdx.y);
  const int p = static_cast<int>(blockIdx.x * blockDim.x + threadIdx.x);
  if (p >= v.nv || v.maxwell[b].offset < 0) return;
  const auto bf = v.bfaces[b];
  const auto xi = velocity_at(v, p);
  F* buffer = Reconstruct ? v.specular : v.specular_first;
  buffer[v.maxwell[b].offset + p] = dot(xi, Vec3T<F>{bf.nx,bf.ny,bf.nz}) < F(0)
      ? reflected_value<Reconstruct>(v, bf, p, xi) : F(0);
}

template <class F>
__global__ void __launch_bounds__(kBoundaryThreads) k_boundary_sums(KineticView<F> v, double* __restrict__ out,
                                                                    int b0) {
  __shared__ double sh[kBoundarySumsPerFace * kBoundaryThreads];
  __shared__ F sh_out[kBoundaryThreads];
  const int b = b0 + static_cast<int>(blockIdx.x);
  const BoundaryFaceInfo<F> bf = v.bfaces[b];
  const MaxwellParams<F> m_in = v.binflow[b].m;
  const F w = v.weight;
  const bool symmetry = bf.kind == kBcSymmetry;
  Accum<F> acc[kBoundarySumsPerFace];
  for (int p0 = 0; p0 < v.nv; p0 += kBoundaryThreads) {
    const int p = p0 + static_cast<int>(threadIdx.x);
    const bool valid = p < v.nv;
    Vec3T<F> xi{F(0), F(0), F(0)};
    F a_n = F(0);
    F fb = F(0);
    bool out_side = true;
    if (valid) {
      xi = velocity_at(v, p);
      a_n = xi.x * bf.nx + xi.y * bf.ny + xi.z * bf.nz;
      out_side = a_n >= F(0) || bf.kind == kBcPeriodic;
      if (out_side) fb = reconstruct_at_boundary(v, bf, p, xi);
    }
    if (symmetry) {
      // 流入側は鏡映した速度点(同じ 128 点の中にある)の流出側の再構成値
      sh_out[threadIdx.x] = fb;
      __syncthreads();
      if (valid && !out_side) fb = sh_out[(p ^ bf.mirror_xor) - p0];
      __syncthreads();
    } else if (valid && !out_side) {
      fb = bf.kind == kBcMaxwellWall ? maxwell_inflow(v, b, p, xi) : maxwellian(m_in, xi);  // 単位(scale を掛けない)
    }
    if (valid) {
      if (out_side) {
        add_boundary_terms<F, Accum<F>>(w * fb, xi, a_n, acc + 0, acc + 10);
      } else {
        add_boundary_terms<F, Accum<F>>(w * fb, xi, a_n, acc + 5, acc + 15);
      }
    }
  }
  double vals[kBoundarySumsPerFace];
#pragma unroll
  for (int k = 0; k < kBoundarySumsPerFace; ++k) vals[k] = acc[k].value();
  block_reduce<kBoundarySumsPerFace, kBoundaryThreads>(vals, sh);
  if (threadIdx.x == 0) {
    double* o = out + kBoundarySumsPerFace * static_cast<i64>(b);
    for (int k = 0; k < kBoundarySumsPerFace; ++k) o[k] = sh[k * kBoundaryThreads];
  }
}

// 内部面の質量流束(フェーズ 5c)。1 ブロック = 1 面。スレッドごとに決まった順に足してから木構造で足す
template <class F>
__global__ void __launch_bounds__(kBoundaryThreads)
    k_internal_face_mass_flux(KineticView<F> v, const std::int32_t* __restrict__ cells,
                              const std::int32_t* __restrict__ entries, double* __restrict__ out, int i0) {
  __shared__ double sh[kBoundaryThreads];
  const int i = i0 + static_cast<int>(blockIdx.x);
  const int c = cells[i];
  const CsrEntry<F> ce = v.csr[entries[i]];
  Accum<F> acc;
  for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kBoundaryThreads) {
    acc.add(internal_face_flux_term(v, c, ce, p, velocity_at(v, p)));
  }
  const double vals[1] = {acc.value()};
  block_reduce<1, kBoundaryThreads>(vals, sh);
  if (threadIdx.x == 0) out[i] = static_cast<double>(v.weight) * sh[0];
}

template <class F>
__global__ void __launch_bounds__(kCellTile) k_gradient(KineticView<F> v, ChunkView<F> ch, int c0) {
  const int c = c0 + static_cast<int>(blockIdx.x);
  const int t = static_cast<int>(blockIdx.y) * kCellTile + static_cast<int>(threadIdx.x);
  if (t >= ch.kc) return;
  const int p = ch.k0 + t;
  const Vec3T<F> xi = velocity_at(v, p);
  const Vec3T<F> g = limited_gradient(v, c, p, xi, f_at(v, c, p));
  const i64 wi = static_cast<i64>(c) * ch.stride + t;
  ch.gx[wi] = g.x;
  ch.gy[wi] = g.y;
  ch.gz[wi] = g.z;
}

template <class F>
__global__ void __launch_bounds__(kCellTile)
    k_residual(KineticView<F> v, ChunkView<F> ch, double* __restrict__ partial, int partial_stride, int c0) {
  __shared__ double sh[kCellTile];
  const int c = c0 + static_cast<int>(blockIdx.x);
  const int t = static_cast<int>(blockIdx.y) * kCellTile + static_cast<int>(threadIdx.x);
  double r2 = 0.0;
  if (t < ch.kc) {
    const F R = residual_value(v, ch, c, t);
    ch.r[static_cast<i64>(c) * ch.stride + t] = R;
    r2 = static_cast<double>(R) * static_cast<double>(R);
  }
  const double vals[1] = {r2};
  block_reduce<1, kCellTile>(vals, sh);
  if (threadIdx.x == 0) partial[static_cast<i64>(c) * partial_stride + blockIdx.y] = sh[0];
}

__global__ void k_residual_reduce(const double* __restrict__ partial, int partial_stride, int n_tiles,
                                  double* __restrict__ residual_cell, int n_cells) {
  const int c = static_cast<int>(blockIdx.x * blockDim.x + threadIdx.x);
  if (c >= n_cells) return;
  double s = 0.0;
  for (int k = 0; k < n_tiles; ++k) s += partial[static_cast<i64>(c) * partial_stride + k];
  residual_cell[c] += s;
}

template <class F>
__global__ void __launch_bounds__(kSweepTile) k_sweep_forward(KineticView<F> v, ChunkView<F> ch,
                                                              const std::int32_t* __restrict__ block_offsets,
                                                              int s0, int s1) {
  const int b = static_cast<int>(blockIdx.y);
  const int t = static_cast<int>(blockIdx.x) * kSweepTile + static_cast<int>(threadIdx.x);
  if (t >= ch.kc) return;
  const int cb = block_offsets[b];
  const int ce = block_offsets[b + 1];
  const Vec3T<F> xi = velocity_at(v, ch.k0 + t);
  const int begin = cb + s0;
  const int end = min(ce, cb + s1);
  const std::int32_t* order;
  const std::int32_t* pos;
  sweep_order_of(v, ch.k0 + t, order, pos);
  if (order == nullptr) {
    for (int c = begin; c < end; ++c) sweep_forward_cell(v, ch, c, t, cb, ce, xi);
  } else {
    for (int k = begin; k < end; ++k) sweep_forward_cell(v, ch, order[k], t, cb, ce, xi, pos);
  }
}

template <class F>
__global__ void __launch_bounds__(kSweepTile) k_sweep_backward(KineticView<F> v, ChunkView<F> ch,
                                                               const std::int32_t* __restrict__ block_offsets,
                                                               int s0, int s1) {
  const int b = static_cast<int>(blockIdx.y);
  const int t = static_cast<int>(blockIdx.x) * kSweepTile + static_cast<int>(threadIdx.x);
  if (t >= ch.kc) return;
  const int cb = block_offsets[b];
  const int ce = block_offsets[b + 1];
  const Vec3T<F> xi = velocity_at(v, ch.k0 + t);
  const int begin = ce - 1 - s0;
  const int end = max(cb, ce - s1);  // この値は含まない手前まで(c >= end)
  const std::int32_t* order;
  const std::int32_t* pos;
  sweep_order_of(v, ch.k0 + t, order, pos);
  if (order == nullptr) {
    for (int c = begin; c >= end; --c) sweep_backward_cell(v, ch, c, t, cb, ce, xi);
  } else {
    for (int k = begin; k >= end; --k) sweep_backward_cell(v, ch, order[k], t, cb, ce, xi, pos);
  }
}

template <class F>
__global__ void __launch_bounds__(kCorrectionThreads)
    k_gsis_correction(KineticView<F> v, const double* __restrict__ w_half, const double* __restrict__ w_new,
                      int* __restrict__ replaced, double* __restrict__ shift, int c0, int mode) {
  __shared__ F sh_min[kCorrectionThreads];
  __shared__ double sh_red[kDeqSums * (kCorrectionThreads / 32)];
  __shared__ double sh_state[12];
  const int c = c0 + static_cast<int>(blockIdx.x);
  const double* wh = w_half + 5 * static_cast<i64>(c);
  const double* wn = w_new + 5 * static_cast<i64>(c);
  MaxwellParams<F> mh = maxwell_params_from<F>(wh[0], wh[1], wh[2], wh[3], wh[4]);
  MaxwellParams<F> mn = maxwell_params_from<F>(wn[0], wn[1], wn[2], wn[3], wn[4]);
  if (v.discrete_eq != 0) {
    // 保存的な離散平衡分布: f^{n+1} のモーメントが W^{n+1} に一致する
    double gamma[5];
    const int rh = block_discrete_maxwell<F, kCorrectionThreads>(v, wh[0], wh[1], wh[2], wh[3], wh[4], nullptr, mh,
                                                                 gamma, sh_red, sh_state);
    const int rn = block_discrete_maxwell<F, kCorrectionThreads>(v, wn[0], wn[1], wn[2], wn[3], wn[4], nullptr, mn,
                                                                 gamma, sh_red, sh_state);
    if (v.deq_fallback != 0 && (rh < 0 || rn < 0)) {
      // 離散平衡が作れない(W が速度格子で表せる限界に近い・超える)セルは補正しない(f はそのまま)。
      // rh・rn はブロックの全スレッドで同じなので、全スレッドがここで抜ける
      if (threadIdx.x == 0) {
        replaced[c] = kCorrectionSkipped;
        shift[3 * static_cast<i64>(c) + 0] = wh[1];
        shift[3 * static_cast<i64>(c) + 1] = wh[2];
        shift[3 * static_cast<i64>(c) + 2] = wh[3];
      }
      return;
    }
  }
  F* fc = v.f + static_cast<i64>(c) * v.nv;
  const double threshold = -1e-8 * wn[0] / (wn[4] * sqrt(wn[4]));
  // "limit" では補正を縮める係数 α の最小値、"blend" では 1 − θ の最小値、それ以外では判定に使う値の最小値を
  // ブロック内で求める
  F local_min = (mode == kNegativeLimit || mode == kNegativeBlend) ? F(1) : F(0);
  bool first = true;
  for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kCorrectionThreads) {
    const Vec3T<F> xi = velocity_at(v, p);
    const F old = fc[p];
    const F e = maxwellian(mn, xi);
    const F d = e - maxwellian(mh, xi);
    if (mode == kNegativeLimit) {
      local_min = min(local_min, correction_alpha_limit(old, d, static_cast<F>(threshold)));
    } else if (mode == kNegativeBlend) {
      local_min = min(local_min, F(1) - blend_theta(old, d, e, static_cast<F>(threshold)));
    } else {
      const F x = old + d;
      fc[p] = x;
      const F chk = negative_check_value(old, x, mode == kNegativeCreated);
      local_min = first ? chk : min(local_min, chk);
      first = false;
    }
  }
  sh_min[threadIdx.x] = local_min;
  __syncthreads();
  for (int stride = kCorrectionThreads / 2; stride > 0; stride >>= 1) {
    if (static_cast<int>(threadIdx.x) < stride) sh_min[threadIdx.x] = min(sh_min[threadIdx.x], sh_min[threadIdx.x + stride]);
    __syncthreads();
  }
  bool replace;
  if (mode == kNegativeLimit) {
    const F alpha = sh_min[0];
    for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kCorrectionThreads) {
      const Vec3T<F> xi = velocity_at(v, p);
      fc[p] += alpha * (maxwellian(mn, xi) - maxwellian(mh, xi));
    }
    replace = alpha < F(1);
  } else if (mode == kNegativeBlend) {
    const F theta = F(1) - sh_min[0];
    for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kCorrectionThreads) {
      const Vec3T<F> xi = velocity_at(v, p);
      const F e = maxwellian(mn, xi);
      const F g = fc[p] + (e - maxwellian(mh, xi));
      fc[p] = (F(1) - theta) * g + theta * e;
    }
    replace = theta > F(0);
  } else {
    replace = static_cast<double>(sh_min[0]) < threshold;
    if (replace) {
      for (int p = static_cast<int>(threadIdx.x); p < v.nv; p += kCorrectionThreads) {
        fc[p] = maxwellian(mn, velocity_at(v, p));
      }
    }
  }
  if (threadIdx.x == 0) {
    replaced[c] = replace ? 1 : 0;
    shift[3 * static_cast<i64>(c) + 0] = wn[1];
    shift[3 * static_cast<i64>(c) + 1] = wn[2];
    shift[3 * static_cast<i64>(c) + 2] = wn[3];
  }
}

int ceil_div(int a, int b) { return (a + b - 1) / b; }

}  // namespace

template <class F>
void launch_set_equilibrium(const KineticView<F>& v, const double* states, double* shift, int c0, int nc) {
  if (nc <= 0) return;
  k_set_equilibrium<F><<<nc, kMomentThreads>>>(v, states, shift, c0);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_moments(const KineticView<F>& v, double* shift, double* moments, int c0, int nc) {
  if (nc <= 0) return;
  k_moments<F><<<nc, kMomentThreads>>>(v, shift, moments, c0);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_scale(F* f, long long n, F s) {
  if (n <= 0) return;
  const long long blocks = (n + 255) / 256;
  k_scale<F><<<static_cast<unsigned>(blocks < 65536 ? blocks : 65536), 256>>>(f, n, s);
  GSIS_CUDA_CHECK_LAUNCH();
}

void launch_scale_moments(double* moments, int n_cells, double s) {
  if (n_cells <= 0) return;
  k_scale_moments<<<ceil_div(n_cells, 256), 256>>>(moments, n_cells, s);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_cell_params(const double* moments, const double* volume, const double* vdt, double mu0, double omega,
                        double Pr, CellParams<F>* out, int n_cells, double cold_bgk_T) {
  if (n_cells <= 0) return;
  k_cell_params<F><<<ceil_div(n_cells, 128), 128>>>(moments, volume, vdt, mu0, omega, Pr, out, n_cells, cold_bgk_T);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_cell_params_deq(const KineticView<F>& v, const double* moments, const double* volume, const double* vdt,
                            double mu0, double omega, double Pr, CellParams<F>* out, int* flags, int c0, int nc) {
  if (nc <= 0) return;
  k_cell_params_deq<F><<<nc, kDeqThreads>>>(v, moments, volume, vdt, mu0, omega, Pr, out, flags, c0);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_maxwell_reflection(const KineticView<F>& v) {
  for (int b0 = 0; b0 < v.n_bfaces; b0 += 16) {
    const dim3 grid((v.nv + 127) / 128, std::min(16, v.n_bfaces - b0));
    k_maxwell_reflection<false,F><<<grid, 128>>>(v, b0);
  }
  for (int b0 = 0; b0 < v.n_bfaces; b0 += 16) {
    const dim3 grid((v.nv + 127) / 128, std::min(16, v.n_bfaces - b0));
    k_maxwell_reflection<true,F><<<grid, 128>>>(v, b0);
  }
}

template <class F>
void launch_boundary_sums(const KineticView<F>& v, double* out, int b0, int nb) {
  if (nb <= 0) return;
  k_boundary_sums<F><<<nb, kBoundaryThreads>>>(v, out, b0);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_internal_face_mass_flux(const KineticView<F>& v, const std::int32_t* cells, const std::int32_t* entries,
                                    double* out, int i0, int ni) {
  if (ni <= 0) return;
  k_internal_face_mass_flux<F><<<ni, kBoundaryThreads>>>(v, cells, entries, out, i0);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_gradient(const KineticView<F>& v, const ChunkView<F>& ch, int c0, int nc) {
  if (nc <= 0 || ch.kc <= 0) return;
  const dim3 grid(static_cast<unsigned>(nc), static_cast<unsigned>(ceil_div(ch.kc, kCellTile)));
  k_gradient<F><<<grid, kCellTile>>>(v, ch, c0);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_residual(const KineticView<F>& v, const ChunkView<F>& ch, double* partial, int partial_stride, int c0,
                     int nc) {
  if (nc <= 0 || ch.kc <= 0) return;
  const dim3 grid(static_cast<unsigned>(nc), static_cast<unsigned>(ceil_div(ch.kc, kCellTile)));
  k_residual<F><<<grid, kCellTile>>>(v, ch, partial, partial_stride, c0);
  GSIS_CUDA_CHECK_LAUNCH();
}

void launch_residual_reduce(const double* partial, int partial_stride, int n_tiles, double* residual_cell,
                            int n_cells) {
  if (n_cells <= 0) return;
  k_residual_reduce<<<ceil_div(n_cells, 256), 256>>>(partial, partial_stride, n_tiles, residual_cell, n_cells);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_sweep_forward(const KineticView<F>& v, const ChunkView<F>& ch, const std::int32_t* block_offsets,
                          int n_blocks, int s0, int s1) {
  if (n_blocks <= 0 || ch.kc <= 0) return;
  const dim3 grid(static_cast<unsigned>(ceil_div(ch.kc, kSweepTile)), static_cast<unsigned>(n_blocks));
  k_sweep_forward<F><<<grid, kSweepTile>>>(v, ch, block_offsets, s0, s1);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_sweep_backward(const KineticView<F>& v, const ChunkView<F>& ch, const std::int32_t* block_offsets,
                           int n_blocks, int s0, int s1) {
  if (n_blocks <= 0 || ch.kc <= 0) return;
  const dim3 grid(static_cast<unsigned>(ceil_div(ch.kc, kSweepTile)), static_cast<unsigned>(n_blocks));
  k_sweep_backward<F><<<grid, kSweepTile>>>(v, ch, block_offsets, s0, s1);
  GSIS_CUDA_CHECK_LAUNCH();
}

template <class F>
void launch_gsis_correction(const KineticView<F>& v, const double* w_half, const double* w_new, int* replaced,
                            double* shift, int c0, int nc, int mode) {
  if (nc <= 0) return;
  k_gsis_correction<F><<<nc, kCorrectionThreads>>>(v, w_half, w_new, replaced, shift, c0, mode);
  GSIS_CUDA_CHECK_LAUNCH();
}

#define GSIS_KINETIC_INSTANTIATE(F)                                                                              \
  template void launch_set_equilibrium<F>(const KineticView<F>&, const double*, double*, int, int);              \
  template void launch_moments<F>(const KineticView<F>&, double*, double*, int, int);                            \
  template void launch_scale<F>(F*, long long, F);                                                               \
  template void launch_cell_params<F>(const double*, const double*, const double*, double, double, double,       \
                                      CellParams<F>*, int, double);                                                \
  template void launch_cell_params_deq<F>(const KineticView<F>&, const double*, const double*, const double*,   \
                                          double, double, double, CellParams<F>*, int*, int, int);               \
  template void launch_maxwell_reflection<F>(const KineticView<F>&);                                           \
  template void launch_boundary_sums<F>(const KineticView<F>&, double*, int, int);                               \
  template void launch_internal_face_mass_flux<F>(const KineticView<F>&, const std::int32_t*, const std::int32_t*, \
                                                  double*, int, int);                                            \
  template void launch_gradient<F>(const KineticView<F>&, const ChunkView<F>&, int, int);                        \
  template void launch_residual<F>(const KineticView<F>&, const ChunkView<F>&, double*, int, int, int);          \
  template void launch_sweep_forward<F>(const KineticView<F>&, const ChunkView<F>&, const std::int32_t*, int,    \
                                        int, int);                                                               \
  template void launch_sweep_backward<F>(const KineticView<F>&, const ChunkView<F>&, const std::int32_t*, int,   \
                                         int, int);                                                              \
  template void launch_gsis_correction<F>(const KineticView<F>&, const double*, const double*, int*, double*,    \
                                          int, int, int);

GSIS_KINETIC_INSTANTIATE(float)
GSIS_KINETIC_INSTANTIATE(double)

#undef GSIS_KINETIC_INSTANTIATE

}  // namespace gsis::kinetic::cuda
