# GUI(gsis-gui、汎用アプリ G3c)

- 版: 0.7 / 2026-10-20。G3c: Maxwell 壁・質量流量入口、初期圧力の検査、入口圧力の履歴と最終流量誤差を接続。G3b の S2 検査・STOP・SI 出力・保存性診断も継続。
- 2026-10-18: 画面「モデリング(AI)」を足した(§2.3。自然言語の指示で、AI が gmsh の MCP ツールで計算領域を作り、パッチを付け、`.pmesh` まで書き出す)
- 2026-10-18(追加): Anthropic 形式の既定のモデルを `claude-opus-5-5` にした(ユーザーの決定)。OpenAI 形式に Responses API を足した(Chat Completions API と選べる。§2.3.4・§2.3.8)。画面の画像 01〜31 を、5 つの画面の今の GUI で撮り直した(§5)
- 仕様: `docs/spec.md` §4.1・§4.6・§6
- 置き場所: `python/src/gsis_tools/gui/`(サブパッケージ `gsis_tools.gui`)、試験は `python/tests/test_gui_*.py`、画面の画像は `docs/gui_screenshots/`

計算を **形状 → 設定 → 境界条件 → メッシュ → 実行 → 結果** の6段階で作るアプリ。
形状から四面体メッシュ、ソルバー用 `case.toml` を作成・保存し、プロジェクト内に実行履歴と結果を残せる。
Maxwell壁・質量流量入口は S3 対応版ソルバーで使用できる。固定版は `runs/phase8/s3/gsis-solver-s3-final.exe`。
ソルバーとはファイルとサブプロセスでやり取りし、Qtなしの `project.case_document()` で `case.toml` を生成する。

## 1. 起動

リポジトリのルートで実行する。初回は extra `gui` の依存を入れるので少し時間がかかる。

```powershell
uv run --project python --extra gui gsis-gui
uv run --project python --extra gui gsis-gui --project <計算フォルダ>
uv run --project python --extra gui gsis-gui --results runs\kinetic\tube_delta1_cis --theme dark
```

| 引数 | 内容 |
|---|---|
| `--theme system\|light\|dark` | 表示モード(既定は前回の選択、初回は `system` = OS の設定に合わせる) |
| `--project DIR\|project.toml` | 起動時にプロジェクトを開く |
| `--case FILE.toml` | 旧ケースを独立した編集窓で開く。管は「プロジェクト → 例題を開く…」でも開ける |
| `--results RUN_DIR` | 起動時に出力フォルダを読み込み、結果の画面を出す |
| `--page geometry\|settings\|boundaries\|mesh\|ai\|run\|results` | 最初に出す段階。前段が未完了なら進めない。`ai` は形状内のAIタブ |
| `--solver EXE` | 使う `gsis-solver`(既定は `build/release/src/gsis-solver.exe`、なければ `build/release-cpu/…`) |
| `--offscreen` | 画面を出さずに動かす(確認用。Qt の offscreen、3D は静止画の方式) |
| `--quit-after 秒` | 指定した秒数で終わる(起動の確認用) |

`python -m gsis_tools.gui` でも起動できる。

| 依存(extra `gui`、`python/pyproject.toml`) | 版(2026-10-16 に解決) | 用途 |
|---|---|---|
| PySide6-Essentials | 6.12.0 | 画面(Qt 6)。WebEngine などを含む PySide6 本体は大きいので、必要な部分だけの Essentials にした |
| pyvista / pyvistaqt | 0.49.1 / 0.13.1 | 3D 表示(VTK 9.7.1) |
| tomlkit | 0.15.1 | ケース TOML の読み書き(コメントと並びを保つ) |
| anthropic / openai | 1.15.0 / 3.35.1 | モデリング(AI): 公式の Python SDK(どちらも HTTP は httpx2)。2026-10-18 に追加 |
| mcp | 2.3.0 | モデリング(AI): MCP サーバー(`gsis-mcp`)とつなぐ公式の SDK(extra `mcp` と同じ)。2026-10-18 に追加 |
| keyring | 25.7.0 | モデリング(AI): API キーを OS の資格情報マネージャーに保存する(選んだときだけ)。2026-10-18 に追加 |

設定(表示モード、ソルバーの場所、最近開いたファイル、モデリング(AI)の設定。API キーは除く)は `%APPDATA%\GSIS-PBC\gsis-gui.ini` に保存する。

| 環境変数 | 内容 |
|---|---|
| `GSIS_GUI_SETTINGS` | 設定ファイルの場所を変える(試験・スクリーンショットで使う) |
| `GSIS_SOLVER` | 既定探索・保存済みのパスに優先するソルバー。`--solver` と画面の選択でさらに変更できる |
| `GSIS_GUI_3D` | 3D の方式: `interactive`(既定)/ `static`(オフスクリーンの静止画)/ `none` |
| `GSIS_ROOT` | リポジトリのルート(既定はパッケージの場所から探す) |
| `GSIS_GUI_AI_WORKSPACE` | モデリング(AI)の既定の作業フォルダ(設定で作業フォルダを決めていないとき)。なく、`GSIS_GUI_SETTINGS` があれば、その設定ファイルの隣の `ai_workspace`(試験とスクリーンショットで、利用者の作業フォルダに触らないため)。どちらもなければ `<リポジトリ>/mcp_workspace/gui` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | モデリング(AI)の API キー(公式の接続先にだけ使う。§2.3.5) |

## 2. 画面

左の6段階に `○ 未着手`・`✓ 済`・`! 要やり直し` を表示する。前段が済んでいない項目は無効になり、ツールチップに理由が出る。
前段を変えると、済になったことのある後段を要やり直しにする。済になったことがない段階は未着手を保つ。古いメッシュは残すが、検証済みの印を外す。

### 2.1 プロジェクトと形状

「プロジェクト」メニューから新規・開く・保存・名前を付けて保存・最近使ったプロジェクトを選ぶ。
新規と別名保存は空のフォルダを選び、別名保存ではCAD・境界・メッシュ・実行履歴も複製して相対パスを保つ。
計算名は上の欄で変えられる。未保存の変更があればタイトルに `*` が付き、切替・終了時に保存・破棄・取消を選べる。

![形状](gui_screenshots/32_geometry_cad.png)
![形状・ダーク](gui_screenshots/33_dark_geometry_cad.png)

- STEP・IGES・BREPを選び、単位(m / cm / mm / um)を指定して読み込む。STEP・IGESはファイル内の単位をOCCが変換する。BREPは選んだ単位で解釈する。
- 内部流は立体そのものを流体にする。外部流は外接箱と同じ中心に各辺を指定倍率で拡大した箱を作り、物体を引く。倍率は1より大きい数。
- 閉じた立体が1つ、体積が正、体積に属さない面がないことを検査する。IGESの閉じた面は縫い合わせる。小さい面・辺の自動削除はしない。
- 表示と一覧はSI(m、m²、m³)。番号・面積・重心・外向き法線を表示し、表で選ぶと3Dでも強調する。曲面の法線は面上の代表点での値。
- 全表面積の1e-6未満の面、外接箱の対角長の1e-5未満の辺を警告する。球の極など正常な退化辺は除く。
- 形状の外接箱の最小辺を `L_ref` の既定にする。単位・流れ・倍率を変更したら再検査する。
- AIタブの「AIの形状を流体領域に使う」でBREP・手順・伏せ字処理済みの会話を保存し、CADと同じ検査を通す。採用後にAI側で編集しても、採用済みBREPは保持する。更新には再度採用する。

![外部流](gui_screenshots/40_geometry_external.png)
![AIタブ](gui_screenshots/42_geometry_ai_tab.png)

#### 保存形式(版1)

```text
<project>/
  project.toml
  geometry/source_<hash>.step    # 元CADの写し(拡張子は元形式)
  geometry/domain_<hash>.brep    # 流体領域(geometry.unitの単位)
  geometry/ai_state.json         # AIを採用した場合の手順
  geometry/ai_conversation.json  # AIを採用した場合の会話。キーは保存しない
  boundaries.json               # 境界の値と面の幾何の指紋
  mesh/fluid.pmesh               # SI単位の四面体メッシュ
  mesh/fluid_quality.md          # 品質、形状の面積・体積との差
  mesh/fluid_quality.json        # 品質の数値とVRAMの概算
  case.toml                     # プロジェクトから生成。手では編集しない
  runs/<日時>/case.toml           # 実行時の入力の写し
  runs/<日時>/gui_run.json        # 例題の識別・作成日時・実行コマンド
  runs/<日時>/                   # history.csv・summary.json・VTU
```

gmshの取り込みキャッシュは `.gsis_mcp/` に置く。AI手順の再現に要る取り込みCADも複製する。
`project.toml` は `format = "gsis-project"` と整数の `version = 1` を持つ。未知の版は開かない。

| 節 | 内容と書式 |
|---|---|
| `[project]` | `name`: 計算名(文字列) |
| `[stages]` | `geometry/settings/boundaries/mesh/run/results`: `pending/done/redo` のいずれか |
| `[geometry]` | `source/domain`: 相対パス、`unit`: m/cm/mm/um、`flow`: internal/external、`box_factor`: 倍率、`source_kind`: cad/ai、`accepted/valid`: 真偽、`volume`: m³、`length_ref`: m、`bbox`: 最小xyz・最大xyzの6要素[m]、`errors/warnings`: 文字列配列 |
| `[[geometry.faces]]` | `id`: 整数、`area`: m²、`centroid`: xyz[m]、`normal`: 外向き単位法線xyz(不明時は省略)、`type`: 面の種類 |
| `[geometry.pending_input]` | 未確定の `unit/flow/box_factor`。前回の領域は検証済みとして扱わない |
| `[settings]` | `GeneralSettings.to_dict()` の全フィールド。SI入力、手動速度の範囲は√(R T0)単位。下記参照 |
| `[settings.kinetic]` / `[settings.macro]` | 上級のキーと値。既知の型を検査する |
| `[mesh]` | `path`: 相対パス(既定 mesh/fluid.pmesh)。`validated` は上流変更時に削除 |

`[settings]` の全フィールド(下は既定値。L_refは形状から更新):

```toml
[settings]
mode = "gsis" # gsis / cis / nsf。四面体を初めて確認したときは cis
gas = "He" # He / Ne / Ar / Kr / Xe / 任意
molecular_mass = 6.65e-27
mu_ref = 1.916e-5
T_ref = 273.15
omega = 0.66
L_ref = 0.001
length_auto = true
T0 = 273.15
wall_temperature = 273.15
p_ref = 100.0
T_min = 273.15
T_max = 273.15
u_max = 0.0
velocity_auto = true
vel_n = [16, 16, 16]
vel_range = [[-5.0, 5.0], [-5.0, 5.0], [-5.0, 5.0]]
n_cis = 5
max_pbc = 10
cfl_pbc = 500.0
tolerance = 1.0e-5
velocity_chunk = 0
estimated_cells = 100000
free_vram_gib = 14.7
[settings.kinetic]
[settings.macro]
```

AIの記録は `geometry.ai_state`・`geometry.ai_conversation` に相対パスを保存する。
`boundaries.json` は境界の辞書の配列。`faces` と同じ順序の `fingerprints`(SIの面積・重心・外向き法線・面の種類)を付けて幾何で照合する。
`case.toml` 生成時は `faces/fingerprints/automatic` を含めず、ソルバー用の境界キーだけを書く。Maxwell壁・質量流量入口の追加値も保持する。

### 2.2 設定、境界条件とメッシュ

![設定](gui_screenshots/34_settings_auto.png)
![上級・手動](gui_screenshots/36_settings_advanced_manual.png)

