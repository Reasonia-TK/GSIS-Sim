# GSIS-PBC

arXiv:2610.07658v1 の GSIS-PBC(希薄気体の定常解法)を C++/CUDA で実装し、論文の「円管→真空」4 条件を再現するプロジェクト。

## 資料

| ファイル | 内容 |
|---|---|
| `docs/spec.md` | 仕様(決定事項、範囲、数値手法、構成、検証計画、開発フェーズ) |
| `docs/paper_notes.md` | 論文の要点、テストケース諸元、論文に書かれていない点と仮定案 A1〜A15 |
| `docs/pmesh_format.md` | 内部メッシュ形式 `.pmesh`(C++ と Python の間の受け渡し) |
| `docs/build.md` | C++/CUDA のビルドとテストの手順 |
| `docs/design_cis_gpu.md` | フェーズ 2 の設計: GPU 運動論ソルバー(CIS) |
| `docs/design_pbc.md` | フェーズ 3 の設計: PBC マクロソルバー(CPU) |
| `docs/reproduction_report.md` | 再現レポート(管 4 条件の結果、論文との違い、受け入れ基準の達成状況、考察) |
| `docs/progress.md` | 進捗ボード(現状、次にやること、利用上限の後の再開手順) |
| `docs/phase*_report.md`、`docs/design_gsis.md` | 各フェーズの作業記録と、GSIS の結合の設計 |
| `docs/gui.md` | GUI の使い方(AI モデリングの画面、API の接続とキーの扱いを含む) |
| `docs/mcp.md` | AI モデリング用 MCP サーバー(gmsh の上の薄いツール層)の使い方とツールの一覧 |
| `docs/python_tools.md` | Python ツール(メッシュ生成・変換・検査)の使い方 |
| `docs/dsmc_survey.md` | DSMC による検証の調査と計画(ユーザーの DSMC コードの使い方) |
| `docs/eval_solidcraft.md` | solidcraft の評価(将来の AI モデリング機能の検討) |

## ディレクトリ

| パス | 内容 |
|---|---|
| `CMakeLists.txt`, `cmake/`, `src/`, `tests/` | C++/CUDA のソルバー本体と単体テスト |
| `python/` | Python(uv)のツール群 `gsis_tools`(メッシュ生成・変換・後処理・GUI) |
| `cases/` | ケース定義(TOML)とメッシュ |
| `verification/` | 検証用の参照解(1 次元の参照ソルバーなど) |
| `validation/dsmc/` | DSMC による検証の入力・実行スクリプト・結果 |
| `reference/` | 論文の数値(表の CSV、図のデジタイズ値) |

## 規約

- 文書・報告・コード中のコメントは日本語。識別子・コマンド・パスは英語のまま。
- C++20 / CUDA C++20。名前空間は `gsis`。ファイル名は snake_case(`.hpp` / `.cpp` / `.cuh` / `.cu`)。
- 入出力は SI 単位、内部計算は無次元量(`docs/spec.md` §3.2)。
- GPU と CPU で共有する 1 セル・1 速度点の計算は、ヘッダ内の `__host__ __device__` 関数にまとめる。
- 集計(総和)に atomic を使わず、決まった順序で足す(同じ入力なら同じ結果になるようにする)。
- Python は `uv` で管理する(`python/pyproject.toml`)。実行はリポジトリのルートから `uv run --project python ...`。依存の追加は `uv add`(`python/` の中で)。

## 作業環境の注意

- GPU は RTX 5070 Ti(`sm_120`、VRAM 16 GB)。画面表示と共用で、空きは約 14.7 GiB。分布関数の VRAM 予算は `docs/spec.md` §4.4。
- 表示に使っている GPU なので WDDM の TDR(既定 2 秒)がかかる。1 回のカーネル実行は 100 ms 未満を目安にする。
- 日本語ロケール(CP932)では MSVC と nvcc に `/utf-8` が必要。
- `C:\Works\20261003_DSMC`(ユーザーの DSMC コード)は、このプロジェクトのエージェントからは読み取り専用。DSMC の入力と結果は `validation/dsmc/` に置く。DSMC コードの不具合は、ユーザーの指示により Codex(`C:\Users\TK\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe exec -C C:\Works\20261003_DSMC ...`)に修正させる。

## 並列作業のルール

- 複数のエージェントで並行して作業するときは、割り当てられたディレクトリ以外を編集しない。仕様や他の担当範囲に問題を見つけたら、直さずに報告する。
- `docs/spec.md`・`docs/paper_notes.md`・`docs/pmesh_format.md`・本ファイルの変更は、統括役(メインのエージェント)が行う。
- 共有モジュール(`src/core`・`config`・`mesh`・`velocity`・`physics`・`io`・`gpu`・`gsis`、`src/app/main.cpp`・`cli.hpp`、最上位・`src/`・`tests/` の CMakeLists.txt)は統括役が管理する。担当者は既存ファイルを編集せず、必要なものは自分のディレクトリに置くか統括役に頼む。
- ビルドフォルダは担当ごとに分ける(同じフォルダで同時にビルドすると壊れる)。`build/release` などのプリセットのフォルダは統括役が使う。

### GPU の順番待ち(2026-10-17 から)

管のような大きな計算は 1 本で VRAM を約 14 GB 使うので、2 本を同時には流せない。VRAM を 2 GB 以上使う GPU の計算は、次の手順で 1 本ずつ流す。
- 始める前に、ディレクトリ `runs\.gpu_lock` を作る(作れれば自分の番。すでにあれば、なくなるまで待つ)。中に `owner.txt`(担当、計算の内容、開始時刻)を書く
- 終わったら(失敗・中断しても)`runs\.gpu_lock` を消す
- 小さな試験(VRAM 2 GB 未満)は順番待ちなしでよい

