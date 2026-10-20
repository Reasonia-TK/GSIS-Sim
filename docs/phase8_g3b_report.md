# フェーズ8 G3b 作業報告

- 担当: G / 2026-10-20。根拠は依頼G3b、`docs/design_general_app.md`、S2の `docs/build.md` §10。
- 中断前の未コミット差分と `check_case.py`・`test_gui_s2.py` を読み、引き継いで仕上げた。開始時は `main @ ceb9de9`。`.handover/2026-10-18_2225.md` は旧状態のため、今回の指示・実ファイルを優先した。
- 担当外の `docs/design_host_offload.md` は開始時から存在する未追跡ファイル。編集していない。C++・ケース・設計書・仕様書・進捗文書も変更していない。
- 依存追加・C++ビルド・commit・pushはしていない。実GPU計算、本物の `runs/.gpu_lock` の作成・削除は行っていない。
- ソルバーは **`runs/phase8/s2/gsis-solver-s2-final.exe`**。SHA256は `e5cac5ae6a7bfa040c453f54008f8b029a9a7e6349c15de179865cbbf6698f50` と照合した。`build/phase8_s` 内のファイルは使っていない。

## やったこと

1. 設定・境界の編集後に `check-case --json --no-mesh`、完成メッシュの確認後に通常の `check-case --json` をQProcessで非同期実行する。導出量・速度格子・VRAM・パッチの面数と面積・警告・エラーを設定とメッシュ画面に表示する。前の要求を取り消し、古い結果を採用しない。
2. VRAMの確定表示と実行前の判定にはC++の `memory` を使用する。実行前は指定した精度も反映して再検査する。CPU/NSFはGPU予算・順番待ちの対象にしない。概算の予算超過だけでメッシュの品質確認を未完了にはしない。
3. 自動速度格子は `[velocity_grid] auto=true` のみを書き、手動は `n/range` を書く。旧編集窓の読み書きも対応し、autoと数値の併用を検出する。Pythonの `velocity_grid()` の式は維持し、C++との不一致を警告する。
4. `gsis/cis/nsf` の全モードを `run` に統一。NSFの対応待ちを外し、非対応の定期出力・運動論オプションを追加欄も含めて除く。NSFからCISへ戻した際に実行先・精度の選択肢が消える問題も修正した。
5. 「停止」は出力先のSTOP作成で要求する。60秒経過すると確認付きの「強制終了…」が使用可能になる。GUIが作ったSTOPだけを内容の所有トークンで確認して終了後に削除し、他者のSTOPは保持する。結果と実行終了の表示で `stopped_by_user` を「利用者が停止」とする。新しい実行では前の停止状態をリセットし、次の入力検査・GPU待機も停止ボタンで取り消せるようにした。
6. パッチ表とCSVは新しいSIキーを優先し、面積・質量流量・局所熱流量・全エネルギー・力3成分・流量の正方向を表示する。流入正、力は気体からパッチへの作用(開口では運動量輸送)と注記する。旧出力は従来の外向き正の集計に戻す。
7. `conservation_diagnostics.warning` に対して補正後の入口・出口・差[%]を警告し、`last_pbc_correction` のepsilon/rho/u/Tも出す。従来の `mass_balance` の入口出口・断面ばらつきの警告も維持する。残差収束・利用者停止と診断を分けて表示する。
8. 四面体を初めて確認するとCISと `positivity_limiter=true`・`pseudo_time="velocity"`・`pseudo_cfl=5` を採用する。初回適用済みをプロジェクトに保存し、その後のGSISなどの明示選択を保持する。再生成後は形状種別を再検査する。GSIS選択時は設定・入力検査・実行画面に検証中の警告を出す。
9. gmshの3つの初期化箇所の引数を修正し、FLTKのポップアップ抑止が有効になるようにした。設定を読まない既存方針も保持した。
10. `docs/gui.md` を現状に合わせて更新し、再撮影用 `screenshots_g3b.py` を追加した。

## 変えたファイル

中断前の差分を含むG3bの最終変更。以下の他担当ファイルは含めない。

