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
