# フェーズ8 G1 作業報告

- 担当: G
- 日付: 2026-10-19
- 根拠: `docs/design_general_app.md` 版0.2の§1・§1.1・§2・§3
- 対象: プロジェクト、6段階の骨組み、形状、設定。C++のビルド・commit・push・依存の追加はしていない。

## やったこと

1. `project.toml` 版1の読書き、新規・開く・保存・別名保存・最近使ったプロジェクト、計算名、未保存の変更の確認を実装した。別名保存はCAD・境界・メッシュ・実行履歴を複製し、相対パスを保つ。保存形式は `docs/gui.md` §2.1。
2. Qtなしの `project.case_document()` が `[case]`・気体・基準量・速度格子・GSIS・運動論・PBC・境界条件のTOMLを生成する。Maxwell壁・質量流量入口の追加値を保持し、面の対応などGUI専用キーは除く。G1の新規ケースは境界とメッシュが未完了のため、まだ実行用ではない。
3. 左の6段階に未着手・済・要やり直しを表示した。前段未完了の項目は無効で、ツールチップとプログラムからの遷移時の状態欄に理由を出す。上流を変えると後段を要やり直しにし、メッシュの検証済みの印を外す。G2の境界・メッシュは仮画面、実行・結果は既存部品。
4. STEP・IGES・BREPの読み込み、単位、内部流／外部流、箱の倍率、立体数・体積・小さい面と辺の検査、面の一覧と3D表示を実装した。CADの処理は既存のMCP engineを再利用し、既存MCPツールのコードと振る舞いは変えていない。
5. 既存AI画面を形状のタブへ移し、AI形状のBREP・手順・伏せ字済み会話を採用できるようにした。採用したBREPを通常CADと同じ検査に通す。AIが取り込んだCADの手順用キャッシュもプロジェクトへ複製する。採用はスナップショットで、後からのAI編集は再採用したときに反映する。
6. GSIS-PBC／CIS／NSF、He・Ne・Ar・Kr・Xe・任意物性、基準量、GSISと折りたたみの上級パラメータ、自動／手動速度格子、Kn・平均自由行程・VRAMの即時計算を実装した。`L_ref` の自動値は流体領域の外接箱の最小辺。G2から境界の既定値を渡せる関数を設けた。
7. 新規のQtなし・offscreen試験を追加し、作り直した旧画面の6試験を置き換えた。流用した実行・結果・AIの試験を保持した。`docs/gui.md`を更新し、画面写真32–43の12枚を作成した。

## 変えたファイル

| 種別 | ファイル |
|---|---|
| 新規のQtなしモデル | `python/src/gsis_tools/gui/project.py`、`general_settings.py`、`geometry.py` |
| 新規の画面 | `python/src/gsis_tools/gui/pages/geometry_page.py`、`settings_page.py` |
| 起動・構成 | `python/src/gsis_tools/gui/app.py`、`main_window.py` |
| AIの形状の受渡し | `python/src/gsis_tools/gui/ai/backend.py`、`pages/ai_page.py` |
| 撮影 | `python/src/gsis_tools/gui/screenshots.py` |
| 新規試験 | `python/tests/test_gui_general.py`、`test_gui_general_pages.py` |
| 既存試験の更新 | `python/tests/test_gui_app.py`、`test_gui_ai_page.py` |
| 文書 | `docs/gui.md`、本報告書 |
| 画像 | `docs/gui_screenshots/32_geometry_cad.png`～`43_dark_geometry_ai_tab.png`(12枚。01–31は保持) |

作業開始時から統括役の文書変更があり、作業中にはS側の差分も見えた。それら、`cases/`、C++、仕様・設計書は編集していない。

## 試験

環境はすべて次を指定した。依頼の例外指示に従い、uvとそのキャッシュは使っていない。

```powershell
New-Item -ItemType Directory -Force build\tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
```

| 確認 | コマンドと結果 |
|---|---|
| 新規のQtなし・画面 | `python\.venv\Scripts\python.exe -m pytest python\tests\test_gui_general.py python\tests\test_gui_general_pages.py -q -p no:cacheprovider --basetemp=build\tmp_g\pytest_review` — **27件通過、失敗0**(24件+3件)、9.20秒 |
| Python全体(最終) | 下記コマンド — **308件通過、2件SKIP、失敗0**、128.96秒、終了コード0。SKIPは本物のAPIとシンボリックリンクの権限の各1件。結果は `build/tmp_g/pytest_delivery.xml` |
| 起動 | `python\.venv\Scripts\python.exe -m gsis_tools.gui --offscreen --quit-after 1` — 終了コード0 |
| 画面写真 | `python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots` — 終了コード0、**12枚生成**。形状の縁の配色調整後は `--only 32_,33_,40_,41_` で4枚を再生成 |
| 差分 | `git diff --check -- python docs/gui.md docs/phase8_g1_report.md` — 空白エラーなし(autocrlfの注意だけ) |

