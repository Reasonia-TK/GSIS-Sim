# CLI の失敗の検査: 終了コードと出力(標準出力 + 標準エラー)の両方を確かめる。
# (CTest の WILL_FAIL は「0 以外」なら何でも合格にするので、異常終了も見逃す)
#
#   cmake -DEXE=<実行ファイル> "-DARGS=<引数|引数|...>" -DEXPECT_CODE=<終了コード>
#         -DEXPECT_REGEX=<出力に含まれるべき正規表現> -P cli_expect.cmake
string(REPLACE "|" ";" ARGS "${ARGS}")
execute_process(
  COMMAND "${EXE}" ${ARGS}
  RESULT_VARIABLE code
  OUTPUT_VARIABLE out
  ERROR_VARIABLE err
  ENCODING UTF8)
if(NOT "${code}" STREQUAL "${EXPECT_CODE}")
  message(FATAL_ERROR "終了コードが ${code} です(期待 ${EXPECT_CODE})\n--- stdout\n${out}\n--- stderr\n${err}")
endif()
if(NOT "${out}${err}" MATCHES "${EXPECT_REGEX}")
  message(FATAL_ERROR "出力が「${EXPECT_REGEX}」に合いません\n--- stdout\n${out}\n--- stderr\n${err}")
endif()
message(STATUS "終了コード ${code}、出力に「${EXPECT_REGEX}」を含む")