- モードはGSIS-PBC / CIS単独 / NSFのみ。気体はHe・Ne・Ar・Kr・Xeのプリセットと任意の単原子物性。
- Heはv1の値。他の4種は **Bird (1994) 表 A1、273 K** のVHS定数へ切り替えた。値は統括役の提供で、G2では一次資料と照合していない。
- 基準長さの自動／手動、基準温度・圧力、境界の最低／最高温度と最大速度を入力する。境界の割り当て・確定時に `GeneralSettings.update_boundary_defaults()` で更新する。圧力境界がなければ入力した圧力を保つ。
- `n_cis/max_pbc/cfl_pbc/tolerance` を編集する。上級では運動論とPBCの既知のパラメータを追加できる。メモリ見積もりはFP32。
- 自動の速度範囲は±(|u|max + 5√(R T_max))。幅/n ≤ 0.7√(R T_min)となる偶数の点数を採る。自動時の `case.toml` は `[velocity_grid] auto=true` のみとし、`n`・`range` は書かない。手動時は各軸の数値を書く。C++ は実際の境界入力から決定し、Python の表示と食い違えば警告する。
- R、μ(T0)、平均自由行程、Kn、点数、VRAMの概算を編集ごとに表示する。Python は FP32 の f=セル数×速度点×4 B、作業領域=セル数×チャンク幅×16 B、固定費0.1 GB、予約1 GiBの概算。固定費・丸め・残差部分和はC++と異なり、安全側とは限らない。NSFのGPU費用は全て0。
- 設定・境界の編集後350 msで `check-case --json --no-mesh`、完成メッシュがあれば `check-case --json` を非同期に実行する。導出量、速度格子、VRAM、パッチ、警告・エラーを表示する。古い要求は取り消し、30秒でタイムアウトする。
- **VRAM 確定見積もり(C++)** は S2 の `memory` を使う。精度・固定配列・鏡映・チャンクの丸めを含む。実行時も精度を反映して再検査し、GPUの予算超過なら開始を止める。空きVRAMは設定欄の仮定値を `--free-vram-bytes` で渡すため、実際の確保成功を保証しない。
- 四面体を含むメッシュの初回確認時は CIS 単独と `[kinetic] positivity_limiter=true, pseudo_time="velocity", pseudo_cfl=5` を既定にする。その後の明示選択は保持する。GSISを選ぶと「四面体の GSIS は検証中(流量がずれることがある)」と警告する。六面体のみの例題は従来どおり。
- 不正な入力では確定・保存を止める。「設定を確定して境界条件へ」で済にする。

| 気体 | 分子の質量 [kg] | μ_ref [Pa·s] | T_ref [K] | ω |
|---|---:|---:|---:|---:|
| Ne | 33.5e-27 | 2.975e-5 | 273 | 0.66 |
| Ar | 66.3e-27 | 2.117e-5 | 273 | 0.81 |
| Kr | 139.1e-27 | 2.328e-5 | 273 | 0.80 |
| Xe | 218.0e-27 | 2.107e-5 | 273 | 0.85 |

#### 境界条件

![境界条件](gui_screenshots/44_boundaries.png)
![境界条件・ダーク](gui_screenshots/45_dark_boundaries.png)

1. 一覧の行、または対話型3Dの面をクリックする。Ctrlで追加選択し、ドラッグは視点を回転する。offscreenの静止画像では一覧を使う。
2. 「新しいパッチ」で名前・種類・値を入力し、「選択した面を割り当て・更新」を押す。同じ面の前の割り当ては置き換える。パッチ名は英字で始まる英数字・`_`・`-`。
3. 上のパッチ一覧から既存パッチを選ぶと、その面と値を編集できる。「削除して既定の壁へ」で割り当てを解除する。
4. 「境界条件を確定してメッシュへ」で済にする。編集中の値は先に割り当てボタンで確定する。未確定の値があれば保存・境界の確定を止める。

| 種類 | 入力 |
|---|---|
| `diffuse_wall` | 温度 [K]、任意の壁速度 x/y/z [m/s] |
| `maxwell_wall` | 温度 [K]、適応係数 `accommodation` (0 < α ≤ 1、既定1)、任意の接線方向の壁速度 `velocity` [m/s] |
| `pressure_inlet` | 正の圧力 [Pa]、温度、接線速度 `extrapolate` / `zero` |
| `pressure_outlet` | 0以上の圧力 [Pa] |
| `vacuum` | 追加の値なし |
| `mass_flow_inlet` | パッチ全体の正の流入質量流量 `mass_flow` [kg/s]、温度 [K]、接線速度 `tangential_velocity` = `extrapolate` / `zero` |
| `symmetry` | 法線が座標軸にそろう平面だけ。同じパッチ内は同じ軸に限定 |

未割り当て面は `default_wall` (予約名)の拡散壁に入れる。温度は設定画面の「既定の壁温度」(`wall_temperature`)で指定する。境界の最高温度から基準温度を更新しても既定壁の温度は変わらない。面ごとに異なる温度にする場合は、新しい壁パッチへ割り当てる。G1の既存プロジェクトでこの設定がない場合は、保存されていた基準温度から補う。

Maxwell壁の α=1 は拡散壁と同じ。α<1 と NSF の組合せでは、設定・境界・実行・結果で精度の注意を出す。NSFは半空間Maxwell混合のPBC境界を使い、連続体のすべり・温度跳躍の定量精度を保証しない。完全拡散壁の既定NSFすべり係数を α<1 に流用しない(ref1d側でも拒否する)。傾斜面の鏡面反射は速度空間の補間を含むので、速度格子への依存性を確認する。

質量流量入口は初期 `p_b = p_ref` から始め、目標流量に合わせて圧力を反復中に更新する。圧力入口・圧力出口がなく、質量流量入口と真空などだけのときは、設定画面の `p_ref` を保持する。流量からの圧力の自動推定は行わない。設定・境界・実行画面にこの扱いを表示する。`check-case --json` のパッチごとの α・目標 [kg/s]・初期 p_b [Pa] は設定・メッシュ画面の検査欄で確認できる。

自動速度格子は質量流量入口の温度とMaxwell壁の温度・任意の壁速度も集計する。接線速度の `zero/extrapolate` は速度ベクトルとして加算しない。C++ の格子と照合し、食い違えば警告してC++値を使う。旧ケース編集窓でも新境界の値を読み書きできる。

![Maxwell壁](gui_screenshots/75_s3_maxwell_wall.png)
![質量流量入口](gui_screenshots/77_s3_mass_flow_inlet.png)
![S3入力検査](gui_screenshots/79_s3_check_case.png)
![NSFと部分適応壁の注意](gui_screenshots/85_s3_nsf_maxwell_warning.png)

CADまたはAI形状の再採用時には、番号に依存せず幾何の指紋で照合する。一意に照合できなかった旧面を形状画面と状態欄に知らせる。割り当てを失った明示パッチは空のまま残すので、再割り当てまたは削除してから境界を確定する。

![境界入力の検査](gui_screenshots/46_boundary_validation.png)

#### メッシュ

![メッシュ](gui_screenshots/48_mesh.png)
![メッシュ・ダーク](gui_screenshots/49_dark_mesh.png)

- 全体の最大・最小寸法、パッチごとの寸法(空欄は全体)、成長率を入力して「メッシュを生成」。パッチ寸法は全体の最小〜最大の範囲。
- 成長率 `g` は、面から離れた距離 `d` に対する目標寸法場の傾き `dh/dd = g - 1` として指定する。gmshのDistance/Threshold場で最大寸法まで広げる。生成後の隣接セル寸法比を厳密に上限規制する値ではない。
- 生成は別スレッドが専用プロセスを起動し、MCP engine のCAD・物理グループ・gmsh生成・pmesh出力を再利用する。「停止」は子プロセスを終了し、途中出力を削除する。以前の完成したメッシュは保持する。生成中は段階の切り替え・保存を止める。
- 終了後にセル数・非直交角・歪み・隣接体積比、各パッチの面積とCAD面積との差、実セル数によるVRAMの概算を表示する。メッシュと品質レポートは `mesh/<名前>.pmesh`・`<名前>_quality.md`・`<名前>_quality.json` に保存する。
- 予算内ならメッシュを済にし、`case.toml` を生成・保存する。予算超過ではメッシュと品質を保存しても済にせず、実行へ進ませない。セル数または速度点を減らす。
- S3固定版でMaxwell壁・質量流量入口を含む入力とCPUの3モードを確認した。自動速度格子は `auto=true` だけを書き、手動時は `n/range` を書く。
- 数値入力欄は有効数字6桁程度で表示する。画面から編集しなければ、表示前の精度を保存・計算に使う。

![VRAM予算超過](gui_screenshots/50_mesh_vram_warning.png)

### 2.3 モデリング(AI) — 形状内のタブ

自然言語で指示すると、AI(LLM)が gmsh の MCP ツール(`docs/mcp.md`、31 個)を使って計算領域の形状を作り、境界パッチを
付け、`.pmesh` まで書き出す。ツールで形状・パッチ・メッシュが変わるたびに、3D 表示がその場で更新される。
AI は API キーで動かし、Anthropic 形式(Messages API)と OpenAI 形式(Responses API と Chat Completions API。OpenAI 互換の
サーバーを含む)に対応する。接続先(base URL)・モデル・API キー・OpenAI の API の種類は設定で選ぶ。

![1 回目の指示: 形状とパッチ(面の番号を表示、batch の記録を開いた)](gui_screenshots/24_ai_shape_patches.png)
![2 回目の指示: メッシュと書き出し(メッシュの画面で開くボタン)](gui_screenshots/25_ai_mesh_export.png)

画像は模擬の AI(決まった応答を返す HTTP の模擬。実際の API は呼んでいない)が、本物の MCP サーバーのツールで管(論文 §4.1)の
計算領域を 2 回の指示で作ったところ(`python -m gsis_tools.gui.screenshots_ai`、§5)。トークン数は模擬の値。

#### 2.3.1 使い方

1. 形状の「モデリング(AI)」タブを開く。APIの設定とキーの扱いは従来どおり(§2.3.4・§2.3.5)。
2. 自然言語で形状を指示し、3Dと会話の結果を確認する。
3. 「AIの形状を流体領域に使う」を押し、通常のCADと同じ領域作成・検査を通す。
4. 形状が済になったら設定に進む。AIの `.pmesh` の受渡しはG2で接続する。

#### 2.3.2 画面

| 場所 | 内容 |
|---|---|
| 上 | 形式・モデル・接続先・キーの読み込み元(値は出さない)・MCP サーバーの状態・作業フォルダ。「元に戻す」(MCP の `undo`。batch は 1 手順)、「状態の保存…」「状態の読み込み…」(`model_save` / `model_load`。作業フォルダの外のファイルは `.gsis_gui/imports/` に写して読む)、「設定…」 |
| 左 | 会話の欄: ユーザーの文章、AI の文章(Markdown。文章の中の生の HTML は文字として出す)、AI の考え(途中の報告や要約が来たとき)、ツールの呼び出しの記録(状態 ✓ / ✗ / —、名前、引数と結果の要約、時間。▶ で開くと引数と結果の JSON と、`render` の画像)、説明(情報・注意・エラー。API のエラーで再送できるときは「再送」)、書き出したメッシュへのリンク。下に指示の欄、「送る」「停止」「再送」「選んだ面を差し込む」、トークン数。「会話の保存…」「会話を開く…」「会話のリセット」 |
| 右上 | 3D 表示: 面はパッチごとに色分け(入口は青、出口は緑、壁は灰緑、付いていない面は薄い灰色)、面の境目に線、2 つの体積の間の面は薄く。メッシュがあればその表面(「メッシュの辺」で辺)。「面の番号」で番号を表示。表示「全体」「断面(手前 y < 中心 を除く)」などで中を見る。視点「斜め」「上から」「横から」 |
| 右下 | タブ「パッチ」(色・名前・kind・面・面積。行を選ぶとその面を強調)、「面」(面の一覧。選んだ面にパッチを付ける)、「ツールを手で実行」(ツールと引数の JSON を選んで実行。結果を表示)、「ログ」 |

