// macro: 境界・内部面・温度のヤコビアンを差分近似と比べる(docs/design_pbc.md §7)
//
// - 境界: 各種類の F_b(W) = Ŧ⁺(W) + C⁺ + Ŧ⁻(W, F⁺) + C⁻ の中心差分と J_b(式 (78))
// - 内部面: ドナーセルと Rhie–Chow 係数・平均勾配・輸送係数・HoT を固定した 1 次のフラックスの
//   中心差分と、式 (44)–(47) のブロック
// - 温度: 式 (52) の F_T(ṁ・ドナー・t・κ などを固定)の中心差分と式 (54)、境界は式 (55)

#include <algorithm>
#include <array>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <random>
#include <vector>

#include "macro/boundary_flux.hpp"
#include "macro/face_flux.hpp"

using namespace gsis;
using namespace gsis::macro;

namespace {

struct Rng {
  std::mt19937 gen;
  explicit Rng(unsigned seed) : gen(seed) {}
  double uniform(double lo, double hi) { return std::uniform_real_distribution<double>(lo, hi)(gen); }
  Vec3 vec(double lo, double hi) { return Vec3{uniform(lo, hi), uniform(lo, hi), uniform(lo, hi)}; }
  Vec3 unit() {
    for (;;) {
      const Vec3 v = vec(-1.0, 1.0);
      const double n = norm(v);
      if (n > 0.2 && n <= 1.0) return v / n;
    }
  }
  Vec5 state() { return make_state(uniform(0.3, 2.0), vec(-0.8, 0.8), uniform(0.5, 1.8)); }
  Vec5 vec5(double lo, double hi) {
    Vec5 v;
    for (int k = 0; k < 5; ++k) v[k] = uniform(lo, hi);
    return v;
  }
};

double max_abs(const Mat5& a) {
  double m = 0.0;
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) m = std::max(m, std::abs(a(r, c)));
  }
  return m;
}

// 中心差分の刻み
double fd_step(double x) { return 1e-6 * std::max(1.0, std::abs(x)); }

}  // namespace

TEST_CASE("boundary flux jacobians match finite differences for every boundary type", "[macro][jacobian]") {
  std::vector<MacroBoundaryCondition> bcs;
  {
    MacroBoundaryCondition b;
    b.kind = MacroBoundaryKind::DiffuseWall;
    b.temperature = 0.9;
    bcs.push_back(b);
    b.temperature = 1.2;
    b.velocity = Vec3{0.3, -0.2, 0.1};  // 移動壁(法線成分は面ごとに除く)
    bcs.push_back(b);
  }
  {
    MacroBoundaryCondition b;
    b.kind = MacroBoundaryKind::PressureInlet;
    b.pressure = 1.3;
    b.temperature = 1.1;
    bcs.push_back(b);
    b.zero_tangential = true;  // 流入の速度の接線成分を 0 にする入口(フェーズ 5a)
    bcs.push_back(b);
    b.zero_tangential = false;
    b.kind = MacroBoundaryKind::PressureOutlet;
    b.pressure = 0.4;
    bcs.push_back(b);
    b.pressure = 0.0;  // p_out = 0(真空と同じ)
    bcs.push_back(b);
  }
  {
    MacroBoundaryCondition b;
    b.kind = MacroBoundaryKind::Vacuum;
    bcs.push_back(b);
    b.kind = MacroBoundaryKind::Symmetry;
    bcs.push_back(b);
    b.kind = MacroBoundaryKind::FarField;
    b.far_field = make_state(0.8, Vec3{0.2, -0.1, 0.05}, 1.1);
    bcs.push_back(b);
  }

  Rng rng(20261016);
  const BoundaryFaceExtra extra{};
  for (const MacroBoundaryCondition& bc : bcs) {
    INFO("境界の種類: " << to_string(bc.kind) << "、T = " << bc.temperature << "、p = " << bc.pressure);
    double worst = 0.0;
    for (int trial = 0; trial < 40; ++trial) {
      const Vec5 W = rng.state();
      const Vec3 n = rng.unit();
      const double A = rng.uniform(0.1, 2.0);
      const Vec5 Cp = rng.vec5(-0.05, 0.05);
      const Vec5 Cm = rng.vec5(-0.05, 0.05);
      auto F = [&](const Vec5& w) { return boundary_flux(bc, extra, w, n, A, Cp, Cm).total(); };
      const Mat5 J = boundary_flux_jacobian(bc, extra, W, n, A);
      Mat5 Jfd;
      for (int c = 0; c < 5; ++c) {
        const double h = fd_step(W[c]);
        Vec5 Wp = W;
        Vec5 Wm = W;
        Wp[c] += h;
        Wm[c] -= h;
        const Vec5 Fp = F(Wp);
        const Vec5 Fm = F(Wm);
        for (int r = 0; r < 5; ++r) Jfd(r, c) = (Fp[r] - Fm[r]) / (2.0 * h);
      }
      double err = 0.0;
      for (int r = 0; r < 5; ++r) {
        for (int c = 0; c < 5; ++c) err = std::max(err, std::abs(J(r, c) - Jfd(r, c)));
      }
      worst = std::max(worst, err / (1.0 + max_abs(J)));
    }
    CHECK(worst < 1e-8);
  }
}

