# GUI(gsis-gui、試作 v0.2)

- 版: 0.2 / 2026-10-16(v0.1 へのユーザーの意見を受けて、結果の画面に流線・任意の断面・線上の分布を足した)
- 2026-10-18: 画面「モデリング(AI)」を足した(§2.3。自然言語の指示で、AI が gmsh の MCP ツールで計算領域を作り、パッチを付け、`.pmesh` まで書き出す)
- 2026-10-18(追加): Anthropic 形式の既定のモデルを `claude-opus-5-5` にした(ユーザーの決定)。OpenAI 形式に Responses API を足した(Chat Completions API と選べる。§2.3.4・§2.3.8)。画面の画像 01〜31 を、5 つの画面の今の GUI で撮り直した(§5)
- 仕様: `docs/spec.md` §4.1・§4.6・§6
- 置き場所: `python/src/gsis_tools/gui/`(サブパッケージ `gsis_tools.gui`)、試験は `python/tests/test_gui_*.py`、画面の画像は `docs/gui_screenshots/`

管ケース(円管 → 真空)の一連の流れ ── ケース設定 → メッシュ → 実行 → 結果の比較 ── を画面で通すための試作。計算領域は、管のメッシュの画面のほか、モデリング(AI)の画面で AI に作らせることもできる。
ソルバー `gsis-solver` とは、ファイル(ケース TOML・メッシュ・結果)とサブプロセスだけでやり取りする。GUI がなくても、
すべての機能は CLI(`gsis-solver`・`gsis-tools`)とスクリプトで使える。

## 1. 起動

リポジトリのルートで実行する。初回は extra `gui` の依存を入れるので少し時間がかかる。

```powershell
uv run --project python --extra gui gsis-gui
uv run --project python --extra gui gsis-gui --case cases\tube_vacuum\kinetic\tube_delta1_cis.toml
uv run --project python --extra gui gsis-gui --results runs\kinetic\tube_delta1_cis --theme dark
```

| 引数 | 内容 |
|---|---|
| `--theme system\|light\|dark` | 表示モード(既定は前回の選択、初回は `system` = OS の設定に合わせる) |
| `--case FILE.toml` | 起動時にケースを開く |
| `--results RUN_DIR` | 起動時に出力フォルダを読み込み、結果の画面を出す |
| `--page case\|mesh\|ai\|run\|results` | 最初に出す画面(`ai` はモデリング(AI)) |
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
| `GSIS_GUI_3D` | 3D の方式: `interactive`(既定)/ `static`(オフスクリーンの静止画)/ `none` |
| `GSIS_ROOT` | リポジトリのルート(既定はパッケージの場所から探す) |
| `GSIS_GUI_AI_WORKSPACE` | モデリング(AI)の既定の作業フォルダ(設定で作業フォルダを決めていないとき)。なく、`GSIS_GUI_SETTINGS` があれば、その設定ファイルの隣の `ai_workspace`(試験とスクリーンショットで、利用者の作業フォルダに触らないため)。どちらもなければ `<リポジトリ>/mcp_workspace/gui` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | モデリング(AI)の API キー(公式の接続先にだけ使う。§2.3.5) |

## 2. 画面

左の一覧で 5 つの画面を切り替える。上のツールバーに、使っているソルバー(と使える実行のサブコマンド)、ソルバーの選択、表示モードがある。

### 2.1 ケース設定

![ケース設定](gui_screenshots/01_case.png)

