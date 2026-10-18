// physics: 半空間 Maxwell 流束の閉形式(付録 B.1)とヤコビアン(付録 C.1)

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <vector>

#include "physics/half_space_flux.hpp"
#include "physics/maxwell.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using Catch::Approx;

namespace {

constexpr double kTwoPi = 6.28318530717958647693;

struct State {
  double p;
  Vec3 u;
  double T;
};

// 流束の成分ごとの尺度(ρ a^k (1 + |u|/a)^k、k = 1, 2, 2, 2, 3)
std::array<double, 5> flux_scales(const State& s, double R) {
  const double a = std::sqrt(R * s.T);
  const double rho = s.p / (R * s.T);
  const double v = a + norm(s.u);
  return {rho * v, rho * v * v, rho * v * v, rho * v * v, rho * v * v * v};
}

// 論文の式 (68)–(74)・(79)(80) を、局所座標と行列 P でそのまま書いた参照実装
struct Frame {
  Vec3 n, t1, t2;
};
Frame make_frame(const Vec3& n) {
  const Vec3 e = std::abs(n.x) < 0.9 ? Vec3{1.0, 0.0, 0.0} : Vec3{0.0, 1.0, 0.0};
  const Vec3 t1 = normalized(cross(n, e));
  return {n, t1, cross(n, t1)};
}

struct PaperLocal {
  double rho, a, un, ut1, ut2, C, G, I1, I2, I3;
};
PaperLocal paper_local(int side, const State& s, double R, const Frame& fr) {
  PaperLocal L;
  L.rho = s.p / (R * s.T);
  L.a = std::sqrt(R * s.T);
  L.un = dot(s.u, fr.n);
  L.ut1 = dot(s.u, fr.t1);
  L.ut2 = dot(s.u, fr.t2);
  const double sg = side > 0 ? 1.0 : -1.0;
  const double eta = L.un / (std::sqrt(2.0) * L.a);
  L.C = 0.5 * (1.0 + sg * std::erf(eta));  // C_± = ½[1 ± erf η](式 (71))
  L.G = std::exp(-eta * eta) / std::sqrt(kTwoPi);
  const double a = L.a, un = L.un;
  L.I1 = un * L.C + sg * a * L.G;  // 式 (72)
  L.I2 = (un * un + a * a) * L.C + sg * un * a * L.G;
  L.I3 = (un * un * un + 3.0 * un * a * a) * L.C + sg * a * (un * un + 2.0 * a * a) * L.G;
  return L;
}

// P(式 (73))の 5×5
Mat5 matrix_P(const Frame& fr) {
  Mat5 P{};
  P(0, 0) = 1.0;
  P(4, 4) = 1.0;
  for (int r = 0; r < 3; ++r) {
    P(r + 1, 1) = fr.n[r];
    P(r + 1, 2) = fr.t1[r];
    P(r + 1, 3) = fr.t2[r];
  }
  return P;
}

Vec5 paper_flux(int side, const State& s, double R, const Vec3& n) {
  const Frame fr = make_frame(n);
  const PaperLocal L = paper_local(side, s, R, fr);
  const double Floc[5] = {L.rho * L.I1, L.rho * L.I2, L.rho * L.ut1 * L.I1, L.rho * L.ut2 * L.I1,
                          0.5 * L.rho * (L.I3 + (L.ut1 * L.ut1 + L.ut2 * L.ut2 + 2.0 * L.a * L.a) * L.I1)};
  const Mat5 P = matrix_P(fr);
  Vec5 F{};
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) F[r] += P(r, c) * Floc[c];  // 式 (74)
  }
  return F;
}

