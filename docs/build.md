# C++/CUDA のビルドとテスト

`gsis-solver`(C++20 / CUDA C++20)のビルド・テスト・実行の手順。ここに書いたコマンドは 2026-10-15 に実際に実行して確認した(Windows 11、RTX 5070 Ti)。

## 1. 必要なもの

| 項目 | 確認した版 | 備考 |
|---|---|---|
| Visual Studio | Community 2026(MSVC 19.51)、Build Tools 2022(MSVC 19.44) | 「C++ によるデスクトップ開発」と「C++ CMake tools for Windows」(同梱の Ninja を使う) |
| CUDA Toolkit | 13.4(`C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4`) | CUDA なしでビルドするときは不要 |
| CMake | 4.4.3(3.28 以上) | `C:\Program Files\CMake\bin` |
| ネットワーク | 初回の構成時のみ | toml++ と Catch2 を FetchContent で取得する(§6) |

GPU のドライバは 610.88(CUDA UMD 13.3)で動作を確認した。ツールキット 13.4 より古いが、CUDA のマイナー版の互換で動く(`gpu-info` に注意として表示される)。

## 2. 開発者環境

プリセットは Ninja + MSVC(`cl.exe`)を使うので、Visual Studio の開発者環境(x64)で `cmake` を実行する。通常の PowerShell からは、次のスクリプトをドットソースすると環境を取り込める(「Developer PowerShell for VS」から実行する場合は不要)。

```powershell
. .\cmake\vs_dev_env.ps1                          # 最新の VS(この PC では VS 2026)
. .\cmake\vs_dev_env.ps1 -InstallPath C:\BuildTools   # VS 2022 Build Tools を使う場合
```

PowerShell の 1 回の呼び出しごとに環境が消える使い方(スクリプトなど)では、毎回最初にドットソースする。

## 3. ビルドとテスト(実際に通したコマンド)

リポジトリのルートで実行する。ビルドディレクトリは `build/<プリセット名>`。

```powershell
. .\cmake\vs_dev_env.ps1
cmake --preset release           # 構成(初回は依存の取得を含めて約 15 秒)
cmake --build --preset release   # ビルド
ctest --preset release           # テスト
```

| プリセット | 内容 | 確認結果 |
|---|---|---|
| `release` | Release、CUDA あり(`sm_120`) | 66/66 通過(約 2 秒)、警告 0 |
| `debug` | Debug、CUDA あり | 66/66 通過(約 7 秒)、警告 0 |
| `release-cpu` | Release、CUDA なし(`GSIS_ENABLE_CUDA=OFF`) | 63/63 通過(GPU のテスト 3 件はビルドしない)、警告 0 |
| `debug-cpu` | Debug、CUDA なし | — |

VS 2022 Build Tools でも、ビルドディレクトリを分けて同じ結果(66/66、クリーンビルドで警告 0)になることを確認した。

```powershell
. .\cmake\vs_dev_env.ps1 -InstallPath C:\BuildTools
cmake --preset release -B build/release-vs2022
cmake --build build/release-vs2022
ctest --test-dir build/release-vs2022
```

### 主なオプション

| 変数 | 既定 | 内容 |
|---|---|---|
| `GSIS_ENABLE_CUDA` | `ON` | `OFF` にすると CPU の部分とテストだけをビルドする |
| `CMAKE_CUDA_ARCHITECTURES` | `120` | 例: `-DCMAKE_CUDA_ARCHITECTURES=89`、`native`。環境変数 `CUDAARCHS` でもよい |
| `GSIS_BUILD_TESTS` | `ON` | 単体テストと補助ツール `gsis-write-box-mesh` |
| `GSIS_WARNINGS_AS_ERRORS` | `OFF` | 自前のターゲットで警告をエラーにする |

コンパイラの設定(`cmake/GsisCompilerOptions.cmake`):
- 日本語ロケールで CUDA ヘッダなどが C4819 を出すのを防ぐため、ホストに `/utf-8`、nvcc に `-Xcompiler=/utf-8` を付ける
- 自前のターゲットは `/W4 /permissive-`。依存ライブラリのヘッダは `SYSTEM` 扱いで警告を出さない
- CUDA は分割コンパイル(RDC)を使わない。cudart は静的リンク
- 実行ファイルに UTF-8 の active code page を埋め込む(`cmake/gsis_utf8.manifest`。日本語のパスを扱うため)

### テストの構成

