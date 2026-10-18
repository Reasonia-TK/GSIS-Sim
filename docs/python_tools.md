# Python ツール(gsis_tools)の使い方

メッシュの生成・変換・検査を行う Python パッケージ `gsis_tools`(`python/`、uv で管理)の使い方。
実行はリポジトリのルートから `uv run --project python ...`。

## 1. コマンド(`gsis-tools`)

| コマンド | 内容 |
|---|---|
| `gsis-tools mesh tube` | 管ケース(円管 → 真空)のメッシュを生成する |
| `gsis-tools mesh slab` | 平板メッシュ(1 次元の検証用)を生成する |
| `gsis-tools mesh-info FILE.pmesh` | `.pmesh` を検査し、セル数・品質を表示する |
| `gsis-tools convert FILE.msh` | gmsh の `.msh` を `.pmesh` に変換する |

共通の出力オプション: `--out`(`.pmesh`)、`--vtu`(セルの VTU。品質の指標をセルデータとして持つ)、
`--boundary-vtu`(境界面の VTU。`patch_id` を面データとして持つ)、`--report`(品質レポート、Markdown)。
`-v` を付けると詳しいログを出し、エラー時に例外の詳細を表示する。

### 1.1 管ケース

```
uv run --project python gsis-tools mesh tube --kind hex --target-cells 135000 \
    --out cases/tube_vacuum/mesh/tube_hex.pmesh --vtu cases/tube_vacuum/mesh/tube_hex.vtu \
    --boundary-vtu cases/tube_vacuum/mesh/tube_hex_boundary.vtu --report cases/tube_vacuum/mesh/tube_hex_quality.md
uv run --project python gsis-tools mesh tube --kind tet --target-cells 135000 --out cases/tube_vacuum/mesh/tube_tet.pmesh ...
```

形状(論文 §4.1、長さは管半径 R 単位): x 軸が管の軸。上流槽 半径 8・x ∈ [0, 8]、管 半径 1・x ∈ [8, 18]、
下流槽 半径 4・x ∈ [18, 22]。パッチは `inlet`(x = 0 の端面)、`outlet`(x = 22 の端面)、`wall`(それ以外)。
`--radius` で R [m] を指定する(既定 1e-3)。

| 方式 | 内容 |
|---|---|
| `--kind hex` | マルチブロックの構造六面体。断面は O 型格子(中心の正方形 + 管壁までのリング + 槽の円環。正方形の境界の節点を等角度に置くので、リングと円環の半径方向の線は原点からの直線になる)。断面を x 方向に掃引する。numpy で直接作るので gmsh は使わない(`--msh` で `.msh` も書ける) |
| `--kind tet` | 非構造の四面体。gmsh(OpenCASCADE)で円柱 3 つの和を作り、管(r ≤ R, 8R ≤ x ≤ 18R)からの距離でセル寸法を変える。既定は Delaunay + Netgen の最適化 |

`--target-cells` を与えると、セル数が目標に近くなるように細かい部分を調整する。

- hex: 壁(`--ds-wall`)と管の出入口(`--ds-mouth`)の最初のセル幅、管の中の軸方向の間隔の上限(`--h-tube`)を
  同じ倍率で変える。周方向の分割(`--n-theta`、1/4 周あたり。既定 16)と槽の中の間隔の上限(`--max-size`、既定 0.5R)は
  変えない。セル数は節点の配置だけから計算できるので、生成は 1 回(数秒)
- tet: 管の中のセル寸法 `h_tube` を変える。セル数を N ≈ C∫dV/s³ で見積もって生成し、結果で C を補正して作り直す
  (最大 3 回、1 回 10〜15 秒)
- メッシュ依存性を調べるときは、hex の `--refine F`(周方向の分割を F 倍、全ての間隔を 1/F 倍)を使う

「セル寸法」は h_V = V^(1/3)(セル体積の 3 乗根)で表す。四面体では gmsh のメッシュサイズを 1.7 h_V にしている
(実測で gmsh の四面体は平均 h_V ≈ 0.6 × サイズ)。品質レポートには辺の長さ(最大・平均・最小)も併記する。

### 1.2 平板

```
uv run --project python gsis-tools mesh slab --length 1e-3 --nx 100 --out cases/slab/mesh/slab_nx100.pmesh
uv run --project python gsis-tools mesh slab --length 1e-3 --nx 100 --first-cell 1e-6 --out cases/slab/mesh/slab_nx100_wall.pmesh
uv run --project python gsis-tools mesh slab --length 1e-3 --nx 100 --cos-stretch 1 --out cases/slab/mesh/slab_nx100_cos.pmesh
```

x ∈ [0, L] を nx セル、y・z は 1 セル(厚さ `--thickness`、既定 L/nx)。壁の近くを細かくする方法は 2 つ(左右対称):