Mat5 paper_jacobian(int side, const State& s, double R, const Vec3& n) {
  const Frame fr = make_frame(n);
  const PaperLocal L = paper_local(side, s, R, fr);
  const double sg = side > 0 ? 1.0 : -1.0;
  const double a2 = L.a * L.a;
  const double e2 = L.ut1 * L.ut1 + L.ut2 * L.ut2 + 2.0 * a2;
  const double I1T = sg * L.a * L.G / (2.0 * s.T);
  const double h[5] = {L.I1, L.I2, L.ut1 * L.I1, L.ut2 * L.I1, 0.5 * (L.I3 + e2 * L.I1)};
  const double h_un[5] = {L.C, 2.0 * L.I1, L.ut1 * L.C, L.ut2 * L.C, 0.5 * (3.0 * L.I2 + e2 * L.C)};
  const double h_ut1[5] = {0.0, 0.0, L.I1, 0.0, L.ut1 * L.I1};
  const double h_ut2[5] = {0.0, 0.0, 0.0, L.I1, L.ut2 * L.I1};
  const double h_T[5] = {I1T, R * L.C, L.ut1 * I1T, L.ut2 * I1T, 0.5 * (5.0 * R * L.I1 + e2 * I1T)};
  Mat5 M{};
  for (int r = 0; r < 5; ++r) {
    M(r, 0) = L.rho * h[r] / s.p;
    M(r, 1) = L.rho * h_un[r];
    M(r, 2) = L.rho * h_ut1[r];
    M(r, 3) = L.rho * h_ut2[r];
    M(r, 4) = L.rho * (h_T[r] - h[r] / s.T);
  }
  const Mat5 P = matrix_P(fr);
  // J = P M Pᵀ(式 (80))
  Mat5 J{};
  for (int r = 0; r < 5; ++r) {
    for (int c = 0; c < 5; ++c) {
      double sum = 0.0;
      for (int i = 0; i < 5; ++i) {
        for (int j = 0; j < 5; ++j) sum += P(r, i) * M(i, j) * P(c, j);
      }
      J(r, c) = sum;
    }
  }
  return J;
}

const State kStates[] = {
    {1.0, {0.3, -0.2, 0.1}, 1.0},
    {0.5, {1.2, 0.4, -0.7}, 0.7},
    {2.0, {-0.8, 0.5, 0.3}, 1.3},
    {0.8, {-2.0, 0.1, 0.2}, 0.9},  // 法線方向の速さが熱速度より大きい場合も含む
};

const Vec3 kNormals[] = {
    {1.0, 0.0, 0.0},
    {0.0, 0.0, -1.0},
    {1.0 / 3.0, 2.0 / 3.0, -2.0 / 3.0},
    {-0.48, 0.6, 0.64},
};

}  // namespace

TEST_CASE("half-space flux: closed form agrees with the paper's local-frame form", "[physics][flux]") {
  for (const double R : {1.0, 2076.16}) {
    for (const State& s0 : kStates) {
      // R が違うときは同じ無次元状態を SI 風に伸ばす(u は √R 倍)
      const State s{s0.p, std::sqrt(R) * s0.u, s0.T};
      const auto scale = flux_scales(s, R);
      for (const Vec3& n : kNormals) {
        for (const int side : {+1, -1}) {
          const Vec5 F = half_space_flux(side, s.p, s.u, s.T, R, n);
          const Vec5 Fp = paper_flux(side, s, R, n);
          for (int k = 0; k < 5; ++k) CHECK(std::abs(F[k] - Fp[k]) <= 1e-13 * scale[static_cast<std::size_t>(k)]);
          const Mat5 J = half_space_flux_jacobian(side, s.p, s.u, s.T, R, n);
          const Mat5 Jp = paper_jacobian(side, s, R, n);
          const double col_scale[5] = {s.p, std::sqrt(R * s.T), std::sqrt(R * s.T), std::sqrt(R * s.T), s.T};
          for (int r = 0; r < 5; ++r) {
            for (int c = 0; c < 5; ++c) {
              CHECK(std::abs(J(r, c) - Jp(r, c)) <= 1e-12 * scale[static_cast<std::size_t>(r)] / col_scale[c]);
            }
          }
        }
      }
    }
  }
}

