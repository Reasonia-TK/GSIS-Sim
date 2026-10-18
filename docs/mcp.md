# AI モデリング用 MCP サーバー(gsis-mcp)

- 作成: 2026-10-17(フェーズ 7)
- 実装: `python/src/gsis_tools/mcp/`(サブパッケージ `gsis_tools.mcp`、依存は extra `mcp` = 公式の MCP Python SDK `mcp>=2.3.0`)
- 関連資料: [spec.md](spec.md) §1.3.1・§7、[eval_solidcraft.md](eval_solidcraft.md)(MCP の設計の手本)、[python_tools.md](python_tools.md)、[pmesh_format.md](pmesh_format.md)

## 1. 概要

AI エージェントが gmsh(OpenCASCADE カーネル)で計算領域の形状を作り、境界パッチを付けて、ソルバー用のメッシュ(`.pmesh`)まで書き出せるようにする、薄いツール層。MCP サーバー(stdio)として動く。

```
エージェント(Claude Code など) ──stdio(JSON-RPC、1 行 1 メッセージ)── gsis-mcp
                                                                         │ 1 つのスレッドで順に実行
                                                                         ▼
                                                     gmsh / OpenCASCADE(形状・メッセージ・メッシュ)
                                                     gsis_tools(.pmesh への変換と検査、品質)
                                                                         │
                                                                         ▼
                                                     作業フォルダ(.pmesh・PNG・STEP・状態の JSON など)
```

[eval_solidcraft.md](eval_solidcraft.md) の評価で手本にした点を取り入れた:

| 手本 | このサーバーでの形 |
|---|---|
| batch と失敗時の巻き戻し | `batch` は 1 つでも失敗したら全体を元に戻し、失敗した手順の番号(0 始まり)・ツール名・理由を返す |
| 小さな計測結果 | 応答は数百バイト〜2 KB の JSON。面の一覧は件数を区切れる。体積は作ったときに解析値との相対誤差も返す |
| 断面のスクリーンショット | `render` の `section` で断面(切り口を塗る)、メッシュは四面体ごとに切って中を見せる |
| 書き出し先の制限 | 作業フォルダの外には書かない(UNC・デバイス名・8.3 形式の名前・`..`・リンクの外向きも拒否) |
| 報告されない誤りへの対策 | 面の選択は距離の許容差の外ではエラー(条件ごとの残りの数を返す)。名前の重複はエラー。形状が変わった面はパッチから外して知らせる |

## 2. 起動

リポジトリのルートで:

```
uv run --project python --extra mcp gsis-mcp                       # 作業フォルダは ./mcp_workspace/
uv run --project python --extra mcp gsis-mcp --workspace D:\work\cad
python -m gsis_tools.mcp --workspace ...                           # python/.venv の Python から直接
```

| 引数 | 内容 |
|---|---|
| `--workspace DIR` | 作業フォルダ(既定はカレントの下の `mcp_workspace/`。なければ作る)。ファイルはこの中にだけ書く |
| `--read-dir DIR` | 読み込み(`import_cad`・`model_load`)を許すフォルダを足す(繰り返し可) |
| `--allow-python` | `run_python`(gmsh の Python スクリプトの実行)を有効にする(§11。危険) |
| `--threads N` | gmsh のスレッド数(既定は min(8, CPU 数)) |
| `--log-level` | 標準エラーに出すログの水準(既定 WARNING) |
| `--gui-tools` | GUI 専用のツール `gui_surface`(§6.7)も一覧に出す。GUI(`gsis-gui` のモデリング(AI)の画面)が子プロセスとして起動するときに付ける(2026-10-18 に追加) |

- 標準出力は JSON-RPC の通信路(1 行 1 メッセージ、改行は LF)。ログと OpenCASCADE の出力(STEP の統計など)は標準エラーに出る(§12.2)
- クライアントが標準入力を閉じると終わる。MCP の initialize の手順で、プロトコル 2025-06-18(生の JSON-RPC)と 2025-11-25(SDK のクライアント)を確かめた

## 3. クライアントへの登録(手順の例)