| CTest の名前 | 内容 |
|---|---|
| `unit:*` | `gsis_tests`(Catch2)。core・config・mesh・velocity・physics・io |
| `gpu:*` | `gsis_gpu_tests`。GPU がなければ SKIP |
| `cli:*` | `gsis-write-box-mesh` でメッシュを書き、`gsis-solver` の各サブコマンドを実行する。誤った使い方・存在しないファイルでは、終了コード(2 または 1)と出力の文言の両方を確かめる(`tests/cli_expect.cmake`) |
| `kinetic:*`・`kinetic_cli:*` | 運動論ソルバー(フェーズ 2)の CPU 参照実装の単体テストと、`cis-run` の通し検査(CUDA なしでも動く) |
| `kinetic_gpu:*` | 運動論ソルバーの GPU 版(CPU との一致、決定性)。GPU がなければ SKIP |
| `macro:*` | PBC マクロソルバー(フェーズ 3)のヤコビアン・製造解法・決定性の単体テストと、`pbc-run` の通し検査 |
| `gsis:*`・`gsis_cli:*` | GSIS の反復(フェーズ 4)。CPU の運動論ソルバー + PBC で、平板で CIS 単独と同じ不動点に収束すること(格子を細かくすると差が減る)、小さな Kn で CIS の反復が大幅に減ること、補正と負値の置換(`all`・`created`・`limit`・`blend`)、面の HoT、補正の低域フィルタ(波数ごとの係数、Kn = 5 でフィルタなしは発散・ありは収束)、決定性(スレッド数 1 と 4 でビット一致)。`gsis_cli:*` は `gsis-solver run` の通し検査(GSIS・CIS 単独・再開・引数の誤り、`history.csv` の列の約束) |
| `gsis_gpu:*` | GSIS を GPU の運動論ソルバーで(決定性、CPU との一致)。GPU がなければ SKIP |

件数(2026-10-16 時点、フェーズ 2・3 を統合した `build/release`): CUDA ありで 108 件、CUDA なしで 101 件。
フェーズ 4(`build/phase4`・`build/phase4-cpu`): CUDA ありで 128 件、CUDA なしで 120 件(GSIS と運動論の正値の制限の試験を足した)。
フェーズ 5a(`build/phase5`・`build/phase5-cpu`): CUDA ありで 133 件、CUDA なしで 124 件(運動論の向きごとのスイープ順序・内部反復・ブロック間の反復・保存的な離散平衡の
試験 4 件(`tests/kinetic/test_kinetic_phase5.cpp`)と GPU の一致 1 件を足した。PBC の境界ヤコビアンの試験に入口の接線速度 0 を足した)。
フェーズ 5b-1(`build/phase5b`): CUDA ありで 138 件、CUDA なしで 129 件。CIS の新しい設定を既定値にした(`docs/design_cis_gpu.md` §11)のに合わせて、従来の
設定を前提にした試験 3 件に設定を明示し、離散平衡が作れないセルの扱い(`deq_fallback`)・冷たいセルの BGK(`cold_bgk_temperature`)・
圧力境界の W_{i,b}(`pressure_boundary_reconstruction`)・補正の減衰(`[gsis].damp_kn`)・連続流の HoT の減衰(`[macro].gsis_hot_kn`)の
試験 5 件を足した。補正のフィルタの試験に長さの下限(`[gsis].filter_min_cells`)を足した(`docs/phase5b_d1000_report.md`)。
フェーズ 5c(`build/phase5c`・`build/phase5c-cpu`): CUDA ありで 152 件、CUDA なしで 142 件。質量の釣り合いと断面の質量流量の診断
(`docs/design_gsis.md` §6a)の試験 14 件を足した: 断面の面の選び方(`unit:section cut: *` 3 件。歪んだ六面体・四面体でも投影面積が
断面積に一致)、一様な流れの平衡分布の断面の流量 = ρu_x × 断面積・収束した解で断面と入口・出口の流量が一致・`mass_balance` と
`[output]` の読み込み(`kinetic:*` 3 件)、GPU と CPU の一致と決定性(`kinetic_gpu:*` 1 件)、PBC の内部面の質量フラックスの断面の和が
手前のセルの質量の残差の和と一致(`macro:*` 1 件)、GSIS の反復の断面の流量と `[gsis].mass_balance_*`(`gsis:*` 1 件)、
`run --sections`(GSIS・CIS 単独)の出力と引数の誤り(`gsis_cli:*` 5 件。`tests/check_mass_balance_outputs.cmake`)。
`[macro].advection` の既定を `upwind2` にしたので、既定値の試験の期待値を直した。

一部だけ実行する例:

```powershell
ctest --preset release -R "unit:.*half-space"
.\build\release\tests\gsis_tests.exe "[pmesh]"        # Catch2 のタグで選ぶ
.\build\release\tests\gsis_gpu_tests.exe
```

## 4. 実行

```powershell
.\build\release\src\gsis-solver.exe --help
.\build\release\src\gsis-solver.exe mesh-info cases\smoke\box.pmesh --vtu out\box.vtu --boundary-vtu out\box_boundary.vtu
.\build\release\src\gsis-solver.exe check-case cases\smoke\smoke.toml
# 運動論ソルバー(CIS)だけで解く。出力は既定で runs\kinetic\<ケース名>\(history.csv・summary.json・fields.vtu・boundary.vtu)
.\build\release\src\gsis-solver.exe cis-run cases\tube_vacuum\kinetic\tube_delta1_cis.toml
# ケースの mode に応じて GSIS(mode = "gsis")か CIS 単独(mode = "cis")で解く。出力は既定で runs\gsis\<ケース名>\
#(history.csv・pbc_history.csv・summary.json・fields.vtu・boundary.vtu。docs/design_gsis.md §6)
.\build\release\src\gsis-solver.exe run cases\tube_vacuum\gsis\tube_delta10_gsis.toml --cells-csv
# PBC マクロソルバーだけ(NSF のみの単独モード)で解く。出力は既定で runs\macro\<ケース名>\
.\build\release\src\gsis-solver.exe pbc-run cases\tube_vacuum\macro\tube_hex_d1000_nsf.toml
.\build\release\src\gsis-solver.exe gpu-info --smoke --cells 4096
```