- 管ケースの TOML(`docs/spec.md` §4.6)を節ごとの入力欄で編集する: `[case]`・`[gas]`・`[conditions]`・`[[boundary]]`・`[velocity_grid]`・`[gsis]`・`[kinetic]`・`[macro]`・`[output]`
- **プリセット**: He 管 δ_rp = 1 / 10 / 100 / 1000(論文 §4.1: He、管半径 1 mm、T0 = 273.15 K、30×24×24 の速度格子 [−10, 10] × [−8, 8]²、GSIS は n_cis = 5・max_pbc = 10・cfl_pbc = 500・tolerance = 1e-5、メッシュは `cases/tube_vacuum/mesh/tube_hex.pmesh`)。mode は gsis / cis を選ぶ。プリセットは新しいケース(保存するまでファイルはない)
- **導出量**(右上): R、μ0、√(2RT0)、p_in、ρ_in、Kn、δ、平均自由行程、質量流量の基準、分布関数のメモリ。C++ の `derive_quantities` と同じ式で画面が計算する(check-case の表示と有効数字 6 桁で一致することを試験で確かめている)
- **検査**: 入力欄の誤り(空欄、負の値、境界条件に要る値の不足、メッシュにないパッチ、境界条件のないパッチ)はその場で表示する。「検査(check-case)」と「保存」は `gsis-solver check-case` を実行して結果を右下に出す。保存は check-case が通ってから書く(エラーなら保存するか尋ねる)
- **読み書き**: 元のファイルのコメント・並び・書き方を保つ(値を変えたキーだけを書き換える。変えなければ 1 文字も変わらない)。`[gsis]` は、既定値(5、10、500、1e-5)と同じで元のファイルにない項目は書かない。境界条件の種類に要らない値(灰色の欄)は書かない
- メッシュのパスは TOML の場所からの相対パスで書く(C++ と同じ規則)。別の場所に保存するときは相対パスを直す
- `[kinetic]`・`[macro]` は既知のキーを一覧から選べる(型・既定値・説明を出す。知らないキーは黄色で示す。ソルバーはエラーにする)

### 2.2 メッシュ

![メッシュ(断面)](gui_screenshots/02_mesh_section.png)
![メッシュ(境界パッチ)](gui_screenshots/03_mesh_patches.png)

- `gsis_tools.geometry.tube` の `generate_tube_hex`(構造六面体、O 型断面)・`generate_tube_tet`(gmsh の四面体)で管メッシュを作る。設定は `gsis-tools mesh tube` と同じ(目標セル数、管半径、六面体の n_theta・ds_wall・ds_mouth・h_tube・max_size・growth・refine、四面体の max_size・h_tube・gradient・アルゴリズム・スレッド数)。同じメッシュを CLI で作るコマンドも表示する
- 生成と読み込みは別スレッド(画面は固まらない)。四面体は gmsh をメインスレッドで先に初期化してから別スレッドで作る
- **品質**: セル数、要素の内訳、セル寸法 h_V(R 単位)、最大辺、辺の比、非直交角(平均・99%・最大)、歪み、体積比、体積(円柱の値との差)、領域別(上流槽・管・下流槽)のセル数と h_V、pmesh §4 の検査。**パッチ**: 面の数と面積(円の面積との差)
- **3D 表示**: 境界パッチを色分けした表面(入口は青、出口は緑、壁は灰緑)。「断面」は z ≤ 0 の側のセル(断面にかかるセルを含む)を見せ、断面のセルの形が分かる。視点は「斜め」「上から」「管の付近」
- 既存の `.pmesh` を開ける(metadata から設定を読み戻す)。保存は `.pmesh` と品質レポート(`<名前>_quality.md`)、必要なら ParaView 用の VTU。「ケースに使う」で、保存したメッシュをケース設定のメッシュにする

### 2.3 モデリング(AI)

自然言語で指示すると、AI(LLM)が gmsh の MCP ツール(`docs/mcp.md`、31 個)を使って計算領域の形状を作り、境界パッチを
付け、`.pmesh` まで書き出す。ツールで形状・パッチ・メッシュが変わるたびに、3D 表示がその場で更新される。
AI は API キーで動かし、Anthropic 形式(Messages API)と OpenAI 形式(Responses API と Chat Completions API。OpenAI 互換の
サーバーを含む)に対応する。接続先(base URL)・モデル・API キー・OpenAI の API の種類は設定で選ぶ。

![1 回目の指示: 形状とパッチ(面の番号を表示、batch の記録を開いた)](gui_screenshots/24_ai_shape_patches.png)
![2 回目の指示: メッシュと書き出し(メッシュの画面で開くボタン)](gui_screenshots/25_ai_mesh_export.png)

画像は模擬の AI(決まった応答を返す HTTP の模擬。実際の API は呼んでいない)が、本物の MCP サーバーのツールで管(論文 §4.1)の
計算領域を 2 回の指示で作ったところ(`python -m gsis_tools.gui.screenshots_ai`、§5)。トークン数は模擬の値。

