# GSIS-Sim(GSIS-PBC)

希薄気体の定常流れを速く解く GSIS-PBC(pressure-based coupled general synthetic iterative scheme)を、C++/CUDA で実装したソルバーです。論文 Y. Zhang, Y. Wen, L. Wu, *A pressure-based coupled general synthetic iterative scheme for rarefied gas flow simulation*, arXiv:2610.07658v1 の「円管 → 真空」の 4 条件(δ_rp = 1, 10, 100, 1000)の再現を目的にしています。

- 運動論ソルバー(Shakhov モデルの離散速度法、CIS)は GPU、マクロソルバー(PBC)は CPU で動きます
- 前処理・後処理、GUI、AI エージェント向けのモデリング用 MCP サーバーは Python です

## 結果の要約

| δ_rp | 無次元質量流量 Ṁ | 論文の GSIS-PBC | 反復(論文) |
|---|---|---|---|
| 1 | 0.1982〜0.1995 | 0.198 | 21(17) |
| 10 | 0.3370 | 0.326 | 8(9) |
| 100 | 0.8920〜0.8953 | 0.885 | 10(7) |
| 1000 | 未解決(GSIS が収束しない) | 1.275 | —(6) |

1 条件の計算は RTX 5070 Ti 1 枚と 8 コアの CPU で 4.5〜10 分です。詳しい結果、論文との違い、考察は [docs/reproduction_report.md](docs/reproduction_report.md) にあります。

## 資料

| 文書 | 内容 |
|---|---|
| [docs/spec.md](docs/spec.md) | 仕様(決定事項、数値手法、論文との違い、受け入れ基準) |
| [docs/reproduction_report.md](docs/reproduction_report.md) | 再現レポート |
| [docs/build.md](docs/build.md) | C++/CUDA のビルド・テスト・実行 |
| [docs/python_tools.md](docs/python_tools.md) | メッシュの生成・変換・検査 |
| [docs/gui.md](docs/gui.md) | GUI |
| [docs/mcp.md](docs/mcp.md) | AI モデリング用の MCP サーバー |
| [docs/paper_notes.md](docs/paper_notes.md) | 論文の要点、論文に書かれていない点と仮定 |
| [CLAUDE.md](CLAUDE.md) | 開発の規約(AI エージェント向けの作業指示を含む) |

## 必要なもの

- Windows 11、NVIDIA GPU(既定は計算能力 12.0。`CMAKE_CUDA_ARCHITECTURES` で変更可)、CUDA 13.x、Visual Studio(MSVC)、CMake 3.28 以上
- Python 3.13 と [uv](https://docs.astral.sh/uv/)(Python の依存は `python/pyproject.toml`)

## 使い方の例

```powershell
# ビルドとテスト(docs/build.md)
. .\cmake\vs_dev_env.ps1
cmake --preset release
cmake --build --preset release
ctest --preset release

# 管のメッシュを作る(大きなメッシュはリポジトリに入れていない)
uv run --project python gsis-tools mesh tube --kind hex --target-cells 135000 --radius 1e-3 --out cases/tube_vacuum/mesh/tube_hex.pmesh

# GSIS-PBC で解く
.\build\release\src\gsis-solver.exe run cases\tube_vacuum\phase5c\tube_d10_p5c.toml

# GUI
uv run --project python --extra gui gsis-gui
```

## リポジトリに入れていないもの

- ビルドの生成物(`build/`)、計算結果(`runs/`)、DSMC の計算結果の大きなファイル、仮想環境
- 作り直せる大きなメッシュと可視化用のファイル(`cases/**/*.pmesh`・`*.vtu`。小さな試験用のメッシュは入れている)。作り方は各ケースのフォルダの品質レポート(`*_quality.md`)や生成スクリプト、[docs/python_tools.md](docs/python_tools.md) を参照

## 出典

比較に使う論文の数値と図(`reference/paper_2610.07658/`)は arXiv:2610.07658v1(CC BY 4.0)から転記・デジタイズしたものです。