- `--first-cell D`: tanh 分布。両壁に接するセルの幅が D になるように分布の強さを決める
- `--cos-stretch s`(0〜1): 余弦分布 x/L = (1−s)t + s(1−cos πt)/2。`verification/ref1d` の `kind="cos"` と同じ式
  (s = 1 で Chebyshev–Lobatto 点)
パッチは `wall_left`(x = 0)、`wall_right`(x = L)、`sym_y`(y の 2 面)、`sym_z`(z の 2 面)。
セル番号は x の小さい順(付け直さない)。

### 1.3 変換と検査

```
uv run --project python gsis-tools convert model.msh --out model.pmesh --patch-kind walls=wall --patch-order inlet,outlet,walls
uv run --project python gsis-tools mesh-info cases/tube_vacuum/mesh/tube_hex.pmesh --report info.md
```

- 1 次要素の四面体・六面体・プリズム・ピラミッドに対応(高次要素はエラー)
- 境界面は 2 次元の Physical Group(名前)からパッチに割り当てる。どのグループにも属さない境界面、
  複数のグループに属する境界面はエラー。境界面でない面がグループに含まれていたら無視して警告する
- 3 次元の Physical Group があれば、それに含まれる体積の要素だけをセルにする
- パッチの kind は `--patch-kind` で指定する。省略時は名前から推定する(`wall*` → wall、`sym*` → symmetry、
  `inlet*` / `outlet*`、それ以外は patch)
- 既定でセル番号を逆 Cuthill–McKee 順に付け直す(`--no-renumber` で gmsh の要素順のまま)

## 2. Python から使う

```python
from gsis_tools.pmesh import read_pmesh, write_pmesh, check_mesh
from gsis_tools.geometry.tube import TubeDims, TubeHexOptions, generate_tube_hex
from gsis_tools.quality import compute_quality, format_report
from gsis_tools.vtu import write_vtu

mesh, info = generate_tube_hex(TubeDims(radius=1e-3), TubeHexOptions(), target_cells=135_000)
g = mesh.geometry()                 # 面積ベクトル・面中心・体積・重心(docs/pmesh_format.md §4)
print(check_mesh(mesh).format())    # §3・§4 の検査
write_pmesh("tube.pmesh", mesh)      # 書く前に検査し、エラーがあれば PMeshError
q = compute_quality(mesh)
write_vtu("tube.vtu", mesh, q.cell_data())
```

| モジュール | 主な関数・クラス |
|---|---|
| `gsis_tools.pmesh` | `PolyMesh`、`Patch`、`write_pmesh`、`read_pmesh`、`read_header`、`face_geometry`、`cell_geometry`、`compute_geometry`、`check_mesh`、`PMeshError` |
| `gsis_tools.builder` | `polymesh_from_elements`(要素 + 境界グループ → `PolyMesh`)、`BoundaryGroup`、`ELEMENT_FACES` |
| `gsis_tools.gmsh_convert` | `polymesh_from_gmsh`(現在の gmsh モデル)、`read_msh`、`write_msh_from_elements`、`gmsh_session` |
| `gsis_tools.vtu` | `write_vtu`、`write_boundary_vtu` |
| `gsis_tools.quality` | `compute_quality`、`summary`、`format_report` |
| `gsis_tools.geometry.tube` | `TubeDims`、`TubeHexOptions`、`TubeTetOptions`、`generate_tube_hex`、`generate_tube_tet`、`build_tube_occ`、`ogrid_cylinder` |
| `gsis_tools.geometry.slab` | `generate_slab`、`slab_nodes` |
| `gsis_tools.geometry.box` | `box_mesh`、`box_hex_elements` |

### 2.1 `.pmesh` の読み書き(docs/pmesh_format.md v1)

- 書き手の dtype は §2 の例のとおり(`points` float64、`face_offsets` int64、`face_nodes`・`owner`・`neighbour` int32)。
  読み手は番号の配列について int32 と int64 の両方を受け付ける
- `read_pmesh` は形式の違反(マジック・版・dtype・形・オフセット・配列の重なり)を常にエラーにし、
  `check=True`(既定)なら `check_mesh` のエラーもエラーにする。警告はログ(`logging`)に出す
- `check_mesh` の内容: 配列の長さと番号の範囲、面の順序とパッチの範囲(§3-1)、`owner < neighbour` と上三角順(§3-2)、
  面の節点数と重複(§3-4)、セルの面の数(§3-5)、パッチ名の重複と kind(§3-7)、metadata が文字列であること、
  閉性(|ΣS_out| ≤ 1e-10 Σ|S_f|)、体積が正、内部面の向き(違反数を警告)、参照されない節点(警告)
- 同じセル対を結ぶ内部面が複数あってもよい(辞書順は非減少で判定し、数を `info` に出す)

