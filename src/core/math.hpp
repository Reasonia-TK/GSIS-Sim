#pragma once
// 物理定数と、CPU・GPU 共通で使える数学関数。
// 数学関数は float / double の多重定義を明示し、nvcc と MSVC の両方で同じ名前で呼べるようにする。

#include <cmath>

#include "core/types.hpp"

namespace gsis {

inline constexpr double kPi = 3.14159265358979323846;
inline constexpr double kSqrtPi = 1.77245385090551602730;
inline constexpr double kBoltzmann = 1.380649e-23;  // J/K(2019 年 SI の定義値)

namespace dmath {

GSIS_HD inline double sqrt(double x) { return ::sqrt(x); }
GSIS_HD inline float sqrt(float x) { return ::sqrtf(x); }
GSIS_HD inline double exp(double x) { return ::exp(x); }
GSIS_HD inline float exp(float x) { return ::expf(x); }
GSIS_HD inline double erf(double x) { return ::erf(x); }
GSIS_HD inline float erf(float x) { return ::erff(x); }
GSIS_HD inline double erfc(double x) { return ::erfc(x); }
GSIS_HD inline float erfc(float x) { return ::erfcf(x); }
GSIS_HD inline double abs(double x) { return ::fabs(x); }
GSIS_HD inline float abs(float x) { return ::fabsf(x); }
GSIS_HD inline double pow(double x, double y) { return ::pow(x, y); }
GSIS_HD inline float pow(float x, float y) { return ::powf(x, y); }

template <class T>
GSIS_HD constexpr T min(T a, T b) {
  return b < a ? b : a;
}
template <class T>
GSIS_HD constexpr T max(T a, T b) {
  return a < b ? b : a;
}
template <class T>
GSIS_HD constexpr T clamp(T x, T lo, T hi) {
  return x < lo ? lo : (hi < x ? hi : x);
}

}  // namespace dmath
}  // namespace gsis
