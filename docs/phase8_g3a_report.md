# フェーズ8 G3a 作業報告

- 担当: G
- 日付: 2026-10-19
- 根拠: 依頼G3a、`docs/design_general_app.md` §1の段階5・6と§1.1。
- `CLAUDE.md`、設計書、G1・G2報告、開始時の引き継ぎを確認した。旧引き継ぎより今回の依頼とG1・G2の実装を優先した。
- 照合時点は `main @ 5855018`。旧引き継ぎの `770d32a` から進んでおり、GPUロックも存在した。旧資料の「作業なし・ロックなし」を現在の状態とは扱っていない。
- 編集は担当範囲内。C++のビルド・編集、依存の追加、commit・pushはしていない。`cases/`と既存の計算結果は読み取りだけ。
- GPU計算は起動していない。本物の `runs/.gpu_lock` は作成・削除していない。ロックの試験・撮影には全て `build/tmp_g/` 以下を使った。

## やったこと

1. プロジェクトのモードから `gsis → run`、`cis → cis-run` を選択する実行画面へ接続した。NSFの対応表は `nsf → pbc-run` とし、G3aでは「ソルバーの対応待ち」で止める。
2. 実行ごとに `<project>/runs/YYYY-MM-DD_HHMMSS/` を作り、入力の写し `case.toml`、例題識別・日時・実行コマンドを持つ `gui_run.json` を保存する。同じ秒の実行は `_2` 以降で区別する。出力先を `--out` で指定し、過去の結果は上書きしない。
3. 2 GB以上のGPU計算に、リポジトリのロックの取得・1秒ごとの再試行・待機表示・取消・自動開始を追加した。実行先と精度を見積もりに反映し、CPUでは待機しない。待機・実行中の入力変更・保存・プロジェクト切替を止める。
4. 履歴の質量流量は入口・出口の種類によってパッチ別に表示する。`mass_flow_inlet`も入口として分類する。管の例題ではp_refで作ったケースでも論文の無次元質量流量へ変換する。
5. 結果の一覧をプロジェクトの `runs/` に接続した。入力が要やり直しでも過去の出力を開ける。結果を正常に読み込んだ後に段階を済にする。
6. 中心線・Fig.3・Table 1・論文の参照線・管用の視点と線プリセットを管の例題だけに限定した。一般形状では領域の外接箱から断面・線・流線の種を決め、無次元量のラベルと線上分布のCSVに `p_ref`・`rho_ref` を使う。
7. パッチごとの質量流量・熱流量・力の表とCSVを追加した。summaryの既存のSI値・無次元値と基準量、境界VTUの面積積分を使う。欠測は「ソルバーの出力にない」と表示する。
8. `boundary.vtu`の壁の全パッチ・境界の全パッチ・個別パッチを選んで、量とベクトル成分をSIで色付けする画面を追加した。法線応力・せん断応力・全エネルギー流束・境界温度など、実際にある量を選択肢に出す。
9. 「プロジェクト → 例題を開く…」でδ=1・10・100・1000からプロジェクトを作る。元のGSIS設定と六面体メッシュを複製し、「既存メッシュから」で形状からメッシュまで済とする。境界の値を変えた後も、既存メッシュを再確認して使える。
10. 浮動小数点の入力欄を有効数字6桁程度にした。未編集なら元の精度を保持する。設定画面の速度範囲の表示も短くし、座標の入力への不要な丸めを除いた。
11. 例題で使う現行の上級キー `gsis_negative_check`・`init_advection`・`hot_interpolation`・`gsis_freeze_kn` をGUIへ登録した。元のGSISの追加設定も保持する。
12. `docs/gui.md`を更新し、画面写真51–62の12枚を追加した。ライト・ダークの実行・待機・管の結果・壁面分布・汎用CPU計算・汎用結果を確認した。

## 変えたファイル

G1・G2と他担当の未コミット差分がある。以下はG3aが追加・変更したもの。

