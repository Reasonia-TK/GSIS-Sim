// macro: 製造解法(MMS)で PBC の空間精度を確かめる(docs/design_pbc.md §7)。
//
// 解析解 W*(x) = (p*, u*, T*) を与え、
//   - 源項 b_i = Σ_f F_NSF(W*(x_f), ∇W*(x_f))·S_f(面中心の値で積分。面積を掛けた NSF の厳密なフラックス)
//   - 境界: 遠方場型(Ŧ⁻ = A F⁻_eq(W*(x_b)))に、整合補正で厳密なフラックスを与える
//     (F⁺⁰ + F⁻⁰ = F_NSF(W*(x_b))·S_b。GSIS の境界の扱いと同じ経路を通す)。勾配の境界値 face_state も W*(x_b)
// として、離散解 W_h と W*(x_i) の差(体積重みの 2 ノルム)が細分化とともに減ることを確かめる。
//
// 結果の目安(2026-10-16、3 水準の最後の 2 つから求めた次数):
//   - 拡散が支配的(u* = 0): u・T は六面体・歪んだ六面体・四面体で約 2 次。p は六面体で約 1.8 次、
//     歪んだ六面体・四面体では約 1 次(面の圧力 p_ij = w_i p_i + w_j p_j(式 (28))に歪みの補正がないため)
//   - 移流と拡散: upwind1 は約 1 次。upwind2 は六面体で約 2 次、歪んだ六面体・四面体では 1〜1.8 次
//     (最小二乗勾配の精度とリミッタのため)。どのメッシュでも upwind2 の誤差は upwind1 より小さい

#include <array>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <vector>

#include "macro/pbc_solver.hpp"
#include "mesh/geometry.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using namespace gsis::macro;

namespace {

constexpr double kPiL = 3.14159265358979323846;

// 解析解(無次元、領域 [0,1]³)
struct Exact {
  double U = 0.2;  // 速度の大きさ(0 なら静止した気体の熱伝導と圧力場)

  double p(const Vec3& x) const {
    return 1.0 + 0.1 * std::cos(kPiL * x.x) * std::cos(kPiL * x.y) * std::cos(kPiL * x.z);
  }
  Vec3 grad_p(const Vec3& x) const {
    const double cx = std::cos(kPiL * x.x), cy = std::cos(kPiL * x.y), cz = std::cos(kPiL * x.z);
    const double sx = std::sin(kPiL * x.x), sy = std::sin(kPiL * x.y), sz = std::sin(kPiL * x.z);
    return Vec3{-0.1 * kPiL * sx * cy * cz, -0.1 * kPiL * cx * sy * cz, -0.1 * kPiL * cx * cy * sz};
  }
  double T(const Vec3& x) const {
    return 1.0 + 0.1 * std::sin(kPiL * x.x) * std::sin(kPiL * x.y) * std::cos(kPiL * x.z);
  }
  Vec3 grad_T(const Vec3& x) const {
    const double cx = std::cos(kPiL * x.x), cy = std::cos(kPiL * x.y), cz = std::cos(kPiL * x.z);
    const double sx = std::sin(kPiL * x.x), sy = std::sin(kPiL * x.y), sz = std::sin(kPiL * x.z);
    return Vec3{0.1 * kPiL * cx * sy * cz, 0.1 * kPiL * sx * cy * cz, -0.1 * kPiL * sx * sy * sz};
  }
  Vec3 u(const Vec3& x) const {
    const double cx = std::cos(kPiL * x.x), cy = std::cos(kPiL * x.y);
    const double sx = std::sin(kPiL * x.x), sy = std::sin(kPiL * x.y), sz = std::sin(kPiL * x.z);
    return Vec3{U * (1.0 + 0.5 * sy * sz), U * 0.5 * cx * sz, U * 0.5 * sx * cy};
  }
  // G[a] = ∇u_a
  std::array<Vec3, 3> grad_u(const Vec3& x) const {
    const double cx = std::cos(kPiL * x.x), cy = std::cos(kPiL * x.y), cz = std::cos(kPiL * x.z);
    const double sx = std::sin(kPiL * x.x), sy = std::sin(kPiL * x.y), sz = std::sin(kPiL * x.z);
    const double k = 0.5 * U * kPiL;
    return {Vec3{0.0, k * cy * sz, k * sy * cz}, Vec3{-k * sx * sz, 0.0, k * cx * cz},
            Vec3{k * cx * cy, -k * sx * sy, 0.0}};
  }
  Vec5 state(const Vec3& x) const { return make_state(p(x), u(x), T(x)); }