| サブコマンド | 内容 |
|---|---|
| `mesh-info <file.pmesh> [--vtu <out>] [--boundary-vtu <out>] [--ascii]` | 個数、面とセルの形、検査結果(閉性・体積・内部面の向き・参照されない節点)、総体積、パッチごとの面数と面積、非直交角の統計、RCM による帯幅。`--vtu` は多面体の VTU(セル値: cell_id、volume、n_faces、non_orthogonality_max_deg、lsq_conditioning、rcm_rank、cell_center)、`--boundary-vtu` は境界面の VTU(patch_id、face_id、area) |
| `check-case <case.toml>` | 設定、導出量(R、p_in、Kn、無次元化の基準量)、境界条件とパッチの対応、速度格子、分布関数の FP32 / FP64 のメモリ量と GPU の空き |
| `gpu-info [--smoke] [--cells N]` | CUDA の版、デバイスの情報。`--smoke` で f^eq のモーメントを GPU と CPU で計算して比べる |
| `run <case.toml> [--out <dir>] [--mode gsis\|cis] [--max-iter N] [--min-iter N] [--tol X] [--backend cpu\|cuda] [--precision fp32\|fp64] [--set [kinetic.\|macro.\|gsis.]key=value]... [--threads N] [--print-every N] [--vtu-every N] [--dump-every N] [--no-vtu] [--cells-csv] [--init-from <cells.csv>] [--save-init <cells.csv>] [--sections <位置の並び>]` | GSIS(フェーズ 4)か CIS 単独。`--save-init` は初期化(PBC 単独)の後の状態を cells.csv の形で書く(同じケースの比較の計算を `--init-from` で始める。フェーズ 5a)。収束判定は式 (59) の ε < `[gsis].tolerance`、上限は `[gsis].max_iterations`。`--set` の接頭辞で節を選ぶ(なしは `[kinetic]`)。詳細は `docs/design_gsis.md` §6 |
| `cis-run <case.toml> ...` | 運動論ソルバー(CIS)だけ(フェーズ 2、`docs/phase2_report.md` §1) |
| `pbc-run <case.toml> ...` | PBC マクロソルバーだけ(NSF のみの単独モード、フェーズ 3、`docs/design_pbc.md` §10) |

共通オプション: `-v`(詳細なログ)、`-q`(警告とエラーだけ)、`--version`。終了コードは 0(成功)、1(エラー)、2(引数の誤り)。

### GUI などから使うときの約束(2026-10-16、フェーズ 4)

- **穏やかな停止:** `run`・`cis-run`・`pbc-run` は、出力先(`--out`)に `STOP` という名前のファイルができると、その反復の終わりで結果(`summary.json`・`fields.vtu`・`boundary.vtu`・`cells.csv` など)を書いて終了コード 0 で終える。`summary.json` の `"stopped"` が `true`(`"converged"` は `false`)になる。古い `STOP` は実行の初めに消す(GSIS は初期化の PBC 単独の途中でも止まる)。
- **`summary.json` の共通の項目:** `run`(GSIS・CIS 単独)・`cis-run`・`pbc-run` とも `iterations`・`converged`・`stopped`・`tolerance`・`Kn`・`delta`・`reference`(無次元化の基準量: `length_m`・`temperature_K`・`pressure_Pa`・`density_kg_m3`・`velocity_m_s`・`heat_flux_W_m2`・`mass_flux_kg_m2_s`)と、管(`delta_rp`)なら `mass_flow`(`scale_kg_s`・`M_dot_inlet`・`M_dot_outlet`・`M_dot`)を持つ。`run` の GSIS には `mode: "gsis"`、`mass_flow_pbc`、時間の内訳などもある(`docs/design_gsis.md` §6)。
- **`history.csv` の GPU メモリ:** `run`・`cis-run` の最後の列 `gpu_memory_bytes` は、このプロセスが GPU に確保した量(分布関数と作業領域。CPU の実行では 0)。nvidia-smi の値は他のプログラムの分を含む。
- **`run` の引数と列(変えない):** `--out`・`--max-iter`・`--tol`、`history.csv` の `iteration`・`epsilon`・`mdot_<パッチ>_nd`・`mdot_<パッチ>_kg_s`・`mdot_pbc_<パッチ>_nd`(GSIS)。
  再開後に足したもの(既存の列・キーは変えていない): `history.csv` の `filter_iterations`・`t_filter_s`(`gpu_memory_bytes` の前。`gpu_memory_bytes` は最後の列のまま)、`summary.json` の `gsis.filter_length`・`gsis.filter_max_length`、`params.gsis_negative_check`、`macro_params` の `init_advection`・`hot_interpolation`・`gsis_face_state`・`gsis_continuity_pseudo_time`・`gsis_freeze_kn`。`--set gsis.filter_length=…`・`gsis.filter_max_length=…` も使える(0 以上)。
  フェーズ 5b で足したもの(既存の列・キーは変えていない): `summary.json` の `gsis.filter_min_cells`・`gsis.damp_kn`、`params.deq_fallback`・
  `params.pressure_boundary_reconstruction`・`params.cold_bgk_temperature`、`macro_params.gsis_hot_kn`。`--set gsis.filter_min_cells=…`・
  `gsis.damp_kn=…` も使える(0 以上)。
  フェーズ 5c で足したもの(既存の列・キーは変えていない。`gpu_memory_bytes` は最後の列のまま): `run`(GSIS)の `history.csv` の
  `M_dot_pbc_outlet` の後に `mass_imbalance`・`mass_imbalance_pbc`・`M_dot_sec<k>`・`M_dot_pbc_sec<k>`、`cis-run`(と `run --mode cis`)の
  `history.csv` の `t_other_s` の後に `mass_imbalance`・`M_dot_sec<k>`(管でなければ断面の列の接頭辞は `mdot_nd_`)、`summary.json` の
  `mass_balance`(境界の釣り合い・断面のばらつき・許容値・警告)・`mass_flow_sections`(断面ごとの位置・面の数・面積・流量)、
  GSIS の `gsis.mass_balance_tolerance`・`gsis.mass_balance_required`、`cis-run` の `params` の CIS の設定(`sweep_order` など)。
  引数 `--sections <位置の並び>`(`run`・`cis-run`)、`--set gsis.mass_balance_tolerance=…`・`gsis.mass_balance_required=true|false`
  (`cis-run` でも使える)。ケースの `[output].mass_flow_sections`(例 `"8, 10.5, 13, 15.5, 18"`)・`mass_flow_section_axis`(既定 `"x"`)、
  `[gsis].mass_balance_tolerance`(既定 2e-3)・`mass_balance_required`(既定 false)。`docs/design_gsis.md` §6a