**面の選択と手でのパッチ**(下の図): 対話型の 3D 表示では「面を選ぶ(クリック)」をオンにして、面を左クリックで選ぶ(Ctrl で足す。押して離すまでに 4 px より動かしたとき(ドラッグで回したとき)は選ばない。線と文字は選ばない)。
画面を出さない方式・静止画の方式では、「面」の一覧で行を選ぶか、番号を入れる。選んだ面は 3D 表示で強調し、
「選んだ面を差し込む」で指示の欄に「面 3」のように入れる。パッチ名と kind を選んで「パッチを付ける」で、その面にパッチを
付ける(`assign_patch` の `mode: add`、別のパッチの面なら移す)。手で行った操作(パッチ・元に戻す・状態の読み込み・
ツールの実行)は、次の指示の先頭に「[GUI での操作]」として AI に知らせる。

![断面と面の選択(入口の面を選んだ)](gui_screenshots/26_ai_section_faces.png)

#### 2.3.3 仕組み

```
GUI のスレッド(Qt)                       別スレッドの asyncio のループ(gsis_tools.gui.ai.backend)
  会話の欄・3D 表示・設定  <──知らせ──   AI の反復(agent) ──HTTPS──> AI の API(Anthropic / OpenAI / 互換サーバー)
                            ──指示・停止→      │
                                               └──stdio(MCP)──> 子プロセス gsis-mcp --gui-tools(gmsh / OpenCASCADE)
                                                                     作業フォルダ(.pmesh・状態・自動保存)
```

- **MCP サーバーは子プロセス**(GUI と同じ Python で `python -m gsis_tools.mcp --workspace <作業フォルダ> --gui-tools`)。
  OpenCASCADE がブーリアンなどで落ちても GUI は落ちない。ツールの定義はサーバーの tools/list から読む(1 か所)。
  公式の MCP Python SDK のクライアントでつなぎ、子プロセスには SDK の既定の安全な環境変数(PATH・TEMP など)だけを渡す
  (API キーの環境変数は渡らない)
- **自動保存と復旧**: モデルを変え得るツール(読むだけでないもの)が成功するたびに、作業フォルダの
  `.gsis_gui/autosave/state.json` に状態(手順の履歴)を保存する。サーバーが落ちたら起動し直して自動保存を読み込み、その
  ツールの結果として「異常終了したので直前の状態に戻した」を AI に返す。GUI を起動し直したときも、前回の自動保存を読み込む
- **AI の反復**: 指示ごとに、API に送る → 応答のツールの呼び出しを順に実行 → 結果を返す、をツールを呼ばなくなるまで繰り返す。
  ツールのエラー(`isError`)は理由と直し方(`error`・`hint`)ごと AI に返して直させる。引数が JSON として読めない・未知の
  ツールも、エラーとして返す
- **ツール呼び出しの上限**: 1 回の指示あたり 40 回(設定)。超えた呼び出しは実行せず、上限の説明を結果として返して止める。
  「続けて」と送れば続く(上限は指示ごと)
- **停止**: AI の応答待ちは、その場で通信を切る(途中の応答は捨てる)。ツールの実行中は 2 秒だけ終わるのを待ち、終わらなければ
  強制停止: MCP サーバーを止めて起動し直し、直前の自動保存を読み込む(実行中の手順の変更とメッシュは失われる)。応答にあった
  ほかの呼び出しは実行しない。どの場合も、すべての呼び出しに結果(「止めた」)を付けて履歴を閉じるので、次の指示を送れる
- **API のエラー**: 429・5xx・通信の失敗・タイムアウトは SDK が 2 回まで再試行し(待ち時間は `retry-after` に従う)、それでも
  だめなら説明と「再送」を出す。401(キー)・403・404(モデル名)・400 は再送せず説明を出す。説明はキーを伏せ字にしてから出す
- **断られた応答**(Anthropic の `stop_reason: refusal`、OpenAI の Responses の `refusal` と `incomplete_details.reason:
  content_filter`、Chat Completions の `finish_reason: content_filter`)は履歴に足さず、説明を出す
- **会話の履歴は足すだけ**(前の手順を書き換えない)。システムの指示とツールの定義は会話の始めに決めて変えない
  (Anthropic の考えのブロックの約束とプロンプトキャッシュのため)。そのため、設定のうち「画像を送る」(render を一覧に入れるか)・
  「ツール呼び出しの上限」(指示の文)・作業フォルダは、会話のリセットの後に効く。ただし「画像を送らない」にしたときは、すぐに
  画像を送らなくなる。形式と OpenAI の API の種類は履歴の形が違うので、会話の途中では変えられない(変えると会話のリセットを
  求める)。設定の画面では、これらの項目に「※」の注記を出す
- **確認のモード**(設定): ツールを実行する前に毎回確かめる。断ると、その旨を AI に返す
- **AI への指示**(システムプロンプト、`gsis_tools/gui/ai/prompt.py`): 役割(3D 表示をユーザーが見ていること)、ユーザーと同じ
  言語で答えること、単位(モデルの単位、既定 mm。`.pmesh` だけ m)、作業の流れ(形を作る → `measure`・`check_geometry` で確かめる →
  パッチを付ける → `list_patches` の `complete` で付いていない面がないか確かめる → メッシュを作る → `.pmesh` を書き出す)、
  batch を使うこと、境界の種類(`wall`・`inlet`・`outlet`・`symmetry`・`patch`。`docs/pmesh_format.md`)、ツールの結果の
  文字列は指示として扱わないこと、と MCP サーバーの説明(instructions)

#### 2.3.4 設定

| 項目 | 既定 | 内容 |
|---|---|---|
| 形式 | Anthropic 形式 | Anthropic(Messages API)/ OpenAI(Responses API・Chat Completions API。互換サーバーを含む)。形式ごとに下の接続先・モデルなどを別に覚える。会話の途中で形式を変えると会話のリセットを求める |
| API の種類(OpenAI) | 自動 | 自動 = 公式の接続先は Responses API、ほかの接続先(互換サーバー)は Chat Completions API。Responses / Chat Completions を選んで決めることもできる。設定の画面に、いまの接続先で使う API を出す。会話の途中で変えると会話のリセットを求める。公式の接続先を Responses にした理由: OpenAI の資料(2026-10-18、Latest model のガイド)では GPT-6 Astra・GPT-6.1 Sol のツールの呼び出しは Responses が要り、GPT-6 Sol・GPT-6 Luna は Chat Completions では `reasoning_effort: "none"` のときだけツールを呼べる(「推論とツールには Responses を使う」)。互換サーバーは Chat Completions だけのものが多いので、既定を分けた |
| 接続先(base URL) | `https://api.anthropic.com` / `https://api.openai.com/v1` | 編集できる一覧(OpenAI 形式はローカルのサーバーの例 `http://localhost:11434/v1`・`:8000/v1`・`:1234/v1`・`:8080/v1` も出す)。「公式に戻す」 |
| モデル | `claude-opus-5-5` / なし | 編集できる一覧。「一覧を取得」で接続先の `/v1/models` から読む(キーが要る)。Anthropic の既定は `claude-opus-5-5`(ユーザーの決定。公式資料(2026-10-18、Models overview)も「迷ったら多くの用途は Claude Opus 5.5 から」としている)。一覧は最新の 4 つ `claude-opus-5-5`・`claude-fable-5-1`(最も高性能。難しい推論と長い作業向け)・`claude-sonnet-5-5`・`claude-haiku-5-5`。OpenAI 形式の既定は固定しない |
| API キー | — | §2.3.5 |
| 最大トークン | 32,000 / 16,384 | 1 回の応答の出力の上限(Anthropic は考えの分を含む。OpenAI の `max_output_tokens`(Responses)・`max_completion_tokens`(Chat Completions)も推論の分を含む) |
| ツール呼び出しの上限 | 40 | 1 回の指示あたり。上限そのものは次の指示から効き、AI への指示の文に書く数は会話のリセットの後に効く |
| 応答を逐次表示する | オン | ストリーミング(SSE) |
| タイムアウト・再試行 | 600 s・2 回 | SDK に渡す |
| Anthropic: プロンプトキャッシュ | オン | ツールの定義の最後とシステムのブロックに `cache_control`、会話はリクエストの最上位の `cache_control`(自動) |
| Anthropic: 考えの表示 | 自動 | 自動 = 公式の接続先で `display: "updates"` を受け付けるモデル(Fable 5.1・Fable 5・Mythos 5.1・Opus 5.5・Sonnet 5.5)ではツールの呼び出しの間の途中の報告を受け取る(beta `thinking-display-updates-2026-08-18`)、ほかは送らない。「考えの要約」(`summarized`)も選べる |
| Anthropic: effort | 指定しない | `output_config.effort`(low〜max)。指定しなければモデルの既定(Opus 5.5 は medium、Fable 5.1 は high) |
| Anthropic: 断られたときの切り替え | オン | 公式の接続先で Opus 5.5・Fable 5.1・Fable 5・Opus 5・Sonnet 5.5 のとき、安全の分類で断られたら別のモデルで続ける `fallbacks: "default"`(beta `server-side-fallback-2026-07-01`。Haiku 5.5 にはサーバー側の切り替えがないので送らない。Refusals and fallback の資料)。途中で切り替わった応答は、公式の規則どおり、最後の切り替えより前の考えとツールの呼び出しを送り返さない |
| OpenAI(Chat Completions): 出力の上限の引数 | 自動 | 自動 = 公式の接続先は `max_completion_tokens`(`max_tokens` は非推奨で o 系などの推論モデルでは使えない)、ほかの接続先は `max_tokens`。400 の説明に両方の名前があれば、もう一方に切り替えて 1 回だけ送り直し、設定に残す。`stream_options` を受け付けない互換サーバーにも、外して 1 回だけ送り直す。Responses は `max_output_tokens`(上の最大トークン) |
| OpenAI(Responses): 応答を OpenAI 側に保存する | オフ | オフ = `store: false` を送る(省くと true で、応答が OpenAI 側に 30 日以上保存される)。どちらでも会話は手元に持ち、毎回すべて送る(`previous_response_id` は使わない)。`include`(公式の接続先にだけ送る)を接続先が受け付けない(400)ときは外して 1 回だけ送り直す。`store` を外して送り直すのは公式以外の接続先だけ(公式の接続先では、省くと保存されるので外さない) |
| 追加の引数(JSON) | なし | リクエストの本文に足す(例 Anthropic `{"output_config": {"effort": "high"}}`、OpenAI の Responses `{"reasoning": {"effort": "low", "summary": "auto"}}`(推論の要約を会話の欄に出す。推論のモデルだけ)、Chat Completions `{"reasoning_effort": "low"}`) |
| 画像を送る | オフ | `render` の画像を AI に送る(オフのときは `render` を AI のツールの一覧に入れない)。Anthropic は `tool_result` の中に、OpenAI の Responses は `function_call_output` の `output` の中に(`input_image`)入れる。Chat Completions は tool のメッセージに画像を入れられないので、後ろの user のメッセージで送る。長い辺が 1568 px を超える画像は縮めて頼む。会話のリセットの後に効く(オフにしたときは、すぐに送らなくなる) |
| 確認のモード | オフ | ツールを実行する前に毎回確かめる |
| 作業フォルダ | `<リポジトリ>/mcp_workspace/gui` | MCP サーバーが書き出してよい場所(git の対象外)。変えると、すぐにサーバーを起動し直し、会話をリセットする |

設定の画面の「※」の注記は、会話のリセットの後に効く項目(画像を送る・上限の数の文)と、会話の途中で変えられない項目
(形式・API の種類)を示す。会話の途中で開いたときは、作業のタブにその旨を出す(31)。

![設定の画面: Anthropic 形式(既定のモデル claude-opus-5-5。API キーは伏せ字)](gui_screenshots/27_ai_settings.png)
![設定の画面: OpenAI 形式(API の種類は自動。公式の接続先なので Responses API を使う)](gui_screenshots/29_ai_settings_openai.png)
![設定の画面: 応答のタブ(OpenAI の出力の上限の引数と、Responses の store)](gui_screenshots/30_ai_settings_openai_reply.png)
![設定の画面: 作業のタブ(会話のリセットの後に効く項目の注記。会話の途中で開いた)](gui_screenshots/31_ai_settings_work.png)

