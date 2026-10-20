# フェーズ8 G3c 作業報告

- 担当: G / 2026-10-20。依頼G3c、`docs/design_general_app.md`、`docs/design_bc_phase8.md`、S3報告と `docs/build.md` §10 を根拠に実装した。
- 開始時のG3b・S3の未コミット変更を保持し、その上に追加した。`.handover/2026-10-18_2225.md` は古い状態なので、現在の指示と実ファイルを優先した。引継ぎ資料への追記や残作業の自動実行はしていない。
- 使用した固定版は **`runs/phase8/s3/gsis-solver-s3-final.exe`**。SHA256: `776b3a3ae4a3b997e28bd1c77397004ae28ff545803db19abacb2e9909c8c18f` (S3報告と一致)。`build/` は指定された一時領域 `build/tmp_g` だけを使った。
- 依存追加、C++ビルド、commit・push、GPUでの計算は行っていない。本物の `runs/.gpu_lock` を作成・削除していない。担当外のケース、C++、検証コード、仕様・設計・進捗文書も編集していない。

## やったこと

1. 仮実装の境界フォームとS3契約を照合した。Maxwell壁は温度・α・任意の壁速度、質量流量入口は正のパッチ全体流量・温度・接線速度を入力する。αの既定1、0<α≤1、流量の正値・有限値・空欄の検査と説明を補った。接線速度は `zero/extrapolate` の選択で、任意キーの省略も許容する。
2. 旧ケースモデル・編集窓も新境界に対応した。α・目標・接線速度を読み書き・複製し、TOML往復や境界の種類変更で値を失わないようにした。既存の圧力入口の接線速度も保持する。
3. `check-case --json` の `patches[].accommodation`、`target_mass_flow_kg_s`、質量流量入口の初期 `pressure_Pa` と接線速度を設定・メッシュの検査パネルへ表示する。
4. 設定・境界・実行に `p_b=p_ref` から開始する旨を表示する。圧力を指定する入口・出口がない場合は設定のp_refを保持し、自動推定しないことを明記した。真空は指定圧力境界に数えない。
5. α<1とNSFでは半空間Maxwell混合のPBC境界の精度上の注意を表示する。連続体のすべり・温度跳躍の精度は未保証で、完全拡散壁の既定NSFすべり係数を流用しない。S3引継ぎ3のref1d側の拒否と区別して記載した。
6. `velocity_grid()` の境界既定値集計を照合した。新しい温度・壁速度を拾い、接線速度の選択はベクトルに数えない。温度100/700 K、静止/移動Maxwell壁でC++の自動格子と一致することを確認した。
7. `history.csv` の `p_b_<patch>_Pa` を実行・結果の履歴グラフの3段目へ描く。複数入口と `_`・`-` を含むパッチ名に対応する。旧履歴は従来の2段を維持する。
8. パッチ集計タブの上に質量流量入口専用の表を追加した。最終p_b・目標・最終流量・相対誤差[%]を表示する。誤差>0.001、または誤差を確認できないと警告する。実行終了時の状態・ログ・結果上部にも警告し、既存の全体保存性診断を併記する。
9. パッチ表に種類・αを表示する。CSVにはα・最終p_b・目標・比率の相対誤差を追加し、新SI出力の流入正と旧出力の外向き正を保持した。質量流量入口は既存の `history.INFLOW_TYPES` に登録済みで、GSISの保存性診断の入口集計まで実出力で検証した。
10. 読み込んだ `[kinetic] precision` が設定画面の追加キー欄で未知キー扱いになる不具合を修正した。精度は実行画面で選ぶ既存設計を保ち、設定画面で編集しない値を保存時に保持する。
11. 操作説明を `docs/gui.md` に追記し、撮影用スクリプトを追加した。

## 変えたファイル

以下はG3cで実際に編集・新規作成したもの。開始時からのG3bの変更全体の一覧ではない。