出力は UTF-8。PowerShell で出力を変数やファイルに受けるときは、先に `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` を実行しないと文字化けする(画面に表示するだけなら不要)。

確認した結果の例(`cases/smoke/box.pmesh`、128 セル): 閉性の残差 8.7e-17、総体積 1.6e-8 m³(箱の体積と一致)、非直交角の最大 14.1°、帯幅 32 → RCM 後 20。

Python(`gsis_tools`)が書いた `.pmesh` も読めることを確認した(`cases/tube_vacuum/mesh/tube_hex.pmesh`(135,104 セル)・`tube_tet.pmesh`(133,586 セル)、`cases/slab/mesh/*.pmesh` の 5 つ)。Python の `uv run --project python --no-sync gsis-tools mesh-info` と比べると、面数・セル数・パッチは同じで、体積の合計の差は相対 3e-14 以下、セルごとの体積の差は相対 8e-14 以下(両者の VTU のセル値で比較)。非直交角は、角度が 0° に近い面で Python 側の値が acos の分解能(約 1.7e-6 度)で止まる以外は一致する(1° を超えるセルで差 1e-12 度)。管の六面体メッシュの `mesh-info --vtu` は約 0.7 秒。`check-case` の分布関数のメモリ量は、管ケースで FP32 9.34 GB、作業領域 4.43 GB(spec §4.4 と一致)。`gpu-info --smoke --cells 4096` では、GPU と CPU の差は FP64 で 1.4e-13 以下、FP32 で 5.3e-8 以下、同じ入力の 2 回の計算はビット単位で一致、GPU の時間は FP64 で 23 ms、FP32 で 10 ms。

### VTU の確認

`mesh-info --vtu` の出力(歪んだ六面体、四面体、appended と ASCII の両形式、境界面)を、Python の `vtk` 9.7.1 の `vtkXMLUnstructuredGridReader` で読めることを確認した。全セルが `VTK_POLYHEDRON`(境界面は `VTK_POLYGON`)として読まれ、面はすべて外向き、VTK が読んだ面の点列から docs/pmesh_format.md §4 の定義で計算した体積は出力の `volume` と 1e-15 の精度で一致した。

なお、ParaView / VTK の Cell Size フィルタ(`vtkCellSizeFilter`)は多面体を凸包で四面体分割するため、面が平面でない凹んだセル(歪んだ六面体など)では体積が大きめに出る。メッシュの体積は VTU のセル値 `volume` を見ること。

## 5. スモーク用のケース

`cases/smoke/` に小さなメッシュとケースを置いている。メッシュはテスト用の補助ツールで作った(作り直すときも同じコマンド)。

```powershell
.\build\release\tests\gsis-write-box-mesh.exe cases/smoke/box.pmesh --n 8 4 4 --lo 0 -1e-3 -1e-3 --hi 4e-3 1e-3 1e-3 --distort 0.15 --tube
.\build\release\tests\gsis-write-box-mesh.exe cases/smoke/box_tet.pmesh --n 4 2 2 --lo 0 -1e-3 -1e-3 --hi 4e-3 1e-3 1e-3 --tets --tube
```