#### 2.3.5 API キー

- **平文では保存しない**。読み込み元は 3 つで、この順に探す: (1) 設定の画面で入力したキー(このセッションの間だけメモリに持つ)、
  (2) OS の資格情報マネージャー(`keyring`。Windows は資格情報マネージャー。設定の画面で「OS の資格情報マネージャーに保存する」を
  選んだときだけ保存する。項目は形式と接続先ごと)、(3) 環境変数 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
- 環境変数のキーは**公式の接続先にだけ**使う(別のサーバーにキーが渡らないように)。公式以外の接続先にも使うときは、形式ごとに
  「環境変数のキーを、公式以外のこの接続先にも使う」を選ぶ
- OpenAI 互換のローカルのサーバーは「キーなしで送る」を選べる(`Authorization` を送らない)。公式以外の接続先には、SDK が
  環境変数から付ける組織・プロジェクトの ID(`OpenAI-Organization`・`OpenAI-Project`)も送らない
- SDK にはキーと接続先を明示して渡す(SDK に環境変数・プロファイルの資格情報を探させない)。ただし SDK は
  `ANTHROPIC_CUSTOM_HEADERS` / `OPENAI_CUSTOM_HEADERS` の環境変数があれば、そのヘッダーを付ける(SDK の仕様)
- キーを出さないもの: ログ、会話の記録・保存した会話、作業フォルダ、設定ファイル、画面(設定の画面の欄は伏せ字で、前に入れた
  キーや保存したキーは欄に戻さない。読み込み元だけを示す)、画面写真、例外とエラーの説明。キーは `Secret`(表示・ログ・pickle で
  伏せ字)に包み、画面とファイルに出す文字列は、扱ったキーとキーらしい文字列(`sk-...` など)を伏せ字にしてから出す。ログの受け手にも
  同じ伏せ字の filter を付ける
- 起動しただけではキーを読まない(送るときと設定の画面を開いたときに読む)

#### 2.3.6 外部に送られるもの

AI の API(設定の接続先)に送るのは次のもの。作業フォルダのファイル(`.pmesh` など)の中身は、ツールの結果に入るもの以外は送らない。

| 送るもの | 内容 |
|---|---|
| システムの指示 | 固定の文と、MCP サーバーの説明(約 7 KB) |
| ツールの定義 | 30 個(画像を送るときは `render` を入れて 31 個)の名前・説明・引数の JSON Schema(約 35 KB) |
| 指示 | ユーザーの文章と、手で行った操作の知らせ「[GUI での操作]」 |
| AI の応答 | 前の応答(文章・考えのブロック・ツールの呼び出し。OpenAI の Responses は推論の項目(暗号化された内容)を含む)を毎回送り返す |
| ツールの結果 | MCP サーバーの応答の JSON(体積・面の一覧(重心・法線・半径)・パッチ・メッシュの数値・作業フォルダからの相対パスなど)。`model_info` の結果には作業フォルダの絶対パスが入る。エラーの説明 |
| 画像 | 「画像を送る」をオンにしたときだけ、`render` の PNG |
| API キー | HTTP のヘッダー(`x-api-key` / `Authorization`)で、その接続先にだけ |

- 送ったデータの扱い(保存期間・学習への利用など)は、各社の規約と組織の設定による(例: Anthropic の資料では、Claude Fable 5.1 は
  30 日のデータ保持が要る)。社外秘の形状を扱うときは、接続先とモデルを選ぶ前に確かめる
- 公式の接続先で「断られたときの切り替え」がオンのとき、断られたリクエストは Anthropic のサーバーの中で別のモデルが処理する
- **OpenAI の Responses API の保存(`store`)**: Responses は `store` を省くと true で、応答を OpenAI 側に保存する(SDK の型の説明:
  少なくとも 30 日。Conversation state のガイド: 既定で 30 日)。データの保持を減らすため、既定で `store: false` を送り、
  `previous_response_id`(OpenAI 側に保存した応答をつなぐ方式)は使わず、会話は手元に持って毎回すべて送る(SDK の
  `ResponsesToolRunner` と同じ方式)。推論のモデルが関数の呼び出しと一緒に返す推論の項目は、次のリクエストで送り返す必要が
  ある(Reasoning のガイド)。`store: false` では推論の項目に暗号化された内容(`encrypted_content`)が付き(Reasoning のガイド:
  既定で付く)、公式の接続先には `include: ["reasoning.encrypted_content"]` も送る。暗号化された内容は会話の保存のファイルにも
  入る(読める文ではない。伏せ字の処理でも書き換えない)。設定で「応答を OpenAI 側に保存する」をオンにすると `store: true` を
  送る(会話は同じく毎回すべて送る)

#### 2.3.7 トークンと費用の注意

- AI の API は**使った分だけ料金がかかる**(入力と出力のトークン数で決まる。料金はモデルごとに違い、変わることがあるので、
  GUI は料金の表を持たない。各社の料金のページで確かめる)。画面の下に、この会話の入力・出力のトークン数(Anthropic は
  キャッシュの読み・書きの内訳、OpenAI はキャッシュの読み)と、直前のリクエストの数を出す
- 1 回の指示で、ツールを呼ぶたびにリクエストを送る(数回〜数十回)。リクエストごとに、システムの指示とツールの定義(合わせて
  約 42 KB。1 万トークンを超える程度と推定)と、それまでの会話の全部を送るので、手順が進むほど 1 回の入力が増える
- Anthropic はプロンプトキャッシュで、前と同じ部分(ツールの定義・システムの指示・会話の前の部分)をキャッシュの読みとして安く
  数える(既定でオン。公式資料では、キャッシュの読みは基本の入力の 5%(Opus 5.5・Sonnet 5.5)、2.5%(Fable 5.1))。
  OpenAI(Responses・Chat Completions)は接続先の自動のキャッシュ(使えれば。`cached_tokens`)
- 画像は 1 枚ごとにトークンを使う(Anthropic の資料では ⌈幅/28⌉ × ⌈高さ/28⌉。既定の 900 × 700 px で約 830 トークン)。
  画像は既定で送らない
- 考える量(effort・考えの表示)を上げると出力のトークンが増える(Opus 5.5 の effort の既定は medium、Fable 5.1 は high)。
  モデルが高性能なほど 1 トークンあたりが高い(2026-10-18 の公式資料では、Claude Fable 5.1 は既定の Claude Opus 5.5 の入力・出力
  とも 2.5 倍。effort の既定も Fable 5.1 のほうが高いので、出力のトークンも増えやすい)
- 費用を抑えるには: ツール呼び出しの上限を下げる、指示を具体的にする(寸法・単位・パッチ名を書く)、画像を送らない、
  安いモデルにする、会話が長くなったらリセットする

#### 2.3.8 形式ごとの違い(`gsis_tools/gui/ai/providers/`)

| 点 | Anthropic(Messages API) | OpenAI(Responses API) | OpenAI(Chat Completions API) |
|---|---|---|---|
| エンドポイント | `POST /v1/messages`(beta を使うときは SDK の beta の名前空間で `?beta=true` と `anthropic-beta` ヘッダー) | `POST {base_url}/responses` | `POST {base_url}/chat/completions` |
| ツールの定義 | `tools: [{name, description, input_schema}]` | `tools: [{type: "function", name, description, parameters, strict: false}]`(`strict` を省くと strict モードに直そうとするので、MCP のスキーマのために false を明示する) | `tools: [{type: "function", function: {name, description, parameters}}]` |
| システムの指示 | `system: [{type: "text", text, cache_control}]` | `instructions` | `messages` の先頭の `{role: "system"}` |
| 会話 | `messages` | `input`(user のメッセージ `{role: "user", content: [{type: "input_text"}]}` と、応答の `output` の項目をそのまま) | `messages` |
| 1 回の応答の複数の呼び出し | `content` の `tool_use` ブロック(`id`・`name`・`input`)を順に実行 | `output` の `function_call` の項目(`call_id`・`name`・`arguments` は JSON の文字列)を順に実行 | `message.tool_calls`(`function.arguments` は JSON の文字列)を順に実行。ストリーミングでは `index` ごとに断片を足す |
| ツールの結果 | 次の 1 つの user のメッセージに `tool_result`(`tool_use_id`・`content`・`is_error`)を呼び出しの順に並べる | 呼び出しごとに `{type: "function_call_output", call_id, output}` | 呼び出しごとに `{role: "tool", tool_call_id, content}` |
| 画像 | `tool_result` の `content` に image のブロック | `output` を `input_text` と `input_image`(`image_url` に data URL)の並びにする | tool のメッセージには入れられないので、後ろに user のメッセージ(`image_url` の data URL) |
| ストリーミング | SDK の `messages.stream()` | `response.output_text.delta`・`response.reasoning_summary_text.delta`・`response.output_item.added`(関数の名前)を表示に使い、最後の `response.completed` / `response.incomplete` の応答の全体を履歴に足す。`response.failed`・`error` はエラー | `stream_options.include_usage`、`index` ごとの断片 |
| トークン数 | `usage.input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`、`output_tokens` | `usage.input_tokens`(キャッシュを含む)、`input_tokens_details.cached_tokens`、`output_tokens`(推論を含む)、`output_tokens_details.reasoning_tokens` | `usage.prompt_tokens`(キャッシュを含む)、`prompt_tokens_details.cached_tokens`、`completion_tokens`(考えを含む) |
| 考え | `thinking` ブロック(署名ごと送り返す)。「途中の報告」を会話の欄に出す | `reasoning` の項目(暗号化された内容ごと送り返す)。要約(`summary`)があれば会話の欄に出す | 互換サーバーの `reasoning_content` / `reasoning` を表示だけ(送り返さない) |
| 出力の上限 | `max_tokens` | `max_output_tokens`(切れると `status: incomplete`) | `max_completion_tokens` / `max_tokens`(設定) |
| 会話の保存 | なし | `store: false`(既定。§2.3.6) | なし(`store` は送らない) |
| キーなし | 不可 | 可 | 可(ローカルのサーバー) |

3 つとも、公式の Python SDK の非同期のクライアント(`anthropic.AsyncAnthropic`・`openai.AsyncOpenAI`)を使い、`base_url` を渡す。
SDK の HTTP の層は httpx2(anthropic 1.x・openai 3.x)で、試験では `httpx2.MockTransport` を渡す(§4)。

公式資料で確かめたこと(2026-10-18): Anthropic は platform.claude.com の Models overview(モデル名と既定)、Tool use の Overview と
Handle tool calls(`tool_result` の形・画像・`is_error`・結果を本文の先頭に置くこと)、Prompt caching、Streaming(イベントと
`usage`)、Models API(`GET /v1/models`)、Vision(画像のトークン数)と、SDK 1.15.0 の型。OpenAI は developers.openai.com の
Function calling のガイドと、SDK 3.35.1 の型(OpenAI の OpenAPI の仕様から生成。tool のメッセージは text だけ、`max_tokens` の非推奨、
`stream_options.include_usage` など。API リファレンスのページは取得できなかったので、SDK の型の説明で確かめた)。Responses API は
developers.openai.com の Function calling(関数の定義・`function_call` と `call_id`・`function_call_output`・`strict` を省いたとき・
ストリーミングのイベント)、Reasoning(推論の項目を送り返すこと、`store: false` での暗号化された内容、`max_output_tokens` と
`incomplete`)、Conversation state(`store`・`previous_response_id`・30 日)、Latest model(モデルごとの Responses の要否)のガイドと、
SDK 3.35.1 の型(`ResponseCreateParams` の `store`・`include` の説明、`FunctionToolParam`、関数の結果の `input_text`・`input_image`・
`input_file`、`ResponseUsage`、ストリーミングのイベントの型)、SDK の `ResponsesToolRunner`(出力の項目をそのまま送り返し、
`include` に `reasoning.encrypted_content` を足す)で確かめた。Anthropic の既定のモデルの変更では、Models overview(価格・effort の
既定)、Thinking(`display: "updates"` のモデル)、Refusals and fallback(`fallbacks: "default"` のモデル)を確かめ直した。

