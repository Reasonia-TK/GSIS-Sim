# 運動論ソルバー(CIS)。フェーズ 2 の担当が管理する(docs/design_cis_gpu.md)。
#   kinetic_kernels.hpp     1 セル・1 速度点の計算(CPU・GPU 共通の GSIS_HD 関数)
#   kinetic_setup.*         前処理(並べ替え、無次元の幾何量、速度点の保存順、境界の表)
#   kinetic_backend_cpu.cpp CPU 参照実装(OpenMP)
#   kinetic_backend_cuda.*  GPU 版(GSIS_ENABLE_CUDA のとき)
#   kinetic_solver.*        反復の進め方(境界条件の更新、収束の指標、GSIS の補正の口)
#   kinetic_output.*        cis-run の出力(VTU・CSV・JSON)
set(_gsis_kinetic_sources
  kinetic_params.cpp
  kinetic_setup.cpp
  kinetic_backend_cpu.cpp
  kinetic_backend_factory.cpp
  kinetic_solver.cpp
  kinetic_output.cpp)
if(GSIS_ENABLE_CUDA)
  list(APPEND _gsis_kinetic_sources kinetic_backend_cuda.cpp kinetic_cuda_kernels.cu)
endif()

gsis_add_module(gsis_kinetic
  SOURCES ${_gsis_kinetic_sources}
  DEPS gsis_config gsis_mesh gsis_velocity gsis_physics gsis_io gsis_core)

if(GSIS_ENABLE_CUDA)
  target_link_libraries(gsis_kinetic PUBLIC gsis_gpu)
  # カーネルのレジスタ数などを確認するときは -Xptxas=-v を足す
  target_compile_options(gsis_kinetic PRIVATE "$<$<COMPILE_LANGUAGE:CUDA>:--expt-relaxed-constexpr>")
endif()

# CPU 参照実装の並列化(見つからなければ逐次で動く)
find_package(OpenMP COMPONENTS CXX)
if(OpenMP_CXX_FOUND)
  target_link_libraries(gsis_kinetic PRIVATE OpenMP::OpenMP_CXX)
endif()
