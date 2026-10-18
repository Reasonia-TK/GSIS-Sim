# PBC マクロソルバー(フェーズ 3)のテスト。フェーズ 3 の担当が管理する。
add_executable(gsis_macro_tests
  test_macro_params.cpp
  test_macro_jacobian.cpp
  test_macro_linear_solver.cpp
  test_macro_linearization.cpp
  test_macro_diagnose.cpp
  test_macro_mms.cpp
  test_macro_solver.cpp)
target_link_libraries(gsis_macro_tests PRIVATE
  gsis_test_support gsis_macro gsis_config gsis_mesh gsis_physics gsis_core
  Catch2::Catch2WithMain)
target_compile_definitions(gsis_macro_tests PRIVATE "GSIS_TEST_OUTPUT_DIR=\"${GSIS_TEST_OUTPUT_DIR}\"")
gsis_target_warnings(gsis_macro_tests)
gsis_target_windows_manifest(gsis_macro_tests)
catch_discover_tests(gsis_macro_tests
  DISCOVERY_MODE PRE_TEST
  WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
  TEST_PREFIX "macro:")

# pbc-run の通し検査(スモーク用の箱。単独モードで収束し、要約と履歴を書く)
set(_macro_cli_dir "${CMAKE_CURRENT_BINARY_DIR}/cli")
add_test(NAME macro:cli_pbc_run_smoke
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=pbc-run|${PROJECT_SOURCE_DIR}/cases/smoke/smoke.toml|--out|${_macro_cli_dir}/smoke|--iterations|100"
    "-DEXPECT_CODE=0" "-DEXPECT_REGEX=(収束)" -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
add_test(NAME macro:cli_pbc_run_bad_option
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=pbc-run|${PROJECT_SOURCE_DIR}/cases/smoke/smoke.toml|--cfl|-1"
    "-DEXPECT_CODE=2" "-DEXPECT_REGEX=--cfl" -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
