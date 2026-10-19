# フェーズ8 G2 作業報告

- 担当: G
- 日付: 2026-10-19
- 根拠: `docs/design_general_app.md` §1の段階3・4、§1.1・§2、依頼G2。
- G1報告と引き継ぎを確認して開始した。開始時からあるG1・S側・統括役の変更は保持した。
- C++の編集・ビルド、commit・push、依存パッケージの追加はしていない。MCP engine・既存MCPツールのコードも変更していない。

## やったこと

1. 境界条件の画面を実装した。一覧と対話型3Dでの面の複数選択、パッチの作成・編集・削除、種類ごとの入力欄、即時検査、パッチの色分けに対応した。
2. `diffuse_wall`・`maxwell_wall`・`pressure_inlet`・`pressure_outlet`・`vacuum`・`mass_flow_inlet`・`symmetry`を扱う。壁速度は任意、適応係数は0 < α ≤ 1。対称面は平面・軸方向の法線を検査し、軸が異なる面を同じパッチへまとめる操作を拒否する。
3. 未割り当て面を `default_wall` の拡散壁に入れる。設定に独立した既定壁温度 `wall_temperature` を追加し、境界から基準温度を更新しても壁の物理温度が変わらないようにした。
4. `boundaries.json`にソルバー用の値と、面番号・SIの幾何の指紋を保存する。CAD・AI形状の再採用時とメッシュ用BREPの読み込み時に指紋で照合し、失った面・曖昧な面を通知する。
5. 境界値を割り当て・確定した際、`update_boundary_defaults()`で温度範囲・基準温度・圧力・最大速度を更新し、設定画面へ戻す。
6. 一般形状の四面体メッシュ画面を実装した。全体の最大・最小寸法、パッチの寸法、成長率を指定できる。QThreadから専用のPythonプロセスを起動し、既存engineのCAD・パッチ・gmsh生成・pmesh出力を再利用する。
7. 停止時には生成プロセスを終了し、途中出力を削除する。Windowsのvenvランチャーの子プロセスも `taskkill /T` で終了し、以前のメッシュを保持する。生成中の段階変更・保存・プロジェクト切り替えを止める。
8. `compute_quality()`によるセル数・非直交角・歪み・隣接体積比、パッチ面積とCAD面積との差、実セル数からのVRAM概算を表示する。出力は `mesh/<名前>.pmesh`・`<名前>_quality.md`・`<名前>_quality.json`。
9. 予算内だけメッシュを済にし、`case.toml`を生成する。予算超過では済にせず、実行への遷移も拒否する。未保存のプロジェクトへの自動出力は未保存扱いを保ち、終了時の保存確認が失われないようにした。
10. G1の「済になったことがない後段も要やり直しになる」処理を修正し、新規段階は未着手を保つ。形状画面の見出しを「流体領域 [m]」にした。
11. Ne・Ar・Kr・Xeを指定のBirdのVHS定数へ切り替えた。NSFのモード名は確定した `nsf` を継続する。
12. `docs/gui.md`を更新し、境界・入力検査・メッシュ・VRAM警告の写真44–50を追加した。既存の写真32–38・40–43も更新し、39はG1の記録として保持した。

## 変えたファイル

G1の差分が未コミットの状態なので、以下はG2が変更したファイルだけを記す。

