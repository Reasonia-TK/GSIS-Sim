# フェーズ8 S4: 分布関数をホスト RAM に置く方式の検討

2026-10-20、担当 S。根拠は `docs/design_general_app.md` §3.4、現行 CUDA 実装、S2 のメモリ見積もりと既存回帰結果。本書は検討と独立計測の報告であり、ソルバー・GUI の実装変更はしていない。GB は 10⁹ bytes、GiB は 2³⁰ bytes とする。

## 1. 提案と判断の要点

**速度チャンクを単位に、VRAM に置けるチャンクは残し、残りだけを pinned RAM に置く方式 B を推す。** 全量をホストに置く方式 A を同じ機構の `resident_bytes=0` として検証し、その後に常駐分を増やす。通常は現在の全量 VRAM 方式を維持し、明示的に退避を選んだ場合だけ使用する。

- 再開後の実測はH2D 35.37 GB/s、D2H 33.69 GB/s、双方向合計33.88 GB/s。双方向で約2倍になる計算は使えない。計算との重なりは独立した小さな演算で確認した。時間モデルには33 GB/sを使う。
- 再開後の pinned 1 GB確保は成功。空きRAMが約5.1 GBのため4/8 GBは確保前に見送った。20 GBは新たに試さず、実用可否も保証しない。旧計測の8 GB超の記録は保管するが、容量判断の根拠から除外する。
- 現在の既定値は `inner_sweeps=4`、`inner_wall_update=true`。全速度を1往復するだけの `2F/B` では足りない。開いた管の既定設定で評価順を保つ段階別実装では、CIS 1反復に `13H`、GSIS 1反復に `70H` の転送を見込む。`H` は RAM にある分布のバイト数。追加診断などは別途加算する。
- `F=9.338 GB` の管は現在の VRAM に収まるので退避不要。比較用に全量退避させると、GSIS 1反復は転送のモデルで約1.27～1.72倍。`F=25 GB` で約12 GBを常駐・13 GBを退避できれば、同じ大きさを全量 VRAM に置ける仮想環境に比べ約1.14～1.37倍。未計測の実装費用を見込む暫定計画値は後者を**約1.2～1.8倍**と置き、実装後に更新する。現在の空きRAMでは13 GB退避は実行不可。
- `inner_wall_update` を遅らせたり、FP16/BF16に変更したりしてこの数字を小さくしない。既存の数値解法を先に維持する。

上記の時間はソルバーの退避実装を測った結果ではない。実測した転送帯域と既存の計時からのモデルで、前提と限界は§6に示す。

## 2. 読んだ資料と現在の状態

`CLAUDE.md`、`docs/design_general_app.md` 全文、`docs/progress.md`、`.handover/2026-10-18_2225.md` 全文、`docs/phase8_s2_report.md`、以下のコードを確認した。再開時は HEAD `ceb9de9`（S2はコミット済み）。引継ぎと `docs/progress.md` のS1実行中という記載は現状より古い。GUI側には別担当の未コミット変更がある。書きかけの本書・計測結果・`runs/phase8/prompts/s4_log_try1.txt` の停止前後も照合し、現在の依頼とファイルを優先した。担当外文書は直していない。

現行 `src/kinetic/kinetic_backend_cuda.cpp` の `CudaBackend<F>` は `f_[n_cells][nv]` を全量確保する。チャンク化しているのは `gx_ / gy_ / gz_ / r_` と残差部分和だけであり、単純な確保先の差し替えでは退避できない。配列構成は `memory_estimate.cpp::estimate<F>` と照合した。

S2 の管の既存結果は、135,104セル、17,280速度点、FP32で `F=9,338,388,480 bytes`。チャンク2,304点、バックエンド全体14,427,211,656 bytes、うち既存固定配列98,621,832 bytes。S4ではこのソルバーを再実行・再ビルドしていない。

## 3. 全速度点を必要とする処理と分割方法

表中のCUDAカーネルは `src/kinetic/kinetic_cuda_kernels.cu`、共通の演算は `src/kinetic/kinetic_kernels.hpp` にある。「追加読込」は、呼び出し順をそのまま保つ最初の実装で、RAM上の部分を走査する回数。境界だけの演算も、まず全セルのチャンクを渡す保守的な数え方にした。

