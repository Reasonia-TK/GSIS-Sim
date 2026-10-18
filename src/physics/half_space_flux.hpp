#pragma once
// Maxwell 分布の半空間流束の閉形式(論文 付録 B.1 の式 (68)–(74))と、
// 状態 W = (p, u_x, u_y, u_z, T) に関するヤコビアン(付録 C.1 の式 (79)(80))。CPU・GPU 共有。
//
//   F^±_eq(W; n) = ∫_{Ξ^±} (ξ·n) ψ(ξ) f^eq(W; ξ) dξ、ψ = (1, ξ, |ξ|²/2)
//   Ξ^+ = {ξ·n > 0}(流出側)、Ξ^- = {ξ·n < 0}(流入側)。n は流体領域から外向きの単位法線。
//   成分の順は [質量, 運動量 x, y, z, エネルギー]。ρ = p / (R T)。
//
// 論文は局所座標 (n, t1, t2) で式 (70) を書き、行列 P (73) で全体座標に戻す (74)。
// ここでは同じ式を接線方向の速度ベクトル u_t = u − u_n n で書き直し、全体座標で直接計算する:
//   F_ρ = ρ I1、F_m = ρ (I2 n + I1 u_t)、F_E = (ρ/2) [I3 + (|u_t|² + 2a²) I1]
// (u_t1 t1 + u_t2 t2 = u_t なので式 (74) と同じ。接線ベクトルの選び方に依存しない)
// I_k^± は式 (72)、η = u_n/(√2 a)、a = √(R T)、C_± = ½[1 ± erf η] = ½ erfc(∓η)、G = e^{−η²}/√(2π)。

#include "core/math.hpp"
#include "core/vec3.hpp"

