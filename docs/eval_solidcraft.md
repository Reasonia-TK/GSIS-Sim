# solidcraft の評価(計算領域の作成と、AI モデリング機能の候補として)

- 作成: 2026-10-15
- 対象: solidcraft 0.11.1(https://github.com/bherbruck/solidcraft 、2026-10-14 リリース、コミット b01b968)
- 比較の基準: gmsh 4.15.2(OpenCASCADE カーネル)。gmsh 単独での評価は [paper_notes.md](paper_notes.md) の付録

## 1. 結論

| 問い | 答え |
|---|---|
| 今このプロジェクトに要るか | **要らない。** gmsh(OCC)だけで 3 つの計算領域を正確に作れ、境界の名前付けとメッシュ生成まで 1 本のスクリプトで済む。solidcraft を挟むと STEP の受け渡しが 1 段増え、DGL を素直な作り方(断面の回転)で作ると、gmsh がそのままではメッシュを作れない STEP になる |
| 将来の AI モデリング機能の候補として | **ツールの設計の手本としては価値が高いが、エンジンとして組み込むのは時期尚早。** MCP の作り(コマンドの体系、batch と巻き戻し、measure、断面のスクリーンショット、ファイルの到達範囲の制限、エージェント向けの手順書)はよくできている。一方、公開から 8 日で仕様が日単位で変わり、カーネル(truck)のブーリアンと回転に、今回の形状でも当たる制約がある |
| 採用するならどこで使うか | 軸対称でない複雑な部品(フィレット・穴・フランジなど)を、利用者が AI と対話しながら作る「前段の CAD」に限る。受け渡しは STEP、境界パッチは面の色で渡す。gmsh 側では読み込み後に体積と面積 0 の面の有無を確かめ、退化面があるときだけ OCC の修復オプションを使う(修復は空洞のある正常な STEP を壊すので既定にしない)。STL・3MF はメッシュに使わない。メッシュと境界パッチの確定は gmsh(`gsis_tools`)に残す |

AI にモデリングさせる機能を作るなら、まず gmsh(OCC)の上に薄いツール層(スクリプト実行、体積・面の一覧、画像、メッシュ)を自作する案を勧める。CAD 機能(フィレット、ロフトなど)が要るなら、下流の gmsh と同じ OCC カーネルを使う build123d(+ build123d-mcp)が次の候補になる(§6)。solidcraft は半年ほど後に再評価する(再評価の条件は §7)。

## 2. 試した版と手順

| 項目 | 内容 |
|---|---|
| 入手 | 公式 GitHub Releases の `solidcraft-0.11.1-windows-x64-portable.zip`(36 MB)。`SHA256SUMS.txt` と一致を確認。中身は `solidcraft-cli.exe`(35 MB)、`solidcraft.exe`(GUI、63 MB)、旧名の互換用 `solvecraft-cli.exe` |
| 設置 | スクラッチ領域に展開しただけ(MSI・WinGet は使わず、管理者権限なし)。MCP サーバーは Claude Code に登録せず、自作の Python クライアントから起動 |
| 形状の作成 | JSON のコマンド列(付録 A)を `solidcraft-cli run script.json --out x.step --out x.stl` で実行。体積などは `solidcraft-cli eval`(= MCP の measure と同じ値)。面タグ付き 3MF はスクリプト内の `file.export {format: "3mf-tagged"}` |
| STEP の確認 | gmsh 4.15.2(Python 3.12、OCC)で `occ.importShapes` → 体積 `occ.getMass`、面の型・面積・重心・名前・色、境界の判別、3 次元メッシュ(テトラ)の生成、メッシュ体積 |
| STL の確認 | numpy で頂点を座標の完全一致で溶接し、辺を共有する三角形の数(境界辺・非多様体辺)、向きの一貫性、連結成分、オイラー標数、囲む体積を計算 |
| MCP の確認 | `solidcraft-cli mcp --allow-path <出力先>` を stdio で起動し、JSON-RPC(1 行 1 メッセージ)で initialize → tools/list → batch / execute → measure → screenshot → export、エラーの返り方、パラメータ変更と undo |

体積の解析値: 管 586π = 1840.9733、DGL 6.363424e6 mm³(パップス、16 頂点の断面から計算)、球殻 4/3·π·(125 − 0.125) = 523.0752。

検証用のスクリプト(gmsh での確認、STL・3MF の水密性、MCP クライアント)と出力は、このセッションのスクラッチ領域(`solidcraft_eval\`・`solidcraft_out\`)に置いた。一時的な場所なので、残す必要があれば `python/` 側に移す。

## 3. 3 つの計算領域の結果

体積の誤差は解析値に対する相対誤差。メッシュの寸法は、管: 最大 0.5・最小 0.05・曲率 16 分割、DGL: 6.0・0.5・12、球殻: 0.5・0.02・16。

| 形状と作り方 | solidcraft 内の体積(measure) | STEP の面(gmsh で読込) | STEP の体積(gmsh / OCC) | gmsh の 3 次元メッシュ | STL |
|---|---|---|---|---|---|
| 管: 円柱 3 本の和(`solid.cylinder` × 3) | −3.2e−5 | 7(平面 4・円柱 3) | −1.9e−15 | ○ 7.8 万テトラ / 1.0 s | 水密(4,684 三角形、1 殻)、体積 −5.4e−4 |
| (参考)管: 段付き断面を x 軸まわりに回転 | −4.5e−5 | 7 | 5e−16 | ○ 8.3 万 | 水密 |
| DGL: 断面(16 頂点)を z 軸まわりに 360° 回転 | −1.1e−5(エラー・警告なし) | **20 = 実際の面 14 + 軸上の面積 0 の回転面 6**。ウェハの空洞が独立した殻になっていない | 3e−14 | **× 既定の読み込みでは失敗**("Impossible to mesh periodic surface")。OCC の修復オプション 5 つを有効にすると 14 面になり ○ 16.2 万 / 2.3 s | **水密でない**(非多様体辺 287、向きの不整合 625、面積 0 の三角形 248)。体積は −6.1e−4 と合う。gmsh の STL 再メッシュは 400 s で打ち切り |
| DGL: ウェハなしの断面を回転 → 円柱(r 50、z 88〜90)を差 | × solidcraft がエラーで拒否(§3.1) | — | — | — | — |
| DGL: 基本形状の和(円柱 + 傾斜押し出しの円錐台)、ウェハの差は最後 | −8.5e−6 | 14、空洞は `BREP_WITH_VOIDS` の殻 | 3e−14 | ○ 16.7 万 / 3.8 s(修復なし) | 水密(4,320 三角形、2 殻) |
| 球殻: 球(r 5)から球(r 0.5)を差 | −2.1e−5 | 4(各球が 2 面)、`BREP_WITH_VOIDS` | −2.9e−8 | ○ 3.8 万 / 0.33 s | 水密(48,240 三角形、2 殻) |

- solidcraft 内の体積はテッセレーションから求める値で(ROADMAP によると曲面で相対 1e−4 程度)、今回は 1e−5 程度ずれた。STEP に書かれた B-rep 自体は正確で、OCC で測ると誤差は丸めの程度だった(球殻の 3e−8 は、球面パッチの境界辺が B スプラインで近似されているためと推測)。
- 実行時間(CLI、STEP・STL・3MF の書き出しを含む): 管 0.3 s、DGL(回転)2.8 s、DGL(基本形状)3.1 s、球殻 1.5 s。失敗するブーリアンは再試行のため 6〜10 s かかった。

### 3.1 DGL(回転)の問題: 軸上に 2 区間以上接する断面

- 断面のうち軸上にある辺(z = 0〜88 と z = 90〜220)が、それぞれ面積 0 の回転面(`SURFACE_OF_REVOLUTION` に軸上の直線)として STEP に書かれ、ウェハの空洞の面もこれらを介して外側と同じ 1 つの閉殻に入る。OCC での体積は正しいが、gmsh はこの退化面をメッシュにできない。
- 原因(ソースで確認): `crates/kernel/src/build.rs` の `revolve_touching_axis` は「軸上の辺がちょうど 1 本」の断面だけを扱い、DGL のように切り欠きで軸上の辺が 2 本に分かれると、汎用の回転掃引に落ちて軸上の辺が面積 0 の面になる(コメントにもその旨がある)。隣り合う断面をまとめて回転して軸上の辺が分割される場合は Issue #487(10-13 にクローズ。軸上で一直線に並ぶ辺を 1 本にまとめる修正)で扱われたが、切り欠きで軸上の辺が離れている場合は残っている。
- 最小の再現: 半径 10・高さ 20 の円柱の断面に、軸上の切り欠き(r ≤ 5、z = 9〜11)を入れて回転する(付録 A の `notch_cyl`)。退化面 6 枚、gmsh のメッシュは失敗、STL は水密でない。上流には報告していない。
- solidcraft は警告もエラーも出さず、measure の体積も正しいので、STEP・STL を外で調べないと気づかない。usd-brep(paper_notes 付録)と同じ種類の入力で、症状は異なる(usd-brep は体積がずれ、solidcraft は体積は正しいが位相とテッセレーションが壊れる)。
- gmsh 側の回避策: `importShapes` の前に `Geometry.OCCFixDegenerated`・`OCCFixSmallEdges`・`OCCFixSmallFaces`・`OCCSewFaces`・`OCCMakeSolids` をすべて 1 にすると、退化面が除かれて 14 面になり、体積も正しく、メッシュも通った(付録 B)。1 つだけでは直らず、`OCCFixSmallEdges` だけだと体積が +0.33%、`OCCSewFaces` だけだと体積 0 になった。読み込み後の `occ.healShapes()` は効かなかった。
- **ただし、この修復は空洞のある正常な STEP を壊す。** DGL(基本形状、下記)と球殻(どちらも `BREP_WITH_VOIDS`)に 5 つを掛けると体積要素が消えて体積 0 になり(`OCCSewFaces` が原因)、`OCCFixSmallEdges` だけでも体積が +0.2〜0.3% ずれた。空洞のない管には影響しなかった。修復は既定にせず、面積 0 の面がある STEP に限って使い、読み込み後に必ず体積を検算する。
- solidcraft 側の回避策: 基本形状の和で作り、ウェハの差を最後にする(上表 5 行目)。これ以外の組み方は、どれも solidcraft がエラーで拒否した:
  - ウェハなしの回転体からウェハを差す(`solid.cylinder` の cut、円の押し出しの cut、`solid.combine` の cut の 3 通り): "the cut could not be computed reliably: it came out empty. The result was refused rather than kept wrong"
  - 先にウェハを抜いた円柱に円錐台を足す(面で接する場合も、0.5 mm 重ねた場合も): "boolean Union: implausible result volume …; the exact fallback for where the bodies meet … takes planar faces only"(空洞が埋まった体積を検出して拒否している)
- 誤った形を黙って返さず、明示的なエラーで止める点はよい。ただし、作れる組み方を探すのに試行錯誤が要る。

### 3.2 境界(入口・出口・壁)の判別

| 手段 | 結果 |
|---|---|
| 面の名前 | STEP の面は名前なし(`ADVANCED_FACE('', …)`)。solidcraft の内部には面の永続名があり、MCP の `body_topology` で "F1:bottom"(管の入口)、"F3:top"(出口)のように見えるが、STEP には出ない。体の名前は `MANIFOLD_SOLID_BREP('Tube')` に入るが、gmsh の体積の名前は製品名(= 出力ファイル名)から "Shapes/tube" になる。STEP の面に名前を手で書き込んでも、gmsh 4.15.2 は読まなかった(`getEntityName` が空) |
| 面の色 | **使える。** `appearance.assign {faces: [[x, y, z]], color}` で面に色を付けると、STEP に `STYLED_ITEM` が書かれ、gmsh の `getColor(2, tag)` で読める。管(入口 赤・出口 青・他 灰)と DGL(入口の円筒面 赤・上の出口 青・下の出口 緑)で、幾何による判別と一致した。面上の 1 点を指定すれば、カーネル内で 2 分割された円筒面でも STEP の 1 面全体に色が付いた。gmsh の修復オプションを掛けると、面に付けた色は残るが、体に付けた色(壁の灰)は消えた。入口・出口など壁以外の面にだけ色を付け、色のない面を壁とみなすのが安全 |
| 幾何 | 全ケースで可能。面の型と重心(管: 平面の x、DGL: 平面の z と円筒の z 範囲、球殻: 半径)で判別できる。平面の境界ボックスは円弧の NURBS 制御点の分だけ膨らむ(r = 100 の円板で ±101〜104)ので、境界ボックスより重心を使う |
| 面タグ付き 3MF | 三角形ごとに B-rep の面番号を持つ(`ft:face`)。gmsh の離散面として取り込み、そのまま体積メッシュにできた(管 6.9 万、DGL(基本形状)12.8 万、球殻 85 万テトラ。CAD のテッセレーションをそのまま境界に使うので "ill-shaped tets" の警告が出る)。ただし面番号はカーネルの面(円筒は半分ずつ、球は 6 枚)で STEP の面と 1 対 1 でなく、名前もない。DGL(回転)の 3MF は STL と同様に水密でない |

色と境界の種類の対応(例: 赤 = 入口)を運用で決めれば、色で Physical Group を作れる(付録 B)。gmsh は色のない面に (0, 0, 255, 0)(不透明度 0 の青)を返すので、青を使うときは不透明度まで含めて比べる。

## 4. MCP の試用

`solidcraft-cli mcp`(ヘッドレス、プロトコル 2025-06-18)を自作の Python クライアントから操作した。

| 操作 | 結果 | 応答の大きさ / 時間 |
|---|---|---|
| initialize | サーバー名・版と、使い方の要約(instructions)を返す | 1.1 KB |
| tools/list | 15 個: list_commands、execute、batch、inspect_design、measure、body_topology、set_parameter、screenshot、export、crash_reports、undo、redo、new_design、open、save。読み取り専用・破壊的などの注記付き | 12.6 KB |
| batch(管の円柱 3 本、`verbose: false`) | 成功。各手順の feature 番号と、作成・変更された体の名前 | 0.8 KB / 0.14 s |
| measure | 体積 1840.9143(−3.2e−5)、面積、重心、境界ボックス、面・辺・頂点の数 | 1.2 KB |
| body_topology | 面ごとに番号・面積・重心・法線・永続名、辺ごとに中点・長さ | 11 KB |
| screenshot | PNG を画像として返す。視点名か、カメラ(注視点・方位・仰角・距離)を指定。`inspect.section` と組み合わせて断面を見られ、DGL のウェハの空洞まで確認できた | 480×320〜480×480 の PNG で 10〜340 KB / 0.03〜0.1 s |
| export | 許可フォルダ内に STEP・STL を書けた。許可フォルダ外は書かずに `isError`("outside the folders this agent may use …") | 0.3 KB |
| 未知のコマンド / 負の半径 / 長さ 0 の線分 | `isError: true` と読みやすい英文("unknown command …"、"radius must be positive"、"zero-length line") | 0.1 KB |
| 引数の型違い | JSON-RPC の −32602("argument `commands` must be of type "array"") | 0.1 KB |
| batch の途中でブーリアンが失敗(DGL のウェハの差) | `failed.index`・`failed.command`・`failed.error` を返し、全手順を巻き戻した(その後の inspect で timeline 0 件) | 2 KB / 約 10 s |
| set_parameter(管の半径 1 → 2 mm)と undo / redo | 再計算され、体積は −5.7e−5 で一致。undo / redo も期待どおり | 0.2 KB / 0.15 s |
| list_commands(絞り込みなし / "revolve") | 全コマンド(約 350)の引数の説明 | 166 KB / 1.5 KB |

エージェントが使うときの手触り:

- よい点
  - コマンドの ID と JSON の引数が、UI・CLI スクリプト・MCP で共通。MCP で試した手順をそのまま CLI スクリプトにでき、再現できる。
  - 失敗の扱いが丁寧。batch は失敗した手順と理由を返して巻き戻し、ブーリアンは疑わしい結果を拒否して理由と対処のヒントを返す。
  - 検算の手段が揃っている(measure の体積・重心・面の数、screenshot と断面、body_topology の面の永続名)。応答を小さく保つ工夫(`verbose: false`、measure 約 1 KB)もある。
  - エージェント向けの手順書(`skills/` の 3 本: 作り方、編集、検査)があり、罠や検算の方法まで書かれている。
  - ファイルの到達範囲の制限が効いている(Windows の UNC・デバイス名・8.3 形式の名前なども拒否する作り)。
- 注意点
  - 辺・面の選択は座標で最寄りのものを拾い、距離の上限がなく、外れてもエラーにならない。公式の手順書も「ok と返っても measure の値が計算と合うまで信じない」としており、検算が前提の設計になっている。
  - 公式の手順書は、報告されない誤り(ブーリアンの再試行で形が狂っても警告だけ、寸法が 0 を通ると形が反転する、同名の体が黙って改名されるなど)を列挙している。今回の退化面も警告なしだった。
  - 絞り込みなしの list_commands は 166 KB、断面のスクリーンショットは 340 KB と大きい。単位も混在する(camera の角度は rad、`solid.move` は deg)。
  - MCP の起動ごとに `%TEMP%\solidcraft-mcp-*` の空フォルダが残り、`%LOCALAPPDATA%\SolidCraft\recovery` が作られる(評価後に削除した)。
  - アプリの画面に接続してエージェントの操作を見せるモード(`solidcraft --control PORT` と `mcp --connect`)は試していない。

## 5. 成熟度とリスク

| 項目 | 状況 |
|---|---|
| 開発の段階 | リポジトリ作成 2026-10-07、v0.1.0 が 10-09、v0.11.1 が 10-14(6 日で正式版 22 個 + nightly 4 個)。0.5.0 で SolveCraft から改名。コミット約 1,150、Issue はオープン 309 件・クローズ 370 件(10-15 時点)、星 2.6k。貢献者 65 名のうち主開発者 1 名がコミットの過半を占め、エージェントによる自律開発の手順(リポジトリの CLAUDE.md、ROADMAP の "agent hours" の見積り)で進めている。自己評価は "early but usable for simple parts" |
| truck カーネル | B-rep は truck(ricosjp、Apache-2.0)を自前の `kernel` crate で包み、フィレット・シェル・STEP / IGES の入出力・ブーリアンの再試行と検証は自前。truck のクレート 3 つをパッチして同梱している。既知の制約は、曲面どうしが接する・浅い角度で交わる場合の厳密な代替処理が平面に限られること。今回、空洞を持つ体への和と、回転体の内部に空洞を作る差が失敗し、軸上に 2 区間で接する回転断面で退化面が出た。計測(体積など)はテッセレーションから求める |
| API の安定性 | コマンドの文書はレジストリから生成され、よく整っている。ただし数日単位で機能と仕様が変わる(設計ファイルの形式番号は 0.11.1 のソースで 20。ROADMAP の記述は 12〜14 のまま。改名の互換シムもある)。文書間で数字がずれている(参照部品の一致数が README は 75/76、ROADMAP は 82/82)。使うなら版を固定し、上げるたびに検算をやり直す必要がある |
| Windows 対応 | ポータブル zip で、インストールなしに CLI と MCP が動いた。実行ファイルは署名なし(README によると SmartScreen が警告する場合がある。今回の CLI 実行では出なかった)。CLI は 1 回 0.3〜3 s で速い |
| ライセンス | MIT OR Apache-2.0(Cargo.toml。GitHub の表示は Apache-2.0)。truck も Apache-2.0、同梱フォントは OFL。GPL の gmsh とはファイルでやり取りするだけなので干渉しない |

## 6. AI にモデリングさせる他の方法との比較

| 方式 | カーネル / ライセンス | エージェントの操作 | 境界を gmsh へ渡す方法 | 成熟度 | 所見 |
|---|---|---|---|---|---|
| A. エージェントが gmsh(OCC)の Python を書く(現行の方針) | OCC / GPL-2.0+ | Python を書いて実行。Claude Code なら MCP は不要。アプリに組み込むなら、薄いツール層(スクリプト実行、体積・面の一覧、画像、メッシュ)を自作する | Physical Group を同じスクリプトで付ける(変換なし) | 高い(gmsh 4.15.2) | 3 形状とも正確(paper_notes 付録)。フィレットなど機械部品向けの機能は乏しいが、本プロジェクトの形状には十分。gmsh 用の MCP サーバーは OFFTECH/gmsh-mcp-server(GPL-3.0、2026-09 公開、星 0)程度 |
| B. build123d + build123d-mcp | OCC(OCP)/ Apache-2.0 | MCP の execute で build123d のコードを持続するセッションで実行し、描画・計測・書き出し | STEP か BREP(+ 色)で渡し、幾何か色で判別 | build123d 0.13.0(2022〜、星 3.4k)。build123d-mcp 0.3.90(2026-04〜、星 113。依存を build123d < 0.12 に固定) | 下流の gmsh と同じカーネルなので受け渡しが素直。作者は CADGenBench で同じモデルの得点が 0.360 → 0.457 に上がったと主張(未検証)。solidcraft の検査用スクリプトも、厳密な比較に build123d(OCC)を使っている |
| C. CadQuery + MCP | OCC(OCP)/ Apache-2.0 | B と同様 | B と同様 | CadQuery 2.8.0(星 5.9k)。MCP サーバーは小規模(星 20、2025-06 で更新停止など) | B と同じ位置づけで、MCP は B のほうが整っている |
| D. FreeCAD + MCP(neka-nat/freecad-mcp) | OCC / FreeCAD は LGPL、MCP は MIT | GUI の FreeCAD を MCP で操作 | STEP。FEM ワークベンチから gmsh / Netgen も使える | MCP の星 2.9k | 重い GUI アプリで、対話的な設計向き。今回は試していない |
| E. solidcraft の MCP(本評価) | truck + 自前 / MIT OR Apache-2.0 | 専用の MCP(15 ツール)、CLI スクリプト | STEP の面の色 | 公開 8 日 | 操作性と失敗時の扱いは最も整っているが、カーネルが若い(§3.1、§5) |

## 7. 結論の補足と、採用する場合の使い方

- 本プロジェクトの計算領域(円管と槽、子午面断面の回転体、球の差)は gmsh(OCC)で正確に作れ、境界パッチの付与とメッシュ生成まで同じスクリプトで完結する。solidcraft を入れる利点は今はない。
- AI にモデリングさせる機能で効くのは、CAD エンジンの種類よりも検算の手段(体積と解析値の比較、境界パッチごとの面の一覧、画像、メッシュ生成の成否)をエージェントに返すことである。solidcraft の MCP はこの点の手本になる(batch と巻き戻し、小さい応答、断面のスクリーンショット、到達範囲の制限、手順書)。同じ考え方で gmsh の上にツール層を作るのが、下流との整合の点で最も素直。
- solidcraft を採用する場合の使い方:
  1. 用途は、軸対称でない複雑な部品を利用者が AI と対話しながら作る前段の CAD に限る。版を固定し、CLI スクリプト(JSON)を設計の正本として保存する。
  2. 受け渡しは STEP のみ。STL・3MF は、軸に接する回転体で水密でないものが出るので、メッシュには使わない。
  3. 境界パッチは面の色で渡す(色と種類の対応表を決めておく)。色を付けるのは壁以外の面だけにし、色のない面を壁とする。gmsh 側では幾何による判別も併せて行い、食い違いを検出する。
  4. gmsh ではまず修復なしで読み込み、体積を期待値と比べ、面積 0 の面がないことを確かめる。退化面があるときだけ修復オプション 5 つを有効にして読み直し、もう一度体積を確かめる(付録 B。空洞のある正常な STEP に修復を掛けると体積 0 になる)。
  5. 回転で作る断面は、軸上の辺を 1 本にとどめる。軸上に切り欠き(空洞)があるときは、基本形状の和で作り、空洞の差を最後にする。
- 再評価の条件(どれかが満たされたら見直す): 軸上に複数区間で接する回転断面が退化面なしで作れる、STEP の面に名前(または安定した面タグ)が書かれ gmsh で読める、空洞や曲面の接触を含むブーリアンが安定する、リリースの間隔が落ち着く(1.0 など)。

## 付録 A. 使ったコマンドスクリプト(solidcraft 0.11.1)

断面の平面は `{origin: [0,0,0], x_dir: [1,0,0], y_dir: [0,0,1]}`(スケッチの x = 半径 r、y = z)とした。既定の `"XZ"` 平面ではスケッチの y が −Z になる点に注意。

管(円柱 3 本の和):

```json
{"commands": [
 {"command": "solid.cylinder", "params": {"radius": 8, "height": 8, "base": [0, 0, 0], "axis": "X", "operation": "new", "name": "Upstream", "body_name": "Tube"}},
 {"command": "solid.cylinder", "params": {"radius": 1, "height": 10, "base": [8, 0, 0], "axis": "X", "operation": "join", "targets": ["Tube"], "name": "Pipe"}},
 {"command": "solid.cylinder", "params": {"radius": 4, "height": 4, "base": [18, 0, 0], "axis": "X", "operation": "join", "targets": ["Tube"], "name": "Downstream"}}
]}
```

境界の色を付ける場合は、続けて次を加える(入口 赤、出口 青。色のない面は壁とみなす。面は、その面上の 1 点で指定する):

```json
 {"command": "appearance.assign", "params": {"faces": [[0, 0, 0]], "body": "Tube", "color": "#ff0000"}},
 {"command": "appearance.assign", "params": {"faces": [[22, 0, 0]], "body": "Tube", "color": "#0000ff"}}
```

DGL では入口の円筒面を `[100, 0, 110]`、上下の出口を `[50, 0, 220]`・`[50, 0, 0]` で指定した(軸上の点は退化面を拾うおそれがあるので避ける)。

DGL(断面の回転。STEP に退化面が出る):

```json
{"commands": [
 {"command": "sketch.create", "params": {"plane": {"origin": [0, 0, 0], "x_dir": [1, 0, 0], "y_dir": [0, 0, 1]}, "name": "Section"}},
 {"command": "sketch.line", "params": {"points": [[0, 0], [100, 0], [100, 100], [3.5, 100], [5.2, 108.5], [100, 108.5], [100, 111.5], [5.8, 111.5], [7.5, 120], [100, 120], [100, 220], [0, 220], [0, 90], [50, 90], [50, 88], [0, 88]], "closed": true}},
 {"command": "sketch.finish", "params": {}},
 {"command": "solid.revolve", "params": {"axis": "Z", "name": "Revolve", "body_name": "DGL"}}
]}
```

DGL(基本形状の和、ウェハの差は最後。正しい STEP になる。円錐台は傾斜押し出しで、`taper` は正で広がる。11.309932474° = atan(1.7 / 8.5)):

```json
{"commands": [
 {"command": "solid.cylinder", "params": {"radius": 100, "height": 100, "base": [0, 0, 0], "axis": "Z", "operation": "new", "name": "Lower", "body_name": "DGL"}},
 {"command": "sketch.create", "params": {"plane": "XY", "offset": 100, "name": "S1"}},
 {"command": "sketch.circle.center", "params": {"center": [0, 0], "radius": 3.5}},
 {"command": "sketch.finish", "params": {}},
 {"command": "solid.extrude", "params": {"distance": 8.5, "taper": "11.309932474 deg", "operation": "join", "targets": ["DGL"], "name": "Cone1"}},
 {"command": "solid.cylinder", "params": {"radius": 100, "height": 3, "base": [0, 0, 108.5], "axis": "Z", "operation": "join", "targets": ["DGL"], "name": "Slot"}},
 {"command": "sketch.create", "params": {"plane": "XY", "offset": 111.5, "name": "S2"}},
 {"command": "sketch.circle.center", "params": {"center": [0, 0], "radius": 5.8}},
 {"command": "sketch.finish", "params": {}},
 {"command": "solid.extrude", "params": {"distance": 8.5, "taper": "11.309932474 deg", "operation": "join", "targets": ["DGL"], "name": "Cone2"}},
 {"command": "solid.cylinder", "params": {"radius": 100, "height": 100, "base": [0, 0, 120], "axis": "Z", "operation": "join", "targets": ["DGL"], "name": "Upper"}},
 {"command": "solid.cylinder", "params": {"radius": 50, "height": 2, "base": [0, 0, 88], "axis": "Z", "operation": "cut", "targets": ["DGL"], "name": "Wafer"}}
]}
```

球殻:

```json
{"commands": [
 {"command": "solid.sphere", "params": {"radius": 5, "name": "Outer", "body_name": "Shell"}},
 {"command": "solid.sphere", "params": {"radius": 0.5, "operation": "cut", "targets": ["Shell"], "name": "Inner"}}
]}
```

退化面の最小再現(`notch_cyl`): DGL(回転)の `points` を `[[0, 0], [10, 0], [10, 20], [0, 20], [0, 11], [5, 11], [5, 9], [0, 9]]` に替える。

## 付録 B. gmsh 側の読み込み

修復なしで読み、面積 0 の面があるか体積が合わないときだけ修復を有効にして読み直す。続けて面の色から境界パッチを作る。管(色付き・色なし)、DGL(回転: 修復あり、基本形状: 修復なし)、球殻の STEP で動作を確かめた。

```python
import gmsh

HEAL = ("OCCFixDegenerated", "OCCFixSmallEdges", "OCCFixSmallFaces", "OCCSewFaces", "OCCMakeSolids")
# 色のない面は gmsh で (0, 0, 255, 0) になるので、不透明度まで含めて比べる。どれにも合わない面は wall
PATCH_BY_COLOR = {(255, 0, 0, 255): "inlet", (0, 0, 255, 255): "outlet", (0, 255, 0, 255): "outlet2"}


def load_step(path, expected_volume, rtol=1e-6):
    """修復なしで読み、面積 0 の面があるか体積が合わなければ、修復を有効にして読み直す。"""
    for heal in (False, True):
        gmsh.clear()
        for k in HEAL:
            gmsh.option.setNumber(f"Geometry.{k}", int(heal))  # importShapes の前に設定する
        gmsh.model.occ.importShapes(path)
        gmsh.model.occ.synchronize()
        areas = [gmsh.model.occ.getMass(2, s) for _, s in gmsh.model.getEntities(2)]
        n_zero = sum(a < 1e-9 * sum(areas) for a in areas)
        vol = sum(gmsh.model.occ.getMass(3, v) for _, v in gmsh.model.getEntities(3))
        if n_zero == 0 and abs(vol - expected_volume) <= rtol * expected_volume:
            return heal
    raise RuntimeError(f"{path}: volume {vol} vs {expected_volume}, zero-area faces {n_zero}")


gmsh.initialize()
load_step("dgl.step", expected_volume=6.363424e6)
groups = {}
for _, s in gmsh.model.getEntities(2):
    groups.setdefault(PATCH_BY_COLOR.get(tuple(gmsh.model.getColor(2, s)), "wall"), []).append(s)
for name, tags in groups.items():
    gmsh.model.addPhysicalGroup(2, tags, name=name)
```

`Geometry.OCCImportLabels` は既定で 1(色と体の名前を読む)。修復オプションの既定はすべて 0。
