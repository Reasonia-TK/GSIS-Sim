#pragma once
// PBC の境界面フラックス(論文 §3.3.2 の式 (33)–(38)、付録 B)とヤコビアン(付録 C の式 (78)–(85))。
//
// すべて無次元(気体定数 R = 1、p = ρT)。状態は W = (p, ux, uy, uz, T)、フラックスは
// [質量, 運動量 x, y, z, エネルギー](全体座標、面積を掛けた値)。n は流体領域から外向きの単位法線。
//
//   Ŧ⁺(W_b)        = A F⁺_eq(W_b; n)                                     (34)
//   F⁺             = Ŧ⁺(W_b) + C⁺                                         (36)
//   F⁻             = Ŧ⁻(W_b, F⁺) + C⁻                                     (37)
//   F_b            = F⁺ + F⁻                                              (38)
//   C⁺ = F⁺⁰ − Ŧ⁺(W⁰_b)、C⁻ = F⁻⁰ − Ŧ⁻(W⁰_b, F⁺⁰)(PBC 段の初めに固定する整合補正)
//
// 境界の種類ごとの Ŧ⁻:
//   拡散壁(壁温 T_w、壁速度 u_w。j_Γ = 0)  A ρ̃ F⁻_eq(W⁽¹⁾_Γ)、ρ̃ = F⁺_ρ / (A sqrt(T_w/2π))      (75)
//                                            W⁽¹⁾_Γ = (T_w, u_w の接線成分, T_w)(単位密度)
//   圧力入口・出口                          A F⁻_eq(W_res)、W_res は式 (64)(65)                    (76)
//   真空                                    0(p_out = 0 の圧力出口と同じ。仮定 A6)
//   対称面                                  M F⁺(流出フラックスの鏡映。質量・エネルギーは符号反転、
//                                            運動量は法線成分だけ保つ: M_mom = 2 n nᵀ − I)
//   遠方場                                  A F⁻_eq(W_∞)(固定、式 (77)。製造解法の試験にも使う)
//   並進周期                                流束の和から除く(この関数群は使わない)
//
// ヤコビアン J_b = (I + ∂Ŧ⁻/∂F⁺) ∂Ŧ⁺/∂W + ∂Ŧ⁻/∂W(式 (78)、W_b = W_i で評価、整合補正は固定)。

#include <cmath>

#include "core/math.hpp"
#include "core/vec3.hpp"
#include "macro/small_matrix.hpp"
#include "physics/half_space_flux.hpp"