| ファイル | 内容 |
|---|---|
| `box.pmesh` | 4 mm × 2 mm × 2 mm の箱、8×4×4 の六面体(内部の節点を格子間隔の 15% まで乱数で動かした)。パッチは inlet(x−)、outlet(x+)、wall |
| `box_tet.pmesh` | 同じ箱を 4×2×2 の六面体に分け、各六面体を 6 個の四面体にしたもの(96 セル) |
| `smoke.toml` | `box.pmesh` を使うケース(気体・条件・速度格子は管の δ_rp = 10 と同じ) |

`gsis-write-box-mesh` のオプション: `--n NX NY NZ`、`--lo X Y Z`、`--hi X Y Z`(m)、`--distort D`(0〜0.3)、`--seed S`、`--tets`、`--tube`(付けないと 6 面が別々のパッチ xmin〜zmax)。

## 6. 依存ライブラリ

`cmake/GsisDependencies.cmake` で、版と SHA-256 を固定して取得する。

| ライブラリ | 版 | 用途 |
|---|---|---|
| toml++ | v3.4.0(MIT) | ケース定義の読み込み(ヘッダのみ) |
| Catch2 | v3.16.1(BSL-1.0) | 単体テスト(`GSIS_BUILD_TESTS=ON` のときだけ) |
| AMGCL | 1.5.0(MIT) | PBC マクロソルバーの疎行列の反復ソルバー(ヘッダのみ、Boost なし `AMGCL_NO_BOOST`。フェーズ 3 で追加) |
| OpenMP | MSVC の `/openmp`(OpenMP 2.0) | PBC マクロソルバーと AMGCL の並列化(見つからなければ 1 スレッドで動く) |

`.pmesh` の JSON ヘッダは依存を増やさないよう、自前の小さな JSON の読み書き(`src/core/json.*`)で扱う。Eigen は使っていない。

オフラインで構成するときは、取得済みのソースを指定する:

```powershell
cmake --preset release -DFETCHCONTENT_SOURCE_DIR_TOMLPLUSPLUS=<toml++ のソース> -DFETCHCONTENT_SOURCE_DIR_CATCH2=<Catch2 のソース> -DFETCHCONTENT_SOURCE_DIR_AMGCL=<AMGCL のソース>
```

## 7. ソースの構成

| ディレクトリ | 内容 |
|---|---|
| `src/core` | 型(`Real`、`Index`、`Vec3T`)、`GSIS_HD`、エラー(`GSIS_CHECK` など)、ロガー、タイマー、JSON、パス(UTF-8) |
| `src/config` | ケース TOML の読み込み・検証、導出量と無次元化の基準量、ケースとメッシュ・速度格子の突き合わせ |
| `src/mesh` | `.pmesh` の読み書きと検査、幾何量、有限体積の量(d_ij、d_n、補間重み、最小二乗勾配、セル → 面の CSR)、品質の統計、RCM とブロック分割、GPU 用の FP32 の写し |
| `src/velocity` | 一様デカルト速度格子(中点配置)、鏡映の対応表 |
| `src/physics` | Maxwell・Shakhov 分布、半空間流束とヤコビアン(`__host__ __device__`)、モーメント(CPU、FP64) |
| `src/io` | VTU(多面体・境界面)、CSV |
| `src/gpu` | CUDA の誤り検査、デバイス情報、GPU 配列、スモークテストのカーネル |
| `src/app` | CLI `gsis-solver` |
| `tests` | Catch2 のテスト、テスト用メッシュ生成(`tests/support`)、`gsis-write-box-mesh` |

## 8. 困ったとき

- `cl.exe` や `ninja` が見つからない: 開発者環境に入っていない(§2)。
- 警告 C4819 が大量に出る: `/utf-8` が効いていない。自前で CMake を呼ぶときも `cmake/GsisCompilerOptions.cmake` を通す。
- GPU のカーネルが止まる(画面が一瞬消える): 表示用の GPU は WDDM の TDR(既定 2 秒)の対象。1 回のカーネルを 100 ms 未満にする(`gpu-info` の「カーネル時間の上限 TDR」)。
- Linux / WSL2(任意、未確認): `cmake -S . -B build/linux -G Ninja -DCMAKE_BUILD_TYPE=Release` で同じ CMakeLists を使える想定(プリセットは Windows 用)。

## 9. フェーズ8 S担当のサンドボックスでの確認

作業用の一時フォルダはリポジトリ内に置く。構成済みのビルド `build/phase8_s` を使用する。

```powershell
New-Item -ItemType Directory -Force build\tmp_s | Out-Null
$env:TEMP = "$PWD\build\tmp_s"
$env:TMP = $env:TEMP
cmd /c 'call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul && cmake --build build\phase8_s'
cmd /c 'call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul && ctest --test-dir build\phase8_s --output-on-failure'
```

