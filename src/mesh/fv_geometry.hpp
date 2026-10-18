#pragma once
// 有限体積法で使う面・セルの量:
//   - 面: d_ij、d_n,ij、補間重み w(論文の式 (22)(23))、境界面の d_n = 2|x_f − x_i|
//   - セル → 面の CSR(セルごとの面の一覧と、相手のセル・向き)
//   - 最小二乗勾配の係数(spec §3.4、仮定 A3)

#include <array>
#include <span>
#include <vector>

#include "core/types.hpp"
#include "core/vec3.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

// 面ごとの量(全ての面について持つ)
struct FvGeometry {
  // 内部面: d_ij = x_N − x_O(owner から neighbour)。境界面: x_f − x_O。
  std::vector<Vec3> d;
  // 内部面: d_n,ij = n̂ · d_ij。境界面: 2 |x_f − x_O|(論文 §3.3.5 の擬似時間刻みの定義)。
  std::vector<Real> dn;
  // 補間重み(式 (23)): w_N = min{1, max[0, n̂·(x_f − x_O)/d_n]}、w_O = 1 − w_N。
  // 面の値は φ_f = w_O φ_O + w_N φ_N。境界面は w_O = 1、w_N = 0。
  std::vector<Real> w_owner;
  std::vector<Real> w_neighbour;
  // d_n <= 0 になった内部面の数(このとき重みは 0.5 にする)
  Index n_nonpositive_dn = 0;
};

FvGeometry compute_fv_geometry(const PolyMesh& mesh, const MeshGeometry& geometry);

// セル → 面の CSR。セル c の面は faces[offsets[c] .. offsets[c+1])(面番号の昇順)。
struct CellFaceCsr {
  std::vector<Offset> offsets;      // 長さ n_cells + 1
  std::vector<Index> faces;         // 面番号
  std::vector<Index> other;         // 内部面なら相手のセル、境界面なら kInvalidIndex
  std::vector<signed char> sign;    // +1: このセルが owner(S_f が外向き)、−1: neighbour

  Index n_cells() const { return static_cast<Index>(offsets.size()) - 1; }
  Offset begin(Index c) const { return offsets[static_cast<std::size_t>(c)]; }
  Offset end(Index c) const { return offsets[static_cast<std::size_t>(c) + 1]; }
  Index n_faces_of(Index c) const { return static_cast<Index>(end(c) - begin(c)); }
};

CellFaceCsr build_cell_faces(const PolyMesh& mesh);

// 最小二乗勾配: grad φ_i = Σ_k c_k (φ_k − φ_i)。k はセル i の面(CellFaceCsr の並び)で、
// 内部面なら φ_k は相手のセルの値(位置はセル中心)、境界面なら φ_k は境界面の値(位置は面中心)。
// 係数は c_k = ω_k G^{-1} r_k、G = Σ_k ω_k r_k r_kᵀ、r_k は相手の位置 − x_i。
// 重み ω_k = |r_k|^(−weight_power)。0 なら重みなし(既定)、2 なら各式を距離で割るのと同じ
// (OpenFOAM の leastSquares と同じ重み)。
struct LsqOptions {
  double weight_power = 0.0;
};

struct LsqGradient {
  LsqOptions options;
  std::vector<Vec3> coeff;          // CellFaceCsr の faces と同じ並び
  // セルごとの G^{-1}(対称 3×3。成分の順は xx, yy, zz, xy, yz, xz)。
  // 係数を持たずに grad φ_i = G^{-1} Σ_k ω_k r_k (φ_k − φ_i) と計算するときに使う(GPU 向け)。
  std::vector<std::array<Real, 6>> g_inverse;
  // セルごとの G の条件の目安 det(G) / (tr(G)/3)^3(1 が最良、0 に近いほど悪い)
  std::vector<Real> conditioning;
};

LsqGradient build_lsq_gradient(const PolyMesh& mesh, const MeshGeometry& geometry,
                               const CellFaceCsr& csr, const LsqOptions& options = {});

// スカラー場の勾配を計算する。phi_boundary は境界面の値(長さ n_boundary_faces、
// 境界面 f の値は phi_boundary[f − n_internal_faces])。
void compute_lsq_gradient(const PolyMesh& mesh, const CellFaceCsr& csr, const LsqGradient& lsq,
                          std::span<const Real> phi_cell, std::span<const Real> phi_boundary,
                          std::span<Vec3> grad);

}  // namespace gsis