| ファイル | 内容 |
|---|---|
| `python/src/gsis_tools/gui/check_case.py` (新規) | 非同期JSON検査、取消・タイムアウト、表示パネル、速度格子の照合 |
| `gui/main_window.py` | 編集後の検査要求、検査表示・段階への反映、四面体既定、終了時の片付け |
| `gui/general_mesh.py` | C++メモリ結果の優先、四面体の検出と初回既定 |
| `gui/general_settings.py` | 概算の予約1 GiB、NSFのGPU費用0 |
| `gui/project.py`・`gui/casefile.py` | auto出力と読込・往復、NSF、positivity_limiterの型、C++結果による実行判定 |
| `gui/context.py`・`gui/paths.py` | 試験用の `GSIS_SOLVER` 指定。未指定時の既定探索は継続 |
| `gui/project_runs.py`・`gui/solver.py` | 全モードのrun対応表、NSFの引数フィルタ |
| `gui/pages/run_page.py` | 実行直前のJSON検査、GPU待機、STOP・強制終了・所有確認、終了時診断 |
| `gui/pages/settings_page.py`・`gui/pages/mesh_page.py` | 概算と確定値の区別、検査パネル、四面体警告、品質完了と予算の分離 |
| `gui/patch_results.py`・`gui/pages/boundary_results.py` | 新SI列、符号と熱/全エネルギーの区別、旧出力の互換表示 |
| `gui/results.py`・`gui/pages/results_page.py` | NSF結果の判定、利用者停止と保存性警告 |
| `python/src/gsis_tools/gmsh_convert.py`・`gui/meshgen.py`・`python/src/gsis_tools/mcp/engine.py` | gmsh初期化引数の修正 |
| `python/tests/test_gui_s2.py` (新規) | S2の実CPU接続、停止、診断、四面体、FLTKの回帰 |
| `python/tests/test_gui_app.py`・`test_gui_project_runs.py` | run統一、NSF実行、STOPと非同期検査に合わせた試験 |
| `python/tests/test_gui_boundaries_mesh.py`・`test_gui_boundaries_mesh_pages.py`・`test_gui_general_pages.py` | auto契約、C++予算判定への更新、概算の区別と設定確定、固定ソルバー指定の共通化 |
| `gui/screenshots_g3b.py` (新規)・`docs/gui_screenshots/63_*.png`〜`74_*.png` | 小さな箱のCPU出力を使うライト・ダークの撮影 |
| `docs/gui.md`・本報告書 | 使い方・出力契約・検証結果 |

表の `gui/` は `python/src/gsis_tools/gui/` の略。

## 試験

最終版は **392件通過・2件SKIP・失敗0、終了コード0**、242.88秒。S2接続の新規25件と、停止後の次実行の待機取消も含めた全Python試験が通過した。SKIPは本物のAPIを指定していない試験と、Windowsでシンボリックリンクを作れない環境の各1件。

一時領域を最初に作り、各実行の環境を以下に向けた。pytestの並列化はしていない。試験・撮影のソルバーは固定版だけを使用する。

```powershell
New-Item -ItemType Directory -Force build\tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$env:GSIS_SOLVER = "$PWD\runs\phase8\s2\gsis-solver-s2-final.exe"
Remove-Item Env:GSIS_AI_LIVE_TEST -ErrorAction SilentlyContinue
```

| 確認 | コマンドと結果 |
|---|---|
| 中断差分の初回確認 | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_s2.py -q -x -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3b_resume --junitxml=build\tmp_g\pytest_g3b_resume.xml` — 13件通過・1件失敗。positivity_limiterの既知キー未登録を修正 |
| S2の中間確認 | 同じ対象で `--disable-warnings --basetemp=build\tmp_g\pytest_g3b_s2 --junitxml=build\tmp_g\pytest_g3b_s2.xml` — 17件通過・失敗0、5.37秒 |
| 全体の初回確認 | `python\.venv\Scripts\python.exe -m pytest python/tests -q -x --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3b_all_probe --junitxml=build\tmp_g\pytest_g3b_all_probe.xml` — 163件通過・1件SKIP・1件失敗、61.23秒。旧試験がautoを出さない契約を期待していたためS2契約へ更新 |
| 対象を広げた確認 | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_s2.py python/tests/test_gui_boundaries_mesh.py python/tests/test_gui_boundaries_mesh_pages.py -q --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3b_target --junitxml=build\tmp_g\pytest_g3b_target.xml` — **59件通過・失敗0**、48.73秒 |
| 全体の中間確認 | `python\.venv\Scripts\python.exe -m pytest python/tests -q --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3b_all --junitxml=build\tmp_g\pytest_g3b_all.xml` — **392件通過・2件SKIP・失敗0**、254.09秒 |
| 表示調整後の全体確認 | 同じ全体対象に `-rs --basetemp=build\tmp_g\pytest_g3b_final --junitxml=build\tmp_g\pytest_g3b_final.xml` — 391件通過・2件SKIP・1件失敗、246.25秒。旧試験が「減らして」という表示文言を要求していた。概算とC++再検査の区別、概算の超過だけでは設定確定を止めない動作を確認する期待へ更新 |
| 表示試験更新後の全体確認 | 同じ全体対象に `-rs --basetemp=build\tmp_g\pytest_g3b_delivery --junitxml=build\tmp_g\pytest_g3b_delivery.xml` — **392件通過・2件SKIP・失敗0**、242.46秒 |
| 停止状態のリセットを含む最終全体確認 | `python\.venv\Scripts\python.exe -m pytest python/tests -q -rs --disable-warnings -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3b_complete --junitxml=build\tmp_g\pytest_g3b_complete.xml` — **392件通過・2件SKIP・失敗0**、242.88秒、終了コード0 |

