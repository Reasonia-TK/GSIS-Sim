#pragma once
// Venkatakrishnan リミッタと制限付き線形再構成(論文の式 (15)、docs/design_cis_gpu.md §5)。
// 運動論(分布関数)と PBC(境界面への (p, u, T) の再構成、式 (36))で同じ関数を使う。CPU・GPU 共有。
//
//   面 f ごとに Δ2 = ∇φ_i·(x_f − x_i)、Δ1 = φ_max − φ_i(Δ2 > 0)または φ_min − φ_i(Δ2 < 0)
//   φ_f = [(Δ1² + ε²) + 2 Δ1 Δ2] / [Δ1² + 2 Δ2² + Δ1 Δ2 + ε²](Δ2 = 0 なら 1)
//   セルのリミッタ φ_i = min_f φ_f(論文に合わせて [0, 1] に収める)
//   ε² = (K h)³、h = V^{1/3}(K はケースのパラメータ)

#include "core/math.hpp"
#include "core/vec3.hpp"

namespace gsis {

// 1 つの面に対するリミッタの値。dmax = φ_max − φ_i(≥ 0)、dmin = φ_min − φ_i(≤ 0)。
template <class T>
GSIS_HD inline T venkatakrishnan_face(T d2, T dmax, T dmin, T eps2) {
  T d1;
  if (d2 > T(0)) {
    d1 = dmax;
  } else if (d2 < T(0)) {
    d1 = dmin;
  } else {
    return T(1);
  }
  const T num = (d1 * d1 + eps2) + T(2) * d1 * d2;
  const T den = d1 * d1 + T(2) * d2 * d2 + d1 * d2 + eps2;
  if (!(den > T(0))) return T(0);  // Δ1 = 0 かつ ε = 0 の退化(平坦な極値)では勾配を使わない
  return dmath::clamp(num / den, T(0), T(1));
}

// ε² = (K h)³、h = V^{1/3}
template <class T>
GSIS_HD inline T venkatakrishnan_eps2(T K, T cell_volume) {
  const T kh3 = K * K * K * cell_volume;  // (K V^{1/3})³ = K³ V
  return kh3;
}

// セル i のリミッタ値を、面ごとの Δ2 から累積する補助。使い方:
//   VenkatakrishnanAccumulator<T> acc(dmax, dmin, eps2);
//   for 面 f: acc.add(dot(grad, x_f - x_i));
//   T phi = acc.value();
template <class T>
struct VenkatakrishnanAccumulator {
  T dmax, dmin, eps2;
  T phi = T(1);
  GSIS_HD VenkatakrishnanAccumulator(T dmax_, T dmin_, T eps2_) : dmax(dmax_), dmin(dmin_), eps2(eps2_) {}
  GSIS_HD void add(T d2) { phi = dmath::min(phi, venkatakrishnan_face(d2, dmax, dmin, eps2)); }
  GSIS_HD T value() const { return phi; }
};

// 制限付きの線形再構成: φ_f = φ_i + lim ∇φ_i·(x_f − x_i)
template <class T>
GSIS_HD inline T reconstruct_limited(T phi_i, const Vec3T<T>& grad, T lim, const Vec3T<T>& dx) {
  return phi_i + lim * dot(grad, dx);
}

}  // namespace gsis
