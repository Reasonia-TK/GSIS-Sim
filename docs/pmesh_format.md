# pmesh 形式(内部メッシュ形式)v1

ソルバー(C++)と前処理(Python)の間でメッシュを受け渡すための形式。任意多面体の有限体積メッシュを、面を単位として表す(OpenFOAM の polyMesh に近い考え方)。

- 1 メッシュ = 1 ファイル、拡張子 `.pmesh`、リトルエンディアン
- 書き手: Python(`gsis_tools.pmesh`)。読み手: C++(`src/mesh`)と Python
- 読み手は、未知の `version`・未知の dtype・下記の規約違反を見つけたらエラーにする

## 1. ファイル構造

| 位置(バイト) | 内容 |
|---|---|
| 0–7 | マジック: ASCII の `PMESH` + 3 バイトの 0(`50 4D 45 53 48 00 00 00`) |
| 8–15 | uint64: JSON ヘッダのバイト数 H |
| 16 から H バイト | UTF-8 の JSON ヘッダ(§2) |
| 以降 | 配列データ。各配列はファイル先頭からの絶対オフセット(64 の倍数)に置き、間は 0 で埋める |

## 2. JSON ヘッダ

```json
{
  "format": "pmesh",
  "version": 1,
  "length_unit": "m",
  "n_points": 1000,
  "n_faces": 3000,
  "n_internal_faces": 2500,
  "n_cells": 800,
  "patches": [
    {"name": "inlet",  "kind": "inlet",  "start": 2500, "count": 100},
    {"name": "outlet", "kind": "outlet", "start": 2600, "count": 100},
    {"name": "wall",   "kind": "wall",   "start": 2700, "count": 300}
  ],
  "arrays": {
    "points":       {"dtype": "float64", "shape": [1000, 3], "offset": 1024},
    "face_offsets": {"dtype": "int64",   "shape": [3001],    "offset": 25024},
    "face_nodes":   {"dtype": "int32",   "shape": [12000],   "offset": 49088},
    "owner":        {"dtype": "int32",   "shape": [3000],    "offset": 97088},
    "neighbour":    {"dtype": "int32",   "shape": [2500],    "offset": 109120}
  },
  "metadata": {"generator": "gsis_tools 0.1.0", "created": "2026-10-15T12:00:00Z", "source": "tube.py"}
}
```

| 配列 | 内容 |
|---|---|
| `points` | 節点座標(単位は `length_unit`。v1 は `"m"` のみ) |
| `face_offsets` | 面 f の節点は `face_nodes[face_offsets[f] : face_offsets[f+1]]`。`face_offsets[0] = 0` |
| `face_nodes` | 面の節点番号を並べたもの |
| `owner` | 全ての面の owner セル |
| `neighbour` | 内部面の neighbour セル(長さ `n_internal_faces`) |

- dtype は `float64`・`int64`・`int32` のみ(すべてリトルエンディアン)。書き手は上の例のとおり(`points` は float64、`face_offsets` は int64、`face_nodes`・`owner`・`neighbour` は int32)にする。読み手は番号の配列(`face_offsets`・`face_nodes`・`owner`・`neighbour`)について int32 と int64 の両方を受け付ける
- 上の例のオフセットは、ヘッダの直後から 64 バイト境界で詰めて置いた場合の値。読み手は必ずヘッダの `offset` を使い、配置を仮定しない
- `metadata` は省略できる。値は文字列のみ。文字列以外の値は、読み手が警告して読み飛ばす。読み手は中身を解釈しない

## 3. 規約

