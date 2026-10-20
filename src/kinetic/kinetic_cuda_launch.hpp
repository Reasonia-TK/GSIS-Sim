#pragma once
// 運動論ソルバーの CUDA カーネルの起動関数(kinetic_cuda_kernels.cu で定義)。CUDA のヘッダを含まない。
// どれも既定のストリームに積むだけで同期しない。ポインタはすべて GPU のメモリ。
// F は float か double(.cu で両方を実体化する)。

#include <cstdint>

#include "kinetic/kinetic_types.hpp"

namespace gsis::kinetic::cuda {

// f = f^eq(states)、shift = u(セル [c0, c0 + nc))。states は 1 セル 5 個(ρ, u_x, u_y, u_z, T)
template <class F>
void launch_set_equilibrium(const KineticView<F>& v, const double* states, double* shift, int c0, int nc);

// モーメント(1 セル 15 個)。shift(1 セル 3 個)のまわりの和から作り、shift を新しい u にする
template <class F>
void launch_moments(const KineticView<F>& v, double* shift, double* moments, int c0, int nc);

// f *= s(n 個)
template <class F>
void launch_scale(F* f, long long n, F s);

// モーメントの ρ, p, σ, q を s 倍する
void launch_scale_moments(double* moments, int n_cells, double s);

// セルの係数
template <class F>
void launch_cell_params(const double* moments, const double* volume, const double* vdt, double mu0, double omega,
                        double Pr, CellParams<F>* out, int n_cells, double cold_bgk_T);

// セルの係数(保存的な離散平衡分布。セル [c0, c0 + nc)。flags[c] にニュートン法の反復数、−1 は失敗)
template <class F>
void launch_cell_params_deq(const KineticView<F>& v, const double* moments, const double* volume, const double* vdt,
                            double mu0, double omega, double Pr, CellParams<F>* out, int* flags, int c0, int nc);

// 境界面 [b0, b0 + nb) の集計(1 面 20 個)
template <class F>
void launch_maxwell_reflection(const KineticView<F>& v);

template <class F>
void launch_boundary_sums(const KineticView<F>& v, double* out, int b0, int nb);

// 内部面 [i0, i0 + ni) の質量流束(フェーズ 5c)。面 i はセル cells[i] の CSR の要素 entries[i]。
// out[i] = Σ_k ϖ (ξ_k·S_out) f_face,k(1 面 = 1 ブロック、決まった順序で足す)
template <class F>
void launch_internal_face_mass_flux(const KineticView<F>& v, const std::int32_t* cells, const std::int32_t* entries,
                                    double* out, int i0, int ni);

// 制限付き勾配(セル [c0, c0 + nc)、チャンクの全点)
template <class F>
void launch_gradient(const KineticView<F>& v, const ChunkView<F>& ch, int c0, int nc);

// 定常残差。partial[c * partial_stride + tile] に 256 点ごとの Σ R² を書く
template <class F>
void launch_residual(const KineticView<F>& v, const ChunkView<F>& ch, double* partial, int partial_stride, int c0,
                     int nc);

// residual_cell[c] += Σ_tile partial[c * partial_stride + tile](tile < n_tiles)
void launch_residual_reduce(const double* partial, int partial_stride, int n_tiles, double* residual_cell,
                            int n_cells);

// LU-SGS の前進・後退。各ブロックの s 番目(前進は先頭から、後退は末尾から数える)の
// セルのうち s ∈ [s0, s1) を処理する
template <class F>
void launch_sweep_forward(const KineticView<F>& v, const ChunkView<F>& ch, const std::int32_t* block_offsets,
                          int n_blocks, int s0, int s1);
template <class F>
void launch_sweep_backward(const KineticView<F>& v, const ChunkView<F>& ch, const std::int32_t* block_offsets,
                           int n_blocks, int s0, int s1);

// GSIS の補正と負値の置換(セル [c0, c0 + nc))。置き換えた(mode = kNegativeLimit なら補正を縮めた)セルは
// replaced[c] = 1。mode は kinetic_kernels.hpp の kNegativeAll・kNegativeCreated・kNegativeLimit
template <class F>
void launch_gsis_correction(const KineticView<F>& v, const double* w_half, const double* w_new, int* replaced,
                            double* shift, int c0, int nc, int mode);

}  // namespace gsis::kinetic::cuda