#### 2.3.1 使い方

1. 「設定…」で形式・接続先・モデル・API キーを選ぶ(§2.3.4・§2.3.5)。環境変数 `ANTHROPIC_API_KEY` があり、公式の接続先を
   使うなら、そのままでよい(既定のモデルは `claude-opus-5-5`)。OpenAI 形式はモデルを選ぶ(キーは `OPENAI_API_KEY` か入力)
2. 下の欄に指示を書いて「送る」(Ctrl+Enter)。例: 「半径 1 mm・長さ 10 mm の管の上流に半径 8 mm・長さ 8 mm、下流に半径
   4 mm・長さ 4 mm の槽を付けた計算領域を作り、x = 0 を inlet、x = 22 を outlet、残りを wall にして tube/tube.pmesh に
   書き出して」
3. AI は形状を作り(batch で検算付き)、`measure`・`check_geometry` で確かめ、パッチを付け、付いていない面がないことを確かめ、
   メッシュを作って `.pmesh` を書き出す。会話の欄に AI の文章とツールの呼び出しの記録(▶ で開くと引数と結果)が出る
4. 書き出すと、会話の欄に「メッシュの画面で開く」が出る(上の「書き出したメッシュを開く」も同じ)。メッシュの画面で品質と
   パッチを確かめ、「ケースに使う」でケース設定のメッシュにする
5. 続けて指示できる(会話は続く)。会話を最初からにするときは「会話のリセット」(モデルの状態はそのまま)

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

![実行(既存の出力を追う)](gui_screenshots/04_run_monitor.png)
![実行(smoke ケースを CPU で実行)](gui_screenshots/05_run_smoke_cpu.png)

- `gsis-solver` のサブコマンド `run`(フェーズ 4)・`cis-run`・`pbc-run` を QProcess で起動・停止する。作業ディレクトリはリポジトリのルート。`run` があればそれを既定にする
- 使えるサブコマンドと引数は `gsis-solver --help` から読む。**ないサブコマンド**は一覧に「このソルバーにはない」と出し、選ぶと注意を表示し、実行しようとすると理由(`run` はフェーズ 4 で追加予定)を窓で知らせて起動しない。ソルバーを作り直したら「サブコマンドを読み直す」
  ![サブコマンドがないとき(いまのソルバーには 3 つともあるので、pbc-run のない古いソルバーを装って撮った)](gui_screenshots/06_run_missing_subcommand.png)
- 引数: 出力先 `--out`(既定はソルバーと同じ `runs/kinetic/<名前>` など)、反復の上限(`--max-iter` / `--iterations`)、収束判定(`--tol` / `--tolerance`)、実行先・精度(cis-run)、表示の間隔、`--no-vtu`、`--cells-csv`、追加の引数。使い方の行にない引数は渡さず、その旨を出す。出力先に前の結果があれば上書きするか尋ねる
- **ログ**: 標準出力と標準エラー(UTF-8)をそのまま出す(エラーは赤、警告は黄)
- **グラフ**(1 秒ごとに `history.csv` を読み足す): 上は収束(cis-run は ε、pbc-run は更新量と質量の残差。「収束のグラフに描く列」で選べる)と収束判定の線。下はパッチごとの質量流量(管ケースは論文の無次元質量流量 Ṁ、入口は流入・出口は流出を正)と、論文 Table 1 の値の線(管のメッシュのときだけ)。横軸は反復か経過時間
- **状況**: 反復、ε(または更新量)、Ṁ と流入・流出の差、経過時間と 1 反復の時間、GPU のメモリ(nvidia-smi で 5 秒ごと。GPU 全体の使用量で、ほかのプログラムの分を含む)
- **停止**: プロセスを終了させる(Windows のコンソールのプログラムは穏やかな終了の要求を受けないため kill)。出力は途中まで残る
- **出力フォルダを追う**: 実行せずに、指定した出力フォルダの `history.csv` を読んで表示する(別に起動した計算や、終わった計算を見る)。summary.json があれば、そのケースと収束判定を使う
- 終わったら「結果を開く」で結果の画面に移る

### 2.5 結果

