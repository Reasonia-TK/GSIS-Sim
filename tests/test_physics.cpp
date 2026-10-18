// physics: Maxwell・Shakhov 分布とモーメントの計算(CPU)

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <vector>

#include "core/error.hpp"
#include "physics/limiter.hpp"
#include "physics/maxwell.hpp"
#include "physics/moments.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using Catch::Approx;

namespace {

std::vector<double> sample_maxwell(const VelocityGrid& g, double rho, const Vec3& u, double T, double R) {
  const MaxwellParams<double> mp = make_maxwell_params(rho, u, T, R);
  std::vector<double> f(static_cast<std::size_t>(g.size()));
  for (int k = 0; k < g.size(); ++k) f[static_cast<std::size_t>(k)] = maxwellian(mp, g.xi(k));
  return f;
}

}  // namespace

TEST_CASE("Maxwellian: closed form", "[physics]") {
  // f^eq(ξ = u) = ρ (2π R T)^{-3/2}、1 次元で e^{-1/2} だけ離れると減る
  const double rho = 1.3, T = 0.8, R = 1.0;
  const Vec3 u{0.2, -0.4, 0.1};
  const double peak = rho / std::pow(2.0 * 3.14159265358979323846 * R * T, 1.5);
  CHECK(maxwellian(rho, u, T, R, u) == Approx(peak).epsilon(1e-15));
  const Vec3 xi = u + Vec3{std::sqrt(R * T), 0.0, 0.0};
  CHECK(maxwellian(rho, u, T, R, xi) == Approx(peak * std::exp(-0.5)).epsilon(1e-15));
  // FP32 版も同じ式
  CHECK(maxwellian(1.3f, vec_cast<float>(u), 0.8f, 1.0f, vec_cast<float>(xi)) ==
        Approx(peak * std::exp(-0.5)).epsilon(1e-6));
}

TEST_CASE("Maxwellian moments are reproduced on the tube velocity grid", "[physics][moments]") {
  // 無次元(R = 1、単位 √(R T0))。spec §3.3 の管の速度格子
  const VelocityGrid g({30, 24, 24}, {-10.0, -8.0, -8.0}, {10.0, 8.0, 8.0});
  struct State {
    double rho;
    Vec3 u;
    double T;
  };
  const State states[] = {
      {1.0, {0.0, 0.0, 0.0}, 1.0},
      {0.37, {1.8, -0.6, 0.3}, 1.3},
      {2.5, {-1.2, 0.9, -0.8}, 0.6},
      {1e-3, {0.5, 0.2, -0.1}, 1.5},  // 真空に近い密度
  };
  for (const State& s : states) {
    const std::vector<double> f = sample_maxwell(g, s.rho, s.u, s.T, 1.0);
    const Moments m = compute_moments(g, f, 1.0);
    // 中点則は Gauss 関数に対して誤差が指数的に小さく、範囲の打ち切りを含めても誤差は 2e-9 以下
    // (最大は T = 1.5 の状態の T)。許容値はその 5 倍程度にする。
    CHECK(m.rho == Approx(s.rho).epsilon(1e-8));
    CHECK(norm(m.u - s.u) < 1e-8);
    CHECK(m.T == Approx(s.T).epsilon(1e-8));
    CHECK(m.p == Approx(s.rho * s.T).epsilon(1e-8));
    for (const double sg : m.sigma) CHECK(std::abs(sg) < 1e-8 * m.p);
    CHECK(norm(m.q) < 1e-8 * m.p * std::sqrt(m.T));
    // FP32 で保存した f でも(集計は FP64)
    std::vector<float> f32(f.size());
    for (std::size_t k = 0; k < f.size(); ++k) f32[k] = static_cast<float>(f[k]);
    const Moments m32 = compute_moments(g, std::span<const float>(f32), 1.0);
    CHECK(m32.rho == Approx(s.rho).epsilon(1e-6));
    CHECK(m32.T == Approx(s.T).epsilon(1e-6));
    // 保存量
    const ConservedMoments c = compute_conserved(g, f);
    CHECK(c.rho == Approx(m.rho).epsilon(1e-14));
    CHECK(c.energy == Approx(0.5 * m.rho * norm2(m.u) + 1.5 * m.p).epsilon(1e-12));
  }
}

