#include "mesh/geometry_f32.hpp"

#include <cmath>
#include <format>

#include "core/error.hpp"

namespace gsis {

std::size_t MeshGeometryF32::bytes() const {
  auto sz = [](const auto& v) { return v.size() * sizeof(v[0]); };
  std::size_t total = sz(faces.sx) + sz(faces.sy) + sz(faces.sz) + sz(faces.cx) + sz(faces.cy) +
                      sz(faces.cz) + sz(faces.owner) + sz(faces.neighbour) + sz(faces.boundary_patch) +
                      sz(cells.volume) + sz(cells.cx) + sz(cells.cy) + sz(cells.cz) +
                      sz(cell_face_offsets) + sz(cell_faces) + sz(cell_face_other) + sz(cell_face_sign) +
                      sz(lsq_cx) + sz(lsq_cy) + sz(lsq_cz);
  for (const auto& a : lsq_g_inverse) total += sz(a);
  return total;
}

MeshGeometryF32 make_geometry_f32(const PolyMesh& mesh, const MeshGeometry& g,
                                  const CellFaceCsr& csr, const LsqGradient& lsq,
                                  double length_scale) {
  GSIS_CHECK(length_scale > 0.0, std::format("長さの基準 {} は正にしてください", length_scale));
  const double inv_l = 1.0 / length_scale;
  const double inv_l2 = inv_l * inv_l;
  const double inv_l3 = inv_l2 * inv_l;

  MeshGeometryF32 out;
  out.length_scale = length_scale;
  const auto n_faces = static_cast<std::size_t>(mesh.n_faces());
  FaceGeometryF32& f32 = out.faces;
  f32.sx.resize(n_faces);
  f32.sy.resize(n_faces);
  f32.sz.resize(n_faces);
  f32.cx.resize(n_faces);
  f32.cy.resize(n_faces);
  f32.cz.resize(n_faces);
  for (std::size_t f = 0; f < n_faces; ++f) {
    f32.sx[f] = static_cast<float>(g.face_area_vector[f].x * inv_l2);
    f32.sy[f] = static_cast<float>(g.face_area_vector[f].y * inv_l2);
    f32.sz[f] = static_cast<float>(g.face_area_vector[f].z * inv_l2);
    f32.cx[f] = static_cast<float>(g.face_center[f].x * inv_l);
    f32.cy[f] = static_cast<float>(g.face_center[f].y * inv_l);
    f32.cz[f] = static_cast<float>(g.face_center[f].z * inv_l);
  }
  f32.owner = mesh.owner;
  f32.neighbour = mesh.neighbour;
  const std::vector<int> patches = mesh.boundary_face_patches();
  f32.boundary_patch.assign(patches.begin(), patches.end());

  const auto n_cells = static_cast<std::size_t>(mesh.n_cells);
  CellGeometryF32& c32 = out.cells;
  c32.volume.resize(n_cells);
  c32.cx.resize(n_cells);
  c32.cy.resize(n_cells);
  c32.cz.resize(n_cells);
  for (std::size_t c = 0; c < n_cells; ++c) {
    c32.volume[c] = static_cast<float>(g.cell_volume[c] * inv_l3);
    c32.cx[c] = static_cast<float>(g.cell_center[c].x * inv_l);
    c32.cy[c] = static_cast<float>(g.cell_center[c].y * inv_l);
    c32.cz[c] = static_cast<float>(g.cell_center[c].z * inv_l);
  }

  out.cell_face_offsets.assign(csr.offsets.begin(), csr.offsets.end());
  out.cell_faces.assign(csr.faces.begin(), csr.faces.end());
  out.cell_face_other.assign(csr.other.begin(), csr.other.end());
  out.cell_face_sign.assign(csr.sign.begin(), csr.sign.end());

  // 勾配の係数は 1/長さ の次元なので L を掛ける
  out.lsq_cx.resize(lsq.coeff.size());
  out.lsq_cy.resize(lsq.coeff.size());
  out.lsq_cz.resize(lsq.coeff.size());
  for (std::size_t k = 0; k < lsq.coeff.size(); ++k) {
    out.lsq_cx[k] = static_cast<float>(lsq.coeff[k].x * length_scale);
    out.lsq_cy[k] = static_cast<float>(lsq.coeff[k].y * length_scale);
    out.lsq_cz[k] = static_cast<float>(lsq.coeff[k].z * length_scale);
  }
  // G = Σ ω r rᵀ(ω = |r|^{−p})は 長さ^{2−p} の次元なので、G^{-1} に L^{2−p} を掛ける
  out.lsq_weight_power = lsq.options.weight_power;
  const double ginv_scale = std::pow(length_scale, 2.0 - lsq.options.weight_power);
  for (std::size_t m = 0; m < 6; ++m) {
    out.lsq_g_inverse[m].resize(lsq.g_inverse.size());
    for (std::size_t c = 0; c < lsq.g_inverse.size(); ++c) {
      out.lsq_g_inverse[m][c] = static_cast<float>(lsq.g_inverse[c][m] * ginv_scale);
    }
  }
  return out;
}

}  // namespace gsis
