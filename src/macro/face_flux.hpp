#pragma once
// 内部面のフラックス(論文 §3.3.1 の式 (21)–(32))と、そのヤコビアン(式 (42)–(47)、(54))。
//
// 無次元(R = 1、p = ρT)。面 f は owner o から neighbour n へ向く(S = A n、d = x_n − x_o)。
// フラックス F は owner から見た外向き(owner の残差に +、neighbour の残差に −)。
//
//   V̇^RC = u_f·S − β Δp、u_f = w_o u_o + w_n u_n、Δp = (p_n − p_o) − ∇̄p·d                  (21)(24)
//   ドナーセル up = o(V̇ ≥ 0)/ n(V̇ < 0)、ṁ = ρ_up V̇                                        (27)
//   F_adv = [ṁ, ṁ u_up + p_f S, ṁ H_up]、p_f = w_o p_o + w_n p_n、H = 5/2 T + |u|²/2             (28)
//   ∇φ_f = ∇̄φ + [(φ_n − φ_o) − ∇̄φ·d]/d_n n(速度の成分と温度)                               (29)
//   t = (σ^NSF_f + HoT_σ,f)·S、Q = (q^NSF_f + HoT_q,f)·S、F_vis = [0, t, t·ū + Q]、ū = (u_o + u_n)/2  (31)(32)
//
// 2 次の移流項では、(p, u, T) を両側から面へ制限付きで再構成し、V̇ の符号で風上の値を選んで
// ρ_up、u_up、H_up に使う(論文 §3.3.1 の最後)。ヤコビアンは常に 1 次(ドナーセルの値)で作る。

#include <array>

#include "core/vec3.hpp"
#include "macro/small_matrix.hpp"