最終の全体試験コマンド:

```powershell
Remove-Item Env:GSIS_AI_LIVE_TEST -ErrorAction SilentlyContinue
python\.venv\Scripts\python.exe -m pytest python\tests -q -rs -p no:cacheprovider --basetemp=build\tmp_g\pytest_delivery --junitxml=build\tmp_g\pytest_delivery.xml
```

新規試験では以下を確認した。

- 自動速度格子の物理的な範囲・刻みの不等式と偶数点数、手動値、境界由来の既定値、無効な物性入力。
- Heの固定値、他気体の近似がNIST表の300・400 Kを通ること、Knと平均自由行程。
- FP32分布・4本の作業領域・チャンク縮小・予算超過・NSFでの分布割当てなし。
- 6段階の順序と無効化、保存・再読込・別名保存、未来の版・範囲外パス・入れ子や空でない別名保存先の拒否、欠落した領域の無効化。
- STEP・IGES・BREPの箱の体積・面積・法線・単位、外部流の体積と内側法線、複数立体の拒否、小さい面・辺の警告、破損CADの入力エラー化。
- offscreenでCADを読み、設定を確定し、仮の境界画面へ進み、保存・別名保存・再読込。未確定の外部流入力も保存して再検査できること。
- 既存AIの模擬試験の中で、管の形状を共通検査へ取り込み、手順・会話を保存してもダミーのAPIキーが含まれないこと。

ライト・ダークの形状、入力、無効状態、面の選択、上級と手動格子、VRAM警告、AIタブの写真を確認した。
テーマは既存の `theme.py`・`qtheme.py` を流用し、上の表示欄の system / light / dark で切り替える。色の直書きは追加していない。
APIキーの処理は従来の仕組みを流用し、`docs/gui.md` §2.3.5が元の文面と完全に同じことも確認した。

## 決めたことと理由

### 保存と段階

- 保存版は整数1、形式名は `gsis-project`。JSONの境界対応とソルバー用TOMLを分け、将来の移行を明示できるようにした。
- TOMLにnullはないため、判定できない法線はキーを省く。GUIは「判定できません」と表示する。
- 別名保存は空の独立したフォルダに限定した。既存ファイルの上書きと入れ子の再帰コピーを避けるため。
- 段階変更時は古いメッシュのファイル自体を削除せず、状態と検証済み印を無効にする。再検査を強制しつつ以前の出力を保つため。
- CADは内容のハッシュ付きの名前でコピーし、領域BREPは内容と設定のハッシュで分ける。読み込み失敗時に前回の領域を上書きしないため。
- 旧 `--case` は独立した編集窓で保持した。管のプロジェクトへの変換はG3に任せる。`--results` で既存出力を明示して開くときだけ、独立したビューアとして結果を直接表示する。

### CADと単位

