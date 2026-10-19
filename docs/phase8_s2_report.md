# フェーズ8 S2 実装報告

担当S。S1の正値性修正と `boundary_iterations` は保持し、四面体GSISの未解決問題と既定の収束条件は変更していない。Pythonコード・依存パッケージ・仕様書・フェーズ8設計書は変更せず、commit・pushもしない。

## やったこと

- `check-case --json` を追加。成功・入力エラー・CLIエラーとも標準出力はJSON文書1個。設定段階の `--no-mesh`、照合用の `--free-vram-bytes` を追加し、メッシュ未読でも導出量・格子・境界条件を返す。
- `[velocity_grid] auto=true` を追加。境界の最高/最低温度と速度の大きさから、設計どおりの3軸共通・偶数点・中点則の格子を作る。
- メッシュ・精度・鏡映の組・CUDA配列構成からVRAMを積算。NSFはGPUのf・作業領域・固定費・合計0。
- CIS/GSISの最終fからパッチごとの質量流量・局所熱流量・全エネルギー流量・力をSIで出す。NSFは最終のPBC境界半空間分布を使用する。
- STOPを保持し、停止時はVTU・セルCSVも必ず保存。`stopped_by_user` を追加。収束と停止要求が同時の場合も停止要求を記録する。
- `case.mode="nsf"` と `run --mode nsf` を追加。PBC単独の実装を共有し、fは確保しない。共通のfields.vtuにはNSFのσ・qも出す。CIS/GSISのCSVからも列名で再開できる。
- GSISの補正後fの保存性と最後のPBC補正量を `conservation_diagnostics` として明示。既存の停止条件は維持する。

## 変えたファイル

| ファイル | 内容 |
|---|---|
| `src/config/case_config.hpp`・`.cpp` | NSFモード、自動速度格子の読み込み・決定・入力検証 |
| `src/kinetic/memory_estimate.hpp`・`.cpp`、`src/kinetic/CMakeLists.txt` | CUDAの保持配列を積算する見積もり |
| `src/kinetic/kinetic_output.cpp` | SIパッチ出力、中心モーメントによる熱流量 |
| `src/app/cmd_check_case.cpp` | JSON契約、警告・エラーの配列、メッシュ未読の経路 |
| `src/app/cmd_run.cpp`・`cmd_cis_run.cpp`・`cmd_pbc_run.cpp` | NSFの接続、停止時の保存、SI出力、保存性診断 |
| `src/app/cli.hpp`・`main.cpp` | NSF共通経路の宣言、ヘルプ、STOPの契約 |
| `src/macro/pbc_solver.hpp`・`.cpp` | 停止と収束の同時記録、NSF構成則の出力専用モーメント |
| `tests/test_config.cpp` | 自動格子、併用エラー、速度過大、NSFの解析 |
| `tests/kinetic/test_kinetic_phase5c.cpp` | SI熱・力・エネルギーの解析的検証、メモリ予算・チャンク |
| `tests/kinetic/test_kinetic_gpu.cpp` | FP32/FP64で見積もりと実GPU配列量の一致 |
| `tests/s2_cli.cmake`、`tests/CMakeLists.txt` | JSON全体の解析、3モードの通常/STOP出力、NSFと旧PBCのCSV一致 |
| `cases/general/s2/` | 自動格子・NSFの小さな箱と再生成手順 |
| `runs/phase8/s2/` | 通し試験・数値照合・六面体回帰の入力、スクリプト、出力、固定exe |
| `docs/build.md` §10、本報告 | G3b向け契約と試験結果 |

## G3bが読む出力契約

完全な表・式・nullの条件は `docs/build.md` §10に記載。JSONの `schema_version=1`。以下のサブキーは、それぞれ左端のオブジェクト内のキー。

