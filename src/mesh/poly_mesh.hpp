#pragma once
// 面ベースの任意多面体メッシュ(docs/pmesh_format.md の内容をそのまま持つ)。
//
// 規約(pmesh_format.md §3):
//   - 面は内部面 [0, n_internal_faces) が先、境界面が後。境界面はパッチごとに連続する。
//   - 内部面は owner < neighbour で、(owner, neighbour) の辞書順に並ぶ。
//   - 面の節点は、法線(右ねじ)が owner → neighbour(境界面では外側)を向く順。

#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "core/types.hpp"
#include "core/vec3.hpp"

namespace gsis {

// 境界パッチ(境界面の連続区間)
struct Patch {
  std::string name;
  std::string kind = "patch";  // wall / inlet / outlet / symmetry / patch(参考情報)
  Index start = 0;             // 最初の面の番号
  Index count = 0;             // 面数
  Index end() const { return start + count; }
};

// パッチの種類として認める文字列
bool is_known_patch_kind(std::string_view kind);

struct PolyMesh {
  std::vector<Vec3> points;              // 節点座標 [m]
  std::vector<Offset> face_offsets{0};   // 長さ n_faces + 1。面 f の節点は face_nodes[off[f], off[f+1])
  std::vector<Index> face_nodes;
  std::vector<Index> owner;              // 全ての面の owner セル
  std::vector<Index> neighbour;          // 内部面の neighbour セル
  std::vector<Patch> patches;
  Index n_cells = 0;
  std::string length_unit = "m";
  std::vector<std::pair<std::string, std::string>> metadata;  // 参考情報(解釈しない)

  Index n_points() const { return static_cast<Index>(points.size()); }
  Index n_faces() const { return static_cast<Index>(owner.size()); }
  Index n_internal_faces() const { return static_cast<Index>(neighbour.size()); }
  Index n_boundary_faces() const { return n_faces() - n_internal_faces(); }
  bool is_internal(Index f) const { return f < n_internal_faces(); }

  std::span<const Index> face(Index f) const {
    const Offset b = face_offsets[static_cast<std::size_t>(f)];
    const Offset e = face_offsets[static_cast<std::size_t>(f) + 1];
    return {face_nodes.data() + b, static_cast<std::size_t>(e - b)};
  }
  Index face_size(Index f) const {
    return static_cast<Index>(face_offsets[static_cast<std::size_t>(f) + 1] -
                              face_offsets[static_cast<std::size_t>(f)]);
  }

  // 境界面 f が属するパッチの番号(内部面・範囲外なら -1)
  int patch_of_face(Index f) const;
  // 名前でパッチを探す(無ければ -1)
  int find_patch(std::string_view name) const;
  // 境界面ごとのパッチ番号(長さ n_boundary_faces)
  std::vector<int> boundary_face_patches() const;
};

// 位相(番号・並び・配列の長さ・面の形)を検査する。違反は gsis::Error。
// 検査する内容: pmesh_format.md §3 の 1・2・4・5(面数)・7、番号の範囲、配列の長さ、長さの単位。
// 同じセルの組を結ぶ内部面が複数あってもよい(並びは非減少)。count が 0 のパッチも許す
// (警告は .pmesh の読み手 read_pmesh が出す)。
void validate_topology(const PolyMesh& mesh);

// どの面からも参照されない節点の数(推奨違反。エラーにはしない)
Index count_unused_points(const PolyMesh& mesh);

}  // namespace gsis