出力フォルダ(`runs/` の下を探して新しい順に並べる。ほかの場所は「参照…」)を読む。タブは「3D 表示」「線上の分布」
「中心線(Fig.3)」「質量流量(Table 1)」「収束の履歴」「要約(summary.json)」。

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
| GUI → ソルバー | ケース TOML | 画面で書く(tomlkit。コメントを保つ)。check-case には一時フォルダの写し(メッシュは絶対パス)を渡す |
| GUI → ソルバー | `gsis-solver --help` | サブコマンドの一覧と、使い方の行にある引数(`--out` など)を読む |
| GUI → ソルバー | `gsis-solver check-case <toml>` | 出力をそのまま表示。終了コード 0 で合格 |
| GUI → ソルバー | `gsis-solver run\|cis-run\|pbc-run <toml> --out <dir> …` | QProcess で起動。標準出力・標準エラーは UTF-8 として読む。終了コード 0 / 1(エラー)/ 2(引数の誤り、「知らないサブコマンド」を含む)で説明を変える |
| ソルバー → GUI | `<out>/history.csv` | 1 秒ごとに後ろだけ読む(途中で切れた行は次回に回す。作り直されたら読み直す。実行を始める前の古いファイルは読まない) |
| ソルバー → GUI | `<out>/summary.json` | 反復、収束、時間、`mass_flow.M_dot`(cis-run・run)または `M_dot`(pbc-run)、`patches`(パッチ名と種類)、`reference`(無次元化の基準量。cis-run・run にあり、pbc-run にはない)、`delta`、`tolerance`、`mode`(run の gsis / cis)、`case_file` |
| ソルバー → GUI | `<out>/fields.vtu`(cis-run)・`cells.vtu`(pbc-run)・`boundary.vtu` | pyvista で読む。セル値は SI(rho, u/U, T, p, …)。ない場はリストに出さない |
| ソルバー → GUI | `<out>/cells.csv`(任意) | VTU がないときに中心線をこれから作る |
| GUI → gsis_tools | `generate_tube_hex` / `generate_tube_tet`、`read_pmesh` / `write_pmesh`、`compute_quality`、`format_report` | 関数を直接呼ぶ(メッシュの画面) |

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
| `test_gui_ai_page.py` | 画面(offscreen): 5 つの画面、模擬の AI で管を作る(会話の欄の記録と折りたたみ、3D 表示、パッチの一覧、トークン数、書き出したメッシュをメッシュの画面で開く)、断面と面の番号、面の選択・差し込み・手でのパッチ・元に戻す、クリックの位置からの面の選択(VTK のセルの picker をオフスクリーンで。選べない線は無視)、ツールを手で実行、設定の画面(キーは伏せ字、欄に戻さない、設定ファイルに書かない、選んだときだけ資格情報マネージャー、OpenAI の既定のモデルは空)、モデルの一覧の取得、停止と再送のボタン、会話の保存・リセット・読み込み、OpenAI 形式(キーなしのローカルのサーバー)、OpenAI の API の種類(設定の画面の「使う API」の表示、会話の形が変わるときのリセット、Responses で送って推論の項目を送り返す、キーを設定ファイルと作業フォルダに書かない)と「※」の注記、AI の文章の HTML を使わないこと、ダーク |
| `test_gui_ai_live.py` | 本物の API で 1 回だけ(既定は飛ばす。下の「本物の API の試験」) |
| `test_gui_app.py` | 画面の組み立てと切り替え、テーマ(スタイルシート・パレット・matplotlib・設定の保存)、ケース設定の入力欄を通した往復と保存、プリセット、check-case(合格と不合格)、run がないときの注意、pbc-run の実行と履歴、停止、六面体・四面体のメッシュ生成と保存、結果の読み込み(解析解を入れた合成データと既存の出力)、Table 1 と中心線の差、書き出し、3D の静止画、任意の断面・流線(3 次元と断面上の 2 次元)・線上の分布と CSV を画面の操作から通す、13.5 万セルの結果で流線を計算している間に画面が固まらないこと(20 ms のタイマーの間隔が 0.5 秒未満)、色の帯の題の字の置き換え |

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

## 5. スクリーンショットの作り方