![ダーク](gui_screenshots/28_dark_ai.png)

### 2.4 実行

![プロジェクトの実行](gui_screenshots/51_project_run.png)
![小さな箱をCPUで5反復](gui_screenshots/59_general_cpu_run.png)

- 3モードとも `run` を使い、ケースの `mode="gsis" / "cis" / "nsf"` で選ぶ。NSFはCPUで動作しGPUロックを使わない。`--min-iter`・`--vtu-every`・`--dump-every`・`--save-init`・`--sections`・`--backend`・`--precision` は追加欄を含めNSFに送らない。NSFのεはp/u/T更新ノルムで、GSISの式(59)とは定義が異なる。
- 実行ごとに `<project>/runs/YYYY-MM-DD_HHMMSS/` を作り、同じ秒なら `_2` 以降を付ける。その中に入力の写し `case.toml` と識別用の `gui_run.json` を保存し、`--out` で同じフォルダを渡す。メッシュはプロジェクト内の完成品を絶対パスで参照する。過去の実行入力は保持する。
- 使える引数は `gsis-solver --help` から読む。反復上限・収束判定・実行先・精度・表示間隔・`--no-vtu`・`--cells-csv` を指定できる。追加欄では出力先・実行先・精度の上書きと `--set` を拒否する。実行先をCPUにすればGPUの順番待ちは不要。
- **ログ**: 標準出力と標準エラー(UTF-8)をそのまま出す(エラーは赤、警告は黄)
- **グラフ**(1 秒ごとに `history.csv` を読み足す): 上は収束(cis-run は ε、pbc-run は更新量と質量の残差。「収束のグラフに描く列」で選べる)と収束判定の線。下はパッチごとの質量流量(管ケースは論文の無次元質量流量 Ṁ、入口は流入・出口は流出を正)と、論文 Table 1 の値の線(管のメッシュのときだけ)。横軸は反復か経過時間
- **状況**: 反復、ε(または更新量)、Ṁ と流入・流出の差、経過時間と 1 反復の時間、GPU のメモリ(nvidia-smi で 5 秒ごと。GPU 全体の使用量で、ほかのプログラムの分を含む)
- **停止**: 出力先に `STOP` を作り、反復終了時に summary・VTU・セルCSVを保存して停止する。60秒経っても動いていれば「強制終了…」を選べる。GUIが作ったSTOPは内容の所有トークンを確認して終了後に削除する。他者のSTOPは保持する。結果では `stopped_by_user` を「利用者が停止」と表示する。
- **出力フォルダを追う**: 実行せずに、指定した出力フォルダの `history.csv` を読んで表示する(別に起動した計算や、終わった計算を見る)。summary.json があれば、そのケースと収束判定を使う
- 終わったら「結果を開く」で結果の画面に移る

質量流量入口がある履歴では、3段目に **入口圧力 p_b [Pa]** を追加する。列は `p_b_<patch>_Pa`、複数入口は別の系列。CIS・GSIS・NSF、実行中・過去の出力・結果の履歴タブで同じグラフを使う。GSISではCISとPBCが共通のp_bを使う。入口圧力を出さない旧履歴では従来の2段の表示を保つ。

![入口圧力の履歴(CPU・5反復、未収束)](gui_screenshots/81_s3_inlet_pressure_history.png)

#### GPUの順番待ち

VRAMのC++確定見積もりが **2,000,000,000バイト以上** のGPU計算は、ソルバーを置いたリポジトリの `runs/.gpu_lock` を `mkdir` で取得してから開始する。プロジェクトをリポジトリ外に保存してもロックは同じ場所。ソルバーの祖先からリポジトリを特定できない場合は、アプリのリポジトリを使う。

![GPU順番待ち(撮影用の一時ロック)](gui_screenshots/53_gpu_wait.png)
![GPU順番待ち・ダーク](gui_screenshots/54_dark_gpu_wait.png)

- 先行計算のロックがあれば「GPUの順番待ち」を表示し、1秒ごとに再試行する。なくなると自動で開始する。「停止」で待機を取り消せる。待機・実行中は設定・保存・プロジェクト切替を止める。
- `owner.txt` はGUI名、所有トークン、ホスト、GUIのPID、計算名・出力先、開始日時を持ち、起動後にはソルバーPIDを追記する。他の担当の自由形式の `owner.txt` もそのまま表示する。
- 正常終了・失敗・起動失敗・停止・GUIの通常終了では、**このGUIが取得し、所有トークンが一致するロックだけ**を解除する。他者のものや所有情報が変わったものは保持する。
- GUIが落ちて残ったロックは、同じPC・同じGUI名でも自動削除しない。GUIのPIDが消えてもソルバーが動いている可能性があるため。表示した `owner.txt` とプロセスを確認し、所有者が計算の終了を確かめてから手動で解除する。GUIには強制解除ボタンを設けていない。
- 入力検査中と順番待ち中の「停止」は、ソルバーを起動せずに要求を取り消す。

#### 例題の管

「プロジェクト → 例題を開く…」でδ=1・10・100・1000と空の保存先を選ぶ。`cases/tube_vacuum/gsis/tube_delta*_gsis.toml` の設定と `mesh/tube_hex.pmesh` の写しから、独立したプロジェクトを作る。元のケースやメッシュは変更しない。

- 形状は「既存メッシュから」で済となる。CADは不要。メッシュのパッチ単位で境界の値を変更できるが、パッチ名・面の割当ては固定する。
- 設定や境界を変えた後は「既存メッシュを確認して使う」で実セル数・VRAMを再確認する。CADがないので四面体への再生成はしない。
- 速度格子は例題の手動値を保持し、GSISの追加設定も引き継ぐ。4条件で現行の `check-case` を確認済み。大規模な管の再計算・精度検証はG3aでは行っていない。
- 結果の一覧にはプロジェクト自身の履歴に加え、既存の `runs/kinetic/`・`runs/gsis/` も並ぶ。`runs/kinetic/tube_delta1_cis` などを読み取って比較できる。

### 2.5 結果

プロジェクトの `runs/` の履歴を新しい順に並べ、選んで読み込む。ほかの場所は「参照…」。過去の出力があれば、現在の設定が要やり直しでも結果を開ける。
タブは「3D表示」「線上の分布」「パッチ集計・壁面分布」「収束の履歴」「要約(summary.json)」。管の例題だけに「中心線(Fig.3)」「質量流量(Table 1)」と論文の参照線・管用プリセットを表示する。一般形状では領域中央の断面・線を既定とする。
一般形状の無次元圧力・密度のラベルと線上分布のCSVは `p_ref`・`rho_ref` と表記し、管の例題だけ入口の `p_in`・`rho_in` を使う。

![結果・管の既存出力](gui_screenshots/55_project_results.png)
![一般の結果・ダーク](gui_screenshots/62_dark_general_results.png)

#### パッチ集計・壁面分布

![パッチの集計と壁面の法線応力](gui_screenshots/57_patch_wall_results.png)
![パッチ集計・ダーク](gui_screenshots/58_dark_patch_wall_results.png)

- S2 の `summary.patches[]` の `area_m2`・`mass_flow_kg_s`・`heat_flow_W`・`energy_flow_W`・`force_N` を表とCSVに使う。質量・局所熱・全エネルギーは **流入が正**。力は気体がパッチに及ぼす力で、開口では運動量輸送を表す。流量の正方向を行ごとに表示する。欠測は **「ソルバーの出力にない」** と明記する。
- 新キーがない旧出力は従来の外向き正で表示する。質量は `mdot_kg_s`、無次元値のSI換算、または境界の `mass_flux × 面積`。壁の力は `momentum_flux_nd` のSI換算か境界積分を使う。
- `heat_flow_W` は局所気体系の q の積分。`energy_flow_W` は移流と仕事を含む全エネルギーで、熱と区別する。境界VTUの `energy_flux` を熱流量と同一視しない。旧出力の熱は `heat_flux_normal × 面積` がある場合のみ集計する。
- 壁の全パッチ・境界の全パッチ・個別パッチを選び、`boundary.vtu` の法線応力・せん断応力・全エネルギー流束・境界温度など、ある量をSIで色付けする。ベクトルは大きさ・x・y・zを選べる。VTUに熱流束がなければ熱流束は選択肢に出ない。
- 表には種類とMaxwell壁の適応係数 α (`accommodation`) も表示する。CSVはUTF-8 BOM付き、列名は `patch,type,accommodation,area_m2,mass_flow_kg_s,heat_flow_W,energy_flow_W,force_x_N,force_y_N,force_z_N,flow_sign,pressure_Pa,target_mass_flow_kg_s,mass_flow_relative_error`。表示の丸め前の値を保存する。

質量流量入口はパッチ集計タブの上の表に **最終 p_b [Pa]・目標 [kg/s]・最終流量 [kg/s]・相対誤差 [%]** を表示する。summaryの `mass_flow_relative_error` とCSVは比率、画面では100倍した百分率。誤差が **0.1%を超えたとき**、または誤差を確認できないときは警告し、実行終了時のログと結果上部にも表示する。GSISの流量は補正後fの値。停止や反復上限の結果は収束解として扱わない。

目標を満たすことと全体の保存性は別に確認する。`mass_flow_inlet` は入口として分類し、従来の入口・出口の差と `conservation_diagnostics` の警告を引き続き表示する。

![入口の最終値・流量誤差・壁のα](gui_screenshots/83_s3_inlet_results.png)

GSIS の `conservation_diagnostics.warning=true` なら、結果の上部と実行終了時に「残差では収束扱いだが、補正後の質量の保存がずれている(入口 x kg/s、出口 y kg/s、差 z %)」と警告する。未収束時は「未収束で」と表記する。`last_pbc_correction` の epsilon・rho・u・T と、従来の `mass_balance` の入口出口・断面ばらつきの警告も表示する。収束フラグの意味は変更しない。

VTU の読み込みは別スレッド(13.5 万セルで 2 秒前後)。3D 表示の計算(断面・等値線・流線)・線上の分布・中心線の作り直しも
別スレッドで 1 つずつ順に行い(`workers.JobRunner`。同じ格子を 2 つのスレッドで同時に触らない)、画面は出来上がったものを
描くだけにしている。設定を続けて変えたときは、最後の設定だけを計算する。

#### 3D 表示

左の設定は「場と表示」「断面」「流線」の 3 ページ。視点のボタンは「斜め」「上から」「断面に垂直」「管の付近」。「画像を保存…」で PNG。

![3D(断面と等値線)](gui_screenshots/07_results_3d_contour.png)
![3D(速度)](gui_screenshots/08_results_3d_velocity.png)

- **場と表示**: 場(p、ρ、u、T、Ma、局所 Kn、q、σ、残差)と成分(大きさ・x・y・z、σ は 6 成分)。既定は無次元
  (p/p_in、ρ/ρ_in、u/√(2RT0)、T/T0、q/(p_in√(RT0))、σ/p_in)。表示は「3D(断面と奥の壁)」「断面(断面に垂直に見る)」
  「断面と等値線」「境界面(全体)」「なし(流線だけ)」。色の範囲は「領域全体」「表示しているもの」(断面の中の範囲。
  管を横切る断面の半径方向の変化が見やすい)「指定」。「領域の輪郭を薄く表示」で領域の表面を半透明で重ねる。
  視点を変えずに場だけを替えられる