### 2.2 品質の指標(`quality.py`)

| 指標 | 定義 |
|---|---|
| 非直交角 | 内部面: 面法線と d = x_N − x_O のなす角。境界面: 面法線と x_f − x_O のなす角 |
| 歪み | 内部面: OpenFOAM の定義(x_O と x_N を結ぶ線分が面の平面と交わる点と面中心の距離 / \|d\|)。境界面: (x_f − x_O) の面内成分 / (2 × 法線方向の距離) |
| 体積比 | 内部面ごとの max(V_O/V_N, V_N/V_O) |
| セル寸法 | h_V = V^(1/3)、セルの辺の長さの最大・最小・平均、最大辺 / 最小辺 |
| 要素の種類 | 面の構成から判定(四面体・ピラミッド・プリズム・六面体・その他の多面体) |

### 2.3 VTU

ParaView で開ける XML 形式(追記形式の生バイナリ)。四面体・ピラミッド・プリズム・六面体は VTK の標準のセル型
(節点順は面の構成から組み立て直す)、それ以外は `VTK_POLYHEDRON` で書く。セルの VTU は `cell_id`・`cell_volume`
と品質の指標(`cell_type`、`cell_size`、`max_edge`、`edge_ratio`、`max_non_orthogonality`、`max_skewness`)を持つ。
境界面の VTU のパッチ番号と名前の対応は、ファイル先頭のコメントにある。

## 3. テスト

```
uv run --project python pytest python/tests
```

`.pmesh` の書き込み → 読み込みと形式の配置、検査が規約違反を見つけること、箱の体積・重心、非平面の面、
円柱の体積の収束(O 型格子は多角形近似と厳密に一致し 2 次で収束、gmsh の四面体は単調に減少)、
gmsh 変換(四面体・六面体・プリズム・ピラミッド・混在、`.msh` 経由)、管のパッチ面積と体積、平板、
VTU を VTK で読み戻した体積、CLI。VTU の試験は開発用依存の `vtk` を使う。

## 4. 生成済みのメッシュ

| ファイル | 内容 |
|---|---|
| `cases/tube_vacuum/mesh/tube_hex.pmesh` | 管、構造六面体(`--kind hex --target-cells 135000`) |
| `cases/tube_vacuum/mesh/tube_tet.pmesh` | 管、四面体(`--kind tet --target-cells 135000`) |
| `cases/tube_vacuum/mesh/*_quality.md` | 品質レポート(セル数、領域別のセル寸法、非直交角などの統計) |
| `cases/tube_vacuum/mesh/*.vtu` | ParaView 用(セル、境界面) |
| `cases/slab/mesh/slab_nx100.pmesh` | 平板、L = 1 mm、一様 100 セル |
| `cases/slab/mesh/slab_nx100_wall.pmesh` | 平板、L = 1 mm、100 セル、壁に接するセルの幅 1 µm(tanh 分布) |
| `cases/slab/mesh/slab_nx100_cos.pmesh` | 平板、L = 1 mm、100 セル、余弦分布(s = 1) |

管メッシュ 2 種の要約(2026-10-15 生成時。詳細は各 `*_quality.md`、寸法は R 単位):

| 項目 | hex(構造六面体) | tet(四面体) |
|---|---|---|
| セル数(うち管の中) | 135,104(41,984) | 133,586(107,951) |
| 管の中の h_V 平均 | 0.086 | 0.065 |
| 管壁・出入口に接するセルの幅 | 0.045 | 集中なし(h_V ≈ 0.065 が一様) |
| 上流槽の遠方(管の入口から 3R 以上)の h_V 平均 / 最大 | 0.28 / 0.56 | 0.50 / 0.72 |
| 最大辺 | 0.79(r = 8R の周方向) | 1.83 |
| 非直交角 平均 / 99% / 最大 [deg] | 2.5 / 29 / 38 | 13.5 / 32 / 56 |
| 歪み 最大、隣接セルの体積比 最大 | 0.46、2.4 | 0.73、4.0 |
| 生成時間(8 コア) | 約 2 s | 約 14 s(目標に合わせて作り直すと 25〜30 s) |

## 5. 既知の制約

- hex: 構造格子なので、管に合わせた細かい断面(中心の正方形とリング)と r = R 付近の細い層が槽の全長に続く
  (槽の中で辺の長さの比が最大 18 程度)。非直交角の最大(約 38°)は O 型格子の正方形の角に出る
- tet: 壁に向かって細かくする層(プリズム層)はない。gmsh の境界層の押し出しは管の入口の角(凸の稜線)で
  扱いが難しいため実装していない
- 槽の中の「最大セル寸法 0.5R」は h_V で見ている。hex の外周の周方向の間隔は 2π·8R / (4·16) ≈ 0.79R

