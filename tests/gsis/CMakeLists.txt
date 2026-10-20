# GSIS の反復(フェーズ 4)のテスト。
#   gsis:*       CPU の運動論ソルバー + PBC(CUDA なしのビルドでも動く)
#   gsis_gpu:*   GPU の運動論ソルバー(CPU との一致・決定性。GPU がなければ SKIP)
#   gsis_cli:*   gsis-solver run の通し検査
add_executable(gsis_gsis_tests
  test_phase8_bc.cpp
  test_gsis.cpp
  gsis_test_cases.cpp
  ../kinetic/kinetic_test_cases.cpp)
target_link_libraries(gsis_gsis_tests PRIVATE
  gsis_test_support gsis_coupling gsis_kinetic gsis_macro gsis_config gsis_mesh gsis_velocity gsis_physics gsis_core
  Catch2::Catch2WithMain)
target_compile_definitions(gsis_gsis_tests PRIVATE "GSIS_TEST_OUTPUT_DIR=\"${GSIS_TEST_OUTPUT_DIR}\"")
gsis_target_warnings(gsis_gsis_tests)
gsis_target_windows_manifest(gsis_gsis_tests)
catch_discover_tests(gsis_gsis_tests
  DISCOVERY_MODE PRE_TEST
  WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
  TEST_PREFIX "gsis:")

if(GSIS_ENABLE_CUDA)
  add_executable(gsis_gsis_gpu_tests
    test_phase8_bc_gpu.cpp
    test_gsis_gpu.cpp
    gsis_test_cases.cpp
    ../kinetic/kinetic_test_cases.cpp)
  target_link_libraries(gsis_gsis_gpu_tests PRIVATE
    gsis_test_support gsis_coupling gsis_kinetic gsis_gpu gsis_macro gsis_config gsis_mesh gsis_velocity gsis_physics
    gsis_core Catch2::Catch2WithMain)
  target_compile_definitions(gsis_gsis_gpu_tests PRIVATE "GSIS_TEST_OUTPUT_DIR=\"${GSIS_TEST_OUTPUT_DIR}\"")
  gsis_target_warnings(gsis_gsis_gpu_tests)
  gsis_target_windows_manifest(gsis_gsis_gpu_tests)
  catch_discover_tests(gsis_gsis_gpu_tests
    DISCOVERY_MODE PRE_TEST
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
    TEST_PREFIX "gsis_gpu:")
endif()

# gsis-solver run の通し検査(tests/CMakeLists.txt の cli_meshes で作る箱のメッシュ、CPU の運動論ソルバー)
set(_gsis_cli_dir "${CMAKE_CURRENT_BINARY_DIR}/cli")
file(WRITE "${_gsis_cli_dir}/run_smoke.toml"
  "# gsis-solver run の通し検査用(tests/gsis/CMakeLists.txt が生成)\n[case]\nname = \"gsis_cli_smoke\"\n"
  "mesh = \"${CMAKE_BINARY_DIR}/tests/cli/box.pmesh\"\nmode = \"gsis\"\n"
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

[gsis]
n_cis = 2
max_pbc = 3
max_iterations = 50

[kinetic]
backend = "cpu"

[macro]
init_iterations = 5
]=])
add_test(NAME gsis_cli:run_gsis
  COMMAND gsis-solver run "${_gsis_cli_dir}/run_smoke.toml" --max-iter 3 --cells-csv --dump-every 2
    --out "${_gsis_cli_dir}/out_gsis")