| ファイル・関数 | 全速度が必要な理由 | 分割と追加の出し入れ |
|---|---|---|
| `kinetic_backend_cuda.cpp::CudaBackend::compute_moments` → `k_moments` | セルごとの13種類の和から15個のマクロ量、応力、熱流束を作り、`shift_`を更新する | チャンクごとの未規格化の和を順に集約し、全速度が終わってから `finalize_moments` とshift更新。読込 `H`、書戻し不要。全マクロ量が揃うまで次の段を開始しない |
| 同 `boundary_sums` → `k_boundary_sums` | 全速度の流出・流入半空間の流束と分布のモーメント、20値/面 | 分割可。全速度の和が揃った後に壁密度と面状態を決める。独立呼出しは読込 `H`。実際に必要なセルは境界ownerとその1近傍なので、将来はこの集合だけに縮められる |
| `kinetic_solver.cpp::Impl::prepare`、`process_boundary_sums`、`update_pressure_inflow` | まずセルのモーメントを作り、新しい圧力流入を設定してから境界の和を評価する。`boundary_iterations`回の整合更新がある | モーメントと最初の境界評価は単純には同じ読込に融合できない。境界評価1回につき上記読込。`prepared_version/version` のキャッシュと無効化を維持 |
| `kinetic_solver.cpp::Impl::refresh_wall_inflow`、`sweep` | 内部反復の間で、更新済みの全速度の `j⁺` から新しい壁密度を決める | 全チャンク1スイープ → 境界の全速度集計 → CPUで壁更新 → 次の全チャンク1スイープ、を保つ。4内部反復なら間に3回の境界読込。チャンク内で4回解いてから戻す方式は同じアルゴリズムにならない |
| `kinetic_backend_cuda.cpp::update_cell_params` → `k_cell_params_deq` → `block_discrete_maxwell`、`discrete_equilibrium.hpp` のNewton補助 | Maxwell分布の5係数とShakhovの保存補正を求めるため、各Newton反復で全速度格子を積分する | **既存の `f` は読まない。** 入力はマクロ量、格子 `xi_*`、重み、多項式係数。格子はVRAMに残し、現行カーネルを利用すれば追加の `f` 転送は0。Newton反復回数倍の `f` 読込は不要 |
| `kinetic_solver.cpp::Impl::correct_inflow` → `discrete_equilibrium.hpp` / `kinetic_kernels.hpp::discrete_maxwell_seq` | CPUで境界の離散Maxwell係数を作る | 上と同じく格子・境界マクロ量だけ。`f` 転送は不要 |
| `kinetic_backend_cuda.cpp::gsis_correction` → `k_gsis_correction`、呼出し元 `src/gsis/gsis_solver.cpp::GsisSolver::iterate` | 式(14)の `f + M(W_new) − M(W_half)` に、セル全速度の負値判定・最小α・最大θを適用する | Maxwell係数2組は先に計算・保持。第1走査で全速度の判定を完了、第2走査で同じセル係数を全速度に適用する。保守的には読込 `2H`＋書戻し `H`。`all/created` の即時書込みをそのまま分割すると、後で見つかった負値により前のチャンクも置換する必要がある |
| `kinetic_backend_cuda.cpp::internal_face_mass_flux` → `k_internal_face_mass_flux`、`kinetic_solver.cpp::Impl::build_sections` | 診断断面の各面について全速度の流束を積分する | 分割可。owner/neighborとそれぞれの勾配用近傍が必要。毎回の全量読込なら `H`、出力のみ。診断頻度により追加。半段と補正後のfを混ぜない |
| `kinetic_backend_cuda.cpp::scale_distribution` → `k_scale`、`kinetic_solver.cpp::Impl::prepare` | 閉じた領域の全質量をモーメントから求めた後で、全fを同じ倍率にする | 分割可。追加 `H`読込＋`H`書戻し。前後の浮動小数点の丸めを変える遅延倍率の持ち方は最初は使わない |
| `kinetic_backend_cuda.cpp::set_equilibrium` → `k_set_equilibrium` | 初期化で全速度へMaxwell分布を書き、shiftを設定する | 離散平衡係数を一度決め、チャンク別に生成して保存。更新後のfの書戻し `H`、既存fの読込不要。初期化1回の費用 |
| 同 `set_distribution/get_distribution`、`kinetic_solver.cpp::KineticSolver::set_distribution/distribution` | 全fを公開APIのFP64・元番号の配列に変換する | 数値演算は分割可。ただし現在のAPIは全量vectorを要求する。FP32の `F` に対しFP64は `2F`、内部/外部の並べ替え用vectorもある。大規模時に従来の全量経路を通すとRAMを使い切るため、チャンクI/Oを追加するかこのAPIを明確に制限する。通常のVTU・マクロCSV出力は全fの出力ではない |

### 3.1 スイープとメモリ配置

`CudaBackend::sweep` の速度チャンク内は、勾配 → 残差 → 残差集約 → `block_passes` 回の前進・後退 → 最後のpassだけf更新という順。`gx/gy`は途中で元残差・前回Δfの置き場に使い回す。これらの4配列をさらに増やす必要はない。セルの空間ブロックだけを退避単位にすると、ブロック外セルのf・勾配との結合が増えるため今回は選ばない。

既存のfはセル優先、作業領域は `[cell][chunk_stride]`。全fの中央の速度区間は連続したDMAにならない。ホスト側を **`[chunk][cell][local_velocity]`** の連続ブロックとして保持し、GPU上は従来どおり `[cell][chunk_stride]` にする案がよい。既存fから毎反復CPUでpack/unpackする案や、幅の狭い行を大量に転送する `cudaMemcpy2DAsync` に、今回の連続転送の帯域を適用しない。初期化・読み込み・外部出力の時に並べ替える。

`KineticView::nv` は全速度数、格子の添字は全体の `p` のまま残し、f参照用の `f_pitch`・`f_k0` を別に持つ。`f_at` 以外にも `k_moments`、`k_set_equilibrium`、`k_gsis_correction`、`sweep_backward_cell` が直接fを添字付けするので、そちらも変更対象。`ChunkView` の全体のk0と物理配置の原点を混同しない。

対称面は `p ^ mirror_xor` と、残差側の `t ^ mirror_xor` の両方を使う。鏡映軌道を分断しないことに加え、現行128スレッドの境界処理・256点の残差タイルと整合させるため、通常のチャンク幅・原点は256点単位、端数は全体の最後だけとする。S3の一般法線Maxwell壁で速度空間補間が入ると、参照速度が別チャンクになる可能性がある。現行HEADにはS3のMaxwell壁・質量流量入口が未実装なので、その完成形の境界用ステンシル・キャッシュを読んでから対応を確定し、未対応の壁を黙って実行しない。

### 3.2 評価順を保った最初のスケジュール

1. 全チャンクからモーメントを作り、全体の和を確定する。閉領域なら質量倍率を適用する。
2. 目標分布の係数・圧力流入を更新し、全チャンクから境界和を作り、壁密度を設定する。`boundary_iterations` の回数を守る。
3. 全チャンクのLU-SGSを1回行って保存する。`inner_wall_update` があれば全チャンクの境界和を作り、壁を更新する。これを必要回数繰り返す。
4. GSISなら必要なCIS反復の後で半段のモーメントと境界量を確定し、PBCへ渡す。PBCの完了後、負値判定と補正適用を別の全速度走査で行う。