```powershell
uv run --project python --extra gui python -m gsis_tools.gui.screenshots                 # 画面を出さずに撮る(既定。3D は静止画の方式)
uv run --project python --extra gui python -m gsis_tools.gui.screenshots --interactive   # 通常の表示で窓を出して撮る
```

`docs/gui_screenshots/` の 01〜23 は、画面を出さない方式(Qt の offscreen、3D は VTK のオフスクリーンの静止画)で撮った
(2026-10-18 に、左の一覧が 5 つの画面の今の GUI で撮り直した。窓は出ず、1 分ほどで終わる。字体は `C:\Windows\Fonts` を使うので、
通常の表示と同じ字になる)。06(ないサブコマンドの表示)は、いまのソルバーには `run`・`cis-run`・`pbc-run` がそろっているので、
`pbc-run` のない古いソルバーを装って撮る。
平面ウィジェットは対話型の 3D 表示だけのものなので、画像にはない。データは `runs/` の既存の出力
(`runs/kinetic/tube_delta1_cis`・`tube_delta10_cis`、`runs/macro/tube_hex_d100_nsf`・`tube_hex_d1000_nsf`)を読むだけで、
実行の画像(05)は smoke ケースの cis-run を CPU で 40 反復だけ実行した(出力は一時フォルダ)。

モデリング(AI)の画像(24〜31)は別のスクリプトで撮る(2026-10-18。窓は出ず、20 秒ほど):

```powershell
uv run --project python --extra gui python -m gsis_tools.gui.screenshots_ai                # 画面を出さずに撮る
uv run --project python --extra gui python -m gsis_tools.gui.screenshots_ai --interactive  # 窓を出して撮る
```

模擬の AI(`gsis_tools.gui.ai.demo`。HTTP の層の模擬で、実際の API は呼ばない)が、本物の MCP サーバーのツールで管の計算領域を
2 回の指示で作る(形状とパッチ → メッシュと書き出し。メッシュは size_max 0.8 mm、管の中 0.25 mm、約 3.5 万セル)。形式は
Anthropic 形式で、既定のモデル `claude-opus-5-5`。設定の画面は、Anthropic 形式(27)、OpenAI 形式の接続(29。API の種類は自動で
Responses)、応答のタブ(30)、作業のタブ(31。会話の途中で開いた)を撮る。API キーはダミーで、設定の画面では伏せ字。設定と
作業フォルダは一時フォルダ。

## 6. 構成

| モジュール | 内容 |
|---|---|
| `app.py`・`main_window.py`・`context.py` | 起動、メインウィンドウ(5 画面とツールバー)、画面で共有する状態(ソルバー、テーマ、設定) |
| `pages/case_page.py`・`mesh_page.py`・`ai_page.py`・`run_page.py`・`results_page.py` | 5 つの画面 |
| `pages/ai_settings_dialog.py` | モデリング(AI)の設定の画面 |
| `ai/`(Qt なし) | モデリング(AI)の裏側: `settings.py`(設定)、`secrets.py`(API キー・伏せ字)、`types.py`(形式によらない型)、`providers/`(形式ごとの層: `anthropic_provider.py`・`openai_responses_provider.py`(Responses)・`openai_provider.py`(Chat Completions))、`mcp_client.py`(MCP サーバーの子プロセスとの接続)、`session.py`(自動保存・起動し直し・3D 表示の更新)、`agent.py`(AI の反復)、`backend.py`(別スレッドの asyncio でまとめる)、`conversation.py`(会話の保存)、`prompt.py`(AI への指示)、`tooldefs.py`(ツールの定義の変換)、`surface.py`(3D 表示のデータ)、`demo.py`(模擬の AI) |
| `pages/results_3d.py`・`line_profile.py` | 結果の画面の「3D 表示」(場と表示・断面・流線)と「線上の分布」のタブ |
| `widgets/` | 入力欄・ログ・キーと値の表(`common.py`)、matplotlib の図(`plots.py`)、履歴のグラフ(`history_plot.py`)、3D 表示(`vtk_view.py`。点の文字と、選べない描画を足した)、会話の欄(`chat_view.py`) |
| `theme.py`・`qtheme.py` | 色のトークンと matplotlib(Qt なし)、Qt のパレット・スタイルシート・モードの切り替え |
| `casefile.py` | ケース TOML の読み書き・プリセット・導出量・検査(Qt なし) |
| `history.py`・`centerline.py`・`reference.py`・`results.py`・`solver.py`・`paths.py` | 履歴、中心線、論文データ、出力フォルダ、ソルバーの呼び出し、パス(Qt なし。試験で直接使う) |
| `meshgen.py`・`fields.py` | メッシュ生成と品質・3D 用のデータ、VTU の場・任意の断面・等値線・流線・線上の標本と CSV(pyvista。Qt なし) |
| `workers.py` | 別スレッドの処理(QThread)、結果の画面の計算を 1 つずつ順に行う `JobRunner`、logging の受け渡し |
| `screenshots.py`・`screenshots_ai.py` | スクリーンショットの作成(01〜23、モデリング(AI)の 24〜31) |

