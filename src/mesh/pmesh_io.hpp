#pragma once
// .pmesh(docs/pmesh_format.md)の読み書き。

#include <filesystem>

#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

// .pmesh を読み、ファイル構造と位相を検査する(幾何の検査はしない)。違反は gsis::Error。
// 整数の配列は int32 と int64 のどちらでも受け付ける(内部では int32 にする)。
PolyMesh read_pmesh(const std::filesystem::path& path);

// 読み込み + 位相の検査 + 幾何量の計算 + 幾何の検査(閉性・体積・内部面の向き)。
// エラーがあれば gsis::Error を投げる。警告はログに出し、report にも残す。
struct LoadedMesh {
  PolyMesh mesh;
  MeshGeometry geometry;
  MeshCheckReport report;
};
LoadedMesh load_pmesh(const std::filesystem::path& path);

// メモリ上のメッシュを同じ手順で検証する(load_pmesh の後半)
LoadedMesh validate_mesh(PolyMesh mesh);

// .pmesh を書く(書く前に位相を検査する)。dtype は points: float64、face_offsets: int64、
// face_nodes・owner・neighbour: int32。metadata には mesh.metadata を書く。
void write_pmesh(const std::filesystem::path& path, const PolyMesh& mesh);

}  // namespace gsis
