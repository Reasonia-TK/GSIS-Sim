#pragma once
// 3 次元ベクトル。CPU(FP64)と GPU(FP32 / FP64)で共有する。
// 集成体(aggregate)なので Vec3{1, 2, 3} で初期化する。Vec3 v{} はゼロ。

#include <type_traits>

#include "core/math.hpp"
#include "core/types.hpp"

namespace gsis {

template <class T>
struct Vec3T {
  T x, y, z;

  GSIS_HD constexpr T operator[](int i) const { return i == 0 ? x : (i == 1 ? y : z); }
  GSIS_HD constexpr T& operator[](int i) { return i == 0 ? x : (i == 1 ? y : z); }

  GSIS_HD constexpr Vec3T& operator+=(const Vec3T& b) {
    x += b.x;
    y += b.y;
    z += b.z;
    return *this;
  }
  GSIS_HD constexpr Vec3T& operator-=(const Vec3T& b) {
    x -= b.x;
    y -= b.y;
    z -= b.z;
    return *this;
  }
  GSIS_HD constexpr Vec3T& operator*=(T s) {
    x *= s;
    y *= s;
    z *= s;
    return *this;
  }
  GSIS_HD constexpr Vec3T& operator/=(T s) {
    x /= s;
    y /= s;
    z /= s;
    return *this;
  }
};

using Vec3 = Vec3T<Real>;
using Vec3f = Vec3T<float>;

template <class T>
GSIS_HD constexpr Vec3T<T> operator+(Vec3T<T> a, const Vec3T<T>& b) {
  return a += b;
}
template <class T>
GSIS_HD constexpr Vec3T<T> operator-(Vec3T<T> a, const Vec3T<T>& b) {
  return a -= b;
}
template <class T>
GSIS_HD constexpr Vec3T<T> operator-(const Vec3T<T>& a) {
  return {-a.x, -a.y, -a.z};
}
// スカラーの型はベクトルの型に合わせる(2.0 * Vec3f も書けるように、スカラーからは型を推論しない)
template <class T>
GSIS_HD constexpr Vec3T<T> operator*(std::type_identity_t<T> s, Vec3T<T> a) {
  return a *= s;
}
template <class T>
GSIS_HD constexpr Vec3T<T> operator*(Vec3T<T> a, std::type_identity_t<T> s) {
  return a *= s;
}
template <class T>
GSIS_HD constexpr Vec3T<T> operator/(Vec3T<T> a, std::type_identity_t<T> s) {
  return a /= s;
}
template <class T>
GSIS_HD constexpr bool operator==(const Vec3T<T>& a, const Vec3T<T>& b) {
  return a.x == b.x && a.y == b.y && a.z == b.z;
}

template <class T>
GSIS_HD constexpr T dot(const Vec3T<T>& a, const Vec3T<T>& b) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}
template <class T>
GSIS_HD constexpr Vec3T<T> cross(const Vec3T<T>& a, const Vec3T<T>& b) {
  return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
template <class T>
GSIS_HD constexpr T norm2(const Vec3T<T>& a) {
  return dot(a, a);
}
template <class T>
GSIS_HD inline T norm(const Vec3T<T>& a) {
  return dmath::sqrt(norm2(a));
}
// 長さ 1 にしたもの(長さ 0 なら 0 ベクトルを返す)
template <class T>
GSIS_HD inline Vec3T<T> normalized(const Vec3T<T>& a) {
  const T n = norm(a);
  return n > T(0) ? a / n : Vec3T<T>{T(0), T(0), T(0)};
}
// 精度を変える(例: vec_cast<float>(v))
template <class U, class T>
GSIS_HD constexpr Vec3T<U> vec_cast(const Vec3T<T>& a) {
  return {static_cast<U>(a.x), static_cast<U>(a.y), static_cast<U>(a.z)};
}

}  // namespace gsis