| ファイル | 内容 |
|---|---|
| `python/src/gsis_tools/gui/project_runs.py` (新規) | 実行入力の固定・実行コマンド記録・ソルバーのリポジトリ探索・所有確認付きGPUロック |
| `python/src/gsis_tools/gui/examples.py` (新規) | 管4条件の複製、既存メッシュのパッチを選択単位にした形状表示 |
| `python/src/gsis_tools/gui/patch_results.py` (新規) | SIのパッチ集計とCSV |
| `python/src/gsis_tools/gui/pages/boundary_results.py` (新規) | パッチ表・壁面の3D分布 |
| `python/src/gsis_tools/gui/screenshots_g3a.py` (新規) | GPUを使わない12枚の再撮影手順 |
| `python/src/gsis_tools/gui/main_window.py` | 例題メニュー・実行中の操作制限・履歴と段階・結果への接続 |
| `python/src/gsis_tools/gui/pages/run_page.py` | プロジェクト実行・待機と取消・終了時の解除・数値と履歴の接続 |
| `python/src/gsis_tools/gui/pages/results_page.py` | プロジェクトの一覧・例題限定タブ・境界結果・読込完了の通知 |
| `python/src/gsis_tools/gui/results.py`・`fields.py` | 実行時入力を優先した読込・例題識別・境界の量と集計・管だけの中心線生成 |
| `python/src/gsis_tools/gui/pages/results_3d.py`・`line_profile.py` | 一般形状の既定と管専用操作の制限 |
| `python/src/gsis_tools/gui/pages/boundaries_page.py`・`mesh_page.py` | 既存メッシュの境界値編集・固定パッチ・再確認 |
| `python/src/gsis_tools/gui/general_settings.py`・`project.py` | GSISの追加設定の保持 |
| `python/src/gsis_tools/gui/casefile.py` | 現行の例題で使う上級キーの登録 |
| `python/src/gsis_tools/gui/history.py` | 質量流量入口の分類 |
| `python/src/gsis_tools/gui/widgets/common.py`・`pages/settings_page.py` | 数値表示と元の精度の保持 |
| `python/tests/test_gui_project_runs.py` (新規) | モデル・画面・CPU実行・例題・ロックの24試験 |
| `python/tests/test_gui_app.py` | 管の合成データに例題識別を追加し、既存の管の結果試験を継続 |
| `python/tests/test_gui_fields.py` | 一般の箱の無次元圧力表示を `p / p_ref` に合わせた期待値更新 |
| `docs/gui.md`・`docs/gui_screenshots/51_*.png`～`62_*.png`・本報告書 | 使い方・写真・報告 |

## 試験

uvを使わず既存の仮想環境を直接起動した。共通環境は次のとおり。

```powershell
New-Item -ItemType Directory -Force build\tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
Remove-Item Env:GSIS_AI_LIVE_TEST -ErrorAction SilentlyContinue
```

最終の全体試験は **366件通過、2件SKIP、失敗0**、311.78秒、終了コード0。
SKIPは、明示指定していない本物のAPIの試験と、Windowsでシンボリックリンクを作れない環境の各1件。
結果は `build/tmp_g/pytest_g3a_complete.xml`。