| ファイル | 内容 |
|---|---|
| `python/src/gsis_tools/gui/boundaries.py` | p_ref・NSFの注意、接線指定の省略 |
| `gui/pages/boundaries_page.py` | 新境界の説明・検査・αの既定 |
| `gui/casefile.py`・`gui/pages/case_page.py` | 旧モデル・編集窓の新境界と往復 |
| `gui/general_settings.py` | 速度集計をC++と同じ壁速度ベクトルに限定 |
| `gui/check_case.py` | 新境界のJSON値表示(G3bの新規ファイルへの追加) |
| `gui/pages/settings_page.py` | 境界の注意、読み込んだprecisionの保持 |
| `gui/history.py`・`gui/widgets/history_plot.py` | p_b列の認識とPaのグラフ |
| `gui/patch_results.py`・`gui/pages/boundary_results.py` | α・入口の最終制御値・CSV |
| `gui/results.py`・`gui/pages/run_page.py` | 誤差とNSFの警告、既存診断との併記 |
| `gui/screenshots_g3c.py` (新規) | 12セル箱と撮影 |
| `python/tests/test_gui_s3.py` (新規) | 新境界の単体・CPU接続試験 |
| `python/tests/test_gui_s2.py` | S2試験も `GSIS_SOLVER` 指定に従い、既定をS3へ変更 |
| `docs/gui.md`・`docs/gui_screenshots/75_*.png`〜`86_*.png` | 操作説明・新画面のライト/ダーク画像 |
| `docs/phase8_g3c_report.md` | 本報告 |

表の `gui/` は `python/src/gsis_tools/gui/` の略。

## 試験

共通環境は以下。pytestは並列化せず、GUIも同時に複数プロセスで起動しない。

```powershell
New-Item -ItemType Directory -Force build/tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"; $env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:GSIS_GUI_3D = 'static'
$env:GSIS_SOLVER = "$PWD\runs\phase8\s3\gsis-solver-s3-final.exe"
$env:OMP_NUM_THREADS = '2'
Remove-Item Env:GSIS_AI_LIVE_TEST -ErrorAction SilentlyContinue
```

- 接続試験の中間確認: `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_s3.py -q -x --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3c_target5 --junitxml=build\tmp_g\pytest_g3c_target5.xml` — **22件通過、失敗0、6.20秒**。
- 初回試行で、試験の壁速度に法線成分が含まれていたためS3が拒否した。接線方向の試験値へ修正した。精度キーの設定フォーム不具合は実装を修正し、パッチ更新後の配列順・SI換算の丸め・CIS履歴の初期行について試験の期待を正した。
- STOP試験追加後の全体初回: `python\.venv\Scripts\python.exe -m pytest python/tests -q --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3c_all --junitxml=build\tmp_g\pytest_g3c_all.xml` — 417件通過・2件SKIP・1件失敗、261.48秒。開始前STOPのGSISは0反復で停止するため履歴に評価行を作らない。これを初期p_bのsummary保存と合わせて検査する期待へ修正した。
- STOP期待修正後: `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_s3.py -q --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3c_complete --junitxml=build\tmp_g\pytest_g3c_complete.xml` — **26件通過、失敗0、5.99秒**。その後、試験・撮影コードの精度コンボ選択を `findText("fp64")` に直し、GUIの実コマンドへの `--precision fp64` を確認する検査も加えた(コンボの項目にuserDataはないため)。ケースのFP64指定自体は以前から保持している。
- 全体の最終確認(精度選択修正後): **418件通過・2件SKIP・失敗0、247.17秒**。`pytest_g3c_final.xml` もtests=420、errors=0、failures=0、skipped=2。PowerShellの `*>` がgmsh/OCC由来の標準エラーを `NativeCommandError` 表示へ変換し、実行シェルの終了コードが1になったため、提出用ログはcmdのリダイレクトで取り直す。

```powershell
python\.venv\Scripts\python.exe -m pytest python/tests -q -rs --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3c_final --junitxml=build\tmp_g\pytest_g3c_final.xml
```

標準出力・標準エラーを `build/tmp_g/pytest_g3c_final.log` へ保存する。新規試験は26件で、CPUのCIS/GSIS/NSF各3反復と開始時STOPを含む。GSISの入口診断は最終補正後fの流量とsummaryの入口流量が一致することを確認する。

提出用の全体確認:

```powershell
cmd /c "python\.venv\Scripts\python.exe -m pytest python/tests -q -rs --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3c_delivery --junitxml=build\tmp_g\pytest_g3c_delivery.xml > build\tmp_g\pytest_g3c_delivery.log 2>&1"
```

**提出用の最終結果: 418件通過・2件SKIP・失敗0、249.19秒、終了コード0。** `build/tmp_g/pytest_g3c_delivery.log` と `pytest_g3c_delivery.xml` に保存した。SKIPは本物のAI APIを指定していない1件と、シンボリックリンクを作れない環境の1件。JUnitはtests=420、errors=0、failures=0、skipped=2。

