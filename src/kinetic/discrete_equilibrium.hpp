#pragma once
// 保存的な離散平衡分布(フェーズ 5a、[kinetic].equilibrium = "discrete")。CPU・GPU 共通の GSIS_HD 関数。
//
// 速度格子の上の離散モーメント Σ_k ϖ ψ(ξ_k) f_k(ψ = 1, ξ, |ξ|²/2)が (ρ, ρu, ρE) に一致する Maxwell 分布
//   M_d(ξ) = exp(β0 + β·c + β4 |c|²/2)、c = ξ − u(u は目標の速度)
// の係数 β をニュートン法で決める(Mieussens の離散速度模型の平衡に相当。目的関数 Σ exp(β·ψ0) − β·U は凸)。
// M_d は係数を変えた Maxwell 分布 A exp(−B |ξ − u'|²)(B = −β4/2、u' = u − β/β4、A = exp(β0 + B |u' − u|²))
// なので、カーネルでは従来の MaxwellParams のまま計算できる。
//
// Shakhov の目標分布は f^S = M_d [1 + P(ξ) + γ0 + γ·c + γ4 |c|²/2]。P = (1 − Pr)(c·q)/(5pT) (|c|²/T − 5) は
// 連続の目標分布の多項式(中心はセルの u、温度は T)、γ は P の離散モーメントを打ち消す補正
// (J γ = −Σ ϖ ψ0 M_d P、J = Σ ϖ ψ0 ψ0ᵀ M_d)。これで Σ ψ f^S = Σ ψ f が速度格子の上で成り立つ(衝突項が保存的)。
//
// 速度格子で十分に解像される Maxwell 分布(中点則の誤差 exp(−2π²T/Δξ²) と範囲の外の裾 exp(−(端までの距離)²/2T)
// が exp(−27) ≈ 2e−12 未満)は、連続のものと丸め誤差の範囲で同じなので補正しない(deq_resolved)。
// 補正が効くのは冷たい流れ(Δξ = 2/3 では T ≲ 0.6)と、速度格子の端に近い速い流れだけ。

#include "core/math.hpp"
#include "core/vec3.hpp"
#include "physics/maxwell.hpp"

namespace gsis::kinetic {

// 速度格子の範囲と刻み(補正が要るかの判定用)
struct DeqGrid {
  double lo[3] = {0.0, 0.0, 0.0};
  double hi[3] = {0.0, 0.0, 0.0};
  double d[3] = {1.0, 1.0, 1.0};
  double criterion = 27.0;  // exp(−criterion) が無視できる大きさ
  int max_iterations = 20;  // ニュートン法の上限
  double tolerance = 1e-6;  // 収束の判定(モーメントの相対誤差。FP32 では 1e−6、FP64 では 1e−11)
};

// (u, T) の Maxwell 分布が速度格子で十分に解像されるか(true なら補正しない)
GSIS_HD inline bool deq_resolved(const DeqGrid& g, double ux, double uy, double uz, double T) {
  if (!(T > 0.0)) return false;
  const double u[3] = {ux, uy, uz};
  for (int a = 0; a < 3; ++a) {
    // 中点則の誤差 ~ exp(−2π² T / Δξ²)
    if (2.0 * kPi * kPi * T / (g.d[a] * g.d[a]) < g.criterion) return false;
    // 範囲の外の裾 ~ exp(−(端までの距離)² / 2T)
    const double m1 = g.hi[a] - u[a];
    const double m2 = u[a] - g.lo[a];
    if (!(m1 > 0.0 && m2 > 0.0)) return false;
    if (m1 * m1 < 2.0 * T * g.criterion || m2 * m2 < 2.0 * T * g.criterion) return false;
  }
  return true;
}

// ニュートン法の 1 点分の和(c = ξ − u、e = M_d(ξ)、pm = P(ξ)(Shakhov の多項式。Maxwell 分布だけなら 0))
//   s[0..4]  e ψ0(ψ0 = 1, cx, cy, cz, h、h = |c|²/2)
//   s[5..14] e ψ0_a ψ0_b(a ≥ 1、a ≤ b): xx, xy, xz, xh, yy, yz, yh, zz, zh, hh
//   s[15..19] e pm ψ0
inline constexpr int kDeqSums = 20;

template <class F>
GSIS_HD inline void deq_terms(F e, F pm, F cx, F cy, F cz, F (&t)[kDeqSums]) {
  const F h = F(0.5) * (cx * cx + cy * cy + cz * cz);
  t[0] = e;
  t[1] = e * cx;
  t[2] = e * cy;
  t[3] = e * cz;
  t[4] = e * h;
  t[5] = t[1] * cx;
  t[6] = t[1] * cy;
  t[7] = t[1] * cz;
  t[8] = t[1] * h;
  t[9] = t[2] * cy;
  t[10] = t[2] * cz;
  t[11] = t[2] * h;
  t[12] = t[3] * cz;
  t[13] = t[3] * h;
  t[14] = t[4] * h;
  const F ep = e * pm;
  t[15] = ep;
  t[16] = ep * cx;
  t[17] = ep * cy;
  t[18] = ep * cz;
  t[19] = ep * h;
}

// 5×5 の J(s から、重み w を掛けて)
GSIS_HD inline void deq_jacobian(const double (&s)[kDeqSums], double w, double (&J)[5][5]) {
  const double v[15] = {s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9], s[10], s[11], s[12], s[13], s[14]};
  // 上三角の並び: (0,0..4), (1,1..4), (2,2..4), (3,3..4), (4,4)
  int k = 0;
  for (int a = 0; a < 5; ++a) {
    for (int b = a; b < 5; ++b) {
      J[a][b] = w * v[k];
      J[b][a] = J[a][b];
      ++k;
    }
  }
}

