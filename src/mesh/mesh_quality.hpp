#pragma once
// メッシュの品質と構成の統計(mesh-info の表示、VTU のセル値に使う)。

#include <array>
#include <string>
#include <vector>

#include "core/types.hpp"
#include "core/vec3.hpp"
#include "mesh/fv_geometry.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

// 非直交角: 内部面の単位法線 n̂ と d_ij のなす角 θ = acos(n̂·d_ij / |d_ij|) [度]
struct NonOrthogonalityStats {
  double max_deg = 0.0;
  double mean_deg = 0.0;           // 単純平均
  double area_weighted_mean_deg = 0.0;
  Index worst_face = kInvalidIndex;
  // 閾値ごとの面数
  static constexpr std::array<double, 5> kThresholdsDeg = {10.0, 30.0, 45.0, 60.0, 70.0};
  std::array<Index, 5> n_above{};
  std::vector<Real> face_angle_deg;  // 内部面ごとの角度
  std::vector<Real> cell_max_deg;    // セルごとの最大(境界のみのセルは 0)
};

NonOrthogonalityStats compute_non_orthogonality(const PolyMesh& mesh, const MeshGeometry& geometry,
                                                const FvGeometry& fv);

// 面とセルの形の内訳
struct ShapeStats {
  Index n_triangles = 0;   // 3 節点の面
  Index n_quads = 0;       // 4 節点の面
  Index n_polygons = 0;    // 5 節点以上の面
  int max_face_nodes = 0;
  std::array<Index, 5> cells_by_faces{};  // 4, 5, 6, 7, 8 面以上
  int max_cell_faces = 0;
  std::vector<int> cell_face_count;       // セルごとの面数
};

ShapeStats compute_shape_stats(const PolyMesh& mesh);

struct BoundingBox {
  Vec3 lo{};
  Vec3 hi{};
};

BoundingBox compute_bounding_box(const PolyMesh& mesh);

// パッチごとの面数と面積
struct PatchSummary {
  std::string name;
  std::string kind;
  Index n_faces = 0;
  double area = 0.0;
  Vec3 area_vector{};  // Σ S_f(平面のパッチなら面積 × 外向き法線)
};

std::vector<PatchSummary> summarize_patches(const PolyMesh& mesh, const MeshGeometry& geometry);

}  // namespace gsis