`vswhere.exe` が見つからない旨の出力はこの環境では無害。実行中のexeはWindowsで上書きできないため、長時間の比較計算は `runs/phase8/s1/` に固定したexeをコピーして使う。
S1の試験では、歪んだ四面体の全ての面の正値性(FP32/FP64)、`limiter="none"` との組合せ、GPU/CPU一致と決定性を追加した。固定fの境界反復の検証も含め、全155件。

Pythonツールを呼ぶときは、このサンドボックスに限り `python\.venv\Scripts\python.exe` を直接使う。依存関係は変更しない。
日本語のコードをPowerShellのパイプ経由でPythonに渡す場合は、先に `$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()` と `$env:PYTHONUTF8 = '1'` を設定する。
計算の再現方法と結果は `docs/phase8_s1_report.md` を参照。

## 10. フェーズ8 S2 の CLI と JSON 契約(G3b 向け)

```powershell
build\phase8_s\src\gsis-solver.exe check-case case.toml --json
build\phase8_s\src\gsis-solver.exe check-case case.toml --json --no-mesh
build\phase8_s\src\gsis-solver.exe check-case case.toml --json --free-vram-bytes 15784004812
build\phase8_s\src\gsis-solver.exe run case.toml --mode nsf --cells-csv
```

`--json` の標準出力は JSON 文書1個だけ。終了コードは成功0、入力・メッシュなどの誤り1、CLI引数の誤り2。失敗でも `ok=false` と `errors` を返す。求め済みの項目は失敗時にも保持し、未計算の項目は省略またはnullとする。`--no-mesh` は意図的にメッシュを読まない設定段階の検査。指定しなければ従来どおりメッシュの読み込み・整合性検査まで行い、メッシュがなければ終了コード1。JSONのパッチ順はTOMLの順。

| キー | 型・単位・意味 | メッシュが必要か |
|---|---|---|
| `schema_version` | 整数、現在1 | 不要 |
| `ok`, `errors`, `warnings` | 真偽値、エラー文字列配列、警告文字列配列 | 不要 |
| `mesh_loaded` | メッシュの読み込みを終えたか | 不要 |
| `input.name`, `input.mode`, `input.case_file`, `input.mesh` | 名前、`gsis/cis/nsf`、入力ファイル、解決済みメッシュパス | 不要 |
| `input.gas.molecular_mass_kg`, `mu_ref_Pa_s`, `T_ref_K`, `omega`, `Pr`, `gamma` | 分子質量[kg]、粘性の基準[Pa s]・温度[K]・指数、Pr、比熱比 | 不要 |
| `input.conditions.L_ref_m`, `T0_K`, `delta_rp`, `Kn`, `p_ref_Pa` | 指定値。圧力指定の3方式のうち不使用のキーはnull | 不要 |
| `derived.R_J_kg_K`, `mu_Pa_s`, `mean_free_path_m`, `Kn`, `delta_rp` | R[J/(kg K)]、μ(T0)[Pa s]、λ[m]、Kn、√π/(2Kn) | 不要 |
| `reference.length_m`, `temperature_K`, `pressure_Pa` | 無次元化の L_ref[m]、T_ref=T0[K]、p_ref[Pa] | 不要 |
| `reference.density_kg_m3`, `velocity_m_s`, `time_s` | ρ_ref[kg/m³]、√(R T0)[m/s]、L_ref/√(R T0)[s] | 不要 |
| `reference.distribution_kg_s3_m6`, `viscosity_Pa_s`, `heat_flux_W_m2`, `mass_flux_kg_m2_s`, `mass_flow_scale_kg_s` | f、粘性、熱流束、質量流束、管のṀの分母のSI基準量 | 不要 |
| `velocity_grid.auto`, `n`, `n_velocity` | 自動か、各軸の点数[3]、速度点総数 | 不要 |
| `velocity_grid.range`, `step` | 各軸の区間[[下限,上限]×3]、幅/n[3]。単位√(R T0)、中点則 | 不要 |
| `velocity_grid.range_m_s` | 同じ区間のSI値[m/s] | 不要 |
| `velocity_grid.T_min_K`, `T_max_K`, `u_max_m_s` | 自動決定に使用した温度[K]と速度の大きさ[m/s]。自動時のみ | 不要 |
| `patches[].name`, `type`, `temperature_K`, `pressure_Pa`, `velocity_m_s`, `tangential_velocity` | 条件の主な値。適用されない物理量はnull、接線指定は圧力入口だけ。省略された入口圧力は導出したp_in | 不要 |
| `patches[].faces`, `area_m2` | 面数、面積[m²]。未読・対応するパッチがない場合null | 必要 |
| `mesh.n_cells`, `n_faces`, `volume_m3` | セル数、面数、総体積[m³] | 必要 |
| `memory` | 下表。メッシュ未読時はnull | 必要 |

`gas.T_ref` は粘性則の温度であり、無次元化の `reference.temperature_K=T0` とは用途が異なる。L_ref/T0/基準圧力は従来どおり入力する。境界条件からの基準値の提案はGUIが担う。

### 自動速度格子