// 5×5 の連立 1 次方程式 A x = b(部分ピボットのガウスの消去法)。特異なら false
GSIS_HD inline bool deq_solve5(double (&A)[5][5], double (&b)[5], double (&x)[5]) {
  for (int k = 0; k < 5; ++k) {
    int piv = k;
    double best = dmath::abs(A[k][k]);
    for (int r = k + 1; r < 5; ++r) {
      if (dmath::abs(A[r][k]) > best) {
        best = dmath::abs(A[r][k]);
        piv = r;
      }
    }
    if (!(best > 0.0)) return false;
    if (piv != k) {
      for (int cc = 0; cc < 5; ++cc) {
        const double tmp = A[k][cc];
        A[k][cc] = A[piv][cc];
        A[piv][cc] = tmp;
      }
      const double tb = b[k];
      b[k] = b[piv];
      b[piv] = tb;
    }
    for (int r = k + 1; r < 5; ++r) {
      const double f = A[r][k] / A[k][k];
      for (int cc = k; cc < 5; ++cc) A[r][cc] -= f * A[k][cc];
      b[r] -= f * b[k];
    }
  }
  for (int k = 4; k >= 0; --k) {
    double sum = b[k];
    for (int cc = k + 1; cc < 5; ++cc) sum -= A[k][cc] * x[cc];
    x[k] = sum / A[k][k];
    if (!(x[k] == x[k])) return false;  // NaN
  }
  return true;
}

// 係数 β(c = ξ − u に対する指数の係数)の初期値: 連続の Maxwell 分布
GSIS_HD inline void deq_initial_beta(double rho, double T, double (&beta)[5]) {
  const double two_pi_T = 2.0 * kPi * T;
  beta[0] = ::log(rho / (two_pi_T * dmath::sqrt(two_pi_T)));
  beta[1] = 0.0;
  beta[2] = 0.0;
  beta[3] = 0.0;
  beta[4] = -1.0 / T;
}

// β から Maxwell 分布の係数(A exp(−B |ξ − u'|²))
template <class F>
GSIS_HD inline MaxwellParams<F> deq_params(const double (&beta)[5], double ux, double uy, double uz) {
  const double B = -0.5 * beta[4];
  const double dx = -beta[1] / beta[4];
  const double dy = -beta[2] / beta[4];
  const double dz = -beta[3] / beta[4];
  MaxwellParams<F> m;
  m.u = Vec3T<F>{static_cast<F>(ux + dx), static_cast<F>(uy + dy), static_cast<F>(uz + dz)};
  m.A = static_cast<F>(::exp(beta[0] + B * (dx * dx + dy * dy + dz * dz)));
  m.B = static_cast<F>(B);
  return m;
}

// ニュートン法の 1 歩。s はいまの β での和(重みを掛ける前)、目標は (ρ, 0, 0, 0, 3ρT/2)(c = ξ − u の基底)。
// 戻り値: 1 = 収束(β は変えない)、0 = β を更新した、−1 = 失敗(J が特異など)。rel にモーメントの相対誤差を入れる
GSIS_HD inline int deq_newton_step(const double (&s)[kDeqSums], double w, double rho, double T, double tol,
                                   double (&beta)[5], double& rel) {
  double G[5] = {w * s[0] - rho, w * s[1], w * s[2], w * s[3], w * s[4] - 1.5 * rho * T};
  const double su = rho * dmath::sqrt(T);
  rel = dmath::max(dmath::abs(G[0]) / rho,
                   dmath::max(dmath::max(dmath::abs(G[1]), dmath::max(dmath::abs(G[2]), dmath::abs(G[3]))) / su,
                              dmath::abs(G[4]) / (rho * T)));
  if (!(rel == rel)) return -1;
  if (rel < tol) return 1;
  double J[5][5];
  deq_jacobian(s, w, J);
  double rhs[5] = {-G[0], -G[1], -G[2], -G[3], -G[4]};
  double d[5] = {0.0, 0.0, 0.0, 0.0, 0.0};
  if (!deq_solve5(J, rhs, d)) return -1;
  // 1 歩の大きさを抑える(温度は 2 倍・半分まで、密度の対数は 1 まで)
  double scale = 1.0;
  if (dmath::abs(d[4]) > 0.5 * dmath::abs(beta[4])) scale = 0.5 * dmath::abs(beta[4]) / dmath::abs(d[4]);
  if (dmath::abs(d[0]) * scale > 1.0) scale = 1.0 / dmath::abs(d[0]);
  for (int k = 0; k < 5; ++k) beta[k] += scale * d[k];
  if (!(beta[4] < 0.0)) return -1;
  return 0;
}

// Shakhov の多項式の離散モーメントを打ち消す補正 γ(J γ = −Σ ϖ ψ0 M_d P)。収束した β での和 s から
GSIS_HD inline bool deq_shakhov_correction(const double (&s)[kDeqSums], double w, double (&gamma)[5]) {
  double J[5][5];
  deq_jacobian(s, w, J);
  double rhs[5] = {-w * s[15], -w * s[16], -w * s[17], -w * s[18], -w * s[19]};
  return deq_solve5(J, rhs, gamma);
}

}  // namespace gsis::kinetic
