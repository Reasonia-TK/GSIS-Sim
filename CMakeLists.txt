# GSIS-PBC ソルバー(C++20 / CUDA C++20)のビルド定義。
# 手順は docs/build.md を参照。
cmake_minimum_required(VERSION 3.28)

project(gsis_pbc
  VERSION 0.1.0
  DESCRIPTION "GSIS-PBC solver for rarefied gas flows (arXiv:2610.07658)"
  LANGUAGES CXX)

option(GSIS_ENABLE_CUDA "CUDA(GPU)の部分をビルドする。OFF なら CPU の部分とテストだけをビルドする" ON)
option(GSIS_BUILD_TESTS "単体テスト(Catch2)とテスト用の補助ツールをビルドする" ON)
option(GSIS_WARNINGS_AS_ERRORS "自前のターゲットで警告をエラーとして扱う" OFF)

list(APPEND CMAKE_MODULE_PATH "${PROJECT_SOURCE_DIR}/cmake")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)  # C++20 モジュールは使わない(依存の走査を省く)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

get_property(_gsis_multi_config GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT _gsis_multi_config AND NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "ビルドの種類(Release / Debug / RelWithDebInfo)" FORCE)
endif()

if(GSIS_ENABLE_CUDA)
  # 既定は RTX 5070 Ti(計算能力 12.0 = sm_120)。
  # -DCMAKE_CUDA_ARCHITECTURES=... か環境変数 CUDAARCHS で変更できる。
  if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES AND NOT DEFINED ENV{CUDAARCHS})
    set(CMAKE_CUDA_ARCHITECTURES 120 CACHE STRING "CUDA の対象アーキテクチャ(例: 120, 89, native)")
  endif()
  enable_language(CUDA)
  find_package(CUDAToolkit REQUIRED)
  set(CMAKE_CUDA_STANDARD 20)
  set(CMAKE_CUDA_STANDARD_REQUIRED ON)
  set(CMAKE_CUDA_EXTENSIONS OFF)
  message(STATUS "GSIS: CUDA ${CMAKE_CUDA_COMPILER_VERSION}、アーキテクチャ ${CMAKE_CUDA_ARCHITECTURES}")
else()
  message(STATUS "GSIS: CUDA なしでビルドする(GPU の部分は作らない)")
endif()

include(GsisCompilerOptions)
include(GsisDependencies)

add_subdirectory(src)

if(GSIS_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