  // 面積ベクトル S を通る厳密な NSF のフラックス(面積を掛けた値)
  Vec5 flux(const Vec3& x, const Vec3& S, const MacroGas& gas) const {
    const double pp = p(x);
    const double TT = T(x);
    const double rho = pp / TT;
    const Vec3 uu = u(x);
    const std::array<Vec3, 3> G = grad_u(x);
    const double mu = gas.mu(TT);
    const double kappa = gas.kappa(TT);
    const double div = G[0].x + G[1].y + G[2].z;
    // σ_ab = −μ (∂u_a/∂x_b + ∂u_b/∂x_a − 2/3 div δ_ab)
    double sig[3][3];
    for (int a = 0; a < 3; ++a) {
      for (int b = 0; b < 3; ++b) {
        sig[a][b] = -mu * (G[static_cast<std::size_t>(a)][b] + G[static_cast<std::size_t>(b)][a] -
                           (a == b ? 2.0 / 3.0 * div : 0.0));
      }
    }
    const double vs = dot(uu, S);
    Vec3 t;
    for (int a = 0; a < 3; ++a) t[a] = sig[a][0] * S.x + sig[a][1] * S.y + sig[a][2] * S.z;
    const Vec3 q = -kappa * grad_T(x);
    const double E = 1.5 * TT + 0.5 * dot(uu, uu);
    Vec5 F;
    F[0] = rho * vs;
    F[1] = rho * uu.x * vs + pp * S.x + t.x;
    F[2] = rho * uu.y * vs + pp * S.y + t.y;
    F[3] = rho * uu.z * vs + pp * S.z + t.z;
    F[4] = (rho * E + pp) * vs + dot(t, uu) + dot(q, S);
    return F;
  }
};

struct MmsResult {
  double h = 0.0;
  double err_p = 0.0;
  double err_u = 0.0;
  double err_T = 0.0;
  int iterations = 0;
  bool converged = false;
};

MmsResult run_mms(const PolyMesh& mesh, const Exact& ex, const MacroGas& gas, const std::string& advection) {
  const MeshGeometry g0 = compute_geometry(mesh);
  std::vector<MacroBoundaryCondition> bcs(mesh.patches.size());
  for (MacroBoundaryCondition& b : bcs) b.kind = MacroBoundaryKind::FarField;
  MacroParams params;
  params.advection = advection;
  params.linear_rtol = 1e-8;
  params.linear_max_iterations = 500;
  PbcSolver solver(mesh, g0, 1.0, gas, bcs, params);
  const MeshGeometry& g = solver.geometry();
  const Index nif = mesh.n_internal_faces();
  const Index nbf = mesh.n_boundary_faces();

  // 遠方場の面ごとの状態
  for (std::size_t p = 0; p < mesh.patches.size(); ++p) {
    const Patch& patch = mesh.patches[p];
    std::vector<Vec5> st;
    for (Index f = patch.start; f < patch.end(); ++f) st.push_back(ex.state(g.face_center[static_cast<std::size_t>(f)]));
    solver.set_far_field_states(static_cast<int>(p), std::move(st));
  }
  // 境界の「運動論の」フラックス: F⁻⁰ = A F⁻_eq(W*_b)、F⁺⁰ = F_exact − F⁻⁰
  BoundaryKineticFluxes bkf;
  bkf.F_plus.resize(static_cast<std::size_t>(nbf));
  bkf.F_minus.resize(static_cast<std::size_t>(nbf));
  bkf.face_state.resize(static_cast<std::size_t>(nbf));
  for (Index b = 0; b < nbf; ++b) {
    const auto f = static_cast<std::size_t>(b + nif);
    const Vec3& xf = g.face_center[f];
    const Vec5 Wb = ex.state(xf);
    const Vec5 Fex = ex.flux(xf, g.face_area_vector[f], gas);
    const Vec5 Fm = half_flux(-1, Wb, g.face_normal[f], g.face_area[f]);
    for (int k = 0; k < 5; ++k) {
      bkf.F_minus[static_cast<std::size_t>(b)][static_cast<std::size_t>(k)] = Fm[k];
      bkf.F_plus[static_cast<std::size_t>(b)][static_cast<std::size_t>(k)] = Fex[k] - Fm[k];
    }
    bkf.face_state[static_cast<std::size_t>(b)] = {Wb[0] / Wb[4], Wb[1], Wb[2], Wb[3], Wb[4]};
  }
  // 源項 b_i = Σ_f F_exact(x_f)·S_f(セルから外向き)
  std::vector<std::array<double, 5>> source(static_cast<std::size_t>(mesh.n_cells), std::array<double, 5>{});
  for (Index f = 0; f < mesh.n_faces(); ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const Vec5 F = ex.flux(g.face_center[fi], g.face_area_vector[fi], gas);
    for (std::size_t k = 0; k < 5; ++k) {
      source[static_cast<std::size_t>(mesh.owner[fi])][k] += F[static_cast<int>(k)];
      if (f < nif) source[static_cast<std::size_t>(mesh.neighbour[fi])][k] -= F[static_cast<int>(k)];
    }
  }
  CellMacroState W0;
  W0.resize(static_cast<std::size_t>(mesh.n_cells));
  for (std::size_t c = 0; c < W0.size(); ++c) {
    const Vec5 w = ex.state(g.cell_center[c]);
    W0.p[c] = w[0];
    W0.ux[c] = w[1];
    W0.uy[c] = w[2];
    W0.uz[c] = w[3];
    W0.T[c] = w[4];
    W0.rho[c] = w[0] / w[4];
  }
  PbcStageInput in;
  in.initial = &W0;
  in.boundary = &bkf;
  in.source = &source;
  PbcStageOptions opt;
  opt.max_iterations = 300;
  opt.cfl = 500.0;
  opt.tolerance = 1e-11;
  CellMacroState Wh;
  const PbcStageReport rep = solver.run_stage(in, opt, Wh);

  MmsResult r;
  r.iterations = rep.iterations;
  r.converged = rep.converged;
  double V = 0.0, ep = 0.0, eu = 0.0, eT = 0.0;
  for (std::size_t c = 0; c < Wh.size(); ++c) {
    const double v = g.cell_volume[c];
    const Vec5 w = ex.state(g.cell_center[c]);
    V += v;
    ep += v * (Wh.p[c] - w[0]) * (Wh.p[c] - w[0]);
    const Vec3 du{Wh.ux[c] - w[1], Wh.uy[c] - w[2], Wh.uz[c] - w[3]};
    eu += v * dot(du, du);
    eT += v * (Wh.T[c] - w[4]) * (Wh.T[c] - w[4]);
  }
  r.err_p = std::sqrt(ep / V);
  r.err_u = std::sqrt(eu / V);
  r.err_T = std::sqrt(eT / V);
  r.h = std::cbrt(V / static_cast<double>(Wh.size()));
  return r;
}

PolyMesh unit_box(int n, double distortion, bool tets) {
  test::BoxMeshOptions o;
  o.n = {n, n, n};
  o.lo = {0.0, 0.0, 0.0};
  o.hi = {1.0, 1.0, 1.0};
  o.distortion = distortion;
  o.tetrahedra = tets;
  o.seed = 7;
  o.patch_names = {"boundary", "boundary", "boundary", "boundary", "boundary", "boundary"};
  return test::make_box_mesh(o);
}

double order(const MmsResult& coarse, const MmsResult& fine, double MmsResult::*e) {
  return std::log((coarse.*e) / (fine.*e)) / std::log(coarse.h / fine.h);
}

void report(const char* label, const std::vector<MmsResult>& rs) {
  for (std::size_t i = 0; i < rs.size(); ++i) {
    std::printf("%-28s h=%.4f  e_p=%.3e e_u=%.3e e_T=%.3e  (反復 %d%s)", label, rs[i].h, rs[i].err_p, rs[i].err_u,
                rs[i].err_T, rs[i].iterations, rs[i].converged ? "" : "、未収束");
    if (i > 0) {
      std::printf("  次数 p %.2f u %.2f T %.2f", order(rs[i - 1], rs[i], &MmsResult::err_p),
                  order(rs[i - 1], rs[i], &MmsResult::err_u), order(rs[i - 1], rs[i], &MmsResult::err_T));
    }
    std::printf("\n");
  }
}

MacroGas mms_gas(double mu) {
  MacroGas gas;
  gas.mu_ref = mu;
  gas.T_ref = 1.0;
  gas.omega = 0.66;
  return gas;
}

}  // namespace

