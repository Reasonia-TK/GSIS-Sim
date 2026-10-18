# 単体テスト(Catch2)と、テスト・スモーク用の補助ツール。
include(Catch)

# テスト用のメッシュ生成など
add_library(gsis_test_support STATIC support/box_mesh.cpp)
target_include_directories(gsis_test_support PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(gsis_test_support PUBLIC gsis_mesh)
gsis_target_warnings(gsis_test_support)

set(GSIS_TEST_OUTPUT_DIR "${CMAKE_CURRENT_BINARY_DIR}/test_output")

# CPU のテスト
add_executable(gsis_tests
  test_config.cpp
  test_core.cpp
  test_half_space_flux.cpp
  test_io.cpp
  test_mesh_fv.cpp
  test_mesh_geometry.cpp
  test_ordering.cpp
  test_physics.cpp
  test_pmesh_io.cpp
  test_section_cut.cpp
  test_velocity.cpp)
target_link_libraries(gsis_tests PRIVATE
  gsis_test_support gsis_config gsis_io gsis_mesh gsis_physics gsis_velocity gsis_core
  Catch2::Catch2WithMain)
target_compile_definitions(gsis_tests PRIVATE "GSIS_TEST_OUTPUT_DIR=\"${GSIS_TEST_OUTPUT_DIR}\"")
gsis_target_warnings(gsis_tests)
gsis_target_windows_manifest(gsis_tests)
catch_discover_tests(gsis_tests
  DISCOVERY_MODE PRE_TEST
  WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
  TEST_PREFIX "unit:")

# GPU のテスト(CUDA デバイスがなければ SKIP になる)
if(GSIS_ENABLE_CUDA)
  add_executable(gsis_gpu_tests test_gpu.cpp)
  target_link_libraries(gsis_gpu_tests PRIVATE gsis_gpu gsis_physics gsis_velocity gsis_core
    Catch2::Catch2WithMain)
  gsis_target_warnings(gsis_gpu_tests)
  gsis_target_windows_manifest(gsis_gpu_tests)
  catch_discover_tests(gsis_gpu_tests
    DISCOVERY_MODE PRE_TEST
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
    TEST_PREFIX "gpu:")
endif()

# 箱メッシュを .pmesh に書く補助ツール(cases/smoke のメッシュもこれで作る)
add_executable(gsis-write-box-mesh tools/write_box_mesh.cpp)
target_link_libraries(gsis-write-box-mesh PRIVATE gsis_test_support gsis_core)
gsis_target_warnings(gsis-write-box-mesh)
gsis_target_windows_manifest(gsis-write-box-mesh)

# CLI の通し検査: メッシュを書き、gsis-solver の各サブコマンドを実行する
set(_cli_dir "${CMAKE_CURRENT_BINARY_DIR}/cli")
add_test(NAME cli:write_box_mesh
  COMMAND gsis-write-box-mesh "${_cli_dir}/box.pmesh" --n 6 4 4 --distort 0.2 --tube)
add_test(NAME cli:write_tet_mesh
  COMMAND gsis-write-box-mesh "${_cli_dir}/box_tet.pmesh" --n 3 2 2 --tets --tube)
set_tests_properties(cli:write_box_mesh cli:write_tet_mesh PROPERTIES FIXTURES_SETUP cli_meshes)
add_test(NAME cli:mesh_info
  COMMAND gsis-solver mesh-info "${_cli_dir}/box.pmesh"
    --vtu "${_cli_dir}/box.vtu" --boundary-vtu "${_cli_dir}/box_boundary.vtu")
add_test(NAME cli:mesh_info_tet
  COMMAND gsis-solver mesh-info "${_cli_dir}/box_tet.pmesh" --vtu "${_cli_dir}/box_tet.vtu" --ascii)
set_tests_properties(cli:mesh_info cli:mesh_info_tet PROPERTIES FIXTURES_REQUIRED cli_meshes)
add_test(NAME cli:check_case_smoke
  COMMAND gsis-solver check-case "${PROJECT_SOURCE_DIR}/cases/smoke/smoke.toml")
add_test(NAME cli:gpu_info COMMAND gsis-solver gpu-info)
# 失敗の検査: 終了コード(2 = 引数の誤り、1 = エラー)と出力の両方を確かめる
function(gsis_add_cli_failure_test name code regex)
  # 引数は | で区切って 1 つにまとめて渡す(; は CTest の引数の区切りと衝突するため)
  string(REPLACE ";" "|" _args "${ARGN}")
  add_test(NAME ${name}
    COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>" "-DARGS=${_args}"
      "-DEXPECT_CODE=${code}" "-DEXPECT_REGEX=${regex}"
      -P "${CMAKE_CURRENT_SOURCE_DIR}/cli_expect.cmake")
endfunction()
gsis_add_cli_failure_test(cli:usage_error 2 "gsis-solver mesh-info <file\\.pmesh>" mesh-info)
gsis_add_cli_failure_test(cli:unknown_command 2 "frobnicate" frobnicate)
gsis_add_cli_failure_test(cli:missing_file 1 "does_not_exist\\.pmesh" mesh-info "${_cli_dir}/does_not_exist.pmesh")
gsis_add_cli_failure_test(cli:bad_case 1 "nothing\\.toml" check-case "${_cli_dir}/nothing.toml")

# フェーズ 2(運動論)・フェーズ 3(PBC)・フェーズ 4(GSIS)のテスト
add_subdirectory(kinetic)
add_subdirectory(macro)
add_subdirectory(gsis)
