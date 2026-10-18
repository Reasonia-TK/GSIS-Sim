# 運動論ソルバー(フェーズ 2)のテスト。フェーズ 2 の担当が管理する。
#   kinetic:*      CPU 参照実装のテスト(CUDA なしのビルドでも動く)
#   kinetic_gpu:*  GPU 版のテスト(CPU との一致・決定性。GPU がなければ SKIP)
add_executable(gsis_kinetic_tests
  test_kinetic_params.cpp
  test_kinetic_cpu.cpp
  test_kinetic_phase5.cpp
  test_kinetic_phase5c.cpp
  kinetic_test_cases.cpp)
target_link_libraries(gsis_kinetic_tests PRIVATE
  gsis_test_support gsis_kinetic gsis_config gsis_mesh gsis_velocity gsis_physics gsis_core
  Catch2::Catch2WithMain)
target_compile_definitions(gsis_kinetic_tests PRIVATE "GSIS_TEST_OUTPUT_DIR=\"${GSIS_TEST_OUTPUT_DIR}\"")
gsis_target_warnings(gsis_kinetic_tests)
gsis_target_windows_manifest(gsis_kinetic_tests)
catch_discover_tests(gsis_kinetic_tests
  DISCOVERY_MODE PRE_TEST
  WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
  TEST_PREFIX "kinetic:")

# cis-run の通し検査(CPU 版、tests/CMakeLists.txt の cli_meshes で作る箱のメッシュを使う)
set(_kin_cli_dir "${CMAKE_CURRENT_BINARY_DIR}/cli")
file(WRITE "${_kin_cli_dir}/cis_smoke.toml"
  "# cis-run の通し検査用(tests/kinetic/CMakeLists.txt が生成)\n[case]\nname = \"cis_cli_smoke\"\n"
  "mesh = \"${CMAKE_BINARY_DIR}/tests/cli/box.pmesh\"\nmode = \"cis\"\n"
  [=[
[gas]
molecular_mass = 6.65e-27
mu_ref = 1.916e-5
T_ref = 273.15
omega = 0.66

[conditions]
L_ref = 0.25
T0 = 273.15
delta_rp = 1.0

[[boundary]]
patch = "inlet"
type = "pressure_inlet"
temperature = 273.15

[[boundary]]
patch = "outlet"
type = "vacuum"

[[boundary]]
patch = "wall"
type = "diffuse_wall"
temperature = 273.15

[velocity_grid]
n = [8, 8, 8]
range = [[-5.0, 5.0], [-5.0, 5.0], [-5.0, 5.0]]

[kinetic]
backend = "cpu"
]=])
add_test(NAME kinetic_cli:cis_run_cpu
  COMMAND gsis-solver cis-run "${_kin_cli_dir}/cis_smoke.toml" --max-iter 3 --print-every 1 --cells-csv
    --out "${_kin_cli_dir}/out_cpu")
set_tests_properties(kinetic_cli:cis_run_cpu PROPERTIES FIXTURES_REQUIRED cli_meshes FIXTURES_SETUP kinetic_cis_out)
add_test(NAME kinetic_cli:cis_run_init_from
  COMMAND gsis-solver cis-run "${_kin_cli_dir}/cis_smoke.toml" --max-iter 2 --no-vtu
    --init-from "${_kin_cli_dir}/out_cpu/cells.csv" --set sweep_blocks=2 --out "${_kin_cli_dir}/out_restart")
set_tests_properties(kinetic_cli:cis_run_init_from PROPERTIES FIXTURES_REQUIRED "cli_meshes;kinetic_cis_out")
add_test(NAME kinetic_cli:cis_run_bad_option
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=cis-run|${_kin_cli_dir}/cis_smoke.toml|--frobnicate" "-DEXPECT_CODE=2" "-DEXPECT_REGEX=frobnicate"
    -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
add_test(NAME kinetic_cli:cis_run_bad_set
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=cis-run|${_kin_cli_dir}/cis_smoke.toml|--set|sweep_blockz=2" "-DEXPECT_CODE=1"
    "-DEXPECT_REGEX=sweep_blockz" -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")

if(GSIS_ENABLE_CUDA)
  add_executable(gsis_kinetic_gpu_tests
    test_kinetic_gpu.cpp
    kinetic_test_cases.cpp)
  target_link_libraries(gsis_kinetic_gpu_tests PRIVATE
    gsis_test_support gsis_kinetic gsis_gpu gsis_config gsis_mesh gsis_velocity gsis_physics gsis_core
    Catch2::Catch2WithMain)
  target_compile_definitions(gsis_kinetic_gpu_tests PRIVATE "GSIS_TEST_OUTPUT_DIR=\"${GSIS_TEST_OUTPUT_DIR}\"")
  gsis_target_warnings(gsis_kinetic_gpu_tests)
  gsis_target_windows_manifest(gsis_kinetic_gpu_tests)
  catch_discover_tests(gsis_kinetic_gpu_tests
    DISCOVERY_MODE PRE_TEST
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
    TEST_PREFIX "kinetic_gpu:")
endif()
