#pragma once
// 運動論ソルバー(CIS)の 1 セル・1 速度点の計算(CPU 参照実装と GPU で共通の GSIS_HD 関数)。
// 式は docs/design_cis_gpu.md §4・§5・§7、論文の式 (15)–(17)。
//
// 記号: セル i、速度点の保存位置 p(チャンク内の位置 t = p − k0)、面 e(セル → 面の CSR の要素)、
// a = ξ·S_out(セル i から見た外向きの面積ベクトルとの内積)。
//
// - 面の値(2 次): a ≥ 0 ならセル i からの制限付き線形再構成、a < 0 なら隣接セル j から。
//   境界面の a < 0(流入)は境界条件の流入分布(拡散壁・圧力・真空)か、対称面なら鏡映した速度点の
//   流出側の再構成値
// - 勾配(最小二乗)とリミッタに使う境界面の値(1 次): 流入側は流入分布(対称面は鏡映した速度点の
//   セル値)、流出側はセル値(勾配 0 の外挿)。並進周期の面はセル値(寄与 0)
// - 定常残差 R = Σ_e a f_e − (V/τ)(f^S − f_i)
// - LU-SGS(1 次風上の陰的演算子): D = V/Δt + V/τ + s Σ a⁺、L・U は同じ空間ブロックの隣接セルの a⁻
//   (擬似時間刻みが "global" なら V/Δt はセルごとの値・s = 1、"velocity" なら V/Δt = Σa⁺/CFL として
//   s = 1 + 1/CFL)

#include "core/math.hpp"
#include "core/vec3.hpp"
#include "kinetic/kinetic_types.hpp"
#include "physics/limiter.hpp"
#include "physics/maxwell.hpp"

