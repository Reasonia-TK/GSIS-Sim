#pragma once
// 運動論ソルバー(CIS)の CPU・GPU 共有のデータ構造(POD)。CUDA のヘッダを含まない。
//
// - すべて無次元量(spec §3.2)。無次元の気体定数は 1(p = ρT)
// - セルと境界面の番号は、ソルバー内部の番号付け(RCM で並べ替えたもの。kinetic_setup.hpp)
// - 分布関数 f の並びは [セル][速度点の保存位置]。保存位置は鏡映の組(軌道)ごとにまとめた順
//   (kinetic_setup.hpp の VelocityLayout)。速度チャンクは保存位置の連続区間
// - 1 セル・1 速度点の計算は kinetic_kernels.hpp の GSIS_HD 関数(CPU 参照実装と GPU で共通)

#include <cstdint>

#include "core/types.hpp"
#include "kinetic/discrete_equilibrium.hpp"
#include "physics/maxwell.hpp"

namespace gsis::kinetic {

// 境界面の種類(運動論側の扱いで分ける)
enum BoundaryKind : std::int32_t {
  kBcDiffuseWall = 0,  // 完全拡散壁(壁速度あり): 流入 = ρ_Γ f^eq(1, u_w, T_w)
  kBcPressure = 1,     // 圧力入口・出口: 流入 = f^eq(W_b)(付録 A.2)
  kBcVacuum = 2,       // 真空: 流入 = 0
  kBcSymmetry = 3,     // 対称面: 流入 = 鏡映した速度点の流出側の再構成値
  kBcPeriodic = 4,     // 並進周期(1 セル厚の対面): 流束の和から除く。勾配への寄与は 0
};

// セル → 面の CSR の 1 要素(セル i から見た面)
template <class F>
struct CsrEntry {
  std::int32_t other;  // 内部面なら相手のセル j、境界面なら −1
  std::int32_t bface;  // 境界面なら境界面の番号(0 始まり)、内部面なら −1
  F sx, sy, sz;        // セル i から見て外向きの面積ベクトル S_out
  F dsx, dsy, dsz;     // x_f − x_i
  F dox, doy, doz;     // x_f − x_j(内部面のみ。境界面は 0)
  F cx, cy, cz;        // 最小二乗勾配の係数 c_k(∇φ_i = Σ_k c_k (φ_k − φ_i))
};

// 境界面の固定の情報
template <class F>
struct BoundaryFaceInfo {
  std::int32_t owner;       // 隣接セル
  std::int32_t kind;        // BoundaryKind
  std::int32_t mirror_xor;  // 対称面: 鏡映した速度点の保存位置 = p ^ mirror_xor(それ以外は 0)
  std::int32_t patch;       // パッチ番号
  F nx, ny, nz;             // 流体領域から外向きの単位法線
  F area;                   // 面積
  F dx, dy, dz;             // x_f − x_owner
};

// 境界面の流入分布 scale · f^eq(m; ξ)(拡散壁は m が単位密度の壁 Maxwell 分布、scale = ρ_Γ。
// 圧力境界は m = f^eq(W_b)、scale = 1。真空は scale = 0)。反復の初めに決めて固定する(遅れ評価)
template <class F>
struct BoundaryInflow {
  MaxwellParams<F> m;
  F scale;
};

// Shakhov の目標分布(フェーズ 5a で保存的な離散平衡の補正を足せる形にした。kinetic/discrete_equilibrium.hpp)
//   f^S(ξ) = eq(ξ) [1 + coef (c·q)(|c|²/θ − 5) + g0 + g·c + g4 |c|²/2]、c = ξ − uc
// 連続の平衡分布(従来)では eq = f^eq(ρ, u, T)、uc = u、g = 0
template <class F>
struct ShakhovTarget {
  MaxwellParams<F> eq;  // 指数の部分(離散の補正では係数を変えた Maxwell 分布)
  Vec3T<F> uc;          // 多項式の中心(セルの u)
  Vec3T<F> q;           // 熱流束
  F theta;              // R T
  F coef;               // (1 − Pr) / (5 p θ)
  F g0, g4;             // 保存の補正の多項式(連続の平衡分布なら 0)
  Vec3T<F> g;
};

// Shakhov の多項式 P = coef (c·q)(|c|²/θ − 5)(c = ξ − uc、c2 = |c|²)
template <class F>
GSIS_HD inline F shakhov_poly(const ShakhovTarget<F>& s, const Vec3T<F>& c, F c2) {
  return s.coef * dot(c, s.q) * (c2 / s.theta - F(5));
}

template <class F>
GSIS_HD inline F shakhov_target(const ShakhovTarget<F>& s, const Vec3T<F>& xi) {
  const Vec3T<F> c = xi - s.uc;
  const F c2 = dot(c, c);
  const F m = maxwellian(s.eq, xi);
  return m * (F(1) + shakhov_poly(s, c, c2) + (s.g0 + dot(s.g, c) + F(0.5) * s.g4 * c2));
}

// セルごとの目標分布と係数(反復の初めにモーメントから作る)
template <class F>
struct CellParams {
  ShakhovTarget<F> s;  // Shakhov の目標分布 f^S
  F coll;              // V/τ
  F diag;              // V/Δt + V/τ(LU-SGS の対角の速度によらない部分)
};

// リミッタの方式
enum LimiterMode : std::int32_t {
  kLimiterNone = 0,
  kLimiterVenkatAbsolute = 1,  // ε² = (K h)³
  kLimiterVenkatRelative = 2,  // ε² = (K h)³ · max(|f_max|, |f_min|)²
};

// モーメントの出力: 1 セルあたり 15 個(gpu/maxwell_moments_launch.hpp と同じ並び)
//   ρ, u_x, u_y, u_z, T, p, σ_xx, σ_yy, σ_zz, σ_xy, σ_yz, σ_xz, q_x, q_y, q_z
inline constexpr int kMomentsPerCell = 15;

// 境界面の集計の出力: 1 面あたり 20 個(面積を掛ける前の、単位面積あたりの値)
//   [0, 5)   流出側 Σ_{Ξ+} ϖ (ξ·n) ψ f_b      ψ = (1, ξ_x, ξ_y, ξ_z, |ξ|²/2)
//   [5, 10)  流入側 Σ_{Ξ-} ϖ (ξ·n) ψ f_b(拡散壁は単位密度の値。ρ_Γ を掛ける前)
//   [10, 15) 流出側 Σ_{Ξ+} ϖ ψ f_b(境界面の分布のモーメント)
//   [15, 20) 流入側 Σ_{Ξ-} ϖ ψ f_b(拡散壁は単位密度の値)
inline constexpr int kBoundarySumsPerFace = 20;

// メッシュ・速度格子・分布関数への参照(ポインタは実行する側のメモリ)
template <class F>
struct KineticView {
  std::int32_t n_cells = 0;
  std::int32_t nv = 0;  // 速度点の数
  std::int32_t n_bfaces = 0;
  // 速度格子(保存位置の順)。重みは一様
  const F* xi_x = nullptr;
  const F* xi_y = nullptr;
  const F* xi_z = nullptr;
  F weight = F(0);
  // メッシュ
  const std::int32_t* csr_offsets = nullptr;  // 長さ n_cells + 1
  const CsrEntry<F>* csr = nullptr;
  const F* eps2 = nullptr;  // セルごとの (K h)³ = K³ V
  std::int32_t limiter_mode = kLimiterVenkatAbsolute;
  // 正値を保つ制限(フェーズ 4、[kinetic].positivity_limiter): 1 なら、面への再構成値が負にならないように
  // φ をさらに φ ≤ f_i / (−∇f·r_f) で抑える(f_i ≤ 0 のセルは 1 次)
  std::int32_t positivity = 0;
  F diag_scale = F(1);      // LU-SGS の対角 D = diag + diag_scale · Σa⁺(擬似時間刻み "velocity" で 1 + 1/CFL)
  F interface_damping = F(0);  // ブロック外の隣接セルとの結合 |a⁻| を対角に足す係数 β(kinetic_kernels.hpp)
  const BoundaryFaceInfo<F>* bfaces = nullptr;
  const BoundaryInflow<F>* binflow = nullptr;
  const CellParams<F>* cells = nullptr;
  // 分布関数 [n_cells][nv]
  F* f = nullptr;
  // LU-SGS のスイープの順序(フェーズ 5a、[kinetic].sweep_order)。n_sweep_dirs = 0 なら内部の番号の順(従来)。
  // n_sweep_dirs > 0 なら、速度点 p は向き g = sweep_dir_of[p] の順序を使う:
  //   sweep_order[g × n_cells + k] は k 番目に処理するセル(各ブロックの区間 [cb, ce) の中の並べ替え)、
  //   sweep_pos[g × n_cells + c] はセル c の位置 k(前進スイープでは位置の小さい隣接セルが L、後退では大きい方が U)
  std::int32_t n_sweep_dirs = 0;
  const std::int32_t* sweep_order = nullptr;
  const std::int32_t* sweep_pos = nullptr;
  const std::uint8_t* sweep_dir_of = nullptr;
  // 保存的な離散平衡分布(フェーズ 5a、[kinetic].equilibrium = "discrete" なら 1。kinetic/discrete_equilibrium.hpp)
  std::int32_t discrete_eq = 0;
  DeqGrid deq;
  // 離散平衡のニュートン法が収束しないセルの扱い(フェーズ 5b、[kinetic].deq_fallback):
  // 1 = 衝突なし・GSIS の補正なし("collisionless")、0 = 連続の Maxwell 分布("continuous")
  std::int32_t deq_fallback = 0;
  // この温度より冷たいセルの目標分布は BGK(Shakhov の項なし。フェーズ 5b、[kinetic].cold_bgk_temperature。0 なら使わない)
  double cold_bgk_temperature = 0.0;
};

// GSIS の補正の印(gsis_correction の replaced)。0 = そのまま、1 = 置換・混合・縮小、
// 2 = 補正しなかった(離散平衡が作れない。[kinetic].deq_fallback = "collisionless")
inline constexpr int kCorrectionSkipped = 2;

// 速度チャンクの作業領域([n_cells][stride]、チャンク内の位置 t = p − k0)
template <class F>
struct ChunkView {
  std::int32_t k0 = 0;      // チャンクの最初の保存位置
  std::int32_t kc = 0;      // チャンクの点数
  std::int32_t stride = 0;  // 作業領域の行の長さ(最大のチャンク幅)
  F* gx = nullptr;          // 制限付き勾配 φ∇f
  F* gy = nullptr;
  F* gz = nullptr;
  F* r = nullptr;           // 残差 R → 前進スイープ後 Δf* → 後退スイープ後 Δf
  // ブロック間の結合の反復(フェーズ 5a、[kinetic].block_passes)。2 回目以降のスイープでは、r0 に残差 R、dprev に前の回の
  // Δf を置き、ブロック外の風上の隣接セルの寄与 Σ a⁻ Δf_prev を右辺に入れる(対角の補償 β は使わない)。null なら従来どおり
  // (ブロック外の Δf = 0 として β Σ|a⁻| を対角に足す)。update_f = 0 なら f を更新しない(最後の回だけ更新する)
  const F* r0 = nullptr;
  const F* dprev = nullptr;
  std::int32_t update_f = 1;
};

}  // namespace gsis::kinetic