| check-caseのキー | 単位・内容 |
|---|---|
| `ok`, `errors`, `warnings`, `mesh_loaded` | 成否、誤り配列、警告配列、メッシュを読んだか |
| `input` | name、mode(gsis/cis/nsf)、case_file、mesh、gas、conditions |
| `input.gas` | molecular_mass_kg[kg]、mu_ref_Pa_s[Pa s]、T_ref_K[K]、omega、Pr、gamma |
| `input.conditions` | L_ref_m[m]、T0_K[K]、delta_rp、Kn、p_ref_Pa[Pa]。不使用の圧力指定はnull |
| `derived` | R_J_kg_K[J/(kg K)]、mu_Pa_s[Pa s]、mean_free_path_m[m]、Kn、delta_rp |
| `reference` | length_m[m]、temperature_K[K]、pressure_Pa[Pa]、density_kg_m3[kg/m³]、velocity_m_s[m/s]、time_s[s]、distribution_kg_s3_m6[kg s³/m⁶]、viscosity_Pa_s[Pa s]、heat_flux_W_m2[W/m²]、mass_flux_kg_m2_s[kg/(m² s)]、mass_flow_scale_kg_s[kg/s] |
| `velocity_grid` | auto、n[3]、n_velocity、range[3][2]・step[3](√(R T0)単位)、range_m_s[3][2][m/s]、自動時T_min_K・T_max_K[K]・u_max_m_s[m/s] |
| `patches[]` | name、type、faces、area_m2[m²]、temperature_K[K]、pressure_Pa[Pa]、velocity_m_s[3][m/s]、圧力入口のtangential_velocity(zero/extrapolate) |
| `mesh` | n_cells、n_faces、volume_m3[m³]。未読なら省略 |
| `memory` | distribution・workspace・overhead・total・free・reserve・budget[bytes]、chunk[速度点]、fits、model、precision、free_source。未読ならnull |

`--no-mesh` では `faces`・`area_m2` はnull。通常の `--json` は従来どおりメッシュを必須とするが、読めなくても既に計算した導出量・格子などは残して `ok=false` を返す。メモリ超過だけなら `ok=true`、`memory.fits=false` と警告。JSONの終了コードは成功0、入力/メッシュ誤り1、CLI引数誤り2。

| summaryのキー | 単位・符号 |
|---|---|
| `patches[].mass_flow_kg_s` | kg/s、流体領域への流入が正 |
| `patches[].heat_flow_W` | W、面の局所静止系の熱流量、流入が正 |
| `patches[].energy_flow_W` | W、移流・仕事込みの全エネルギー輸送、流入が正 |
| `patches[].force_N` | [Fx,Fy,Fz]、N。気体の外向き運動量輸送。固体面では気体がパッチへ及ぼす力 |
| `patches[].area_m2`, `faces` | m²、面数 |
| `stopped_by_user` | STOPによる停止。旧stoppedも維持 |
| `conservation_diagnostics.state` | corrected_f(GSISのみ) |
| `conservation_diagnostics.inlet_kg_s`, `outlet_kg_s` | 圧力入口の流入・圧力出口/真空の流出、kg/s |
| `conservation_diagnostics.relative_imbalance`, `valid`, `warning` | (出口−入口)/max(abs(入口),abs(出口))、有効か、許容差超過か。流入/流出がなければnull・valid=false |
| `conservation_diagnostics.last_pbc_correction.epsilon`, `rho`, `u`, `T` | 最後のW_new−W_halfの相対ノルム。epsilonは3成分の最大。外側反復0回ならnull |

旧CIS/GSISの `mdot_kg_s` と `energy_flux_nd` は外向きの意味を保つ。新mass_flowは旧mdotの符号反転。energy_flowは旧energy_fluxを `−p_ref sqrt(R T0)L_ref²` 倍した値。境界VTUの `energy_flux` は外向きの全エネルギー流束[W/m²]で、`energy_flow_W=−∫energy_flux dA`。heat_flowは面ごとに `E−u·P+|u|²M/2−1.5ρT(u·n)A` を集計し、移流・仕事と区別する。動く壁でも、壁の静止系と局所気体の静止系は異なり得る。

静止固体壁との正味のエネルギー授受にはenergy_flowを使う。すべりがある場合、局所気体系のheat_flowと静止固体との熱授受は一致しないことがある。動く固体との熱授受には壁速度の仕事も考慮する。

旧 `pbc-run` は既存の `mass_flow_kg_s`・`energy_flow_W` の外向き符号を保持する。G3bは統一された `run` を使用すること。NSFのepsilonはPBCのp/u/T更新ノルムの最大、residualは最終の質量残差。式(59)のGSIS epsilonとは別定義。NSFのCSVも列名で読むこと(旧PBCのp先行、運動論はrho先行)。

## 決めたことと理由

