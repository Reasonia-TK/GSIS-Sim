# 四面体の管: フェーズ8 S1

本番四面体133,586セルと診断用四面体25,387セルの比較ケース。気体・寸法・速度格子は `cases/tube_vacuum/phase5c/` と同じ。**S1は未達であり、このフォルダは汎用GSISの推奨設定ではない。** 詳細は `docs/phase8_s1_report.md`。

| ファイル群 | 用途 |
|---|---|
| small_d1.toml / small_d10.toml | 小規模の基準設定。正値性のない既定CISは発散する |
| full_d1.toml / full_d10.toml | 本番の基準設定 |
| full_d1_robust.toml / full_d10_robust.toml | 正値性+CFL5、マクロ境界cell。本番CIS比較に使用。GSISの収束を保証しない |
| *_limited_cfl5.toml | 正値性+CFL5、マクロ境界limited |
| *_boundary8.toml | 上記limitedに固定fの境界更新8回を追加 |
| *_matched_cell.toml | 境界更新8回、圧力境界の状態を運動論/マクロともcellに揃える診断 |
| *_jac*.toml / *_walls*.toml / *_cont*.toml | δ=10のPBC不安定性の切り分け。成功設定とはみなさない |
| *_ncis20.toml / small_*_robust.toml | CFL1+n_cis20の高コスト案。未採用。再開用の推奨ではない |

Venkatakrishnan K=3、最小二乗、RCB/octant、内部反復4回、ブロック間反復2回は変更していない。全体の既定値も変更していない。正値性制限は面中心への再構成に対するものであり、反復全体の非負性を保証しない。

## メッシュ

- small: `mesh/tube_small.pmesh`、25,387セル。品質は `mesh/tube_small_quality.md`。
- full: 既存の `cases/tube_vacuum/mesh/tube_tet.pmesh` を相対パスで参照する。コピー・変更しない。

小規模メッシュの再生成 (依存追加なし、リポジトリのルートから):

```powershell
New-Item -ItemType Directory -Force build\tmp_s | Out-Null
$env:TEMP = "$PWD\build\tmp_s"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
python\.venv\Scripts\python.exe -m gsis_tools mesh tube --kind tet --target-cells 25000 --max-size 0.85 --threads 1 --out cases/general/tet_tube/mesh/tube_small.pmesh --report cases/general/tet_tube/mesh/tube_small_quality.md
```

## 計算の再現

構成済み `build/phase8_s` のビルド後、固定exeを用意する。計算スクリプトはGPUロックを取得し、終了時に解放する。既存の出力を残すには `-Name` を変える。

```powershell
New-Item -ItemType Directory -Force build\tmp_s | Out-Null
New-Item -ItemType Directory -Force runs\phase8\s1 | Out-Null
Copy-Item build\phase8_s\src\gsis-solver.exe runs\phase8\s1\gsis-solver-boundary.exe
& cases\general\tet_tube\run_case.ps1 -Case cases\general\tet_tube\full_d1_robust.toml -Name repeat_d1_gsis -SaveInit runs\phase8\s1\repeat_d1_init.csv -Iterations 1
& cases\general\tet_tube\run_case.ps1 -Case cases\general\tet_tube\full_d1_robust.toml -Name repeat_d1_cis -Mode cis -Iterations 1200 -InitFrom runs\phase8\s1\repeat_d1_init.csv
& cases\general\tet_tube\run_case.ps1 -Case cases\general\tet_tube\full_d10_robust.toml -Name repeat_d10_gsis -SaveInit runs\phase8\s1\repeat_d10_init.csv -Iterations 1
& cases\general\tet_tube\run_case.ps1 -Case cases\general\tet_tube\full_d10_robust.toml -Name repeat_d10_cis -Mode cis -Iterations 1600 -InitFrom runs\phase8\s1\repeat_d10_init.csv
```

上のGSISはNSF初期化50回の場を保存した後にGSISを1反復だけ行う手順であり、GSISが合格する例ではない。CISはGSISの最終場ではなく、NSF初期化50回の場から平衡分布を作って開始する。元の比較では正値性修正のみのcandidate.exeを使用した。現在の実装は境界反復1の既定値で同じ計算順を保つ。

両側cell/境界8回の本番診断。δ=10は41回STOP時点の物理場を反復上限で再現するので停止理由のフラグは元と異なる。δ=1は54回でε達成するが入口流量は不合格:

```powershell
& cases\general\tet_tube\run_case.ps1 -Case cases\general\tet_tube\full_d10_matched_cell.toml -Name repeat_d10_matched -Iterations 41 -InitFrom runs\phase8\s1\repeat_d10_init.csv
& cases\general\tet_tube\run_case.ps1 -Case cases\general\tet_tube\full_d1_matched_cell.toml -Name repeat_d1_matched -Iterations 80 -InitFrom runs\phase8\s1\repeat_d1_init.csv
```

スクリプトの `-Overrides` はCLIの `--set` へ渡す。例えば `-Overrides @('kinetic.positivity_limiter=true')`。`-Tolerance` で収束許容値を指定できる。
起動時に `runs/phase8/s1/CANCEL_<Name>` が存在すれば開始を取り止める (GPU待機に入る前に確認する)。開始した計算は出力先に `STOP` を作ると反復の境目で保存して停止する。

終了コード0は収束を意味しない。`summary.json` の `converged`、`stopped`、`mass_balance` と流量を併せて確認する。GSISの最終流量は **summary.mass_flow (補正後f)**。historyの入口/出口はCIS後である。
各出力には `arguments.json`、`case_file.json`、`executable.json`、stdout/stderr、exit_codeを残す。入力メッシュは `runs/phase8/s1/manifest.json` でも照合する。

中心線は既存 `gsis_tools.gui.centerline.extract_centerline` を使用し、四面体は管内0.25R幅のbinで抽出した。分析JSONとCSVは `runs/phase8/s1/` を参照。
