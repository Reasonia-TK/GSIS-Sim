#pragma once
// 離散分布関数からマクロ量を計算する(CPU、集計は FP64)。
//
//   ρ = Σ w f、ρu = Σ w ξ f
//   Π = Σ w c c f(c = ξ − u)、p = tr(Π)/3、T = p/(ρR)、σ = Π − p I、q = ½ Σ w |c|² c f
// 2 段階で計算する(先に u を求め、次に c = ξ − u で 2 次・3 次のモーメントを足す)。
// 足す順序は速度点の番号順で固定(同じ入力なら同じ結果)。

#include <array>
#include <span>

#include "core/vec3.hpp"
#include "velocity/velocity_grid.hpp"

namespace gsis {

struct Moments {
  double rho = 0.0;
  Vec3 u{};
  double T = 0.0;
  double p = 0.0;
  std::array<double, 6> sigma{};  // 応力偏差 σ の (xx, yy, zz, xy, yz, xz)(VTK の対称テンソルと同じ順)
  Vec3 q{};                       // 熱流束
};

// f は速度点の順(VelocityGrid の番号)に並んだ 1 セル分。R は単位系の気体定数。
// ρ <= 0 なら gsis::Error。
Moments compute_moments(const VelocityGrid& grid, std::span<const double> f, double R);
Moments compute_moments(const VelocityGrid& grid, std::span<const float> f, double R);

// 保存量 (ρ, ρu, ρE) だけ(1 段階)
struct ConservedMoments {
  double rho = 0.0;
  Vec3 momentum{};
  double energy = 0.0;  // ρE = Σ w ½|ξ|² f
};
ConservedMoments compute_conserved(const VelocityGrid& grid, std::span<const double> f);

// 2 組のモーメントの差の最大値(セルごとに次の量で無次元化した差の最大):
//   ρ, T, p: 相対差。u: |Δu| / sqrt(R T)。σ: max_ij |Δσ_ij| / p。q: |Δq| / (p sqrt(R T))
struct MomentDifference {
  double rho = 0.0;
  double u = 0.0;
  double T = 0.0;
  double p = 0.0;
  double sigma = 0.0;
  double q = 0.0;
  double max() const;
};
MomentDifference max_moment_difference(std::span<const Moments> a, std::span<const Moments> b,
                                       double R);

}  // namespace gsis
