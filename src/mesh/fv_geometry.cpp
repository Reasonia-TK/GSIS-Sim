#include "mesh/fv_geometry.hpp"

#include <cmath>
#include <format>

#include "core/error.hpp"

namespace gsis {

FvGeometry compute_fv_geometry(const PolyMesh& mesh, const MeshGeometry& g) {
  const Index n_faces = mesh.n_faces();
  const Index n_internal = mesh.n_internal_faces();
  FvGeometry fv;
  fv.d.resize(static_cast<std::size_t>(n_faces));
  fv.dn.resize(static_cast<std::size_t>(n_faces));
  fv.w_owner.resize(static_cast<std::size_t>(n_faces));
  fv.w_neighbour.resize(static_cast<std::size_t>(n_faces));

  for (Index f = 0; f < n_faces; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const Vec3& xo = g.cell_center[static_cast<std::size_t>(mesh.owner[fi])];
    const Vec3& xf = g.face_center[fi];
    const Vec3& n = g.face_normal[fi];
    if (f < n_internal) {
      const Vec3& xn = g.cell_center[static_cast<std::size_t>(mesh.neighbour[fi])];
      const Vec3 d = xn - xo;
      const double dn = dot(n, d);
      fv.d[fi] = d;
      fv.dn[fi] = dn;
      double wn = 0.5;
      if (dn > 0.0) {
        wn = dmath::clamp(dot(n, xf - xo) / dn, 0.0, 1.0);
      } else {
        ++fv.n_nonpositive_dn;
      }
      fv.w_neighbour[fi] = wn;
      fv.w_owner[fi] = 1.0 - wn;
    } else {
      const Vec3 d = xf - xo;
      fv.d[fi] = d;
      fv.dn[fi] = 2.0 * norm(d);
      fv.w_owner[fi] = 1.0;
      fv.w_neighbour[fi] = 0.0;
    }
  }
  return fv;
}

CellFaceCsr build_cell_faces(const PolyMesh& mesh) {
  const Index n_faces = mesh.n_faces();
  const Index n_internal = mesh.n_internal_faces();
  const auto n_cells = static_cast<std::size_t>(mesh.n_cells);

  CellFaceCsr csr;
  csr.offsets.assign(n_cells + 1, 0);
  for (Index f = 0; f < n_faces; ++f) {
    ++csr.offsets[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)]) + 1];
    if (f < n_internal) {
      ++csr.offsets[static_cast<std::size_t>(mesh.neighbour[static_cast<std::size_t>(f)]) + 1];
    }
  }
  for (std::size_t c = 0; c < n_cells; ++c) csr.offsets[c + 1] += csr.offsets[c];

  const auto total = static_cast<std::size_t>(csr.offsets.back());
  csr.faces.resize(total);
  csr.other.resize(total);
  csr.sign.resize(total);
  std::vector<Offset> fill(csr.offsets.begin(), csr.offsets.end() - 1);
  // 面番号の昇順に詰めるので、各セルの中でも面番号の昇順になる
  for (Index f = 0; f < n_faces; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const Index o = mesh.owner[fi];
    const Index nb = f < n_internal ? mesh.neighbour[fi] : kInvalidIndex;
    {
      const auto k = static_cast<std::size_t>(fill[static_cast<std::size_t>(o)]++);
      csr.faces[k] = f;
      csr.other[k] = nb;
      csr.sign[k] = 1;
    }
    if (nb != kInvalidIndex) {
      const auto k = static_cast<std::size_t>(fill[static_cast<std::size_t>(nb)]++);
      csr.faces[k] = f;
      csr.other[k] = o;
      csr.sign[k] = -1;
    }
  }
  return csr;
}

namespace {

// 対称 3×3 行列 [a00 a01 a02; a01 a11 a12; a02 a12 a22]
struct Sym3 {
  double a00 = 0, a01 = 0, a02 = 0, a11 = 0, a12 = 0, a22 = 0;