| ファイル | 内容 |
|---|---|
| `python/src/gsis_tools/gui/boundaries.py` (新規) | 境界の入力検査、既定壁、割り当て、指紋照合 |
| `python/src/gsis_tools/gui/general_mesh.py` (新規) | engineの再利用、隔離プロセス、停止、品質・VRAM |
| `python/src/gsis_tools/gui/pages/boundaries_page.py` (新規) | 面選択・パッチ編集の画面 |
| `python/src/gsis_tools/gui/pages/mesh_page.py` | 管専用画面を一般形状の画面へ置換 |
| `python/src/gsis_tools/gui/main_window.py` | 境界・メッシュの接続、状態、保存、再採用の通知 |
| `python/src/gsis_tools/gui/project.py` | 未着手の保持、実行ゲート、GUIキーの除外、欠落メッシュの無効化 |
| `python/src/gsis_tools/gui/general_settings.py` | Birdプリセット、既定壁温度、接線速度の文字列の取り扱い |
| `python/src/gsis_tools/gui/pages/settings_page.py` | 既定壁温度とBird出典の表示 |
| `python/src/gsis_tools/gui/pages/geometry_page.py` | 見出しの日本語化 |
| `python/src/gsis_tools/gui/screenshots.py` | G2の撮影手順 |
| `python/tests/test_gui_boundaries_mesh.py` (新規) | Qtなしの28試験 |
| `python/tests/test_gui_boundaries_mesh_pages.py` (新規) | offscreenの6試験 |
| `python/tests/test_gui_general.py`・`test_gui_general_pages.py` | プリセット・段階・既定壁温度の期待値更新 |
| `python/tests/test_gui_ai_page.py` | AIメッシュの案内文の期待値更新 |
| `docs/gui.md`・`docs/gui_screenshots/`・本報告書 | 使い方・18枚の撮影・報告 |

## 試験

すべて以下の環境で、uvを使わず既存の仮想環境を直接使った。pytestのキャッシュは無効にした。

```powershell
New-Item -ItemType Directory -Force build\tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
Remove-Item Env:GSIS_AI_LIVE_TEST -ErrorAction SilentlyContinue
```

最終の全体試験は **342件通過、2件SKIP、失敗0**、227.97秒、終了コード0。
SKIPは本物のAPIの明示指定がない試験と、Windowsでシンボリックリンクを作れない環境の各1件。
結果は `build/tmp_g/pytest_g2_verified.xml`。既定壁温度・未保存プロジェクトの最終修正も含む。

```powershell
python\.venv\Scripts\python.exe -m pytest python/tests -q -rs -p no:cacheprovider --basetemp=build\tmp_g\pytest_g2_verified --junitxml=build\tmp_g\pytest_g2_verified.xml
```