`[velocity_grid] auto=true` で n・range を省略する。手動では従来の n・range を使用し、`auto=false` も指定可能。`auto=true` と n または range の併用はエラーにする。境界の温度から Tmin/Tmax、壁速度のベクトルの大きさから umax を取る。温度が1個もなければTmin=Tmax=T0、速度がなければ0。

`limit = umax + 5 sqrt(R Tmax)`、`n = max(2, 2 ceil(limit / (0.7 sqrt(R Tmin))))` を3軸共通に使い、範囲は `±limit/sqrt(R T0)`。中点の刻みは幅/nであり、端点間隔の幅/(n−1)ではない。総速度点1億点までの既存の上限を適用する。現在の圧力入口の `tangential_velocity` は `zero/extrapolate` であり、入力速度ベクトルではない。外挿される内部速度・低温噴流まで自動格子の精度を保証するものではない。

### VRAM 見積もり

| `memory` のキー | 単位・意味 |
|---|---|
| `distribution`, `workspace`, `overhead`, `total` | バイト。f、チャンク作業領域、保持する固定配列、その和 |
| `free`, `reserve`, `budget` | バイト。仮定/取得した空き、予約1 GiB、max(0,free−reserve) |
| `chunk` | 速度チャンク幅。自動は256点単位、手動は鏡映の組の大きさの倍数へ切り上げ。総速度点数で上限 |
| `fits` | 合計≤予算かつチャンク≥1。NSFはCPUなのでtrue |
| `model`, `precision` | `cuda_arrays_v1` と `fp32/fp64`。NSFは `nsf_cpu`、precisionなし |
| `free_source` | `argument` / `cuda_device_0` / `default_14.7_GiB` |

空きは `--free-vram-bytes` を優先し、通常はGPU 0の現在の空きを取得する。CUDAなし・デバイスなしでは14.7 GiB。`memory.fits=false` は警告であり、ケースの入力誤りにはしない。NSFはGPU配列を確保しないのでf・作業領域・固定費・合計・チャンクをすべて0にする。

セル数Nc、速度点Nv、実数のバイト数b(4または8)、チャンクkに対し、f=`Nc Nv b`、作業領域=`4 b Nc k + 8 Nc ceil(k/256)`。固定費はCUDAバックエンドの各配列の要素数と `sizeof` から積算する。`Nc` 個のFP64値31本とint値1本(体積・時間刻み・モーメント・シフト・残差・GSIS補正用)、CSR、境界面、流入分布、衝突係数、速度座標、スイープ順序などを含む。FP32/FP64・対称面ありの単体試験で、手動チャンクの合計が `backend.memory_bytes()` に一致することを確認している。

自動チャンクは最終固定配列の費用を先に差し引き、残りを `Nc(4b+8/256)` で割って256単位に切り下げる。現バックエンドは補正用の一部の配列をチャンク決定後に確保するため、見積もりの自動幅は安全側で、実行時の幅より256点程度小さくなる場合がある。CUDAコンテキスト・WDDM・一時的なアップロードは固定配列に含めず予約分で余裕を持つ。空きは実行までに変わり得る。

GUIの旧暫定式からの変更点は、予約1,000,000,000→1,073,741,824 bytes、固定費100,000,000→実配列積算、残差部分和配列の追加、精度・鏡映・256点丸めの考慮、NSFの全GPU費用0。速度格子と導出量の式はGUIと同じ。G3bでこの差を反映すること。

### summary.json のパッチ出力・停止・診断

`run` のCIS・GSIS・NSFの `patches[]` に次の共通キーを出す。CIS/GSISは最終f(GSISは補正後)の半空間積分、NSFは最終状態のPBC境界分布・フラックスを使う。

| キー | 単位・符号 |
|---|---|
| `name`, `type`, `faces`, `area_m2` | 名前、種類、面数、面積[m²] |
| `mass_flow_kg_s` | kg/s、領域へ入る向きが正 |
| `heat_flow_W` | W、面ごとの局所流速に対する三次中心モーメントqの法線積分。領域へ入る向きが正 |
| `energy_flow_W` | W、移流と仕事を含む全エネルギー輸送。領域へ入る向きが正 |
| `force_N` | [Fx,Fy,Fz]、N。外向き運動量フラックス。固体パッチでは気体が固体へ及ぼす力。入口・出口では運動量輸送を表す |

外向き法線n、面積A、無次元の面フラックスF=(M,Px,Py,Pz,E)、面分布のモーメント(ρ,u,T)から `Q = E − u·P + |u|² M/2 − (3ρT/2)(u·n)A` を面ごとに計算し、パッチ内で和を取る。熱流量は `−ΣQ p_ref sqrt(R T0) L_ref²`。NSFもPBCの最終の半空間分布のモーメントを使い、勾配に使用したラグ付きの面状態とは区別する。

既存のCIS/GSISの `mdot_kg_s` は外向きが正なので、新キー `mass_flow_kg_s` と符号が逆。`energy_flux_nd` と境界VTUの `energy_flux`[W/m²]も外向きの全エネルギーであり、`energy_flow_W = −energy_flux_nd p_ref sqrt(R T0)L_ref² = −∫energy_flux dA`。局所熱流量の `heat_flow_W` は移流・仕事を除くため、一般には一致しない。旧 `pbc-run` の既存 `mass_flow_kg_s`・`energy_flow_W` は外向きの意味を維持する。上表の流入正の共通契約は `run --mode nsf` / `case.mode="nsf"` に適用する。

