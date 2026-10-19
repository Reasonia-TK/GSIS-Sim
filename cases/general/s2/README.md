# S2 の小規模 CLI 例

リポジトリのルートで実行する。

```powershell
build\phase8_s\tests\gsis-write-box-mesh.exe cases\general\s2\mesh\box.pmesh --n 6 4 4 --lo 0 -0.001 -0.001 --hi 0.004 0.001 0.001 --tube
build\phase8_s\src\gsis-solver.exe check-case cases\general\s2\auto_box.toml --json
build\phase8_s\src\gsis-solver.exe run cases\general\s2\auto_box.toml --out runs\phase8\s2\example_nsf --max-iter 5 --threads 1 --cells-csv
```

メッシュ生成前は `check-case ... --json --no-mesh` で条件と自動格子を確認できる。既定はNSF。`--mode cis`・`--mode gsis` で運動論を使用する。この小さな入力は機能の通し検査用であり、解の精度を評価したケースではない。

実行中に出力先の `STOP` を作ると、その反復の終わりにsummary・VTU・CSVを保存して停止する。STOPは残るため、再実行前に削除する。

キーと符号の契約は `docs/build.md` §10、検証結果は `docs/phase8_s2_report.md`。Pythonとの数値照合・実行中停止の再現スクリプトは `runs/phase8/s2/verify_contract.py`。