### 画面写真と文書の検査

```powershell
python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots_g3c --solver runs/phase8/s3/gsis-solver-s3-final.exe
git -c core.safecrlf=false diff --check -- python docs/gui.md docs/phase8_g3c_report.md
```

- 最終撮影は終了コード0、**12枚(75–86)、全て1800×1120**。ログは `build/tmp_g/g3c_screenshots_final.log`、入力・実出力は `build/tmp_g/gsis_g3c_shots_7z937e72/`。CPUの12セル・8³速度点・5反復で、模擬の結果値は使っていない。
- 新境界の入力欄・無効欄・面の選択行、検査のα/目標/初期p_b、圧力グラフ、最終値と誤差警告、NSFの精度注意をライト・ダーク両方で目視確認した。
- テーマは既存の `theme.py`・`qtheme.py` を継続。新たな色ファイルやHEX直書きは追加していない。上部の「表示」でsystem/light/darkを切り替える。
- `docs/gui.md` の画像リンク欠落0、`git diff --check` の空白エラー0。

## 決めたことと理由

- **新しい例題は試験・撮影のときだけ生成する。** `screenshots_g3c.demo_project()` の12セル六面体箱を用いる。S3のTOMLキー・単位・接線指定に合わせた入力で、メニューに精度未検証の例題を追加せず、RAM・CPU負担を小さくする。元の `cases/general/s3/` は読み取り専用のまま。
- **p_bは独立したPa軸にする。** 質量流量や収束ノルムと単位・桁が異なるため。該当列がない場合は従来表示を保つ。
- **画面の誤差は百分率、CSV・内部はS3と同じ比率。** 0.1%の判定と照合を混同しないようヘッダに明記する。
- **p_refは圧力境界がないときの明示値を保持する。** S3がそこから制御を始める契約で、流量だけから初期圧力を決める推定式は未定のため。
- **NSFの注意を入力と結果の両方に残す。** 未保証の精度を値の有効性と区別し、S3のref1dの既定すべり係数の拒否も説明する。
- **全体の保存性警告は目標流量の警告に置き換えない。** 入口が目標に一致しても入口出口の保存性が満たされるとは限らないため。

## 確かめていないこと・既知の問題

- CPUの小さい問題を数反復だけ実行した接続確認であり、流量制御の収束・数値精度を新たに保証しない。精度と圧力回復はS3の報告を参照する。
- GPU実行、大きなCAD・メッシュ、傾斜/曲面壁の精度、非常に小さいα・高Mach、Linux、対話型OpenGLの実マウス操作は未検証。S1の四面体GSISとS3のNSF・補間の精度上の限界を継承する。
- 自動速度格子は入力した境界値から決まり、内部の加速・低温噴流までは保証しない。食い違い時は従来どおりC++を優先する。
- 非Heプリセットの一次資料照合、担当外の設計・進捗・仕様文書の古い記述は修正していない。
- 本物のAI API・認証情報・OS資格情報マネージャーは使用していない。

## 統括役への質問

作業を止める質問・承認待ちはない。新しい例題を製品メニューへ公開する場合は、S3の収束済みケースと保存性の制約を踏まえ、別途選定してほしい。今回の小さい箱はGUI接続の試験用とする。

## 次の担当への引き継ぎ

- 新境界の使用にはS3対応のexeを選ぶ。固定版パスとSHA256は冒頭のとおり。`test_gui_s2.py` も `GSIS_SOLVER` を参照する。
- 結果の入口制御値は `patch_results.mass_flow_inlet_rows()`、警告は `results.summary_warnings()`、履歴列は `History.inlet_pressure_columns()` が窓口。新旧流量の符号は維持する。
- `check-case` の `pressure_Pa` は初期圧力、summaryの同名キーは最終p_b。画面とCSVで取り違えない。
- GUIのp_refは流量から自動推定しない。NSFの警告はすべり係数の追加実装を意味しない。S4のRAM退避・反射キャッシュの扱いは今回変更していない。
- G3bの未コミット変更が残っているため、統括役が合わせてレビュー・commit・pushする。担当外の未追跡ファイルを本担当の成果物に含めない。
- G担当の試験・撮影・ソルバー子プロセスは終了済み。次の確認には `pytest_g3c_delivery.log/.xml` と `g3c_screenshots_final.log` を使う。撮影用入力・出力は一時領域へ残している。
