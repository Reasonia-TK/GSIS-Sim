# gsis-solver run の出力を確かめる(cmake -P で使う)。
#   -DDIR=<出力先> -DMODE=gsis|cis -DITERATIONS=<反復数>
# 出力一式(history.csv・summary.json・fields.vtu・boundary.vtu・cells.csv)があり、summary.json の反復数が
# ITERATIONS であること。GSIS では pbc_history.csv・diag_*.csv があり、"mode": "gsis" で、
# history.csv の行数が見出し + 反復数で、見出しが約束どおり(epsilon、filter_iterations、最後が gpu_memory_bytes)であること。

foreach(f history.csv summary.json fields.vtu boundary.vtu cells.csv)
  if(NOT EXISTS "${DIR}/${f}")
    message(FATAL_ERROR "${DIR}/${f} がありません")
  endif()
endforeach()
file(READ "${DIR}/summary.json" summary)
if(NOT summary MATCHES "\"iterations\": ${ITERATIONS},")
  message(FATAL_ERROR "summary.json の iterations が ${ITERATIONS} ではありません:\n${summary}")
endif()
if(MODE STREQUAL "gsis")
  foreach(f pbc_history.csv diag_000002.csv)
    if(NOT EXISTS "${DIR}/${f}")
      message(FATAL_ERROR "${DIR}/${f} がありません")
    endif()
  endforeach()
  if(NOT summary MATCHES "\"mode\": \"gsis\"")
    message(FATAL_ERROR "summary.json に \"mode\": \"gsis\" がありません")
  endif()
  foreach(key "\"cis_iterations\": " "\"time_breakdown_s\": " "\"negative_replacement\": " "\"mass_flow\": "
              "\"mass_flow_pbc\": " "\"filter_length\": " "\"init_advection\": " "\"gsis_negative_check\": ")
    string(FIND "${summary}" "${key}" pos)
    if(pos LESS 0)
      message(FATAL_ERROR "summary.json に ${key} がありません")
    endif()
  endforeach()
  file(STRINGS "${DIR}/history.csv" lines)
  list(LENGTH lines n)
  math(EXPR expected "${ITERATIONS} + 1")
  if(NOT n EQUAL expected)
    message(FATAL_ERROR "history.csv の行数 ${n} が ${expected} ではありません")
  endif()
  # 列の約束(docs/build.md §4): 補正のフィルタの列があり、最後の列は gpu_memory_bytes
  list(GET lines 0 header)
  if(NOT header MATCHES ",epsilon," OR NOT header MATCHES ",filter_iterations,t_filter_s,gpu_memory_bytes$")
    message(FATAL_ERROR "history.csv の見出しが約束と違います: ${header}")
  endif()
endif()
message(STATUS "${DIR}: 出力一式を確認しました")