TEST_CASE("Maxwellian moments in SI units", "[physics][moments]") {
  // He、T = 300 K。速度格子は √(R T0) を単位にした範囲を SI に直して作る
  const double R = 1.380649e-23 / 6.65e-27;
  const double T0 = 273.15;
  const double c0 = std::sqrt(R * T0);
  const VelocityGrid g({40, 40, 40}, {-8 * c0, -8 * c0, -8 * c0}, {8 * c0, 8 * c0, 8 * c0});
  const double rho = 3.2e-4, T = 300.0;
  const Vec3 u{150.0, -80.0, 20.0};
  const Moments m = compute_moments(g, sample_maxwell(g, rho, u, T, R), R);
  CHECK(m.rho == Approx(rho).epsilon(1e-8));
  CHECK(norm(m.u - u) < 1e-8 * c0);
  CHECK(m.T == Approx(T).epsilon(1e-8));
  CHECK(m.p == Approx(rho * R * T).epsilon(1e-8));
}

TEST_CASE("moments of a two-Maxwellian mixture (stress and heat flux)", "[physics][moments]") {
  const double R = 1.0;
  // 打ち切り誤差が 1e-10 より十分小さくなる範囲(高温側の成分で約 8.6σ)
  const VelocityGrid g({64, 64, 64}, {-11.0, -11.0, -11.0}, {11.0, 11.0, 11.0});
  const double r1 = 0.7, T1 = 0.9, r2 = 0.4, T2 = 1.4;
  const Vec3 u1{0.8, -0.3, 0.2}, u2{-0.5, 0.6, -0.4};
  std::vector<double> f = sample_maxwell(g, r1, u1, T1, R);
  const std::vector<double> f2 = sample_maxwell(g, r2, u2, T2, R);
  for (std::size_t k = 0; k < f.size(); ++k) f[k] += f2[k];
  const Moments m = compute_moments(g, f, R);

  // 解析値: Π = Σ ρ_i (θ_i I + d_i d_iᵀ)、q = Σ ½ ρ_i d_i (5 θ_i + |d_i|²)、d_i = u_i − u
  const double rho = r1 + r2;
  const Vec3 u = (r1 * u1 + r2 * u2) / rho;
  const Vec3 d1 = u1 - u, d2 = u2 - u;
  double Pi[3][3];
  for (int a = 0; a < 3; ++a) {
    for (int b = 0; b < 3; ++b) {
      Pi[a][b] = r1 * d1[a] * d1[b] + r2 * d2[a] * d2[b] + (a == b ? r1 * R * T1 + r2 * R * T2 : 0.0);
    }
  }
  const double p = (Pi[0][0] + Pi[1][1] + Pi[2][2]) / 3.0;
  const Vec3 q = 0.5 * r1 * (5.0 * R * T1 + norm2(d1)) * d1 + 0.5 * r2 * (5.0 * R * T2 + norm2(d2)) * d2;
  CHECK(m.rho == Approx(rho).epsilon(1e-10));
  CHECK(norm(m.u - u) < 1e-10);
  CHECK(m.p == Approx(p).epsilon(1e-10));
  CHECK(m.T == Approx(p / (rho * R)).epsilon(1e-10));
  const double sigma[6] = {Pi[0][0] - p, Pi[1][1] - p, Pi[2][2] - p, Pi[0][1], Pi[1][2], Pi[0][2]};
  for (int k = 0; k < 6; ++k) CHECK(m.sigma[static_cast<std::size_t>(k)] == Approx(sigma[k]).margin(1e-10));
  CHECK(norm(m.q - q) < 1e-10);
}