  void add(double w, const Vec3& r) {
    a00 += w * r.x * r.x;
    a01 += w * r.x * r.y;
    a02 += w * r.x * r.z;
    a11 += w * r.y * r.y;
    a12 += w * r.y * r.z;
    a22 += w * r.z * r.z;
  }
  double det() const {
    return a00 * (a11 * a22 - a12 * a12) - a01 * (a01 * a22 - a12 * a02) +
           a02 * (a01 * a12 - a11 * a02);
  }
  double trace() const { return a00 + a11 + a22; }
  // 逆行列(余因子 / det)
  Sym3 inverse(double d) const {
    Sym3 inv;
    inv.a00 = (a11 * a22 - a12 * a12) / d;
    inv.a01 = (a02 * a12 - a01 * a22) / d;
    inv.a02 = (a01 * a12 - a02 * a11) / d;
    inv.a11 = (a00 * a22 - a02 * a02) / d;
    inv.a12 = (a01 * a02 - a00 * a12) / d;
    inv.a22 = (a00 * a11 - a01 * a01) / d;
    return inv;
  }
  Vec3 mul(const Vec3& v) const {
    return {a00 * v.x + a01 * v.y + a02 * v.z, a01 * v.x + a11 * v.y + a12 * v.z,
            a02 * v.x + a12 * v.y + a22 * v.z};
  }
};

}  // namespace

LsqGradient build_lsq_gradient(const PolyMesh& mesh, const MeshGeometry& g, const CellFaceCsr& csr,
                               const LsqOptions& options) {
  GSIS_CHECK(options.weight_power >= 0.0,
             std::format("最小二乗の重みのべき {} は 0 以上にしてください", options.weight_power));
  const Index n_cells = mesh.n_cells;
  LsqGradient lsq;
  lsq.options = options;
  lsq.coeff.resize(csr.faces.size());
  lsq.g_inverse.resize(static_cast<std::size_t>(n_cells));
  lsq.conditioning.resize(static_cast<std::size_t>(n_cells));

  auto displacement = [&](Index c, Offset k) {
    const auto ki = static_cast<std::size_t>(k);
    const Vec3& xc = g.cell_center[static_cast<std::size_t>(c)];
    if (csr.other[ki] != kInvalidIndex) {
      return g.cell_center[static_cast<std::size_t>(csr.other[ki])] - xc;
    }
    return g.face_center[static_cast<std::size_t>(csr.faces[ki])] - xc;
  };
  auto weight = [&](const Vec3& r) {
    if (options.weight_power == 0.0) return 1.0;
    return std::pow(norm(r), -options.weight_power);
  };

  for (Index c = 0; c < n_cells; ++c) {
    Sym3 G;
    for (Offset k = csr.begin(c); k < csr.end(c); ++k) {
      const Vec3 r = displacement(c, k);
      G.add(weight(r), r);
    }
    const double det = G.det();
    const double tr3 = G.trace() / 3.0;
    const double cond = tr3 > 0.0 ? det / (tr3 * tr3 * tr3) : 0.0;
    lsq.conditioning[static_cast<std::size_t>(c)] = cond;
    GSIS_CHECK(cond > 1e-12,
               std::format("セル {} の最小二乗勾配の行列が特異に近いです(det(G)/(tr(G)/3)^3 = {:.3e})",
                           c, cond));
    const Sym3 inv = G.inverse(det);
    lsq.g_inverse[static_cast<std::size_t>(c)] = {inv.a00, inv.a11, inv.a22, inv.a01, inv.a12, inv.a02};
    for (Offset k = csr.begin(c); k < csr.end(c); ++k) {
      const Vec3 r = displacement(c, k);
      lsq.coeff[static_cast<std::size_t>(k)] = weight(r) * inv.mul(r);
    }
  }
  return lsq;
}

void compute_lsq_gradient(const PolyMesh& mesh, const CellFaceCsr& csr, const LsqGradient& lsq,
                          std::span<const Real> phi_cell, std::span<const Real> phi_boundary,
                          std::span<Vec3> grad) {
  const Index n_cells = mesh.n_cells;
  const Index n_internal = mesh.n_internal_faces();
  GSIS_ASSERT(phi_cell.size() == static_cast<std::size_t>(n_cells));
  GSIS_ASSERT(phi_boundary.size() == static_cast<std::size_t>(mesh.n_boundary_faces()));
  GSIS_ASSERT(grad.size() == static_cast<std::size_t>(n_cells));
  for (Index c = 0; c < n_cells; ++c) {
    const double phi_c = phi_cell[static_cast<std::size_t>(c)];
    Vec3 gsum{};
    for (Offset k = csr.begin(c); k < csr.end(c); ++k) {
      const auto ki = static_cast<std::size_t>(k);
      const Index other = csr.other[ki];
      const double phi_k =
          other != kInvalidIndex
              ? phi_cell[static_cast<std::size_t>(other)]
              : phi_boundary[static_cast<std::size_t>(csr.faces[ki] - n_internal)];
      gsum += (phi_k - phi_c) * lsq.coeff[ki];
    }
    grad[static_cast<std::size_t>(c)] = gsum;
  }
}

}  // namespace gsis
