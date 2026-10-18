#pragma once
// ケース定義とメッシュ・速度格子の突き合わせ(check-case と、今後のソルバーの起動時に使う)。

#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"
#include "velocity/velocity_grid.hpp"

namespace gsis {

// 次を確かめ、問題を文の一覧で返す(空なら問題なし):
//   - メッシュの全パッチに境界条件がある / 境界条件のパッチがメッシュにある
//   - 対称面のパッチは、法線が速度格子の 1 つの軸に揃い、その軸の範囲が 0 に対して対称
//     (面が 0 個のパッチは形の条件がないので調べない)
std::vector<std::string> check_case_against_mesh(const CaseConfig& config, const PolyMesh& mesh,
                                                 const MeshGeometry& geometry,
                                                 const VelocityGrid& velocity_grid);

// 対称面のパッチの法線が揃っている軸(0, 1, 2)。揃っていない・面が 0 個なら -1。
int symmetry_patch_axis(const PolyMesh& mesh, const MeshGeometry& geometry, const Patch& patch,
                        double tolerance = 1e-8);

}  // namespace gsis