TEST_CASE("Shakhov target: moments and heat flux (1 - Pr) q", "[physics][moments]") {
  const double R = 1.0, Pr = 2.0 / 3.0;
  const VelocityGrid g({48, 48, 48}, {-9.0, -9.0, -9.0}, {9.0, 9.0, 9.0});
  const double rho = 1.2, T = 0.9;
  const Vec3 u{0.3, -0.2, 0.5};
  const Vec3 q{0.08, -0.05, 0.02};
  const ShakhovParams<double> sp = make_shakhov_params(rho, u, T, q, R, Pr);
  std::vector<double> f(static_cast<std::size_t>(g.size()));
  for (int k = 0; k < g.size(); ++k) f[static_cast<std::size_t>(k)] = shakhov(sp, g.xi(k));
  const Moments m = compute_moments(g, f, R);
  CHECK(m.rho == Approx(rho).epsilon(1e-10));
  CHECK(norm(m.u - u) < 1e-10);
  CHECK(m.T == Approx(T).epsilon(1e-10));
  for (const double sg : m.sigma) CHECK(std::abs(sg) < 1e-10);
  CHECK(norm(m.q - (1.0 - Pr) * q) < 1e-10);
  // q = 0 なら Maxwell 分布に一致する
  const Vec3 xi{0.7, -1.1, 0.4};
  CHECK(shakhov(rho, u, T, Vec3{}, R, Pr, xi) == Approx(maxwellian(rho, u, T, R, xi)).epsilon(1e-15));
}

TEST_CASE("compute_moments rejects bad input", "[physics][moments]") {
  const VelocityGrid g({4, 4, 4}, {-1.0, -1.0, -1.0}, {1.0, 1.0, 1.0});
  const std::vector<double> zero(64, 0.0);
  CHECK_THROWS_AS(compute_moments(g, zero, 1.0), Error);
  const std::vector<double> short_f(10, 1.0);
  CHECK_THROWS_AS(compute_moments(g, short_f, 1.0), Error);
}

TEST_CASE("max_moment_difference normalizes by the state", "[physics][moments]") {
  Moments a;
  a.rho = 2.0;
  a.u = Vec3{1.0, 0.0, 0.0};
  a.T = 4.0;
  a.p = 8.0;
  Moments b = a;
  b.rho = 2.002;
  b.u = Vec3{1.0, 0.02, 0.0};
  b.q = Vec3{0.0, 0.0, 0.16};
  const std::vector<Moments> va = {a};
  const std::vector<Moments> vb = {b};
  const MomentDifference d = max_moment_difference(va, vb, 1.0);
  CHECK(d.rho == Approx(1e-3));
  CHECK(d.u == Approx(0.01));     // 0.02 / √4
  CHECK(d.q == Approx(0.01));     // 0.16 / (8 · 2)
  CHECK(d.T == 0.0);
  CHECK(d.max() == Approx(0.01));
}

TEST_CASE("Venkatakrishnan limiter", "[physics][limiter]") {
  // Δ2 = 0 なら 1、Δ1 = 0(極値)で ε = 0 なら 0
  CHECK(venkatakrishnan_face(0.0, 1.0, -1.0, 0.0) == 1.0);
  CHECK(venkatakrishnan_face(0.5, 0.0, -1.0, 0.0) == 0.0);
  // 余裕が十分(Δ1 ≫ Δ2)なら 1 に近く、[0, 1] に収まる
  const double big = venkatakrishnan_face(0.01, 1.0, -1.0, 0.0);
  CHECK(big <= 1.0);
  CHECK(big == Approx(1.0).margin(1e-3));
  // Δ2 = Δ1(再構成値がちょうど隣の最大値)のとき (1 + 2)/(1 + 2 + 1) = 3/4
  CHECK(venkatakrishnan_face(1.0, 1.0, -1.0, 0.0) == Approx(0.75));
  // 負の側も対称
  CHECK(venkatakrishnan_face(-1.0, 1.0, -1.0, 0.0) == Approx(0.75));
  // ε が大きいと制限が弱まる
  CHECK(venkatakrishnan_face(1.0, 1.0, -1.0, 100.0) > 0.99);
  // ε² = (K h)³ = K³ V
  CHECK(venkatakrishnan_eps2(2.0, 0.125) == Approx(1.0));
  // 累積は面ごとの最小値
  VenkatakrishnanAccumulator<double> acc(1.0, -1.0, 0.0);
  acc.add(0.0);
  acc.add(1.0);
  acc.add(-0.1);
  CHECK(acc.value() == Approx(0.75));
  // 制限付きの再構成
  CHECK(reconstruct_limited(2.0, Vec3{1.0, 2.0, 0.0}, 0.5, Vec3{0.1, 0.1, 5.0}) == Approx(2.15));
  // float でも同じ
  CHECK(venkatakrishnan_face(1.0f, 1.0f, -1.0f, 0.0f) == Approx(0.75f));
}