| 確認 | コマンド・結果 |
|---|---|
| G2のモデル・画面 | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_boundaries_mesh_pages.py python/tests/test_gui_boundaries_mesh.py -q -x -p no:cacheprovider --basetemp=build\tmp_g\pytest_g2_last_fixes` — **34件通過、失敗0**、38.57秒 |
| 既定壁温度とG1画面の回帰 | `python\.venv\Scripts\python.exe -m pytest python/tests/test_gui_boundaries_mesh_pages.py python/tests/test_gui_general.py python/tests/test_gui_general_pages.py -q -x -p no:cacheprovider --basetemp=build\tmp_g\pytest_g2_wall_temperature` — 32件通過、失敗0、70.61秒 (未保存の試験追加前) |
| 全体・中間確認 | `python\.venv\Scripts\python.exe -m pytest python/tests -q -rs -p no:cacheprovider --basetemp=build\tmp_g\pytest_g2_delivery --junitxml=build\tmp_g\pytest_g2_delivery.xml` — 341件通過、2件SKIP、失敗0、207.89秒 (既定壁温度・未保存の最終修正前) |
| 写真 | `python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots` — 終了コード0、18枚生成。ライト・ダーク、選択、無効状態、入力エラー、メッシュ、予算超過を目視確認 |
| 起動 | `python\.venv\Scripts\python.exe -m gsis_tools.gui --offscreen --quit-after 1` — 終了コード0。設定ファイルは `build/tmp_g/g2_smoke.ini` に隔離 |
| 差分 | `git diff --check -- python docs/gui.md docs/phase8_g2_report.md` — 空白エラーなし (autocrlfの注意だけ) |

開発途中には旧プリセット・旧段階表示・旧「G2で対応」の期待値で失敗した。また、Windowsで停止時にvenvの子プロセスが残ることを停止試験で検出した。いずれも修正し、再検査している。

### 箱・オリフィスの一貫試験

`test_boundary_mesh_case_check_case[box/orifice]` は、5種の現行境界を同じケースに割り当て、生成したメッシュ・数値の速度格子・境界を保存し、以下を実行する。

```powershell
build\release\src\gsis-solver.exe check-case <試験プロジェクト>\case.toml
```

両形状で終了コード0。offscreenの箱・オリフィスでも画面操作から同じコマンドまで通すので、計4回のcheck-case接続を確認する。C++のビルドはしていない。

| 形状 (Qtなし) | セル数 | 非直交角の最大 [deg] | 歪みの最大 | 隣接体積比の最大 |
|---|---:|---:|---:|---:|
| 箱 | 690 | 56.69 | 0.8166 | 4.153 |
| 穴のある板 | 1,575 | 59.11 | 1.113 | 7.151 |

上記は `pytest_g2_all` で得た値。全パッチの面積差は試験の6%以内の条件を通る。撮影用の細かいオリフィスは6,409セルで、穴の壁の面積差は約−0.135%、入口・出口は約+0.049%。

新規試験では他に、7種のTOML出力、GUI専用キーの除外、指紋照合での番号変更・欠落・曖昧性・法線反転、無効なα・非有限値・対称面、重複名への改名の拒否、割り当て・編集・削除、既定壁温度の保持、境界由来の設定更新、停止での旧出力保持、予算超過、別名保存・再読込、欠落メッシュの無効化、未保存プロジェクトの保存確認を検査する。

## 決めたことと理由

- **既定壁温度を基準温度から分離:** `wall_temperature`をGUI設定に追加した。`T0`は境界の最高温度へ更新するため、両者が同じ設定だと予算などを再確定しただけで境界の温度が変わる。G1形式でこのキーがなければ、保存されていたT0から補う。ソルバーには境界の `temperature` だけを渡す。
- **指紋:** 面積・重心・外向き法線・面の種類を照合し、相対許容差は基本1e-6。重心は形状の代表寸法に比例する許容差。面の番号だけで引き継がず、一意でない面は知らせて再割り当てを求める。全ての面を失った明示パッチは空のまま残し、見落として確定しないようにした。
- **接線速度:** 現行ソルバーの書式に合わせて `extrapolate` / `zero` の選択とした。任意の速度ベクトルではない。既知の壁速度から最大速度を更新し、外挿される入口速度の最大値を推測して加える処理はしていない。
- **出口圧力:** 現行ソルバーが受け付ける0以上とした。入口圧力・温度・質量流量は正とする。
- **対称面のパッチ:** 面ごとの平面・軸方向の検査に加え、同一パッチは1つの軸に限定した。現行 `symmetry_patch_axis()` と一致させるため。
- **メッシュ成長率:** gmshの既存Distance/Threshold場で `dh/dd = growth - 1` とした。隣接セルの寸法比を厳密に制約するものではない。既存engineの公開操作だけでなめらかな局所細分化を再利用できるため。
- **メッシュの隔離:** Qtのワーカーは専用プロセスの完了を待つ。gmshのグローバル状態をCAD表示と共有せず、C APIの途中でも停止できるようにした。Windowsではランチャーの親だけでなく子も終了する。
- **数値の速度格子:** `case_document()`は自動入力から算出した値でも数値の `n/range` を書き、現行ソルバーでのcheck-caseを可能にした。`auto=true`の出力への切り替えはG3。
- **出力の採用:** 子の作業領域は `mesh/.generating_*`。正常終了時だけ完成ファイルへ置き換える。停止時は一時領域を削除し、旧完成品を保持する。3つの出力全体はトランザクションではない。
- **プリセット:** Heはv1の論文値を保持。Ne・Ar・Kr・Xeは **G. A. Bird, Molecular Gas Dynamics and the Direct Simulation of Gas Flows (1994), 付録A、表A1、273 K**。値は**統括役の提供**で、G側では一次資料を照合していない。画面と `docs/gui.md` にも出典・提供元を明記した。

| 気体 | 分子の質量 [kg] | μ_ref [Pa·s] | ω |
|---|---:|---:|---:|
| Ne | 33.5e-27 | 2.975e-5 | 0.66 |
| Ar | 66.3e-27 | 2.117e-5 | 0.81 |
| Kr | 139.1e-27 | 2.328e-5 | 0.80 |
| Xe | 218.0e-27 | 2.107e-5 | 0.85 |

## 確かめていないこと・既知の問題

- Maxwell壁・質量流量入口・NSF・ソルバー側の自動速度格子の受理・実計算は依頼どおりG3で確認する。G2は画面・検査・保存・TOMLの保持まで。
- VRAMは既存Python式の概算。固定費0.1 GB、予約1 GB、空きVRAMは入力値で実測ではない。S2のJSON出力との一致は未確認。
- **RAMへの退避の選択欄は作っていない。** ソルバー未対応のため。S4で方式と実装が決まった後に接続する。
- 通常の対話型OpenGLでの実マウス操作は未実施。新画面の選択関数・offscreen表示と、既存の実セルpickerの試験を使っている。
- 大規模・複雑CAD、境界層プリズム、生成後の解の収束・物理精度は対象外。gmsh生成上限は既存engineの既定200万セル。品質値と面積差は表示し、pmeshの幾何検査以外の独自の品質閾値で強制拒否はしていない。
- 実行画面・結果画面の一般化、GPU順番待ち、管の例題、境界VTUなどはG3。AIが書き出した既存メッシュを一般画面へ直接採用する操作はなく、採用したAI形状へ境界を付けて生成する。
- Bird表の一次資料との照合、本物のAPI、OSの資格情報マネージャー、Linuxでの動作は本作業では未確認。
- 範囲外の設計・仕様・C++・統括役の文書は変更していない。作業開始時からある `.handover/2026-10-18_2225.md` は旧フェーズの資料なので、今回の依頼とG1報告を優先した。引き継ぎ資料への追記はしていない。

## 統括役への質問

今回の実装を止める質問・承認待ちはない。G3でS2/S3の対応状況とJSONの書式を確認してほしい。接線速度は既存の文字列の選択を継続する前提で、S3の質量流量入口も同じ書式にそろえてほしい。

## 次の担当への引き継ぎ

- `MainWindow.project` / `ctx.project` を継続使用する。`boundaries_page.changed/accepted`と `mesh_page.changed/ready` を `Project.change()/complete()` へ接続済み。
- 境界は `patch/type/値/faces/fingerprints` の配列。自動の壁だけ `automatic=true`。`case_document()`はソルバー用キーの許可リストでGUIのキーを除外する。
- `settings.wall_temperature` はGUIの既定壁温度。`T0/T_min/T_max/p_ref/u_max`の境界由来の更新と混同しないこと。
- メッシュ完成後は `project.mesh` に `path/report/quality_path/cells/品質/memory/options` を保持し、予算内かつケース保存成功時だけ `validated=true`・段階済になる。`blocked_reason('run')` は実セル数で予算を再判定する。
- `general_mesh.generate_mesh(project, options, cancel_event)`はQtなしでも使用可能。GUIは渡すプロジェクトをコピーする。停止試験は旧完成品が残り、子プロセスのログが閉じて一時領域を削除できることまで検査する。
- G3は `case_document()` と `mesh_memory()` をS2のJSONへ照合し、新境界・NSFを受け付けるソルバーで試験を追加する。旧実行画面の出力先・保存状態・GPUロックも一般プロジェクトへ接続する。
- 写真の更新は `python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots`。44/45が境界、46/47がαの検査、48/49がメッシュ、50がVRAM超過。
- 変更は未コミット。G1も未コミットのため、統括役はG1とG2を合わせて差分を確認すること。