TEST_CASE("MMS: diffusion-dominated field converges at second order", "[macro][mms]") {
  // 静止した気体(u* = 0): 圧力場と熱伝導だけ。移流項の 1 次の誤差が入らない
  Exact ex;
  ex.U = 0.0;
  const MacroGas gas = mms_gas(0.05);
  struct Family {
    const char* name;
    double distortion;
    bool tets;
    std::vector<int> n;
  };
  const std::vector<Family> fams = {{"六面体", 0.0, false, {6, 12, 24}},
                                    {"歪んだ六面体(0.2)", 0.2, false, {6, 12, 24}},
                                    {"四面体", 0.0, true, {4, 8, 16}}};
  for (const Family& fam : fams) {
    std::vector<MmsResult> rs;
    for (int n : fam.n) rs.push_back(run_mms(unit_box(n, fam.distortion, fam.tets), ex, gas, "upwind1"));
    report(fam.name, rs);
    for (const MmsResult& r : rs) CHECK(r.converged);
    const double ord_T = order(rs[rs.size() - 2], rs.back(), &MmsResult::err_T);
    const double ord_u = order(rs[rs.size() - 2], rs.back(), &MmsResult::err_u);
    const double ord_p = order(rs[rs.size() - 2], rs.back(), &MmsResult::err_p);
    CHECK(ord_T > 1.7);
    CHECK(ord_u > 1.8);
    const bool orthogonal = fam.distortion == 0.0 && !fam.tets;
    CHECK(ord_p > (orthogonal ? 1.6 : 0.9));
  }
}

