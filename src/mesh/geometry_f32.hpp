#pragma once
// GPU に送るためのメッシュ幾何量の FP32 の写し(フェーズ 2 で使う。docs/design_cis_gpu.md §2)。
// - 量ごとに別の配列にする(構造体の配列ではなく配列の構造体。GPU で連続アクセスになるように)
// - 長さは length_scale(通常は L_ref)で割って無次元にしてから FP32 にする(spec §3.2)
// - CPU 側の正本は FP64 の MeshGeometry / FvGeometry / LsqGradient のまま

#include <array>
#include <cstdint>
#include <vector>

#include "mesh/fv_geometry.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

struct FaceGeometryF32 {
  std::vector<float> sx, sy, sz;            // 面積ベクトル S_f / L^2
  std::vector<float> cx, cy, cz;            // 面中心 x_f / L
  std::vector<std::int32_t> owner;          // 全ての面
  std::vector<std::int32_t> neighbour;      // 内部面
  std::vector<std::int32_t> boundary_patch; // 境界面 f のパッチ番号(添字は f − n_internal_faces)
};

struct CellGeometryF32 {
  std::vector<float> volume;      // V_i / L^3
  std::vector<float> cx, cy, cz;  // 重心 x_i / L
};

struct MeshGeometryF32 {
  double length_scale = 1.0;  // 割った長さ [m]
  FaceGeometryF32 faces;
  CellGeometryF32 cells;
  // セル → 面の CSR(CellFaceCsr の写し。符号は +1: owner 側、−1: neighbour 側。相手は境界なら −1)
  std::vector<std::int64_t> cell_face_offsets;
  std::vector<std::int32_t> cell_faces;
  std::vector<std::int32_t> cell_face_other;
  std::vector<std::int8_t> cell_face_sign;
  // 最小二乗勾配の係数ベクトル(CSR 順、無次元の座標での値 = c_k × L)
  std::vector<float> lsq_cx, lsq_cy, lsq_cz;
  // 最小二乗勾配の G^{-1}(セルごと、成分 xx, yy, zz, xy, yz, xz の順に 6 本の配列)。
  // 無次元の座標での値(G^{-1} × L^{2−p}、p は重みのべき)。grad = G^{-1} Σ_k ω_k r_k Δφ_k で使う。
  std::array<std::vector<float>, 6> lsq_g_inverse;
  double lsq_weight_power = 0.0;

  std::size_t bytes() const;  // GPU に置いたときの大きさの目安
};

MeshGeometryF32 make_geometry_f32(const PolyMesh& mesh, const MeshGeometry& geometry,
                                  const CellFaceCsr& csr, const LsqGradient& lsq,
                                  double length_scale);

}  // namespace gsis