namespace gsis::kinetic {

using i64 = long long;

// ---------------------------------------------------------------------------
// 総和の補助: FP32 は Kahan の補正付き、FP64 はそのまま足す(結果は double で取り出す)
// ---------------------------------------------------------------------------

template <class F>
struct Accum;

template <>
struct Accum<float> {
  float s = 0.0f;
  float c = 0.0f;
  GSIS_HD void add(float x) {
    const float y = x - c;
    const float t = s + y;
    c = (t - s) - y;
    s = t;
  }
  GSIS_HD double value() const { return static_cast<double>(s) - static_cast<double>(c); }
};

template <>
struct Accum<double> {
  double s = 0.0;
  GSIS_HD void add(double x) { s += x; }
  GSIS_HD double value() const { return s; }
};

// ---------------------------------------------------------------------------
// 参照の補助
// ---------------------------------------------------------------------------

template <class F>
GSIS_HD inline Vec3T<F> velocity_at(const KineticView<F>& v, int p) {
  return Vec3T<F>{v.xi_x[p], v.xi_y[p], v.xi_z[p]};
}

template <class F>
GSIS_HD inline F f_at(const KineticView<F>& v, int cell, int p) {
  return v.f[static_cast<i64>(cell) * v.nv + p];
}

template <class F>
GSIS_HD inline F flux_coefficient(const Vec3T<F>& xi, F sx, F sy, F sz) {
  return xi.x * sx + xi.y * sy + xi.z * sz;
}

// 境界の流入分布 scale · f^eq(m; ξ)
template <class F>
GSIS_HD inline F inflow_value(const BoundaryInflow<F>& b, const Vec3T<F>& xi) {
  return b.scale * maxwellian(b.m, xi);
}

// ---------------------------------------------------------------------------
// 勾配とリミッタ(docs/design_cis_gpu.md §5)
// ---------------------------------------------------------------------------

// 勾配・リミッタ用の境界面の値
//   - 対称面: 流入・流出とも (f_i(ξ) + f_i(ξ'))/2(ξ' は鏡映)。鏡映した仮想セル(面の向こう側の距離 2d)
//     の値 f_i(ξ') を使う最小二乗の行と同じ傾きになる。厳密に対称な解では面の値そのもの
//   - 並進周期: セル値(寄与 0)
//   - そのほか: 流出側はセル値(勾配 0 の外挿)、流入側は流入分布
template <class F>
GSIS_HD inline F boundary_value_first_order(const KineticView<F>& v, const CsrEntry<F>& c, int cell,
                                            int p, const Vec3T<F>& xi, F f_i) {
  const BoundaryFaceInfo<F>& b = v.bfaces[c.bface];
  if (b.kind == kBcPeriodic) return f_i;
  if (b.kind == kBcSymmetry) return F(0.5) * (f_i + f_at(v, cell, p ^ b.mirror_xor));
  const F a = flux_coefficient(xi, c.sx, c.sy, c.sz);
  if (a >= F(0)) return f_i;  // 流出側: 勾配 0 の外挿
  return inflow_value(v.binflow[c.bface], xi);
}

// セル cell・速度点 p の制限付き勾配 φ∇f(最小二乗 + Venkatakrishnan)
template <class F>
GSIS_HD inline Vec3T<F> limited_gradient(const KineticView<F>& v, int cell, int p, const Vec3T<F>& xi,
                                         F f_i) {
  const int e0 = v.csr_offsets[cell];
  const int e1 = v.csr_offsets[cell + 1];
  F gx = F(0), gy = F(0), gz = F(0);
  F fmax = f_i, fmin = f_i;
  for (int e = e0; e < e1; ++e) {
    const CsrEntry<F>& c = v.csr[e];
    const F fk = c.other >= 0 ? f_at(v, c.other, p) : boundary_value_first_order(v, c, cell, p, xi, f_i);
    const F d = fk - f_i;
    gx += c.cx * d;
    gy += c.cy * d;
    gz += c.cz * d;
    fmax = dmath::max(fmax, fk);
    fmin = dmath::min(fmin, fk);
  }
  const Vec3T<F> g{gx, gy, gz};
  if (v.limiter_mode == kLimiterNone && v.positivity == 0) return g;
  // 正値の制限は Venkatakrishnan の有無によらず使う。非正値のセルは再構成しない。
  if (v.positivity != 0 && !(f_i > F(0))) return Vec3T<F>{};
  F eps2 = v.eps2[cell];
  if (v.limiter_mode == kLimiterVenkatRelative) {
    const F s = dmath::max(dmath::abs(fmax), dmath::abs(fmin));
    eps2 *= s * s;
  }
  VenkatakrishnanAccumulator<F> acc(fmax - f_i, fmin - f_i, eps2);
  F proj_min = F(0);  // 面への増分 ∇f·r_f の最小(正値の制限用)
  for (int e = e0; e < e1; ++e) {
    const CsrEntry<F>& c = v.csr[e];
    if (c.other < 0 && v.bfaces[c.bface].kind == kBcPeriodic) continue;
    const F proj = g.x * c.dsx + g.y * c.dsy + g.z * c.dsz;
    if (v.limiter_mode != kLimiterNone) acc.add(proj);
    proj_min = dmath::min(proj_min, proj);
  }
  F phi = acc.value();
  if (v.positivity != 0 && proj_min < F(0)) {
    // 面の値 f_i + φ ∇f·r_f ≥ 0(f_i ≤ 0 なら φ = 0)
    phi = f_i > F(0) ? dmath::min(phi, f_i / (-proj_min)) : F(0);
  }
  return Vec3T<F>{phi * gx, phi * gy, phi * gz};
}

// 境界面での流出側の再構成値(勾配はその場で計算する。境界の集計用)
template <class F>
GSIS_HD inline F reconstruct_at_boundary(const KineticView<F>& v, const BoundaryFaceInfo<F>& b, int p,
                                         const Vec3T<F>& xi) {
  const F f_i = f_at(v, b.owner, p);
  if (b.kind == kBcPeriodic) return f_i;
  const Vec3T<F> g = limited_gradient(v, b.owner, p, xi, f_i);
  return f_i + g.x * b.dx + g.y * b.dy + g.z * b.dz;
}

// 内部面の流束の 1 点分 a f_face(フェーズ 5c、断面の質量流量の集計用)。c はセル cell の CSR の要素(内部面)、
// a = ξ·S_out(cell)。面の値は residual_value と同じ風上側の制限付き線形再構成(勾配はその場で計算する)なので、
// 和 Σ ϖ a f_face は CIS の残差に入る面の質量流束と同じになる
template <class F>
GSIS_HD inline F internal_face_flux_term(const KineticView<F>& v, int cell, const CsrEntry<F>& c, int p,
                                         const Vec3T<F>& xi) {
  const F a = flux_coefficient(xi, c.sx, c.sy, c.sz);
  F ff;
  if (a >= F(0)) {
    const F fi = f_at(v, cell, p);
    const Vec3T<F> g = limited_gradient(v, cell, p, xi, fi);
    ff = fi + g.x * c.dsx + g.y * c.dsy + g.z * c.dsz;
  } else {
    const F fj = f_at(v, c.other, p);
    const Vec3T<F> g = limited_gradient(v, c.other, p, xi, fj);
    ff = fj + g.x * c.dox + g.y * c.doy + g.z * c.doz;
  }
  return a * ff;
}

// ---------------------------------------------------------------------------
// 定常残差(式 (15) の右辺から左辺の流束を引いた形。2 次精度の風上流束 + 衝突項)
// ---------------------------------------------------------------------------

template <class F>
GSIS_HD inline F residual_value(const KineticView<F>& v, const ChunkView<F>& ch, int cell, int t) {
  const int p = ch.k0 + t;
  const Vec3T<F> xi = velocity_at(v, p);
  const F f_i = f_at(v, cell, p);
  const i64 wi = static_cast<i64>(cell) * ch.stride + t;
  const F gix = ch.gx[wi];
  const F giy = ch.gy[wi];
  const F giz = ch.gz[wi];
  F R = F(0);
  const int e0 = v.csr_offsets[cell];
  const int e1 = v.csr_offsets[cell + 1];
  for (int e = e0; e < e1; ++e) {
    const CsrEntry<F>& c = v.csr[e];
    int kind = -1;
    if (c.other < 0) {
      kind = v.bfaces[c.bface].kind;
      if (kind == kBcPeriodic) continue;
    }
    const F a = flux_coefficient(xi, c.sx, c.sy, c.sz);
    F ff;
    if (a >= F(0)) {
      ff = f_i + gix * c.dsx + giy * c.dsy + giz * c.dsz;
    } else if (c.other >= 0) {
      const i64 wj = static_cast<i64>(c.other) * ch.stride + t;
      ff = f_at(v, c.other, p) + ch.gx[wj] * c.dox + ch.gy[wj] * c.doy + ch.gz[wj] * c.doz;
    } else if (kind == kBcSymmetry) {
      // 鏡映した速度点(同じチャンクにある)の流出側の再構成値
      const int tm = t ^ v.bfaces[c.bface].mirror_xor;
      const i64 wm = static_cast<i64>(cell) * ch.stride + tm;
      ff = f_at(v, cell, ch.k0 + tm) + ch.gx[wm] * c.dsx + ch.gy[wm] * c.dsy + ch.gz[wm] * c.dsz;
    } else {
      ff = inflow_value(v.binflow[c.bface], xi);
    }
    R += a * ff;
  }
  const CellParams<F>& cp = v.cells[cell];
  R -= cp.coll * (shakhov_target(cp.s, xi) - f_i);
  return R;
}

// ---------------------------------------------------------------------------
// LU-SGS(ブロック・ヤコビ)。r は R → Δf* → Δf に置き換わる
// ---------------------------------------------------------------------------

// 前進: Δf*_i = (−R_i − Σ_{j<i, j∈block} a⁻_ij Δf*_j) / D_i
// D_i = (V/Δt + V/τ) + s Σ a⁺ + β Σ_{j∉block} |a⁻_ij|(β = interface_damping。ブロック外の隣接セルとの結合を
// 落とした分を対角に足して、ブロック境界の陽的な結合を安定にする。収束した解は変わらない)
// 「j < i」はスイープの順序での前後。pos が null なら内部の番号、そうでなければ pos[j] < pos[i]
// (sweep_order = "octant" などの向きごとの順序。kinetic_setup.hpp)
template <class F>
GSIS_HD inline void sweep_forward_cell(const KineticView<F>& v, const ChunkView<F>& ch, int cell, int t,
                                       int block_begin, int block_end, const Vec3T<F>& xi,
                                       const std::int32_t* pos = nullptr) {
  F ap = F(0);
  F am_out = F(0);
  F L = F(0);
  F Lout = F(0);  // ブロック外の風上の隣接セルの前の回の Δf の寄与(block_passes の 2 回目以降)
  const int e0 = v.csr_offsets[cell];
  const int e1 = v.csr_offsets[cell + 1];
  const int pi = pos != nullptr ? pos[cell] : cell;
  for (int e = e0; e < e1; ++e) {
    const CsrEntry<F>& c = v.csr[e];
    if (c.other < 0) {
      if (v.bfaces[c.bface].kind == kBcPeriodic) continue;
    }
    const F a = flux_coefficient(xi, c.sx, c.sy, c.sz);
    if (a > F(0)) {
      ap += a;
    } else if (c.other >= 0) {
      if (c.other < block_begin || c.other >= block_end) {
        if (ch.dprev != nullptr) {
          Lout += a * ch.dprev[static_cast<i64>(c.other) * ch.stride + t];
        } else {
          am_out -= a;
        }
      } else if ((pos != nullptr ? pos[c.other] : c.other) < pi) {
        L += a * ch.r[static_cast<i64>(c.other) * ch.stride + t];
      }
    }
  }
  const F D = v.cells[cell].diag + v.diag_scale * ap + v.interface_damping * am_out;
  const i64 wi = static_cast<i64>(cell) * ch.stride + t;
  const F Ri = ch.r0 != nullptr ? ch.r0[wi] : ch.r[wi];
  ch.r[wi] = (-Ri - Lout - L) / D;
}

// 後退: Δf_i = Δf*_i − (Σ_{j>i, j∈block} a⁻_ij Δf_j) / D_i、f_i += Δf_i(D_i は前進と同じ)
template <class F>
GSIS_HD inline void sweep_backward_cell(const KineticView<F>& v, const ChunkView<F>& ch, int cell, int t,
                                        int block_begin, int block_end, const Vec3T<F>& xi,
                                        const std::int32_t* pos = nullptr) {
  F ap = F(0);
  F am_out = F(0);
  F U = F(0);
  const int e0 = v.csr_offsets[cell];
  const int e1 = v.csr_offsets[cell + 1];
  const int pi = pos != nullptr ? pos[cell] : cell;
  for (int e = e0; e < e1; ++e) {
    const CsrEntry<F>& c = v.csr[e];
    if (c.other < 0) {
      if (v.bfaces[c.bface].kind == kBcPeriodic) continue;
    }
    const F a = flux_coefficient(xi, c.sx, c.sy, c.sz);
    if (a > F(0)) {
      ap += a;
    } else if (c.other >= 0) {
      if (c.other < block_begin || c.other >= block_end) {
        if (ch.dprev == nullptr) am_out -= a;
      } else if ((pos != nullptr ? pos[c.other] : c.other) > pi) {
        U += a * ch.r[static_cast<i64>(c.other) * ch.stride + t];
      }
    }
  }
  const F D = v.cells[cell].diag + v.diag_scale * ap + v.interface_damping * am_out;
  const i64 wi = static_cast<i64>(cell) * ch.stride + t;
  const F df = ch.r[wi] - U / D;
  ch.r[wi] = df;
  if (ch.update_f != 0) v.f[static_cast<i64>(cell) * v.nv + ch.k0 + t] += df;
}

// 速度点 p のスイープの順序(n_sweep_dirs > 0 のとき)。order は sweep_order の向き g の行、pos は sweep_pos の行
template <class F>
GSIS_HD inline void sweep_order_of(const KineticView<F>& v, int p, const std::int32_t*& order,
                                   const std::int32_t*& pos) {
  if (v.n_sweep_dirs > 0) {
    const i64 off = static_cast<i64>(v.sweep_dir_of[p]) * v.n_cells;
    order = v.sweep_order + off;
    pos = v.sweep_pos + off;
  } else {
    order = nullptr;
    pos = nullptr;
  }
}

// ---------------------------------------------------------------------------
// モーメント(シフト u0 のまわりの 13 個の和 → 15 個のモーメント)
// ---------------------------------------------------------------------------

inline constexpr int kMomentSums = 13;

// c = ξ − u0 に対する項: f, f c(3), f c c(6: xx, yy, zz, xy, yz, xz), f |c|² c(3)
template <class F>
GSIS_HD inline void moment_terms(F f, F cx, F cy, F cz, F (&t)[kMomentSums]) {
  t[0] = f;
  t[1] = f * cx;
  t[2] = f * cy;
  t[3] = f * cz;
  t[4] = t[1] * cx;
  t[5] = t[2] * cy;
  t[6] = t[3] * cz;
  t[7] = t[1] * cy;
  t[8] = t[2] * cz;
  t[9] = t[1] * cz;
  const F fc2 = f * (cx * cx + cy * cy + cz * cz);
  t[10] = fc2 * cx;
  t[11] = fc2 * cy;
  t[12] = fc2 * cz;
}

// 和 s(重みを掛ける前)とシフト u0 から 15 個のモーメントを作る(R = 1)。
// ρ ≤ 0 なら out[0] に ρ を入れ、残りは 0 にする(呼び出し側が検査する)
GSIS_HD inline void finalize_moments(const double (&s)[kMomentSums], double weight, double u0x, double u0y,
                                     double u0z, double* out) {
  const double s0 = s[0];
  if (!(s0 > 0.0)) {
    out[0] = weight * s0;
    for (int k = 1; k < kMomentsPerCell; ++k) out[k] = 0.0;
    return;
  }
  const double dx = s[1] / s0;
  const double dy = s[2] / s0;
  const double dz = s[3] / s0;
  const double rho = weight * s0;
  // 中心 2 次モーメント P_ab = w (s2_ab − s0 δu_a δu_b)
  const double pxx = weight * (s[4] - s0 * dx * dx);
  const double pyy = weight * (s[5] - s0 * dy * dy);
  const double pzz = weight * (s[6] - s0 * dz * dz);
  const double pxy = weight * (s[7] - s0 * dx * dy);
  const double pyz = weight * (s[8] - s0 * dy * dz);
  const double pxz = weight * (s[9] - s0 * dx * dz);
  const double p = (pxx + pyy + pzz) / 3.0;
  // q_a = ½ w [s3_a − δu_a tr(s2) − 2 (s2 δu)_a + 2 s0 |δu|² δu_a]
  const double tr2 = s[4] + s[5] + s[6];
  const double du2 = dx * dx + dy * dy + dz * dz;
  const double s2dx = s[4] * dx + s[7] * dy + s[9] * dz;
  const double s2dy = s[7] * dx + s[5] * dy + s[8] * dz;
  const double s2dz = s[9] * dx + s[8] * dy + s[6] * dz;
  out[0] = rho;
  out[1] = u0x + dx;
  out[2] = u0y + dy;
  out[3] = u0z + dz;
  out[4] = p / rho;
  out[5] = p;
  out[6] = pxx - p;
  out[7] = pyy - p;
  out[8] = pzz - p;
  out[9] = pxy;
  out[10] = pyz;
  out[11] = pxz;
  out[12] = 0.5 * weight * (s[10] - dx * tr2 - 2.0 * s2dx + 2.0 * s0 * du2 * dx);
  out[13] = 0.5 * weight * (s[11] - dy * tr2 - 2.0 * s2dy + 2.0 * s0 * du2 * dy);
  out[14] = 0.5 * weight * (s[12] - dz * tr2 - 2.0 * s2dz + 2.0 * s0 * du2 * dz);
}

// ---------------------------------------------------------------------------
// セルの係数(目標分布、衝突項、対角)
// ---------------------------------------------------------------------------

// m: 15 個のモーメント、volume: V、vdt: V/Δt、mu0・omega: 無次元の粘性 μ = μ0 T^ω。
// cold_bgk_T > 0 なら、T < cold_bgk_T のセルは Shakhov の多項式を使わない(BGK の目標分布。フェーズ 5b)
template <class F>
GSIS_HD inline CellParams<F> make_cell_params(const double* m, double volume, double vdt, double mu0,
                                              double omega, double Pr, double cold_bgk_T = 0.0) {
  const ShakhovParams<double> sd = make_shakhov_params<double>(
      m[0], Vec3T<double>{m[1], m[2], m[3]}, m[4], Vec3T<double>{m[12], m[13], m[14]}, 1.0, Pr);
  CellParams<F> cp;
  cp.s.eq.u = Vec3T<F>{static_cast<F>(sd.eq.u.x), static_cast<F>(sd.eq.u.y), static_cast<F>(sd.eq.u.z)};
  cp.s.eq.A = static_cast<F>(sd.eq.A);
  cp.s.eq.B = static_cast<F>(sd.eq.B);
  cp.s.uc = cp.s.eq.u;
  cp.s.q = Vec3T<F>{static_cast<F>(sd.q.x), static_cast<F>(sd.q.y), static_cast<F>(sd.q.z)};
  cp.s.theta = static_cast<F>(sd.theta);
  cp.s.coef = m[4] < cold_bgk_T ? F(0) : static_cast<F>(sd.coef);
  cp.s.g0 = F(0);
  cp.s.g4 = F(0);
  cp.s.g = Vec3T<F>{F(0), F(0), F(0)};
  const double mu = mu0 * dmath::pow(m[4], omega);
  const double coll = volume * m[5] / mu;
  cp.coll = static_cast<F>(coll);
  cp.diag = static_cast<F>(coll + vdt);
  return cp;
}

// Maxwell 分布の係数(精度を変えて作る)
template <class F>
GSIS_HD inline MaxwellParams<F> maxwell_params_from(double rho, double ux, double uy, double uz, double T) {
  const MaxwellParams<double> md = make_maxwell_params<double>(rho, Vec3T<double>{ux, uy, uz}, T, 1.0);
  MaxwellParams<F> m;
  m.u = Vec3T<F>{static_cast<F>(md.u.x), static_cast<F>(md.u.y), static_cast<F>(md.u.z)};
  m.A = static_cast<F>(md.A);
  m.B = static_cast<F>(md.B);
  return m;
}

// ---------------------------------------------------------------------------
// 保存的な離散平衡分布(フェーズ 5a。kinetic/discrete_equilibrium.hpp)
// ---------------------------------------------------------------------------

// ニュートン法の打ち切り: 収束(r = 1)か、相対誤差が 1e−4 未満で減り方が止まった(FP32 の丸めの水準)
GSIS_HD inline bool deq_accept(int r, double rel, double rel_prev, bool last) {
  if (r == 1) return true;
  if (r == 0 && rel < 1e-4 && (rel > 0.5 * rel_prev || last)) return true;
  return false;
}

// 1 セル分の離散平衡分布の係数(逐次。CPU 参照実装と境界の流入分布用)。xi_* は保存位置の順の速度点、w は重み。
// poly が null でなければ Shakhov の多項式の補正 γ も求める(poly の uc・q・theta・coef を使う)。
// 戻り値: ニュートン法の反復数(0 なら補正は不要で連続の Maxwell 分布)、−1 は失敗(連続の Maxwell 分布にする)
template <class F>
inline int discrete_maxwell_seq(const F* xi_x, const F* xi_y, const F* xi_z, int nv, double w, const DeqGrid& g,
                                double rho, double ux, double uy, double uz, double T, const ShakhovTarget<F>* poly,
                                MaxwellParams<F>& out, double (&gamma)[5]) {
  for (double& x : gamma) x = 0.0;
  out = maxwell_params_from<F>(rho, ux, uy, uz, T);
  if (deq_resolved(g, ux, uy, uz, T)) return 0;
  double beta[5];
  deq_initial_beta(rho, T, beta);
  const F fux = static_cast<F>(ux);
  const F fuy = static_cast<F>(uy);
  const F fuz = static_cast<F>(uz);
  double rel_prev = 1e300;
  for (int it = 0; it < g.max_iterations; ++it) {
    const MaxwellParams<F> m = deq_params<F>(beta, ux, uy, uz);
    double sums[kDeqSums] = {};
    F t[kDeqSums];
    for (int p = 0; p < nv; ++p) {
      const Vec3T<F> xi{xi_x[p], xi_y[p], xi_z[p]};
      const Vec3T<F> c{xi.x - fux, xi.y - fuy, xi.z - fuz};
      const F e = maxwellian(m, xi);
      const F pm = poly != nullptr ? shakhov_poly(*poly, c, dot(c, c)) : F(0);
      deq_terms<F>(e, pm, c.x, c.y, c.z, t);
      for (int k = 0; k < kDeqSums; ++k) sums[k] += static_cast<double>(t[k]);
    }
    double b[5] = {beta[0], beta[1], beta[2], beta[3], beta[4]};
    double rel = 0.0;
    const int r = deq_newton_step(sums, w, rho, T, g.tolerance, b, rel);
    if (deq_accept(r, rel, rel_prev, it + 1 == g.max_iterations)) {
      out = m;
      if (poly != nullptr && !deq_shakhov_correction(sums, w, gamma)) {
        for (double& x : gamma) x = 0.0;
      }
      return it + 1;
    }
    if (r < 0) break;
    for (int k = 0; k < 5; ++k) beta[k] = b[k];
    rel_prev = rel;
  }
  out = maxwell_params_from<F>(rho, ux, uy, uz, T);
  for (double& x : gamma) x = 0.0;
  return -1;
}

// 離散平衡の Maxwell 分布の係数を、同じ形の連続の Maxwell 分布の (ρ', u', T') に直す(境界の流入分布用)
template <class F>
inline void maxwell_params_to_state(const MaxwellParams<F>& m, double& rho, double& ux, double& uy, double& uz,
                                    double& T) {
  T = 1.0 / (2.0 * static_cast<double>(m.B));
  const double two_pi_T = 2.0 * kPi * T;
  rho = static_cast<double>(m.A) * two_pi_T * std::sqrt(two_pi_T);
  ux = static_cast<double>(m.u.x);
  uy = static_cast<double>(m.u.y);
  uz = static_cast<double>(m.u.z);
}

// GSIS の負値の判定に使う値(old: 補正前の f、x: 補正後の f)。
// created = false なら x(論文どおり)、true なら補正で生じた負の部分 x − min(old, 0)
template <class F>
GSIS_HD inline F negative_check_value(F old, F x, bool created) {
  return created ? x - dmath::min(old, F(0)) : x;
}

// GSIS の補正を縮める係数の上限(1 点分。gsis_negative_check = "limit")。補正 d = f^eq(W_new) − f^eq(W_half) を
// f_new = f + α d としたとき f_new ≥ min(f, 0) + thr(thr < 0 は閾値)になる α ≤ 1 の上限。d ≥ 0 なら 1
template <class F>
GSIS_HD inline F correction_alpha_limit(F old, F d, F thr) {
  if (!(d < F(0))) return F(1);
  const F room = old - (dmath::min(old, F(0)) + thr);  // ≥ −thr > 0
  return dmath::min(F(1), room / (-d));
}

// GSIS の補正の後の平衡分布との混合の割合(1 点分。gsis_negative_check = "blend")。
// f_new = (1 − θ)(f + d) + θ e(e = f^eq(W_new) > 0)が f_new ≥ min(f, 0) + thr になる最小の θ ∈ [0, 1]。
// f + d がすでにそうなら 0。モーメントは θ によらず W_new(f + d も e もモーメントが W_new)
template <class F>
GSIS_HD inline F blend_theta(F old, F d, F e, F thr) {
  const F g = old + d;
  const F bound = dmath::min(old, F(0)) + thr;
  if (!(g < bound)) return F(0);
  const F den = e - g;
  if (!(den > F(0))) return F(1);
  return dmath::min(F(1), (bound - g) / den);
}

// gsis_negative_check の種類(カーネルに渡す整数)
inline constexpr int kNegativeAll = 0;      // 論文どおり(補正後の最小値)
inline constexpr int kNegativeCreated = 1;  // 補正で生じた負の部分
inline constexpr int kNegativeLimit = 2;    // 置き換えずに、そのセルの補正を α 倍に縮める
inline constexpr int kNegativeBlend = 3;    // 補正した f と f^eq(W_new) を最小の割合 θ で混ぜる


// ---------------------------------------------------------------------------
// 境界の集計の 1 点分(ϖ f_b を double で足す)
// ---------------------------------------------------------------------------

// flux5 += a_n ψ (ϖ f_b)、mom5 += ψ (ϖ f_b)
template <class F, class A>
GSIS_HD inline void add_boundary_terms(F wf, const Vec3T<F>& xi, F a_n, A* flux5, A* mom5) {
  const F e = F(0.5) * (xi.x * xi.x + xi.y * xi.y + xi.z * xi.z);
  const F af = a_n * wf;
  flux5[0].add(af);
  flux5[1].add(af * xi.x);
  flux5[2].add(af * xi.y);
  flux5[3].add(af * xi.z);
  flux5[4].add(af * e);
  mom5[0].add(wf);
  mom5[1].add(wf * xi.x);
  mom5[2].add(wf * xi.y);
  mom5[3].add(wf * xi.z);
  mom5[4].add(wf * e);
}

}  // namespace gsis::kinetic