1. `auto=true` と手動n/rangeの併用はエラー。GUIや手編集で意図しない優先順位が生まれないため。auto=falseなら従来の手動値を使う。
2. 自動格子は設計・GUIと同じ `limit=umax+5√(R Tmax)`、`n=max(2,2ceil(limit/(0.7√(R Tmin))))`。T0による無次元化、中点の幅/nを維持。境界温度がなければT0、速度がなければ0。
3. VRAMはGUIの暫定固定費より実バックエンドの配列構成を優先。残差の部分和配列、精度、鏡映と256点の丸め、1 GiBの予約分を反映する。GUIはG3bで合わせる。自動幅は補正用固定配列も先に引いた安全側の値。既存CUDAの確保順とチャンク選択は変更しないため、実行時の自動幅と少し異なることがある。
4. STOPは開始時・終了時とも削除しない。要求の取りこぼしを防ぎ、停止の痕跡を残す。GUIは新しい出力先を作るか、再実行前に自分のSTOPを消す。停止時はno-vtu指定より結果の保全を優先する。
5. NSFはPBC単独を共有し、runのmax-iter/tol/threads/init-fromとmacro/gsis設定を使う。未対応のmin-iter/vtu-every/dump-every/save-init/sectionsはNSF経路では利用しない。運動論のbackend/precisionもNSFには適用しない。新しい数値解法・既定設定にはしない。
6. 補正ノルムは既存 `last.pbc_change` を成分ごとに明示し、保存性と並べてGUIが評価できるようにした。フィルタ・減衰後のWのノルムであり、凍結・負値処理を含めたΔfのノルムを新規に定義しない。収束条件への組み込みは本依頼の範囲外とした。

## 試験

作業フォルダの一時領域 `build/tmp_s`、構成済み `build/phase8_s` を使用。依存追加なし。

```powershell
$env:TEMP = "$PWD\build\tmp_s"
$env:TMP = $env:TEMP
$env:OMP_NUM_THREADS = '8'
cmd /c 'call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul && cmake --build build\phase8_s'
cmd /c 'call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul && ctest --test-dir build\phase8_s --output-on-failure'
python\.venv\Scripts\python.exe runs\phase8\s2\verify_contract.py
& runs\phase8\s2\run_regression.ps1
python\.venv\Scripts\python.exe runs\phase8\s2\verify_regression.py
```

- C++: 既存155件に11件追加、最終166/166件通過、77.58秒 (`ctest_final.log`)。追加は自動格子1、SIパッチ1、メモリCPU1、GPU1、CLI7。初回は165/166で、CMakeのWindows標準出力取得が日本語を既定コードページで変換してJSONを壊した。`execute_process(ENCODING UTF-8)` に修正して解消。
- GPU: FP32/FP64・対称面・手動チャンク37→鏡映単位38で、見積もりの保持配列合計と実際のbackend.memory_bytesが完全一致。
- `verify_contract.py`: 5気体の数式照合、3モードの実行開始後STOP、5種のJSONエラー(TOML・auto併用・パッチ・kineticキー・導出量オーバーフロー)、入力/引数エラー、メッシュ未読/欠損、警告、VRAM予算不足、NSFのCIS CSVからの再開を通過。STOP時はno-vtu・CSV指定なしで3成果物を確認 (`contract_verification.json`)。停止反復数は検知タイミングで変わる。全3モード・各96セルのCSVが全列有限、ρ・Tが正であることも確認 (`stopped_field_checks.json`)。
- NSFと旧pbc-run: CLI試験で同じ2反復・許容値のセルCSV SHA256が一致。NSFの見積もりf=0と共通fields.vtuも確認。
- `cases/general/s2/auto_box.toml`: 96セルの箱を再生成し、check-case --jsonとNSF 5反復、summary・VTU・CSVの出力を確認。収束・精度の検証用ではない。

### Pythonとの数値照合

`python/src/gsis_tools/gui/general_settings.py` の `GeneralSettings/derived/velocity_grid/estimate_vram` を直接読み込んで照合した。L_ref=0.25 m、p_ref=100 Pa、T0=Tmax、96セル、手動チャンク37、空き15,784,004,812 bytes。気体の値はGUIプリセットの入力値をそのまま使用した。

