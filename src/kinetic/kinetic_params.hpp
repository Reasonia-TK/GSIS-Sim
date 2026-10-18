#pragma once
// 運動論ソルバー(CIS)のパラメータ。ケース TOML の [kinetic] から読む(docs/design_cis_gpu.md)。
// 項目を足したら parse_kinetic_params と docs/design_cis_gpu.md §11 も直す。

#include <string>

#include "config/case_config.hpp"

namespace gsis {

struct KineticParams {
  double pseudo_cfl = 0.0;            // CIS の擬似時間刻みの CFL 数。0 なら Δt = ∞(仮定 A2)
  // 擬似時間刻みの形: "global" は Δt_i = CFL V_i / (ξ_max Σ_f |S_f|)(セルごと、速度によらない)、
  // "velocity" は V/Δt_{i,k} = Σ_f max(ξ_k·S_f, 0) / CFL(速度点ごとの局所的な刻み)
  std::string pseudo_time = "global";
  double limiter_k = 3.0;             // Venkatakrishnan の定数 K(仮定 A4)
  std::string limiter = "venkatakrishnan";  // "venkatakrishnan" | "none"(勾配をそのまま使う)
  // Venkatakrishnan の ε² の決め方: "absolute" は ε² = (K h)³(無次元量)、
  // "relative" は ε² = (K h)³ · f_s²(f_s は自分と隣接の |f| の最大。速度点ごとの桁の違いをそろえる)。
  // 既定は relative・K = 3(相対的な許容幅 (Kh)^{3/2}。管では真空との境のような強い不連続でだけ働く。
  // absolute・K = 5 は f の大きさ(≤ 0.06)に対して ε が大きすぎて、リミッタがどこでも働かない。
  // docs/phase2_report.md §3・§4)
  std::string limiter_eps = "relative";
  double lsq_weight_power = 0.0;      // 最小二乗の重み |r|^{-p}(0 なら重みなし)
  // 正値を保つ制限(spec §9 の案、フェーズ 4 で追加): 面への再構成値が負にならないように、リミッタの φ をさらに
  // 面ごとに φ ≤ f_i / (−∇f·r_f) で抑える。冷たい超音速の噴流(管 δ = 1000 の真空の出口の近く)で、再構成の
  // 負の値から CIS が負の温度になるのを防ぐ。既定は false(フェーズ 2 の結果を変えない)
  bool positivity_limiter = false;
  long long velocity_chunk = 0;       // 速度チャンクの点数(0 なら空き VRAM から自動)
  // LU-SGS の空間ブロック数 P。0 なら自動(セル数 / 2048 を 1〜64 に収める)。
  // ブロックが小さすぎる(数セル)と 2 次精度の残差とブロック・ヤコビの組み合わせが不安定になる
  long long sweep_blocks = 0;
  // セルの並べ替えと LU-SGS のブロック: "rcb"(座標の再帰二分割でコンパクトなブロックに分け、中は逆 Cuthill–McKee)、
  // "rcm"(全体を RCM で並べて連続区間で分ける)、"none"(元の番号のまま連続区間)
  std::string cell_ordering = "rcb";
  std::string precision = "fp32";     // 分布関数の保存精度: "fp32" | "fp64"
  std::string backend = "cuda";       // "cuda" | "cpu"(CPU 参照実装、小さいメッシュ用)
  long long max_iterations = 100000;  // CIS 単独モードの反復の上限
  double initial_pressure = 1.0;      // 一様な初期状態の圧力(p_ref に対する比)
  double initial_temperature = 1.0;   // 一様な初期状態の温度(T0 に対する比)
  // 収束判定 (59) の u の分母の下限: Σ|u|²V を max(Σ|u|²V, floor² ΣV) にする(単位 √(R T0))。
  // 静止に近い流れ(平板の熱伝導など)で 0/0 にならないようにするため
  double convergence_u_floor = 1.0e-2;
  // 拡散壁の流入密度 ρ_Γ の緩和: 次の反復で使う ρ_Γ = ω ρ_Γ(新) + (1 − ω) ρ_Γ(前)。1 なら緩和なし。
  // 遅れ評価の ρ_Γ が壁どうしの往復で振動する場合の対策として用意したが、平板・管の試験では 0.5 にしても
  // 収束は速くならなかった(docs/phase2_report.md §4)。GSIS に渡す F⁻ は緩和しない値で作る(壁の正味質量流束 0)
  double wall_relaxation = 1.0;
  // LU-SGS のブロック境界の安定化: ブロック外の隣接セルとの結合(反復の中では 0 とみなす)の大きさ |a⁻| に
  // この係数を掛けて対角に足す。0 なら足さない。収束した解には影響しない。
  // 既定 0.5: 管(RCB・P = 64・Δt = ∞)で、0 ではブロック境界(管を横切る分割面)の残差が 1 反復 1% ほどで
  // 増え続けた。0.5・1 で単調に減る(0.5 の方が速い)。docs/phase2_report.md §3
  double interface_damping = 0.5;
  // GSIS の負値の置換(式 (14) の補正の後)の判定(フェーズ 4、docs/design_gsis.md §4):
  //   "all":     補正した f の最小値が閾値 −1e−8 ρ/T^{3/2} を下回るセルを f^eq(W^{n+1}) で置き換える(論文どおり)
  //   "created": 補正で生じた負の部分だけを見る。補正の前から負だった速度点は、補正で下がった量 f_new − f_old を
  //              閾値と比べる。CIS の 2 次の再構成で f が少し負になったセルを、補正が 0 でも毎回置き換えない
  //              (f が負にならない運動論の解では "all" と同じ)
  //   "limit":   置き換えずに、そのセルの補正を α 倍(α ≤ 1。補正で生じる負の部分が閾値を超えない最大の値)に縮める。
  //              運動論の非平衡の部分を消さない
  //   "blend":   補正した f と f^eq(W^{n+1}) を、補正で生じた負の部分が閾値を超えない最小の割合 θ で混ぜる
  //              f = (1 − θ)(f + d) + θ f^eq(W^{n+1})。θ = 1 が論文の置換、θ = 0 は置換なし。モーメントは θ によらず
  //              W^{n+1} で、わずかな負値のために非平衡の部分をすべて消すことがない(フェーズ 4)
  // 既定は "blend"(フェーズ 5b で "created" から変更。本実装の CIS は f を少し負にすることがあり、"all" では補正が 0 の
  // セルも毎回置き換わる(docs/phase4_report.md)。"created" の置換は非平衡の部分を消すので、平板の heat_nl の流束が
  // Kn = 1 で +2%・Kn = 10 で +79% ずれた。"blend" は一致する(docs/phase5_report.md §4.1))
  std::string gsis_negative_check = "blend";
  long long launch_cells = 16384;     // 1 回のカーネル起動で扱うセル数の上限(WDDM の TDR 対策)
  long long sweep_cells_per_launch = 4096;  // LU-SGS の 1 回の起動で各ブロックが進むセル数の上限
  // --- フェーズ 5a(CIS の強化。docs/design_cis_gpu.md §14) ---
  // 既定値はフェーズ 5b で、フェーズ 5a の管のケースの設定(octant・内部反復 4 回 + 壁の更新・ブロック間の反復 2 回・
  // 保存的な離散平衡)に変えた(ユーザーの判断 2026-10-17。従来の値は inner_sweeps = 1、inner_wall_update = false、
  // block_passes = 1、sweep_order = "block"、equilibrium = "continuous")。
  // 1 回の CIS 反復の中で、Shakhov の目標分布と境界の流入分布を固定したまま LU-SGS(勾配・残差・前進・後退)を
  // 何回まわすか(内部反復)。1 なら従来どおり。輸送方程式を解き切る「源反復」に近づける
  long long inner_sweeps = 4;
  // 内部反復の間に拡散壁の流入密度 ρ_Γ を新しい f から作り直す(壁での反射も内部反復で解く)。false なら固定
  bool inner_wall_update = true;
  // LU-SGS のブロック間の結合の反復の回数(1 なら従来のブロック・ヤコビ 1 回)。2 以上なら、同じ残差に対して前進・後退
  // スイープをくり返し、2 回目以降はブロック外の風上の隣接セルの前の回の Δf を右辺に入れる(1 次の陰的演算子の
  // ブロック・ヤコビ反復。ブロック境界の対角補償 β は 1 回目だけ)。f は最後の回だけ更新する
  long long block_passes = 2;
  // LU-SGS のブロックの中のセルの順序: "block"(従来: ブロックの中の逆 Cuthill–McKee の順を全速度点で共通に使う)、
  // "octant"(速度点を 4 つの体対角の向き (1, ±1, ±1) のうち最も近いものに割り当て、その向きへの射影 x·d の順に
  // 並べ替えた順序でスイープする。前進スイープでその向き、後退スイープで逆向きの速度点の風上の依存をほぼ厳密に解く)、
  // "dir13"(軸 3・面対角 6・体対角 4 の 13 方向で同じことをする)
  std::string sweep_order = "octant";
  // 平衡分布(Shakhov の目標分布、GSIS の補正(式 (14))、初期化、境界の流入の Maxwell 分布)の作り方:
  // "continuous"(従来: 連続の Maxwell 分布をそのまま速度格子の点で評価する)、
  // "discrete"(保存的な離散平衡: 速度格子の上の離散モーメントが (ρ, ρu, ρE) に一致するように係数をニュートン法で
  // 決める。速度格子で十分に解像されない冷たい流れ(Δξ = 2/3 では T ≲ 0.6)や格子の端に近い速い流れだけ補正する。
  // kinetic/discrete_equilibrium.hpp)
  std::string equilibrium = "discrete";
  // 保存的な離散平衡のニュートン法が収束しないセルの扱い(フェーズ 5b):
  // "collisionless"(既定): 衝突項を 0 にする(目標分布 = f。保存的で、f が負でなければ負にならない)。GSIS の補正では、
  //   W^{n+1/2} か W^{n+1} の離散平衡が作れないセルを補正しない(f をそのままにする)
  // "continuous"(フェーズ 5a): 連続の Maxwell 分布にする(離散モーメントが合わず、冷たいセルでは衝突のたびに密度・
  //   温度がずれて壊れる)
  // ニュートン法が収束しないのは、モーメントが速度格子で表せる限界に近いとき(冷たい流れ。速度格子の各軸の隣り合う
  // 2 点に分けた分布の温度 T_min(u) ≤ 0.11(Δξ = 2/3)より冷たい状態は、負でない分布では表せない)
  std::string deq_fallback = "collisionless";
  // 圧力境界(入口・出口)の流入分布 f^eq(W_b) の W_{i,b} の作り方(フェーズ 5b、付録 A.2):
  // "limited"(既定、論文どおり): セルの (p, u, T) の制限付き線形再構成(境界の値は境界面の分布のモーメント、仮定 A7)
  // "cell": セルの値(PBC の [macro].boundary_reconstruction = "cell" と同じ扱い。GSIS の段の整合補正 C⁻ に
  //         再構成の違いが入らない)
  std::string pressure_boundary_reconstruction = "limited";
  // この温度(T0 単位)より冷たいセルの目標分布を BGK(Shakhov の熱流束の項なし、Pr = 1)にする(フェーズ 5b)。0 なら使わない。
  // 速度格子で解像できない冷たいセル(Δξ = 2/3 では T ≲ 0.1)では、Shakhov の多項式 (c·q)(|c|²/T − 5) が隣の速度点で
  // 大きくなり(|c|/√T ≈ Δξ/√T が大きい)、目標分布が負になって CIS が負の温度になる(管 δ = 1000 の真空の出口の層)。
  // BGK の目標分布(保存的な離散平衡)は負にならない
  double cold_bgk_temperature = 0.0;
};

// [kinetic] を読んで検証する(節が無ければ既定値)
KineticParams parse_kinetic_params(const CaseConfig& config);

}  // namespace gsis
