# コンパイラ共通の設定と、ターゲットに付ける設定の関数。

if(MSVC)
  # 日本語ロケール(CP932)では、MSVC が CUDA ヘッダなどで C4819 を大量に出す。
  # ソースを UTF-8 として読ませるため、ホストと nvcc の両方に /utf-8 を付ける。
  add_compile_options(
    "$<$<COMPILE_LANGUAGE:CXX>:/utf-8>"
    "$<$<COMPILE_LANGUAGE:CXX>:/Zc:__cplusplus>"
    "$<$<COMPILE_LANGUAGE:CUDA>:-Xcompiler=/utf-8>"
    "$<$<COMPILE_LANGUAGE:CUDA>:-Xcompiler=/Zc:__cplusplus>")
endif()

if(GSIS_ENABLE_CUDA)
  # Nsight Compute でソース行と対応させるため、Debug 以外でも行情報を付ける(速度には影響しない)
  add_compile_options("$<$<AND:$<COMPILE_LANGUAGE:CUDA>,$<NOT:$<CONFIG:Debug>>>:-lineinfo>")
endif()

# gsis_target_warnings(<target>)
#   自前のターゲットにだけ警告を強める(依存ライブラリには付けない)。
function(gsis_target_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:/W4;/permissive->")
    if(GSIS_WARNINGS_AS_ERRORS)
      target_compile_options(${target} PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:/WX>")
    endif()
  else()
    target_compile_options(${target} PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:-Wall;-Wextra;-Wpedantic>")
    if(GSIS_WARNINGS_AS_ERRORS)
      target_compile_options(${target} PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:-Werror>")
    endif()
  endif()
endfunction()

# gsis_target_windows_manifest(<target>)
#   Windows の実行ファイルに、UTF-8 の active code page と長いパスの対応を埋め込む。
#   (日本語を含むパスを narrow 文字列の API で扱えるようにする)
function(gsis_target_windows_manifest target)
  if(WIN32 AND MSVC)
    target_sources(${target} PRIVATE "${PROJECT_SOURCE_DIR}/cmake/gsis_utf8.manifest")
  endif()
endfunction()
