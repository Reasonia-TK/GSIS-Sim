#include "gpu/cuda_check.hpp"

#include <format>
#include <string>

#include "core/error.hpp"

namespace gsis::gpu {

void throw_cuda_error(cudaError_t error, const char* expression, const char* file, int line) {
  const std::string path(file);
  const std::size_t pos = path.find_last_of("/\\");
  const std::string name = pos == std::string::npos ? path : path.substr(pos + 1);
  throw Error(std::format("CUDA の呼び出しが失敗しました: {}({}: {})", expression,
                          cudaGetErrorName(error), cudaGetErrorString(error)),
              std::format("{}:{}", name, line));
}

}  // namespace gsis::gpu
