# GSIS の反復(フェーズ 4、docs/design_gsis.md)。運動論ソルバー(gsis_kinetic)と PBC(gsis_macro)をつなぐ。
#   interfaces.hpp   運動論とマクロの受け渡しの型(ヘッダのみ)
#   gsis_solver.*    GSIS の反復(初期化、CIS → モーメント → PBC → 補正 → 収束の指標)
#   gsis_output.*    run の履歴(history.csv・pbc_history.csv)
gsis_add_module(gsis_coupling
  SOURCES
    gsis_solver.cpp
    gsis_output.cpp
  DEPS gsis_kinetic gsis_macro gsis_config gsis_io gsis_mesh gsis_core)
