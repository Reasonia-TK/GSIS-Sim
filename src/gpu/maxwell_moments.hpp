#pragma once
// GPU のスモークテスト: 多数のセルに (ρ, u, T) を与え、速度格子上の f^eq のモーメントを GPU で計算する。
// - 物理の式は physics/maxwell.hpp の __host__ __device__ 関数をそのまま使う
// - f^eq は FP32 か FP64 で評価し、集計は常に FP64(フェーズ 2 の「f は FP32、集計は FP64」と同じ形)
// - 集計に atomic を使わない: 1 セルを 1 ブロックが受け持ち、スレッドごとに決まった順に足してから、
//   共有メモリの木構造で足す(同じ入力なら毎回同じ結果)
// このヘッダは CUDA のヘッダを含まない。

#include <span>
#include <vector>

#include "core/vec3.hpp"
#include "physics/moments.hpp"
#include "velocity/velocity_grid.hpp"

namespace gsis::gpu {

struct CellState {
  double rho = 0.0;
  Vec3 u{};
  double T = 0.0;
};

enum class Precision { FP32, FP64 };  // f^eq を評価する精度

struct KernelStats {
  double kernel_ms = 0.0;  // カーネルの合計時間(CUDA イベントで計測)
  int launches = 0;        // カーネルを起動した回数
};

// GPU 版。max_cells_per_launch ごとにカーネルを分けて起動する(WDDM の TDR を避けるため)。
std::vector<Moments> maxwell_moments_gpu(const VelocityGrid& grid, std::span<const CellState> cells,
                                         double R, Precision precision, KernelStats* stats = nullptr,
                                         int max_cells_per_launch = 4096);

// CPU 版(比較用)。同じ関数で同じ精度の f^eq を作り、compute_moments で集計する。
std::vector<Moments> maxwell_moments_cpu(const VelocityGrid& grid, std::span<const CellState> cells,
                                         double R, Precision precision);

// 乱数で状態を作る(無次元: ρ ∈ [0.5, 2]、各成分 u ∈ [−u_max, u_max]、T ∈ [0.5, 2])。seed で決まる。
std::vector<CellState> make_random_states(int n_cells, unsigned long long seed, double u_max = 1.5);

}  // namespace gsis::gpu