namespace gsis::macro {

struct InternalFaceGeometry {
  Vec3 S;          // 面積ベクトル(owner → neighbour)
  Vec3 n;          // 単位法線
  double A = 0.0;  // 面積
  Vec3 d;          // x_n − x_o
  double dn = 0.0; // n·d
  double wo = 0.5; // 補間重み(式 (23))
  double wn = 0.5;
};

struct FaceCellState {
  double p = 1.0;
  Vec3 u{};
  double T = 1.0;
};

// 面の補助量。残差の評価では現在の状態から作る。ヤコビアンの試験ではこれを固定する。
struct FaceAuxiliary {
  double beta = 0.0;                 // β^RC(式 (25))
  Vec3 gradp_bar{};                  // ∇̄p = w_o ∇p_o + w_n ∇p_n
  std::array<Vec3, 3> gradu_bar{};   // ∇̄u_x, ∇̄u_y, ∇̄u_z
  Vec3 gradT_bar{};                  // ∇̄T
  double mu = 0.0;                   // μ_f = (μ_o + μ_n)/2
  double kappa = 0.0;                // κ_f = (κ_o + κ_n)/2
  std::array<double, 6> hot_sigma{}; // HoT_σ の面の値(xx, yy, zz, xy, yz, xz)(式 (30))
  Vec3 hot_q{};                      // HoT_q の面の値
};

struct InternalFaceResult {
  Vec5 F;                  // 面フラックス(owner から見て外向き)
  double vdot = 0.0;       // V̇^RC
  double mdot = 0.0;       // ṁ(選んだ再構成の ρ を使う。次回の a_u に使う ṁ̂)
  bool owner_is_donor = true;
  Vec3 u_up{};             // 移流に使った速度(運動エネルギーの欠損 𝒦 に使う)
  double defect_owner = 0.0;      // 𝒦_{o,f} = ½ ṁ |u_up − u_o|²(式 (51))
  double defect_neighbour = 0.0;  // 𝒦_{n,f} = ½ (−ṁ) |u_up − u_n|²
  // ヤコビアン用(1 次、ドナーセルの値)
  double rho_up1 = 0.0;    // ρ_up = p_up / T_up
  double mdot1 = 0.0;      // ρ_up V̇
  Vec3 u_up1{};            // ドナーセルの速度
};

// 対称テンソル(xx, yy, zz, xy, yz, xz)と S の積
inline Vec3 sym_mul(const std::array<double, 6>& s, const Vec3& v) {
  return Vec3{s[0] * v.x + s[3] * v.y + s[5] * v.z, s[3] * v.x + s[1] * v.y + s[4] * v.z,
              s[5] * v.x + s[4] * v.y + s[2] * v.z};
}

// 非直交補正付きの面勾配(式 (29))
inline Vec3 corrected_face_gradient(const InternalFaceGeometry& g, const Vec3& grad_bar, double phi_o,
                                    double phi_n) {
  const double corr = ((phi_n - phi_o) - dot(grad_bar, g.d)) / g.dn;
  return grad_bar + corr * g.n;
}

// NSF の応力 σ = −μ [G + Gᵀ − 2/3 (∇·u) I](式 (9))。gx・gy・gz は u_x・u_y・u_z の勾配(G の行)
inline std::array<double, 6> nsf_stress(double mu, const Vec3& gx, const Vec3& gy, const Vec3& gz) {
  const double div = gx.x + gy.y + gz.z;
  std::array<double, 6> s;
  s[0] = -mu * (2.0 * gx.x - (2.0 / 3.0) * div);
  s[1] = -mu * (2.0 * gy.y - (2.0 / 3.0) * div);
  s[2] = -mu * (2.0 * gz.z - (2.0 / 3.0) * div);
  s[3] = -mu * (gx.y + gy.x);
  s[4] = -mu * (gy.z + gz.y);
  s[5] = -mu * (gx.z + gz.x);
  return s;
}

// 面の NSF の応力 σ^NSF_f(非直交補正付きの面勾配(式 (29))と μ_f から。HoT は含まない)
inline std::array<double, 6> face_nsf_stress(const InternalFaceGeometry& g, const Vec3& uo, const Vec3& un,
                                             const FaceAuxiliary& aux) {
  // G_ab = ∂u_a/∂x_b(行 a が成分 a の面勾配)
  const Vec3 gx = corrected_face_gradient(g, aux.gradu_bar[0], uo.x, un.x);
  const Vec3 gy = corrected_face_gradient(g, aux.gradu_bar[1], uo.y, un.y);
  const Vec3 gz = corrected_face_gradient(g, aux.gradu_bar[2], uo.z, un.z);
  return nsf_stress(aux.mu, gx, gy, gz);
}

// 面の NSF の熱流束 q^NSF_f = −κ_f ∇T_f(HoT は含まない)
inline Vec3 face_nsf_heat_flux(const InternalFaceGeometry& g, double To, double Tn, const FaceAuxiliary& aux) {
  return -aux.kappa * corrected_face_gradient(g, aux.gradT_bar, To, Tn);
}

// 面の応力ベクトル t = (σ^NSF_f + HoT_σ,f)·S(式 (31))
inline Vec3 face_traction(const InternalFaceGeometry& g, const Vec3& uo, const Vec3& un,
                          const FaceAuxiliary& aux) {
  std::array<double, 6> s = face_nsf_stress(g, uo, un, aux);
  for (int k = 0; k < 6; ++k) s[static_cast<std::size_t>(k)] += aux.hot_sigma[static_cast<std::size_t>(k)];
  return sym_mul(s, g.S);
}

// 面の熱流量 Q = (q^NSF_f + HoT_q,f)·S、q^NSF = −κ ∇T(式 (31))
inline double face_heat_flow(const InternalFaceGeometry& g, double To, double Tn, const FaceAuxiliary& aux) {
  return dot(face_nsf_heat_flux(g, To, Tn, aux) + aux.hot_q, g.S);
}

// 内部面のフラックス。rec_o・rec_n は 2 次の移流項で使う面への再構成値(1 次なら null)。
// forced_donor: −1 なら V̇ の符号で決める、0 なら owner、1 なら neighbour(ヤコビアンの試験用)。
inline InternalFaceResult internal_face_flux(const InternalFaceGeometry& g, const FaceCellState& Wo,
                                             const FaceCellState& Wn, const FaceAuxiliary& aux,
                                             const FaceCellState* rec_o = nullptr,
                                             const FaceCellState* rec_n = nullptr, int forced_donor = -1) {
  InternalFaceResult r;
  const Vec3 uf = g.wo * Wo.u + g.wn * Wn.u;
  const double dp = (Wn.p - Wo.p) - dot(aux.gradp_bar, g.d);
  r.vdot = dot(uf, g.S) - aux.beta * dp;
  r.owner_is_donor = forced_donor < 0 ? (r.vdot >= 0.0) : (forced_donor == 0);
  const FaceCellState& Wup = r.owner_is_donor ? Wo : Wn;
  r.rho_up1 = Wup.p / Wup.T;
  r.mdot1 = r.rho_up1 * r.vdot;
  r.u_up1 = Wup.u;

  const FaceCellState* rec = r.owner_is_donor ? rec_o : rec_n;
  const FaceCellState& Wa = rec != nullptr ? *rec : Wup;
  const double rho_f = Wa.p / Wa.T;
  r.mdot = rho_f * r.vdot;
  r.u_up = Wa.u;
  const double H = 2.5 * Wa.T + 0.5 * dot(Wa.u, Wa.u);
  const double pf = g.wo * Wo.p + g.wn * Wn.p;

  const Vec3 t = face_traction(g, Wo.u, Wn.u, aux);
  const double Q = face_heat_flow(g, Wo.T, Wn.T, aux);
  const Vec3 ubar = 0.5 * (Wo.u + Wn.u);

  r.F[0] = r.mdot;
  r.F[1] = r.mdot * Wa.u.x + pf * g.S.x + t.x;
  r.F[2] = r.mdot * Wa.u.y + pf * g.S.y + t.y;
  r.F[3] = r.mdot * Wa.u.z + pf * g.S.z + t.z;
  r.F[4] = r.mdot * H + dot(t, ubar) + Q;

  const Vec3 duo = Wa.u - Wo.u;
  const Vec3 dun = Wa.u - Wn.u;
  r.defect_owner = 0.5 * r.mdot * dot(duo, duo);
  r.defect_neighbour = -0.5 * r.mdot * dot(dun, dun);
  return r;
}

// 圧力–速度のヤコビアンの、面ごとの縮約したデータ(式 (44)(47))
struct FacePuJacobianData {
  std::array<double, 4> Jrho_o{};  // ∂ṁ/∂(p_o, u_o)
  std::array<double, 4> Jrho_n{};  // ∂ṁ/∂(p_n, u_n)
  double mdot1 = 0.0;              // 1 次の ṁ
  bool owner_is_donor = true;
  Vec3 u_up1{};
  double cmu = 0.0;                // μ_f A / d_n(C_μ = cmu (I + n nᵀ/3)、式 (45))
};

inline FacePuJacobianData face_pu_jacobian_data(const InternalFaceGeometry& g, const FaceCellState& Wo,
                                                const FaceCellState& Wn, const FaceAuxiliary& aux,
                                                const InternalFaceResult& r) {
  FacePuJacobianData J;
  const double rb = r.rho_up1 * aux.beta;
  J.Jrho_o[0] = rb + (r.owner_is_donor ? r.vdot / Wo.T : 0.0);
  J.Jrho_n[0] = -rb + (r.owner_is_donor ? 0.0 : r.vdot / Wn.T);
  J.Jrho_o[1] = r.rho_up1 * g.wo * g.S.x;
  J.Jrho_o[2] = r.rho_up1 * g.wo * g.S.y;
  J.Jrho_o[3] = r.rho_up1 * g.wo * g.S.z;
  J.Jrho_n[1] = r.rho_up1 * g.wn * g.S.x;
  J.Jrho_n[2] = r.rho_up1 * g.wn * g.S.y;
  J.Jrho_n[3] = r.rho_up1 * g.wn * g.S.z;
  J.mdot1 = r.mdot1;
  J.owner_is_donor = r.owner_is_donor;
  J.u_up1 = r.u_up1;
  J.cmu = aux.mu * g.A / g.dn;
  return J;
}

// owner の行の 2 つのブロック ∂F_pu/∂W_pu,o(Joo)と ∂F_pu/∂W_pu,n(Jon)。neighbour の行は符号を反転する。
//   J_mom,s = u_up J_ρ,s + ṁ δ_{s,up} E_u + w_s S e_pᵀ + ζ_s C_μ E_u(式 (47)、ζ_o = 1、ζ_n = −1)
inline void face_pu_blocks(const InternalFaceGeometry& g, const FacePuJacobianData& J, Block4& Joo, Block4& Jon) {
  const double u[3] = {J.u_up1.x, J.u_up1.y, J.u_up1.z};
  const double S[3] = {g.S.x, g.S.y, g.S.z};
  const double nn[3] = {g.n.x, g.n.y, g.n.z};
  for (int c = 0; c < 4; ++c) {
    Joo[static_cast<std::size_t>(c)] = J.Jrho_o[static_cast<std::size_t>(c)];
    Jon[static_cast<std::size_t>(c)] = J.Jrho_n[static_cast<std::size_t>(c)];
  }
  const double m_o = J.owner_is_donor ? J.mdot1 : 0.0;
  const double m_n = J.owner_is_donor ? 0.0 : J.mdot1;
  for (int r = 0; r < 3; ++r) {
    for (int c = 0; c < 4; ++c) {
      const auto k = static_cast<std::size_t>(4 * (r + 1) + c);
      double vo = u[r] * J.Jrho_o[static_cast<std::size_t>(c)];
      double vn = u[r] * J.Jrho_n[static_cast<std::size_t>(c)];
      if (c == 0) {
        vo += g.wo * S[r];
        vn += g.wn * S[r];
      } else {
        const int a = c - 1;
        const double cm = J.cmu * ((r == a ? 1.0 : 0.0) + nn[r] * nn[a] / 3.0);
        vo += (r == a ? m_o : 0.0) + cm;
        vn += (r == a ? m_n : 0.0) - cm;
      }
      Joo[k] = vo;
      Jon[k] = vn;
    }
  }
}

// 温度のヤコビアンの面の係数(式 (54))。セル i から見た ṁ_i(外向き)と C_κ = κ_f A/d_n から、
// (∂F_T/∂T_i, ∂F_T/∂T_j) を返す。
inline std::array<double, 2> temperature_face_coefficients(double mdot_i, double ckappa) {
  if (mdot_i >= 0.0) return {mdot_i + ckappa, -ckappa};
  return {-1.5 * mdot_i + ckappa, 2.5 * mdot_i - ckappa};
}

// 保存量のフラックス F(セル i から外向き)を温度の式のフラックスに変える(式 (51)(52) の変換):
//   F_T = F_E − u_i·F_mom + (K_i − 3/2 T_i) F_ρ、K_i = |u_i|²/2(内部面ではさらに 𝒦 を引く)
inline double temperature_flux_from_conservative(const Vec5& F, const Vec3& ui, double Ti) {
  const double K = 0.5 * dot(ui, ui);
  return F[4] - (ui.x * F[1] + ui.y * F[2] + ui.z * F[3]) + (K - 1.5 * Ti) * F[0];
}

// 境界面の温度のヤコビアン(式 (55))。J は境界フラックスのヤコビアン(5×5、W_i で評価)、
// Fb は境界フラックス、(ui, Ti) はセルの速度と温度
inline double temperature_boundary_coefficient(const Mat5& J, const Vec5& Fb, const Vec3& ui, double Ti) {
  const double K = 0.5 * dot(ui, ui);
  return J(4, 4) - (ui.x * J(1, 4) + ui.y * J(2, 4) + ui.z * J(3, 4)) + (K - 1.5 * Ti) * J(0, 4) - 1.5 * Fb[0];
}

}  // namespace gsis::macro