- **断面**(任意の断面): プリセット「z = 0(xy 面)」「y = 0(xz 面)」「x 一定(yz 面、既定 X = 5)」と「自由」。原点(L_ref
  単位)と法線を入力できる(入力すると「自由」になる)。「法線の向きに動かす」のスライダーで、向きを変えずに断面を
  平行に動かす(放したときに作り直す)。「平面ウィジェットで動かす」で pyvista の平面ウィジェットを出し、マウスで
  断面を動かす・傾けることができる(離したときに作り直し、原点と法線の欄に入る。対話型の 3D 表示のときだけ)。
  「3D」の表示では、既定の斜めの視点から見て断面の奥の側の壁を描く。断面に垂直に見るときは、断面と管の付近の
  重なりに合わせて拡大する

  ![任意断面(x 一定、u_x の等値線)](gui_screenshots/19_results_slice_x.png)
  ![任意断面(自由な法線、下流槽の噴流を斜めに切る)](gui_screenshots/20_results_slice_free.png)
- **流線**: セル値の速度を点へ補間し(`cell_data_to_point_data`)、VTK の流線(RK45)で積分する。種の置き方は
  「円盤」(中心・法線・半径。既定は管の入口の手前 x = 7、半径 1.5、法線 x)「線分」(点 A・B)「点の群」(球の中に乱数で。
  毎回同じ点)「断面上の 2 次元の流線」(「断面」のページの断面に速度を投影し、断面の上で積分する。種は断面のセルから
  選ぶ)。本数(種の数)、積分の向き(両方・流れの向き・逆向き)、長さの上限を選び、「流線を計算」で計算する。
  色は速さ |u|/√(2RT0)(2 次元の流線は面内の速さ)で、場のカラーマップと別のもの(cividis ↔ viridis)。
  「管(チューブ)のように描く」で太く描く。同じ設定の流線は覚えておき、場や表示を替えても計算し直さない

  ![流線(円盤の種、3D の断面と)](gui_screenshots/17_results_streamlines.png)
  ![流線だけ(領域の輪郭)](gui_screenshots/18_results_streamlines_outline.png)
  ![断面上の 2 次元の流線](gui_screenshots/21_results_streamlines_2d.png)

13.5 万セルの管の結果(`runs/kinetic/tube_delta1_cis`)での時間: 点への補間 0.1 秒、流線は種 60 個(両方の向きで 120 本・
4,110 点)0.9 秒・種 300 個(600 本・2 万点)4 秒、断面 0.01〜0.02 秒、断面上の 2 次元の流線(種 200 個)0.2 秒。
計算している間も画面は動く(20 ms ごとのタイマーの間隔は、描く時間を含めて最大 0.09 秒。試験で 0.5 秒未満を確かめている)。

#### 線上の分布

![線上の分布(管の直径、X = 5)](gui_screenshots/22_results_line_profile.png)

- 任意の 2 点 A・B(L_ref 単位)を結ぶ線の上の分布。プリセット「中心線(管の中、X = 0〜10)」「中心線(全体)」
  「管の直径(X = 5、y 方向)」「管の出口の直径(X = 9.9)」「下流槽の断面(x = 20)」と「任意」
- 点の数、描く量(p、ρ、u の各成分と大きさ、T、Ma、局所 Kn、q、σ など。最大 6 つを上下に並べる)、無次元、横軸
  (A からの距離 s・x・y・z・X)を選ぶ。値はセル値を点へ補間したもの(VTK の probe)。領域の外の点は描かない
- 「図を保存…」(PNG・SVG・PDF)、「CSV を書き出す…」(列: s, x, y, z と全部の量。列名は ASCII、例 `p/p_in`・
  `u_x/sqrt(2RT0)`・`T[K]`。領域の外は空欄)。「3D 表示に線を出す」で線を 3D 表示に重ねる
- 中心線を論文 Fig.3 と比べるのは次の「中心線(Fig.3)」のタブ(軸の近くのセル値に当てはめて r → 0 に外挿した値)。
  こちらは補間した値そのものなので、壁の近くなどで少し違う

#### 中心線・質量流量・履歴

![中心線](gui_screenshots/09_results_centerline.png)
![Table 1](gui_screenshots/10_results_table1.png)
![収束の履歴](gui_screenshots/11_results_history.png)

- **中心線(Fig.3)**: p/p_in と u_x/√(2RT0) を横軸 X = x/L_ref − 8(管の入口 0、出口 10)で描き、論文 Fig.3 のデジタイズ値(GSIS-PBC の線、CIS の記号、δ = 100 は DSMC の記号)を重ねる。δ はこの計算の δ から自動で選ぶ(選び直せる)。表に差(計算 − 論文)の RMS・最大・その位置・平均を出す(論文の点の X で計算を線形補間し、線が欠けている X < 0.15 は使わない)。「図を保存…」(PNG・SVG・PDF)、「CSV を書き出す…」(中心線と、論文 GSIS-PBC の線をこの X で補間した列。差の表は `_diff.csv`)
- **質量流量(Table 1)**: summary.json の Ṁ(入口と出口の平均)を Table 1(参照値、論文の GSIS-PBC・CIS、反復回数)と並べ、差を出す(論文の GSIS-PBC との差が ±2% 以内なら緑、外なら黄。受け入れ基準 spec §1.4)。「今の計算を『比べる計算』に加える」で複数の計算(δ の違う計算)を並べられ、中心線の図にも破線で重ねられる。CSV に書き出せる
- **収束の履歴**: 実行の画面と同じグラフ(論文の Ṁ の線付き)。**要約**: summary.json の中身

δ = 1 の CIS(`runs/kinetic/tube_delta1_cis`)で、中心線の差は論文の CIS と p で RMS 0.0054・最大 0.0087、u_x で RMS 0.023・最大 0.050(X = 9.94)と、`docs/phase2_report.md` の値と同じになる(試験で確かめている)。

### 2.6 表示モード(配色)

![ダーク(中心線)](gui_screenshots/12_dark_results_centerline.png)
![ダーク(実行)](gui_screenshots/14_dark_run_monitor.png)

- TK 標準の warm clay テーマ。色は `gsis_tools/gui/theme.py` のトークン(ライト・ダークで同じ名前の 2 組)だけから使い、Qt のパレットとスタイルシート(`qtheme.py`)、matplotlib の rcParams、3D 表示の背景・文字に当てる
- モードは「システムに合わせる」(既定、OS の設定が変わると追従)/ ライト / ダーク。ツールバーで切り替え、設定に保存する
- アクセント(テラコッタ)は主ボタン・選択・フォーカス・進捗・チェックだけに使う。グラフは系列色 6 色、連続量のカラーマップは cividis(既定)/ viridis
- その他のダークの画面: `13_dark_results_3d.png`、`15_dark_case.png`、`16_dark_mesh.png`、`23_dark_results_streamlines.png`
- 3D 表示の色の帯の題は VTK の字体で描くので、日本語と一部の記号を使わない(`|u|` は `u_mag`、ρ は `rho` にして描く)

## 3. ソルバーとの受け渡し

| 方向 | ファイル・プロセス | GUI の使い方 |
|---|---|---|
| GUI → ソルバー | ケース TOML | `project.case_document()` がメッシュの相対パスと境界を生成。旧編集窓は従来の読書きを保持 |
| GUI → ソルバー | `gsis-solver --help` | サブコマンドの一覧と、使い方の行にある引数(`--out` など)を読む |
| GUI → ソルバー | `gsis-solver check-case <toml> --json [--no-mesh] --free-vram-bytes N` | QProcess で非同期検査。成功・失敗ともJSONを読む。詳細は `docs/build.md` §10 |
| GUI → ソルバー | `gsis-solver run <toml> --out <dir> …` | 3モード共通。QProcess で起動。標準出力・標準エラーは UTF-8 として読む。終了コード 0 / 1(エラー)/ 2(引数の誤り)で説明を変える |
| ソルバー → GUI | `<out>/history.csv` | 1 秒ごとに後ろだけ読む(途中で切れた行は次回に回す。作り直されたら読み直す。実行を始める前の古いファイルは読まない) |
| ソルバー → GUI | `<out>/summary.json` | 反復、収束、停止、時間、`patches` のSI値、`reference`、`delta`、`tolerance`、`mode`、`case_file`、保存性診断。旧出力も読み取る |
| ソルバー → GUI | `<out>/fields.vtu`(run)・`cells.vtu`(旧pbc-run)・`boundary.vtu` | pyvista で読む。セル値は SI(rho, u/U, T, p, …)。ない場はリストに出さない |
| ソルバー → GUI | `<out>/cells.csv`(任意) | VTU がないときに中心線をこれから作る |
| GUI → gsis_tools | MCP engine のメッシュ生成・物理グループ・pmesh出力、`compute_quality` / `format_report` | 一般形状の四面体。管の生成器もPython APIに保持 |

`history.csv` の列の使い方:

| 列 | cis-run | pbc-run | run(フェーズ 4) |
|---|---|---|---|
| 反復 | `iteration` | `iteration` | `iteration` |
| 収束(既定で描く列) | `epsilon` | `update_p`・`update_u`・`update_T`・`residual_mass` | `epsilon`(ほかに `eps_*`・`pbc_change` なども選べる) |
| 質量流量 | `mdot_<patch>_nd`(なければ `_kg_s`) | `mdot_<patch>`(無次元) | `mdot_<patch>_nd`(運動論側、実線)と `mdot_pbc_<patch>_nd`(PBC 側、同じ色の破線。流入と流出の差には入れない) |
| 経過時間 | `time_total_s` | `elapsed_seconds` | `time_total_s` |

列は名前で探すので、知らない列は「収束のグラフに描く列」で選べるだけで、あっても困らない。`run` の列は、
開発中の `build/phase4/src/gsis-solver.exe` の出力(`runs/gsis/_diag/` の history.csv・summary.json、2026-10-16)で確かめた。

- **無次元質量流量**: ソルバーの無次元の質量流量(ρ_ref √(RT0) L_ref² 単位、外向きが正)に √(2/π) を掛けると論文の Ṁ = ṁ / (√π L_ref² p_in / √(2RT0)) になる(p_ref = p_in のとき。kg/s の列は質量流量の基準で割る)。ケースが `delta_rp` で与えられたときだけ Ṁ にし、それ以外は無次元の値のまま描く
- **中心線**: 軸の近く(r < r_max、既定 0.3 L_ref)のセルに、同じ x の層ごとに a + b r² を最小二乗で当て、r = 0 の値 a を取る(`cases/tube_vacuum/kinetic/centerline.py` と同じ。層がない四面体では x の区間ごとに a + b (x − x_c) + c r²)。VTU のセル中心から作った値は、cells.csv(FV の重心)から作った値と 1e-8 以内で一致する
- **論文との比較**: `reference/paper_2610.07658/` の `table1_tube.csv` と `fig3_*.csv` を読む。論文の線・記号は管のメッシュのとき(メッシュの metadata が `gsis_tools.geometry.tube` か、ファイル名が tube で始まる)だけ実行の画面に出す

## 4. 試験

G3bは固定版 `runs/phase8/s2/gsis-solver-s2-final.exe` を指定して、`test_gui_s2.py` のS2接続試験と全Python試験を行う。今回のコマンドと結果は `docs/phase8_g3b_report.md` に記録する。サンドボックスではuvを使わず既存環境を直接起動する。

G3cではS3固定版へ切り替え、既存S2の接続試験も `GSIS_SOLVER` の指定に従う。`test_gui_s3.py` では境界値の検査・TOMLの往復・自動速度格子のC++照合・12セル箱のCPU3反復とSTOP・複数入口の圧力表示・結果とCSV・0.1%の警告閾値を確認する。小さい箱は試験・撮影時だけ生成し、例題メニューには追加しない。数値精度の検証には `cases/general/s3/` とS3報告を参照する。

```powershell
New-Item -ItemType Directory -Force build/tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"; $env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:GSIS_SOLVER = "$PWD\runs\phase8\s3\gsis-solver-s3-final.exe"
$env:OMP_NUM_THREADS = '2'
python\.venv\Scripts\python.exe -m pytest python/tests -q -p no:cacheprovider --basetemp=build\tmp_g\pytest_g3c
```

全体試験の結果と未確認事項は `docs/phase8_g3c_report.md`。