TEST_CASE("boundary fluxes satisfy the boundary conditions and the consistency correction", "[macro][boundary]") {
  Rng rng(7);
  const BoundaryFaceExtra extra{};
  for (int trial = 0; trial < 20; ++trial) {
    const Vec5 W = rng.state();
    const Vec3 n = rng.unit();
    const double A = rng.uniform(0.1, 2.0);
    const Vec5 zero = vec5_zero();

    // 静止拡散壁: 質量流束 0。整合補正を入れると質量流束は C⁻_ρ になる
    MacroBoundaryCondition wall;
    wall.kind = MacroBoundaryKind::DiffuseWall;
    wall.temperature = rng.uniform(0.6, 1.4);
    const Vec5 Fw = boundary_flux(wall, extra, W, n, A, zero, zero).total();
    CHECK(std::abs(Fw[0]) < 1e-14 * A);

    // 対称面: 質量・エネルギー・接線方向の運動量の流束は 0
    MacroBoundaryCondition sym;
    sym.kind = MacroBoundaryKind::Symmetry;
    const Vec5 Fs = boundary_flux(sym, extra, W, n, A, zero, zero).total();
    const Vec3 Fm{Fs[1], Fs[2], Fs[3]};
    CHECK(std::abs(Fs[0]) < 1e-14);
    CHECK(std::abs(Fs[4]) < 1e-14);
    CHECK(norm(Fm - dot(Fm, n) * n) < 1e-13);

    // 真空: Ŧ⁺ だけ
    MacroBoundaryCondition vac;
    vac.kind = MacroBoundaryKind::Vacuum;
    const Vec5 Fv = boundary_flux(vac, extra, W, n, A, zero, zero).total();
    const Vec5 Fo = outgoing_flux(W, n, A);
    for (int k = 0; k < 5; ++k) CHECK(Fv[k] == Fo[k]);

    // 整合補正: W = W⁰ で F_b = F⁺⁰ + F⁻⁰(運動論の値)を厳密に返す
    MacroBoundaryCondition inlet;
    inlet.kind = MacroBoundaryKind::PressureInlet;
    inlet.pressure = 1.0;
    inlet.temperature = 1.0;
    for (const MacroBoundaryCondition* bc : {&wall, &sym, &vac, &inlet}) {
      const Vec5 Fp0 = rng.vec5(-1.0, 1.0);
      const Vec5 Fm0 = rng.vec5(-1.0, 1.0);
      const ConsistencyCorrection cc = consistency_correction(*bc, extra, W, n, A, Fp0, Fm0);
      const BoundaryFaceFlux bf = boundary_flux(*bc, extra, W, n, A, cc.C_plus, cc.C_minus);
      for (int k = 0; k < 5; ++k) {
        CHECK(std::abs(bf.F_plus[k] - Fp0[k]) < 1e-14);
        CHECK(std::abs(bf.F_minus[k] - Fm0[k]) < 1e-13);
      }
    }
  }
}