最終ログは `build/tmp_g/pytest_g3b_complete.log`、件数と各試験の記録は `build/tmp_g/pytest_g3b_complete.xml`。ログ保存は `cmd /c '<pytestコマンド> > build\tmp_g\pytest_g3b_complete.log 2>&1'` で行い、PowerShellのNativeCommandError形式が混ざらないようにした。

撮影は `python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots_g3b --solver runs/phase8/s2/gsis-solver-s2-final.exe` で終了コード0、**12枚**。最終ログは `build/tmp_g/g3b_screenshots_final.log`、入力と出力は `build/tmp_g/gsis_g3b_shots_fe8z0djr/`。ライト・ダーク両方の入力検査、STOP要求・保存後、パッチ表、保存性警告を目視確認した。73–74の診断値だけは表示例の模擬値であり、画面内にも明記している。テーマは既存の `theme.py/qtheme.py` を継続し、上の「表示」でsystem/light/darkを切り替える。色の直書きは加えていない。

`git diff --check -- python docs/gui.md docs/phase8_g3b_report.md` は空白エラーなし。

新規画面写真は12枚とも1700×1060。`docs/gui.md` の画像リンクの欠落は0件。

新規試験で確認する主な内容:

- He/Ne/Ar/Kr/Xeの自動格子と導出量のC++照合。点数は完全一致、範囲・導出量は相対2e-13以内。
- メッシュ前・後の非同期検査、古い要求の取消、エラーJSON、起動失敗、タイムアウト。
- FP32/FP64のC++費用と概算の区別、予約1 GiB、NSFのGPU費用が全て0。
- 全3モードのCPU停止とVTU/CSV/summary保存、STOPの自動削除、他者のSTOPの保持、強制終了の確認の取消/実行。
- 前の実行を穏やかに停止した後でも、次の検査・GPU待機の停止ボタンを使えること。
- NSFの非対応引数の除外、NSFからCISへの切替でソルバーの引数情報を壊さないこと。
- 新SIパッチの優先、熱と全エネルギーの区別、CSVの符号、旧パッチ集計の継続。
- 保存性警告と既存mass_balanceの併記、補正量だけでは警告にしないこと。
- 四面体の検出・既定保存・再検査後の明示GSIS選択の保持・警告。
- gmsh_session・MCP engine・GUI meshgenの全入口で `General.NoPopup=1` とリポジトリ直下に新規項目ができないこと。

## 決めたことと理由

- **Pythonのメモリは概算として残す。** 予約を1 GiB、NSFの全GPU費用を0に合わせたが、固定費・鏡映・部分和・チャンク丸めをPythonに複製しない。C++との二重管理を避け、確定値は `memory` を表示する。暫定固定費0.1 GBは安全側とは限らないと明記した。
- **空きVRAMは設定の仮定値を渡す。** `--free-vram-bytes` によりJSON検査でGPUを初期化せず、GUIと試験の比較を再現可能にする。確保保証ではない。実行前は指定精度で再検査し、その結果からロック要否を判断する。
- **STOPの猶予は60秒。** 長い反復・出力保存を待つ余地を取り、経過後も自動killせず利用者が選ぶ。GUI終了時も通常の終了操作ではSTOP完了を待ち、終了確認に戻れる。内部shutdownによる強制終了は既存の後片付けとして保持した。
- **STOPの所有はUUID入りの内容で確認する。** 既存ファイルは上書きせず、他者が書き換えたものも削除しない。異常終了で残っても次回は新しい出力先なので影響を避けられる。
- **パッチは新SIキーの存在で判別する。** 旧pbc-runの同名質量キーは外向き正なので、質量キーだけで新契約と判断せず、heat_flow_Wまたはforce_Nを確認する。新旧の流量符号をCSVにも残す。
- **四面体既定は初回のみ適用する。** 自動変更を繰り返してGSIS/NSFの明示選択を打ち消さないため。再メッシュ時は種別を再判定し、適用済みの選択は保持する。六面体へ変わった場合は適用済み印を外すが、過去に利用者が選んだ物理設定は勝手に戻さない。
- **last_pbc_correctionは保存性警告に添える。** 補正量が存在するだけで全GSIS結果を警告色にはしない。元のsummaryは要約タブで確認できる。