- STEP・IGESの単位メタデータはOCCに任せ、選んだ座標単位へ変換する。BREPは選んだ単位と解釈する。表示・検査・保存する面の幾何はSI、領域BREPは `geometry.unit` の座標。
- IGESの閉じた面には `healShapes` の縫合と立体化だけを使い、小さい面・辺の修復・削除は無効にした。[gmshのAPI](https://gmsh.info/doc/texinfo/#gmsh_002fmodel_002focc_002fhealShapes)に従う。
- この環境のOCCは最初のIGES読み込みで `OCCTargetUnit=MM` を拒むことがあった。GUIだけで既定のmm読み込み→単位変換→BREP→既存engineという経路にした。新しいPythonプロセスでIGESを最初に読めることも別に確認した。
- 小さい面は全表面積の1e-6未満、短い辺は対角長の1e-5未満を警告とした。形状を壊さず注意を出すための相対的な閾値。

### 気体の出典

Heは指定どおりv1の値を固定した。他の気体は、一次資料で確認できた[NIST、Huber・Harvey「Viscosity of Gases」](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=907539)の低密度の300・400 Kの値から、局所的なべき乗モデルを作った。
`T_ref=300 K`、`μ_ref=μ(300 K)`、`ω=log(μ400/μ300)/log(400/300)`。
これは**今回選んだ近似**であり、BirdのVHS表のωを採用したものではない。

| 気体 | μ300 [μPa·s] | μ400 [μPa·s] | 原子量(質量計算用) |
|---|---:|---:|---:|
| Ne | 31.9 | 38.6 | 20.180 |
| Ar | 22.7 | 28.6 | 39.95 |
| Kr | 25.5 | 32.9 | 83.798 |
| Xe | 23.2 | 30.5 | 131.29 |

原子量は[CIAAWの簡略原子量表](https://www.ciaaw.org/abridged-atomic-weights.htm)。分子質量は原子量×1e-3 / 6.02214076e23 kgとした。
画面にも300–400 Kの局所近似と表示した。広い温度域への使用は任意物性で適切な値を指定する。

### 見積もりとソルバー用の書式

- 速度点はソルバーの中点則に合わせ、幅/nを刻みとする。自動値は物理範囲と最小温度の刻みから偶数に切り上げ、`sqrt(R*T0)` 単位でTOMLに書く。
- VRAM固定費は暫定0.1 GB、予約は設計どおり1 GB。作業領域はセル数×チャンク幅×16 Bで、自動幅は予算に収まる最大値。G1では未生成のセル数と空きVRAMを入力値で仮定する。
- NSFのプロジェクトと生成ケースのモード値は暫定 `nsf` とした。S2の正式なモード名とG3で照合する。G1の実行画面へは境界とメッシュが済むまで渡さない。

## 確かめていないこと・既知の問題

- S2の `check-case --json` との速度格子・VRAMの一致、新境界条件・NSFのソルバー受理、汎用プロジェクトの実行はG3で未確認。
- 境界条件の割当て・一般メッシュの生成・実メッシュでのVRAM超過の禁止はG2。GPU順番待ち・一般のパッチ別結果・管の例題はG3。
- 本物のAPI、OSの資格情報マネージャー、通常の対話型OpenGLでのマウス操作は本作業では実行していない。既存の模擬API・static描画・セルpickerの試験は通した。
- 保存したAI会話・手順はプロジェクトを開いたとき自動でAIタブに読み戻さない。流体領域は保存したCADから再表示する。AI側の作業フォルダの設定は従来どおり独立。
- 別名保存は実行履歴も含めてコピーするので、大きいプロジェクトでは時間・容量がかかる。コピー失敗時は元のプロジェクトを保持するが、コピー先に途中のファイルが残ることはある。
- 保存は各ファイルを一時名から置き換えるが、3ファイル全体のトランザクションではない。
- CADの取り込みは別スレッド。プロジェクトを開いたときのCADの再表示は同期処理で、大きいCADでは待つことがある。
- 手動速度格子は値と範囲を検査するが、境界のMaxwell分布を十分に解像するかの最終判断はG3のソルバー検査が必要。

## 統括役への質問

1. NSFの正式な `case.mode` はS2で何にするか。G1は仮に `nsf` としたので、G3で確定した表記へ合わせてほしい。
2. Ne・Ar・Kr・Xeは出典を追えるNISTの300–400 K近似にした。より広い温度域を既定で対象にする場合は、BirdのVHS表など採用する一組の物性を統括役で指定してほしい。現在は近似範囲を表示して任意入力でも使える。

## 次の担当への引き継ぎ

- G2は `MainWindow.project`(同じものを `ctx.project` にも保持)と `Project.change()/complete()/blocked_reason()` を使用する。画面の変更時に状態を無効にし、正常終了時だけ済にする。
- 受理した領域は `project.asset(project.geometry['domain'])` のBREP、座標単位は `project.geometry['unit']`。`geometry.faces` はSIの番号・面積・重心・法線。G2はこれを面の対応の照合に使う。
- `boundaries.json` の配列へソルバー用の境界入力と `faces/fingerprints` を保持する。`GeneralSettings.update_boundary_defaults()` で温度・圧力・速度の既定を更新し、設定フォームを `load_settings()` で更新できる。
- `geometry_page.ready` が `GeometryResult` を返す。AIとCADの両方がこの経路を通る。G2は境界画面・メッシュ画面を仮画面から差し替える。
- メッシュ生成後は `project.mesh['path']`・`validated`・設定の実セル数を更新し、`estimate_vram()` で実行へのゲートを作る。G3でS2のJSONと比較するまで概算表示を保つ。
- ケース生成の窓口は `case_document(project)` と `save_project(project)`。既存の `casefile.py` は管用の既存読書きを保持しており、新規プロジェクトの形式を無理に通していない。
- 試験はネットワークを使わずに全体実行できる。APIのライブ試験は既定でスキップする。