TEST_CASE("half-space density moments add up to the Maxwellian moments", "[macro][boundary]") {
  Rng rng(11);
  for (int trial = 0; trial < 20; ++trial) {
    const Vec5 W = rng.state();
    const Vec3 n = rng.unit();
    const Vec5 Dp = half_space_density(+1, W, n);
    const Vec5 Dm = half_space_density(-1, W, n);
    const double rho = W[0] / W[4];
    const Vec3 u = velocity_of(W);
    CHECK(std::abs(Dp[0] + Dm[0] - rho) < 1e-14);
    CHECK(std::abs(Dp[1] + Dm[1] - rho * u.x) < 1e-14);
    CHECK(std::abs(Dp[2] + Dm[2] - rho * u.y) < 1e-14);
    CHECK(std::abs(Dp[3] + Dm[3] - rho * u.z) < 1e-14);
    CHECK(std::abs(Dp[4] + Dm[4] - rho * (1.5 * W[4] + 0.5 * dot(u, u))) < 1e-13);

    // 対称面の境界面の値: 流出側の半分とその鏡映の和。密度 2ρC₊、法線方向の速度 0、
    // 接線方向の速度はそのまま、温度 T_f = 2/3 T + I2/(3 C₊)(u_n = 0 なら T_f = T)
    MacroBoundaryCondition sym;
    sym.kind = MacroBoundaryKind::Symmetry;
    const Vec5 fs = boundary_face_state(sym, BoundaryFaceExtra{}, W, n);
    const Vec3 uf = velocity_of(fs);
    const double un = dot(u, n);
    const HalfRangeMoments<double> hm = half_range_moments(+1, un, std::sqrt(W[4]));
    CHECK(std::abs(dot(uf, n)) < 1e-13);
    CHECK(norm(uf - (u - un * n)) < 1e-13);
    CHECK(std::abs(fs[0] / fs[4] - 2.0 * rho * hm.C) < 1e-13);
    CHECK(std::abs(fs[4] - (2.0 / 3.0 * W[4] + hm.I2 / (3.0 * hm.C))) < 1e-13);
    const Vec5 at_rest = boundary_face_state(sym, BoundaryFaceExtra{}, make_state(W[0], u - un * n, W[4]), n);
    for (int k = 0; k < 5; ++k) {
      CHECK(std::abs(at_rest[k] - make_state(W[0], u - un * n, W[4])[k]) < 1e-13);
    }
  }
}

namespace {

struct FaceSetup {
  InternalFaceGeometry g;
  FaceCellState Wo, Wn;
  FaceAuxiliary aux;
};

FaceSetup random_face(Rng& rng) {
  FaceSetup s;
  s.g.n = rng.unit();
  s.g.A = rng.uniform(0.2, 1.5);
  s.g.S = s.g.A * s.g.n;
  const double dn = rng.uniform(0.3, 1.2);
  const Vec3 t = rng.vec(-0.3, 0.3);
  s.g.d = dn * s.g.n + (t - dot(t, s.g.n) * s.g.n);  // 非直交
  s.g.dn = dot(s.g.n, s.g.d);
  s.g.wn = rng.uniform(0.3, 0.7);
  s.g.wo = 1.0 - s.g.wn;
  const Vec5 a = rng.state();
  const Vec5 b = rng.state();
  s.Wo = FaceCellState{a[0], velocity_of(a), a[4]};
  s.Wn = FaceCellState{b[0], velocity_of(b), b[4]};
  s.aux.beta = rng.uniform(0.02, 0.5);
  s.aux.gradp_bar = rng.vec(-0.5, 0.5);
  for (Vec3& gu : s.aux.gradu_bar) gu = rng.vec(-0.5, 0.5);
  s.aux.gradT_bar = rng.vec(-0.5, 0.5);
  s.aux.mu = rng.uniform(0.01, 0.2);
  s.aux.kappa = rng.uniform(0.02, 0.5);
  for (double& h : s.aux.hot_sigma) h = rng.uniform(-0.05, 0.05);
  s.aux.hot_q = rng.vec(-0.05, 0.05);
  return s;
}

}  // namespace

