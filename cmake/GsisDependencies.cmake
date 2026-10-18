# 外部ライブラリ(FetchContent で版とハッシュを固定して取り込む)。
#
# オフラインでビルドするときは、取得済みのソースを次の変数で指定できる(FetchContent の標準機能):
#   -DFETCHCONTENT_SOURCE_DIR_TOMLPLUSPLUS=<toml++ のソース>
#   -DFETCHCONTENT_SOURCE_DIR_CATCH2=<Catch2 のソース>
#   -DFETCHCONTENT_SOURCE_DIR_AMGCL=<AMGCL のソース>
include(FetchContent)

# toml++ v3.4.0(MIT、ヘッダのみ): ケース定義(TOML)の読み込み
FetchContent_Declare(tomlplusplus
  URL https://github.com/marzer/tomlplusplus/archive/refs/tags/v3.4.0.tar.gz
  URL_HASH SHA256=8517f65938a4faae9ccf8ebb36631a38c1cadfb5efa85d9a72e15b9e97d25155
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  SYSTEM)
FetchContent_MakeAvailable(tomlplusplus)

if(GSIS_BUILD_TESTS)
  # Catch2 v3.16.1(BSL-1.0): C++ の単体テスト
  set(CATCH_INSTALL_DOCS OFF CACHE BOOL "" FORCE)
  set(CATCH_INSTALL_EXTRAS OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(Catch2
    URL https://github.com/catchorg/Catch2/archive/refs/tags/v3.16.1.tar.gz
    URL_HASH SHA256=d16bd1b8b2364918bd472cb216394acb93bde6784890df0d91bccfb3f28d777d
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SYSTEM)
  FetchContent_MakeAvailable(Catch2)
  # catch_discover_tests を使うため
  list(APPEND CMAKE_MODULE_PATH "${catch2_SOURCE_DIR}/extras")
endif()

# AMGCL 1.5.0(MIT、ヘッダのみ): PBC マクロソルバーの疎行列の反復ソルバー(フェーズ 3、docs/design_pbc.md)。
# Boost を使わない静的な構成(AMGCL_NO_BOOST。ソルバーと前処理はテンプレート引数で選ぶ)にする。
# AMGCL の CMakeLists.txt は Boost を探したり例を足したりするので読み込まない
# (SOURCE_SUBDIR に存在しないディレクトリを指定すると、FetchContent は取得だけを行う)。
FetchContent_Declare(amgcl
  URL https://github.com/ddemidov/amgcl/archive/refs/tags/1.5.0.tar.gz
  URL_HASH SHA256=6794cab752f0f15bc40db9c8a08a5ad761adf7d74b3d6652b9e2d4bf7f19ab31
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  SOURCE_SUBDIR _gsis_no_cmake)
FetchContent_MakeAvailable(amgcl)
add_library(gsis_amgcl INTERFACE)
target_include_directories(gsis_amgcl SYSTEM INTERFACE "${amgcl_SOURCE_DIR}")
target_compile_definitions(gsis_amgcl INTERFACE AMGCL_NO_BOOST)
if(MSVC)
  # AMGCL の推奨設定(大きなテンプレートのオブジェクト、min/max マクロの抑止)
  target_compile_options(gsis_amgcl INTERFACE "$<$<COMPILE_LANGUAGE:CXX>:/bigobj>")
  target_compile_definitions(gsis_amgcl INTERFACE NOMINMAX _USE_MATH_DEFINES)
endif()

# OpenMP(PBC マクロソルバーと AMGCL の並列化)。MSVC は /openmp(OpenMP 2.0)
find_package(OpenMP COMPONENTS CXX)
if(OpenMP_CXX_FOUND)
  target_link_libraries(gsis_amgcl INTERFACE OpenMP::OpenMP_CXX)
  message(STATUS "GSIS: OpenMP ${OpenMP_CXX_VERSION}(PBC マクロソルバー)")
else()
  message(STATUS "GSIS: OpenMP が見つからないので、PBC マクロソルバーは 1 スレッドで動く")
endif()