静止固体壁との正味のエネルギー授受を評価する場合は `energy_flow_W` を使う。`heat_flow_W` は局所気体の静止系でのqであり、すべり速度がある壁では、静止壁との正味熱授受とも異なり得る。動く壁との熱授受では壁速度による仕事も別途考慮する。

出力先に `STOP` があれば各反復の終わりに停止する。`stopped_by_user=true` を従来の `stopped=true` に追加し、その時点の残差と通常の成果物を出す。停止時は `--no-vtu`・`--cells-csv` の有無によらず `fields.vtu`・`boundary.vtu`・`cells.csv` を保存する。STOPは削除せず保持するため、再実行時は呼び出し側が削除する。GSISのNSF初期化中はPBC反復の終わり、通常のGSIS段ではCIS→PBC→補正を終えた外側反復の終わりに止める。

NSFは `run` の `--max-iter`・`--tol`・`--threads`・`--init-from`・`--set macro.*` と `--set gsis.*` をPBC単独へ渡す。NSFの `epsilon` は従来PBCの `final_update`(p/u/T更新ノルムの最大)であり、GSISの式(59)とは定義が違う。`residual` は最終の質量残差。`fields.vtu` には共通のSI場とNSFのσ・qを出す。`cells.csv` は従来PBCの列順(cell,x,y,z,volume,p,ux,uy,uz,T,rho)なので列名で読む。NSFの `--init-from` は列名で読み、CIS/GSISのCSVからも再開できる。NSFでは運動論の精度・バックエンド指定は使わずCPUで計算する。`--min-iter`・`--vtu-every`・`--dump-every`・`--save-init`・`--sections` はNSF経路では未対応。

GSISの追加 `conservation_diagnostics`:

| キー | 意味 |
|---|---|
| `state` | `corrected_f` |
| `inlet_kg_s`, `outlet_kg_s` | 補正後fでの圧力入口の流入総量・出口/真空の流出総量[kg/s] |
| `relative_imbalance`, `valid` | (出口−入口)/max(abs(入口),abs(出口))。入口・出口がない/ゼロならnull、valid=false |
| `warning` | 上の差の絶対値が `gsis.mass_balance_tolerance` を超えたか |
| `last_pbc_correction.epsilon`, `rho`, `u`, `T` | フィルタ・減衰後のW_newとCIS後W_halfの式(59)型相対変化。外側反復0回ならnull。epsilonは3成分の最大 |

既定の収束判定は変更していない。`converged=true` でも `warning=true` になり得る。従来の `mass_balance`、`mass_flow`、`pbc_change` は維持する。局所凍結・負値置換のあるセルでは、上記補正ノルムが実際のΔfのノルムと一致するとは限らない。

### S3の境界条件と追加出力

`maxwell_wall` は必須の `temperature`[K]・`accommodation`(0<α≤1)、任意の `velocity`[m/s]を受け付ける。α=1は既存のdiffuse_wallと同じ計算経路。一般法線の鏡面反射には平衡分布で規格化した三線形補間と零質量流束の密度補正を使う。NSF単独も半空間PBC境界を使うが、連続体のすべり・温度跳躍条件と同じ精度ではないことを警告する。

`mass_flow_inlet` は必須の `mass_flow`[kg/s、流入正、正値]・`temperature`[K]、任意の `tangential_velocity="extrapolate"|"zero"` を受け付ける。初期圧力はp_ref。CISは各スイープ前、GSISは補正後fを使って各外側反復前、NSFは各マクロ反復で圧力を更新する。新境界がある場合だけ、従来のノルム判定に加え、各入口の目標との相対差≤0.001を収束の必要条件とする。GSISは補正後fで判定する。全体の保存性警告は引き続き別途確認する。

| 場所・キー | 意味 |
|---|---|
| check-case / summary の `patches[].accommodation` | Maxwell壁のα |
| check-case / summary の `patches[].target_mass_flow_kg_s` | 質量流量入口の目標、流入正[kg/s] |
| check-case の `patches[].pressure_Pa` | 質量流量入口の初期圧力p_ref[Pa] |
| summary の `patches[].pressure_Pa` | 質量流量入口の最終のp_b[Pa] |
| summary の `patches[].mass_flow_relative_error` | `abs(Q−target)/target`、Qは最終流量 |
| history.csv の `p_b_<patch>_Pa` | 行に対応する評価時のp_b。対象入口だけ末尾に追加 |

check-caseの `tangential_velocity` と自動格子の温度集計には質量流量入口も含める。`conservation_diagnostics.inlet_kg_s` と管の流量診断の入口分類にも含める。部分適応壁がある場合、`memory.overhead` に反射キャッシュ・面の対応・格子の逆対応を含める。既存境界のみの場合の見積もりは維持する。実装の導出・速度格子への依存は `docs/design_bc_phase8.md` を参照。