TEST_CASE("internal face pressure-velocity blocks match finite differences of the frozen first-order flux",
          "[macro][jacobian]") {
  Rng rng(99);
  double worst = 0.0;
  for (int trial = 0; trial < 50; ++trial) {
    const FaceSetup s = random_face(rng);
    for (int donor = 0; donor < 2; ++donor) {
      const InternalFaceResult r = internal_face_flux(s.g, s.Wo, s.Wn, s.aux, nullptr, nullptr, donor);
      const FacePuJacobianData jd = face_pu_jacobian_data(s.g, s.Wo, s.Wn, s.aux, r);
      Block4 Joo;
      Block4 Jon;
      face_pu_blocks(s.g, jd, Joo, Jon);
      // x = (p_o, u_o, p_n, u_n)
      double x[8] = {s.Wo.p, s.Wo.u.x, s.Wo.u.y, s.Wo.u.z, s.Wn.p, s.Wn.u.x, s.Wn.u.y, s.Wn.u.z};
      auto flux = [&](const double* y) {
        FaceCellState a = s.Wo;
        FaceCellState b = s.Wn;
        a.p = y[0];
        a.u = Vec3{y[1], y[2], y[3]};
        b.p = y[4];
        b.u = Vec3{y[5], y[6], y[7]};
        return internal_face_flux(s.g, a, b, s.aux, nullptr, nullptr, donor).F;
      };
      double scale = 1.0;
      for (double v : Joo) scale = std::max(scale, std::abs(v));
      for (double v : Jon) scale = std::max(scale, std::abs(v));
      for (int c = 0; c < 8; ++c) {
        const double h = fd_step(x[c]);
        double xp[8];
        double xm[8];
        std::copy(x, x + 8, xp);
        std::copy(x, x + 8, xm);
        xp[c] += h;
        xm[c] -= h;
        const Vec5 Fp = flux(xp);
        const Vec5 Fm = flux(xm);
        for (int rr = 0; rr < 4; ++rr) {
          const double fd = (Fp[rr] - Fm[rr]) / (2.0 * h);
          const double an = c < 4 ? Joo[static_cast<std::size_t>(4 * rr + c)]
                                  : Jon[static_cast<std::size_t>(4 * rr + c - 4)];
          worst = std::max(worst, std::abs(fd - an) / scale);
        }
      }
    }
  }
  CHECK(worst < 1e-8);
}