TEST_CASE("half-space flux: F+ + F- is the full Euler flux, and F-(n) = -F+(-n)", "[physics][flux]") {
  const double R = 1.0;
  for (const State& s : kStates) {
    const auto scale = flux_scales(s, R);
    for (const Vec3& n : kNormals) {
      const Vec5 Fp = half_space_flux(+1, s.p, s.u, s.T, R, n);
      const Vec5 Fm = half_space_flux(-1, s.p, s.u, s.T, R, n);
      const Vec5 Ffull = full_space_flux(s.p, s.u, s.T, R, n);
      const Vec5 Fp_neg = half_space_flux(+1, s.p, s.u, s.T, R, -n);
      for (int k = 0; k < 5; ++k) {
        CHECK(std::abs(Fp[k] + Fm[k] - Ffull[k]) <= 1e-14 * scale[static_cast<std::size_t>(k)]);
        CHECK(std::abs(Fm[k] + Fp_neg[k]) <= 1e-14 * scale[static_cast<std::size_t>(k)]);
      }
      CHECK(Fp[0] > 0.0);  // 流出側の質量流束は正、流入側は負
      CHECK(Fm[0] < 0.0);
    }
  }
  // 法線方向の速さが大きいと、流入側はほぼ 0、流出側はほぼ全流束
  const State fast{1.0, {7.0, 0.3, -0.2}, 1.0};
  const Vec3 n{1.0, 0.0, 0.0};
  const Vec5 Fm = half_space_flux(-1, fast.p, fast.u, fast.T, R, n);
  const Vec5 Fp = half_space_flux(+1, fast.p, fast.u, fast.T, R, n);
  const Vec5 Ff = full_space_flux(fast.p, fast.u, fast.T, R, n);
  for (int k = 0; k < 5; ++k) {
    CHECK(std::abs(Fm[k]) < 1e-10 * std::abs(Ff[k]) + 1e-300);
    CHECK(Fp[k] == Approx(Ff[k]).epsilon(1e-10));
  }
}

TEST_CASE("half-space flux: closed form agrees with discrete sums on a fine velocity grid", "[physics][flux]") {
  const double R = 1.0;
  // 細かい中点格子(Δ = 0.1)。誤差は ξ·n = 0 の折れ目による O(Δ²)
  const VelocityGrid g({160, 160, 160}, {-8.0, -8.0, -8.0}, {8.0, 8.0, 8.0});
  const VelocityGrid coarse({80, 80, 80}, {-8.0, -8.0, -8.0}, {8.0, 8.0, 8.0});

  auto discrete = [&](const VelocityGrid& grid, const std::vector<double>& f, const Vec3& n, int side) {
    double F[5] = {0, 0, 0, 0, 0};
    for (int k = 0; k < grid.size(); ++k) {
      const Vec3 xi = grid.xi(k);
      const double xn = dot(xi, n);
      if (side > 0 ? !(xn > 0.0) : !(xn < 0.0)) continue;
      const double w = xn * f[static_cast<std::size_t>(k)];
      F[0] += w;
      F[1] += w * xi.x;
      F[2] += w * xi.y;
      F[3] += w * xi.z;
      F[4] += w * 0.5 * norm2(xi);
    }
    Vec5 out;
    for (int c = 0; c < 5; ++c) out[c] = F[c] * grid.weight();
    return out;
  };
  auto sample = [&](const VelocityGrid& grid, const State& s) {
    const MaxwellParams<double> mp = make_maxwell_params(s.p / (R * s.T), s.u, s.T, R);
    std::vector<double> f(static_cast<std::size_t>(grid.size()));
    for (int k = 0; k < grid.size(); ++k) f[static_cast<std::size_t>(k)] = maxwellian(mp, grid.xi(k));
    return f;
  };

  for (std::size_t si = 0; si < 3; ++si) {
    const State& s = kStates[si];
    const auto scale = flux_scales(s, R);
    const std::vector<double> f = sample(g, s);
    const std::vector<double> fc = sample(coarse, s);
    for (const Vec3& n : kNormals) {
      for (const int side : {+1, -1}) {
        const Vec5 exact = half_space_flux(side, s.p, s.u, s.T, R, n);
        const Vec5 fine_sum = discrete(g, f, n, side);
        const Vec5 coarse_sum = discrete(coarse, fc, n, side);
        double err_fine = 0.0;
        double err_coarse = 0.0;
        for (int k = 0; k < 5; ++k) {
          err_fine = std::max(err_fine, std::abs(fine_sum[k] - exact[k]) / scale[static_cast<std::size_t>(k)]);
          err_coarse = std::max(err_coarse, std::abs(coarse_sum[k] - exact[k]) / scale[static_cast<std::size_t>(k)]);
        }
        INFO("state " << si << ", n = (" << n.x << ", " << n.y << ", " << n.z << "), side " << side
                      << ": error fine " << err_fine << ", coarse " << err_coarse);
        CHECK(err_fine < 1e-3);
        CHECK(err_coarse < 4e-3);
      }
    }
  }
}