TEST_CASE("MMS: advection-diffusion converges at first order (upwind1) and better with upwind2",
          "[macro][mms]") {
  Exact ex;
  ex.U = 0.2;
  const MacroGas gas = mms_gas(0.02);
  struct Family {
    const char* name;
    double distortion;
    bool tets;
    std::vector<int> n;
  };
  const std::vector<Family> fams = {{"六面体", 0.0, false, {6, 12, 24}},
                                    {"歪んだ六面体(0.2)", 0.2, false, {6, 12, 24}},
                                    {"四面体", 0.0, true, {4, 8, 16}}};
  for (const Family& fam : fams) {
    std::vector<MmsResult> finest;
    for (const char* adv : {"upwind1", "upwind2"}) {
      std::vector<MmsResult> rs;
      for (int n : fam.n) rs.push_back(run_mms(unit_box(n, fam.distortion, fam.tets), ex, gas, adv));
      const std::string label = std::string(fam.name) + " " + adv;
      report(label.c_str(), rs);
      for (const MmsResult& r : rs) CHECK(r.converged);
      const double ord_u = order(rs[rs.size() - 2], rs.back(), &MmsResult::err_u);
      const double ord_T = order(rs[rs.size() - 2], rs.back(), &MmsResult::err_T);
      const bool orthogonal = fam.distortion == 0.0 && !fam.tets;
      if (std::string(adv) == "upwind1") {
        CHECK(ord_u > 0.75);
        CHECK(ord_T > 0.75);
      } else if (orthogonal) {
        CHECK(ord_u > 1.8);
        CHECK(ord_T > 1.8);
      } else {
        CHECK(ord_u > 0.85);
        CHECK(ord_T > 1.1);
      }
      finest.push_back(rs.back());
    }
    // 最も細かいメッシュで upwind2 の誤差は upwind1 より小さい
    CHECK(finest[1].err_u < finest[0].err_u);
    CHECK(finest[1].err_T < finest[0].err_T);
  }
}
