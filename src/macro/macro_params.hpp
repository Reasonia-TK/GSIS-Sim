#pragma once
// PBC マクロソルバーのパラメータ。ケース TOML の [macro] から読む(docs/design_pbc.md)。
// PBC の反復の上限 M と CFL は [gsis] の max_pbc・cfl_pbc を使う。
// 項目を足したら parse_macro_params と docs/design_pbc.md §8 も直す。

#include <string>

#include "config/case_config.hpp"

namespace gsis {

struct MacroParams {
  // 移流項: "upwind1" | "upwind2"(仮定 A11)。GSIS の段と pbc-run が使う(初期化は init_advection)。
  // 既定はフェーズ 5c で "upwind2" にした(従来は "upwind1")。1 次(論文の式 (27)(28) のドナーセル)は GSIS の不動点を
  // 動かし、管 δ = 10 で入口と出口の質量流量が 4% 食い違った(docs/phase5b_d10_report.md §4.4)。管のケースは
  // フェーズ 4 から TOML で "upwind2" を指定していた
  std::string advection = "upwind2";
  std::string linear_solver = "gmres";     // "bicgstab" | "gmres"
  std::string preconditioner = "ilu0";     // "ilu0" | "amg" | "cpr"(温度の系は cpr のとき ilu0)
  double linear_rtol = 1e-3;               // 線形ソルバーの相対許容誤差(仮定 A8)
  long long linear_max_iterations = 200;   // 線形ソルバーの反復の上限
  long long gmres_restart = 30;            // GMRES の再始動までの反復数
  double pbc_tolerance = 1e-6;             // PBC 内側の収束判定 ‖ΔW‖/‖W‖(仮定 A8)
  double relaxation = 1.0;                 // 更新の緩和係数
  long long init_iterations = 50;          // NSF のみの初期化での PBC 反復数(仮定 A9)
  // GSIS の初期化(NSF のみの PBC 単独、gsis-solver run)の移流項: "upwind1"(既定)| "upwind2"。
  // 初期化は f^eq の初期値を作るだけなので頑丈な 1 次にする(管の δ = 1・1000 で upwind2 の初期化は真空の出口の近くに
  // 不自然な状態を残し、CIS が 2 回目で負の温度になった。docs/phase4_report.md)。GSIS の段は advection を使う
  std::string init_advection = "upwind1";
  double limiter_k = 5.0;                  // 境界面・2 次の移流項の再構成に使う Venkatakrishnan の定数 K
  double lsq_weight_power = 0.0;           // 最小二乗勾配の重み |r|^{-p}(0 なら重みなし)
  double max_relative_decrease = 0.5;      // 1 回の更新で p・T を下げてよい割合の上限(正値を保つ安全策)
  double pressure_floor = 0.0;             // p の下限(無次元、p_ref 単位。0 なら使わない)
  double temperature_floor = 0.0;          // T の下限(無次元、T0 単位。0 なら使わない)
  // 線形ソルバーの相対残差がこれを超えたら、その反復の更新を捨てて CFL を半分にしてやり直す
  double reject_linear_residual = 0.1;
  // 境界面の状態 W_{i,b}(式 (36))の作り方(GSIS の段):
  //   "limited": 制限付きの線形再構成(論文どおり)
  //   "walls":   壁と対称面は制限付きの線形再構成、開いた境界(入口・出口・真空・遠方場)は W_i
  //   "cell":    すべて W_i(1 次)
  std::string boundary_reconstruction = "limited";
  // 単独モード(NSF のみ。初期化と pbc-run)での W_{i,b} の作り方(値は boundary_reconstruction と同じ)。
  // 一様な状態から始める単独モードでは、制限付きの再構成は発散・停滞しやすいので既定は "cell"
  // (docs/phase3_report.md)
  std::string standalone_boundary_reconstruction = "cell";
  // 単独モードで、境界セルの最小二乗勾配に使う境界面の値:
  //   "cell":         勾配 0 の外挿(W_b = W_i。既定)
  //   "moments":      境界面の分布(流出側は再構成した状態の Maxwell 分布、流入側は境界条件の分布)の
  //                   モーメント(運動論側の face_state のマクロ版。前回の評価の値を使う)
  //   "wall_moments": 壁と対称面だけ "moments"、開いた境界は "cell"
  std::string standalone_face_state = "cell";
  // 境界ヤコビアンの評価(式 (49)):
  //   "cell":           W_{i,b} = W_i として評価する(論文どおり)
  //   "reconstruction": さらに再構成のセル自身への依存 ∂W_{i,b}/∂W_i = 1 − φ (Σ_k c_k)·(x_b − x_i)
  //                     (リミッタ φ と隣のセルの値は固定。c_k は最小二乗の係数)を列ごとに掛ける
  std::string boundary_jacobian = "cell";
  // GSIS の段の HoT の面の値(フェーズ 4、docs/design_gsis.md §3):
  //   "cell": セル中心の HoT(式 (12)、セル中心の最小二乗勾配の σ^NSF)を重み w で面へ補間する(式 (30)、論文どおり)
  //   "face": 面の値を HoT_f = (σ のセルの値の補間) − σ^NSF_f(W^{n+1/2})(q も同様)とする。σ^NSF_f は PBC の面の
  //           粘性フラックスと同じ式 (29) の面勾配で評価するので、W = W^{n+1/2} で面の粘性フラックスが運動論の
  //           σ・q の補間に厳密に一致する(セル中心と面の NSF の差が、希薄で粗いセルで大きな残差にならない)。
  //           既定(平板で CIS 単独との不動点の差が 1/10〜1/20、管で "cell" は最初の段で発散。docs/phase4_report.md)
  std::string hot_interpolation = "face";
  // GSIS の段で境界セルの勾配に使う境界面の値(HoT の計算と PBC の両方):
  //   "kinetic": 運動論の境界面の分布のモーメント face_state(仮定 A7、論文どおり)
  //   "walls":   壁と対称面は face_state、開いた境界(入口・出口・真空)はセルの値(勾配 0 の外挿)
  //   "cell":    すべてセルの値
  std::string gsis_face_state = "kinetic";
  // GSIS の段で連続式にも擬似時間項 V/(Δt T) を入れる(論文の式 (57) にはない。PbcStageOptions::continuity_pseudo_time)。
  // Λ に 8μ/(3ρ d_n) が入るので、密度の小さいセルほど Δp が強く抑えられる。PBC 段の定常解は変わらない
  bool gsis_continuity_pseudo_time = false;
  // GSIS の段で、局所 Kn(式 (8) をセルの状態で評価、L_ref 基準)がこの値を超えるセルの p, u, T を段の間は固定する
  // (線形系の行を恒等にする)。0 なら固定しない(論文どおり)。自由分子流に近いセルでは NSF の粘性項が
  // 圧力の項より桁違いに大きく、速度がほぼ動かないので、離散的な連続式に正の解がなくなることがある
  // (管の下流槽の淀んだ角で、段のたびに p が半分になる)。固定したセルは補正 0 なので運動論だけで解く
  double gsis_freeze_kn = 0.0;
  // GSIS の段の CFL の立ち上げ(フェーズ 5a。論文は CFL = 500 で固定)。正なら、段ごとにこの CFL から始め、残差に応じて
  // [gsis].cfl_pbc まで上げる(SER: CFL_m = min(CFL, CFL_{m−1} g, max(c0, c0 r_0/r_m))、g = gsis_cfl_growth)。
  // 0 なら固定(論文どおり)。管 δ = 1000 では、運動論の状態から始めた段が CFL 500 の最初の数歩で残差を増やして発散した
  double gsis_cfl_start = 0.0;
  double gsis_cfl_growth = 2.0;
  // GSIS の段で、温度(W^{n+1/2})がこの値より低いセルも段の間は固定する(フェーズ 5a。0 なら使わない)。
  // 速度格子で解像できない冷たい流れ(管 δ = 1000 の真空の出口の手前の噴流。Δξ = 2/3 では T ≲ 0.3)では、
  // NSF の合成方程式が等エントロピーの膨張で T → 0.04 まで冷やし、その補正の後の CIS が負の温度になった
  double gsis_freeze_tmin = 0.0;
  // 連続流の領域で HoT を弱める(フェーズ 5b。0 なら使わない、論文どおり)。正なら、GSIS の段の HoT(面の値とセルの値)を
  // w = Kn² / (Kn² + gsis_hot_kn²) 倍にする(Kn は W^{n+1/2} の局所 Kn、面は両側のセルの平均)。連続流の極限では HoT は
  // O(Kn²) で消えるはずだが、運動論とマクロの離散化の違いの分が残り、GSIS の反復で PBC の定常問題のほぼ中立なモード
  // (上流槽の循環)が 1 反復ごとに育つ(管 δ = 1000、docs/phase5b_d1000_report.md)。不動点は HoT の分だけ変わる
  double gsis_hot_kn = 0.0;
};

// [macro] を読んで検証する(節が無ければ既定値)
MacroParams parse_macro_params(const CaseConfig& config);

// 値の範囲を検査する(parse_macro_params の後半。プログラムで値を変えたときにも使う)
void validate_macro_params(const MacroParams& p);

}  // namespace gsis
