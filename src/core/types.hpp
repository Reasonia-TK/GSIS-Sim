#pragma once
// 基本の型と、CPU・GPU で共有する関数に付ける修飾子。
// このヘッダは nvcc(.cu)からも読まれるので、標準ライブラリは最小限にする。

#include <cstdint>

// GSIS_HD: CPU と GPU の両方で使う関数に付ける。nvcc 以外でコンパイルするときは空になる。
#if defined(__CUDACC__)
#define GSIS_HD __host__ __device__
#else
#define GSIS_HD
#endif

namespace gsis {

using Real = double;          // CPU 側の実数(FP64)
using Index = std::int32_t;   // 節点・面・セルの番号(.pmesh の int32 に合わせる)
using Offset = std::int64_t;  // CSR のオフセットなど、2^31 を超え得る位置

inline constexpr Index kInvalidIndex = -1;

}  // namespace gsis