二つのf用スロットと1組の4作業配列を使い、H2D・計算・D2Hを非既定ストリームとイベントで接続する。前のスロットのD2H終了前に上書きしない。共有する `r/gx/gy/gz` を同時に二つの計算ストリームで使わない。今のlaunchラッパーは既定ストリーム固定なので、ストリーム引数とPhaseTimerの記録先も変更が必要。

壁更新は全チャンクの完了後に行う。途中のチャンクの結果で壁を更新する・常駐分だけ新しい壁を使う、といった順序依存は作らない。次の段階で、更新後の境界和をスイープの直後に同じスロットから作って保持すれば、間の境界用再読込を省ける。ただし最新の全近傍fと必要な流入状態が揃った場合だけであり、最初の見積もりには織り込まない。

## 4. 方式の比較と必要メモリ

`F = b Ncell Nv`、`S = b Ncell K`、`b=4/8`、`K`は物理チャンク幅。既存固定配列を`O`、新しい集約・管理用領域を`P`、常駐するfを`R`、退避分を`H=F−R`とする。表は二つのfスロット・一組の4作業配列なのでおおむね `6S`。残差部分和の `8 Ncell ceil(K/256)`、端数パディング、CUDA予約1 GiBは別途必ず足す。全量VRAM方式は従来どおり `F+4S+O+partial`。

| 案 | VRAM / RAM | 1反復の転送時間モデル | 変える範囲・収束と結果 | 決まった順序の総和 |
|---|---|---|---|---|
| A: 全fをpinned RAM、速度チャンク転送 | VRAM `O+P+6S`、RAM `F`＋CPU側の既存データ。全fのGPUコピーは持たない | 既定CIS `13F/B`、GSIS `70F/B`を追加。スイープ分は一部隠せる | 新backend、view/launch、全速度集約、補正2段化、見積もりと設定。既存の内部壁更新を守れば手法は同じ。25 GB全量は現在の空きRAMを超え、pin総量も未検証 | 保てる。ただしチャンク部分和の結合は現在の全速度リダクションと丸めが変わる |
| **B: 置けるチャンクをVRAM常駐、残りだけpinned RAM** | VRAM `R+O+P+6S`、RAM `H`＋既存CPUデータ。fを二重に保持しない | 同じ式の `F` を `H` に置換。25 GBでR≈12 GBなら転送分はAの約52% | Aの共通処理に、常駐場所・スロット・イベントの管理を追加。全チャンクの論理的な処理順は同じ。結果への影響はAと同じ。25 GB級への推奨 | 保てる。常駐の有無・イベント完了順によって集計順を変えない |
| C: FP16/BF16でfを保持し、FP32で演算 | fは約`F/2`、変換用チャンクと従来の作業領域が必要。RAM退避を必須としない | f転送は不要になり得るが、変換・再読込の時間は未測定。現行比の数値は出せない | 全f読み書き、共有演算、補正と保存性の再検証。FP16の指数範囲・BF16の粗い仮数、δが大きい場合の `f^S−f` と裾の丸めが懸念。収束・解が変わるため今回は推さない | 演算順は固定できるが、現在の精度の結果との一致は保てない |

最初の試作はAでデータ移動を検証し、最終的にはBを使う。Aだけを製品化して「25 GBにも対応」とはしない。速度格子の自動選択・物理的に妥当な小さな速度範囲は先に確認するが、解像度を落として必ず収まると約束しない。

ページング可能な通常RAM＋少量のpinned stagingも代替になる。pin上限に対しては有効だが、CPUでの追加メモリコピーと同期が増える。CPUメモリ帯域・packを今回は測っておらず、33 GB/sをそのまま当てはめない。全量25 GBを通常RAMに置く場合も、設計時の空き20～24 GBでも足りず、再開時は空き約5.1 GBしかない。

### 4.1 25 GBの配置例

計測時のGPU空きは約15.77 GB、S2と同じ1 GiBを引くと予算は約14.69 GB。管と同じNvでセル数だけ25/9.338倍になる仮定では、`K=256` により `S≈0.370 GB`、6S≈2.22 GB、O≈0.264 GB。集約・管理用Pを仮に0.15 GBとすれば、R=12 GBとの合計は約14.63 GBで、予算に近い。実行時はチャンク単位でRを切り下げ、必要なら11～12 GBにする。

この例ならRAMのfは約13～14 GB。**このpin総量は今回の上限内の試験からは保証できず、現在の空きRAMでは不可。** 運用時の空きが少なくともH＋3 GiB＋追加CPU領域を満たすことが条件で、既存CPUメッシュ・PBC・GUI・結果書込みも計上する。割当は約0.37 GBずつのチャンクに分けるが、分割してもpin総量の制限はなくならない。25 GBが速度点増加で生じた場合はOやSの増え方が違う。セル数とNvを分けて見積もり、Fだけから実行可能とは判定しない。VRAM常駐部を全量RAMにも複製するとこの利点が失われる。

## 5. この機械での小さな計測

ソース・スクリプト・生データは `runs/phase8/s4/`、独立ビルドは `build/phase8_s4/`、一時領域は `build/tmp_s4/`。依存追加なし。前回の `probe_results.json` と `managed_observations.json` は上書きせず、旧集約は `summary_try1.json` に保存した。再開後の結果は `resume_results.json`、両者を出所別に集約した最終値は `summary.json` にある。

### 5.1 環境と方法