## 7. 未実装の点・既知の制約

- **フェーズ 4 の `run`**: 2026-10-18 の時点では `build/release` のソルバーにある(上のツールバーに run・cis-run・pbc-run と出る。2026-10-16 にはなかった)。開発中の `build/phase4` のソルバーでは、`--help` から `run` と引数(`--out`・`--max-iter`・`--tol`・`--backend` など)を読み、GUI のすべての項目が渡せること、出力(history.csv・summary.json)が読めることを確かめた。GPU を長く使うので、`run` の実際の実行は GUI から試していない
- 結果の 3D 表示に、等値面と境界面の値(`boundary.vtu` の質量流束など)の表示はない。流線・任意の断面・線上の分布は v0.2 で足した
- 断面の色はセルの値(セルごとに一定)で、等値線・流線・線上の分布は点へ補間した値から作る。そのため等値線はセルの大きさの段差を含み、壁に接するセルの値は壁の上の値ではない
- 平面ウィジェットは対話型の 3D 表示のときだけ使える(画面を出さない方式・静止画の方式では使えない)。ウィジェットの矢印は、正面から見ると少し大きく見える
- 分布関数の断面表示(spec §6 の任意の機能)はない(ソルバー側にプローブ出力が要る)
- 停止はプロセスの強制終了なので、止めた時点の summary.json・VTU は書かれない(履歴は残る)
- 実行の画面は 1 つの計算だけを扱う(複数の計算を並べて実行・監視しない)。ソルバーの標準出力は、この画面から起動したときだけ表示できる(「出力フォルダを追う」は history.csv だけ)
- メッシュの画面は管(論文 §4.1 の形)だけ。平板などほかの形、gmsh の `.msh` の変換は CLI(`gsis-tools`)を使う
- 3D の表示は 13.5 万セルで実用的な速さ(VTU の読み込みと中心線 2 秒前後、断面 0.02 秒、流線は種 60 個で 0.9 秒・300 個で 4 秒)。さらに大きいメッシュと、種が数千個の流線は確かめていない(別スレッドなので画面は固まらないが、時間はかかる)
- Windows 11 だけで確かめた。Linux では `QT_QPA_FONTDIR`(offscreen のフォント)などの扱いを確かめていない
- **モデリング(AI)**:
  - 本物の API では試していない(試験は HTTP の層の模擬だけ)。本物の API の試験は §4 のコマンドで動く
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

1. 穏やかな停止の手段(例: 出力先に `STOP` というファイルができたら、その反復で止めて summary.json と VTU を書く)。今は強制終了しかできず、止めた計算は結果の画面で 3D・中心線・Ṁ を見られない
2. `pbc-run` の summary.json にも、cis-run・run と同じ `reference`(無次元化の基準量)・`delta`・`tolerance` を入れてほしい(今は GUI がケース TOML から計算している。ケースが動かされると無次元化できない)
3. history.csv に GPU のメモリの列があると、nvidia-smi(GPU 全体の値で、ほかのプログラムの分を含む)より正確に出せる
4. (確認済み)`run` の引数の名前が cis-run と同じ(`--out`・`--max-iter`・`--tol`)で、history.csv に `epsilon`・`mdot_<patch>_nd` があるので、GUI はそのまま使える。この形を保ってほしい