```powershell
New-Item -ItemType Directory -Force build\tmp_g | Out-Null
$env:TEMP = "$PWD\build\tmp_g"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$env:GSIS_SOLVER = "$PWD\runs\phase8\s2\gsis-solver-s2-final.exe"
python\.venv\Scripts\python.exe -m pytest python/tests -q -p no:cacheprovider --basetemp=build\tmp_g\pytest
```

G1の結果は `docs/phase8_g1_report.md`、G2の結果・コマンドは `docs/phase8_g2_report.md`。`test_gui_general.py` はQtなし、`test_gui_general_pages.py` は6段階・CAD・設定・保存を検査する。G2は `test_gui_boundaries_mesh.py` と `test_gui_boundaries_mesh_pages.py` で、指紋の引き継ぎ、7種の境界、停止、予算超過、箱とオリフィスから現行ソルバーの `check-case` までを検査する。以下の件数は旧版の記録。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run --project python --extra gui pytest python/tests
```

- 2026-10-18(既定のモデルの変更と Responses API を足した後、`--extra gui` だけで): 287 件が通り、2 件を飛ばす(シンボリック
  リンクを作れない環境の 1 件と、本物の API の試験 1 件)。所要 約 90 秒。モデリング(AI)の試験は 115 件で、実際の API は呼ばない
  (HTTP の層の模擬)。その前(モデリング(AI)を足したとき)は 257 件で、既存の試験は `test_gui_app.py` の画面の数の 1 件だけ、
  4 → 5 に直した(`test_window_has_five_pages`)
- 以下は 2026-10-16 の記録: 127 件(既存 57 件 + GUI 70 件)。所要 30〜40 秒。extra `gui` がない環境では、GUI の試験のうち Qt・pyvista・tomlkit が要る 5 ファイルは飛ばし、既存の 57 件と残りの GUI の試験 13 件(履歴・ソルバーの呼び出し)が通る(別の仮想環境で確かめた。2026-10-16)
- 画面の試験は `QT_QPA_PLATFORM=offscreen`(試験の中でも既定にする)、3D は静止画の方式(`GSIS_GUI_3D=static`)で描く。確認の窓は差し替えて止まらないようにしている
- ソルバーを使う試験(check-case、pbc-run・cis-run の短い実行)は `build/release/src/gsis-solver.exe` がなければ飛ばす。実行は smoke ケース(128 セル)を CPU で数反復だけにし、GPU は使わない

| 試験ファイル | 確かめること |
|---|---|
| `test_gui_casefile.py` | 既存の TOML 10 個が変更なしで 1 文字も変わらない往復、値を変えたときの往復とコメントの保持、プリセット 4 つ、浮動小数点の書き方、導出量(check-case の表示と一致)、入力の検査、メッシュの相対パス、書いた TOML を check-case が通すこと |
| `test_gui_history.py` | cis-run・pbc-run・run(PBC 側の流量の列を含む)・kg/s だけの列の解析、Ṁ への換算と向き、追いかけ読み(行の途中、作り直し、古いファイル)、既存の出力の最終行が summary.json と一致 |
| `test_gui_centerline.py` | 層ごとの当てはめ(2 次の場で厳密)、区間の当てはめ(四面体の並び)、既存の `centerline.csv` との一致、VTU と cells.csv の一致、CSV の書き出し |
| `test_gui_reference.py` | Table 1 の値、Fig.3 のファイル、差の計算(範囲外の点を除く)、phase2_report の値の再現 |
| `test_gui_solver.py` | `--help` の解析、引数の組み立て(使えない引数は省く)、`run` がないときの扱い、失敗の説明 |
| `test_gui_fields.py` | 流線の種(円盤・線分・点の群)、断面のプリセット・量の名前・CSV の列名、斜めの法線の断面と等値線・「3D」で描く壁の側・色の範囲の 3 つの方式・断面を動かせる範囲、一様な流れの流線(向き・覚えておく・逆向き)、断面上の 2 次元の流線(断面の上にあること、面内の流れがないとき)、線上の標本(1 次の場で厳密)と CSV |
| `test_gui_ai_core.py` | (Qt なし)AI の設定の保存と読み込み・形式ごとの既定(既定のモデル `claude-opus-5-5`、公式かどうかで出力の上限の引数・途中の報告・切り替え・OpenAI の API の種類が変わる)・作業フォルダの既定、API キー(`Secret` の伏せ字、伏せ字の関数と log の filter、API が返す不透明な値(暗号化された推論・署名)は書き換えないこと、読み込みの順: その場の入力 → 資格情報マネージャー → 環境変数(公式の接続先だけ)、キーなし、資格情報マネージャーの失敗でキーが出ない)、ツールの定義の変換(`$ref` の展開、GUI 専用と画像のツールを外す)、AI への指示の中身、会話の保存と読み込み(キーの伏せ字)、`gui_surface` の応答の読み込み・面の境目・パッチ、エンジンの `gui_surface`(形状とメッシュの版、変わっていないときの省略、batch に入れられない) |
| `test_gui_ai_providers.py` | 形式ごとの層(Anthropic と OpenAI の Chat Completions)を `httpx2.MockTransport` で: 送ったリクエストの形(パス・ヘッダー・本文。Anthropic のキャッシュの区切り・beta・`fallbacks`・`thinking`、公式以外の接続先では beta を付けない、OpenAI の `tools`・`max_completion_tokens` / `max_tokens`・`stream_options`)、応答の読み方(ストリーミングとそうでないもの、考え、複数の呼び出し、トークン数)、結果の返し方(Anthropic は 1 つの user のメッセージ、OpenAI は呼び出しごとの tool と画像の user のメッセージ)、途中で別のモデルに切り替わった応答の送り返し方、断られた応答と出力の上限、エラー(401・404・429 の再試行・500・503・529・通信の切断・SSE の途中の error)とキーが説明に出ないこと、モデルの一覧、キーを明示して渡すこと(環境変数を使わない)、OpenAI のキーなしと公式以外への組織の ID、400 での出力の上限の引数の切り替え、読めない引数 |
| `test_gui_ai_responses.py` | OpenAI の Responses API の層を `httpx2.MockTransport` で: API の種類の選び方(自動・明示)、送ったリクエストの形(`/v1/responses`、`instructions`・`input`、関数の定義と `strict: false`、`store: false`・`include`、`max_output_tokens`、`previous_response_id` を送らない)、ストリーミングとそうでない応答の読み方(文章・推論の要約・複数の `function_call`・`call_id`・トークン数)、関数の結果(`function_call_output`、画像は `input_image`)、推論の項目を暗号化された内容ごとそのまま送り返すこと、`store: true` の設定、互換サーバー(キーなし・組織の ID を送らない・`include` を送らない)、知らない引数(`include`・`store`)を外して送り直すこと(公式の接続先では `store` を外さない)、`incomplete`(出力の上限・`content_filter`)・断られた応答・`response.failed`、エラー(401・404・500・429 の再試行・通信の切断・SSE の途中の `error`)とキーが説明に出ないこと、読めない引数、モデルの一覧 |
| `test_gui_ai_agent.py` | AI の反復と裏側(3 つの形式: Anthropic、OpenAI の Chat Completions と Responses): ツールのエラーを返して直させる、1 回の応答の複数の呼び出し、上限、API の応答待ちの停止、ツールの実行中の強制停止(起動し直して自動保存に戻る)、サーバーの異常終了からの復旧、断られた応答、API のエラーと再送、キーがない・401、確認のモード、画像(送る・送らない)、GUI での操作の知らせ、出力の上限で切れた呼び出し、読めない引数・未知のツール、会話の保存と読み込み(形式・API の種類が違えば開かない)、モデルの一覧。本物の MCP サーバーで管を 3 つの形式で作る、メッシュの生成の途中の強制停止(slow)、試験用の小さなサーバーのプロセスを落として復旧する(`fake_mcp_server.py`)、GUI 専用のツールの `_meta`、子プロセスに API キーの環境変数を渡さないこと |
| `test_gui_ai_secrets.py` | ダミーのキーで管を作る反復を 3 つの形式(Anthropic、OpenAI の Chat Completions と Responses)で通し(SDK の DEBUG のログも集める)、作業フォルダのすべてのファイル・保存した会話・設定・ログ・画面への知らせにキーがないこと、資格情報マネージャーに書かないこと |
| `test_gui_ai_page.py` | 画面(offscreen): 形状内のAIタブと共通検査への受渡し、模擬の AI で管を作る(会話の欄の記録と折りたたみ、3D 表示、パッチの一覧、トークン数、AI形状・手順・会話の採用)、断面と面の番号、面の選択・差し込み・手でのパッチ・元に戻す、クリックの位置からの面の選択(VTK のセルの picker をオフスクリーンで。選べない線は無視)、ツールを手で実行、設定の画面(キーは伏せ字、欄に戻さない、設定ファイルに書かない、選んだときだけ資格情報マネージャー、OpenAI の既定のモデルは空)、モデルの一覧の取得、停止と再送のボタン、会話の保存・リセット・読み込み、OpenAI 形式(キーなしのローカルのサーバー)、OpenAI の API の種類(設定の画面の「使う API」の表示、会話の形が変わるときのリセット、Responses で送って推論の項目を送り返す、キーを設定ファイルと作業フォルダに書かない)と「※」の注記、AI の文章の HTML を使わないこと、ダーク |
| `test_gui_ai_live.py` | 本物の API で 1 回だけ(既定は飛ばす。下の「本物の API の試験」) |
| `test_gui_app.py` | 流用した部品: テーマ(スタイルシート・パレット・matplotlib・設定の保存)、run がないときの注意、pbc-run の実行と履歴、停止、結果の読み込み(解析解を入れた合成データと既存の出力)、Table 1 と中心線の差、書き出し、3D の静止画、任意の断面・流線(3 次元と断面上の 2 次元)・線上の分布と CSV を画面の操作から通す、13.5 万セルの結果で流線を計算している間に画面が固まらないこと(20 ms のタイマーの間隔が 0.5 秒未満)、色の帯の題の字の置き換え |

**本物の API の試験**(料金がかかり、指示とツールの結果が外部に送られるので、明示したときだけ動く。開発の中では実行していない):

```powershell
# Anthropic 形式(キーは環境変数 ANTHROPIC_API_KEY。モデルは既定 claude-opus-5-5。変えるなら $env:GSIS_AI_LIVE_MODEL)
$env:GSIS_AI_LIVE_TEST = "anthropic"
uv run --project python --extra gui pytest python/tests/test_gui_ai_live.py -s
# OpenAI 形式の Responses API(キーは OPENAI_API_KEY、モデルは必須。store: false で送る)
$env:GSIS_AI_LIVE_TEST = "openai-responses"; $env:GSIS_AI_LIVE_MODEL = "<モデル名>"
uv run --project python --extra gui pytest python/tests/test_gui_ai_live.py -s
# OpenAI 形式の Chat Completions API(互換サーバーは GSIS_AI_LIVE_BASE_URL、キーなしは GSIS_AI_LIVE_NO_KEY=1)
$env:GSIS_AI_LIVE_TEST = "openai-chat"; $env:GSIS_AI_LIVE_MODEL = "<モデル名>"
uv run --project python --extra gui pytest python/tests/test_gui_ai_live.py -s
```

`GSIS_AI_LIVE_TEST = "openai"` は API の種類を自動で選ぶ(公式の接続先は Responses、ほかは Chat Completions)。

1 辺 10 mm の立方体を作って `measure` で確かめる短い指示を 1 回送り(ツールの上限 12)、AI が `add_box`(か `batch`)と `measure` を
呼んで終わること、最後の文章があることを確かめ、使ったトークン数を出す。

## 5. 画面写真

次のコマンドで32–43のうち39を除く11枚と、G2の44–50の7枚を生成する。39はG1の仮画面の記録として保持する。CADは一時フォルダで作り、本物のAPI・ソルバーは呼ばない。

```powershell
$env:TEMP = "$PWD\build\tmp_g"
$env:TMP = $env:TEMP
python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots
```

ライト・ダークの形状、設定、自動・手動、上級、境界、入力検査、一般メッシュ、VRAM警告、外部流、AIタブを含む。
01–31は以前のGUI・AIの記録として保持する。
従来のAIだけの模擬デモは `python -m gsis_tools.gui.screenshots_ai` で撮れる。

G3aの実行・順番待ち・結果の写真51–62(ライト・ダーク各6枚)は次で生成する。
既存の管の出力を読み、小さな箱だけCPUで5反復計算する。GPU計算は起動せず、順番待ちは一時フォルダの模擬ロックを使う。本物の `runs/.gpu_lock` は操作しない。

```powershell
python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots_g3a
```

G3bの63–74は、入力検査(メッシュ前・後)、停止要求・保存後、SIパッチ表、保存性警告のライト・ダーク各6枚。S2固定版と128セル・64速度点の箱をCPUで使用し、本物のGPUロックを触らない。撮影の入力・出力は `TEMP` 以下へ残す。**73–74だけは保存性診断値を模擬した表示例**で、画面内にも明記する。

```powershell
python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots_g3b --solver runs/phase8/s2/gsis-solver-s2-final.exe
```

![S2入力検査・メッシュ前](gui_screenshots/63_s2_check_settings.png)
![S2入力検査・メッシュ込み](gui_screenshots/65_s2_check_mesh.png)
![STOPによる停止要求](gui_screenshots/67_s2_stop_request.png)
![停止後の保存](gui_screenshots/69_s2_stopped.png)
![SIパッチ表・ダーク](gui_screenshots/72_dark_s2_patch_si.png)
![保存性警告の表示例・診断値は模擬](gui_screenshots/73_s2_conservation_example.png)

G3cの75–86はMaxwell壁、質量流量入口、S3入力検査、p_b履歴、入口最終値とパッチ表、NSFの注意。各画面をライト・ダークで撮影する。12セル・8³速度点の箱をCPUで5反復だけ解き、未収束時の実際の数値を表示する。模擬の結果値は使わない。撮影用ロックは一時領域へ向ける。

```powershell
python\.venv\Scripts\python.exe -m gsis_tools.gui.screenshots_g3c --solver runs/phase8/s3/gsis-solver-s3-final.exe
```

![Maxwell壁・ダーク](gui_screenshots/76_dark_s3_maxwell_wall.png)
![質量流量入口・ダーク](gui_screenshots/78_dark_s3_mass_flow_inlet.png)
![S3入力検査・ダーク](gui_screenshots/80_dark_s3_check_case.png)
![入口圧力履歴・ダーク](gui_screenshots/82_dark_s3_inlet_pressure_history.png)
![入口最終値・ダーク](gui_screenshots/84_dark_s3_inlet_results.png)
![NSF注意・ダーク](gui_screenshots/86_dark_s3_nsf_maxwell_warning.png)

## 6. 構成

| モジュール | 内容 |
|---|---|
| `app.py`・`main_window.py`・`context.py` | 起動、メインウィンドウ(6段階とプロジェクトメニュー)、画面で共有する状態(ソルバー、テーマ、設定) |
| `pages/geometry_page.py`・`settings_page.py` | 形状(CAD・AI)と設定 |
| `boundaries.py`・`pages/boundaries_page.py` | 境界の検査、指紋の照合、面の選択と割り当て |
| `general_mesh.py`・`pages/mesh_page.py` | 一般形状のメッシュ生成・停止、品質、VRAMの検査 |
| `project.py`・`general_settings.py`・`geometry.py` | Qtなしのプロジェクト・状態・case生成、導出量・速度格子・VRAM、CADの検査 |
| `pages/ai_page.py`・`run_page.py`・`results_page.py` | AI、プロジェクトの実行と結果。旧 `case_page.py` はCLI互換用に保持 |
| `project_runs.py`・`examples.py` | 実行入力の写し、実行コマンドの記録、所有者を確認するGPUロック、管の例題の複製 |
| `patch_results.py`・`pages/boundary_results.py` | パッチのSI集計とCSV、boundary.vtuの壁面分布 |
| `pages/ai_settings_dialog.py` | モデリング(AI)の設定の画面 |
| `ai/`(Qt なし) | モデリング(AI)の裏側: `settings.py`(設定)、`secrets.py`(API キー・伏せ字)、`types.py`(形式によらない型)、`providers/`(形式ごとの層: `anthropic_provider.py`・`openai_responses_provider.py`(Responses)・`openai_provider.py`(Chat Completions))、`mcp_client.py`(MCP サーバーの子プロセスとの接続)、`session.py`(自動保存・起動し直し・3D 表示の更新)、`agent.py`(AI の反復)、`backend.py`(別スレッドの asyncio でまとめる)、`conversation.py`(会話の保存)、`prompt.py`(AI への指示)、`tooldefs.py`(ツールの定義の変換)、`surface.py`(3D 表示のデータ)、`demo.py`(模擬の AI) |
| `pages/results_3d.py`・`line_profile.py` | 結果の画面の「3D 表示」(場と表示・断面・流線)と「線上の分布」のタブ |
| `widgets/` | 入力欄・ログ・キーと値の表(`common.py`)、matplotlib の図(`plots.py`)、履歴のグラフ(`history_plot.py`)、3D 表示(`vtk_view.py`。点の文字と、選べない描画を足した)、会話の欄(`chat_view.py`) |
| `theme.py`・`qtheme.py` | 色のトークンと matplotlib(Qt なし)、Qt のパレット・スタイルシート・モードの切り替え |
| `casefile.py` | ケース TOML の読み書き・プリセット・導出量・検査(Qt なし) |
| `history.py`・`centerline.py`・`reference.py`・`results.py`・`solver.py`・`paths.py` | 履歴、中心線、論文データ、出力フォルダ、ソルバーの呼び出し、パス(Qt なし。試験で直接使う) |
| `meshgen.py`・`fields.py` | メッシュ生成と品質・3D 用のデータ、VTU の場・任意の断面・等値線・流線・線上の標本と CSV(pyvista。Qt なし) |
| `workers.py` | 別スレッドの処理(QThread)、結果の画面の計算を 1 つずつ順に行う `JobRunner`、logging の受け渡し |
| `screenshots.py`・`screenshots_ai.py` | スクリーンショットの作成(G1/G2の32〜50、従来AIの24〜31) |
| `screenshots_g3b.py`・`screenshots_g3c.py` | S2/S3接続画面の撮影。G3cの小さな試験用入力の生成 |

## 7. 未実装の点・既知の制約

- 四面体GSISの精度はS1で未解決。GUIはCISを既定にし、GSIS選択時は警告する。Maxwell壁の傾斜面での速度格子依存性、NSF単独のすべり・温度跳躍の精度もS3の制約が残る。
- C++のVRAM見積もりに渡す空きは設定の仮定値であり、GPUの空きの実測ではない。Python表示は概算。非HeプリセットはBird (1994) 表 A1、273 Kの統括役提供値。
- IGESは面を縫い合わせる。閉じない面・複数の立体は拒否する。OCCで読み込めない破損CADの自動修復はしない。

- S3 の固定版 `runs/phase8/s3/gsis-solver-s3-final.exe` で新境界を含む3モードのCPU短時間実行を確認した。G3cではGUI接続を検証し、大規模計算・GPU実行・任意形状での数値精度は検証していない。
- 結果の 3D 表示に、等値面と境界面の値(`boundary.vtu` の質量流束など)の表示はない。流線・任意の断面・線上の分布は v0.2 で足した
- 断面の色はセルの値(セルごとに一定)で、等値線・流線・線上の分布は点へ補間した値から作る。そのため等値線はセルの大きさの段差を含み、壁に接するセルの値は壁の上の値ではない
- 平面ウィジェットは対話型の 3D 表示のときだけ使える(画面を出さない方式・静止画の方式では使えない)。ウィジェットの矢印は、正面から見ると少し大きく見える
- 分布関数の断面表示(spec §6 の任意の機能)はない(ソルバー側にプローブ出力が要る)
- STOP後60秒で選べる強制終了やGUIの異常終了では、出力が欠ける場合がある。GUIを強制終了すると自分のSTOPが残る可能性があるが、次の実行は新しい出力先を使う。
- 実行の画面は 1 つの計算だけを扱う(複数の計算を並べて実行・監視しない)。ソルバーの標準出力は、この画面から起動したときだけ表示できる(「出力フォルダを追う」は history.csv だけ)
- 一般形状は四面体、管の例題は既存六面体の複製。例題の再生成にはCADがないため対応しない。管の生成器はPython APIに保持する
- 3D の表示は 13.5 万セルで実用的な速さ(VTU の読み込みと中心線 2 秒前後、断面 0.02 秒、流線は種 60 個で 0.9 秒・300 個で 4 秒)。さらに大きいメッシュと、種が数千個の流線は確かめていない(別スレッドなので画面は固まらないが、時間はかかる)
- Windows 11 だけで確かめた。Linux では `QT_QPA_FONTDIR`(offscreen のフォント)などの扱いを確かめていない
- **モデリング(AI)**:
  - 本作業の試験はHTTPの模擬。以前の版ではユーザーがAnthropic形式とResponses APIの実動を確認済み。Chat Completionsと互換サーバーの実動は未確認。本物のAPIの試験は§4の明示指定で動く
  - OpenAI の Responses API は、関数のツール(MCP のツール)だけを使う。OpenAI のサーバー側のツール(Web 検索など)、
    `previous_response_id` と Conversations API、バックグラウンドの実行、推論の要約の既定の要求(推論のモデルでないと 400 に
    なるので、追加の引数で頼む)は使わない。Chat Completions で GPT-6 Sol・GPT-6 Luna のツールを使うときは、追加の引数に
    `{"reasoning_effort": "none"}` が要る(Latest model のガイド)。OpenAI 互換のサーバーは、ツールの呼び出し(特にストリーミングの
    断片)・`stream_options`・出力の上限の引数・Responses の `store`・`include` の対応がまちまちで、自動で直すのはこれらの引数
    だけ。ツールの呼び出しに対応していないモデルでは使えない
  - 面のクリックでの選択は対話型の 3D 表示のときだけ(画面を出さない方式・静止画の方式では面の一覧で選ぶ)。3D 表示は表面
    (粗い面のメッシュかメッシュの表面)で、体積のメッシュの中は描かない(断面は表面を切ったもの)
  - 強制停止と異常終了からの復旧では、実行中の手順の変更とメッシュは失われる(自動保存は手順の履歴で、メッシュは入らない)
  - システムの指示とツールの定義は会話ごとに固定する。画像を送るか・ツールの上限の文・作業フォルダを変えたら、会話をリセットしてから
    効く。形式と OpenAI の API の種類は会話の途中では変えられない(保存した会話も、同じ形式と API の種類でだけ開ける)
  - 1 つの画面で扱う会話と MCP サーバーは 1 つずつ。会話の自動保存(`.gsis_gui/conversation_autosave.json`)は、起動したときに
    自動では開かない(「会話を開く…」で開ける)
  - OS の資格情報マネージャーへの保存は、試験では差し替えた(本物の資格情報マネージャーには書いていない)

## 8. ソルバー側への要望(統括役・フェーズ 4 の担当へ)

1. (S2・G3bで接続済み)STOPによる停止で summary.json・VTU・セルCSVを保存する。
2. `pbc-run` の summary.json にも、cis-run・run と同じ `reference`(無次元化の基準量)・`delta`・`tolerance` を入れてほしい(今は GUI がケース TOML から計算している。ケースが動かされると無次元化できない)
3. history.csv に GPU のメモリの列があると、nvidia-smi(GPU 全体の値で、ほかのプログラムの分を含む)より正確に出せる
4. (確認済み)`run` の引数の名前が cis-run と同じ(`--out`・`--max-iter`・`--tol`)で、history.csv に `epsilon`・`mdot_<patch>_nd` があるので、GUI はそのまま使える。この形を保ってほしい