- NVIDIA GeForce RTX 5070 Ti、ドライバ610.88、WDDM。前回のPCIe属性照会はGen5 x16。再開時のアイドル状態はGen1 x16と表示されたため、転送中もGen5で動いていたとは断定しない。
- `asyncEngineCount=1`、`concurrentManagedAccess=0`、`pageableMemoryAccess=0`、`managedMemory=1`、`canMapHostMemory=1`。再開後も同じ値。
- CUDAコンパイラ13.4.92、MSVC 19.51。実行APIの `cudaRuntimeGetVersion/cudaDriverGetVersion` は13030。この違いは生データにも記録した。
- 物理RAMは33,594,175,488 bytes（約31.29 GiB）。前回は空き約19.8 GB、再開時は約5.1 GB。`GlobalMemoryStatusEx` で確保前に確認した。旧 `pin_capacity` のガード緩和とVRAM超過managed試験は廃止した。
- 再開後は **1確保最大8,000,000,000 bytes**。空きRAM・コミット量に3 GiB＋128 MiBの余裕を残す条件をC++に置き、Python側も事前判定する。送受信用の複数確保は合計で判定し、各 `cudaMallocHost` の直前・直後にも確認する。指示の3 GBより多い3 GiBを予約する。
- 転送はpinned送信・受信各512 MiB、独立GPU配列各512 MiB、演算用32 MiB。GPU合計1,107,296,256 bytesで2 GB未満のためGPUロックは不要。ホストの転送バッファ合計は約1.07 GB。768 MiB各2本の案は空きRAMのガードで見送った。
- 16回の転送、ウォームアップ4回、7条件×3試行。ホストの投入開始から最終 `cudaDeviceSynchronize` までを計時。確保・初期化・照合は帯域時間に含めない。H2D/D2Hの全バイトのパターン照合を行った。
- 演算は独立した32 MiB配列の128回FMAで、片方向転送と同程度の単独時間になるよう起動回数を校正した。CISの模擬計算ではない。H2D・D2H・演算は別の非既定ストリーム。演算の校正カーネルは再開後0.205 ms、managedの最長起動は30.57 msで、いずれも100 ms未満。

### 5.2 実効帯域

単位は10進のGB/s、各3試行の中央値。双方向と演算重複の帯域は **H2D+D2Hの総転送量/経過時間**。重複の改善率は `(転送単独時間+演算単独時間)/重複時間` であり、CISの高速化率ではない。

| 条件 | 旧256 MiB | 旧512 MiB | 再開512 MiB | 再開の重複改善率 |
|---|---:|---:|---:|---:|
| H2D | 23.92 | 35.52 | 35.37 | — |
| D2H | 34.32 | 34.07 | 33.69 | — |
| 双方向合計 | 27.29 | 34.69 | 33.88 | — |
| H2D＋演算 | 29.59 | 34.01 | 33.69 | 1.915倍 |
| D2H＋演算 | 25.37 | 33.69 | 32.91 | 1.917倍 |
| 双方向＋演算 | 28.60 | 36.08 | 34.14 | 1.496倍 |

再開後の512 MiBではH2D 0.242870秒、D2H 0.254954秒、双方向0.507143秒、演算単独0.245358秒、双方向＋演算0.503149秒。双方向の合計帯域は片方向と同程度で、単一コピーエンジンという属性と整合する。512 MiBの代表モデルには33 GB/sを使うが、旧256 MiBでは24 GB/s近くまで落ちた。WDDMの投入・転送サイズ・RAM負荷などの影響を分離しておらず、全チャンク幅に33 GB/sを保証しない。§6には24 GB/sの場合も示す。旧草稿の「常に36～38 GB/s」という表現は生データに合わせて訂正した。