## FLTKの原因と対処

3箇所が `gmsh.initialize(["-nopopup"], ...)` だった。gmshのargvにはプログラム名が必要で、この形では先頭がargv[0]となり、`-nopopup` がオプションとして処理されない。`["gsis-tools", "-nopopup"]` へ修正し、`General.NoPopup=1` を実APIで確認した。

両形式を別の初期化で実測した結果は **旧形式0、修正後1**。異常ファイルを旧形式で開くことは避け、比較実験でもリポジトリ直下の新規項目は0だった。記録は `build/tmp_g/gmsh_argv_probe.json`。試験・撮影後の直下一覧も開始時と同じで、文字化けフォルダや新規ファイルは増えていない。

ポップアップが無効なら、CAD読込エラーなどをgmshのFLTK画面で開かず、FLTKの設定保存経路を通らない。`readConfigFiles=False` も維持した。GUI・MCPとも表示はQt側なのでFLTKは不要。Windowsの壊れた保存先パスを環境変数で覆う方式は採らず、FLTKが起動し得る引数の誤りを修正した。既存の文字化けフォルダを削除する処理は追加していない。

## 確かめていないこと・既知の問題

- 四面体GSISの流量精度・保存性はS1の未解決のまま。CISの安定設定を既定にしても任意流れでの精度を保証するものではない。
- GPU実計算、大規模な再計算・収束、WDDM/TDR、実際のVRAM確保量、Linuxと対話型OpenGLの実マウス操作は未確認。CPUの小規模問題・Qt offscreen/staticを使用した。
- 自動格子は入力した境界値による決定であり、内部の加速・低温化まで精度を保証しない。PythonのTmin/Tmax/uの入力と境界の実入力がずれる場合は警告し、C++を優先する。
- Maxwell壁・質量流量入口はG3c。既存フォーム・保存機構は残し、S3未対応のソルバーへ接続していない。
- 本物のAI API・OSの資格情報マネージャーは使っていない。全体試験のAPIは模擬で、live指定を外した。
- 担当外文書の進捗・仕様・設計の古い説明は編集していない。`docs/gui.md` と本報告書がG3bの実装状況。

## 統括役への質問

作業を止める質問・承認待ちはない。S1の数値精度問題と、S3後の境界接続は別作業として継続してほしい。

## 次の担当への引き継ぎ

- S3の新境界を接続する際は `check_case.grid_warnings()`、C++のauto境界値、パッチの入口出口分類・符号を再照合する。S2固定版の試験をS3の明示した固定版へ切り替える。
- `CheckCaseJob` は入力の一時的な写しを使う。プロジェクト切替・編集では取消が必要。実行前の独立した検査は `RunPage.check_job`。実行中・検査中・待機中の判定は `busy` を使う。
- C++の入力検査結果はメモリ上の `project._solver_check`。設定変更時に捨て、プロジェクトファイルには保存しない。`mesh.contains_tetrahedra/tet_defaults_applied` は保存する。
- 試験のソルバー指定は `GSIS_SOLVER` または `AppContext.set_solver_path(..., save=False)`。ロック試験は `RunPage.gpu_lock_path` を一時領域へ向ける。本物のロックは片付けに使わない。
- パッチ集計の窓口は `patch_results.patch_rows()`、診断は `results.summary_warnings()`。新SI項目の追加と表示・CSVの符号を同時に確認する。
- 撮影は `python -m gsis_tools.gui.screenshots_g3b --solver runs/phase8/s2/gsis-solver-s2-final.exe`。入力・出力は `build/tmp_g` 以下へ残す。保存性警告だけは模擬診断値の表示例で、写真内にも明記する。
- 統括役が本差分をレビューしてcommit・pushする。担当外の未追跡文書は本作業の変更に含めない。