1. **面の順序:** 内部面が先(`[0, n_internal_faces)`)、境界面が後。境界面はパッチごとに連続し、`patches` の順に並ぶ。最初のパッチの `start` は `n_internal_faces`、各パッチの `start` は前のパッチの `start + count`、最後のパッチは `n_faces` で終わる(境界面を過不足なく覆う)。
2. **内部面の向き:** `owner < neighbour`。内部面は `(owner, neighbour)` の辞書順で非減少に並べる(上三角順)。同じセルの組を結ぶ内部面が複数あってもよい(多面体メッシュで面が分割されている場合)。
3. **節点の順序:** 面の法線(右ねじの向き)が owner から neighbour へ向くように並べる。境界面では領域の外側を向く。
4. **面:** 3 節点以上で、同じ節点を重複して含まない。非平面の多角形も許す(幾何量は §4 の定義で計算する)。
5. **セル:** 各セルは 4 面以上を持ち、閉じている(§4 の外向き面積ベクトルの和がゼロ)。体積は正。
6. **番号:** 節点・面・セルは 0 始まり。どの面からも参照されない節点は置かないことを推奨する。
7. **パッチ:** `name` は一意で、ケース定義(TOML)の境界条件はこの名前に対して指定する。`kind` は `wall`・`inlet`・`outlet`・`symmetry`・`patch` のいずれか(それ以外はエラー。打ち間違いを見つけるため)。ただし `kind` は参考情報で、境界条件の種類は決めない。`count` が 0 のパッチは許す(読み手は警告する)。

## 4. 幾何量の定義

C++ と Python で同じ定義を使う(OpenFOAM の `primitiveMesh` と同じ方法)。

**面**(節点 $\boldsymbol p_0,\dots,\boldsymbol p_{n-1}$、$\boldsymbol p_n=\boldsymbol p_0$)
- 三角形($n=3$)は、面積ベクトル $\tfrac12(\boldsymbol p_1-\boldsymbol p_0)\times(\boldsymbol p_2-\boldsymbol p_0)$ と重心をそのまま使う。
- $n>3$ では、節点の平均 $\bar{\boldsymbol x}=\frac1n\sum_k\boldsymbol p_k$ を仮の中心として扇形に分割する。
  - $\boldsymbol n_k=(\boldsymbol p_k-\bar{\boldsymbol x})\times(\boldsymbol p_{k+1}-\bar{\boldsymbol x})$、$\boldsymbol c_k=(\boldsymbol p_k+\boldsymbol p_{k+1}+\bar{\boldsymbol x})/3$
  - 面積ベクトル $\boldsymbol S_f=\tfrac12\sum_k\boldsymbol n_k$、単位法線 $\hat{\boldsymbol n}=\boldsymbol S_f/|\boldsymbol S_f|$
  - 面中心 $\boldsymbol x_f=\sum_k(\boldsymbol n_k\cdot\hat{\boldsymbol n})\,\boldsymbol c_k\big/\sum_k(\boldsymbol n_k\cdot\hat{\boldsymbol n})$

**セル** $i$
- 外向き面積ベクトル: owner 側では $\boldsymbol S_f$、neighbour 側では $-\boldsymbol S_f$
- 仮の中心 $\bar{\boldsymbol c}$: セルの面中心の単純平均
- 各面で角錐に分割: $v_f=\tfrac13\,\boldsymbol S^{out}_f\cdot(\boldsymbol x_f-\bar{\boldsymbol c})$、角錐の重心 $\tfrac34\boldsymbol x_f+\tfrac14\bar{\boldsymbol c}$
- 体積 $V_i=\sum_fv_f$、重心 $\boldsymbol x_i=\sum_fv_f(\tfrac34\boldsymbol x_f+\tfrac14\bar{\boldsymbol c})/V_i$

**検査**(読み手が行う。許容値は相対値)
- 閉じていること: $|\sum_f\boldsymbol S^{out}_f|\le10^{-10}\sum_f|\boldsymbol S_f|$
- 体積が正: $V_i>0$
- 内部面の向き: $\boldsymbol S_f\cdot(\boldsymbol x_{neighbour}-\boldsymbol x_{owner})>0$(違反は件数を警告として報告し、エラーにはしない)
- 番号の範囲、配列の長さ、§3 の 1・2・4・7

**注意:** 非平面の面では、この定義だと 1 次モーメント(重心の総和)が厳密には保存されない(強く歪めた試験で相対 1e-5 程度)。平面の面では問題にならない。

## 5. 版の管理

- 互換性のない変更をしたら `version` を上げ、本書に差分を書く。
- 読み手は自分が知っている版だけを受け付ける。