GPUメモリアクセスとの競合、実ソルバーのイベント依存、スロットの充填・排出は未測定。pinnedと別ストリームによる演算重複の条件、コピーエンジン数の説明は [CUDA Best Practices Guide §10.1.2](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#asynchronous-and-overlapping-transfers-with-computation) で再開時にも確認した。

### 5.3 pinned の確保量と20 GBの見通し

| 出所・要求量 | 結果 | 空きRAM（前→確保中） |
|---|---|---|
| 再開・1 GB | 成功、全4 KiBページに書込み・端点照合 | 5.007→4.021 GB |
| 再開・4 GB | 確保前のRAMガードで見送り | 約5.10 GB、確保なし |
| 再開・8 GB | 確保前のRAMガードで見送り | 約5.10 GB、確保なし |
| 旧・1 GiB / 4 GiB | 成功、前回生データから採用可能な上限内の記録 | 19.831→18.729 / 19.814→15.506 GB |

新たに8 GBを超える確保APIは呼んでいない。`8 GB+1 byte` の引数をpinned/managed各1件でサイズ判定だけに渡し、CUDA初期化より前に拒否されることも確認した。8 GB自体も現在の空きでは試せないため、上限を8 GBと測定できたという意味ではない。

20 GBは今回の上限外であり、確保せず推定とする。現時点では空きRAM約5.1 GBなので不可。設計時の空き20～24 GBでも、20 GBをpinして3 GiBとCPU追加領域を残す条件は厳しく、4 GiBまでの成功から20 GBの成功を外挿できない。13～14 GBを複数チャンクでpinするBも総量は未検証。確保失敗時に実行開始前で止め、必要なら通常RAM＋少量stagingを別途評価する。

旧草稿に使われた8 GiB～20 GiBの上限探索は、再開依頼より前の記録として残す。今回は8 GB超の旧記録を容量の結論・実行可能性の根拠から除外した。旧14 GiB成功から「Windowsの上限は14 GiB」とも判断しない。大量のpinはシステム性能を落とし得るため、単発の成功とGUI/PBCを含む長時間運用を区別する。[CUDA Best Practices Guide §10.1.1](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#pinned-memory)

### 5.4 WindowsのManaged Memory

**このWindows環境では、managed memoryによるVRAM超過ページングを退避の設計に使わない。** ローカルのCUDA 13.4 `include/cuda_runtime_api.h` の `cudaMallocManaged` 説明は、デバイスメモリ超過・退避を `cudaDevAttrConcurrentManagedAccess != 0` のGPUに対して説明している。このGPUの実測は0。

公式Webも閲覧ツールで2026-10-20に確認できた（シェルからネットワークを使うビルド・依存取得はしていない）。[CUDA Programming Guide §4.1.3「Unified Memory on Windows, WSL, and Tegra」](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/unified-memory.html#unified-memory-on-windows-wsl-and-tegra) は、該当するWindows/`concurrentManagedAccess=0` 環境で細粒度のGPUオンデマンド移動とGPUメモリ超過をサポートしないと説明する。[§2.6.2.3「Limited Unified Memory Support」](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/understanding-memory.html#limited-unified-memory-support) もCPU/GPUの同時アクセス・移動の制限を示す。Linuxのフルサポートとは区別する。

再開後は64 MiBだけを確保し、CPUで全4 KiBページを初期化 → GPUで各ページに書込み → 同期 → CPUで全ページ照合に成功した。GPU側計時30.69 ms、最長起動30.57 ms。これは小さなmanaged配列が使える証拠であり、VRAM超過ページングの実証ではない。

前回の `managed_observations.json` には、VRAM総量＋256 MiB（17,362,911,232 bytes）の確保APIだけが成功したとの転記記録がある。元レコードは旧スクリプトで上書きされ、残った `run.log` の終了コードと転記が出所であり、今回の生測定と同等には扱わない。その後の超過アクセス試験は45秒で未完了となり終了要求された。どの段で停止したか不明で、GPUアクセスの完了・ページング帯域は未検証。再開時には該当プロセス・GPUロックは残っておらず、小容量managed試験と転送が再び完了した。

新しい8 GB上限はこのGPUの物理VRAM約17.09 GBより小さいため、超過確保・アクセスは再試行できない。前回のAPI成功を公式のGPU超過サポートや有用な帯域の証明にはしない。WDDMの物理配置や退避が関係する可能性は推測に留める。明示的転送なら転送量・カーネル時間・メモリ予算をアプリ側で管理できる。

## 6. 1反復の時間見積もり

### 6.1 走査回数

ここでは開いた管、`boundary_iterations=1`、`inner_sweeps=4`、`inner_wall_update=true`、`n_cis=5`、補正は既定の`blend`を対象とする。転送が必要な分布をHとし、Rの部分は移動しない。

| 処理 | H2D | D2H | 合計 |
|---|---:|---:|---:|
| CISのprepare: モーメント＋境界 | 2H | 0 | 2H |
| 4内部スイープ | 4H | 4H | 8H |
| 内部スイープ間の壁更新3回 | 3H | 0 | 3H |
| **CIS 1反復** | **9H** | **4H** | **13H** |
| GSIS: CISを5反復 | 45H | 20H | 65H |
| PBC前の半段prepare | 2H | 0 | 2H |
| 補正の判定＋適用 | 2H | H | 3H |
| **GSIS 1反復** | **49H** | **21H** | **70H** |

境界の全セル読込は減らせるが、最初は保守的に計上した。`block_passes=2` は同じスロット内なのでPCIe転送を倍にしない。離散平衡のNewton反復数も転送回数に掛けない。閉領域の規格化はprepareごとに追加2H、境界の整合更新1回追加につきH、独立した断面診断は全量方式なら各Hを加える。GSIS補正後の最終診断や出力、初期化・STOP処理はこの定常反復表の外。実際にはバージョンキャッシュの再利用も反映して数える。

任意設定では、内部スイープ数m、境界反復数b、壁更新onをu=1（offは0）、CIS回数nとすると、CISは `[1+b+2m+u(m−1)]H`、GSISはそのn倍＋半段prepareの `(1+b)H`＋補正の `3H`。閉領域・断面診断・終端出力の追加は別途数える。これをJSONの交通量にも使う。

転送が全く隠れない場合、`T_CIS = T_CIS,0 + 13H/B`、`T_GSIS = T_GSIS,0 + 70H/B`。各CISのスイープ分8Hだけが完全に隠れる理想では、それぞれ追加は5H/B、30H/B。この理想値も非重複部分を残した条件付きの下側目安であり、達成を保証する下限ではない。より強い融合・境界キャッシュを実装すれば走査そのものを減らせる。

### 6.2 管の大きさ F=9.338 GB

設計の `18.6 GB / B` は1回の全速度スイープの往復に相当し、B=33 GB/sなら約0.566秒。既定のCISは内部スイープが4回あり、prepareと壁更新も含むので、段階別実装では**約3.68秒/CIS**、GSISでは**約19.81秒/外側反復**の転送となる。

S2の既存回帰結果（8スレッド）を時間基準に使う。δ=1/10でCIS部分は1CIS反復あたり3.745/4.025秒、GSIS外側反復全体は27.536/30.877秒。初期化・最終出力は除く。設計にある古い0.6～3.7秒を、現在の既定4内部反復と同じ測定量とはみなさない。

| 全fを強制的にRAMへ置く比較 | 理想的にスイープ転送が隠れる | 転送が隠れない |
|---|---:|---:|
| CIS 1反復 | 5.16～5.44秒、約1.35～1.38倍 | 7.42～7.70秒、約1.91～1.98倍 |
| GSIS 1反復 | 36.03～39.37秒、約1.27～1.31倍 | 47.35～50.69秒、約1.64～1.72倍 |

管の通常運用は全VRAM方式なので約1.00倍を維持する。退避機構の比較試験だけAを強制する。

### 6.3 F=25 GB

同じ計算密度・反復設定のまま、全VRAMでの時間が25/9.338≈2.677倍になる仮定を置く。25 GBはこのGPUに載らないため、この表の基準は実測ではなく**同じ大きさを載せられる仮想的な全VRAM実行**。メッシュ構造・PBC反復数・Newtonの対象セル割合が変われば線形にならない。

| 方式 | 仮想全VRAMのGSIS | 理想的なスイープ重複 | 重複なし | 実行可能性 |
|---|---:|---:|---:|---|
| A: 全25 GBをホスト | 73.72～82.66秒 | 96.44～105.39秒、約1.27～1.31倍 | 126.75～135.69秒、約1.64～1.72倍 | 時間だけの仮想値。現在の空きRAMでは不可。pin総量も未検証 |
| B: 12 GB常駐、13 GB退避 | 同上 | 85.54～94.48秒、約1.14～1.16倍 | 101.29～110.24秒、約1.33～1.37倍 | §4.1の細いチャンクと十分な空きRAMが条件。現時点は不可 |

Bの13 GB退避ならCISの転送は約5.12秒、GSISは約27.58秒/反復。現在の管のGSIS時間を基準にすると、25 GBのBはおおむね3.06～3.68倍の総時間になる。これは「問題が約2.68倍」と「退避の遅さ」を合わせた値。見積もり誤差を含む導入時の表示は、仮想全VRAM比で約1.2～1.8倍を暫定範囲にし、測定済みと表示しない。

25 GBのBでCIS 1反復を比較すると、仮想全VRAMの10.03～10.77秒に対し、理想的重複で12.00～12.74秒（1.18～1.20倍）、重複なしで15.15～15.90秒（1.48～1.51倍）。CISとGSISでは転送以外のCPU/PBC時間の割合が異なる。

旧256 MiBの低い帯域に合わせ **24 GB/s** に落とすと、BのGSISは89.97～98.91秒（理想重複）、111.63～120.58秒（重複なし）、仮想全VRAM比1.20～1.51倍。管全量退避のGSISは転送だけで27.24秒となり、モデルの倍率範囲は約1.38～1.99倍に広がる。小チャンクやRAM圧迫時にはこの感度を考慮する。暫定のGUI計画値1.2～1.8倍はB向けの目安であり、性能保証や既知の最悪値ではない。

GPUの作業チャンクを小さくした影響、追加集約、ホスト処理、イベント、メモリ競合、S3の壁用キャッシュ、FP64、反復回数の変化は未測定。これらは `13H/B` の転送モデルには含まれていない。収束反復数が同じことも実装後の検証事項。

## 7. 決まった順序と結果の一致

チャンクID・セル/面ID・論理タイルIDを固定し、**イベントの完了順で足さない**。各チャンク内は既存のKahan補正と固定木を使い、部分和をFP64で論理順に結合する。atomicによる総和は使わない。常駐するチャンクと退避するチャンクの計算・集約順を同じにする。

現在の `k_moments` は256 laneごとに全速度をストライド走査し、FP32では `Accum<float>` の `s/c` を持ち続け、最後に固定木で足す。`k_boundary_sums` は128 lane。単純にチャンクごとの最終和を足すだけでは、現在と浮動小数点の足し算の順序が異なる。**同一設定の再実行の再現性**と、**既存全VRAM実装とのビット一致**は区別する。

最初は同じ物理分割を使う全VRAM参照経路を用意し、A/Bとビット一致させる。既存の全速度カーネルとの比較は丸め誤差を許す。厳密な既存ビット一致が必要なら、laneのKahan状態をチャンク境界で持ち越し、最後だけ現在と同じ木で縮約する。その全セル保持はモーメントだけでも `135104×256×13×2×4≈3.60 GB` と大きい。モーメント専用のセルバッチ化などを別途設計する必要があり、標準方式のP=0.15 GBには含めない。

残差も物理チャンク幅を変えると部分和の括り方が変わるため、比較では論理タイルと結合順を固定する。補正の最小値は通常の有限値なら結合しやすいが、閾値近傍の置換/skip、NaN、±0も試験する。`shift_` は全速度のモーメント確定後に一度だけ更新し、GSIS補正のskippedセルのshiftとflagsも既存どおりにする。

## 8. 実装する場合の分担・試験・作業量

以下は次の依頼の作業候補であり、S4では変更していない。

| 作業 | 主なファイル候補 | 確かめ方 | 目安 |
|---|---|---|---:|
| 配置・確保・view分離 | 新規 `src/kinetic/host_distribution.*`、`kinetic_types.hpp`、`kinetic_kernels.hpp`、backend factory、kineticのCMake | FP32/64、端数・鏡映軌道、chunk→外部番号の往復、確保失敗でリークしない | 1～2人日 |
| Aの同期版、全速度集約、補正2段化 | 新規 `kinetic_backend_offload.cpp`、CUDA kernel/launch、`kinetic_solver.cpp`、`kinetic_backend.hpp` | 同じ分割の全VRAMと1スイープ・prepare・全負値モードが一致、既存評価順を比較 | 3～4人日 |
| ストリーム重複・Bの常駐配置 | 上記、`kinetic_backend_cuda.cpp` の計時共通化、イベントとスロット管理 | R=0/中間/全量、1/2スロット、完了順を乱しても一致、GPU/ホストのピーク確認 | 2～3人日 |
| 入口・CLI・GUIとの接続 | `kinetic_params.*`、`config/case_config.*`、`memory_estimate.*`、`cmd_check_case.cpp`、`cmd_run.cpp`、`cmd_cis_run.cpp`、Python GUI側 | JSONの既存キー互換、RAM不足の実行拒否、STOP・確保失敗・結果保存、OOM時に再実行しない | 1～2人日 |
| 大規模測定・回帰・文書 | `tests/kinetic/`、CLI試験、`runs/phase8/`、S4続編報告 | 下記の数値比較と時間/メモリ実測 | 2～3人日 |

合計9～14人日程度を初期目安とする。担当SのC++と担当GのGUIを分けられる。S3一般法線Maxwell壁・既存ビット一致の強制・全fチェックポイントを同時に要求する場合は別見積もり。

必要な比較は次のとおり。

1. 小さな同一入力で、既存CUDA、全VRAMの同じ分割、A、Bを比較。まずFP64の1回の各演算、次にFP32でf・全モーメント・境界20値・残差・Newton flags・補正flags・shiftを比較する。補正前の同じfから `all/created/limit/blend`、`collisionless`のskipを強制する。
2. `inner_sweeps=1/4`、壁更新on/off、`boundary_iterations=1/複数`、`block_passes=1/2`、連続/離散平衡、対称・周期・圧力・真空・閉領域の質量規格化を組み合わせた少数の代表試験。全体反復の比較だけで中間の壁更新の差を見落とさない。
3. 同じ分割同士はfとflagsをビット比較し、重複on/off・常駐割合・完了順を変えても同じこと。旧カーネルとのFP32比較は初期目標として `|Δf|≤5e−7 max(|f_ref|,1e−6)`、マクロ量相対1e−6程度を用い、極低温・熱流束の相殺・負値閾値近傍は別途絶対誤差とflagsを見る。これらは未検証の受入案で、通らない差を自動的に緩めない。
4. 管δ=1/10の既存S2回帰を使い、同じ収束判定で反復履歴・質量流量・保存性・補正量を照合する。既存丸め差を許す場合の流量目標は相対1e−5以内を暫定案とする。S1の四面体GSISの未解決精度を退避方式の受入と混同しない。
5. 予算を下げて小さなケースを強制退避し、利用可能RAM/VRAMの変化、allocation失敗、STOP、再開、出力、非同期中の例外・解放を検証する。全f APIのFP64一括コピーを避けたまま試験する。
6. 最後にGPUロックを取得し、9.3 GBのA/B比較と25 GB級のBで実測。SMカーネル時間、DMA量・待ち時間、ピークVRAM、pinned/通常RAM、全反復時間を分けて記録する。カーネルは既存のセル/スイープ分割を維持して100 ms未満を目標にする。

## 9. GUIと `check-case --json` の案

S2の `memory` は `distribution/workspace/overhead/reserve/free/budget/chunk/total/fits/model/precision`。**これらの意味を無言で変えない。** `fits` は従来の全量VRAM方式のまま保持し、追加の退避候補を `memory.offload` にネストする案とする。

| 追加キー案 | 型・意味 |
|---|---|
| `offload.supported` | bool。このビルドで退避が実装され、境界条件・精度が対応するか。検討段階ではfalse |
| `offload.mode` | `hybrid_velocity_chunks` / `host_velocity_chunks` / `none` |
| `offload.feasible` | bool またはnull。全メモリ条件を満たす見込み。未読メッシュ・未確認RAMならnull |
| `offload.resident_distribution` / `host_distribution` | VRAM/RAMに分けて保持するfのbytes。合計が元のdistributionと一致 |
| `offload.device_total` / `host_total` / `pinned_total` | 配列・集約・ステージング等を含む計画ピークbytes。host_totalはこの実行で追加するRAMで、OS全体の使用量ではない |
| `offload.host_free` / `host_reserve` / `host_budget` | RAM空き・OS/GUI/PBCなどに残す量・予算、bytes。取得不可はnull |
| `offload.chunk` / `slots` / `async_engine_count` | 計画チャンク幅、fスロット数、コピーエンジン数 |
| `offload.transfer_bytes_per_iteration` / `iteration_kind` | 既定設定も加味した交通量、`cis` / `gsis`。式の係数だけでなく現在の設定を使う |
| `offload.estimated_transfer_s` / `slowdown_min` / `slowdown_max` | 転送単独時間と、全VRAMで同じ問題を解く場合に対する倍率範囲。算定できなければnull |
| `offload.estimate_source` / `estimate_confidence` | 例 `s4_pcie_2026_10_20`、`measured_transfer_modelled_solver`。転送だけの実測と本体実測を区別 |
| `offload.model` / `reason_codes` | 例 `cuda_host_offload_v1`。未対応壁、RAM不足、pin未確認等の機械可読理由 |

設定は `[kinetic] memory_mode="device"/"host_offload"`、`host_budget_bytes`、`device_resident_bytes`（0なら全ホスト、未指定なら予算から選択）などを候補にする。既定はdevice。`auto`による勝手な低速実行・FP16への切替はしない。利用者が退避を選んだ後のR/Kの調整は自動化してよい。

GUIの表示例は「**RAMを約13 GB使います。遅くなる見込み 約1.2～1.8倍（推定）**」。詳細でVRAM常駐量・RAM予算・基準（同じ問題の全VRAM計算）を見られるようにする。管との問題サイズの違いを倍率に混ぜない。見積もり不能なら倍率を出さず理由を出す。`supported=false`の現状は実行ボタンを有効にしない。

G3b側は現在の `memory.fits=false` で止める動作を維持し、将来 `offload.supported && feasible` かつ利用者の選択があるときだけ通す。`check-case --json --no-mesh` ではセル数が確定しないので、RAM使用量や倍率を確定値として出さない。開始前に空きと実際のpin確保を再確認し、失敗時は具体的な不足量を返す。

## 10. S4で行った変更・試験と未確認事項

変更は本書と `runs/phase8/s4/` の独立計測コード（`CMakeLists.txt`、`transfer_probe.cu`、`run_probe.py`、`summarize_results.py`）・ログ・JSONのみ。再開後にCMakeListsは変更していない。ビルド生成物は `build/phase8_s4`、一時領域は `build/tmp_s4`。旧試験はGPUロックを使用した記録があり、再開後は全試験がGPU 2 GB未満なので作成していない。`build/phase8_s` は使わず、src/tests/本体CMake/他の設計・仕様文書/依存関係を変更せず、commit・pushもしていない。

再現コマンド（リポジトリのルート、PowerShell）は次のとおり。

```powershell
New-Item -ItemType Directory -Force build/tmp_s4, build/phase8_s4 | Out-Null
$env:TEMP = "$PWD\build\tmp_s4"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
cmd /c 'call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul 2>&1 && cmake -S runs/phase8/s4 -B build/phase8_s4 -G Ninja -DCMAKE_CUDA_COMPILER="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.4/bin/nvcc.exe" -DCMAKE_BUILD_TYPE=Release && cmake --build build/phase8_s4'
python/.venv/Scripts/python.exe runs/phase8/s4/run_probe.py
python/.venv/Scripts/python.exe runs/phase8/s4/summarize_results.py
```

- 再開後の独立ビルドは成功、終了コード0（`resume_build_final.log`）。最初の呼出しは無害なvswhereのstderrをPowerShellが例外扱いしたが、コンパイルとリンクは完了していた。stderr処理を整えて終了コードを再確認した。旧試行のCUDA 13.0/VSの非互換や廃止済み属性のエラーは前回修正済みで、今回は追加の回避フラグを使っていない。
- 再開後は9件の計画のうち7子プロセスが終了コード0。帯域7条件×3試行の21サンプル、H2D/D2Hの全バイト照合1組、pinned 1 GB照合1件、managed 64 MiB照合1件、サイズ上限ガード2件が通過。RAMガードで4/8 GB確保と768 MiB転送の計3件を見送った（うち2件は子プロセス起動前、1件はCUDA初期化後）。失敗0件、タイムアウト0件。環境情報の取得1件も完了。
- 上記のPythonコマンドで計測・集約。現在の実行では最初の `run_probe.py` 後、512 MiB転送だけを追加して同じ `run()` で `resume_results.json` に追記した。再現用スクリプトにはその512 MiB試験も組み込んである。旧結果と合わせて63帯域サンプル・全バイト照合3組を検査し、`summary.json` の `validation.status=passed`。スキップは成功した確保に数えない。
- 本体のコードハッシュは旧計測時の9ファイルと一致することも集約時に確認。独立計測のみなので本体のctest/pytestは実行していない。再現時の空きRAMによって成功・スキップ件数は変わる。新計測の生データを上書きしたくない場合は `resume_results.json` と `resume_*.log` を同じディレクトリに別名保存してから実行する。

未確認事項は、実退避ソルバーの速度・収束・旧カーネルとの丸め差、pinnedの分割総上限、長時間のRAM圧迫、実際の25 GBケース、S3一般法線Maxwell壁、CPU stagingの帯域、FP64/FP16/BF16、Linux。`runs/` と `build/` は既存gitignore対象なので、再現ソース・小さなJSONを共有する場合は統括役が別途保存対象を決める。

決めたことは、内部壁更新の評価順を維持すること、Newton法のf転送を数えないこと、全量のホスト複製を作らないこと、式(14)の負値判定を2段にすること、倍率の基準と未測定の費用を明示すること。いずれも現行の結果と検証可能性を優先した。再開後の計測ではガードを緩和せず、8 GB超の旧結果を容量の結論から除外した。現在のRAM圧迫を繰り返さないための判断である。

次の担当は本書と `runs/phase8/s4/summary.json` のコードハッシュを確認し、S3の完成した境界処理を追加で読むこと。S4の計測プロセスは完了し、GPUロックは残っていない。統括役への質問・ユーザー判断待ちは次節にまとめる。今回の検討を完成させるうえで返答を要する質問はない。将来の25 GB受入試験は今回の計測上限の承認には含まれず、RAM余裕と新たな実装依頼が条件となる。

## 11. 統括役とユーザーが決めること

1. **B（常駐できる速度チャンク＋残りのpinned RAM）を採るか、退避を実装するか。** 推奨はBを採用し、Aを強制できる同期版で一致を確認してから重複・常駐最適化へ進むこと。必要な作業量は9～14人日程度。
2. 全VRAM参照との差を丸め誤差で許すか、現行カーネルとのビット一致を必須にするか。推奨は同じ分割経路同士のビット一致＋現行方式との丸め誤差検証。厳密な既存ビット一致はメモリ・工数が増える。
3. 最初の対応範囲を既存境界のCIS/GSISにするか、S3の一般法線Maxwell壁まで必須にするか。S3の完成形を確認して対応可否を判定し、未対応は明示的に止める。
4. RAMの予約量と許容する遅さ。初期案は開始時の利用可能RAMから4 GiB以上を残し、既存CPUデータ・追加PBC領域も別途計上する。倍率は推定範囲を表示し、利用者が明示的に選んだ場合に実行する。
5. 25 GB級を最初の受入規模とするか、先に管9.3 GBの強制退避だけを試作するか。推奨は管での一致・計時を先に通し、次に25 GBのBでメモリと速度を確認すること。