TEST_CASE("half-space flux Jacobian agrees with central differences", "[physics][flux]") {
  for (const double R : {1.0, 2076.16}) {
    for (const State& s0 : kStates) {
      const State s{s0.p, std::sqrt(R) * s0.u, s0.T};
      const double a = std::sqrt(R * s.T);
      const double W0[5] = {s.p, s.u.x, s.u.y, s.u.z, s.T};
      const double h[5] = {1e-5 * s.p, 1e-5 * a, 1e-5 * a, 1e-5 * a, 1e-5 * s.T};
      const double col_scale[5] = {s.p, a, a, a, s.T};
      const auto scale = flux_scales(s, R);
      for (const Vec3& n : kNormals) {
        for (const int side : {+1, -1}) {
          const Mat5 J = half_space_flux_jacobian(side, s.p, s.u, s.T, R, n);
          for (int c = 0; c < 5; ++c) {
            double Wp[5], Wm[5];
            std::copy(W0, W0 + 5, Wp);
            std::copy(W0, W0 + 5, Wm);
            Wp[c] += h[c];
            Wm[c] -= h[c];
            const Vec5 Fp = half_space_flux(side, Wp[0], Vec3{Wp[1], Wp[2], Wp[3]}, Wp[4], R, n);
            const Vec5 Fm = half_space_flux(side, Wm[0], Vec3{Wm[1], Wm[2], Wm[3]}, Wm[4], R, n);
            for (int r = 0; r < 5; ++r) {
              const double fd = (Fp[r] - Fm[r]) / (2.0 * h[c]);
              const double tol = 1e-7 * scale[static_cast<std::size_t>(r)] / col_scale[c];
              INFO("R " << R << ", side " << side << ", J(" << r << ", " << c << ") = " << J(r, c)
                        << ", 差分 " << fd);
              CHECK(std::abs(J(r, c) - fd) <= tol);
            }
          }
        }
      }
    }
  }
}

TEST_CASE("half-space flux in FP32 is close to FP64", "[physics][flux]") {
  const State s = kStates[1];
  const Vec3 n = kNormals[2];
  const auto scale = flux_scales(s, 1.0);
  for (const int side : {+1, -1}) {
    const Vec5 Fd = half_space_flux(side, s.p, s.u, s.T, 1.0, n);
    const Vec5T<float> Ff = half_space_flux(side, static_cast<float>(s.p), vec_cast<float>(s.u),
                                            static_cast<float>(s.T), 1.0f, vec_cast<float>(n));
    for (int k = 0; k < 5; ++k) CHECK(std::abs(Ff[k] - Fd[k]) <= 1e-5 * scale[static_cast<std::size_t>(k)]);
  }
}