### フェーズ 8 の担当(2026-10-18 から。汎用アプリ化、`docs/design_general_app.md`)

コードは Codex(`gpt-6.1-sol`、推論 high)が書く(ユーザーの指示)。統括役が依頼・確認・commit をする。C++ 側と Python 側を 1 本ずつ並行する。

| 担当 | 編集してよい場所 | ビルド |
|---|---|---|
| S(ソルバー。S1 四面体 → S2 → S3 → S4) | C++ 全体、`verification/ref1d/`(S3)、`cases/general/`、`runs/phase8/`、`docs/design_bc_phase8.md`、`docs/design_host_offload.md`、`docs/phase8_s*_report.md`、`docs/build.md` | `build/phase8_s` |
| G(GUI。G1 → G2 → G3) | `python/`、`docs/gui.md`、`docs/gui_screenshots/`、`docs/phase8_g*_report.md` | なし(Python のみ) |

### GUI への AI モデリングの組み込みの担当(2026-10-18、完了)

| 担当 | 編集してよい場所 |
|---|---|
| GUI の AI モデリング | `python/`(`gsis_tools.gui`、試験、依存の追加は extra `gui`。`gsis_tools.mcp` は GUI に必要な最小限の追加だけ)、`docs/gui.md`、`docs/gui_screenshots/`、`docs/mcp.md`(足したツールの追記だけ) |

- git: リポジトリは https://github.com/Reasonia-TK/GSIS-Sim.git(`origin`、`main`)。担当者はコミットも push もしない。コミットと push は統括役が行う。

### フェーズ 7 の担当(2026-10-17、完了)

| 担当 | 編集してよい場所 | ビルド |
|---|---|---|
| フェーズ 7(gmsh の上の AI モデリング用 MCP) | `python/`(サブパッケージ `gsis_tools.mcp`、依存の追加は extra `mcp`)、`cases/mcp_demo/`、`docs/mcp.md` | なし(Python のみ) |

### フェーズ 5c の担当(2026-10-17、完了)

| 担当 | 編集してよい場所 | ビルド |
|---|---|---|
| 5c(診断の改良、CIS 単独との比較、中心線、DSMC の感度、最終の再現レポート) | C++ 全体、`cases/`(既存のメッシュは変えない)、`runs/`、`validation/dsmc/`、`docs/design_*.md`、`docs/build.md`、`docs/phase5c_report.md`、`docs/reproduction_report.md` | `build/phase5c` |

### フェーズ 5b の担当(2026-10-17、完了)

| 担当 | 編集してよい場所 | ビルド |
|---|---|---|
| 5b-1(δ=1000 の安定化。CIS の新しい設定を既定値にする) | C++ 全体、`cases/tube_vacuum/phase5b_d1000/`、`runs/phase5b/d1000/`、`docs/design_*.md`、`docs/build.md`、`docs/phase5b_d1000_report.md` | `build/phase5b` |
| 5b-2(δ=10 の質量流量の差の感度調査。コードは変えない) | `cases/tube_vacuum/phase5b_d10/`(メッシュの生成物を含む)、`runs/phase5b/d10/`、`docs/phase5b_d10_report.md` | ビルドしない。固定版 `runs\phase5b\bin\gsis-solver-5a.exe` を使う |

### フェーズ 5a の担当(2026-10-16、完了)

| 担当 | 編集してよい場所 | ビルドフォルダ |
|---|---|---|
| フェーズ 5a(CIS の強化と保存的な離散平衡、管 4 条件の GSIS) | C++ 全体、`cases/`(メッシュの生成物を除く)、`runs/`、`docs/design_*.md`、`docs/build.md`、`docs/phase5_report.md` | `build/phase5` |

### フェーズ 4・6 の担当(2026-10-16、完了)

| 担当 | 編集してよい場所 | ビルドフォルダ |
|---|---|---|
| フェーズ 4(GSIS の結合) | C++ 全体(`src/`、`tests/`、CMake のファイル)、`cases/`(メッシュの生成物を除く)、`runs/gsis/`、`docs/design_*.md`、`docs/build.md`、`docs/phase4_report.md` | `build/phase4` |
| フェーズ 6(GUI) | `python/`(GUI のサブパッケージ `gsis_tools.gui`、依存の追加は extra `gui`)、`docs/gui.md`、`docs/gui_screenshots/` | なし(Python のみ) |

### フェーズ 2・3 の担当(2026-10-16、完了)

| 担当 | 編集してよい場所 | ビルドフォルダ |
|---|---|---|
| フェーズ 2(GPU 運動論) | `src/kinetic/`、`src/app/cmd_cis_run.cpp`、`tests/kinetic/`、`cases/verification/kinetic/`、`cases/tube_vacuum/kinetic/`、`runs/kinetic/`、`docs/design_cis_gpu.md`、`docs/phase2_report.md` | `build/phase2` |
| フェーズ 3(PBC) | `src/macro/`、`src/app/cmd_pbc_run.cpp`、`tests/macro/`、`cmake/GsisDependencies.cmake`(線形ソルバーの追加だけ)、`cases/verification/macro/`、`cases/tube_vacuum/macro/`、`runs/macro/`、`docs/design_pbc.md`、`docs/phase3_report.md` | `build/phase3` |
