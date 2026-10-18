#pragma once
// maxwell_moments の内部: .cu のカーネル起動関数の宣言(標準の型だけを使う)。

namespace gsis::gpu::detail {

// GPU 上の速度格子(量ごとの配列)
struct DeviceGridView {
  const double* xd = nullptr;
  const double* yd = nullptr;
  const double* zd = nullptr;
  const float* xf = nullptr;
  const float* yf = nullptr;
  const float* zf = nullptr;
  int nv = 0;
  double weight = 0.0;
};

// 1 セルあたりの出力: ρ, u_x, u_y, u_z, T, p, σ_xx, σ_yy, σ_zz, σ_xy, σ_yz, σ_xz, q_x, q_y, q_z
inline constexpr int kMomentsPerCell = 15;
// 1 セルあたりの入力: ρ, u_x, u_y, u_z, T
inline constexpr int kStatePerCell = 5;

// セル [cell_begin, cell_begin + cell_count) を計算する(同期しない)
void launch_maxwell_moments(bool fp32, const DeviceGridView& grid, const double* states,
                            int cell_begin, int cell_count, double R, double* out);

}  // namespace gsis::gpu::detail