set_tests_properties(gsis_cli:run_gsis PROPERTIES FIXTURES_REQUIRED cli_meshes FIXTURES_SETUP gsis_run_out)
add_test(NAME gsis_cli:run_outputs
  COMMAND "${CMAKE_COMMAND}" "-DDIR=${_gsis_cli_dir}/out_gsis" "-DMODE=gsis" "-DITERATIONS=3"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/check_run_outputs.cmake")
set_tests_properties(gsis_cli:run_outputs PROPERTIES FIXTURES_REQUIRED gsis_run_out)
add_test(NAME gsis_cli:run_init_from
  COMMAND gsis-solver run "${_gsis_cli_dir}/run_smoke.toml" --max-iter 2 --no-vtu
    --init-from "${_gsis_cli_dir}/out_gsis/cells.csv" --set macro.hot_interpolation=face --set gsis.n_cis=1
    --out "${_gsis_cli_dir}/out_restart")
set_tests_properties(gsis_cli:run_init_from PROPERTIES FIXTURES_REQUIRED "cli_meshes;gsis_run_out")
add_test(NAME gsis_cli:run_cis_mode
  COMMAND gsis-solver run "${_gsis_cli_dir}/run_smoke.toml" --mode cis --max-iter 3 --cells-csv
    --out "${_gsis_cli_dir}/out_cis")
set_tests_properties(gsis_cli:run_cis_mode PROPERTIES FIXTURES_REQUIRED cli_meshes FIXTURES_SETUP gsis_cis_out)
add_test(NAME gsis_cli:run_cis_outputs
  COMMAND "${CMAKE_COMMAND}" "-DDIR=${_gsis_cli_dir}/out_cis" "-DMODE=cis" "-DITERATIONS=3"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/check_run_outputs.cmake")
set_tests_properties(gsis_cli:run_cis_outputs PROPERTIES FIXTURES_REQUIRED gsis_cis_out)
# フェーズ 5c: 断面の質量流量と質量の釣り合いの出力(--sections、--set gsis.mass_balance_tolerance)
add_test(NAME gsis_cli:run_sections
  COMMAND gsis-solver run "${_gsis_cli_dir}/run_smoke.toml" --max-iter 2 --no-vtu --sections "1, 2, 3"
    --set gsis.mass_balance_tolerance=0.5 --out "${_gsis_cli_dir}/out_sections")
set_tests_properties(gsis_cli:run_sections PROPERTIES FIXTURES_REQUIRED cli_meshes FIXTURES_SETUP gsis_sections_out)
add_test(NAME gsis_cli:run_sections_outputs
  COMMAND "${CMAKE_COMMAND}" "-DDIR=${_gsis_cli_dir}/out_sections" "-DSECTIONS=3" "-DGSIS=1"
    -P "${PROJECT_SOURCE_DIR}/tests/check_mass_balance_outputs.cmake")
set_tests_properties(gsis_cli:run_sections_outputs PROPERTIES FIXTURES_REQUIRED gsis_sections_out)
add_test(NAME gsis_cli:run_cis_sections
  COMMAND gsis-solver run "${_gsis_cli_dir}/run_smoke.toml" --mode cis --max-iter 2 --no-vtu --sections "1,3"
    --set gsis.mass_balance_tolerance=0.5 --out "${_gsis_cli_dir}/out_cis_sections")
set_tests_properties(gsis_cli:run_cis_sections PROPERTIES FIXTURES_REQUIRED cli_meshes
  FIXTURES_SETUP gsis_cis_sections_out)
add_test(NAME gsis_cli:run_cis_sections_outputs
  COMMAND "${CMAKE_COMMAND}" "-DDIR=${_gsis_cli_dir}/out_cis_sections" "-DSECTIONS=2"
    -P "${PROJECT_SOURCE_DIR}/tests/check_mass_balance_outputs.cmake")
set_tests_properties(gsis_cli:run_cis_sections_outputs PROPERTIES FIXTURES_REQUIRED gsis_cis_sections_out)
add_test(NAME gsis_cli:run_bad_sections
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=run|${_gsis_cli_dir}/run_smoke.toml|--sections|1,x" "-DEXPECT_CODE=2" "-DEXPECT_REGEX=--sections"
    -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
set_tests_properties(gsis_cli:run_bad_sections PROPERTIES FIXTURES_REQUIRED cli_meshes)
add_test(NAME gsis_cli:run_bad_option
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=run|${_gsis_cli_dir}/run_smoke.toml|--frobnicate" "-DEXPECT_CODE=2" "-DEXPECT_REGEX=frobnicate"
    -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
add_test(NAME gsis_cli:run_bad_set_macro
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=run|${_gsis_cli_dir}/run_smoke.toml|--set|macro.frobnicate=1" "-DEXPECT_CODE=1"
    "-DEXPECT_REGEX=frobnicate" -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
add_test(NAME gsis_cli:run_bad_set_gsis
  COMMAND "${CMAKE_COMMAND}" "-DEXE=$<TARGET_FILE:gsis-solver>"
    "-DARGS=run|${_gsis_cli_dir}/run_smoke.toml|--set|gsis.n_cys=3" "-DEXPECT_CODE=1"
    "-DEXPECT_REGEX=n_cys" -P "${PROJECT_SOURCE_DIR}/tests/cli_expect.cmake")
set_tests_properties(gsis_cli:run_bad_option gsis_cli:run_bad_set_macro gsis_cli:run_bad_set_gsis PROPERTIES
  FIXTURES_REQUIRED cli_meshes)