このプロジェクトでは、Claude Code などへの登録は行っていない。使うときは次のどれかで登録する(パスは例)。Claude Code の書式は 2026-10-18 に公式文書(https://code.claude.com/docs/en/mcp 、mcp-quickstart、env-vars)で確かめた。

**Claude Code(コマンド)**

```
claude mcp add gsis-gmsh -- uv run --project C:\Works\20261015_GSIS-PBC\python --extra mcp gsis-mcp --workspace C:\Works\20261015_GSIS-PBC\mcp_workspace
claude mcp add --scope project --transport stdio gsis-gmsh -- uv run ...(同じ)   # .mcp.json に書いてリポジトリで共有
claude mcp list
claude mcp get gsis-gmsh
claude mcp remove gsis-gmsh
```

- `--scope`・`--transport`・`--env` などの Claude Code の引数は `--` の前に置く。`--` の後はそのままサーバーに渡る(`--project` などが Claude Code の引数と取り違えられない)
- scope は local(既定。`~/.claude.json` のこのプロジェクトの項目)、project(プロジェクトのルートの `.mcp.json`)、user(`~/.claude.json` の全体の `mcpServers`)
- project の `.mcp.json` のサーバーは、対話のセッションでは使う前に承認を求められる(後から `/mcp` で承認できる)
- 公式文書の例はラッパーなしで起動している(Windows で `cmd /c` が要るという記述はない)。起動に失敗したら、同じコマンドを端末で直接実行して原因を見る

**プロジェクトの `.mcp.json`(リポジトリで共有する場合)**

```json
{
  "mcpServers": {
    "gsis-gmsh": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "C:\\Works\\20261015_GSIS-PBC\\python", "--extra", "mcp", "gsis-mcp",
               "--workspace", "C:\\Works\\20261015_GSIS-PBC\\mcp_workspace"]
    }
  }
}
```

`"type"` は省略できる(省略すると stdio)。`.mcp.json` はセッションの開始時に読まれるので、書き換えたら Claude Code を起動し直す。

**Claude Desktop など(`mcpServers` の設定を持つクライアント)** は、上の `command` と `args` を同じ形で書く。

注意:

- 最初の起動では uv が extra `mcp` の依存を入れるので時間がかかる。Claude Code のサーバー起動の待ち時間は既定 30 s(環境変数 `MCP_TIMEOUT`、ミリ秒)なので、前もって `uv sync --project python --extra mcp`(GUI も使うなら `--extra gui` も。付けないと GUI の依存が外れる)を実行しておく
- メッシュの生成は数秒〜数十秒かかる(§10)。Claude Code のツール呼び出しの待ち時間は既定で約 28 時間(`MCP_TOOL_TIMEOUT`、ミリ秒)なので足りる
- Claude Code はツールの応答が 1 万トークンを超えると警告し、`MAX_MCP_OUTPUT_TOKENS`(既定 25,000)を上限とする。このサーバーの応答は 2 KB 以下(画像は `inline: true` のときだけ応答に入れ、既定ではパスだけ返す)
- `--allow-python` は付けない(§11)

## 4. 作業フォルダと到達範囲

| 操作 | 許す場所 |
|---|---|
| 書き出し(`export*`・`render`・`model_save`) | 作業フォルダの中だけ。パスは作業フォルダからの相対パス(作業フォルダの中を指す絶対パスも可)。親のフォルダは作る。同じ名前のファイルは上書きする |
| 読み込み(`import_cad`・`model_load`) | 作業フォルダと `--read-dir` のフォルダの中だけ |
| 内部用 | `作業フォルダ/.gsis_mcp/`(取り込んだ CAD の写し `imports/`、一時ファイル `tmp/`)。ツールからは書けない |

拒否するパス: 空、NUL 文字、UNC(`\\server\share`)とデバイス名前空間(`\\?\`・`\\.\`)、`..` を含むもの、ドライブ相対(`C:foo`)、名前の中の `:`(代替データストリーム)、予約デバイス名(`CON`・`NUL`・`COM1` など。拡張子付きも)、末尾が `.` か空白の名前、8.3 形式の短い名前(`PROGRA~1`)、`<>"|?*`、シンボリックリンク・ジャンクションをたどると外に出るもの、ツールごとに決めた以外の拡張子。

## 5. 約束

- **単位**: 長さはモデルの単位(`model_new` の `unit`: m・cm・mm・um)。`.pmesh` だけ m に換算して書く。STEP・IGES の読み書きはモデルの単位に換算する(OpenCASCADE の `xstep.cascade.unit`)。BREP・STL・MSH・VTU はモデルの単位の数値のまま
- **角度**は度、**ベクトル**は `[x, y, z]`
- **名前**: 形状を作るツールは `name` で名前を付ける(省略すると `box1`・`cylinder2`・`profile1` などを自動で付け、応答で返す)。同じ名前の二重登録はエラー。`boolean` の結果は `name`(省略すると objects の最初の名前)を引き継ぎ、消えた入力の名前は消える。消えた名前を使うと「もうありません(手順 N で消えた)」と返す
- **参照**: 引数 `entities`・`objects`・`tools` は名前か番号の並び。番号は、`boolean`・`transform`・`remove`・`measure` では体積、`extrude`・`revolve` では面。`'v:3'`(体積)・`'s:5'`(面)・`'c:2'`(曲線)・`'p:1'`(点)と書くと次元を明示できる
- **番号の安定性**: 同じ手順からは同じ番号の形状ができる(gmsh / OpenCASCADE の性質)。元に戻す・読み込みは手順のやり直しなので、番号も元どおりになる
- **応答**: 成功は JSON の文字列 1 つ(画像を頼んだときは PNG も)。失敗は `isError: true` と JSON `{"error": 理由, "hint": 直し方, ...}`。引数の型・範囲・未知の引数はツールを動かす前に弾く。gmsh の警告・エラーがあれば `gmsh_messages` に入れる

## 6. ツール一覧

31 個(`--allow-python` のときは 32 個)。`*` は必須の引数。tools/list の応答は約 29 KB。

`--gui-tools` で起動したときは、GUI 専用の `gui_surface`(§6.7)が 1 つ増える(2026-10-18 に追加)。

### 6.1 モデル

| ツール | 主な引数 | 返り値・動き |
|---|---|---|
| `model_new` | `name`、`unit`(既定 mm) | 新しい空のモデル(履歴も消える) |
| `model_info` | `history` | 体積の一覧と体積、名前、境界の面・内部の面・体積に属さない面の数、パッチ、付いていない面、メッシュの有無、細分化 |
| `model_save` | `path*`(.json) | 手順の履歴を JSON に保存 |
| `model_load` | `path*` | 保存した手順をやり直して状態を戻す。途中で失敗したら元のモデルに戻す |
| `undo` | `steps`(既定 1) | 最後の手順を取り消す(batch は 1 手順)。取り消した手順を新しい順に返す。設定(パッチ・メッシュの設定)だけの取り消しではメッシュを残す |
| `batch` | `steps*`(`[{tool, args}]`、最大 500)、`verbose` | 順に実行。失敗したら全体を元に戻し `failed: {index, tool, error}`・`rolled_back`・`completed_before_failure` を返す。引数の誤りは実行の前にすべて調べる。`model_new`・`model_load`・`model_save`・`undo`・`batch` は入れられない |

### 6.2 形状

| ツール | 主な引数 | 返り値 |
|---|---|---|
| `add_box` | `corner*`、`size*` | 名前、体積の番号、体積、面の数、解析値と相対誤差 |
| `add_cylinder` | `base*`(底面の中心)、`axis*`(上面の中心へのベクトル。長さが高さ)、`radius*`、`angle_deg` | 同上 |
| `add_sphere` | `center*`、`radius*` | 同上 |
| `add_cone` | `base*`、`axis*`、`radius1*`(底面)、`radius2*`(上面。0 で尖る) | 同上 |
| `add_torus` | `center*`、`radius_major*`、`radius_minor*`、`axis` | 同上 |
| `add_profile` | `plane`(xy・xz・yz)か `u_axis`+`v_axis`、`origin`、`outer*`、`holes` | 平面の断面(面)。面積、重心(3 次元と u, v)、法線。断面が v 軸の片側にあれば、v 軸まわりに 360° 回したときの体積(パップスの定理)`pappus_volume_v_axis` |
| `extrude` | `entities*`(面)、`vector*` | 押し出した体積と、断面の面積 × 押し出しの長さとの相対誤差 |
| `revolve` | `entities*`(面)、`axis_point`、`axis_dir`、`angle_deg`(既定 360) | 回転体。断面が平らで軸がその平面内にあれば、パップスの定理の体積 `pappus_volume` と相対誤差。360° のときは残った元の断面を消す |
| `boolean` | `op*`(fuse・cut・intersect・fragment)、`objects*`、`tools`、`keep_objects`、`keep_tools` | 結果の名前・体積の番号・体積・面の数。結果が空ならエラー(形状は元のまま) |
| `transform` | `entities*`、`op*`(translate・rotate・scale・mirror)、`vector`・`point`・`axis`・`angle_deg`・`factor`・`normal`、`duplicate` | 番号は変わらない(`duplicate=true` で写しを作って変形)。パッチは付いたまま |
| `remove` | `entities*` | 消した形状 |
| `import_cad` | `path*`(.step・.stp・.iges・.igs・.brep・.brp)、`heal` | 体積・面の数、面積 0 の面。ファイルは `.gsis_mcp/imports/<SHA-256 の先頭 16 桁>.拡張子` に写し、履歴はその写しを指す(元のファイルが変わってもやり直せる) |
| `run_python` | `code*` | (`--allow-python` のときだけ)§11 |

断面の輪(`outer`・`holes` の要素)は次のどれか 1 つ。最後の点から始点へは自動で閉じる。

| 形 | 例 |
|---|---|
| 折れ線 | `{"points": [[0, 0], [100, 0], [100, 100], ...]}` |
| 直線と円弧 | `{"start": [0, 0], "segments": [{"line_to": [4, 0]}, {"arc_to": [0, 4], "center": [0, 0]}, {"arc_to": [-1, 0], "through": [-0.7, 0.7]}]}`(`center` の円弧は `ccw`(既定 true)で向きを決める) |
| 円 | `{"circle": {"center": [1, 1], "radius": 0.5}}` |

断面は作る前に、自己交差・面積 0・穴が外にはみ出ていないかを調べる。平面 `xz` は u = x、v = z(法線は −y)。回転体の子午面は `plane: "xz"` で (r, z) を点にし、`revolve` を z 軸(`axis_dir: [0, 0, 1]`)で行う。

### 6.3 計測・検査

| ツール | 主な引数 | 返り値 |
|---|---|---|
| `measure` | `entities`、`expected_volume`、`rtol` | 体積ごとの体積・表面積・重心・外接箱・面の数・殻の数(空洞があると 2 以上)、合計、期待値との相対誤差。`rtol` を超えたらエラー(batch の検算の手順に使う) |
| `list_faces` | `select`、`limit`(既定 60)、`offset` | 面ごとに `id`・`type`・`area`・`c`(重心)・`n`(平らな面の外向き法線)・`r`・`axis`(円柱)・`r`・`center`(球)・`bbox`・`patch`(・内部の面は `volumes`、CAD の色は `color`)。`select` を与えると条件に合う面だけと、条件ごとの残りの数 `filter_counts` |
| `check_geometry` | `mesh_test` | 体積が正か、面積 0 の面、長さ 0 の曲線(球の極の退化した曲線は除く)、体積に属さない面、重なっている体積(別のモデルで積をとって調べる)、殻の数。`mesh_test` で粗い面のメッシュを作り、各体積の三角形が閉じているかも調べる |

外向き法線は、面の体積の殻の中での向き(`getBoundary` の符号)× 曲面のパラメータの法線(∂/∂u × ∂/∂v)で求める(gmsh の `getNormal` は、押し出す前の断面など、面が最初に作られたときの向きを保つので外向きとは限らない)。この方法は、面上の点から法線の向きに少し進んだ点が体積の内か外かで決める方法と、11 種類の形状の 312 面で一致した。念のため、毎回 12 面までをその方法で確かめ、食い違えばすべての面をその方法で求め直す。円柱・球の半径・軸・中心は主曲率から求める。

### 6.4 境界パッチ

| ツール | 主な引数 | 返り値 |
|---|---|---|
| `assign_patch` | `name*`、`select*`、`kind`、`mode`(set・add・remove)、`steal` | パッチの面・面積・一致した数、付いていない面の数と番号。1 つの面は 1 つのパッチにだけ入る(別のパッチの面は `steal: true` で移す) |
| `remove_patch` | `name*`(`'*'` ですべて) | |
| `list_patches` | | パッチごとの面と面積、付いていない面の一覧(種類・面積・重心など)、`complete`(すべての境界の面にパッチがあるか) |

`kind` は wall・inlet・outlet・symmetry・patch のいずれか(`.pmesh` の規約。参考情報で、境界条件はケースの TOML で決める)。省略すると名前から推定する(`wall*`→wall、`inlet*`→inlet、`outlet*`→outlet、`sym*`→symmetry、それ以外は patch)。

**面の選び方(`select`)**: 指定した条件をすべて満たす面。既定では計算領域の境界の面(体積 1 つに属する面)だけが対象。

| 条件 | 意味 |
|---|---|
| `faces` | 面の番号の並び |
| `volumes` | この体積(名前か番号)の面に限る |
| `type` | plane・cylinder・cone・sphere・torus・bspline・bezier・revolution・extrusion・other |
| `on_plane` | `{point, normal}` の平面の上にある平らな面(重心が平面上にあり、法線が平行) |
| `normal` | 外向き法線がこの向きの平らな面 |
| `in_box` | 外接箱が `[xmin, ymin, zmin, xmax, ymax, zmax]` に収まる面 |
| `contains_point` | この点を通る面(距離 `tol` 以内で、面の範囲の中) |
| `radius`・`axis` | 円柱・球の半径、円柱の軸の向き |
| `patch`・`unassigned` | このパッチの面、どのパッチにも入っていない面 |
| `color` | CAD で付いた面の色 `[r, g, b(, a)]`(STEP の色で境界を渡すとき) |
| `area_min`・`area_max` | 面積の範囲 |
| `tol`・`angle_tol_deg` | 長さの許容差(既定はモデルの外接箱の対角の 1e-6)、向きの許容差(既定 1°) |
| `include_internal` | true で 2 つの体積に挟まれた内部の面も対象にする(細分化の対象など) |

条件に合う面がなければエラーにし、`filter_counts`(条件ごとの残りの数)を返す。最後に `{"unassigned": true}` で残りを壁にまとめるのが簡単。

### 6.5 画像

| ツール | 主な引数 | 返り値 |
|---|---|---|
| `render` | `target`(geometry・mesh)、`view`(auto・iso・iso_back・front・back・right・left・top・bottom)か `azimuth`・`elevation`か `direction`、`section`(`{point, normal}`)、`color_by`(patch・face・volume)、`labels`(auto・faces・patches・none)、`focus`、`zoom`、`width`・`height`、`path`、`inline` | PNG のパス(作業フォルダからの相対)、大きさ、三角形の数、凡例の色。`inline: true` で画像も応答に入れる |

§9 に詳しく書く。

### 6.6 メッシュ

| ツール | 主な引数 | 返り値 |
|---|---|---|
| `mesh_settings` | `size_max`(既定は外接箱の対角の 1/20)、`size_min`、`curvature_points`(円周あたりの要素数)、`algorithm_3d`(delaunay・hxt・frontal)、`algorithm_2d`、`optimize`(netgen・gmsh・none、既定 netgen)、`extend_from_boundary`、`threads`、`reset` | 設定の全体(指定した項目だけ変える) |
| `mesh_refine` | `name*`、`target`(faces・box・ball・cylinder)、`size`、`size_far`、faces: `select`・`dist_min`・`dist_max`・`sampling`、box: `box`・`thickness`、ball: `center`・`radius`・`thickness`、cylinder: `base`・`axis`・`radius`、`remove` | 場所ごとの要素寸法。同じ名前で置き換え、`remove: true` で消す。複数あれば最も細かいものが効く |
| `mesh_generate` | `dim`(3・2)、`max_cells`(既定 200 万) | 四面体の数の見積り(`estimate`)が上限を超えたら作らずにエラー。節点・四面体の数、メッシュの体積と形状の体積の相対差、品質 minSICN(最小・1% 点・0.1 未満の数)、h_V = V^(1/3) の最小・平均・最大、パッチごとの三角形の数、時間 |
| `mesh_quality` | | `.pmesh` と同じ形に変換して、セル数・要素の内訳・体積・パッチ(面の数と面積)・h_V・最大辺・非直交角(平均・99%・最大)・歪み・体積比・形式の検査 |
| `export_pmesh` | `path*`、`patch_order`、`renumber`(既定 true、逆 Cuthill–McKee)、`report`(.md)、`vtu`、`boundary_vtu` | `.pmesh`(座標は m)と検査の結果、セル・面・節点の数、体積 [m³]、パッチの面の数と面積 [m²] |
| `export` | `path*`(.step・.stp・.iges・.igs・.brep・.stl・.msh・.vtu)、`binary` | 形状(STEP・IGES・BREP)とメッシュ(STL・MSH 4.1・VTU)。MSH・STL にはパッチ名の Physical Group(付いていない面は `unassigned`)と体積 `fluid` |

### 6.7 GUI 専用(`--gui-tools` のときだけ。2026-10-18 に追加)

| ツール | 主な引数 | 返り値 |
|---|---|---|
| `gui_surface` | `known`(前回の `key`) | GUI の 3D 表示用の表面: 三角形(`render` と同じもの。メッシュがあればその表面、なければ粗い面のメッシュ)と三角形ごとの面の番号、面の一覧(番号・種類・境界か・面積・重心)、パッチ(名前・kind・面)、`key`(形状とメッシュの版)、`source`(geometry / mesh)。配列はリトルエンディアンの生のバイト列を base64 にしたもの(点 float32 (N, 3)、三角形 int32 (M, 3)、面の番号 int32 (M,))。`known` が今の `key` と同じなら配列を省き `unchanged: true` を返す(パッチの対応は毎回返す) |

- AI には見せない: tools/list の `_meta` に `{"gsis-pbc/gui-only": true}` の印を付け、GUI は AI に渡すツールの一覧から外す。`--gui-tools` なしのサーバーでは一覧に出ず、呼ぶと「未知のツール」。batch の中では使えない
- 読むだけの手順(履歴に残らない)。管の形状で三角形 8,176 個・応答 約 240 KB・0.14 s(1 回目)

## 7. 使い方の例

円管 → 真空(R = 1 mm、論文 §4.1)。エージェントは次の順にツールを呼ぶ(引数は JSON)。

```
model_new      {"name": "tube", "unit": "mm"}
batch          {"steps": [
                 {"tool": "add_cylinder", "args": {"base": [0,0,0],  "axis": [8,0,0],  "radius": 8, "name": "upstream"}},
                 {"tool": "add_cylinder", "args": {"base": [8,0,0],  "axis": [10,0,0], "radius": 1, "name": "pipe"}},
                 {"tool": "add_cylinder", "args": {"base": [18,0,0], "axis": [4,0,0],  "radius": 4, "name": "downstream"}},
                 {"tool": "boolean", "args": {"op": "fuse", "objects": ["upstream"], "tools": ["pipe", "downstream"], "name": "tube"}},
                 {"tool": "measure", "args": {"expected_volume": 1840.97329500, "rtol": 1e-9}}]}
check_geometry {"mesh_test": true}
list_faces     {}
assign_patch   {"name": "inlet",  "select": {"on_plane": {"point": [0,0,0],  "normal": [1,0,0]}}}
assign_patch   {"name": "outlet", "select": {"on_plane": {"point": [22,0,0], "normal": [1,0,0]}}}
assign_patch   {"name": "wall",   "select": {"unassigned": true}}
list_patches   {}                                         → complete: true
render         {"section": {"point": [0,0,0], "normal": [0,-1,0]}}
mesh_settings  {"size_max": 0.6}
mesh_refine    {"name": "pipe", "target": "cylinder", "base": [7,0,0], "axis": [12,0,0], "radius": 1.4, "size": 0.12}
mesh_refine    {"name": "mouths", "target": "faces", "select": {"type": "cylinder", "radius": 1},
                "size": 0.12, "size_far": 0.6, "dist_min": 0.3, "dist_max": 5}
mesh_generate  {}
mesh_quality   {}
export_pmesh   {"path": "tube/tube.pmesh", "report": "tube/tube_quality.md"}
model_save     {"path": "tube/tube_state.json"}
```

DGL(動的ガスロック)は子午面の断面を回す:

```
model_new    {"name": "dgl", "unit": "mm"}
add_profile  {"plane": "xz", "outer": {"points": [[0,0],[100,0],[100,100],[3.5,100],[5.2,108.5],[100,108.5],[100,111.5],
              [5.8,111.5],[7.5,120],[100,120],[100,220],[0,220],[0,90],[50,90],[50,88],[0,88]]}, "name": "meridian"}
                                                          → pappus_volume_v_axis: 6363424.181
revolve      {"entities": ["meridian"], "axis_dir": [0,0,1]}    → faces: 14, rel_error: 6.3e-11
measure      {}                                           → shells: 2(外側とウェハの空洞)
assign_patch {"name": "inlet", "select": {"type": "cylinder", "radius": 100, "in_box": [-101,-101,108,101,101,112]}}
assign_patch {"name": "outlet_top", "select": {"on_plane": {"point": [0,0,220], "normal": [0,0,1]}}}
assign_patch {"name": "outlet_bottom", "select": {"on_plane": {"point": [0,0,0], "normal": [0,0,1]}}}
assign_patch {"name": "wall", "select": {"unassigned": true}}
```

球まわり(半径 5 m の球から直径 1 m の球を抜く)は `add_sphere` 2 つと `boolean`(cut)、パッチは `{"type": "sphere", "radius": 5}` と `{"type": "sphere", "radius": 0.5}`。

3 つの全手順は `cases/mcp_demo/run_demo.py`(§14)。

検算のこつ:

- 体積は解析値と比べる(作るツールの `rel_error`、`measure` の `expected_volume`、回転体はパップスの定理)。OpenCASCADE の体積は B-rep から求めるので、正しく作れていれば相対誤差は 1e-9 より小さい
- 面の選択は `list_faces` の `select` で試してから `assign_patch` する。`list_patches` の `complete` が true になるまで付ける
- 中の形(空洞、狭い流路)は `render` の `section` と `focus` で見る。`measure` の `shells` で空洞の数を確かめる

## 8. 状態の仕組み

- **履歴**: モデルを変える手順(形状・パッチ・メッシュの設定・細分化)を、検証済みの引数(既定値を埋めたもの)とともに記録する。読むだけの手順(計測・画像)と、ファイルやメッシュを作る手順(`mesh_generate`・`export*`・`model_save`)は記録しない
- **やり直し**: gmsh / OpenCASCADE は同じ手順から同じ番号の形状を作るので、元に戻す(`undo`)・状態の読み込み(`model_load`)・失敗した手順の巻き戻しは、新しい gmsh のモデルで履歴を最初からやり直して行う(穴あけのブーリアン 30 回を含む 60 手順のやり直しで 0.3 s。大きな CAD の取り込みや重いブーリアンが多いと遅くなる)
- **失敗した手順**: 途中まで変わったモデル(点や線だけ作られた、など)は、モデルの指紋(形状の一覧と OpenCASCADE の最大の番号)を比べ、変わっていればやり直して戻す(gmsh の 1 回の呼び出し、たとえば変形やブーリアンは、失敗すればモデルを変えない)。`run_python` の失敗は常にやり直す
- **パッチの追跡**: パッチは面の番号と、その面の面積・重心(指紋)で持つ。形状を変える手順の後、面が消えたか指紋が変わった面はパッチから外し、応答の `patch_changes` で知らせる。`transform`(移動・回転・拡大・鏡映)では面は同じなので、指紋を更新してパッチを保つ。パッチは形状を確定させてから付ける
- **メッシュ**: 形状を変えると消える。パッチやメッシュの設定を変えても残る(`model_info` の `mesh.stale_settings` で設定が変わったことを示す)
- **状態のファイル**(`model_save`): `{"format": "gsis-mcp-model", "version": 1, "name", "unit", "history": [{"tool", "args", "group"}]}`。`group` は batch の手順のまとまり(undo の単位)。読み込むときに手順の引数を検証し直す。`run_python` を含むファイルは `--allow-python` のサーバーでだけ読める

## 9. 画像(render)

- 画面を出さずに描く: 三角形を numpy の z バッファで描き(縦横 2 倍で描いて縮める)、文字・凡例・軸の向きを matplotlib(Agg)で重ねる。OpenGL もウィンドウも使わない
- 形状(`target: geometry`)は粗い面のメッシュ(外接箱の対角の 1/45、曲率で 24 分割)で描く。形状に合うメッシュがあればそれを使う。粗いメッシュは描いた後に消すので、形状の番号は変わらない
- 面の色は既定でパッチ(付いていない面は灰色)。面の境目に黒い線を引き、文字(既定は面の番号。60 面を超えると出さない)は面の見えている部分の中心に置く。左下に x・y・z の向き
- **断面** `section: {point, normal}`: 法線の側を取り除く(画素ごとに切る)。形状では切り口を灰色で塗る(三角形を平面で切った線分を偶奇規則で塗る。空洞は穴になる)。メッシュ(`target: mesh`)では、重心が残す側にある四面体だけを描き、切り口に四面体の面を見せる(切り口の面は薄い黄色、境界はパッチの色)
- **視点**: `view: auto` は、断面なしなら iso(方位角 −60°、仰角 25°)、断面ありなら取り除いた側から少し斜めに見る。`front` は −y から +y を見る(x が右、z が上)。名前の視点で断面を指定して、カメラが残した側にあると切り口が見えないので、応答の `note` で知らせる
- `focus: [xmin, ymin, zmin, xmax, ymax, zmax]` でその箱が画面に収まるように拡大する(狭い流路などを見る)
- 大きさは既定 900 × 700(PNG で 20〜330 KB)。描く時間は 1〜2 秒(三角形 2 万個程度)

## 10. メッシュ

- 要素は 1 次の四面体だけ(gmsh の Delaunay + Netgen の最適化が既定)
- 寸法は、全体の上限 `size_max`・下限 `size_min`、曲率(`curvature_points`)、細分化(gmsh の寸法の場: faces は Distance + Threshold、box・ball・cylinder は同名の場。まとめて Min)で決まる
- **セル数の見積り**: 2 次元メッシュを作った後、体積の中の点(乱数で 800 点)で目標の寸法 s を求め、N ≈ C·V·平均(1/s³)(C = 4.2、HXT は 3.6。`geometry/tube.py` と同じ実測値)。曲率による細分化は見積りに入らないので、実際は見積りの 0.9〜1.5 倍程度になった(管 0.95、DGL 1.5)。`max_cells` は誤操作で巨大なメッシュを作って止まらないようにするための上限
- `.pmesh` への変換は既存の `gsis_tools.gmsh_convert.polymesh_from_gmsh`(パッチは 2 次元の Physical Group、体積は `fluid`)、検査は `gsis_tools.pmesh.check_mesh`、品質は `gsis_tools.quality` をそのまま使う。書き出す前にすべての境界の面にパッチがあることを確かめ、なければ付いていない面の番号を返す
- 座標はモデルの単位から m に換算する(`metadata` に `model_unit`・`scale_to_m`・`model`・`steps` を入れる)
- 複数のスレッドで作るので、同じ設定でもセル数が少し変わることがある(管で 184,855・185,144・185,656)
- ソルバー・GUI との連携: 書き出した `.pmesh` はケースの TOML の `[case] mesh`(TOML の場所からの相対パス)に指定し、`[[boundary]]` の `patch` をパッチ名に合わせる。GUI(`gsis-gui`)のメッシュの画面で開いて品質を見たり、「ケースに使う」で設定したりできる(GUI の読み込みの処理 `gsis_tools.gui.meshgen.load_preview` で 3 つのデモのメッシュを開けることを確かめた。画面そのものは開いていない。GUI の品質の表は管半径 R を単位にするので、管以外では寸法の単位が R = 1 mm になる)

## 11. run_python(既定では無効。危険)

`--allow-python` で起動したときだけ、ツール `run_python` が一覧に出る。gmsh の Python API を使うコードをサーバーのプロセスの中で実行する(変数 `gmsh`・`occ`(= `gmsh.model.occ`)・`np`・`math`、名前を付ける `names.set('名前', [(3, tag)])`・`names.get('名前')`)。標準出力に書いたものを応答で返す(4,000 文字まで)。

**危険**: 任意の Python が、サーバーを起動した利用者の権限で動く。作業フォルダの制限は効かない(ファイルの読み書き・削除・外部への通信・プログラムの起動ができる)。エージェントの誤りやプロンプトインジェクションで、意図しない操作が起こり得る。

- 信頼できる環境で、ツールで足りない操作(フィレット、ロフト、gmsh の特殊な機能など)を試すときだけ使う
- 失敗したら(例外が出たら)モデルはスクリプトの前に戻す。成功したコードは履歴に残り、元に戻す・保存・読み込みでやり直される(コードが決まった結果を出すことが前提)
- `gmsh.finalize()`・`gmsh.clear()` や、別のモデルへの切り替えはしない

## 12. 実装

### 12.1 構成

| モジュール | 内容 |
|---|---|
| `gsis_tools/mcp/__init__.py` | 入口 `main()`(`gsis-mcp`)。引数を読み、標準入出力を守ってからサーバーを読み込む |
| `config.py` | 起動の引数 |
| `server.py` | MCP サーバー(SDK の低水準の `Server`、`on_list_tools`・`on_call_tool`)。ツールの呼び出しは gmsh 専用の 1 つのスレッドで、届いた順に 1 つずつ実行(gmsh はスレッドに安全でない。クライアントが 12 個を同時に呼んでも、名前と番号が重ならないことを確かめた)。SDK の非同期ループは止めない |
| `tools.py` | ツールの引数(pydantic のモデル)、説明、注記(読むだけ・破壊的など)、引数の検証と正規化、サーバーの説明(instructions)。スキーマは pydantic の title と `anyOf: [X, null]` を除いて小さくしている |
| `engine.py` | gmsh のモデル、履歴とやり直し、名前、形状・計測・パッチ・メッシュ・書き出し・画像のツールの実装 |
| `faces.py` | 面の情報(種類・外向き法線・半径など)と選択 |
| `meshing.py` | メッシュの設定、寸法の場、セル数の見積り、メッシュの要約 |
| `render.py` | 画面を出さずに描く画像 |
| `workspace.py` | 作業フォルダの制限 |
| `stdio_guard.py` | 標準入出力の保護(§12.2) |
| `errors.py` | エージェントに返すエラー(`ToolFailure`、`BatchFailure`) |

### 12.2 標準出力の保護

stdio の MCP では標準出力が通信路になる。OpenCASCADE は STEP の読み書きなどで、gmsh の `General.Terminal = 0` でも C の標準出力に統計を書く。Windows の gmsh(`gmsh-4.15.dll`)は MinGW で作られていて、システムの `msvcrt.dll` の標準出力を使う。`msvcrt.dll` はプロセスの起動時に標準のハンドルを覚えるので、MCP SDK の `stdio_server()` が行う Python(UCRT)の fd 1 の付け替えでは止まらず、(SDK の既定のままだと)閉じたハンドルの番号に書くことになる(試した範囲では出力は失われた。ハンドルの番号が使い回されると別のファイルに混ざるおそれがある)。

そこで、gmsh を読み込む前に、(1) 通信路として fd 0・1 の複製を作り、(2) `msvcrt.dll` の fd 1 を標準エラーに、fd 0 を NUL に付け替え、(3) UCRT の fd 0・1 と Win32 の標準ハンドルも同じ先に合わせ(2 つの CRT が同じハンドルの番号を持つので、閉じたハンドルの番号が残らないように確かめながら)、(4) 通信路の複製を SDK の `stdio_server(stdin=, stdout=)` に渡す。標準入力も NUL にするのは、gmsh の DLL が読み込みの時に標準入力を調べ、通信路の読み取りと競合して 11 秒止まったため(SDK も標準入力を NUL にしている)。この結果、STEP の統計は標準エラーに出て、通信路の各行はすべて JSON になる(試験 `test_mcp_stdio.py::test_stdio_raw_jsonrpc_lines`)。Windows 以外では SDK の既定の処理を使う。

### 12.3 依存

extra `mcp` に `mcp>=2.3.0`(公式の MCP Python SDK v2。pydantic・anyio などが入る)を足した(`uv add --optional mcp mcp>=2.3.0`、`uv.lock` も更新)。gmsh・numpy・scipy・matplotlib は既存の依存。画像に OpenGL・VTK は使わない。

## 13. 試験

```
uv run --project python --extra mcp pytest python/tests                         # すべて(173 件、約 65 秒)
uv run --project python --extra mcp pytest python/tests/test_mcp_engine.py      # ツールを関数として呼ぶ試験
uv run --project python --extra mcp pytest python/tests/test_mcp_stdio.py       # stdio の MCP クライアントで
```

| ファイル | 内容 |
|---|---|
| `test_mcp_workspace.py`(22 件、うち 1 件はシンボリックリンクを作れない環境で飛ばす) | 書き出し・読み込みのパスの制限(`..`、絶対パス、UNC、デバイス名、代替データストリーム、8.3、末尾の `.`、内部用のフォルダ、拡張子、ジャンクション・シンボリックリンクで外に出るもの) |
| `test_mcp_engine.py`(21 件) | ツールのスキーマ、基本形状の体積、管の面(外向き法線・半径・軸)と選択とパッチの面積、外向き法線の速い方法が誤ったときの求め直し、DGL の回転(14 面・パップスの定理・殻 2・検査)、球殻、断面(円弧・穴・自己交差・面積 0)、名前と参照、失敗したブーリアンでモデルが変わらないこと、batch の巻き戻し(番号も元どおり、引数の誤りは実行前、検算の手順)、undo とやり直しで番号が同じ、保存と読み込み(壊れたファイルでは元のまま、変なモデル名は置き換え)、変形とパッチの追跡、重なりの検査と fragment、STEP・BREP・IGES の往復と単位の換算、メッシュと `.pmesh`(読み直して検査、パッチ名・種類・面積・体積)、セル数の上限、MSH・STL・STEP、面による細分化、画像、run_python |
| `test_mcp_stdio.py`(3 件) | サーバーを子プロセスで起動し、SDK のクライアントで initialize → tools/list(ツール名・注記・スキーマ)→ tools/call(batch、失敗した batch の巻き戻し、パッチ、STEP、画像、メッシュ、`.pmesh`、エラー)。`--allow-python`。生の JSON-RPC で STEP の書き出し・読み込みの後も標準出力のすべての行が JSON(改行は LF)であること |

既存の試験(127 件)はそのまま通る。

## 14. 確認(`cases/mcp_demo/`)

`cases/mcp_demo/run_demo.py` は、MCP サーバーを子プロセスとして起動し、MCP のツールだけで 3 つの計算領域を作って `.pmesh` を書き、`gsis-tools mesh-info` で検査する(作業フォルダは `cases/mcp_demo/`)。

```
uv run --project python --extra mcp python cases/mcp_demo/run_demo.py             # 約 1.5 分
uv run --project python --extra mcp python cases/mcp_demo/run_demo.py --launch uv # サーバーを uv run ... gsis-mcp で起動
```

結果(2026-10-18、`--launch uv`。`cases/mcp_demo/results.md`・`results.json`):

| ケース | 形状の体積の相対誤差 | パッチ(境界の三角形の数) | セル数 | メッシュの体積の相対差 | `mesh-info` | MCP の呼び出し / 時間 |
|---|---|---|---|---|---|---|
| 管 → 真空(R = 1 mm) | 1.2e−16(586π mm³) | inlet 1,342、outlet 436、wall 20,148 | 185,656 | −8.4e−4 | OK | 20 回 / 19 s |
| DGL(子午面の回転、mm) | 6.3e−11(パップスの定理 6.363424181e6 mm³) | inlet 1,578、outlet_top 2,077、outlet_bottom 2,081、wall 109,634 | 521,782 | −4.4e−4 | OK | 29 回 / 50 s |
| 球まわり(m) | 0(4/3 π (125 − 0.125) m³) | far_field 3,176、sphere 4,566 | 122,126 | −3.5e−3 | OK | 19 回 / 12 s |

- DGL の回転体は面 14 枚(退化面なし)、殻 2(外側とウェハの空洞)。`check_geometry(mesh_test=true)` も OK。STEP に書いて別のモデルに読み直しても体積の相対誤差 −1.7e−14・面 14 枚で、修復なしで読めた(solidcraft の回転体で起きた、軸上に 2 区間で接する断面の退化面は出ない)
- パッチの面積(.pmesh)と解析値の差は、平らな円の面で −6e−4〜−4e−3(円周の多角形近似による。DGL の入口の円筒面は −8e−6)
- メッシュの体積が形状の体積より小さいのは、曲面を三角形で近似するため
- 各ケースのフォルダに、`.pmesh`、品質レポート、状態の JSON(`model_load` で作り直せる)、画像(形状・断面・メッシュの断面)、MCP の呼び出しと応答の記録 `transcript.jsonl`。応答の大きさは 40 B〜2 KB(面の一覧が最大)、tools/list は 29 KB

## 15. 制限と今後の課題

- **CAD 機能**: フィレット、面取り、ロフト、シェル、スイープはツールにない(`run_python` なら gmsh の機能で可能)。要るなら build123d(+ build123d-mcp)を検討する(spec §1.3.1)
- **IGES**: OpenCASCADE は IGES を面の集まりとして読み書きすることが多い。面だけの IGES は体積を作れず「体積がありません」と返す(面を縫い合わせて体積にする処理は、空洞のある形で正しく作れなかったので入れていない)。STEP か BREP で渡す
- **メッシュの種類**: 1 次の四面体だけ。六面体・プリズム層(境界層)・2 次要素はない。管の構造六面体メッシュは既存の `gsis-tools mesh tube --kind hex` を使う
- **セル数の見積り**は曲率による細分化を入れないので、実際より少なめに出ることがある(1.5 倍まで確かめた)
- **取り消し**: やり直し(redo)はない。取り消しは履歴のやり直しなので、重いブーリアンや大きな CAD の取り込みが多いと時間がかかる
- **長い処理の中断**: メッシュの生成は途中で止められない(クライアントが取り消しても、gmsh のスレッドは最後まで動く。接続が切れたら待たずに終わる)
- **モデルは 1 つ**: 1 つのサーバーで同時に扱うモデルは 1 つ(状態の保存・読み込みで切り替える)
- **面の情報**は面ごとに gmsh を何度か呼ぶ。穴が 484 個の板(490 面)で、面の一覧(形状を変えた後の 1 回目)が 0.3 s、画像が 8 s、形状を作る batch(485 手順)が 11 s。数千面の CAD では試していない
- **パッチの追跡**は面の指紋(面積と重心)による。形状を変えるとパッチが外れるので付け直す(外れた面は知らせる)
- **GUI との連携**は `.pmesh` と状態の JSON を介する。GUI の中からエージェントを呼ぶ機能、GUI で MCP の状態を開く機能はない(このエンジンを GUI のメッシュの画面に組み込むのが次の候補)
- **画像**は面を平らに塗る(陰影は三角形ごと)。透明・半透明、等値面、流れの結果の表示はない(結果の表示は GUI と ParaView)
