#pragma once
// 版とビルド構成の情報(CMake が build_info.cpp を生成する)。

#include <string>
#include <string_view>

namespace gsis {

std::string_view version_string();      // 例: "0.1.0"
std::string build_description();        // コンパイラ、ビルドの種類、CUDA の有無と版
bool built_with_cuda();

}  // namespace gsis
