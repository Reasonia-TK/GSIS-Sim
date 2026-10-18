#include "mesh/poly_mesh.hpp"

#include <format>
#include <set>

#include "core/error.hpp"

namespace gsis {

bool is_known_patch_kind(std::string_view kind) {
  return kind == "wall" || kind == "inlet" || kind == "outlet" || kind == "symmetry" ||
         kind == "patch";
}

int PolyMesh::patch_of_face(Index f) const {
  if (f < n_internal_faces() || f >= n_faces()) return -1;
  // パッチは面番号の順に並ぶので二分探索できるが、パッチ数は少ないので線形で探す
  for (std::size_t p = 0; p < patches.size(); ++p) {
    if (f >= patches[p].start && f < patches[p].end()) return static_cast<int>(p);
  }
  return -1;
}

int PolyMesh::find_patch(std::string_view name) const {
  for (std::size_t p = 0; p < patches.size(); ++p) {
    if (patches[p].name == name) return static_cast<int>(p);
  }
  return -1;
}

std::vector<int> PolyMesh::boundary_face_patches() const {
  std::vector<int> out(static_cast<std::size_t>(n_boundary_faces()), -1);
  for (std::size_t p = 0; p < patches.size(); ++p) {
    for (Index f = patches[p].start; f < patches[p].end(); ++f) {
      out[static_cast<std::size_t>(f - n_internal_faces())] = static_cast<int>(p);
    }
  }
  return out;
}

void validate_topology(const PolyMesh& mesh) {
  const Index n_points = mesh.n_points();
  const Index n_faces = mesh.n_faces();
  const Index n_internal = mesh.n_internal_faces();
  const Index n_cells = mesh.n_cells;

  GSIS_CHECK(mesh.length_unit == "m",
             std::format("長さの単位 \"{}\" には対応していません(v1 は \"m\" のみ)", mesh.length_unit));
  GSIS_CHECK(n_cells > 0, "セルがありません");
  GSIS_CHECK(n_points > 0, "節点がありません");
  GSIS_CHECK(n_internal <= n_faces,
             std::format("内部面の数 {} が面の数 {} を超えています", n_internal, n_faces));

  // 面の節点リスト
  GSIS_CHECK(mesh.face_offsets.size() == static_cast<std::size_t>(n_faces) + 1,
             std::format("face_offsets の長さ {} が n_faces + 1 = {} と違います",
                         mesh.face_offsets.size(), static_cast<long long>(n_faces) + 1));
  GSIS_CHECK(mesh.face_offsets.front() == 0, "face_offsets[0] が 0 ではありません");
  GSIS_CHECK(mesh.face_offsets.back() == static_cast<Offset>(mesh.face_nodes.size()),
             std::format("face_offsets の最後 {} が face_nodes の長さ {} と違います",
                         mesh.face_offsets.back(), mesh.face_nodes.size()));
  // 節点を読む前に、全ての面で節点数が 3 以上(face_offsets が単調に増える)ことを確かめる。
  // 先頭が 0、最後が face_nodes の長さなので、これで全ての区間が配列の中に収まる。
  for (Index f = 0; f < n_faces; ++f) {
    const Offset b = mesh.face_offsets[static_cast<std::size_t>(f)];
    const Offset e = mesh.face_offsets[static_cast<std::size_t>(f) + 1];
    GSIS_CHECK(e - b >= 3, std::format("面 {} の節点が 3 個未満です({} 個)", f, e - b));
  }
  for (Index f = 0; f < n_faces; ++f) {
    const std::span<const Index> nodes = mesh.face(f);
    for (std::size_t i = 0; i < nodes.size(); ++i) {
      GSIS_CHECK(nodes[i] >= 0 && nodes[i] < n_points,
                 std::format("面 {} の節点番号 {} が範囲 [0, {}) の外です", f, nodes[i], n_points));
      for (std::size_t j = 0; j < i; ++j) {
        GSIS_CHECK(nodes[i] != nodes[j],
                   std::format("面 {} に同じ節点 {} が重複しています", f, nodes[i]));
      }
    }
  }

  // owner / neighbour
  std::vector<Index> faces_per_cell(static_cast<std::size_t>(n_cells), 0);
  for (Index f = 0; f < n_faces; ++f) {
    const Index o = mesh.owner[static_cast<std::size_t>(f)];
    GSIS_CHECK(o >= 0 && o < n_cells,
               std::format("面 {} の owner {} が範囲 [0, {}) の外です", f, o, n_cells));
    ++faces_per_cell[static_cast<std::size_t>(o)];
  }
  for (Index f = 0; f < n_internal; ++f) {
    const Index o = mesh.owner[static_cast<std::size_t>(f)];
    const Index n = mesh.neighbour[static_cast<std::size_t>(f)];
    GSIS_CHECK(n >= 0 && n < n_cells,
               std::format("面 {} の neighbour {} が範囲 [0, {}) の外です", f, n, n_cells));
    GSIS_CHECK(o < n, std::format("内部面 {} で owner {} < neighbour {} になっていません", f, o, n));
    ++faces_per_cell[static_cast<std::size_t>(n)];
    if (f > 0) {
      const Index po = mesh.owner[static_cast<std::size_t>(f) - 1];
      const Index pn = mesh.neighbour[static_cast<std::size_t>(f) - 1];
      GSIS_CHECK(po < o || (po == o && pn <= n),
                 std::format("内部面が (owner, neighbour) の順に並んでいません(面 {} の ({}, {}) の後に"
                             "面 {} の ({}, {}))",
                             f - 1, po, pn, f, o, n));
    }
  }
  for (Index c = 0; c < n_cells; ++c) {
    GSIS_CHECK(faces_per_cell[static_cast<std::size_t>(c)] >= 4,
               std::format("セル {} の面が 4 未満です({} 面)", c,
                           faces_per_cell[static_cast<std::size_t>(c)]));
  }

  // パッチ
  std::set<std::string> names;
  Index expected_start = n_internal;
  for (std::size_t p = 0; p < mesh.patches.size(); ++p) {
    const Patch& patch = mesh.patches[p];
    GSIS_CHECK(!patch.name.empty(), std::format("パッチ {} の名前が空です", p));
    GSIS_CHECK(names.insert(patch.name).second,
               std::format("パッチ名 \"{}\" が重複しています", patch.name));
    GSIS_CHECK(is_known_patch_kind(patch.kind),
               std::format("パッチ \"{}\" の kind \"{}\" は知らない種類です(wall, inlet, outlet, "
                           "symmetry, patch のいずれか)",
                           patch.name, patch.kind));
    GSIS_CHECK(patch.count >= 0,
               std::format("パッチ \"{}\" の count {} が負です", patch.name, patch.count));
    GSIS_CHECK(patch.start == expected_start,
               std::format("パッチ \"{}\" の start {} が {} と違います(パッチは内部面の直後から"
                           "連続して並ぶ)",
                           patch.name, patch.start, expected_start));
    expected_start = patch.start + patch.count;
  }
  GSIS_CHECK(expected_start == n_faces,
             std::format("パッチが境界面を過不足なく覆っていません(パッチの終わり {}、面の数 {})",
                         expected_start, n_faces));
}

Index count_unused_points(const PolyMesh& mesh) {
  std::vector<char> used(mesh.points.size(), 0);
  for (const Index n : mesh.face_nodes) {
    if (n >= 0 && n < mesh.n_points()) used[static_cast<std::size_t>(n)] = 1;
  }
  Index unused = 0;
  for (const char u : used) unused += u ? 0 : 1;
  return unused;
}

}  // namespace gsis
