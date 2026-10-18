#pragma once
// Maxwell 分布と Shakhov の目標分布(論文の式 (2)(3))。CPU・GPU 共有の関数。
//
//   f^eq(ρ, u, T; ξ) = ρ (2π R T)^{-3/2} exp(−|ξ − u|² / (2 R T))
//   f^S = f^eq [1 + (1 − Pr) (c·q) / (5 p R T) (|c|²/(R T) − 5)]、c = ξ − u、p = ρ R T
//
// R は使う単位系の気体定数(SI なら k_B/m、ソルバー内部の無次元量なら 1)。
// T(テンプレート引数)は float か double。セルごとに係数を前計算する形(*Params)も用意する。

#include "core/math.hpp"
#include "core/vec3.hpp"

namespace gsis {

// f^eq = A exp(−B |ξ − u|²)
template <class T>
struct MaxwellParams {
  Vec3T<T> u;
  T A;  // ρ (2π R T)^{-3/2}
  T B;  // 1 / (2 R T)
};

template <class T>
GSIS_HD inline MaxwellParams<T> make_maxwell_params(T rho, const Vec3T<T>& u, T temp, T R) {
  const T theta = R * temp;
  const T two_pi_theta = T(2.0 * kPi) * theta;
  MaxwellParams<T> m;
  m.u = u;
  m.A = rho / (two_pi_theta * dmath::sqrt(two_pi_theta));
  m.B = T(1) / (T(2) * theta);
  return m;
}

template <class T>
GSIS_HD inline T maxwellian(const MaxwellParams<T>& m, const Vec3T<T>& xi) {
  const Vec3T<T> c = xi - m.u;
  return m.A * dmath::exp(-m.B * dot(c, c));
}

template <class T>
GSIS_HD inline T maxwellian(T rho, const Vec3T<T>& u, T temp, T R, const Vec3T<T>& xi) {
  return maxwellian(make_maxwell_params(rho, u, temp, R), xi);
}

// f^S = f^eq [1 + coef (c·q) (|c|² / θ − 5)]、θ = R T、coef = (1 − Pr) / (5 p θ)
template <class T>
struct ShakhovParams {
  MaxwellParams<T> eq;
  Vec3T<T> q;
  T theta;
  T coef;
};

template <class T>
GSIS_HD inline ShakhovParams<T> make_shakhov_params(T rho, const Vec3T<T>& u, T temp,
                                                    const Vec3T<T>& q, T R, T Pr) {
  ShakhovParams<T> s;
  s.eq = make_maxwell_params(rho, u, temp, R);
  s.q = q;
  s.theta = R * temp;
  const T p = rho * s.theta;
  s.coef = (T(1) - Pr) / (T(5) * p * s.theta);
  return s;
}

template <class T>
GSIS_HD inline T shakhov(const ShakhovParams<T>& s, const Vec3T<T>& xi) {
  const Vec3T<T> c = xi - s.eq.u;
  const T c2 = dot(c, c);
  const T feq = s.eq.A * dmath::exp(-s.eq.B * c2);
  return feq * (T(1) + s.coef * dot(c, s.q) * (c2 / s.theta - T(5)));
}

template <class T>
GSIS_HD inline T shakhov(T rho, const Vec3T<T>& u, T temp, const Vec3T<T>& q, T R, T Pr,
                         const Vec3T<T>& xi) {
  return shakhov(make_shakhov_params(rho, u, temp, q, R, Pr), xi);
}

}  // namespace gsis
