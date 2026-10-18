#include "physics/moments.hpp"

#include <algorithm>
#include <cmath>
#include <format>

#include "core/error.hpp"

namespace gsis {
namespace {

template <class F>
Moments compute_moments_impl(const VelocityGrid& grid, std::span<const F> f, double R) {
  const int nv = grid.size();
  GSIS_CHECK(f.size() == static_cast<std::size_t>(nv),
             std::format("分布関数の長さ {} が速度点の数 {} と違います", f.size(), nv));
  const double* xs = grid.xs().data();
  const double* ys = grid.ys().data();
  const double* zs = grid.zs().data();
  const double w = grid.weight();

  // 1 段階目: ρ と ρu
  double s0 = 0.0, sx = 0.0, sy = 0.0, sz = 0.0;
  for (int k = 0; k < nv; ++k) {
    const double fk = static_cast<double>(f[static_cast<std::size_t>(k)]);
    s0 += fk;
    sx += fk * xs[k];
    sy += fk * ys[k];
    sz += fk * zs[k];
  }
  Moments m;
  m.rho = w * s0;
  GSIS_CHECK(m.rho > 0.0, std::format("密度が正ではありません(ρ = {})", m.rho));
  m.u = Vec3{sx / s0, sy / s0, sz / s0};

  // 2 段階目: c = ξ − u で 2 次・3 次のモーメント
  double pxx = 0.0, pyy = 0.0, pzz = 0.0, pxy = 0.0, pyz = 0.0, pxz = 0.0;
  double qx = 0.0, qy = 0.0, qz = 0.0, c2sum = 0.0;
  for (int k = 0; k < nv; ++k) {
    const double fk = static_cast<double>(f[static_cast<std::size_t>(k)]);
    const double cx = xs[k] - m.u.x;
    const double cy = ys[k] - m.u.y;
    const double cz = zs[k] - m.u.z;
    const double c2 = cx * cx + cy * cy + cz * cz;
    pxx += fk * cx * cx;
    pyy += fk * cy * cy;
    pzz += fk * cz * cz;
    pxy += fk * cx * cy;
    pyz += fk * cy * cz;
    pxz += fk * cx * cz;
    qx += fk * c2 * cx;
    qy += fk * c2 * cy;
    qz += fk * c2 * cz;
    c2sum += fk * c2;
  }
  m.p = w * c2sum / 3.0;
  m.T = m.p / (m.rho * R);
  m.sigma = {w * pxx - m.p, w * pyy - m.p, w * pzz - m.p, w * pxy, w * pyz, w * pxz};
  m.q = Vec3{0.5 * w * qx, 0.5 * w * qy, 0.5 * w * qz};
  return m;
}

}  // namespace

Moments compute_moments(const VelocityGrid& grid, std::span<const double> f, double R) {
  return compute_moments_impl(grid, f, R);
}

Moments compute_moments(const VelocityGrid& grid, std::span<const float> f, double R) {
  return compute_moments_impl(grid, f, R);
}

double MomentDifference::max() const {
  return std::max({rho, u, T, p, sigma, q});
}

MomentDifference max_moment_difference(std::span<const Moments> a, std::span<const Moments> b,
                                       double R) {
  GSIS_CHECK(a.size() == b.size(), "比べるモーメントの数が違います");
  MomentDifference d;
  for (std::size_t i = 0; i < a.size(); ++i) {
    const Moments& x = a[i];
    const Moments& y = b[i];
    const double vt = std::sqrt(R * x.T);
    d.rho = std::max(d.rho, std::abs(x.rho - y.rho) / std::abs(x.rho));
    d.u = std::max(d.u, norm(x.u - y.u) / vt);
    d.T = std::max(d.T, std::abs(x.T - y.T) / std::abs(x.T));
    d.p = std::max(d.p, std::abs(x.p - y.p) / std::abs(x.p));
    for (int k = 0; k < 6; ++k) {
      d.sigma = std::max(d.sigma, std::abs(x.sigma[static_cast<std::size_t>(k)] -
                                           y.sigma[static_cast<std::size_t>(k)]) /
                                      std::abs(x.p));
    }
    d.q = std::max(d.q, norm(x.q - y.q) / (std::abs(x.p) * vt));
  }
  return d;
}

ConservedMoments compute_conserved(const VelocityGrid& grid, std::span<const double> f) {
  const int nv = grid.size();
  GSIS_CHECK(f.size() == static_cast<std::size_t>(nv),
             std::format("分布関数の長さ {} が速度点の数 {} と違います", f.size(), nv));
  ConservedMoments c;
  for (int k = 0; k < nv; ++k) {
    const Vec3 xi = grid.xi(k);
    const double fk = f[static_cast<std::size_t>(k)];
    c.rho += fk;
    c.momentum += fk * xi;
    c.energy += 0.5 * norm2(xi) * fk;
  }
  const double w = grid.weight();
  c.rho *= w;
  c.momentum *= w;
  c.energy *= w;
  return c;
}

}  // namespace gsis
