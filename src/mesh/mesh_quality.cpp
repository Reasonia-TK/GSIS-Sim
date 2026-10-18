#include "mesh/mesh_quality.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

#include "core/math.hpp"

namespace gsis {

NonOrthogonalityStats compute_non_orthogonality(const PolyMesh& mesh, const MeshGeometry& g,
                                                const FvGeometry& fv) {
  const Index n_internal = mesh.n_internal_faces();
  NonOrthogonalityStats s;
  s.face_angle_deg.resize(static_cast<std::size_t>(n_internal));
  s.cell_max_deg.assign(static_cast<std::size_t>(mesh.n_cells), 0.0);
  double sum = 0.0;
  double sum_area = 0.0;
  double sum_weighted = 0.0;
  for (Index f = 0; f < n_internal; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    // 角度が 0 に近いときに acos は桁落ちするので、atan2(|n × d|, n·d) で求める
    const Vec3& d = fv.d[fi];
    const Vec3& n = g.face_normal[fi];
    const double deg = std::atan2(norm(cross(n, d)), dot(n, d)) * 180.0 / kPi;
    s.face_angle_deg[fi] = deg;
    sum += deg;
    sum_area += g.face_area[fi];
    sum_weighted += g.face_area[fi] * deg;
    if (s.worst_face == kInvalidIndex || deg > s.max_deg) {
      s.max_deg = deg;
      s.worst_face = f;
    }
    for (std::size_t t = 0; t < s.kThresholdsDeg.size(); ++t) {
      if (deg > s.kThresholdsDeg[t]) ++s.n_above[t];
    }
    auto& co = s.cell_max_deg[static_cast<std::size_t>(mesh.owner[fi])];
    auto& cn = s.cell_max_deg[static_cast<std::size_t>(mesh.neighbour[fi])];
    co = std::max(co, deg);
    cn = std::max(cn, deg);
  }
  if (n_internal > 0) {
    s.mean_deg = sum / n_internal;
    s.area_weighted_mean_deg = sum_area > 0.0 ? sum_weighted / sum_area : 0.0;
  }
  return s;
}

ShapeStats compute_shape_stats(const PolyMesh& mesh) {
  ShapeStats s;
  s.cell_face_count.assign(static_cast<std::size_t>(mesh.n_cells), 0);
  for (Index f = 0; f < mesh.n_faces(); ++f) {
    const Index n = mesh.face_size(f);
    if (n == 3) {
      ++s.n_triangles;
    } else if (n == 4) {
      ++s.n_quads;
    } else {
      ++s.n_polygons;
    }
    s.max_face_nodes = std::max(s.max_face_nodes, static_cast<int>(n));
    ++s.cell_face_count[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)])];
    if (mesh.is_internal(f)) {
      ++s.cell_face_count[static_cast<std::size_t>(mesh.neighbour[static_cast<std::size_t>(f)])];
    }
  }
  for (const int nf : s.cell_face_count) {
    const int bin = std::clamp(nf, 4, 8) - 4;
    ++s.cells_by_faces[static_cast<std::size_t>(bin)];
    s.max_cell_faces = std::max(s.max_cell_faces, nf);
  }
  return s;
}

BoundingBox compute_bounding_box(const PolyMesh& mesh) {
  BoundingBox b;
  if (mesh.points.empty()) return b;
  constexpr double inf = std::numeric_limits<double>::infinity();
  b.lo = Vec3{inf, inf, inf};
  b.hi = Vec3{-inf, -inf, -inf};
  for (const Vec3& p : mesh.points) {
    for (int a = 0; a < 3; ++a) {
      b.lo[a] = std::min(b.lo[a], p[a]);
      b.hi[a] = std::max(b.hi[a], p[a]);
    }
  }
  return b;
}

std::vector<PatchSummary> summarize_patches(const PolyMesh& mesh, const MeshGeometry& g) {
  std::vector<PatchSummary> out;
  for (const Patch& p : mesh.patches) {
    PatchSummary s;
    s.name = p.name;
    s.kind = p.kind;
    s.n_faces = p.count;
    for (Index f = p.start; f < p.end(); ++f) {
      s.area += g.face_area[static_cast<std::size_t>(f)];
      s.area_vector += g.face_area_vector[static_cast<std::size_t>(f)];
    }
    out.push_back(std::move(s));
  }
  return out;
}

}  // namespace gsis