namespace gsis {

// 5 成分のベクトル(流束 [ρ, m_x, m_y, m_z, E] または状態 [p, u_x, u_y, u_z, T])
template <class T>
struct Vec5T {
  T v[5];
  GSIS_HD constexpr T& operator[](int i) { return v[i]; }
  GSIS_HD constexpr const T& operator[](int i) const { return v[i]; }
};

// 5×5 行列(行: 流束の成分、列: 状態の成分)
template <class T>
struct Mat5T {
  T a[5][5];
  GSIS_HD constexpr T& operator()(int r, int c) { return a[r][c]; }
  GSIS_HD constexpr const T& operator()(int r, int c) const { return a[r][c]; }
};

using Vec5 = Vec5T<double>;
using Mat5 = Mat5T<double>;

// 半空間モーメント(式 (71)(72))。side = +1 なら Ξ^+、−1 なら Ξ^-。
template <class T>
struct HalfRangeMoments {
  T C;    // C_±
  T G;    // G
  T I1;   // ∫ ξ_n f_n dξ_n(f_n は 1 次元の正規化された Maxwell 分布)
  T I2;   // ∫ ξ_n² f_n dξ_n
  T I3;   // ∫ ξ_n³ f_n dξ_n
  T dI1_da2;  // ∂I1/∂(a²) = ±G / (2a)(∂I1/∂T = R · dI1_da2)
};

template <class T>
GSIS_HD inline HalfRangeMoments<T> half_range_moments(int side, T un, T a) {
  const T s = side > 0 ? T(1) : T(-1);
  const T eta = un / (T(1.4142135623730950488) * a);
  HalfRangeMoments<T> m;
  m.C = T(0.5) * dmath::erfc(-s * eta);
  m.G = dmath::exp(-eta * eta) * T(0.39894228040143267794);  // 1/√(2π)
  const T a2 = a * a;
  m.I1 = un * m.C + s * a * m.G;
  m.I2 = (un * un + a2) * m.C + s * un * a * m.G;
  m.I3 = (un * un * un + T(3) * un * a2) * m.C + s * a * (un * un + T(2) * a2) * m.G;
  m.dI1_da2 = s * m.G / (T(2) * a);
  return m;
}

// 半空間流束 F^±_eq(W; n)。side = +1 / −1、W = (p, u, temp)、n は単位ベクトル。
template <class T>
GSIS_HD inline Vec5T<T> half_space_flux(int side, T p, const Vec3T<T>& u, T temp, T R,
                                        const Vec3T<T>& n) {
  const T a2 = R * temp;
  const T a = dmath::sqrt(a2);
  const T rho = p / a2;
  const T un = dot(u, n);
  const Vec3T<T> ut = u - un * n;
  const T ut2 = dot(ut, ut);
  const HalfRangeMoments<T> m = half_range_moments(side, un, a);
  Vec5T<T> F;
  F[0] = rho * m.I1;
  F[1] = rho * (m.I2 * n.x + m.I1 * ut.x);
  F[2] = rho * (m.I2 * n.y + m.I1 * ut.y);
  F[3] = rho * (m.I2 * n.z + m.I1 * ut.z);
  F[4] = T(0.5) * rho * (m.I3 + (ut2 + T(2) * a2) * m.I1);
  return F;
}

// ヤコビアン J^±_eq = ∂F^±_eq / ∂W、W = (p, u_x, u_y, u_z, T)(式 (79)(80) を全体座標で書いたもの)。
//   F = ρ h、ρ = p/(R T) より J = ρ [h/p, ∂h/∂u, ∂h/∂T − h/T]
//   ∂h_ρ/∂u = C nᵀ、∂h_m/∂u = I1 (I + n nᵀ) + C u_t nᵀ、∂h_E/∂u = ½[3 I2 n + 2 I1 u_t + (|u_t|²+2a²) C n]ᵀ
//   ∂h_ρ/∂T = I1T、∂h_m/∂T = R C n + I1T u_t、∂h_E/∂T = ½[5 R I1 + (|u_t|²+2a²) I1T]
//   (I1T = ∂I1/∂T = ±a G/(2T)。h_m の ∂/∂u は局所座標の h_{u_n}, h_{u_t1}, h_{u_t2} を回転したもの)
template <class T>
GSIS_HD inline Mat5T<T> half_space_flux_jacobian(int side, T p, const Vec3T<T>& u, T temp, T R,
                                                 const Vec3T<T>& n) {
  const T a2 = R * temp;
  const T a = dmath::sqrt(a2);
  const T rho = p / a2;
  const T un = dot(u, n);
  const Vec3T<T> ut = u - un * n;
  const T ut2 = dot(ut, ut);
  const HalfRangeMoments<T> m = half_range_moments(side, un, a);
  const T I1T = R * m.dI1_da2;
  const T e2 = ut2 + T(2) * a2;

  // h = F / ρ
  T h[5];
  h[0] = m.I1;
  h[1] = m.I2 * n.x + m.I1 * ut.x;
  h[2] = m.I2 * n.y + m.I1 * ut.y;
  h[3] = m.I2 * n.z + m.I1 * ut.z;
  h[4] = T(0.5) * (m.I3 + e2 * m.I1);

  // ∂h/∂T
  T hT[5];
  hT[0] = I1T;
  hT[1] = R * m.C * n.x + I1T * ut.x;
  hT[2] = R * m.C * n.y + I1T * ut.y;
  hT[3] = R * m.C * n.z + I1T * ut.z;
  hT[4] = T(0.5) * (T(5) * R * m.I1 + e2 * I1T);

  Mat5T<T> J;
  const T nn[3] = {n.x, n.y, n.z};
  const T tt[3] = {ut.x, ut.y, ut.z};
  for (int r = 0; r < 5; ++r) {
    J(r, 0) = rho * h[r] / p;
    J(r, 4) = rho * (hT[r] - h[r] / temp);
  }
  for (int c = 0; c < 3; ++c) {
    J(0, c + 1) = rho * m.C * nn[c];
    for (int r = 0; r < 3; ++r) {
      const T delta = r == c ? T(1) : T(0);
      J(r + 1, c + 1) = rho * (m.I1 * (delta + nn[r] * nn[c]) + m.C * tt[r] * nn[c]);
    }
    J(4, c + 1) = rho * T(0.5) * (T(3) * m.I2 * nn[c] + T(2) * m.I1 * tt[c] + e2 * m.C * nn[c]);
  }
  return J;
}

// 全空間の流束(F^+ + F^-): [ρ u_n, ρ u u_n + p n, (ρE + p) u_n]。検査用。
template <class T>
GSIS_HD inline Vec5T<T> full_space_flux(T p, const Vec3T<T>& u, T temp, T R, const Vec3T<T>& n) {
  const T rho = p / (R * temp);
  const T un = dot(u, n);
  const T E = T(1.5) * R * temp + T(0.5) * dot(u, u);
  Vec5T<T> F;
  F[0] = rho * un;
  F[1] = rho * u.x * un + p * n.x;
  F[2] = rho * u.y * un + p * n.y;
  F[3] = rho * u.z * un + p * n.z;
  F[4] = (rho * E + p) * un;
  return F;
}

}  // namespace gsis
