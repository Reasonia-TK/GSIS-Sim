# 質量の釣り合いと断面の質量流量の出力を確かめる(フェーズ 5c。cmake -P で使う)。
#   -DDIR=<出力先> -DSECTIONS=<断面の数> [-DGSIS=1]
# summary.json に "mass_balance"(釣り合い)と "mass_flow_sections"(断面)があり、history.csv の見出しに
# mass_imbalance と断面の列 M_dot_sec0 .. M_dot_sec<SECTIONS-1>(GSIS では mass_imbalance_pbc・M_dot_pbc_sec<k> も)が
# あり、最後の列が gpu_memory_bytes であること。

file(READ "${DIR}/summary.json" summary)
set(keys "\"mass_balance\": " "\"within_tolerance\": " "\"imbalance\": " "\"section_spread\": "
         "\"mass_flow_sections\": " "\"face_position\": " "\"mdot_kinetic_nd\": " "\"M_dot_kinetic\": ")
if(GSIS)
  list(APPEND keys "\"kinetic_half\": " "\"mdot_pbc_nd\": " "\"M_dot_pbc\": " "\"mass_balance_tolerance\": ")
endif()
foreach(key IN LISTS keys)
  string(FIND "${summary}" "${key}" pos)
  if(pos LESS 0)
    message(FATAL_ERROR "summary.json に ${key} がありません")
  endif()
endforeach()
file(STRINGS "${DIR}/history.csv" lines)
list(GET lines 0 header)
math(EXPR last "${SECTIONS} - 1")
set(columns ",mass_imbalance," ",M_dot_sec0," ",M_dot_sec${last},")
if(GSIS)
  list(APPEND columns ",mass_imbalance_pbc," ",M_dot_pbc_sec0," ",M_dot_pbc_sec${last},")
endif()
foreach(col IN LISTS columns)
  string(FIND "${header}" "${col}" pos)
  if(pos LESS 0)
    message(FATAL_ERROR "history.csv の見出しに ${col} がありません: ${header}")
  endif()
endforeach()
if(NOT header MATCHES ",gpu_memory_bytes$")
  message(FATAL_ERROR "history.csv の最後の列が gpu_memory_bytes ではありません: ${header}")
endif()
message(STATUS "${DIR}: 質量の釣り合いと断面の出力を確認しました")
