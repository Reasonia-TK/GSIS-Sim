#include "config/case_mesh_check.hpp"

#include <cmath>
#include <format>
#include <map>
#include <utility>

namespace gsis {

int symmetry_patch_axis(const PolyMesh& mesh, const MeshGeometry& g, const Patch& patch, double tolerance) {
  (void)mesh;
  int axis = -1;
  for (Index f = patch.start; f < patch.end(); ++f) {
    const Vec3& n = g.face_normal[static_cast<std::size_t>(f)];
    int a = -1;
    for (int k = 0; k < 3; ++k) {
      if (std::abs(std::abs(n[k]) - 1.0) <= tolerance) a = k;
    }
    if (a < 0 || (axis >= 0 && a != axis)) return -1;
    axis = a;
  }
  return axis;
}

std::vector<std::string> check_case_against_mesh(const CaseConfig& config, const PolyMesh& mesh,
                                                 const MeshGeometry& geometry,
                                                 const VelocityGrid& velocity_grid) {
  std::vector<std::string> errors;
  for (const Patch& p : mesh.patches) {
    if (config.find_boundary(p.name) == nullptr) {
      errors.push_back(std::format("メッシュのパッチ \"{}\" に境界条件がありません", p.name));
    }
  }
  for (const BoundarySpec& b : config.boundaries) {
    const int p = mesh.find_patch(b.patch);
    if (p < 0) {
      errors.push_back(std::format("境界条件のパッチ \"{}\" がメッシュにありません", b.patch));
      continue;
    }
    const Patch& patch = mesh.patches[static_cast<std::size_t>(p)];
    // 面が 0 個のパッチ(pmesh_format.md §3 の 7 で許される)には形の条件がない
    if (patch.count == 0) continue;
    if (b.type == BoundaryType::PeriodicSlab) {
      // 並進周期: 同じセルのパッチ面が対になり、面積ベクトルの和が 0 になること
      std::map<Index, std::pair<Vec3, Real>> per_cell;  // セル → (ΣS, Σ|S|)
      for (Index f = patch.start; f < patch.end(); ++f) {
        auto& [sum, abs_sum] = per_cell[mesh.owner[static_cast<std::size_t>(f)]];
        sum += geometry.face_area_vector[static_cast<std::size_t>(f)];
        abs_sum += geometry.face_area[static_cast<std::size_t>(f)];
      }
      for (const auto& [cell, s] : per_cell) {
        if (norm(s.first) > 1e-9 * s.second) {
          errors.push_back(std::format("並進周期 \"{}\" の面が、セル {} で対になっていません(1 セル厚の方向の"
                                       "対面だけに使えます)",
                                       b.patch, cell));
          break;
        }
      }
      continue;
    }
    if (b.type != BoundaryType::Symmetry) continue;
    const int axis = symmetry_patch_axis(mesh, geometry, patch);
    if (axis < 0) {
      errors.push_back(std::format("対称面 \"{}\" の法線が速度格子の 1 つの軸に揃っていません", b.patch));
    } else if (!velocity_grid.symmetric(axis)) {
      errors.push_back(std::format("対称面 \"{}\" の法線の軸 {} で、速度格子の範囲が 0 に対して対称ではありません",
                                   b.patch, "xyz"[axis]));
    }
  }
  return errors;
}

}  // namespace gsis