| 気体 | Tmin/Tmax[K] | umax[m/s] | 3軸共通n | 無次元の上限 | R[J/(kg K)] | μ(T0)[Pa s] | Kn |
|---|---|---:|---:|---:|---:|---:|---:|
| He | 273.15/273.15 | 0 | 16 | 5 | 2076.16390977 | 1.916e-5 | 0.000723347284951 |
| Ne | 100/600 | 300 | 40 | 5.60329036826 | 412.134029851 | 5.00264226883e-5 | 0.00124713722135 |
| Ar | 200/400 | 1200 | 38 | 9.15782659904 | 208.242684766 | 2.88468042101e-5 | 0.000417380840745 |
| Kr | 80/900 | 50 | 50 | 5.16729026660 | 99.2558590942 | 6.04570380177e-5 | 0.000905870520589 |
| Xe | 300/1200 | 700 | 44 | 7.53918312081 | 63.3325229358 | 7.41703527956e-5 | 0.00102507181426 |

nは完全一致、範囲・R・μ・平均自由行程・Knは相対2e-13以内で一致。入力値と全数値は `auto_*.toml`・`check_*.json`・`contract_verification.json` に保存。

| 気体 | f[bytes](C++/Python一致) | 作業領域 C++ / Python[bytes] | C++固定費[bytes] | 合計 C++ / Python[bytes] |
|---|---:|---|---:|---|
| He | 1,572,864 | 57,600 / 56,832 | 150,412 | 1,780,876 / 101,629,696 |
| Ne | 24,576,000 | 57,600 / 56,832 | 929,164 | 25,562,764 / 124,632,832 |
| Ar | 21,070,848 | 57,600 / 56,832 | 810,500 | 21,938,948 / 121,127,680 |
| Kr | 48,000,000 | 57,600 / 56,832 | 1,722,164 | 49,779,764 / 148,056,832 |
| Xe | 32,710,656 | 57,600 / 56,832 | 1,204,556 | 33,972,812 / 132,767,488 |

作業領域の差768 bytesは `96×ceil(37/256)×8` の残差部分和配列。Pythonの固定費は一律100,000,000 bytesであり、C++は実配列積算。予約はC++1,073,741,824対Python1,000,000,000 bytes、予算は14,710,262,988対14,784,004,812 bytes。これらは意図した差であり、見積もり式がすべて一致したとは主張しない。NSFはC++のf・作業・固定費すべて0、Pythonは現状固定費0.1 GBを残す。

### 六面体 δ=1・10 の回帰

GPUロックを取って `runs/phase8/s2/` に固定したexeで実行。比較基準は `runs/phase5c/gsis/d1`・`d10`。`verify_regression.py` による2条件とも通過 (`regression_verification.json`)。

| δ | 反復 | ε | Ṁ入口 | Ṁ出口 | 入口の流入[kg/s] | 出口の流出[kg/s] |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 21 | 9.857896972863473e-6 | 0.19820365370244056 | 0.19952653540387963 | 6.7310388487831975e-9 | 6.775964196819913e-9 |
| 10 | 8 | 7.982434646019091e-6 | 0.3369777550074916 | 0.3369275904952138 | 1.144383727424316e-7 | 1.1442133676581541e-7 |

全反復の物理量61列、反復数、ε、入口/出口流量、既存のパッチ項目、mass_balance・mass_flow_sectionsが完全一致。実行環境に依存する時間・メモリ量は比較から除外した。セルCSVは全135,104セルを含めてSHA256が一致:

- δ=1: `75dc8fb280697a575c1435e52e74c29edc808714ed32fb13abbbeea94805aced`
- δ=10: `3451d31ab1e460ce8ce533dcbf9640ce4a5877e011ff5cad71b3c6327ad04295`

δ=10の質量流量の基準は `3.3960215783349687e-7 kg/s`。上表の入口/出口のSI流量をこれで割ると既存のṀと相対1e-14以内で一致。共通キーの出口 `mass_flow_kg_s` は流入正なので `−1.1442133676581541e-7`。両条件・全パッチで、energy_flowが旧energy_flux_ndのSI換算の符号反転、forceが旧momentum_flux_ndのSI換算と一致したことも確認。

保存性診断は、δ=1で相対不釣り合い `+0.006630104105006841`・warning=true、最後のPBC補正epsilon `9.169571197267523e-5`。δ=10は `−0.00014886594599293671`・warning=false、PBC補正 `2.1476505292035468e-4`。いずれも既存の値から換算・再計算した結果と一致。δ=10でも断面の流量ばらつきは0.453%あり、既存mass_balanceには警告が残る。新warningは入口/出口だけの診断なので、G3bでは既存mass_balanceの警告も引き続き使うこと。