TEST_CASE("temperature residual transform and temperature jacobians", "[macro][jacobian]") {
  Rng rng(1234);
  double worst_identity = 0.0;
  double worst_int = 0.0;
  for (int trial = 0; trial < 50; ++trial) {
    const FaceSetup s = random_face(rng);
    // 2 次の再構成値(風上側だけが使われる)
    const Vec5 ra = rng.state();
    const Vec5 rb = rng.state();
    const FaceCellState Ro{ra[0], velocity_of(ra), ra[4]};
    const FaceCellState Rn{rb[0], velocity_of(rb), rb[4]};
    for (int order = 1; order <= 2; ++order) {
      const InternalFaceResult r = order == 1 ? internal_face_flux(s.g, s.Wo, s.Wn, s.aux)
                                              : internal_face_flux(s.g, s.Wo, s.Wn, s.aux, &Ro, &Rn);
      const FaceCellState& up = r.owner_is_donor ? (order == 1 ? s.Wo : Ro) : (order == 1 ? s.Wn : Rn);
      const double pf = s.g.wo * s.Wo.p + s.g.wn * s.Wn.p;
      const Vec3 t = face_traction(s.g, s.Wo.u, s.Wn.u, s.aux);
      const double Q = face_heat_flow(s.g, s.Wo.T, s.Wn.T, s.aux);
      const Vec3 ubar = 0.5 * (s.Wo.u + s.Wn.u);
      // 式 (52)(owner から見る)
      const double FTo = r.mdot * (2.5 * up.T - 1.5 * s.Wo.T) - pf * dot(s.Wo.u, s.g.S) + dot(t, ubar - s.Wo.u) + Q;
      const double FTo_tr = temperature_flux_from_conservative(r.F, s.Wo.u, s.Wo.T) - r.defect_owner;
      worst_identity = std::max(worst_identity, std::abs(FTo - FTo_tr) / (1.0 + std::abs(FTo)));
      // neighbour から見る(S, ṁ, t, Q の向きが逆)
      const double FTn =
          -r.mdot * (2.5 * up.T - 1.5 * s.Wn.T) + pf * dot(s.Wn.u, s.g.S) - dot(t, ubar - s.Wn.u) - Q;
      const Vec5 Fneg = vec5_scale(r.F, -1.0);
      const double FTn_tr = temperature_flux_from_conservative(Fneg, s.Wn.u, s.Wn.T) - r.defect_neighbour;
      worst_identity = std::max(worst_identity, std::abs(FTn - FTn_tr) / (1.0 + std::abs(FTn)));
    }

    // 式 (54): ṁ、ドナー、p_f、u、t、κ_f、∇̄T、HoT を固定した F_T(T_o, T_n) の差分
    const InternalFaceResult r = internal_face_flux(s.g, s.Wo, s.Wn, s.aux);
    const double m = r.mdot1;
    const bool owner_up = r.owner_is_donor;
    const double pf = s.g.wo * s.Wo.p + s.g.wn * s.Wn.p;
    const Vec3 t = face_traction(s.g, s.Wo.u, s.Wn.u, s.aux);
    const Vec3 ubar = 0.5 * (s.Wo.u + s.Wn.u);
    auto FT_owner = [&](double To, double Tn) {
      const double Tup = owner_up ? To : Tn;
      return m * (2.5 * Tup - 1.5 * To) - pf * dot(s.Wo.u, s.g.S) + dot(t, ubar - s.Wo.u) +
             face_heat_flow(s.g, To, Tn, s.aux);
    };
    auto FT_neigh = [&](double Tn, double To) {
      const double Tup = owner_up ? To : Tn;
      return -m * (2.5 * Tup - 1.5 * Tn) + pf * dot(s.Wn.u, s.g.S) - dot(t, ubar - s.Wn.u) -
             face_heat_flow(s.g, To, Tn, s.aux);
    };
    const double ck = s.aux.kappa * s.g.A / s.g.dn;
    const std::array<double, 2> co = temperature_face_coefficients(m, ck);
    const std::array<double, 2> cn = temperature_face_coefficients(-m, ck);
    const double To = s.Wo.T;
    const double Tn = s.Wn.T;
    const double h = 1e-6;
    const double dFo_dTo = (FT_owner(To + h, Tn) - FT_owner(To - h, Tn)) / (2.0 * h);
    const double dFo_dTn = (FT_owner(To, Tn + h) - FT_owner(To, Tn - h)) / (2.0 * h);
    const double dFn_dTn = (FT_neigh(Tn + h, To) - FT_neigh(Tn - h, To)) / (2.0 * h);
    const double dFn_dTo = (FT_neigh(Tn, To + h) - FT_neigh(Tn, To - h)) / (2.0 * h);
    const double sc = 1.0 + std::abs(m) + ck;
    worst_int = std::max(worst_int, std::abs(dFo_dTo - co[0]) / sc);
    worst_int = std::max(worst_int, std::abs(dFo_dTn - co[1]) / sc);
    worst_int = std::max(worst_int, std::abs(dFn_dTn - cn[0]) / sc);
    worst_int = std::max(worst_int, std::abs(dFn_dTo - cn[1]) / sc);
  }
  CHECK(worst_identity < 1e-12);
  CHECK(worst_int < 1e-8);

  // 境界(式 (55)): F_T,b(T) = F_E − u·F_mom + (K − 3/2 T) F_ρ(p, u は固定、W_b = W_i)
  std::vector<MacroBoundaryCondition> bcs(5);
  bcs[0].kind = MacroBoundaryKind::DiffuseWall;
  bcs[0].temperature = 1.15;
  bcs[1].kind = MacroBoundaryKind::PressureInlet;
  bcs[1].pressure = 1.2;
  bcs[1].temperature = 0.95;
  bcs[2].kind = MacroBoundaryKind::PressureOutlet;
  bcs[2].pressure = 0.5;
  bcs[3].kind = MacroBoundaryKind::Vacuum;
  bcs[4].kind = MacroBoundaryKind::Symmetry;
  double worst_b = 0.0;
  for (const MacroBoundaryCondition& bc : bcs) {
    for (int trial = 0; trial < 20; ++trial) {
      const Vec5 W = rng.state();
      const Vec3 n = rng.unit();
      const double A = rng.uniform(0.2, 1.5);
      const Vec5 Cp = rng.vec5(-0.05, 0.05);
      const Vec5 Cm = rng.vec5(-0.05, 0.05);
      const Vec3 u = velocity_of(W);
      auto FTb = [&](double T) {
        Vec5 w = W;
        w[4] = T;
        const Vec5 F = boundary_flux(bc, BoundaryFaceExtra{}, w, n, A, Cp, Cm).total();
        return temperature_flux_from_conservative(F, u, T);
      };
      const double h = 1e-6;
      const double fd = (FTb(W[4] + h) - FTb(W[4] - h)) / (2.0 * h);
      const Mat5 J = boundary_flux_jacobian(bc, BoundaryFaceExtra{}, W, n, A);
      const Vec5 F = boundary_flux(bc, BoundaryFaceExtra{}, W, n, A, Cp, Cm).total();
      const double an = temperature_boundary_coefficient(J, F, u, W[4]);
      worst_b = std::max(worst_b, std::abs(fd - an) / (1.0 + std::abs(an)));
    }
  }
  CHECK(worst_b < 1e-8);
}
