# ソルバーのモジュール(静的ライブラリ)と CLI。
# 依存の向き: core ← {mesh, velocity} ← {config, physics} ← {io, gpu} ← app

# gsis_add_module(<name> SOURCES ... [DEPS ...])
function(gsis_add_module name)
  cmake_parse_arguments(ARG "" "" "SOURCES;DEPS" ${ARGN})
  add_library(${name} STATIC ${ARG_SOURCES})
  target_include_directories(${name} PUBLIC "${PROJECT_SOURCE_DIR}/src")
  target_link_libraries(${name} PUBLIC ${ARG_DEPS})
  gsis_target_warnings(${name})
endfunction()

if(GSIS_ENABLE_CUDA)
  set(GSIS_CUDA_ENABLED_INT 1)
  string(REPLACE ";" "," GSIS_CUDA_ARCH_STRING "${CMAKE_CUDA_ARCHITECTURES}")
else()
  set(GSIS_CUDA_ENABLED_INT 0)
  set(GSIS_CUDA_ARCH_STRING "")
endif()
configure_file(core/build_info.cpp.in "${CMAKE_CURRENT_BINARY_DIR}/generated/build_info.cpp" @ONLY)
set_source_files_properties("${CMAKE_CURRENT_BINARY_DIR}/generated/build_info.cpp"
  PROPERTIES COMPILE_DEFINITIONS "GSIS_BUILD_TYPE=\"$<CONFIG>\"")

# 型・エラー・ログ・計時・JSON・パス
gsis_add_module(gsis_core
  SOURCES
    core/error.cpp
    core/json.cpp
    core/log.cpp
    core/paths.cpp
    core/timer.cpp
    "${CMAKE_CURRENT_BINARY_DIR}/generated/build_info.cpp")
target_compile_definitions(gsis_core PUBLIC "GSIS_WITH_CUDA=${GSIS_CUDA_ENABLED_INT}")

# メッシュ: .pmesh の読み書き、幾何量、有限体積の量、並べ替え
gsis_add_module(gsis_mesh
  SOURCES
    mesh/fv_geometry.cpp
    mesh/geometry.cpp
    mesh/geometry_f32.cpp
    mesh/mesh_quality.cpp
    mesh/ordering.cpp
    mesh/pmesh_io.cpp
    mesh/poly_mesh.cpp
    mesh/section_cut.cpp
  DEPS gsis_core)

# 速度格子
gsis_add_module(gsis_velocity
  SOURCES velocity/velocity_grid.cpp
  DEPS gsis_core)

# ケース定義(TOML)と、ケース・メッシュ・速度格子の突き合わせ
gsis_add_module(gsis_config
  SOURCES
    config/case_config.cpp
    config/case_mesh_check.cpp
  DEPS gsis_core gsis_mesh gsis_velocity tomlplusplus::tomlplusplus)

# 物理(Maxwell・Shakhov・半空間流束はヘッダのみ、モーメントは CPU)
gsis_add_module(gsis_physics
  SOURCES physics/moments.cpp
  DEPS gsis_core gsis_velocity)

# 出力(VTU・CSV)
gsis_add_module(gsis_io
  SOURCES
    io/csv_writer.cpp
    io/vtu_writer.cpp
  DEPS gsis_core gsis_mesh)

# GPU(CUDA)
if(GSIS_ENABLE_CUDA)
  gsis_add_module(gsis_gpu
    SOURCES
      gpu/cuda_check.cpp
      gpu/device_info.cpp
      gpu/maxwell_moments.cpp
      gpu/maxwell_moments_kernels.cu
      gpu/physics_check.cpp
      gpu/physics_check_kernels.cu
    DEPS gsis_core gsis_velocity gsis_physics CUDA::cudart_static)
endif()

# 運動論ソルバー(フェーズ 2)、PBC マクロソルバー(フェーズ 3)、GSIS の反復(フェーズ 4)。
# 各ディレクトリの CMakeLists.txt で定義する
add_subdirectory(kinetic)
add_subdirectory(macro)
add_subdirectory(gsis)

# CLI
add_executable(gsis-solver
  app/main.cpp
  app/cli_util.cpp
  app/cmd_check_case.cpp
  app/cmd_cis_run.cpp
  app/cmd_gpu_info.cpp
  app/cmd_mesh_info.cpp
  app/cmd_pbc_run.cpp
  app/cmd_run.cpp
  app/mass_balance_report.cpp)
target_link_libraries(gsis-solver PRIVATE gsis_coupling gsis_kinetic gsis_macro gsis_config gsis_io gsis_mesh
  gsis_physics gsis_velocity gsis_core)
if(GSIS_ENABLE_CUDA)
  target_link_libraries(gsis-solver PRIVATE gsis_gpu)
endif()
gsis_target_warnings(gsis-solver)
gsis_target_windows_manifest(gsis-solver)