namespace gsis::macro {

inline constexpr double kGamma = 5.0 / 3.0;  // 単原子の比熱比

enum class MacroBoundaryKind {
  DiffuseWall,     // 完全拡散壁(壁速度は任意)
  PressureInlet,   // 圧力入口 (p_in, T_in)
  PressureOutlet,  // 圧力出口 p_out
  Vacuum,          // 真空
  Symmetry,        // 対称面(鏡映)
  PeriodicSlab,    // 並進周期(1 セル厚の方向の対面。流束の和から除く)
  FarField,        // 遠方場(状態を固定。製造解法の試験用の境界にも使う)
};

inline const char* to_string(MacroBoundaryKind kind) {
  switch (kind) {
    case MacroBoundaryKind::DiffuseWall:
      return "diffuse_wall";
    case MacroBoundaryKind::PressureInlet:
      return "pressure_inlet";
    case MacroBoundaryKind::PressureOutlet:
      return "pressure_outlet";
    case MacroBoundaryKind::Vacuum:
      return "vacuum";
    case MacroBoundaryKind::Symmetry:
      return "symmetry";
    case MacroBoundaryKind::PeriodicSlab:
      return "periodic_slab";
    case MacroBoundaryKind::FarField:
      return "far_field";
  }
  return "?";
}

// 1 つのパッチの境界条件(無次元)
struct MacroBoundaryCondition {
  MacroBoundaryKind kind = MacroBoundaryKind::DiffuseWall;
  double accommodation = 1.0;
  double target_mass_flow = 0.0; // 流入正。0なら圧力を固定
  double temperature = 1.0;  // 拡散壁の T_w、圧力入口の T_in
  double pressure = 0.0;     // 圧力入口の p_in、圧力出口の p_out
  Vec3 velocity{};           // 拡散壁の壁速度(面ごとに法線成分を除いて使う)
  Vec5 far_field{{1.0, 0.0, 0.0, 0.0, 1.0}};  // 遠方場の状態 (p, u, T)(面ごとの値がないとき)
  bool zero_tangential = false;  // 圧力入口の貯気槽の速度の接線成分を 0 にする(BoundarySpec::inlet_zero_tangential)
};

// 面に固有の境界の付帯データ(遠方場の面ごとの状態など)
struct BoundaryFaceExtra {
  const Vec5* far_field_state = nullptr;  // null なら MacroBoundaryCondition::far_field
};

// ---------------------------------------------------------------------------
// 共通の部品
// ---------------------------------------------------------------------------

// A F^±_eq(W; n)
inline Vec5 half_flux(int side, const Vec5& W, const Vec3& n, double A) {
  return vec5_scale(half_space_flux(side, W[0], velocity_of(W), W[4], 1.0, n), A);
}

// A J^±_eq(W; n)
inline Mat5 half_flux_jacobian(int side, const Vec5& W, const Vec3& n, double A) {
  return mat5_scale(half_space_flux_jacobian(side, W[0], velocity_of(W), W[4], 1.0, n), A);
}

// 単位密度の壁の Maxwell 分布 W⁽¹⁾_Γ = (T_w, u_w,t, T_w)
inline Vec5 wall_unit_state(const MacroBoundaryCondition& bc, const Vec3& n) {
  const Vec3 ut = bc.velocity - dot(bc.velocity, n) * n;
  return make_state(bc.temperature, ut, bc.temperature);
}

// 圧力境界の貯気槽の状態(式 (64)(65))と Δu
struct ReservoirState {
  Vec5 W;
  double du = 0.0;
};

inline ReservoirState pressure_reservoir(const MacroBoundaryCondition& bc, const Vec5& Wb, const Vec3& n) {
  const double p = Wb[0];
  const double T = Wb[4];
  const double rho = p / T;
  const double cs = std::sqrt(kGamma * T);
  ReservoirState r;
  r.du = (p - bc.pressure) / (rho * cs);
  const Vec3 ub = velocity_of(Wb);
  const Vec3 u = bc.zero_tangential && bc.kind == MacroBoundaryKind::PressureInlet ? (dot(ub, n) + r.du) * n
                                                                                    : ub + r.du * n;
  const double Tres = bc.kind == MacroBoundaryKind::PressureInlet ? bc.temperature : T;
  r.W = make_state(bc.pressure, u, Tres);
  return r;
}

// 鏡映 M F(質量・エネルギーは符号反転、運動量は M_mom = 2 n nᵀ − I)
inline Vec5 mirror_flux(const Vec5& F, const Vec3& n) {
  const double mn = F[1] * n.x + F[2] * n.y + F[3] * n.z;
  Vec5 G;
  G[0] = -F[0];
  G[1] = 2.0 * mn * n.x - F[1];
  G[2] = 2.0 * mn * n.y - F[2];
  G[3] = 2.0 * mn * n.z - F[3];
  G[4] = -F[4];
  return G;
}

inline const Vec5& far_field_state(const MacroBoundaryCondition& bc, const BoundaryFaceExtra& extra) {
  return extra.far_field_state != nullptr ? *extra.far_field_state : bc.far_field;
}

// ---------------------------------------------------------------------------
// フラックス
// ---------------------------------------------------------------------------

// Ŧ⁺(W_b) = A F⁺_eq(W_b; n)(式 (34))
inline Vec5 outgoing_flux(const Vec5& Wb, const Vec3& n, double A) { return half_flux(+1, Wb, n, A); }

// Ŧ⁻(W_b, F⁺)(付録 B)。F_plus は整合補正を含む流出フラックス
inline Vec5 incoming_flux(const MacroBoundaryCondition& bc, const BoundaryFaceExtra& extra, const Vec5& Wb,
                          const Vec3& n, double A, const Vec5& F_plus) {
  switch (bc.kind) {
    case MacroBoundaryKind::DiffuseWall: {
      // 式 (75)、j_Γ = 0
      const double Tw = bc.temperature;
      const double rho_t = F_plus[0] / (A * std::sqrt(Tw / (2.0 * kPi)));
      const Vec5 diffuse = vec5_scale(half_flux(-1, wall_unit_state(bc, n), n, A), rho_t);
      if (bc.accommodation == 1.0) return diffuse;
      return vec5_add(vec5_scale(diffuse,bc.accommodation),vec5_scale(mirror_flux(F_plus,n),1.0-bc.accommodation));
    }
    case MacroBoundaryKind::PressureInlet:
    case MacroBoundaryKind::PressureOutlet: {
      if (bc.pressure <= 0.0) return vec5_zero();  // p_out = 0 は真空と同じ(ρ_res = 0)
      return half_flux(-1, pressure_reservoir(bc, Wb, n).W, n, A);
    }
    case MacroBoundaryKind::Symmetry:
      return mirror_flux(F_plus, n);
    case MacroBoundaryKind::FarField:
      return half_flux(-1, far_field_state(bc, extra), n, A);
    case MacroBoundaryKind::Vacuum:
    case MacroBoundaryKind::PeriodicSlab:
      break;
  }
  return vec5_zero();
}

struct BoundaryFaceFlux {
  Vec5 F_plus;   // F⁺(式 (36))
  Vec5 F_minus;  // F⁻(式 (37))
  Vec5 total() const { return vec5_add(F_plus, F_minus); }
};

// 境界面のフラックス(式 (36)–(38))。Wb は内部側から境界面へ再構成した状態
inline BoundaryFaceFlux boundary_flux(const MacroBoundaryCondition& bc, const BoundaryFaceExtra& extra,
                                      const Vec5& Wb, const Vec3& n, double A, const Vec5& C_plus,
                                      const Vec5& C_minus) {
  BoundaryFaceFlux f;
  f.F_plus = vec5_add(outgoing_flux(Wb, n, A), C_plus);
  f.F_minus = vec5_add(incoming_flux(bc, extra, Wb, n, A, f.F_plus), C_minus);
  return f;
}

// 整合補正 C⁺ = F⁺⁰ − Ŧ⁺(W⁰_b)、C⁻ = F⁻⁰ − Ŧ⁻(W⁰_b, F⁺⁰)
struct ConsistencyCorrection {
  Vec5 C_plus;
  Vec5 C_minus;
};

inline ConsistencyCorrection consistency_correction(const MacroBoundaryCondition& bc,
                                                    const BoundaryFaceExtra& extra, const Vec5& W0b,
                                                    const Vec3& n, double A, const Vec5& F_plus0,
                                                    const Vec5& F_minus0) {
  ConsistencyCorrection c;
  c.C_plus = vec5_sub(F_plus0, outgoing_flux(W0b, n, A));
  c.C_minus = vec5_sub(F_minus0, incoming_flux(bc, extra, W0b, n, A, F_plus0));
  return c;
}

// ---------------------------------------------------------------------------
// ヤコビアン(付録 C)
// ---------------------------------------------------------------------------

// J_b = ∂F_b/∂W(5×5、W = W_i で評価)。式 (78)、(81)、(84)、(85)、対称面は (I + M) A J⁺_eq
inline Mat5 boundary_flux_jacobian(const MacroBoundaryCondition& bc, const BoundaryFaceExtra& extra,
                                   const Vec5& Wi, const Vec3& n, double A) {
  (void)extra;
  const Mat5 Jp = half_flux_jacobian(+1, Wi, n, A);  // ∂Ŧ⁺/∂W
  switch (bc.kind) {
    case MacroBoundaryKind::DiffuseWall: {
      // (I + sqrt(2π/T_w) F⁻_eq(W⁽¹⁾) e_ρᵀ) A J⁺_eq(式 (81))
      const double Tw = bc.temperature;
      const Vec5 Fm1 = half_space_flux(-1, Tw, velocity_of(wall_unit_state(bc, n)), Tw, 1.0, n);
      const double s = std::sqrt(2.0 * kPi / Tw);
      Mat5 J = Jp;
      for (int r = 0; r < 5; ++r) {
        for (int c = 0; c < 5; ++c) J(r, c) += s * Fm1[r] * Jp(0, c);
      }
      if (bc.accommodation < 1.0) {
        const double nn[3] = {n.x,n.y,n.z};
        for (int c = 0; c < 5; ++c) {
          const double proj = nn[0]*Jp(1,c)+nn[1]*Jp(2,c)+nn[2]*Jp(3,c);
          for (int r = 0; r < 5; ++r) {
            const double spec = r > 0 && r < 4 ? 2.0*nn[r-1]*proj : 0.0;
            J(r,c) = bc.accommodation*J(r,c)+(1.0-bc.accommodation)*spec;
          }
        }
      }
      return J;
    }
    case MacroBoundaryKind::PressureInlet:
    case MacroBoundaryKind::PressureOutlet: {
      if (bc.pressure <= 0.0) return Jp;  // p_out = 0: Ŧ⁻ = 0
      // A J⁺_eq(W_i) + A J⁻_eq(W_res) J_res(式 (82)–(84))
      const ReservoirState res = pressure_reservoir(bc, Wi, n);
      const double p = Wi[0];
      const double T = Wi[4];
      const double rho = p / T;
      const double cs = std::sqrt(kGamma * T);
      const double ddu_dp = 1.0 / (rho * cs) - res.du / p;
      const double ddu_dT = res.du / (2.0 * T);
      Mat5 Jres = mat5_zero();
      const double nn[3] = {n.x, n.y, n.z};
      const bool normal_only = bc.zero_tangential && bc.kind == MacroBoundaryKind::PressureInlet;
      for (int a = 0; a < 3; ++a) {
        Jres(1 + a, 0) = nn[a] * ddu_dp;
        if (normal_only) {
          for (int b = 0; b < 3; ++b) Jres(1 + a, 1 + b) = nn[a] * nn[b];  // u_res = (u·n + Δu) n
        } else {
          Jres(1 + a, 1 + a) = 1.0;
        }
        Jres(1 + a, 4) = nn[a] * ddu_dT;
      }
      if (bc.kind == MacroBoundaryKind::PressureOutlet) Jres(4, 4) = 1.0;
      const Mat5 Jm = half_flux_jacobian(-1, res.W, n, A);
      return mat5_add(Jp, mat5_mul(Jm, Jres));
    }
    case MacroBoundaryKind::Symmetry: {
      // (I + M) Jp: 質量・エネルギーの行は 0、運動量の行は 2 n (nᵀ Jp_mom)
      Mat5 J = mat5_zero();
      const double nn[3] = {n.x, n.y, n.z};
      for (int c = 0; c < 5; ++c) {
        const double proj = nn[0] * Jp(1, c) + nn[1] * Jp(2, c) + nn[2] * Jp(3, c);
        for (int a = 0; a < 3; ++a) J(1 + a, c) = 2.0 * nn[a] * proj;
      }
      return J;
    }
    case MacroBoundaryKind::FarField:
    case MacroBoundaryKind::Vacuum:
      return Jp;
    case MacroBoundaryKind::PeriodicSlab:
      break;
  }
  return mat5_zero();
}

// ---------------------------------------------------------------------------
// 境界面の分布のモーメント(単独モードで、境界セルの最小二乗勾配に使う境界面の値。仮定 A7 のマクロ版)
// ---------------------------------------------------------------------------

// Maxwell 分布 f^eq(W) の半空間 Ξ^± 部分の密度モーメント [ρ, ρu (3), ρE]
//   ρ C、ρ (I1 n + C u_t)、½ ρ [I2 + (|u_t|² + 2a²) C]
inline Vec5 half_space_density(int side, const Vec5& W, const Vec3& n) {
  const double T = W[4];
  const double a = std::sqrt(T);
  const double rho = W[0] / T;
  const Vec3 u = velocity_of(W);
  const double un = dot(u, n);
  const Vec3 ut = u - un * n;
  const HalfRangeMoments<double> m = half_range_moments(side, un, a);
  Vec5 D;
  D[0] = rho * m.C;
  D[1] = rho * (m.I1 * n.x + m.C * ut.x);
  D[2] = rho * (m.I1 * n.y + m.C * ut.y);
  D[3] = rho * (m.I1 * n.z + m.C * ut.z);
  D[4] = 0.5 * rho * (m.I2 + (dot(ut, ut) + 2.0 * T) * m.C);
  return D;
}

// 境界面の分布(流出側は W_b の Maxwell 分布、流入側は境界条件の分布)のモーメントを (p, u, T) で返す。
// 運動論側の BoundaryKineticFluxes::face_state(境界面の分布 f_b のモーメント)に対応する。
inline Vec5 boundary_face_state(const MacroBoundaryCondition& bc, const BoundaryFaceExtra& extra, const Vec5& Wb,
                                const Vec3& n) {
  const Vec5 Dp = half_space_density(+1, Wb, n);
  Vec5 Dm = vec5_zero();
  switch (bc.kind) {
    case MacroBoundaryKind::DiffuseWall: {
      // 流出する質量流束 ρ_b I1⁺ と釣り合う壁の分布の密度 ρ̃
      const Vec5 Fp = half_space_flux(+1, Wb[0], velocity_of(Wb), Wb[4], 1.0, n);
      const double rho_t = Fp[0] / std::sqrt(bc.temperature / (2.0 * kPi));
      Dm = vec5_scale(half_space_density(-1, wall_unit_state(bc, n), n), rho_t);
      if (bc.accommodation < 1.0) {
        Vec5 reflected = Dp;
        const double mn = Dp[1]*n.x+Dp[2]*n.y+Dp[3]*n.z;
        for (int a = 0; a < 3; ++a) reflected[1+a] -= 2.0*mn*n[a];
        Dm = vec5_add(vec5_scale(Dm,bc.accommodation),vec5_scale(reflected,1.0-bc.accommodation));
      }
      break;
    }
    case MacroBoundaryKind::PressureInlet:
    case MacroBoundaryKind::PressureOutlet:
      if (bc.pressure > 0.0) Dm = half_space_density(-1, pressure_reservoir(bc, Wb, n).W, n);
      break;
    case MacroBoundaryKind::Symmetry: {
      const double mn = Dp[1] * n.x + Dp[2] * n.y + Dp[3] * n.z;
      Dm = Dp;
      Dm[1] -= 2.0 * mn * n.x;
      Dm[2] -= 2.0 * mn * n.y;
      Dm[3] -= 2.0 * mn * n.z;
      break;
    }
    case MacroBoundaryKind::FarField:
      Dm = half_space_density(-1, far_field_state(bc, extra), n);
      break;
    case MacroBoundaryKind::Vacuum:
    case MacroBoundaryKind::PeriodicSlab:
      break;
  }
  const Vec5 D = vec5_add(Dp, Dm);
  const double rho = D[0];
  const Vec3 u{D[1] / rho, D[2] / rho, D[3] / rho};
  const double T = (2.0 / 3.0) * (D[4] / rho - 0.5 * dot(u, u));
  return make_state(rho * T, u, T);
}

}  // namespace gsis::macro
