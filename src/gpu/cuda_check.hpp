#pragma once
// CUDA ランタイム API の誤りを検査するマクロ。.cu と .cpp の両方から使う。
//
//   GSIS_CUDA_CHECK(cudaMalloc(&p, n));   // 失敗したら gsis::Error(関数名・誤りの名前・場所つき)
//   kernel<<<...>>>(...); GSIS_CUDA_CHECK_LAUNCH();

#include <cuda_runtime.h>

namespace gsis::gpu {

[[noreturn]] void throw_cuda_error(cudaError_t error, const char* expression, const char* file,
                                   int line);

}  // namespace gsis::gpu

#define GSIS_CUDA_CHECK(expr)                                                      \
  do {                                                                             \
    const cudaError_t gsis_cuda_error_ = (expr);                                   \
    if (gsis_cuda_error_ != cudaSuccess) {                                         \
      ::gsis::gpu::throw_cuda_error(gsis_cuda_error_, #expr, __FILE__, __LINE__);  \
    }                                                                              \
  } while (false)

#define GSIS_CUDA_CHECK_LAUNCH() GSIS_CUDA_CHECK(cudaGetLastError())
