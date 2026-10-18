#pragma once
// テストの出力先(ビルドディレクトリの中。CMake が GSIS_TEST_OUTPUT_DIR を渡す)

#include <filesystem>

#include "core/paths.hpp"

namespace gsis::test {

inline std::filesystem::path output_dir() {
  const std::filesystem::path dir = path_from_utf8(GSIS_TEST_OUTPUT_DIR);
  std::filesystem::create_directories(dir);
  return dir;
}

}  // namespace gsis::test