δ=1は `gsis-solver-s2.exe` (SHA256 `3c177753e913819bc960345f192e4333d7c3a28c0ce1ecd017c9b4345ddc8987`)、δ=10は最終 `gsis-solver-s2-final.exe` (`e5cac5ae6a7bfa040c453f54008f8b029a9a7e6349c15de179865cbbf6698f50`)。δ=1の固定後に加えたソルバー差分はNSFの熱流量の評価とcheck-caseのJSON失敗時の処理で、GSIS/CISの計算・出力経路には変更なし。小規模CIS/GSIS各3反復でも、固定版と最終版のセルCSV・epsilon・全パッチ出力が完全一致した (`binary_consistency.json`)。

実行スクリプトは初回δ=1の最終の保存性警告(stderr)をWindows PowerShellがNativeCommandErrorとして扱い、wrapperが終了コード1になった。summaryとセルCSVの保存は既に完了しており、上記の全数値照合に通過した。solver自体の終了コードはその初回では未記録で、推測の0は書かず `wrapper_status.json` にnullとして記録した。stderrを数値計算の失敗と誤認しないよう呼び出し部分を修正し、δ=10は終了コード0を記録。再現用には修正済みrun_regression.ps1を使うこと。ケース・exeのSHA256とCLI引数は各出力先に保存した。

## 確かめていないこと・既知の問題

- 四面体GSISはS1の未解決のまま。S2の診断・GUI接続機能が通ったことを、四面体GSISの精度保証と解釈しない。
- 自動速度格子の精度や全流速の包含は任意流れで未保証。境界入力からの見積もりであり、内部で加速/冷却する流れには手動確認が必要。
- メモリは保持するCUDA配列を見積もる。実行時の空きの変化、WDDM、CUDAコンテキスト、アロケータ、一時アップロードは予約分で余裕を持つ。実際の確保成功を保証するものではない。
- G3bへのPython接続、NSFの高度な出力オプション、Maxwell壁・質量流量入口(S3)、大規模NSFの精度、Linuxは未確認/対象外。
- NSFの熱流量は最終PBC境界分布の中心モーメント。セルのFourier則を境界まで外挿した値と同一とは限らない。力は開いたパッチでは移流を含む運動量輸送。
- NSFの追加オプションは基本のrun経路に限定。min-iter/vtu-every/dump-every/save-init/sectionsは現時点でNSFでは処理しないので、G3bから送らないこと。
- 気体プリセット(Ne/Ar/Kr/Xe)の一次資料照合は統括役の担当。ここでは入力数値と両言語の式だけを確認し、物性値の出典の妥当性は判断していない。
- `docs/spec.md`・`docs/design_general_app.md` など担当外文書は変更していない。specのmodeやSTOPの旧説明がある場合、統括役が更新すること。

## 統括役への質問

今回の実装を止める質問はない。G3bはメモリの暫定式から実配列見積もりへの差、STOPの保持、熱と全エネルギーの区別、NSF epsilonの別定義を反映すること。四面体GSISの保存性診断を収束必須条件にするかは、別の設計判断として残す。

## 次の担当への引き継ぎ

1. G3bは `docs/build.md` §10のキーを接続する。メッシュ生成前は `--json --no-mesh`、生成後は通常 `--json`。VRAMの確定表示はC++のmemoryを使用し、入力の暫定見積もりとは区別する。
2. `run` の `patches[]` の共通SIキーを表・CSVに使う。旧mdotや旧pbc-runの符号と混在させない。STOPはGUIが要求時に作り、再実行前に自分の要求を消す。
3. GSISはconvergedだけでなく、conservation_diagnostics.warningとlast_pbc_correctionを表示できる。既定のconvergedの意味は変えていない。
4. S3のMaxwell壁・質量流量入口を追加したら、自動格子の境界情報・JSONの主な値・パッチ診断の入口/出口分類も更新する。S1の未解決事項は引き続き別途調査する。
5. 小さなケースはREADMEからメッシュを再生成できる。大きな計算結果・固定exe・runs内スクリプトは既存ignore対象なので、統括役が共有方法を判断すること。

終了時点でSの計算・待機プロセスは完了し、`runs/.gpu_lock` は解放済み。ソースと最終exeのSHA256は `runs/phase8/s2/source_manifest.json`。commit・pushはしていない。
