#pragma once
// PBC マクロソルバーで使う小さな密行列・ベクトルの補助(FP64、CPU のみ)。
//   - Vec5 / Mat5(physics/half_space_flux.hpp)の演算
//   - 4×4 ブロック(行優先 16 個)の演算
//   - 対称 3×3 行列の最大固有値(||B||_2 = sqrt(λ_max(BᵀB)) に使う)

#include <array>
#include <cmath>

#include "core/math.hpp"
#include "core/vec3.hpp"
#include "physics/half_space_flux.hpp"

namespace gsis::macro {

using Block4 = std::array<double, 16>;  // 4×4 ブロック(行優先)

inline Vec5 vec5_zero() { return Vec5{{0.0, 0.0, 0.0, 0.0, 0.0}}; }

inline Vec5 vec5_add(const Vec5& a, const Vec5& b) {
  Vec5 c;
  for (int k = 0; k < 5; ++k) c[k] = a[k] + b[k];
  return c;
}

inline Vec5 vec5_sub(const Vec5& a, const Vec5& b) {
  Vec5 c;
  for (int k = 0; k < 5; ++k) c[k] = a[k] - b[k];
  return c;
}

inline Vec5 vec5_scale(const Vec5& a, double s) {
  Vec5 c;
  for (int k = 0; k < 5; ++k) c[k] = s * a[k];
  return c;
}

inline Mat5 mat5_zero() {
  Mat5 m;
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) m(r, c) = 0.0;
  }
  return m;
}

inline Mat5 mat5_scale(const Mat5& a, double s) {
  Mat5 m;
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) m(r, c) = s * a(r, c);
  }
  return m;
}

inline Mat5 mat5_add(const Mat5& a, const Mat5& b) {
  Mat5 m;
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) m(r, c) = a(r, c) + b(r, c);
  }
  return m;
}

inline Mat5 mat5_mul(const Mat5& a, const Mat5& b) {
  Mat5 m;
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) {
      double s = 0.0;
      for (int k = 0; k < 5; ++k) s += a(r, k) * b(k, c);
      m(r, c) = s;
    }
  }
  return m;
}

inline Vec5 mat5_vec(const Mat5& a, const Vec5& x) {
  Vec5 y;
  for (int r = 0; r < 5; ++r) {
    double s = 0.0;
    for (int k = 0; k < 5; ++k) s += a(r, k) * x[k];
    y[r] = s;
  }
  return y;
}

// 速度の取り出し: W = (p, ux, uy, uz, T)
inline Vec3 velocity_of(const Vec5& W) { return Vec3{W[1], W[2], W[3]}; }

inline Vec5 make_state(double p, const Vec3& u, double T) { return Vec5{{p, u.x, u.y, u.z, T}}; }

// 対称 3×3 行列 [a00 a01 a02; a01 a11 a12; a02 a12 a22] の最大固有値(解析解)
inline double sym3_max_eigenvalue(double a00, double a11, double a22, double a01, double a12,
                                  double a02) {
  const double p1 = a01 * a01 + a02 * a02 + a12 * a12;
  if (p1 == 0.0) return dmath::max(a00, dmath::max(a11, a22));
  const double q = (a00 + a11 + a22) / 3.0;
  const double b00 = a00 - q;
  const double b11 = a11 - q;
  const double b22 = a22 - q;
  const double p2 = b00 * b00 + b11 * b11 + b22 * b22 + 2.0 * p1;
  const double p = std::sqrt(p2 / 6.0);
  // B = (A − qI)/p、r = det(B)/2
  const double det = b00 * (b11 * b22 - a12 * a12) - a01 * (a01 * b22 - a12 * a02) +
                     a02 * (a01 * a12 - b11 * a02);
  const double r = det / (2.0 * p * p * p);
  double phi;
  if (r <= -1.0) {
    phi = kPi / 3.0;
  } else if (r >= 1.0) {
    phi = 0.0;
  } else {
    phi = std::acos(r) / 3.0;
  }
  return q + 2.0 * p * std::cos(phi);
}

// 3×3 行列 B(行優先 9 個)の 2 ノルム(最大特異値)
inline double mat3_norm2(const double b[9]) {
  // BᵀB
  double m[3][3];
  for (int i = 0; i < 3; ++i) {
    for (int j = 0; j < 3; ++j) {
      double s = 0.0;
      for (int k = 0; k < 3; ++k) s += b[3 * k + i] * b[3 * k + j];
      m[i][j] = s;
    }
  }
  const double lam = sym3_max_eigenvalue(m[0][0], m[1][1], m[2][2], m[0][1], m[1][2], m[0][2]);
  return std::sqrt(dmath::max(lam, 0.0));
}

}  // namespace gsis::macro