| 確認 | コマンド・結果 |
|---|---|
| Python全体・最終 | `python\.venv\Scripts\python.exe -m pytest python/tests -q -rs -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3a_complete --junitxml=build\tmp_g\pytest_g3a_complete.xml` — **366件通過、2件SKIP、失敗0**、311.78秒 |
| G3aの24試験 | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_project_runs.py -q -x -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3a_latest --junitxml=build\tmp_g\pytest_g3a_latest.xml` — **24件通過、失敗0**、28.53秒 |
| 最終の線上分布・CSVとG3a | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_project_runs.py python/tests/test_gui_fields.py -q -x -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3a_profile_labels --junitxml=build\tmp_g\pytest_g3a_profile_labels.xml` — **30件通過、失敗0**、25.64秒 |
| 既存の実行・結果・G1の回帰 | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_app.py python/tests/test_gui_general.py python/tests/test_gui_general_pages.py -q -x -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3a_regression` — **37件通過、失敗0**、38.22秒 |
| 全体の中間確認 | `python\.venv\Scripts\python.exe -m pytest python/tests -q -rs -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3a_all --junitxml=build\tmp_g\pytest_g3a_all.xml` — **363件通過、2件SKIP、失敗0**、408.44秒 |
| 追加試験を含む全体確認 | 同じ全体コマンドで `--basetemp=build\tmp_g\pytest_g3a_delivery --junitxml=build\tmp_g\pytest_g3a_delivery.xml` — **366件通過、2件SKIP、失敗0**、309.23秒 |
| 一般の圧力表記変更後の中間確認 | 同じ全体コマンドで `--basetemp=build\tmp_g\pytest_g3a_final --junitxml=build\tmp_g\pytest_g3a_final.xml` — **365件通過、2件SKIP、1件失敗**、327.82秒。箱の断面の表示を `p / p_ref` に直した後、旧試験だけが `p / p_in` を期待していたため更新した。最終全体試験で通過を確認済み |
| 写真 | `python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots_g3a` — 終了コード0、**12枚**。模擬ロックとCPU5反復だけで撮影 |
| 起動 | `GSIS_GUI_SETTINGS=build/tmp_g/g3a_smoke.ini` を指定して `python\.venv\Scripts\python.exe -m gsis_tools.gui --offscreen --quit-after 1` — 終了コード0 |
| 差分 | `git diff --check -- python docs/gui.md docs/phase8_g3a_report.md` — 空白エラーなし。既存のautocrlfの注意だけ |

新規試験は、ロックの所有者判定・他者や不明な所有者の保持・owner書込失敗時の解除・待機の取消と自動再開・正常終了/失敗/起動失敗/停止/GUI終了での解除を検査する。プロセスの終了経路にはCPUのPython子プロセスを使い、GPUを起動しない。

ソルバーとの接続は、現行 `build/release/src/gsis-solver.exe` を使う。管の4条件は生成したプロジェクトの `check-case <case.toml>` が終了コード0。汎用の小さな箱はCIS・GSISをそれぞれ `--backend cpu --max-iter 3` で実行し、summary・history・VTU・境界表・CSVまで確認する。既存の `runs/kinetic/tube_delta1_cis` は結果の画面で読み、中心線・管専用タブ・壁面表示を確認した。

## 決めたことと理由

### GPUロックと異常終了

- 閾値は十進の2 GB、**2,000,000,000バイト**とした。見積もりはG2の `mesh_memory()` を使う。FP64の指定は安全側に合計を2倍として扱う。厳密なC++との一致はG3bで行う。
- ロック先はソルバーの祖先にある `CLAUDE.md` と `CMakeLists.txt` を持つリポジトリ。特定できない単独配置のソルバーでは、アプリのリポジトリへ戻す。外部プロジェクトに専用ロックを作ることはしない。
- `owner.txt` はJSONで、`application=gsis-gui`・UUIDの所有トークン・ホスト・GUIのPID・計算名と出力先・開始日時・起動後のソルバーPIDを持つ。先行担当の自由形式のownerもそのまま表示する。所有者の更新には一時ファイルからの置換を使う。
- **この実行が取得したロックで、所有トークンが一致するときだけ解除する。** 所有情報が変わったり別ファイルが増えたりした場合は、他者のロックを消さないよう保持する。
- **異常終了後は自動回収しない。** 再起動したGUIが同じホスト・アプリ・PIDを持っていても新しい所有者とする。PID再利用と、GUI終了後にソルバーだけが生存する場合を避けるため。ownerの内容と計算の終了を人が確認してから手動解除する。強制解除ボタンは設けていない。
- 待機取消後は入力の写しを残す。history・summaryがないフォルダは結果一覧に出さない。順番待ちに厳密なFIFO順序は設けず、ロックを作れた計算が開始する。

### 入力と例題

- 実行時に入力を作り直して固定し、メッシュを絶対パスで参照する。GUIの後続の編集で過去の入力が書き換わらないため。CLI引数は `gui_run.json` の `command` に保存する。実行画面の反復上限は既定で無効にし、例題を含めケースの設定を使う。
- 例題は `cases/tube_vacuum/gsis/tube_delta*_gsis.toml` を使い、メッシュを参照ではなく複製する。元ファイルやその場所に依存せず、新しいプロジェクトとして扱うため。
- CADがない例題では、メッシュの各パッチ全体を1つの選択単位とする。境界の種類と値は編集可能、パッチ名と割当ては固定。設定変更後は「既存メッシュを確認して使う」で再検査する。
- GSISの元の `max_iterations`・`filter_length` などは `settings.gsis_extra` に保持する。既存の物理設定を例題化の際に落とさないため。
- 管の識別には実行フォルダの `gui_run.json` を優先し、一般プロジェクトなら管に似た名前でも論文比較を出さない。古い出力は元ケースのメッシュの識別を使う。

### パッチ集計

- 質量・熱は領域から外向きを正とする。履歴の入口だけは既存の慣用に従い流入を正として表示する。
- 質量はsummaryの `mdot_kg_s` を優先し、なければ無次元値と基準量か境界の面積積分を使う。
- 壁の力は運動量流束のパッチ積分をSIへ直す。開口の運動量流束には移流輸送が含まれるので、壁の力として表示しない。
- 熱は出力に `heat_flux_normal` があれば積分する。現行出力の `energy_flux` は全エネルギー流束であり、そのまま熱としない。欠測を0として扱わず、表とCSVに明記する。
- VTUの `patch_id` はメッシュ順。summaryにパッチがなければメッシュのヘッダから名前を得る。TOMLの境界の記述順で割り当てることは避ける。

## 確かめていないこと・既知の問題

- 依頼どおり、`check-case --json`との速度格子・VRAMの突き合わせ、`maxwell_wall`・`mass_flow_inlet`・`mode="nsf"`・`velocity_grid.auto` の実ソルバー受理と計算は行っていない。G3bの担当範囲。
- GPUの実計算、大規模な管の再計算・収束・物理精度、実際のGUI異常終了後のソルバー生存は未実施。ロックの状態機械とCPUプロセスの終了経路を試験した。
- `STOP` による穏やかな停止とS2の新しいsummaryの熱流量・力のキーは未接続。停止は既存の強制終了で、VTU・summaryがない場合は残る履歴だけを表示する。
- 熱流量は今回読んだ既存出力では欠測となる。壁面には全エネルギー流束をその名前で表示できるが、熱流束と呼び替えていない。
- 実行入力はメッシュの絶対パスを持つ。プロジェクトを移動・別名保存した後も既存結果のsummary・VTUは読めるが、過去の入力だけをCLIで再実行する場合はメッシュのパスの確認が必要。GUIからの新規実行は現在のメッシュを参照する。
- メッシュとVTUの重い読込・断面処理などは既存のワーカーを流用する。例題の初期化時のメッシュ読込と境界の色付けは同期処理で、大きな既存メッシュでは短い待ちがある。
- 通常の対話型OpenGLでの実マウス操作、Linux、本物のAPI、OSの資格情報マネージャーは未確認。画面はQt offscreenとstatic描画で確認した。
- テーマは既存の `theme.py`・`qtheme.py` を継続し、上の表示欄でsystem/light/darkを切り替える。新しい色の直書きは追加していない。

## 統括役への質問

作業を止める質問・承認待ちはない。G3bでS2・S3の出力形式を照合し、熱流量・力のSIキーと符号、STOPによる停止、NSFを接続してほしい。現行の `energy_flux` を熱流量と同一視しない方針を採った。

## 次の担当への引き継ぎ

- G3bでは数値の `n/range` 出力を継続している `case_document()` と `mesh_memory()` をS2のJSONに突き合わせる。`auto=true`への切替、新境界の実計算、NSF実行の禁止解除はこの確認後に行う。
- 実行は `RunPage.bind_project()`、入力固定は `project_runs.prepare_run()`、実行コマンドの保存は `record_command()`。`RunPage.busy` は待機中も真になる。プロジェクト切替・保存・終了時は `proc` の有無だけで判定しないこと。
- ロックの状態は `GpuLock` が持つ。試験では必ず `RunPage.gpu_lock_path` を一時フォルダへ差し替える。本物のロックを試験の片付けに使わないこと。
- 結果は `ResultBundle.patch_totals`、集計の窓口は `patch_results.patch_rows()`。S2のsummaryキーへ対応を足す場所はここ。壁面の選択・色付けは `BoundaryResultsTab`。
- 線上分布のCSVは `write_profile_csv(..., inlet_reference=False)` で一般の `p_ref/rho_ref` 表記になる。GUIは例題識別からこの引数を渡す。既存Python APIの既定の入口表記は保持した。
- 管の例題は `examples.create_tube_project()`。`geometry.source_kind="existing_mesh"` と `geometry.example="tube_vacuum"` を持つ。`gsis_extra`も保存される。
- 新規試験は `python/tests/test_gui_project_runs.py`。写真51–62は `python -m gsis_tools.gui.screenshots_g3a`。待機撮影もCPUの実計算も一時領域内で完結する。
- G1・G2と今回の変更は未コミット。統括役は従来差分を含めて確認し、commit・pushを行うこと。範囲外の文書・C++・ケースへ変更は加えていない。
