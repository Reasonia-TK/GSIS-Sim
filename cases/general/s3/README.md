# S3の境界条件の検証

`generate_cases.py` を既存の `gsis_tools` が読める環境で実行する。依存追加は不要。

```powershell
$env:TEMP = "$PWD\build\tmp_s"
$env:TMP = $env:TEMP
$env:PYTHONPATH = "$PWD\python\src"
$env:PYTHONUTF8 = '1'
python\.venv\Scripts\python.exe cases/general/s3/generate_cases.py
```

- 平板: 64/128セル、両壁α=0.5、δ=0.1/1/10。熱伝導はT/T0=0.95と1.05、クエット流はu/√(RT0)=±0.1。横方向は1セル厚の並進周期。
- 傾斜平板: δ=1、128セル、0度と30度、40³/64³速度点。座標と壁速度を同じ角度だけ回転する。
- `tube_d10_pressure.toml`: 元の管と同じ槽の寸法、3168セル、30×24×24速度点。粗いメッシュと既存の局所凍結のため保存性誤差が残る参考試行用。
- `tube_short_pressure.toml`: 槽半径1.5R、槽長各R、管長2Rの短い管。960セル、16³速度点、δ=10、局所凍結なし。圧力入口と質量流量入口の比較に使う。管半径は1 mm。
- `tube_short_mass_cis.toml`・`tube_short_mass_gsis.toml`: 各モードの圧力入口の収束解の流入量を目標にしたケース。圧力入口を再計算した場合は目標も作り直す。

実行・比較用のスクリプトと全出力は `runs/phase8/s3/` にある。`run_validation.py` の `--group aligned|coarse|rotated`、`run_reference.py`、`compare_slabs.py`、`run_mass_final.py`、`run_regression.ps1`、`verify_regression.py` を使う。固定exeのパスを指定し、上書き中のexeで計算しない。

平板と短い管はそれぞれVRAM 2 GB未満。3168セルのFP64管と既存の大きな回帰計算は `runs/.gpu_lock` を開始前に取得し、終了後に解放する。生成したpmeshは既存のgitignore対象であり、このスクリプトから再生成できる。
