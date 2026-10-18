# PBC マクロソルバー(CPU、FP64)。フェーズ 3 の担当が管理する(docs/design_pbc.md)。
# 線形ソルバーは AMGCL(cmake/GsisDependencies.cmake の gsis_amgcl。OpenMP を含む)。
gsis_add_module(gsis_macro
  SOURCES
    linear_solver.cpp
    macro_params.cpp
    pbc_solver.cpp
  DEPS gsis_config gsis_mesh gsis_physics gsis_core)
target_link_libraries(gsis_macro PRIVATE gsis_amgcl)
if(OpenMP_CXX_FOUND)
  # ソルバー本体のループも OpenMP で並列化する(リンクは PUBLIC にして実行ファイルにも vcomp を渡す)
  target_link_libraries(gsis_macro PUBLIC OpenMP::OpenMP_CXX)
endif()
