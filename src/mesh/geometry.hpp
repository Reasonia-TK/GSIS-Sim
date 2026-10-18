#pragma once
// 面とセルの幾何量(docs/pmesh_format.md §4 の定義。OpenFOAM の primitiveMesh と同じ方法)と、その検査。

#include <span>
#include <string>
#include <vector>

#include "core/types.hpp"
#include "core/vec3.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

struct FaceGeometry {
  Vec3 area_vector;  // S_f
  Vec3 center;       // x_f
};

// 1 枚の面の幾何量。三角形はそのまま、4 節点以上は節点平均を仮の中心とする扇形分割。
FaceGeometry compute_face_geometry(std::span<const Vec3> points, std::span<const Index> nodes);

struct MeshGeometry {
  std::vector<Vec3> face_area_vector;  // S_f(owner から neighbour、境界では外向き)
  std::vector<Real> face_area;         // |S_f|
  std::vector<Vec3> face_normal;       // 単位法線 S_f / |S_f|
  std::vector<Vec3> face_center;       // x_f
  std::vector<Real> cell_volume;       // V_i
  std::vector<Vec3> cell_center;       // x_i(角錐分割による重心)
};

// 全ての面とセルの幾何量を計算する(面積ゼロの面は gsis::Error)
MeshGeometry compute_geometry(const PolyMesh& mesh);

// 幾何の検査結果(pmesh_format.md §4 の検査)
struct MeshCheckReport {
  double closure_tolerance = 1e-10;   // 閉性の許容値(相対)
  double max_closure_residual = 0.0;  // max_i |Σ S_out| / Σ |S_f|
  Index worst_closure_cell = kInvalidIndex;
  Index n_open_cells = 0;             // 許容値を超えたセル
  double min_volume = 0.0;
  double max_volume = 0.0;
  double total_volume = 0.0;
  Index n_nonpositive_volume = 0;
  Index n_orientation_warnings = 0;   // S_f · (x_N − x_O) <= 0 の内部面
  Index n_unused_points = 0;
  std::vector<std::string> errors;    // エラー(閉じていない、体積が正でない)
  std::vector<std::string> warnings;  // 警告(内部面の向き、参照されない節点)

  bool ok() const { return errors.empty(); }
};

// 幾何を検査する。エラーは errors に入れて返す(例外は投げない)。
// 個々の不具合は最初の max_listed 件まで文にする。
MeshCheckReport check_geometry(const PolyMesh& mesh, const MeshGeometry& geometry,
                               double closure_tolerance = 1e-10, int max_listed = 10);

}  // namespace gsis
